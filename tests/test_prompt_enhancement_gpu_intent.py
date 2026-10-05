"""Durable GPU cleanup checkpoints and real disposable-journal recovery, CPU only."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from services import job_lifecycle as lifecycle
from services.queue_recovery import QueueRecoveryJournal
from services.queue_recovery_adapter import (
    QueueRecoveryAdapterError, QueueRecoveryCoordinator, owner_principal_digest,
    project_instance_digest, prompt_enhancement_gpu_cleanup_pending,
)


def intent(state="request_attempted"):
    return {"schema": "maestro.h3-prompt-rewriter.gpu-intent.v1", "request_id": "request-1",
        "agent_id": "agent-1", "project_id": "maestro-local", "binding_sha256": "a" * 64,
        "minimum_remaining_seconds": 750, "stop_margin_seconds": 150, "state": state}


def job(job_id="enhance-1", status="queued", cursor=None):
    return {"id": job_id, "kind": "prompt_enhancement", "logical_job_kind": "prompt_enhancement",
        "status": status, "execution_attempt": 1, "resource_intent": "text",
        "resource_execution": "standard", "resource_state": "queued", "preemption_mode": "none",
        "recovery_cursor": {} if cursor is None else cursor}


class IntentCheckpointTests(unittest.TestCase):
    def setUp(self):
        lifecycle._reset_queue_state_for_tests()
        self.addCleanup(lifecycle._reset_queue_state_for_tests)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = QueueRecoveryJournal(Path(self.temp.name) / "queue.jsonl")
        self.coordinator = QueueRecoveryCoordinator(self.journal)
        self.value = job(cursor={"continuation": {"unit": 3}})
        self.register(self.value)
        lifecycle.configure_durability_hook(self.coordinator.prospective_transition)

    def register(self, value):
        self.coordinator.register_job(value,
            owner_digest=owner_principal_digest(b"cpu-intent-test-secret", "owner"),
            project_digest=project_instance_digest(b"cpu-intent-test-secret", "a" * 32),
            request_manifest={"kind": "prompt_enhancement"})

    def checkpoint(self, value=None, record=None, **options):
        return lifecycle.checkpoint_prompt_enhancement_gpu_intent(self.value if value is None else value,
            expected_execution_attempt=options.pop("expected_execution_attempt", 1),
            intent=intent() if record is None else record, **options)

    def install_hook(self, hook):
        lifecycle.configure_durability_hook(None)
        lifecycle.configure_durability_hook(hook)

    def test_persists_before_live_mutation_and_preserves_cursor_and_resources(self):
        observed = []
        before = deepcopy(self.value)
        def persist(proposal):
            observed.append(self.value == before)
            self.coordinator.prospective_transition(proposal)
        self.install_hook(persist)
        self.assertTrue(self.checkpoint(child_reaped=True))
        self.assertEqual(observed, [True])
        recovered = self.journal.recover().jobs[self.value["id"]]
        self.assertEqual(recovered["recovery_cursor"], self.value["recovery_cursor"])
        self.assertEqual(self.value["recovery_cursor"]["h3_prompt_rewriter_gpu"], intent())
        self.assertEqual(self.value["recovery_cursor"]["continuation"], {"unit": 3})
        self.assertIs(recovered["recovery_cursor"]["h3_prompt_rewriter_child_reaped"], True)
        self.assertEqual({key: self.value[key] for key in before if key != "recovery_cursor"},
                         {key: before[key] for key in before if key != "recovery_cursor"})

    def test_failed_or_absent_persistence_never_acknowledges_publication(self):
        before, durable = deepcopy(self.value), self.journal.recover().jobs
        with patch.object(self.journal, "commit_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.checkpoint(child_reaped=False)
        self.assertEqual(self.value, before)
        self.assertEqual(self.journal.recover().jobs, durable)
        lifecycle.configure_durability_hook(None)
        self.assertFalse(self.checkpoint())
        self.assertEqual(self.value, before)

    def test_cancellation_during_persist_cannot_revive_live_or_durable_job(self):
        for stage in ("before", "after"):
            with self.subTest(stage=stage):
                value = job("cancel-" + stage)
                self.register(value)
                delivered = []
                def persist(proposal):
                    target = proposal.name == "prompt_enhancement_gpu_intent" and not delivered
                    if target and stage == "before":
                        delivered.append(True)
                        lifecycle.request_cancel(value)
                    self.coordinator.prospective_transition(proposal)
                    if target and stage == "after":
                        delivered.append(True)
                        lifecycle.request_cancel(value)
                self.install_hook(persist)
                self.assertFalse(self.checkpoint(value))
                self.assertEqual(value["status"], "cancelled")
                recovered = self.journal.recover().jobs[value["id"]]
                self.assertEqual(recovered["status"], "cancelled")
                self.assertNotIn("h3_prompt_rewriter_gpu", recovered["recovery_cursor"])

    def test_retry_during_cleanup_persist_fences_old_attempt_in_memory_and_journal(self):
        self.assertTrue(self.checkpoint(child_reaped=False))
        self.assertTrue(lifecycle.try_start(self.value, expected_execution_attempt=1))
        self.assertTrue(lifecycle.finish_job(self.value, "failed", expected_execution_attempt=1))
        delivered = []
        def persist(proposal):
            if proposal.name == "prompt_enhancement_gpu_intent" and not delivered:
                delivered.append(True)
                self.assertTrue(lifecycle.retry_failed_recovery_job(self.value,
                    expected_execution_attempt=1, expected_recovery_attempt=0,
                    recovery_attempt=1, recovery_state="retrying"))
            self.coordinator.prospective_transition(proposal)
        self.install_hook(persist)
        self.assertFalse(self.checkpoint(record=intent("withdraw_attempted"), child_reaped=True))
        self.assertEqual(self.value["execution_attempt"], 2)
        self.assertEqual(self.value["status"], "queued")
        recovered = self.journal.recover().jobs[self.value["id"]]
        self.assertEqual(recovered["execution_attempt"], 2)
        self.assertEqual(recovered["recovery_cursor"]["h3_prompt_rewriter_gpu"]["state"], "request_attempted")
        self.assertFalse(self.checkpoint(record=intent("withdraw_attempted"), child_reaped=True))

    def test_terminal_cleanup_updates_evidence_without_status_or_resource_revival(self):
        for status in ("completed", "cancelled", "failed"):
            with self.subTest(status=status):
                value = job("terminal-" + status)
                self.register(value)
                self.install_hook(self.coordinator.prospective_transition)
                self.assertTrue(self.checkpoint(value, child_reaped=False))
                self.assertTrue(lifecycle.try_start(value, expected_execution_attempt=1))
                if status == "cancelled":
                    lifecycle.request_cancel(value)
                else:
                    self.assertTrue(lifecycle.finish_job(value, status, expected_execution_attempt=1))
                before = deepcopy({key: field for key, field in value.items() if key != "recovery_cursor"})
                self.assertFalse(self.checkpoint(value))
                self.assertFalse(self.checkpoint(value, intent("withdraw_attempted"), child_reaped=False))
                self.assertTrue(self.checkpoint(value, intent("withdraw_attempted"), child_reaped=True))
                self.assertTrue(self.checkpoint(value, intent("closed"), child_reaped=True))
                self.assertEqual({key: field for key, field in value.items() if key != "recovery_cursor"}, before)

    def test_identity_and_one_way_states_are_immutable_after_first_record(self):
        self.assertTrue(self.checkpoint())
        for key, replacement in {"request_id": "other", "agent_id": "other", "project_id": "other",
                "binding_sha256": "b" * 64, "minimum_remaining_seconds": 800, "stop_margin_seconds": 200}.items():
            with self.subTest(key=key):
                self.assertFalse(self.checkpoint(record={**intent(), key: replacement}))
        self.assertTrue(self.checkpoint())
        self.assertTrue(self.checkpoint(record=intent("withdraw_attempted")))
        self.assertFalse(self.checkpoint())
        self.assertTrue(self.checkpoint(record=intent("closed"), child_reaped=True))
        self.assertTrue(self.checkpoint(record=intent("closed")))
        self.assertFalse(self.checkpoint(record=intent("unpublished")))
        other = job("unpublished")
        self.register(other)
        self.assertTrue(self.checkpoint(other))
        self.assertTrue(self.checkpoint(other, intent("unpublished"), child_reaped=True))
        self.assertFalse(self.checkpoint(other))

    def test_malformed_pathful_or_wrong_attempt_records_do_not_persist(self):
        before = self.journal.path.read_bytes()
        bad = [None, {}, {**intent(), "workspace": "/private"}, {**intent(), "state": []},
            {**intent(), "agent_id": "/private"}, {**intent(), "binding_sha256": "x" * 64},
            {**intent(), "minimum_remaining_seconds": float("nan")},
            {**intent(), "minimum_remaining_seconds": 10 ** 500}, {**intent(), "stop_margin_seconds": True}]
        for record in bad:
            with self.subTest(record=repr(record)[:80]):
                self.assertFalse(lifecycle.checkpoint_prompt_enhancement_gpu_intent(self.value,
                    expected_execution_attempt=1, intent=record))
        for attempt in (True, 0, 2):
            self.assertFalse(self.checkpoint(expected_execution_attempt=attempt))
        self.assertEqual(self.journal.path.read_bytes(), before)

    def test_native_compaction_retains_unsettled_and_malformed_intents_until_reaped_settlement(self):
        self.install_hook(self.coordinator.prospective_transition)
        retained = {}
        cases = [("queued-lease", intent(), True), ("withdrawal", intent("withdraw_attempted"), True),
            ("malformed", None, True), ("unknown", {**intent(), "state": "unknown"}, True),
            ("not-reaped", intent("closed"), False), ("unknown-reap", intent("closed"), "unknown"),
            ("absent-reap", intent("closed"), None)]
        for name, record, reaped in cases:
            cursor = {"h3_prompt_rewriter_gpu": record}
            if reaped is not None:
                cursor["h3_prompt_rewriter_child_reaped"] = reaped
            value = job(name, "cancelled", cursor)
            self.register(value)
            retained[name] = value
        for state in ("closed", "unpublished"):
            self.register(job("settled-" + state, "completed", {
                "h3_prompt_rewriter_gpu": intent(state), "h3_prompt_rewriter_child_reaped": True}))
        compacted = self.coordinator.compact()
        self.assertEqual(set(compacted.jobs) - {self.value["id"]}, set(retained))
        fresh = QueueRecoveryCoordinator(self.journal)
        recovered = fresh.restore().jobs
        self.assertEqual(set(recovered) - {self.value["id"]}, set(retained))
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "cleanup is pending"):
            fresh.tombstone_terminal("queued-lease")
        self.install_hook(fresh.prospective_transition)
        self.assertTrue(self.checkpoint(retained["queued-lease"], intent("withdraw_attempted"), child_reaped=True))
        self.assertTrue(self.checkpoint(retained["queued-lease"], intent("closed"), child_reaped=True))
        self.assertNotIn("queued-lease", fresh.compact().jobs)
        self.assertTrue(prompt_enhancement_gpu_cleanup_pending(recovered["malformed"]))

    def test_unconfirmed_terminal_child_keeps_physical_slot_until_durable_reap(self):
        for status in ("failed", "cancelled"):
            for reaped in (None, False):
                with self.subTest(status=status, reaped=reaped):
                    value = job(f"owned-{status}-{reaped}")
                    self.register(value)
                    self.assertTrue(self.checkpoint(value, child_reaped=reaped))
                    lock = threading.Lock()
                    self.assertTrue(lifecycle.acquire_and_start_generation_slot(lock, value))
                    if status == "cancelled":
                        lifecycle.request_cancel(value)
                    else:
                        self.assertTrue(lifecycle.finish_job(value, status))
                    self.assertFalse(lifecycle.release_generation_slot(lock, value))
                    self.assertFalse(lifecycle.complete_prompt_enhancement_resource_release(value))
                    self.assertTrue(lock.locked())
                    self.assertIs(value["_generation_slot_owned"], True)
                    with patch.object(self.journal, "commit_state", side_effect=OSError("disk full")):
                        with self.assertRaises(OSError):
                            self.checkpoint(value, intent("withdraw_attempted"), child_reaped=True)
                    self.assertFalse(lifecycle.release_generation_slot(lock, value))
                    self.assertTrue(self.checkpoint(value, intent("withdraw_attempted"), child_reaped=True))
                    self.assertTrue(lifecycle.release_generation_slot(lock, value))
                    self.assertTrue(lifecycle.complete_prompt_enhancement_resource_release(value))
                    self.assertFalse(lock.locked())
                    self.assertEqual(value["resource_state"], "released")

    def test_malformed_child_or_intent_never_releases_an_owned_slot(self):
        for cursor in ({"h3_prompt_rewriter_gpu": None, "h3_prompt_rewriter_child_reaped": True},
                       {"h3_prompt_rewriter_gpu": intent(), "h3_prompt_rewriter_child_reaped": "true"},
                       {"h3_prompt_rewriter_gpu": {**intent(), "state": "unknown"},
                        "h3_prompt_rewriter_child_reaped": True}):
            with self.subTest(cursor=cursor):
                lock = threading.Lock()
                value = job("malformed-" + str(len(self.coordinator.restore().jobs)), cursor=cursor)
                self.register(value)
                self.assertTrue(lifecycle.acquire_and_start_generation_slot(lock, value))
                lifecycle.request_cancel(value)
                self.assertFalse(lifecycle.release_generation_slot(lock, value))
                self.assertFalse(lifecycle.complete_prompt_enhancement_resource_release(value))
                self.assertTrue(lock.locked())
                self.assertIs(value["_generation_slot_owned"], True)

    def test_fresh_lock_restore_blocks_cancellably_until_positive_durable_reap(self):
        self.assertTrue(self.checkpoint(child_reaped=False))
        self.assertTrue(lifecycle.try_start(self.value))
        self.assertTrue(lifecycle.finish_job(self.value, "failed"))
        restored = self.journal.recover().jobs[self.value["id"]]
        cancelled = job("waiting-cancel")
        self.register(cancelled)
        lifecycle.restore_scheduler_state([restored, cancelled], {})
        lock = threading.Lock()
        done, results = threading.Event(), []
        def wait(value):
            results.append(lifecycle.acquire_and_start_generation_slot(lock, value, poll_interval=0.01))
            done.set()
        def start_waiter(value):
            thread = threading.Thread(target=wait, args=(value,), daemon=True)
            def stop():
                if thread.is_alive():
                    lifecycle.request_cancel(value)
                    thread.join(1)
            self.addCleanup(stop)
            thread.start()
            return thread
        thread = start_waiter(cancelled)
        self.assertFalse(done.wait(0.05))
        self.assertFalse(lock.locked())
        lifecycle.request_cancel(cancelled)
        self.assertTrue(done.wait(1))
        thread.join(1)
        self.assertEqual(results, [False])
        waiting = job("waiting-reap")
        self.register(waiting)
        done.clear()
        thread = start_waiter(waiting)
        saved_cursor = restored["recovery_cursor"]
        restored["recovery_cursor"] = {}
        self.assertFalse(done.wait(0.05))
        restored["recovery_cursor"] = {**saved_cursor, "h3_prompt_rewriter_child_reaped": "true"}
        self.assertFalse(done.wait(0.05))
        restored["recovery_cursor"] = saved_cursor
        self.assertTrue(self.checkpoint(restored, intent("withdraw_attempted"), child_reaped=True))
        self.assertTrue(done.wait(1))
        thread.join(1)
        self.assertEqual(results, [False, True])
        self.assertTrue(lock.locked())
        self.assertTrue(lifecycle.release_generation_slot(lock, waiting))


if __name__ == "__main__":
    unittest.main()
