"""Model-free CPU text coexistence, fencing, and privacy regressions."""
from __future__ import annotations

import ast
import os
import sys
import threading
import time
import types
import unittest
from pathlib import Path


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.cpu_text_lane import (  # noqa: E402
    BreakEvenEstimate,
    CPUTextLane,
    GIB,
    HostAdmissionSnapshot,
    PreemptionGate,
    host_admission_decision,
    should_preempt_cpu_attempt,
)
from services.job_lifecycle import (  # noqa: E402
    _reset_queue_state_for_tests,
    acquire_generation_slot,
    block_resource_admission_failure,
    finish_job,
    generation_slot,
    request_cancel,
    resource_descriptor,
    transition_resource_execution,
    try_start,
    update_job,
)
from services.queue_recovery_adapter import (  # noqa: E402
    QueueRecoveryAdapterError,
    owner_principal_digest,
    project_instance_digest,
    serialize_job,
)


def _snapshot(*, available=48 * GIB, threads=24, workers=8, cpu=20.0):
    return HostAdmissionSnapshot(
        available_bytes=available,
        total_bytes=64 * GIB,
        required_bytes=16 * GIB,
        logical_threads=threads,
        worker_threads=workers,
        cpu_percent=cpu,
    )


def _resource_job(status="running"):
    return {
        "id": "cpu-job",
        "status": status,
        "message": "Working",
        "resource_intent": "text",
        "resource_execution": "cpu",
        "preemption_mode": "discard_restart",
        "resource_state": "running",
        "execution_attempt": 1,
    }


