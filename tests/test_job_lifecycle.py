"""Model-free regression tests for generation job state races."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock


_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.abspath(os.path.join(_HERE, "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from services.job_lifecycle import (  # noqa: E402
    GENERATED_MEDIA_EXTENSIONS,
    MAX_RESIDENCY_BYPASSES,
    RESIDENCY_AGE_CEILING_SECONDS,
    _reset_queue_state_for_tests,
    _select_next_waiter,
    acquire_generation_slot,
    call_with_sticky_interrupt,
    clear_job_residency,
    collect_job_outputs,
    finish_job,
    invalidate_residency_state,
    job_events,
    make_residency_key,
    note_residency_state,
    promote_queued_job,
    queue_control_state,
    queue_position,
    queue_scheduler_snapshot,
    queue_wait_reason,
    record_job_outputs,
    register_abort_state,
    residency_configuration_update,
    request_cancel,
    set_job_hold,
    set_queue_pause_after_current,
    set_queue_paused,
    snapshot_job,
    stamp_job_residency,
    try_requeue,
    try_start,
    unregister_abort_state,
    update_queue_job,
    update_requested_outputs,
    update_job,
    yield_generation_slot_after_output,
)


def _job() -> dict:
    return {"id": "job-1", "status": "queued", "message": "Queued"}


class TestJobLifecycle(unittest.TestCase):
    def setUp(self):
        _reset_queue_state_for_tests()

    def test_generated_media_extension_contract_is_complete(self):
        self.assertEqual(GENERATED_MEDIA_EXTENSIONS, frozenset({
            ".aac", ".flac", ".gif", ".jpeg", ".jpg", ".m4a", ".mkv",
            ".mov", ".mp3", ".mp4", ".ogg", ".png", ".wav", ".webm",
            ".webp",
        }))

    def test_sticky_interrupt_survives_model_entry_reset(self):
        state = {"abort": False}
        model = type("FakeModel", (), {"_interrupt": False})()
        entered = threading.Event()
        result: list[str] = []

        def reset_then_wait():
            model._interrupt = False
            entered.set()
            deadline = time.time() + 1
            while not model._interrupt and time.time() < deadline:
                time.sleep(0.005)
            return "aborted" if model._interrupt else "timed-out"

        worker = threading.Thread(target=lambda: result.append(
            call_with_sticky_interrupt(
                state,
                model,
                reset_then_wait,
                poll_interval=0.005,
            )
        ))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        state["abort"] = True
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ["aborted"])
        self.assertTrue(model._interrupt)

    def test_pre_cancelled_model_call_is_never_invoked(self):
        state = {"abort": True}
        model = type("FakeModel", (), {"_interrupt": False})()
        callable_ = Mock()
        self.assertIsNone(call_with_sticky_interrupt(
            state, model, callable_, poll_interval=0.005,
        ))
        callable_.assert_not_called()
        self.assertTrue(model._interrupt)

    def test_explicit_outputs_ignore_concurrent_unrelated_files(self):
        with tempfile.TemporaryDirectory() as out_dir:
            own_path = os.path.join(out_dir, "clip-image.png")
            unrelated_path = os.path.join(
                out_dir, "_rerun_audio_other-pipeline.wav",
            )
            for path in (own_path, unrelated_path):
                with open(path, "wb") as handle:
                    handle.write(b"artifact")

            outputs = collect_job_outputs(
                {
                    "artifact_list": [own_path],
                    "file_list": [],
                    "audio_file_list": [],
                },
                out_dir,
                before=set(),
                allow_legacy_fallback=False,
            )

            self.assertEqual(outputs, ["clip-image.png"])

    def test_relative_output_root_prefix_is_not_joined_twice(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as work_dir:
            out_dir = os.path.join(work_dir, "outputs")
            os.makedirs(out_dir)
            rooted_name = os.path.join("outputs", "rooted-image.jpg")
            bare_name = "bare-image.jpg"
            outside_name = os.path.join(work_dir, "outside-image.jpg")
            for path in (
                os.path.join(work_dir, rooted_name),
                os.path.join(out_dir, bare_name),
                outside_name,
            ):
                with open(path, "wb") as handle:
                    handle.write(b"artifact")

            try:
                os.chdir(work_dir)
                outputs = collect_job_outputs(
                    {
                        "artifact_list": [
                            rooted_name,
                            bare_name,
                            outside_name,
                        ],
                    },
                    "outputs",
                    allow_legacy_fallback=False,
                )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(
                outputs,
                ["rooted-image.jpg", "bare-image.jpg"],
            )

    def test_director_job_never_uses_ambiguous_directory_fallback(self):
        with tempfile.TemporaryDirectory() as out_dir:
            with open(os.path.join(out_dir, "unrelated.png"), "wb") as handle:
                handle.write(b"artifact")
            self.assertEqual(
                collect_job_outputs(
                    {"file_list": [], "audio_file_list": []},
                    out_dir,
                    before=set(),
                    allow_legacy_fallback=False,
                ),
                [],
            )

    def test_cancel_queued_prevents_start(self):
        job = _job()
        result = request_cancel(job)
        self.assertTrue(result.changed)
        self.assertFalse(result.was_running)
        self.assertFalse(try_start(job))
        self.assertEqual(job["status"], "cancelled")

    def test_cancel_running_signals_abort_and_model_once(self):
        job = _job()
        states: dict = {}
        state = {"abort": False}
        interrupt = Mock()
        self.assertTrue(try_start(job))
        self.assertTrue(register_abort_state(
            job, job["id"], states, state, interrupt_model=interrupt,
        ))

        result = request_cancel(
            job, job_id=job["id"], active_states=states,
        )
        self.assertTrue(result.was_running)
        self.assertTrue(result.abort_signalled)
        self.assertTrue(state["abort"])
        interrupt.assert_called_once_with()

        # Cancellation is idempotent and cannot signal the model again.
        self.assertFalse(request_cancel(
            job, job_id=job["id"], active_states=states,
        ).changed)
        interrupt.assert_called_once_with()
        unregister_abort_state(job["id"], states, state)

    def test_finish_and_failure_cannot_overwrite_cancellation(self):
        for terminal in ("completed", "failed"):
            with self.subTest(terminal=terminal):
                job = _job()
                self.assertTrue(try_start(job))
                request_cancel(job)
                self.assertFalse(finish_job(job, terminal, message=terminal))
                self.assertEqual(job["status"], "cancelled")
                self.assertEqual(job["message"], "Cancelled")

    def test_outputs_can_settle_after_cancel_without_changing_terminal_state(self):
        job = _job()
        job["output_files"] = ["clip-1.mp4"]
        self.assertTrue(try_start(job))
        request_cancel(job)

        merged = record_job_outputs(
            job,
            ["clip-1.mp4", "clip-2.mp4"],
            clip_output_files={0: "clip-2.mp4"},
        )
        self.assertEqual(merged, ["clip-1.mp4", "clip-2.mp4"])
        self.assertEqual(job["artifact_files"], ["clip-1.mp4", "clip-2.mp4"])
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["message"], "Cancelled")

        snapshot = snapshot_job(job)
        snapshot["output_files"].append("snapshot-only.mp4")
        snapshot["clip_output_files"]["0"] = "snapshot-only.mp4"
        self.assertEqual(
            job["output_files"], ["clip-1.mp4", "clip-2.mp4"],
        )
        self.assertEqual(job["clip_output_files"], {"0": "clip-2.mp4"})

    def test_segmented_h3_counts_only_joined_variants_and_keeps_lineage(self):
        job = {
            **_job(),
            "params": {
                "repeat_generation": 2,
                "_h3_longform": {"clip_count": 2},
            },
            "requested_outputs": 2,
            "output_files": [],
        }
        self.assertTrue(try_start(job))

        first_artifacts = [
            "variant1-segment1-window1.mp4",
            "variant1-segment1.mp4",
            "variant1-segment-audio.wav",
            "variant1-segment2.mp4",
            "variant1_seed11_multiclip.mp4",
        ]
        self.assertEqual(record_job_outputs(
            job,
            first_artifacts,
            clip_output_files={
                0: "variant1-segment1.mp4",
                1: "variant1-segment2.mp4",
            },
            join_output_file="variant1_seed11_multiclip.mp4",
        ), ["variant1_seed11_multiclip.mp4"])
        self.assertEqual(len(job["output_files"]), 1)
        self.assertEqual(job["artifact_files"], first_artifacts)

        second_artifacts = [
            "variant2-segment1.mp4",
            "variant2-segment-audio.wav",
            "variant2-segment2.mp4",
            "variant2_seed12_multiclip.mp4",
        ]
        # The worker retains only the first scalar join_output_file.  Every
        # additional H3 variant still follows WGP's producer-owned multiclip
        # naming contract and must count exactly once.
        self.assertEqual(record_job_outputs(
            job,
            second_artifacts,
            clip_output_files={
                0: "variant2-segment1.mp4",
                1: "variant2-segment2.mp4",
            },
        ), [
            "variant1_seed11_multiclip.mp4",
            "variant2_seed12_multiclip.mp4",
        ])
        self.assertEqual(len(job["output_files"]), 2)
        self.assertEqual(
            job["artifact_files"],
            first_artifacts + second_artifacts,
        )

        snapshot = snapshot_job(job)
        snapshot["artifact_files"].append("snapshot-only.mp4")
        self.assertNotIn("snapshot-only.mp4", job["artifact_files"])

    def test_segmented_h3_accepts_explicit_producer_finality(self):
        job = {
            **_job(),
            "params": {"_h3_longform": {"clip_count": 2}},
            "output_files": [],
        }
        self.assertTrue(try_start(job))
        artifacts = [
            "segment-a.mp4",
            "segment-audio.wav",
            "segment-b.mp4",
            "assembled-final.mp4",
        ]
        self.assertEqual(record_job_outputs(
            job,
            artifacts,
            clip_output_files={0: "segment-a.mp4", 1: "segment-b.mp4"},
            final_output_files=["assembled-final.mp4"],
        ), ["assembled-final.mp4"])
        self.assertEqual(job["artifact_files"], artifacts)

    def test_stale_single_clip_h3_marker_does_not_hide_native_output(self):
        for plan in ({}, {"clip_count": 1}, {"clip_count": "invalid"}):
            with self.subTest(plan=plan):
                job = {
                    **_job(),
                    "params": {"_h3_longform": plan},
                    "output_files": [],
                }
                self.assertTrue(try_start(job))
                self.assertEqual(
                    record_job_outputs(job, ["native-output.mp4"]),
                    ["native-output.mp4"],
                )

    def test_direct_h3_replacement_is_authoritative_after_postprocessing(self):
        for transition in ("update", "finish"):
            with self.subTest(transition=transition):
                job = {
                    **_job(),
                    "params": {"_h3_longform": {"clip_count": 2}},
                    "output_files": [],
                }
                self.assertTrue(try_start(job))
                record_job_outputs(job, ["segment-a.mp4", "segment-b.mp4"])
                if transition == "update":
                    changed = update_job(
                        job, output_files=["assembled-final.mp4"],
                    )
                else:
                    changed = finish_job(
                        job,
                        "completed",
                        output_files=["assembled-final.mp4"],
                        join_output_file="assembled-final.mp4",
                    )
                self.assertTrue(changed)
                self.assertEqual(job["output_files"], ["assembled-final.mp4"])
                self.assertEqual(
                    job["artifact_files"],
                    ["segment-a.mp4", "segment-b.mp4", "assembled-final.mp4"],
                )

    def test_ordinary_repeat_outputs_remain_independent_finals(self):
        job = {
            **_job(),
            "params": {"repeat_generation": 2},
            "requested_outputs": 2,
            "output_files": [],
        }
        self.assertTrue(try_start(job))
        outputs = ["sample-seed21.mp4", "sample-seed22.mp4"]
        self.assertEqual(record_job_outputs(job, outputs), outputs)
        self.assertEqual(job["artifact_files"], outputs)
        self.assertEqual(len(job["output_files"]), 2)

    def test_direct_output_replacement_keeps_prior_artifact_provenance(self):
        job = {
            **_job(),
            "params": {"repeat_generation": 1},
            "output_files": [],
        }
        self.assertTrue(try_start(job))
        self.assertEqual(
            record_job_outputs(job, ["render.mp4"]),
            ["render.mp4"],
        )
        self.assertTrue(update_job(job, output_files=["render-final.mp4"]))
        self.assertEqual(job["output_files"], ["render-final.mp4"])
        self.assertEqual(
            job["artifact_files"],
            ["render.mp4", "render-final.mp4"],
        )

    def test_completion_wins_before_late_cancel(self):
        job = _job()
        interrupt = Mock()
        self.assertTrue(try_start(job))
        self.assertTrue(finish_job(job, "completed", message="Done"))
        result = request_cancel(job)
        self.assertFalse(result.changed)
        self.assertEqual(job["status"], "completed")
        interrupt.assert_not_called()

    def test_worker_updates_require_a_running_job(self):
        job = _job()
        self.assertFalse(update_job(job, message="Not started"))
        self.assertFalse(try_requeue(job, message="Still queued"))
        self.assertFalse(finish_job(job, "completed", message="Too early"))
        self.assertEqual(job["status"], "queued")

    def test_output_count_can_change_queued_and_live_repeat_target(self):
        job = {**_job(), "params": {"repeat_generation": 2}}
        self.assertTrue(update_requested_outputs(job, 4))
        self.assertEqual(job["params"]["repeat_generation"], 4)
        self.assertEqual(job["requested_outputs"], 4)

        self.assertTrue(try_start(job))
        state = {"repeat_no": 1, "total_generation": 4, "extra_orders": 0}
        self.assertTrue(update_requested_outputs(job, 2, active_state=state))
        self.assertEqual(state["extra_orders"], -2)
        self.assertEqual(job["requested_outputs"], 2)
        state.update(repeat_no=2, total_generation=2, extra_orders=0)
        self.assertFalse(update_requested_outputs(job, 1, active_state=state))

    def test_running_h3_count_change_is_rejected_but_queued_is_allowed(self):
        job = {
            **_job(),
            "params": {"repeat_generation": 1, "_h3_longform": {"clip_count": 2}},
        }
        self.assertTrue(update_requested_outputs(job, 3))
        self.assertTrue(try_start(job))
        self.assertFalse(update_requested_outputs(
            job,
            2,
            active_state={"repeat_no": 0, "total_generation": 1},
        ))

    def test_job_event_log_is_bounded_copied_and_deduplicated(self):
        job = _job()
        self.assertTrue(try_start(job, message="Preparing"))
        for index in range(300):
            self.assertTrue(update_job(job, message=f"Step {index}", step=index))
        events = job_events(job, 500)
        self.assertEqual(len(events), 250)
        events[-1]["message"] = "changed copy"
        self.assertEqual(job_events(job, 1)[0]["message"], "Step 299")
        before = len(job_events(job, 250))
        self.assertTrue(update_job(job, message="Step 299", step=299))
        self.assertEqual(len(job_events(job, 250)), before)

    def test_cancel_between_start_and_abort_registration_refuses_work(self):
        job = _job()
        states: dict = {}
        state = {"abort": False}
        self.assertTrue(try_start(job))
        request_cancel(job)
        self.assertFalse(register_abort_state(
            job, job["id"], states, state, interrupt_model=Mock(),
        ))
        self.assertTrue(state["abort"])
        self.assertNotIn(job["id"], states)

    def test_requeue_after_cancel_is_refused(self):
        job = _job()
        self.assertTrue(try_start(job))
        request_cancel(job)
        self.assertFalse(try_requeue(job, message="Queued again"))
        self.assertFalse(update_job(job, message="Late worker message"))
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["message"], "Cancelled")

    def test_queued_job_ignores_a_stale_active_state(self):
        job = _job()
        stale_state = {"abort": False}
        states = {job["id"]: stale_state}
        result = request_cancel(
            job, job_id=job["id"], active_states=states,
        )
        self.assertFalse(result.was_running)
        self.assertFalse(result.abort_signalled)
        self.assertFalse(stale_state["abort"])

    def test_mismatched_state_never_invokes_wan_interrupt(self):
        job = _job()
        states: dict = {}
        registered_state = {"abort": False}
        replacement_state = {"abort": False}
        interrupt = Mock()
        self.assertTrue(try_start(job))
        self.assertTrue(register_abort_state(
            job,
            job["id"],
            states,
            registered_state,
            interrupt_model=interrupt,
        ))
        states[job["id"]] = replacement_state
        try:
            result = request_cancel(
                job, job_id=job["id"], active_states=states,
            )
            self.assertFalse(result.abort_signalled)
            self.assertFalse(registered_state["abort"])
            self.assertFalse(replacement_state["abort"])
            interrupt.assert_not_called()
        finally:
            unregister_abort_state(job["id"], states, registered_state)
            states.pop(job["id"], None)

    def test_non_wan_abort_state_does_not_interrupt_model(self):
        job = _job()
        states: dict = {}
        state = {"abort": False}
        interrupt = Mock()
        self.assertTrue(try_start(job))
        self.assertTrue(register_abort_state(
            job, job["id"], states, state,
        ))
        request_cancel(
            job, job_id=job["id"], active_states=states,
        )
        self.assertTrue(state["abort"])
        interrupt.assert_not_called()
        unregister_abort_state(job["id"], states, state)

    def test_cancelled_waiter_exits_without_acquiring_generation_lock(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        job = _job()
        result: list[bool] = []
        waiting = threading.Event()

        def wait_for_slot():
            waiting.set()
            result.append(acquire_generation_slot(
                generation_lock, job, poll_interval=0.01,
            ))

        thread = threading.Thread(target=wait_for_slot)
        thread.start()
        self.assertTrue(waiting.wait(timeout=1))
        time.sleep(0.03)
        request_cancel(job)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [False])
        generation_lock.release()

    def test_priority_hold_resume_and_pause_after_current_are_cooperative(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        low = {**_job(), "id": "low", "queue_priority": 0, "created_at": 1}
        high = {**_job(), "id": "high", "queue_priority": 10, "created_at": 2}
        order: list[str] = []

        self.assertTrue(update_queue_job(high, held=True))

        def run(candidate):
            if acquire_generation_slot(generation_lock, candidate, poll_interval=0.005):
                order.append(candidate["id"])
                generation_lock.release()

        low_thread = threading.Thread(target=run, args=(low,))
        high_thread = threading.Thread(target=run, args=(high,))
        low_thread.start()
        high_thread.start()
        time.sleep(0.03)
        generation_lock.release()
        low_thread.join(timeout=1)
        self.assertEqual(order, ["low"])
        self.assertTrue(update_queue_job(high, held=False))
        high_thread.join(timeout=1)
        self.assertEqual(order, ["low", "high"])

        self.assertEqual(set_queue_pause_after_current(True)["pause_after_current"], True)
        self.assertEqual(set_queue_paused(True)["paused"], True)
        self.assertEqual(set_queue_paused(False), {
            "paused": False, "pause_after_current": False,
        })

    def test_running_job_yields_slot_and_waits_when_held_after_output(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        job = _job()
        self.assertTrue(try_start(job))
        self.assertEqual(set_job_hold(job, True), "after_output")
        result: list[bool] = []

        thread = threading.Thread(target=lambda: result.append(
            yield_generation_slot_after_output(
                generation_lock, job, poll_interval=0.005,
            )
        ))
        thread.start()
        deadline = time.time() + 1
        while job.get("status") != "queued" and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(job.get("status"), "queued")
        self.assertTrue(job.get("queue_held"))
        self.assertTrue(generation_lock.acquire(timeout=1))
        generation_lock.release()
        self.assertEqual(set_job_hold(job, False), "resumed")
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(job.get("status"), "running")
        generation_lock.release()

    def test_hold_after_admission_relinquishes_and_requeues_before_start(self):
        generation_lock = threading.Lock()
        job = _job()
        self.assertTrue(acquire_generation_slot(
            generation_lock, job, poll_interval=0.005,
        ))
        job["_generation_slot_owned"] = True
        self.assertEqual(set_job_hold(job, True), "held")
        result: list[bool] = []

        thread = threading.Thread(target=lambda: result.append(try_start(
            job,
            generation_lock=generation_lock,
            poll_interval=0.005,
            message="Preparing",
        )))
        thread.start()
        self.assertTrue(generation_lock.acquire(timeout=1))
        generation_lock.release()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(set_job_hold(job, False), "resumed")
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(job["status"], "running")
        self.assertTrue(job["_generation_slot_owned"])
        job["_generation_slot_owned"] = False
        generation_lock.release()

    def test_cancel_contending_with_yield_transition_cannot_requeue_job(self):
        cancel_entered = threading.Event()
        cancel_thread: list[threading.Thread] = []

        class CancelDuringStatusCheck(dict):
            armed = True

            def get(self, key, default=None):
                value = super().get(key, default)
                if key == "status" and self.armed:
                    self.armed = False

                    def cancel():
                        cancel_entered.set()
                        request_cancel(self)

                    thread = threading.Thread(target=cancel)
                    cancel_thread.append(thread)
                    thread.start()
                    self.assert_cancel_entered()
                return value

            def assert_cancel_entered(self):
                if not cancel_entered.wait(timeout=1):
                    raise AssertionError("cancel did not contend with yield")

        generation_lock = threading.Lock()
        generation_lock.acquire()
        job = CancelDuringStatusCheck({
            **_job(),
            "status": "running",
            "hold_after_output": True,
            "_generation_slot_owned": True,
        })
        self.assertFalse(yield_generation_slot_after_output(
            generation_lock, job, poll_interval=0.005,
        ))
        cancel_thread[0].join(timeout=1)

        self.assertFalse(cancel_thread[0].is_alive())
        self.assertEqual(job["status"], "cancelled")
        self.assertIsNone(queue_position(job))
        self.assertEqual(
            queue_scheduler_snapshot([job])["summary"]["active_total"],
            0,
        )
        self.assertTrue(generation_lock.acquire(timeout=1))
        generation_lock.release()

    def test_global_pause_after_output_yields_until_queue_resume(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        job = _job()
        self.assertTrue(try_start(job))
        self.assertTrue(set_queue_pause_after_current(True)["pause_after_current"])
        result: list[bool] = []

        thread = threading.Thread(target=lambda: result.append(
            yield_generation_slot_after_output(
                generation_lock, job, poll_interval=0.005,
            )
        ))
        thread.start()
        deadline = time.time() + 1
        while job.get("status") != "queued" and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(queue_control_state(), {
            "paused": True, "pause_after_current": False,
        })
        self.assertFalse(job.get("queue_held"))
        self.assertEqual(set_queue_paused(False)["paused"], False)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [True])
        generation_lock.release()

    def test_queued_local_job_precedes_remote_without_preempting_running_work(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()  # Represents an already-running job.
        remote = {
            **_job(), "id": "remote", "source_remote": True,
            "queue_priority": 1000, "created_at": 1,
        }
        local = {
            **_job(), "id": "local", "source_remote": False,
            "queue_priority": -1000, "created_at": 2,
        }
        order: list[str] = []

        def run(candidate):
            if acquire_generation_slot(generation_lock, candidate, poll_interval=0.005):
                order.append(candidate["id"])
                generation_lock.release()

        remote_thread = threading.Thread(target=run, args=(remote,))
        local_thread = threading.Thread(target=run, args=(local,))
        remote_thread.start()
        local_thread.start()
        time.sleep(0.03)
        self.assertEqual(order, [])  # Active remote/local work is untouched.
        generation_lock.release()
        local_thread.join(timeout=1)
        remote_thread.join(timeout=1)
        self.assertEqual(order, ["local", "remote"])

    def test_residency_affinity_reorders_only_within_exact_local_priority_tier(self):
        base = make_residency_key("base", "model-a")
        affinity = make_residency_key("affinity", "overlay-a")
        other = make_residency_key("base", "model-b")
        note_residency_state(base, affinity)

        local_high = {
            **_job(), "id": "local-high", "queue_priority": 10,
            "created_at": 3,
        }
        local_old = {
            **_job(), "id": "local-old", "queue_priority": 0,
            "created_at": 1,
        }
        local_base = {
            **_job(), "id": "local-base", "queue_priority": 0,
            "created_at": 2,
        }
        local_affinity = {
            **_job(), "id": "local-affinity", "queue_priority": 0,
            "created_at": 3,
        }
        remote_affinity = {
            **_job(), "id": "remote-affinity", "queue_priority": 100,
            "source_remote": True, "created_at": 0,
        }
        stamp_job_residency(local_high, other)
        stamp_job_residency(local_old, other)
        stamp_job_residency(local_base, base)
        stamp_job_residency(local_affinity, base, affinity)
        stamp_job_residency(remote_affinity, base, affinity)

        eligible = list(enumerate((
            remote_affinity, local_affinity, local_base, local_old, local_high,
        ), start=1))
        selected, reason, skipped = _select_next_waiter(eligible)
        self.assertIs(selected[1], local_high)
        self.assertEqual(reason, "queue_order")
        self.assertEqual(skipped, [])

        selected, reason, skipped = _select_next_waiter([
            entry for entry in eligible if entry[1] is not local_high
        ])
        self.assertIs(selected[1], local_affinity)
        self.assertEqual(reason, "resident_affinity")
        self.assertEqual([entry[1]["id"] for entry in skipped], ["local-old", "local-base"])

    def test_queue_position_simulates_affinity_without_mutating_bypass_counts(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        base = make_residency_key("base", "resident")
        other = make_residency_key("base", "other")
        note_residency_state(base)
        old = {**_job(), "id": "old", "created_at": 1}
        resident = {**_job(), "id": "resident", "created_at": 2}
        stamp_job_residency(old, other)
        stamp_job_residency(resident, base)
        results: list[bool] = []

        threads = [
            threading.Thread(target=lambda candidate=candidate: results.append(
                acquire_generation_slot(
                    generation_lock, candidate, poll_interval=0.005,
                )
            ))
            for candidate in (old, resident)
        ]
        for thread in threads:
            thread.start()
        deadline = time.time() + 1
        while (
            (queue_position(resident) is None or queue_position(old) is None)
            and time.time() < deadline
        ):
            time.sleep(0.005)

        self.assertEqual(queue_position(resident), 1)
        self.assertEqual(queue_position(old), 2)
        self.assertEqual(queue_position(old), 2)
        self.assertEqual(old.get("_residency_bypass_count", 0), 0)
        request_cancel(old)
        request_cancel(resident)
        generation_lock.release()
        for thread in threads:
            thread.join(timeout=1)
        self.assertEqual(results, [False, False])

    def test_scheduler_snapshot_has_exact_anonymous_counts_and_positions(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        base = make_residency_key("base", "resident")
        other = make_residency_key("base", "other")
        note_residency_state(base)
        older = {
            **_job(), "id": "older", "created_at": 1,
            "session_id": "owner-a",
        }
        resident = {
            **_job(), "id": "resident", "created_at": 2,
            "session_id": "owner-b",
        }
        stamp_job_residency(older, other)
        stamp_job_residency(resident, base)
        held = {
            **_job(), "id": "held", "queue_held": True,
            "session_id": "owner-a",
        }
        registering = {
            **_job(), "id": "registering", "session_id": "owner-a",
        }
        running = {
            **_job(), "id": "running", "status": "running",
            "session_id": "owner-a",
        }
        completed = {**_job(), "id": "done", "status": "completed"}

        threads = [
            threading.Thread(
                target=acquire_generation_slot,
                args=(generation_lock, candidate),
                kwargs={"poll_interval": 0.005},
            )
            for candidate in (older, resident)
        ]
        for thread in threads:
            thread.start()
        deadline = time.time() + 1
        all_jobs = [running, older, resident, held, registering, completed]
        snapshot = queue_scheduler_snapshot(all_jobs)
        while snapshot["summary"]["waiting"] != 2 and time.time() < deadline:
            time.sleep(0.005)
            snapshot = queue_scheduler_snapshot(all_jobs)

        self.assertEqual(snapshot["summary"], {
            "running": 1,
            "waiting": 2,
            "held": 1,
            "registering": 1,
            "active_total": 5,
        })
        self.assertEqual(snapshot["positions"][id(resident)], 1)
        self.assertEqual(snapshot["positions"][id(older)], 2)
        self.assertGreaterEqual(
            snapshot["summary"]["waiting"],
            max(snapshot["positions"].values()),
        )
        self.assertEqual(
            snapshot["wait_reasons"][id(resident)],
            "waiting_for_other_user",
        )
        self.assertEqual(snapshot["wait_reasons"][id(older)], "waiting_for_turn")
        self.assertEqual(snapshot["wait_reasons"][id(held)], "held")
        self.assertEqual(snapshot["wait_reasons"][id(registering)], "registering")
        self.assertNotIn(id(completed), snapshot["positions"])

        set_queue_paused(True)
        paused_snapshot = queue_scheduler_snapshot(all_jobs)
        self.assertTrue(paused_snapshot["paused"])
        self.assertEqual(paused_snapshot["summary"], snapshot["summary"])
        self.assertEqual(
            paused_snapshot["wait_reasons"][id(resident)], "queue_paused",
        )

        request_cancel(older)
        request_cancel(resident)
        generation_lock.release()
        for thread in threads:
            thread.join(timeout=1)

    def test_actual_affinity_admission_stamps_bounded_bypass_metadata(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        base = make_residency_key("base", "resident")
        other = make_residency_key("base", "other")
        note_residency_state(base)
        old = {**_job(), "id": "old", "created_at": 1}
        resident = {**_job(), "id": "resident", "created_at": 2}
        stamp_job_residency(old, other)
        stamp_job_residency(resident, base)
        order: list[str] = []

        def run(candidate):
            if acquire_generation_slot(
                generation_lock, candidate, poll_interval=0.005,
            ):
                order.append(candidate["id"])
                generation_lock.release()

        threads = [
            threading.Thread(target=run, args=(candidate,))
            for candidate in (old, resident)
        ]
        for thread in threads:
            thread.start()
        deadline = time.time() + 1
        while (
            (queue_position(resident) is None or queue_position(old) is None)
            and time.time() < deadline
        ):
            time.sleep(0.005)
        generation_lock.release()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(order, ["resident", "old"])
        self.assertEqual(resident["queue_reorder_reason"], "resident_base")
        self.assertEqual(resident["queue_residency_bypassed_waiters"], 1)
        self.assertEqual(old["queue_residency_bypass_count"], 1)
        self.assertNotIn("_queue_enqueued_monotonic", resident)
        self.assertNotIn("_queue_enqueued_monotonic", old)
        self.assertLessEqual(
            old["queue_residency_bypass_count"], MAX_RESIDENCY_BYPASSES,
        )

    def test_configuration_update_blocks_admission_until_atomic_restamp(self):
        generation_lock = threading.Lock()
        old_key = make_residency_key("base", "old")
        new_key = make_residency_key("base", "new")
        job = {**_job(), "id": "restamp", "created_at": 1}
        stamp_job_residency(job, old_key)
        entered = threading.Event()
        acquired = []

        def run():
            entered.set()
            if acquire_generation_slot(
                generation_lock, job, poll_interval=0.005,
            ):
                acquired.append(True)
                generation_lock.release()

        with residency_configuration_update():
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(entered.wait(timeout=1))
            time.sleep(0.02)
            self.assertEqual(acquired, [])
            self.assertTrue(clear_job_residency(job))
            stamp_job_residency(job, new_key)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(acquired, [True])
        self.assertEqual(job["residency_base_key"], new_key)

    def test_bypass_limit_and_age_ceiling_prevent_residency_starvation(self):
        base = make_residency_key("base", "resident")
        other = make_residency_key("base", "other")
        note_residency_state(base)
        protected = {
            **_job(), "id": "protected", "created_at": 1,
            "_residency_bypass_count": MAX_RESIDENCY_BYPASSES,
        }
        resident = {**_job(), "id": "resident", "created_at": 2}
        stamp_job_residency(protected, other)
        stamp_job_residency(resident, base)
        selected, reason, skipped = _select_next_waiter([
            (1, protected), (2, resident),
        ])
        self.assertIs(selected[1], protected)
        self.assertEqual(reason, "starvation_guard")
        self.assertEqual(skipped, [])

        protected["_residency_bypass_count"] = 0
        protected["_queue_enqueued_monotonic"] = (
            time.monotonic() - RESIDENCY_AGE_CEILING_SECONDS - 1
        )
        selected, reason, _ = _select_next_waiter([
            (1, protected), (2, resident),
        ])
        self.assertIs(selected[1], protected)
        self.assertEqual(reason, "starvation_guard")

    def test_held_cancelled_and_invalidated_residency_are_not_selected(self):
        base = make_residency_key("base", "resident")
        other = make_residency_key("base", "other")
        note_residency_state(base)
        old = {**_job(), "id": "old", "created_at": 1}
        held = {
            **_job(), "id": "held", "created_at": 2,
            "queue_held": True,
        }
        cancelled = {**_job(), "id": "cancelled", "created_at": 3}
        stamp_job_residency(old, other)
        stamp_job_residency(held, base)
        stamp_job_residency(cancelled, base)
        request_cancel(cancelled)
        eligible = [
            entry for entry in ((1, old), (2, held), (3, cancelled))
            if entry[1].get("status") == "queued"
            and not entry[1].get("queue_held", False)
        ]
        selected, reason, _ = _select_next_waiter(eligible)
        self.assertIs(selected[1], old)
        self.assertEqual(reason, "queue_order")

        held["queue_held"] = False
        invalidate_residency_state()
        selected, reason, _ = _select_next_waiter([(1, old), (2, held)])
        self.assertIs(selected[1], old)
        self.assertEqual(reason, "queue_order")

    def test_residency_keys_are_opaque_and_raw_keys_are_rejected(self):
        key = make_residency_key(
            "wgp-generation-v1", "model-a", {"profile": 4},
        )
        self.assertRegex(key, r"^r1:[0-9a-f]{64}$")
        self.assertNotIn("model-a", key)
        self.assertNotIn("profile", key)
        with self.assertRaises(ValueError):
            stamp_job_residency(_job(), "/private/model/path")

    def test_priority_change_is_rejected_after_start(self):
        job = _job()
        self.assertTrue(try_start(job))
        self.assertFalse(update_queue_job(job, priority=99))

    def test_start_next_clears_global_pause_and_job_hold(self):
        job = {**_job(), "queue_held": True, "queue_priority": -4}
        self.assertTrue(set_queue_paused(True)["paused"])
        self.assertEqual(queue_wait_reason(job), "held")
        self.assertTrue(promote_queued_job(job))
        self.assertFalse(job["queue_held"])
        self.assertEqual(job["queue_priority"], 1_000_000)
        self.assertEqual(job["message"], "Queued — starting next")
        self.assertEqual(queue_control_state(), {
            "paused": False, "pause_after_current": False,
        })

    def test_repeated_start_next_is_last_selection_wins_over_residency(self):
        resident_key = make_residency_key("base", "resident")
        other_key = make_residency_key("base", "other")
        note_residency_state(resident_key)
        first = {**_job(), "id": "first", "created_at": 1}
        second = {**_job(), "id": "second", "created_at": 2}
        stamp_job_residency(first, resident_key)
        stamp_job_residency(second, other_key)
        self.assertTrue(promote_queued_job(first))
        self.assertTrue(promote_queued_job(second))

        selected, reason, skipped = _select_next_waiter([
            (1, first), (2, second),
        ])
        self.assertIs(selected[1], second)
        self.assertEqual(reason, "queue_order")
        self.assertEqual(skipped, [])

    def test_wait_reason_distinguishes_pause_hold_registration_turn_and_gpu(self):
        generation_lock = threading.Lock()
        generation_lock.acquire()
        first = {**_job(), "id": "first", "created_at": 1}
        second = {**_job(), "id": "second", "created_at": 2}
        started = threading.Event()

        self.assertEqual(queue_wait_reason(first), "registering")
        self.assertTrue(update_queue_job(first, held=True))
        self.assertEqual(queue_wait_reason(first), "held")
        self.assertTrue(update_queue_job(first, held=False))
        self.assertTrue(set_queue_paused(True)["paused"])
        self.assertEqual(queue_wait_reason(first), "queue_paused")
        set_queue_paused(False)

        def wait(candidate):
            started.set()
            acquire_generation_slot(generation_lock, candidate, poll_interval=0.005)

        first_thread = threading.Thread(target=wait, args=(first,))
        second_thread = threading.Thread(target=wait, args=(second,))
        first_thread.start()
        self.assertTrue(started.wait(timeout=1))
        second_thread.start()
        deadline = time.time() + 1
        while queue_wait_reason(second) == "registering" and time.time() < deadline:
            time.sleep(0.005)
        self.assertEqual(
            queue_wait_reason(first, generation_busy=True),
            "waiting_for_active_generation",
        )
        self.assertEqual(
            queue_wait_reason(
                first,
                generation_busy=True,
                active_other_user=True,
            ),
            "waiting_for_other_user",
        )
        self.assertEqual(queue_wait_reason(second), "waiting_for_turn")
        request_cancel(first)
        request_cancel(second)
        generation_lock.release()
        first_thread.join(timeout=1)
        second_thread.join(timeout=1)

    def test_start_next_rejects_running_or_cancelled_job(self):
        running = _job()
        self.assertTrue(try_start(running))
        self.assertFalse(promote_queued_job(running))
        cancelled = _job()
        request_cancel(cancelled)
        self.assertFalse(promote_queued_job(cancelled))

    def test_finish_cancel_race_has_only_valid_outcomes(self):
        for _ in range(50):
            job = _job()
            states: dict = {}
            state = {"abort": False}
            interrupt = Mock()
            self.assertTrue(try_start(job))
            self.assertTrue(register_abort_state(
                job, job["id"], states, state, interrupt_model=interrupt,
            ))
            barrier = threading.Barrier(3)

            def complete():
                barrier.wait()
                finish_job(job, "completed", message="Done")

            def cancel():
                barrier.wait()
                request_cancel(
                    job, job_id=job["id"], active_states=states,
                )

            finish_thread = threading.Thread(target=complete)
            cancel_thread = threading.Thread(target=cancel)
            finish_thread.start()
            cancel_thread.start()
            barrier.wait()
            finish_thread.join(timeout=1)
            cancel_thread.join(timeout=1)

            if job["status"] == "completed":
                self.assertFalse(state["abort"])
                interrupt.assert_not_called()
            else:
                self.assertEqual(job["status"], "cancelled")
                self.assertTrue(state["abort"])
                interrupt.assert_called_once_with()
            unregister_abort_state(job["id"], states, state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
