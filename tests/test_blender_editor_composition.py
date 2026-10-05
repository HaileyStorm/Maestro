"""Composition queue publication and recovery with actual CPU FFmpeg.

Native Blender/GPU, project authentication and unrelated model/credit guards
are fixtures. These tests prove queue/manifest/finality behavior, not native
rendering or live access acceptance.
"""
from __future__ import annotations
import sys
import unittest
import subprocess
import shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import ast, asyncio, copy, contextvars, hashlib, hmac, json, math, os, threading, time, types, uuid
from contextlib import nullcontext, contextmanager
from pathlib import Path
import tempfile
from unittest import mock
from fastapi import HTTPException
from services import blender_mcp_service, blender_mcp_transport, composition_worker, job_lifecycle as lifecycle
from services.composition_package import digest, CompositionError
from services.composition_worker import prepare_delivery, publish_delivery, delivery_paths
from services.editor_projects import probe_media
from services.output_access import output_policy_from_request
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import QueueRecoveryCoordinator, owner_principal_digest, project_instance_digest
from services.queue_recovery_runtime import atomic_write_request_manifest, load_request_manifest, remove_request_manifest, QueueRecoveryRuntimeError
from services.blender_mcp_service import BlenderMCPService
TREE = ast.parse((ROOT / "app/launch.py").read_text())
def raw_package():
    segments = []
    for sid in ["A", "B"]:
        segments.append(
            {
                "id": sid,
                "scene": {
                    "clear_scene": True,
                    "objects": [{"name": sid, "primitive": "cube"}],
                },
                "animation": {
                    "frame_start": 0,
                    "frame_end": 23,
                    "objects": [
                        {
                            "name": sid,
                            "keyframes": [
                                {"frame": 0, "location": [0, 0, 0]},
                                {"frame": 23, "location": [1, 0, 0]},
                            ],
                        }
                    ],
                },
                "fps": 24,
                "width": 128,
                "height": 72,
            }
        )
    return {
        "schema": "maestro/composition/v1",
        "id": "composition-queue-fixture",
        "canvas": {"width": 128, "height": 72, "fps": 24},
        "audio": {"mode": "silence", "sample_rate": 48000},
        "segments": segments,
        "clips": [
            {"id": "use-A-1", "segment_id": "A", "source_frame": 6, "frame_count": 12},
            {"id": "use-B", "segment_id": "B", "source_frame": 0, "frame_count": 24},
            {"id": "use-A-2", "segment_id": "A", "source_frame": 0, "frame_count": 18},
        ],
    }
SECRET = b'private-cpu-queue-fixture-signing-key-64bytes-not-production-key'

class Boundary(BlenderMCPService):
    invocations = []

    def __init__(self, client, root):
        super().__init__(object(), root)

    def invoke(self, tool, args, *, cancelled):
        Boundary.invocations.append((tool, copy.deepcopy(args)))
        if cancelled():
            raise InterruptedError('cancelled')
        if tool == 'render_animation':
            color = 'red' if args['output_path'] == 'A.mp4' else 'blue'
            path = self.project_root / args['output_path']
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i', f"color={color}:s={args['width']}x{args['height']}:r={args['fps']}", '-frames:v', str(args['frame_end'] - args['frame_start'] + 1), '-an', '-c:v', 'libx264', '-threads', '1', str(path)], check=True)
        return {'status': 'ok'}

class Request:

    def __init__(self, body):
        self.body = body
        self.state = types.SimpleNamespace(maestro_session_id='fixture-owner')

    async def stream(self):
        yield json.dumps(self.body).encode()

class NativeClient:
    closes = 0

    def __init__(self, **kwargs):
        pass

    def close(self):
        NativeClient.closes += 1

class NativeSlot:
    admissions = 0

    def __init__(self, **kwargs):
        self.check = kwargs.get('cancel_checkpoint')

    def __enter__(self):
        if self.check:
            self.check()
        NativeSlot.admissions += 1
        return True

    def __exit__(self, *args):
        pass