class TestCPUTextLane(unittest.TestCase):
    def setUp(self):
        _reset_queue_state_for_tests()

    def test_host_admission_requires_ram_threads_and_cpu_headroom(self):
        self.assertTrue(host_admission_decision(_snapshot()).admitted)
        self.assertEqual(
            host_admission_decision(_snapshot(available=20 * GIB)).reason,
            "memory_pressure",
        )
        self.assertEqual(
            host_admission_decision(_snapshot(threads=11)).reason,
            "thread_pressure",
        )
        self.assertEqual(
            host_admission_decision(_snapshot(cpu=90.0)).reason,
            "cpu_pressure",
        )
        lane = CPUTextLane()
        lease, decision = lane.acquire(
            "measurement-failure",
            snapshot_supplier=lambda: (_ for _ in ()).throw(
                RuntimeError("synthetic measurement failure")
            ),
            timeout=0.01,
        )
        self.assertIsNone(lease)
        self.assertEqual(decision.reason, "memory_pressure")

    def test_exactly_one_cpu_lane_runs_while_synthetic_gpu_lock_is_held(self):
        gpu_lock = threading.Lock()
        gpu_lock.acquire()
        lane = CPUTextLane()
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        active = 0
        peak = 0
        guard = threading.Lock()

        def worker(owner, entered, release=None):
            nonlocal active, peak
            lease, decision = lane.acquire(
                owner,
                snapshot_supplier=_snapshot,
                timeout=2.0,
            )
            self.assertTrue(decision.admitted)
            self.assertIsNotNone(lease)
            with lease:
                with guard:
                    active += 1
                    peak = max(peak, active)
                entered.set()
                if release is not None:
                    self.assertTrue(release.wait(1.0))
                with guard:
                    active -= 1

        first = threading.Thread(
            target=worker, args=("first", first_entered, release_first),
        )
        second = threading.Thread(
            target=worker, args=("second", second_entered),
        )
        first.start()
        self.assertTrue(first_entered.wait(1.0))
        second.start()
        self.assertFalse(second_entered.wait(0.1))
        self.assertTrue(gpu_lock.locked())
        release_first.set()
        self.assertTrue(second_entered.wait(1.0))
        first.join(1.0)
        second.join(1.0)
        gpu_lock.release()
        self.assertEqual(peak, 1)
        self.assertEqual(lane.aggregate_snapshot(), {
            "cpu_text_running": 0,
            "cpu_text_waiting": 0,
        })

    def test_runtime_token_clear_is_lease_fenced(self):
        lane = CPUTextLane()
        first, _ = lane.acquire("same", snapshot_supplier=_snapshot)
        lane.bind_runtime_tokens(
            first, runtime_generation=3, runtime_attempt_id=4,
        )
        first.release()
        replacement, _ = lane.acquire("same", snapshot_supplier=_snapshot)
        expected = lane.bind_runtime_tokens(
            replacement, runtime_generation=5, runtime_attempt_id=6,
        )
        lane.clear_runtime_tokens(first)
        self.assertEqual(lane.runtime_tokens("same"), expected)
        replacement.release()

    def test_break_even_is_fail_closed_and_near_completion_is_not_preempted(self):
        self.assertFalse(should_preempt_cpu_attempt(BreakEvenEstimate(
            None, 5.0, 10.0, 10.0,
        )))
        self.assertFalse(should_preempt_cpu_attempt(BreakEvenEstimate(
            30.0, 5.0, 10.0, 10.0,
        )))
        self.assertTrue(should_preempt_cpu_attempt(BreakEvenEstimate(
            90.0, 5.0, 10.0, 10.0,
        )))

    def test_preemption_gate_bounds_restart_and_cooldown(self):
        estimate = BreakEvenEstimate(90.0, 5.0, 10.0, 10.0)
        gate = PreemptionGate(maximum_restarts=2, cooldown_seconds=60.0)
        self.assertTrue(gate.permits("job", estimate, now=100.0))
        gate.record("job", now=100.0)
        self.assertFalse(gate.permits("job", estimate, now=120.0))
        self.assertTrue(gate.permits("job", estimate, now=161.0))
        gate.record("job", now=161.0)
        self.assertFalse(gate.permits("job", estimate, now=300.0))

    def test_delayed_abort_abandonment_releases_gpu_reservation(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_preemption_monitor"
        )
        gpu_lock = threading.Lock()
        abort_entered = threading.Event()
        release_abort = threading.Event()
        restored = []
        token = types.SimpleNamespace(
            runtime_generation=7, runtime_attempt_id=11,
        )

        class NeverStopped:
            @staticmethod
            def wait(_timeout):
                return False

            @staticmethod
            def is_set():
                return False

        class Lane:
            @staticmethod
            def runtime_tokens(_owner):
                return token

        class Gate:
            @staticmethod
            def permits(_owner, _estimate):
                return True

            @staticmethod
            def record(_owner):
                raise AssertionError("an abandoned handoff must not be recorded")

        class Runtime:
            @staticmethod
            def get_local_runtime_control():
                return {
                    "execution": "cooperative_cpu",
                    "preemptible": True,
                    "generation": 7,
                    "attempt_id": 11,
                    "remaining_estimate_seconds": 100.0,
                }

            @staticmethod
            def abort_local_cpu_runtime(*_args, **_kwargs):
                abort_entered.set()
                self.assertTrue(release_abort.wait(2.0))
                return {"resources_released": True}

        namespace = {
            "threading": threading,
            "_cpu_text_job_cancelled": lambda _job: False,
            "_cpu_text_lane": Lane(),
            "_cpu_text_runtime_preemption_eligible": (
                lambda _control, _tokens: True
            ),
            "_cpu_text_break_even_estimate": lambda *_args, **_kwargs: (
                BreakEvenEstimate(100.0, 1.0, 1.0, 1.0)
            ),
            "_cpu_text_preemption_gate": Gate(),
            "_cpu_text_generation_waiter_exists": lambda _job: False,
            "_gen_lock": gpu_lock,
            "_cpu_text_transition": lambda *_args, **_kwargs: 1,
            "_cpu_text_restore_after_abandoned_preemption": (
                lambda _job, attempt: restored.append(attempt)
            ),
            "RESOURCE_EXECUTION_CPU": "cpu",
        }
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), "cpu-monitor", "exec"),
            namespace,
        )
        abandoned = threading.Event()
        done = threading.Event()
        monitor = threading.Thread(
            target=namespace["_cpu_text_preemption_monitor"],
            kwargs={
                "llm_service": Runtime(),
                "selection": {},
                "job": _resource_job(),
                "owner_key": "job",
                "lease": object(),
                "execution_attempt": 1,
                "stopped": NeverStopped(),
                "outcome": {},
                "handoff_ready": threading.Event(),
                "handoff_acknowledged": threading.Event(),
                "handoff_abandoned": abandoned,
                "handoff_transferred": threading.Event(),
                "monitor_done": done,
            },
        )
        monitor.start()
        self.assertTrue(abort_entered.wait(1.0))
        self.assertFalse(gpu_lock.acquire(blocking=False))
        abandoned.set()
        release_abort.set()
        monitor.join(2.0)
        self.assertFalse(monitor.is_alive())
        self.assertTrue(done.is_set())
        self.assertEqual(restored, [1])
        self.assertTrue(gpu_lock.acquire(blocking=False))
        gpu_lock.release()

    def test_runtime_token_change_restores_nonpreemptible_projection(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_preemption_monitor"
        )
        gpu_lock = threading.Lock()
        original = types.SimpleNamespace(
            runtime_generation=7, runtime_attempt_id=11,
        )
        replacement = types.SimpleNamespace(
            runtime_generation=8, runtime_attempt_id=12,
        )
        token_reads = []
        restored = []

        class NeverStopped:
            @staticmethod
            def wait(_timeout):
                return False

            @staticmethod
            def is_set():
                return False

        class Lane:
            @staticmethod
            def runtime_tokens(_owner):
                token_reads.append(True)
                return original if len(token_reads) == 1 else replacement

        class Runtime:
            @staticmethod
            def get_local_runtime_control():
                return {"execution": "cooperative_cpu"}

            @staticmethod
            def abort_local_cpu_runtime(*_args, **_kwargs):
                raise AssertionError("replacement tokens must fence abort")

        class Gate:
            @staticmethod
            def permits(_owner, _estimate):
                return True

        done = threading.Event()
        outcome = {}
        namespace = {
            "threading": threading,
            "_cpu_text_job_cancelled": lambda _job: False,
            "_cpu_text_lane": Lane(),
            "_cpu_text_runtime_preemption_eligible": (
                lambda _control, _tokens: True
            ),
            "_cpu_text_break_even_estimate": lambda *_args, **_kwargs: (
                BreakEvenEstimate(100.0, 1.0, 1.0, 1.0)
            ),
            "_cpu_text_preemption_gate": Gate(),
            "_cpu_text_generation_waiter_exists": lambda _job: False,
            "_gen_lock": gpu_lock,
            "_cpu_text_transition": lambda *_args, **_kwargs: 1,
            "_cpu_text_restore_after_abandoned_preemption": (
                lambda _job, attempt: restored.append(attempt)
            ),
            "RESOURCE_EXECUTION_CPU": "cpu",
        }
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]),
                "cpu-monitor-token-change",
                "exec",
            ),
            namespace,
        )
        namespace["_cpu_text_preemption_monitor"](
            llm_service=Runtime(),
            selection={},
            job=_resource_job(),
            owner_key="job",
            lease=object(),
            execution_attempt=1,
            stopped=NeverStopped(),
            outcome=outcome,
            handoff_ready=threading.Event(),
            handoff_acknowledged=threading.Event(),
            handoff_abandoned=threading.Event(),
            handoff_transferred=threading.Event(),
            monitor_done=done,
        )
        self.assertEqual(restored, [1])
        self.assertEqual(outcome, {})
        self.assertTrue(done.is_set())
        self.assertTrue(gpu_lock.acquire(blocking=False))
        gpu_lock.release()

    def test_evidence_degrading_during_publication_prevents_abort(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_preemption_monitor"
        )
        gpu_lock = threading.Lock()
        tokens = types.SimpleNamespace(
            runtime_generation=7, runtime_attempt_id=11,
        )
        control_reads = []
        restored = []
        abort_calls = []

        class NeverStopped:
            @staticmethod
            def wait(_timeout):
                return False

            @staticmethod
            def is_set():
                return False

        class Lane:
            @staticmethod
            def runtime_tokens(_owner):
                return tokens

        class Runtime:
            @staticmethod
            def get_local_runtime_control():
                control_reads.append(True)
                return {
                    "execution": "cooperative_cpu",
                    "decision_eligible": len(control_reads) == 1,
                }

            @staticmethod
            def abort_local_cpu_runtime(*_args, **_kwargs):
                abort_calls.append(True)
                return {"resources_released": True}

        class Gate:
            @staticmethod
            def permits(_owner, _estimate):
                return True

        done = threading.Event()
        outcome = {}
        namespace = {
            "threading": threading,
            "_cpu_text_job_cancelled": lambda _job: False,
            "_cpu_text_lane": Lane(),
            "_cpu_text_runtime_preemption_eligible": (
                lambda control, _tokens: control.get(
                    "decision_eligible",
                ) is True
            ),
            "_cpu_text_break_even_estimate": lambda *_args, **_kwargs: (
                BreakEvenEstimate(100.0, 1.0, 1.0, 1.0)
            ),
            "_cpu_text_preemption_gate": Gate(),
            "_cpu_text_generation_waiter_exists": lambda _job: False,
            "_gen_lock": gpu_lock,
            "_cpu_text_transition": lambda *_args, **_kwargs: 1,
            "_cpu_text_restore_after_abandoned_preemption": (
                lambda _job, attempt: restored.append(attempt)
            ),
            "RESOURCE_EXECUTION_CPU": "cpu",
        }
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]),
                "cpu-monitor-evidence-toctou",
                "exec",
            ),
            namespace,
        )
        namespace["_cpu_text_preemption_monitor"](
            llm_service=Runtime(),
            selection={},
            job=_resource_job(),
            owner_key="job",
            lease=object(),
            execution_attempt=1,
            stopped=NeverStopped(),
            outcome=outcome,
            handoff_ready=threading.Event(),
            handoff_acknowledged=threading.Event(),
            handoff_abandoned=threading.Event(),
            handoff_transferred=threading.Event(),
            monitor_done=done,
        )
        self.assertEqual(len(control_reads), 2)
        self.assertEqual(restored, [1])
        self.assertEqual(abort_calls, [])
        self.assertEqual(outcome, {})
        self.assertTrue(done.is_set())
        self.assertTrue(gpu_lock.acquire(blocking=False))
        gpu_lock.release()

    def test_runtime_preemption_requires_exact_decision_evidence(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_runtime_preemption_eligible"
        )
        namespace = {"math": __import__("math")}
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]),
                "cpu-preemption-evidence",
                "exec",
            ),
            namespace,
        )
        eligible = namespace["_cpu_text_runtime_preemption_eligible"]
        tokens = types.SimpleNamespace(
            runtime_generation=3, runtime_attempt_id=4,
        )
        control = {
            "abort_capable": True,
            "preemptible": True,
            "generation": 3,
            "attempt_id": 4,
            "remaining_estimate_seconds": 90.0,
            "remaining": {
                "state": "decision_grade",
                "decision_eligible": True,
                "runtime_generation": 3,
                "attempt_id": 4,
            },
        }
        self.assertTrue(eligible(control, tokens))
        for update in (
            {"abort_capable": False},
            {"preemptible": False},
            {"remaining_estimate_seconds": None},
            {"remaining_estimate_seconds": float("nan")},
            {"remaining": {
                **control["remaining"], "decision_eligible": False,
                "state": "budget_projection",
            }},
            {"remaining": {
                **control["remaining"], "decision_eligible": True,
                "state": "budget_projection",
            }},
            {"remaining": {**control["remaining"], "attempt_id": 5}},
        ):
            with self.subTest(update=repr(update)):
                self.assertFalse(eligible({**control, **update}, tokens))

    def test_runtime_evidence_loss_withdraws_restart_projection(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_revoke_preemption_if_published"
        )
        transitions = []
        namespace = {
            "_cpu_text_transition": (
                lambda job, **kwargs: transitions.append((job, kwargs))
            ),
            "RESOURCE_EXECUTION_CPU": "cpu",
        }
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]),
                "cpu-preemption-revoke",
                "exec",
            ),
            namespace,
        )
        revoke = namespace["_cpu_text_revoke_preemption_if_published"]
        job = _resource_job()
        revoke(job, 1)
        self.assertEqual(transitions, [])
        job["_resource_preemption_eligible"] = True
        revoke(job, 1)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0][1]["expected_attempt"], 1)
        self.assertEqual(transitions[0][1]["execution"], "cpu")

    def test_malformed_runtime_control_revokes_and_finishes_monitor(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_preemption_monitor"
        )
        calls = {"wait": 0, "revoked": 0}

        class StopAfterOnePoll:
            @staticmethod
            def wait(_timeout):
                calls["wait"] += 1
                return calls["wait"] > 1

        class Runtime:
            @staticmethod
            def get_local_runtime_control():
                return None

        class Lane:
            @staticmethod
            def runtime_tokens(_owner):
                return types.SimpleNamespace(
                    runtime_generation=1, runtime_attempt_id=1,
                )

        done = threading.Event()
        namespace = {
            "threading": threading,
            "_cpu_text_job_cancelled": lambda _job: False,
            "_cpu_text_lane": Lane(),
            "_cpu_text_revoke_preemption_if_published": (
                lambda _job, _attempt: calls.__setitem__(
                    "revoked", calls["revoked"] + 1,
                )
            ),
        }
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]),
                "cpu-monitor-malformed-control",
                "exec",
            ),
            namespace,
        )
        namespace["_cpu_text_preemption_monitor"](
            llm_service=Runtime(),
            selection={},
            job={"_resource_preemption_eligible": True},
            owner_key="job",
            lease=object(),
            execution_attempt=1,
            stopped=StopAfterOnePoll(),
            outcome={},
            handoff_ready=threading.Event(),
            handoff_acknowledged=threading.Event(),
            handoff_abandoned=threading.Event(),
            handoff_transferred=threading.Event(),
            monitor_done=done,
        )
        self.assertEqual(calls["revoked"], 1)
        self.assertTrue(done.is_set())

    def test_production_generation_admission_failures_hold_child(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef) and item.name == "_run_generation"
        )
        for failed_transition in ("queue_register", "start"):
            with self.subTest(failed_transition=failed_transition):
                _reset_queue_state_for_tests()
                job = _resource_job(status="queued")
                job.update({
                    "id": f"child-{failed_transition}",
                    "resource_intent": "generation",
                    "resource_execution": "standard",
                    "preemption_mode": "none",
                    "resource_state": "queued",
                    "parent_job_id": "reference-parent",
                })
                generation_lock = threading.Lock()
                failed = {"value": False}

                def durable(proposal):
                    if (
                        proposal.name == failed_transition
                        and not failed["value"]
                    ):
                        failed["value"] = True
                        raise OSError("synthetic persistence failure")

                from services import job_lifecycle as lifecycle
                lifecycle.configure_durability_hook(durable)
                namespace = {
                    "time": time,
                    "_jobs": {job["id"]: job},
                    "_gen_lock": generation_lock,
                    "_credit_prepare_admission": lambda _job: False,
                    "_credit_admission_evaluations": {},
                    "CreditRuntimeError": ValueError,
                    "EntitlementError": ValueError,
                    "_credit_block_runtime_error": lambda _job: None,
                    "_stamp_requested_generation_residency": (
                        lambda _job, replace=False: None
                    ),
                    "generation_slot": generation_slot,
                    "try_start": try_start,
                    "_active_gen_states": {},
                    "_restore_base_coefficient": lambda: None,
                    "_workspace_dir": lambda: "",
                    "wgp": types.SimpleNamespace(
                        save_path="", image_save_path="",
                    ),
                }
                exec(
                    compile(
                        ast.Module(body=[node], type_ignores=[]),
                        "production-generation-admission",
                        "exec",
                    ),
                    namespace,
                )
                self.assertFalse(namespace["_run_generation"](job["id"]))
                self.assertTrue(failed["value"])
                self.assertEqual(job["status"], "queued")
                self.assertTrue(job["queue_held"])
                self.assertEqual(job["resource_state"], "blocked")
                self.assertEqual(job["recovery_state"], "blocked_preparation")

                lifecycle.configure_durability_hook(None)
                admission = []
                waiter = threading.Thread(
                    target=lambda: admission.append(acquire_generation_slot(
                        generation_lock, job, poll_interval=0.005,
                    )),
                )
                waiter.start()
                time.sleep(0.05)
                self.assertEqual(admission, [])
                self.assertTrue(request_cancel(job).changed)
                waiter.join(1.0)
                self.assertEqual(admission, [False])

    def test_attempt_fence_discards_late_progress_and_result(self):
        job = _resource_job()
        attempt = transition_resource_execution(
            job,
            expected_execution_attempt=1,
            intent="text",
            execution="standard",
            preemption_mode="none",
            state="restarting_on_accelerator",
            increment_attempt=True,
            reset_progress=True,
        )
        self.assertEqual(attempt, 2)
        self.assertFalse(update_job(
            job, expected_execution_attempt=1, progress=99,
        ))
        self.assertFalse(finish_job(
            job, "completed", expected_execution_attempt=1,
        ))
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["progress"], 0)
        self.assertTrue(finish_job(
            job, "completed", expected_execution_attempt=2,
        ))

    def test_lifecycle_keeps_resource_state_aligned_with_admission(self):
        running = _resource_job(status="queued")
        running["resource_state"] = "queued"
        self.assertTrue(try_start(running, expected_execution_attempt=1))
        self.assertEqual(resource_descriptor(running)["state"], "running")

        blocked = _resource_job(status="queued")
        blocked["resource_state"] = "queued"
        self.assertTrue(block_resource_admission_failure(blocked))
        descriptor = resource_descriptor(blocked)
        self.assertEqual(descriptor["state"], "blocked")
        self.assertEqual(descriptor["execution"], "standard")
        self.assertFalse(descriptor["preemptible"])

    def test_cancel_finality_wins_over_preempt_finish_race(self):
        job = _resource_job()
        self.assertTrue(request_cancel(job).changed)
        self.assertIsNone(transition_resource_execution(
            job,
            expected_execution_attempt=1,
            state="restarting_on_accelerator",
            increment_attempt=True,
        ))
        self.assertFalse(finish_job(
            job, "completed", expected_execution_attempt=1,
        ))
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(resource_descriptor(job)["state"], "released")

    def test_descriptor_and_recovery_are_closed_and_content_free(self):
        self.assertIsNone(resource_descriptor({
            "id": "legacy", "status": "queued",
        }))
        job = _resource_job()
        descriptor = resource_descriptor(job)
        self.assertEqual(set(descriptor), {
            "intent", "execution", "preemptible", "preemption_mode",
            "state", "execution_attempt",
        })
        self.assertFalse(descriptor["preemptible"])
        job["_resource_preemption_eligible"] = True
        self.assertTrue(resource_descriptor(job)["preemptible"])
        job.update({
            "workspace": "x_test",
            "prompt": "private prompt",
            "api_key": "private key",
            "model_runtime_id": "raw/model-id",
        })
        secret = b"0123456789abcdef0123456789abcdef"
        serialized = serialize_job(
            job,
            owner_digest=owner_principal_digest(secret, "owner"),
            project_digest=project_instance_digest(secret, "a" * 32),
            request_manifest={},
        )
        self.assertNotIn("prompt", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("model_runtime_id", serialized)
        self.assertNotIn("_resource_preemption_eligible", serialized)
        self.assertEqual(serialized["execution_attempt"], 1)
        invalid = dict(job, resource_state="invented")
        with self.assertRaises(QueueRecoveryAdapterError):
            serialize_job(
                invalid,
                owner_digest=owner_principal_digest(secret, "owner"),
                project_digest=project_instance_digest(secret, "a" * 32),
                request_manifest={},
            )

    def test_server_operation_capability_classifier_is_content_neutral(self):
        source = (Path(_APP_DIR) / "launch.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        node = next(
            item for item in module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_cpu_text_operation_eligible"
        )
        isolated = ast.Module(body=[node], type_ignores=[])
        namespace = {
            "_CPU_TEXT_OPERATIONS": frozenset({
                "prompt_enhancement", "generation_preparation",
                "reference_planning",
            }),
        }
        exec(compile(isolated, "isolated-cpu-classifier", "exec"), namespace)
        classify = namespace["_cpu_text_operation_eligible"]
        local = {"provider": "local", "vision_capable": False}
        ordinary = {"model_type": "wan", "generation_mode": "video"}
        self.assertTrue(classify(
            local,
            operation_name="prompt_enhancement",
            text_only=True,
            job={**ordinary, "private_prompt": "content is not inspected"},
        ))
        self.assertFalse(classify(
            local,
            operation_name="download",
            text_only=True,
            job=ordinary,
        ))
        self.assertFalse(classify(
            {**local, "provider": "remote"},
            operation_name="reference_planning",
            text_only=True,
            job=ordinary,
        ))
        self.assertFalse(classify(
            {**local, "vision_capable": True},
            operation_name="reference_planning",
            text_only=True,
            job=ordinary,
        ))
        self.assertFalse(classify(
            {"provider": "local"},
            operation_name="reference_planning",
            text_only=True,
            job=ordinary,
        ))
        self.assertFalse(classify(
            local,
            operation_name="prompt_enhancement",
            text_only=False,
            job=ordinary,
        ))
        self.assertFalse(classify(
            local,
            operation_name="generation_preparation",
            text_only=True,
            job={"model_type": "minimax_h3_14b", "generation_mode": "video"},
        ))


if __name__ == "__main__":
    unittest.main()
