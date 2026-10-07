"""CPU-only proof for the temporary native acceptance admission boundary."""
import ast
import importlib.util
import inspect
import os
import sys
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import traceback
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from services import job_lifecycle as lifecycle
from services.native_acceptance_reservation import (
    NativeAcceptanceReserved, Reservation, job_identity,
)


def candidate():
    return {"id": "retained", "created_at": 1.0, "workspace": "project", "status": "queued",
        "queue_held": False, "execution_attempt": 1, "recovery_attempt": 2,
        "project_instance": {"instance": "original"}, "owner_principal": {"owner": "original"},
        "request_manifest": {"sealed": "original"}}


class NativeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        lifecycle._reset_queue_state_for_tests()
        self.job = candidate()
        self.guard = Reservation({"identity": job_identity(self.job),
            "execution_attempt": 1, "recovery_attempt": 2})

    def tearDown(self):
        lifecycle._reset_queue_state_for_tests()

    def test_ordinary_lock_identity_and_cross_thread_release(self):
        lock = threading.Lock()
        self.assertIs(Reservation().wrap_lock(lock), lock)
        reserved = self.guard.wrap_lock(threading.Lock())
        with patch.object(lifecycle, "_native_acceptance", self.guard):
            with lifecycle.generation_slot(threading.Lock(), self.job) as admitted:
                self.assertTrue(admitted)
                self.assertTrue(reserved.acquire(False))
                thread = threading.Thread(target=reserved.release)
                thread.start(); thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertFalse(reserved.locked())

    def test_restored_runtime_identity_can_enter_its_frozen_slot(self):
        # launch._queue_recovery_materialize_job keeps private identity in these
        # recovery fields rather than exposing it on the runtime job.
        restored = dict(self.job)
        restored["_recovery_project_digest"] = restored.pop("project_instance")
        restored["_recovery_owner_digest"] = restored.pop("owner_principal")
        restored["_recovery_manifest_pointer"] = restored.pop("request_manifest")
        self.assertEqual(job_identity(restored), job_identity(self.job))
        restored["queue_held"] = True
        with patch.object(lifecycle, "_native_acceptance", self.guard):
            self.assertEqual(lifecycle.set_job_hold(restored, False), "resumed")
            self.assertEqual(job_identity(restored), job_identity(self.job))
            self.assertEqual((restored["execution_attempt"], restored["recovery_attempt"]), (1, 2))
            with lifecycle.generation_slot(threading.Lock(), restored) as admitted:
                self.assertTrue(admitted)
                self.guard.require_model_admission()
                with self.guard.wrap_lock(threading.Lock()):
                    pass

    def test_recovery_identity_conflicts_and_missing_components_fail_closed(self):
        for key, alias in (
            ("project_instance", "_recovery_project_digest"),
            ("owner_principal", "_recovery_owner_digest"),
            ("request_manifest", "_recovery_manifest_pointer"),
        ):
            changed = dict(self.job)
            changed[alias] = {"different": "identity"}
            with self.subTest(key=key, case="conflict"):
                self.assertFalse(self.guard.eligible(changed))
            changed.pop(key)
            changed.pop(alias)
            with self.subTest(key=key, case="missing"):
                self.assertFalse(self.guard.eligible(changed))
            changed[key] = None
            changed[alias] = self.job[key]
            with self.subTest(key=key, case="null canonical"):
                self.assertFalse(self.guard.eligible(changed))

    def test_direct_calls_and_other_threads_cannot_borrow_target_slot(self):
        lock = self.guard.wrap_lock(threading.Lock())
        with self.assertRaises(NativeAcceptanceReserved):
            lock.acquire(False)
        denied = []
        def unrelated():
            try:
                lock.acquire(False)
            except NativeAcceptanceReserved:
                denied.append(True)
        with patch.object(lifecycle, "_native_acceptance", self.guard):
            with lifecycle.generation_slot(threading.Lock(), self.job):
                thread = threading.Thread(target=unrelated)
                thread.start(); thread.join(1)
                self.assertEqual(denied, [True])
                with lock:
                    self.assertTrue(lock.locked())
            with self.assertRaises(NativeAcceptanceReserved):
                lock.acquire(False)

    def test_new_attempt_or_changed_sealed_identity_loses_model_permit(self):
        lock = self.guard.wrap_lock(threading.Lock())
        with patch.object(lifecycle, "_native_acceptance", self.guard):
            with lifecycle.generation_slot(threading.Lock(), self.job):
                for key, value in [("execution_attempt", 2), ("recovery_attempt", 3),
                        ("request_manifest", {"sealed": "changed"}), ("execution_attempt", True)]:
                    prior = self.job[key]; self.job[key] = value
                    with self.subTest(key=key, value=value), self.assertRaises(NativeAcceptanceReserved):
                        lock.acquire(False)
                    self.job[key] = prior
                self.assertFalse(lock.locked())

    def test_declared_running_attempt_requires_same_live_scheduler_admission(self):
        guard = Reservation(dict(self.guard.target, running_execution_attempt=2))
        native_lock = guard.wrap_lock(threading.Lock())
        generation_lock = threading.Lock()
        with patch.object(lifecycle, "_native_acceptance", guard):
            with lifecycle.generation_slot(generation_lock, self.job) as admitted:
                self.assertTrue(admitted)
                with self.assertRaises(NativeAcceptanceReserved):
                    native_lock.acquire(False)
                self.assertTrue(lifecycle.try_start(self.job,
                    generation_lock=generation_lock, expected_execution_attempt=1,
                    execution_attempt=2))
                with native_lock:
                    self.assertTrue(native_lock.locked())
                self.assertFalse(guard.eligible(self.job))
                for key, value in [("execution_attempt", 3), ("execution_attempt", 1),
                        ("recovery_attempt", 3), ("status", "queued"),
                        ("request_manifest", {"sealed": "changed"})]:
                    original = self.job[key]
                    try:
                        self.job[key] = value
                        with self.subTest(key=key, value=value), self.assertRaises(NativeAcceptanceReserved):
                            native_lock.acquire(False)
                    finally:
                        self.job[key] = original
                copied = dict(self.job)
                self.assertFalse(guard.eligible(copied))
            with self.assertRaises(NativeAcceptanceReserved):
                native_lock.acquire(False)

    def test_running_attempt_declaration_rejects_invalid_or_skipped_attempts(self):
        for value in (True, None, "2", 1, 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Reservation(dict(self.guard.target, running_execution_attempt=value))

    def test_target_terminal_does_not_release_reservation_to_other_jobs(self):
        foreign = dict(candidate(), id="other")
        entered = threading.Event(); ended = threading.Event()
        def queued_other():
            with lifecycle.generation_slot(threading.Lock(), foreign) as admitted:
                if admitted:
                    entered.set()
            ended.set()
        with patch.object(lifecycle, "_native_acceptance", self.guard):
            thread = threading.Thread(target=queued_other); thread.start()
            try:
                lifecycle.promote_queued_job(foreign)
                with lifecycle.generation_slot(threading.Lock(), self.job) as admitted:
                    self.assertTrue(admitted)
                    self.job["status"] = "completed"
                self.assertFalse(entered.wait(.15))
                lifecycle.promote_queued_job(foreign)
                self.assertFalse(entered.wait(.15))
            finally:
                lifecycle.request_cancel(foreign)
                thread.join(1)
            self.assertTrue(ended.is_set())
            self.assertFalse(entered.is_set())

    def test_explicit_release_clears_permit_before_same_thread_reuse(self):
        with patch.object(lifecycle, "_native_acceptance", self.guard):
            generation_lock = threading.Lock()
            self.assertTrue(lifecycle.acquire_and_start_generation_slot(generation_lock, self.job))
            self.guard.require_model_admission()
            self.assertTrue(lifecycle.release_generation_slot(generation_lock, self.job))
            with self.assertRaises(NativeAcceptanceReserved):
                self.guard.require_model_admission()
            self.assertFalse(generation_lock.locked())

    def test_startup_ack_is_bound_to_actual_process_and_all_boundaries(self):
        from services import native_acceptance_reservation as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ["app/services/native_acceptance_reservation.py", "app/services/job_lifecycle.py",
                "app/wgp.py", "app/services/llm_service.py", "app/launch.py"]
            actual_root = Path(module.__file__).resolve().parents[2]
            for name in names:
                destination = root / name; destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((actual_root / name).read_bytes())
            attempt = root / ".artifacts-temp" / "attempt"; attempt.mkdir(parents=True, mode=0o700)
            path = attempt / "plan.json"
            plan = {"schema": "maestro/native-acceptance-reservation/v1", "workspace": str(root),
                "agent_id": "cpu-proof", "generation": "cpu-proof07",
                "target": dict(self.guard.target, running_execution_attempt=2),
                "release_action": "resume",
                "guardian_request": "cpu-proof", "guardian_unit": "cpu-proof.service",
                "source_pins": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}}
            path.write_text(json.dumps(plan)); path.chmod(0o600)
            environment = os.environ.copy()
            environment.update(PYTHONPATH=str(root / "app"), MAESTRO_NATIVE_ACCEPTANCE_PLAN=str(path),
                MAESTRO_NATIVE_ACCEPTANCE_PLAN_SHA256=hashlib.sha256(path.read_bytes()).hexdigest())
            code = ("import os,json; from services.native_acceptance_reservation import installed,reservation; "
                "assert reservation.release_action == 'resume'; "
                "installed('queue'); "
                "assert not os.path.exists(os.path.join(os.path.dirname(os.environ['MAESTRO_NATIVE_ACCEPTANCE_PLAN']),'admission-ack.json')); "
                "installed('model'); installed('http'); print(json.dumps({'pid':os.getpid(),'sid':os.getsid(0)}))")
            result = subprocess.run([sys.executable, "-B", "-c", code], cwd=root,
                env=environment, capture_output=True, text=True, timeout=4)
            self.assertEqual(result.returncode, 0, result.stderr)
            identity = json.loads(result.stdout)
            ack = json.loads((attempt / "admission-ack.json").read_text())
            self.assertEqual(ack["pid"], identity["pid"])
            self.assertEqual(ack["session_id"], identity["sid"])
            self.assertEqual(ack["plan_sha256"], environment["MAESTRO_NATIVE_ACCEPTANCE_PLAN_SHA256"])
            self.assertEqual(ack["boundaries"], ["http", "model", "queue"])
            for fault in ["source_changed", "plan_changed", "unsafe_mode", "invalid_release", "invalid_running_attempt", "duplicate_ack"]:
                with self.subTest(fault=fault):
                    source = root / "app/wgp.py"; original = source.read_bytes()
                    original_plan = path.read_bytes()
                    if fault == "source_changed": source.write_bytes(original + b"\n# changed\n")
                    if fault == "plan_changed": path.write_bytes(original_plan + b" ")
                    if fault == "unsafe_mode": path.chmod(0o644)
                    check_environment = environment.copy()
                    if fault == "invalid_release":
                        changed_plan = json.loads(original_plan)
                        changed_plan["release_action"] = "recovery-retry"
                        path.write_text(json.dumps(changed_plan))
                        check_environment["MAESTRO_NATIVE_ACCEPTANCE_PLAN_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    if fault == "invalid_running_attempt":
                        changed_plan = json.loads(original_plan)
                        changed_plan["target"]["running_execution_attempt"] = 3
                        path.write_text(json.dumps(changed_plan))
                        check_environment["MAESTRO_NATIVE_ACCEPTANCE_PLAN_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    try:
                        failed_code = "from services.native_acceptance_reservation import installed; installed('queue'); installed('model'); installed('http')"
                        failed = subprocess.run([sys.executable, "-B", "-c", failed_code], cwd=root,
                            env=check_environment, capture_output=True, text=True, timeout=4)
                        self.assertNotEqual(failed.returncode, 0)
                        self.assertIn("FileExistsError" if fault == "duplicate_ack"
                            else "Native acceptance startup plan did not verify", failed.stderr)
                        self.assertEqual(json.loads((attempt / "admission-ack.json").read_text()), ack)
                    finally:
                        source.write_bytes(original); path.write_bytes(original_plan); path.chmod(0o600)

    def test_http_window_keeps_reads_and_exact_target_controls_only(self):
        for method, path in [("GET", "/api/v1/queue"), ("HEAD", "/ready"),
                ("POST", "/api/v1/queue/retained/start-next"),
                ("POST", "/api/v1/cancel/retained"),
                ("PUT", "/api/v1/access-context/share-url")]:
            self.assertTrue(self.guard.permits_http_action(method, path))
        for method, path in [("POST", "/api/v1/generate"), ("POST", "/api/v1/llm/chat"),
                ("POST", "/api/v1/queue/other/start-next"), ("POST", "/api/v1/queue/resume"),
                ("POST", "/api/v1/cancel/other"),
                ("POST", "/api/v1/queue/retained/recovery-retry"), ("DELETE", "/api/v1/projects/a")]:
            self.assertFalse(self.guard.permits_http_action(method, path))
            self.assertTrue(Reservation().permits_http_action(method, path))

    def test_unready_guardian_blocks_start_and_model_but_keeps_cancel(self):
        ready = False
        guard = Reservation(self.guard.target, dispatch_check=lambda: ready)
        start = "/api/v1/queue/retained/start-next"
        with patch.object(lifecycle, "_native_acceptance", guard):
            with lifecycle.generation_slot(threading.Lock(), self.job):
                self.assertFalse(guard.permits_http_action("POST", start))
                self.assertTrue(guard.permits_http_action("POST", "/api/v1/cancel/retained"))
                with self.assertRaises(NativeAcceptanceReserved): guard.require_model_admission()
                ready = True
                self.assertTrue(guard.permits_http_action("POST", start))
                guard.require_model_admission()
                ready = False
                with self.assertRaises(NativeAcceptanceReserved): guard.require_model_admission()

    def test_resume_window_cannot_repeat_start_next_or_retry(self):
        ready = False
        guard = Reservation(self.guard.target, dispatch_check=lambda: ready, release_action="resume")
        resume = "/api/v1/queue/retained/resume"
        self.assertFalse(guard.permits_http_action("POST", resume))
        self.assertTrue(guard.permits_http_action("POST", "/api/v1/cancel/retained"))
        ready = True
        self.assertTrue(guard.permits_http_action("POST", resume))
        for path in ("/api/v1/queue/retained/start-next", "/api/v1/queue/other/resume",
                "/api/v1/queue/retained/recovery-retry", "/api/v1/queue/resume"):
            self.assertFalse(guard.permits_http_action("POST", path))
        for action in ("retry", "", None, True, ["resume"]):
            with self.subTest(action=action), self.assertRaises(ValueError):
                Reservation(self.guard.target, release_action=action)


class WorkerDelegationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from services import native_acceptance_reservation as guard_module
        cls.guard_module = guard_module
        app = Path(__file__).resolve().parents[1] / "app"
        spec = importlib.util.spec_from_file_location(
            "native_acceptance_cpu_listener", app / "shared/utils/thread_utils.py")
        cls.listener = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.listener)
        tree = ast.parse((app / "launch.py").read_text())
        cls.slot_ast = next(n for n in tree.body if isinstance(n, ast.ClassDef)
            and n.name == "_WgpNativeGpuExecutionSlot")
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name == "_run_generation")
        cls.factory_ast = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef)
            and n.name == "make_error_handler")

    def setUp(self):
        lifecycle._reset_queue_state_for_tests()
        self.job = candidate()
        self.job["params"] = {}
        self.ready = True
        self.guard = self.guard_module.Reservation(dict(identity=self.guard_module.job_identity(self.job),
            execution_attempt=1, recovery_attempt=2), dispatch_check=lambda: self.ready)
        self.lock = threading.Lock()
        native_lock = self.guard.wrap_lock(threading.Lock())
        namespace = dict(wgp=types.SimpleNamespace(native_gpu_execution_lock=native_lock,
            acquire_native_gpu_execution_lock=lambda *args: native_lock.acquire()),
            _WgpNativeGpuWaitCancelled=type("NativeWaitCancelled", (RuntimeError,), {}), _wgp_native_gpu_slot_state=threading.local())
        exec(compile(ast.Module(body=[self.slot_ast], type_ignores=[]), 'actual-native-slot', 'exec'), namespace)
        self.slot = namespace['_WgpNativeGpuExecutionSlot']()
        self.patch = patch.object(lifecycle, '_native_acceptance', self.guard)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        lifecycle._reset_queue_state_for_tests()

    def listener_call(self, operation):
        done = threading.Event()
        failures = []
        def callback():
            try:
                operation()
            except BaseException as error:
                failures.append(error)
            finally:
                done.set()
        self.listener.async_run(callback)
        self.assertTrue(done.wait(3), 'Listener callback stalled')
        if failures:
            raise failures[0]

    def make_handler(self):
        commands, calls = [], []
        def generate_video(task, send_cmd, model_type, plugin_data=None):
            self.guard.require_model_admission()
            calls.append(threading.current_thread())
        ns = dict(inspect=inspect, time=time, traceback=traceback,
            worker_start_lock=threading.Lock(), worker_started=threading.Event(),
            worker_start_state={'cancelled': False}, job=self.job, _gen_lock=self.lock,
            native_slot=self.slot, task_h3_turbo_validation_authorized=False,
            _H3_LONG_STUDIO_MODELS=set(), task_resource_failure={},
            wgp=types.SimpleNamespace(generate_video=generate_video),
            _run_generation_task_with_llm_exclusion=lambda model, send, call: call(),
            _safe_failure_updates=lambda error, job: {'failure_details': str(error)})
        exec(compile(ast.Module(body=[self.factory_ast], type_ignores=[]), 'actual-generation-worker', 'exec'), ns)
        with patch.dict(sys.modules, {'services.native_acceptance_reservation': types.SimpleNamespace(reservation=self.guard)}):
            handler = ns['make_error_handler']({}, {'model_type': 'ltx2_22B'},
                lambda *message: commands.append(message), {})
        return handler, commands, calls, ns

    def test_actual_worker_and_reused_listener_have_scoped_admission(self):
        with lifecycle.generation_slot(self.lock, self.job) as acquired, self.slot as native_acquired:
            self.assertTrue(acquired and native_acquired)
            handler, commands, calls, ns = self.make_handler()
            self.listener_call(handler)
            self.assertEqual(commands, [('exit', None)])
            self.assertEqual(calls, [self.listener.Listener.thread])
            self.assertIsNot(calls[0], threading.current_thread())
            def unrelated():
                with self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                    self.guard.require_model_admission()
                with self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                    self.guard.capture_worker(self.job, self.lock, self.slot)
            self.listener_call(unrelated)
            self.listener_call(handler)  # Same closure cannot replay its capability.
            self.assertEqual(len(calls), 1)
            self.assertEqual([c[0] for c in commands], ['exit', 'error', 'exit'])

    def test_old_capability_cannot_survive_scheduler_release_and_reacquire(self):
        with lifecycle.generation_slot(self.lock, self.job), self.slot:
            token = self.guard.capture_worker(self.job, self.lock, self.slot)
            self.assertTrue(lifecycle.release_generation_slot(self.lock, self.job))
            self.assertTrue(lifecycle.acquire_generation_slot(self.lock, self.job))
            def expired():
                with self.assertRaises(self.guard_module.NativeAcceptanceReserved), self.guard.worker(token):
                    self.fail('Old admission accepted after slot reacquisition')
            self.listener_call(expired)

    def test_worker_revalidates_guardian_identity_and_actual_native_slot(self):
        with lifecycle.generation_slot(self.lock, self.job), self.slot:
            token = self.guard.capture_worker(self.job, self.lock, self.slot)
            def callback():
                with self.guard.worker(token):
                    self.guard.require_model_admission()
                    for key, value in [('execution_attempt', 2), ('recovery_attempt', 3),
                            ('request_manifest', {'sealed': 'changed'}), ('status', 'failed')]:
                        original = self.job[key]
                        try:
                            self.job[key] = value
                            with self.subTest(key=key), self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                                self.guard.require_model_admission()
                        finally:
                            self.job[key] = original
                    self.ready = False
                    with self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                        self.guard.require_model_admission()
                    self.ready = True
                    self.slot.release()
                    with self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                        self.guard.require_model_admission()
                    self.assertTrue(lifecycle.release_generation_slot(self.lock, self.job))
                    self.assertTrue(lifecycle.acquire_generation_slot(self.lock, self.job))
                    with self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                        self.guard.require_model_admission()
                    with self.assertRaises(self.guard_module.NativeAcceptanceReserved):
                        self.slot.acquire()
            self.listener_call(callback)
            self.assertFalse(self.slot._acquired)
            self.listener_call(lambda: self.assertRaises(self.guard_module.NativeAcceptanceReserved,
                self.guard.require_model_admission))

    def test_guardian_loss_reports_error_and_exit_without_model_work(self):
        with lifecycle.generation_slot(self.lock, self.job), self.slot:
            handler, commands, calls, ns = self.make_handler()
            self.ready = False
            self.listener_call(handler)
            self.assertEqual([c[0] for c in commands], ['error', 'exit'])
            self.assertIn('guardian is not ready', commands[0][1])
            self.assertEqual(calls, [])
            self.ready = True
            self.listener_call(lambda: self.assertRaises(self.guard_module.NativeAcceptanceReserved,
                self.guard.require_model_admission))

    def test_cancelled_callback_preserves_exit_and_does_not_consume_model_work(self):
        with lifecycle.generation_slot(self.lock, self.job), self.slot:
            handler, commands, calls, ns = self.make_handler()
            ns['worker_start_state']['cancelled'] = True
            self.listener_call(handler)
            self.assertEqual(commands, [('exit', None)])
            self.assertFalse(ns['worker_started'].is_set())
            self.assertEqual(calls, [])


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux acceptance-only SDK ownership')
class StdioOwnershipTests(unittest.TestCase):
    def setUp(self):
        from services import native_acceptance_reservation as module
        self.module = module
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(module, '_PLAN', {'guardian_request': 'cpu-ownership-fixture'}),
            patch.object(module, '_PLAN_PATH', self.root / 'plan.json'),
            patch.object(module, '_PLAN_HASH', 'a' * 64)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait_file(self, path, process=None):
        deadline = time.monotonic() + 5
        while not path.exists():
            if process is not None and process.poll() is not None:
                self.fail('CPU child exited before publishing ownership')
            if time.monotonic() >= deadline:
                self.fail('CPU ownership deadline')
            time.sleep(.01)
        return json.loads(path.read_text())

    def pidfd_open(self, pid):
        # Pinokio's Python builds omit these wrappers; glibc exposes the same
        # kernel process handles without falling back to a reusable numeric PID.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.pidfd_open
        call.argtypes = [ctypes.c_int, ctypes.c_uint]
        call.restype = ctypes.c_int
        result = call(pid, 0)
        if result < 0:
            raise OSError(ctypes.get_errno(), 'pidfd_open')
        return result

    def pidfd_kill(self, descriptor):
        import ctypes, signal
        call = ctypes.CDLL(None, use_errno=True).pidfd_send_signal
        call.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
        call.restype = ctypes.c_int
        if call(descriptor, signal.SIGKILL, None, 0) < 0:
            raise OSError(ctypes.get_errno(), 'pidfd_send_signal')

    def test_unconfigured_spawn_keeps_original_arguments_and_creates_no_records(self):
        args = ('-c', 'raise SystemExit(0)', '--transport', 'stdio')
        with patch.object(self.module, '_PLAN', None):
            actual, entry = self.module.prepare_stdio_ownership(args)
        self.assertIs(actual, args)
        self.assertIsNone(entry)
        self.assertFalse((self.root / 'stdio').exists())

    def test_duplicate_publication_and_invalid_exit_evidence_preserve_original(self):
        args, entry = self.module.prepare_stdio_ownership(('-c', 'pass'))
        intent = self.module._read_stdio_record(entry / 'intent.json')
        with self.assertRaises(FileExistsError):
            self.module._stdio_record(entry / 'intent.json', {'replacement': True})
        self.assertEqual(self.module._read_stdio_record(entry / 'intent.json'), intent)
        # A dead numeric PID alone cannot turn an unrelated receipt into closure.
        unrelated = {**intent, 'id': 'other-launch', 'pid': 2147483647,
            'sid': 2147483647, 'start_ticks': '1', 'uid': os.getuid(), 'exe': sys.executable}
        unrelated.pop('argv_sha256')
        self.module._stdio_record(entry / 'child.json', unrelated)
        with self.assertRaises(self.module.NativeAcceptanceReserved):
            self.module.finish_stdio_ownership(entry)
        self.assertFalse((entry / 'closed.json').exists())
        (entry / 'child.json').unlink()
        (entry / 'child.json').symlink_to(entry / 'intent.json')
        with self.assertRaises(OSError):
            self.module.finish_stdio_ownership(entry)
        self.assertFalse((entry / 'closed.json').exists())

    def test_real_child_requires_exact_ack_before_application_contact_and_exit(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                contact = self.root / ('contact-' + str(valid))
                args, entry = self.module.prepare_stdio_ownership(
                    ('-c', 'from pathlib import Path; Path(' + repr(str(contact)) + ').touch()'))
                intent = json.loads((entry / 'intent.json').read_text())
                self.assertEqual(intent['argv_sha256'], self.module.digest(list(args)))
                child = subprocess.Popen([sys.executable, *args], start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    receipt = self.wait_file(entry / 'child.json', child)
                    self.assertEqual((receipt['pid'], receipt['sid']), (child.pid, child.pid))
                    self.module.finish_stdio_ownership(entry)
                    self.assertFalse((entry / 'closed.json').exists())
                    self.assertFalse(contact.exists())
                    ack = dict(receipt)
                    if not valid:
                        ack['start_ticks'] = 'wrong-birth'
                    self.module._stdio_record(entry / 'ack.json', ack)
                    self.assertEqual(child.wait(timeout=5), 0 if valid else 1)
                    self.assertEqual(contact.exists(), valid)
                    self.module.finish_stdio_ownership(entry)
                    self.assertEqual(json.loads((entry / 'closed.json').read_text()), receipt)
                finally:
                    if child.poll() is None:
                        child.kill(); child.wait(timeout=5)

    def test_parent_death_between_setsid_and_exec_preserves_unresolved_intent(self):
        # A real SDK-shaped POSIX launch pauses after setsid but before exec.
        # Killing the parent leaves the child outside its SID, without marked
        # argv yet. The pre-spawn intent must survive that exact race.
        contact = self.root / 'orphan-contact'
        event = self.root / 'pre-exec.json'
        script = '''import os,json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from services import native_acceptance_reservation as m
root=Path(sys.argv[2]); m._PLAN={'guardian_request':'cpu-ownership-fixture'}
m._PLAN_PATH=root/'plan.json'; m._PLAN_HASH='a'*64
args,entry=m.prepare_stdio_ownership(('-c','from pathlib import Path; Path('+repr(str(root/'orphan-contact'))+').touch()'))
def pause():
    fields=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
    m._stdio_record(root/'pre-exec.json',{'pid':os.getpid(),'sid':os.getsid(0),'start_ticks':fields[19]})
    time.sleep(1)
subprocess.Popen([sys.executable,*args],start_new_session=True,preexec_fn=pause,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(30)
'''
        parent = subprocess.Popen([sys.executable, '-c', script,
            str(Path(__file__).resolve().parents[1] / 'app'), str(self.root)],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        orphan_fd = None
        try:
            value = self.wait_file(event, parent)
            self.assertEqual(value['pid'], value['sid'])
            self.assertNotEqual(value['sid'], parent.pid)
            orphan_fd = self.pidfd_open(value['pid'])
            entry, = (self.root / 'stdio').iterdir()
            self.assertTrue((entry / 'intent.json').is_file())
            self.assertFalse((entry / 'child.json').exists())
            parent.kill(); parent.wait(timeout=5)
            self.module.finish_stdio_ownership(entry)
            self.assertFalse((entry / 'closed.json').exists())
            receipt = self.wait_file(entry / 'child.json')
            self.assertEqual((receipt['pid'], receipt['start_ticks']),
                (value['pid'], value['start_ticks']))
            self.assertFalse(contact.exists())
            self.assertFalse((entry / 'ack.json').exists())
        finally:
            if parent.poll() is None:
                parent.kill(); parent.wait(timeout=5)
            if orphan_fd is not None:
                import select
                self.pidfd_kill(orphan_fd)
                self.assertTrue(select.select([orphan_fd], [], [], 5)[0])
                os.close(orphan_fd)
        self.module.finish_stdio_ownership(entry)
        self.assertTrue((entry / 'closed.json').is_file())
        self.assertFalse(contact.exists())


if __name__ == "__main__":
    unittest.main()