def functions(namespace, names):
    selected = []
    for node in TREE.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in names
        ):
            node = copy.deepcopy(node)
            if hasattr(node, "decorator_list"):
                node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == set(names)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
            "private-launch-overlay",
            "exec",
        ),
        namespace,
    )

def environment(root):
    project = root / "scene"
    project.mkdir(exist_ok=True)
    project_digest = project_instance_digest(SECRET, "a" * 32)
    coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(root / "queue.jsonl"))
    namespace = {
        "os": os,
        "json": json,
        "hmac": hmac,
        "hashlib": hashlib,
        "math": math,
        "copy": copy,
        "time": time,
        "uuid": uuid,
        "threading": threading,
        "Request": Request,
        "HTTPException": HTTPException,
        "QueueRecoveryRuntimeError": QueueRecoveryRuntimeError,
        "_request_remote": contextvars.ContextVar(
            "private-fixture-remote", default=True
        ),
        "_request_session_id": contextvars.ContextVar(
            "private-fixture-session", default="fixture-owner"
        ),
        "_workspace_lifecycle_lock": threading.RLock(),
        "_gen_lock": threading.Lock(),
        "_blender_scene_lock": threading.RLock(),
        "_queue_recovery_checkpoint_lock": threading.RLock(),
        "_active_gen_states": {},
        "_session_secret": lambda: SECRET,
        "_SAMPLE_CAMPAIGN_JOB_KIND": "sample_campaign_fixture_unused",
        "output_policy_from_request": output_policy_from_request,
        "owner_principal_digest": owner_principal_digest,
        "atomic_write_request_manifest": atomic_write_request_manifest,
        "load_request_manifest": load_request_manifest,
        "remove_request_manifest": remove_request_manifest,
        "_queue_recovery_coordinator": coordinator,
        "_queue_recovery_with_bounded_compaction": lambda callback: callback(),
        "_queue_recovery_project_identity": lambda *args: project_digest,
        "_queue_recovery_existing_project_identity": lambda *args: project_digest,
        "_existing_workspace_dir": lambda workspace: (
            str(project)
            if workspace == "scene"
            else (_ for _ in []).throw(ValueError("Wrong project"))
        ),
        "_reserve_workspace_operations": lambda *args: nullcontext(),
        "_output_lineage_mutation_guard": lambda *args: nullcontext(),
        "_require_project_access": lambda request, workspace, **kwargs: (
            str(project)
            if workspace == "scene"
            else (_ for _ in []).throw(HTTPException(403))
        ),
        "_require_job_workspace_available": lambda job: None,
        "_require_job_model_recipe_terms": lambda job: None,
        "_seal_h3_offload_plan_for_job": lambda *args, **kwargs: None,
        "_stamp_h3_lightx2v_recovery_identity": lambda *args: None,
        "_credit_prepare_submission": lambda job: None,
        "_stamp_requested_generation_residency": lambda job: None,
        "_stamp_job_origin": lambda job: job,
        "_queue_recovery_input_descriptors": lambda *args: [],
        "_blender_runtime_info": lambda: {"version": "5.1.2"},
        "_blender_checkout_root": lambda: "fixture-unused",
        "_require_blender_ready": lambda: None,
        "_new_generation_job_id": lambda: uuid.uuid4().hex,
        "_WgpNativeGpuExecutionSlot": NativeSlot,
        "_WgpNativeGpuWaitCancelled": InterruptedError,
        "generation_slot": lifecycle.generation_slot,
        "try_start": lifecycle.try_start,
        "finish_job": lifecycle.finish_job,
        "is_cancel_requested": lifecycle.is_cancel_requested,
        "register_abort_state": lifecycle.register_abort_state,
        "unregister_abort_state": lifecycle.unregister_abort_state,
        "checkpoint_recovery_job": lifecycle.checkpoint_recovery_job,
        "durable_queue_state": lifecycle.durable_queue_state,
    }
    names = [
        "_JobRegistry",
        "_editor_request_body",
        "_queue_recovery_register_and_publish",
        "_queue_recovery_worker",
        "_queue_recovery_checkpoint",
        "_queue_recovery_materialize_job",
        "_queue_recovery_reconcile_cursor",
        "_run_tool_editor_export",
        "submit_blender_editor_composition",
    ]
    names.extend(
        node.name
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_composition_")
    )
    names.append("_run_tool_composition_export")
    functions(namespace, names)
    namespace["_jobs"] = namespace["_JobRegistry"]()
    lifecycle.configure_durability_hook(None)
    lifecycle.configure_durability_hook(coordinator.prospective_transition)
    return namespace, coordinator, project, project_digest

def new_job(ns):
    # Exercise the actual HTTP route, registry, manifest and coordinator, while
    # retaining worker start for deterministic observation in this fixture.
    actual = ns["_queue_recovery_register_and_publish"]
    ns["_queue_recovery_register_and_publish"] = lambda job, **kwargs: actual(
        job, **kwargs, defer_worker=True
    )
    try:
        result = asyncio.run(
            ns["submit_blender_editor_composition"](
                "scene",
                Request({"package": raw_package(), "private_output": True}),
            )
        )
    finally:
        ns["_queue_recovery_register_and_publish"] = actual
    job = ns["_jobs"][result["job_id"]]
    assert (
        job["private"] and job["source_remote"] and job["kind"] == "tool_editor_export"
    )
    return job

def restore(ns, root, project, digest):
    lifecycle.configure_durability_hook(None)
    coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(root / "queue.jsonl"))
    lifecycle.configure_durability_hook(coordinator.prospective_transition)
    snapshot = next(iter(coordinator.restore().jobs.values()))
    ns["_queue_recovery_coordinator"] = coordinator
    job, resume = ns["_queue_recovery_materialize_job"](
        snapshot, {"scene": (str(project), digest)}
    )
    ns["_jobs"][job["id"]] = job
    return job, resume

class BlenderEditorCompositionPackageTests(unittest.TestCase):
    def test_repeated_segment_instances_have_distinct_ids_and_final_frame_clock(self):
        from services.composition_package import normalize_package
        with tempfile.TemporaryDirectory() as temporary:
            service = BlenderMCPService(object(), temporary)
            package = normalize_package(raw_package(), service)
            self.assertEqual(package["frame_count"], 54)
            self.assertEqual([clip["segment_id"] for clip in package["clips"]], ["A", "B", "A"])
            self.assertEqual(len({clip["id"] for clip in package["clips"]}), 3)
            self.assertEqual(len(package["segments"]), 2)

    def test_closed_package_rejects_unsupported_shapes_and_frame_references_before_native_use(self):
        from services.composition_package import normalize_package
        with tempfile.TemporaryDirectory() as temporary:
            service = BlenderMCPService(object(), temporary)
            cases = []
            def changed(section, key, value):
                package = raw_package()
                target = package if section is None else package[section]
                if isinstance(target, list): target = target[0]
                target[key] = value
                cases.append(package)
            changed(None, "code", "arbitrary code")
            changed("canvas", "fps", True)
            changed("canvas", "fps", 121)
            changed("segments", "fps", 241)
            changed("segments", "width", 127)
            changed("segments", "scene", {"clear_scene": False, "objects": []})
            changed("clips", "segment_id", "missing")
            changed("clips", "source_frame", 23)
            changed("clips", "frame_count", True)
            changed("clips", "id", "use-B")
            for index, package in enumerate(cases):
                with self.subTest(case=index), self.assertRaises(ValueError):
                    normalize_package(package, service)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg is required")
class BlenderEditorCompositionQueueTests(unittest.TestCase):

    def test_prepared_delivery_rejects_changed_bytes_binding_and_signature_without_native_resend(self):
        from services.composition_worker import load_delivery
        with tempfile.TemporaryDirectory() as temporary:
            ns, coordinator, project, pdigest = environment(Path(temporary))
            job = new_job(ns)
            package, directory, output_root, binding = ns["_composition_job_context"](job)
            prepare_delivery(package, Boundary(object(), directory), directory, output_root,
                binding=binding, secret=SECRET, probe=probe_media, cancelled=lambda: False,
                policy=job["access_policy"], native_context=lambda: nullcontext())
            before = len(Boundary.invocations)
            media = delivery_paths(directory, output_root, binding)[0][0]
            original = media.read_bytes()
            media.write_bytes(b"replaced")
            with self.assertRaises(CompositionError):
                load_delivery(package, directory, output_root, binding=binding, secret=SECRET)
            media.write_bytes(original)
            wrong_binding = {**binding, "owner_digest": "different-owner"}
            with self.assertRaises(CompositionError):
                load_delivery(package, directory, output_root, binding=wrong_binding, secret=SECRET)
            receipt = directory / "delivery.receipt.json"
            unsigned = json.loads(receipt.read_text()); unsigned["mac"] = "0" * 64
            receipt.write_text(json.dumps(unsigned))
            with self.assertRaises(CompositionError):
                load_delivery(package, directory, output_root, binding=binding, secret=SECRET)
            self.assertEqual(len(Boundary.invocations), before)

    def setUp(self):
        Boundary.invocations = []
        NativeSlot.admissions = 0
        NativeClient.closes = 0
        lifecycle.configure_durability_hook(None)
        self.addCleanup(lifecycle.configure_durability_hook, None)
        service_patch = mock.patch.object(blender_mcp_service, 'BlenderMCPService', Boundary)
        client_patch = mock.patch.object(blender_mcp_transport, 'StdioBlenderMCPClient', NativeClient)
        service_patch.start()
        self.addCleanup(service_patch.stop)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def test_registered_private_worker_completed_and_terminal_after_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            ns, coordinator, project, pdigest = environment(Path(temp))
            job = new_job(ns)
            assert ns['_queue_recovery_worker'](job) == ns['_run_tool_editor_export']
            before = len(Boundary.invocations)
            assert ns['_run_tool_editor_export'](job['id']) and job['status'] == 'completed'
            final = project / job['output_files'][0]
            media = probe_media(str(final))
            assert media['duration'] == 2.25 and media['width'] == 128 and (media['height'] == 72) and media['has_audio']
            meta = json.loads(final.with_suffix('.meta.json').read_text())
            assert meta['private'] and meta['params'] is None and (len(meta['transform']['clips']) == 3)
            assert len(Boundary.invocations) - before == 6 and NativeSlot.admissions == 1
            self.assertEqual(NativeClient.closes, 1)
            reopened, resume = restore(ns, Path(temp), project, pdigest)
            assert reopened['status'] == 'completed' and (not resume)

    def test_sealed_delivery_crash_gaps_adopt_without_blender_or_native_resend(self):
        for gap in ['sidecar_only', 'media_before_terminal']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ns, coordinator, project, pdigest = environment(root)
                job = new_job(ns)
                assert lifecycle.try_start(job, phase='Rendering')
                package, directory, output_root, binding = ns['_composition_job_context'](job)
                service = Boundary(object(), directory)
                prepare_delivery(package, service, directory, output_root, binding=binding, secret=SECRET, probe=probe_media, cancelled=lambda: False, policy=job['access_policy'], native_context=lambda: nullcontext())

                class Crash(BaseException):
                    pass

                def crash(stage):
                    if stage == ('metadata_published' if gap == 'sidecar_only' else 'media_published'):
                        raise Crash()
                try:
                    publish_delivery(package, directory, output_root, binding=binding, secret=SECRET, cancelled=lambda: False, finish=lambda name: (_ for _ in []).throw(AssertionError('terminal reached before injected crash')), event=crash)
                except Crash:
                    pass
                else:
                    raise AssertionError('crash gap not reached')
                if gap == 'media_before_terminal':

                    def unavailable():
                        raise RuntimeError('substituted unavailable Blender')
                    ns['_require_blender_ready'] = unavailable
                    ns['_blender_runtime_info'] = unavailable
                reopened, resume = restore(ns, root, project, pdigest)
                assert resume and reopened['status'] == 'queued'
                before = len(Boundary.invocations)
                admissions = NativeSlot.admissions
                assert ns['_run_tool_editor_export'](reopened['id']) and reopened['status'] == 'completed'
                assert len(Boundary.invocations) == before and NativeSlot.admissions == admissions

    def test_later_unknown_segment_holds_before_earlier_segment_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            package, directory, output_root, binding = ns['_composition_job_context'](job)
            from services.composition_package import atomic_json, sign_receipt
            second = package['segments'][1]
            atomic_json(directory / 'B.receipt.json', sign_receipt({'package_sha256': digest(package), 'binding': binding, 'blender_mcp_revision': blender_mcp_service.PINNED_INSTALL.revision, 'segment_id': 'B', 'segment_sha256': digest(second), 'state': 'attempting', 'attempt_id': uuid.uuid4().hex}, SECRET))
            before = len(Boundary.invocations)
            reopened, resume = restore(ns, root, project, pdigest)
            assert not resume and reopened['queue_held'] and (len(Boundary.invocations) == before)

    def test_cancel_all_execution_phases_survives_fresh_journal(self):
        for phase in ['native_wait', 'native_scene', 'cpu_assembly', 'publication']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ns, coordinator, project, pdigest = environment(root)
                job = new_job(ns)
                before = len(Boundary.invocations)

                def cancel():
                    lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                if phase == 'native_wait':

                    class CancelSlot(NativeSlot):

                        def __enter__(self):
                            cancel()
                            return super().__enter__()
                    ns['_WgpNativeGpuExecutionSlot'] = CancelSlot
                    patch = nullcontext()
                elif phase == 'native_scene':
                    original = Boundary.invoke

                    def invoke(self, tool, args, *, cancelled):
                        result = original(self, tool, args, cancelled=cancelled)
                        if tool == 'scene_create':
                            cancel()
                        return result
                    patch = mock.patch.object(Boundary, 'invoke', invoke)
                elif phase == 'cpu_assembly':
                    original = composition_worker.render_video_sequence

                    def render(*args, **kwargs):
                        result = original(*args, **kwargs)
                        cancel()
                        return result
                    patch = mock.patch.object(composition_worker, 'render_video_sequence', render)
                else:
                    checkpoint = ns['_queue_recovery_checkpoint']

                    def transition(job, **kwargs):
                        if ((kwargs.get('recovery_cursor') or {}).get('composition') or {}).get('stage') == 'media_published':
                            cancel()
                        return checkpoint(job, **kwargs)
                    ns['_queue_recovery_checkpoint'] = transition
                    patch = nullcontext()
                with patch:
                    assert not ns['_run_tool_editor_export'](job['id'])
                assert job['status'] == 'cancelled' and (not job['output_files']) and (not ns['_active_gen_states'])
                assert not list(project.glob('composition_*.mp4')) and (not list(project.glob('composition_*.meta.json')))
                if phase == 'native_wait':
                    assert len(Boundary.invocations) == before
                reopened, resume = restore(ns, root, project, pdigest)
                assert reopened['status'] == 'cancelled' and (not resume)

    def test_scene_owner_can_acquire_generation_lock_while_worker_waits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            failed_attempt = threading.Event()
            lock = threading.RLock()

            class SceneLock:

                def acquire(self, **kwargs):
                    result = lock.acquire(**kwargs)
                    if not result and kwargs.get('blocking') is False:
                        failed_attempt.set()
                    return result

                def release(self):
                    lock.release()
            ns['_blender_scene_lock'] = SceneLock()
            lock.acquire()
            outcome = []
            worker = threading.Thread(
                target=lambda: outcome.append(ns['_run_tool_editor_export'](job['id'])),
                daemon=True,
            )
            ownership_proved = False
            try:
                worker.start()
                assert failed_attempt.wait(2), 'worker did not attempt the owned scene lock'
                acquired = ns['_gen_lock'].acquire(timeout=2)
                try:
                    assert acquired, 'composition held Gen while waiting for Scene'
                finally:
                    if acquired:
                        ns['_gen_lock'].release()
                ownership_proved = True
            finally:
                lock.release()
                if not ownership_proved:
                    lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                if worker.ident is not None:
                    worker.join(5)
                    if worker.is_alive():
                        lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                        worker.join(2)
            assert not worker.is_alive() and outcome == [True] and (job['status'] == 'completed')

    def test_missing_manifest_cannot_borrow_generic_adoption_or_revive_terminal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            snapshot = next(iter(coordinator.restore().jobs.values()))
            snapshot['request_manifest'] = {'invalid': 'missing'}
            ns['_queue_recovery_final_adoption_jobs'] = {('scene', job['id']): {'state': 'adopted', 'declared': 1, 'adopted': 1, 'missing': 0, 'quarantined': 0, 'output_files': ['forged.mp4']}}
            reopened, resume = ns['_queue_recovery_materialize_job'](snapshot, {'scene': (str(project), pdigest)})
            assert reopened['queue_held'] and (not resume) and (not reopened['output_files'])
            for terminal in ['completed', 'failed', 'cancelled']:
                snapshot['status'] = terminal
                reopened, resume = ns['_queue_recovery_materialize_job'](snapshot, {'scene': (str(project), pdigest)})
                assert reopened['status'] == terminal and (not resume)

    def test_cancel_rollback_occurs_before_generation_and_scene_ownership_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)
            original = composition_worker.rollback_delivery
            observed = []

            def rollback(*args, **kwargs):
                assert ns['_gen_lock'].locked() and ns['_blender_scene_lock']._is_owned()
                observed.append('rollback-under-generation-and-scene-ownership')
                return original(*args, **kwargs)

            def cancel_finish(*args, **kwargs):
                lifecycle.request_cancel(job, job_id=job['id'], active_states=ns['_active_gen_states'])
                return False
            ns['finish_job'] = cancel_finish
            with mock.patch.object(composition_worker, 'rollback_delivery', rollback):
                assert not ns['_run_tool_editor_export'](job['id'])
            assert observed and job['status'] == 'cancelled' and (not list(project.glob('composition_*')))

    def test_old_worker_preserves_newer_completed_registry_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ns, coordinator, project, pdigest = environment(root)
            job = new_job(ns)

            def newer_winner(*args, **kwargs):
                newer = copy.deepcopy(job)
                newer['execution_attempt'] += 1
                newer['status'] = 'completed'
                newer['output_files'] = ['composition_' + job['id'] + '.mp4']
                ns['_jobs'][job['id']] = newer
                return False
            ns['finish_job'] = newer_winner
            assert not ns['_run_tool_editor_export'](job['id'])
            assert ns['_jobs'][job['id']]['status'] == 'completed' and len(list(project.glob('composition_*'))) == 2

    def test_old_exception_cannot_hold_newer_same_object_attempt(self):
        for error_type in [CompositionError, RuntimeError]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                ns, coordinator, project, pdigest = environment(root)
                job = new_job(ns)

                @contextmanager
                def changed_attempt(lock, job):
                    try:
                        with lifecycle.generation_slot(lock, job) as acquired:
                            yield acquired
                    finally:
                        assert lifecycle.checkpoint_recovery_job(job, expected_execution_attempt=job['execution_attempt'], execution_attempt=job['execution_attempt'] + 1, status='running', queue_held=False)
                ns['generation_slot'] = changed_attempt

                def unavailable(*args, **kwargs):
                    raise error_type('substituted pre-native evidence failure')
                ns['_composition_job_context'] = unavailable
                before = len(Boundary.invocations)
                assert not ns['_run_tool_editor_export'](job['id'])
                assert job['status'] == 'running' and job['execution_attempt'] == 3 and (not job['queue_held'])
                assert len(Boundary.invocations) == before
