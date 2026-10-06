"""Focused durable marker coverage for logical Reference jobs."""

from pathlib import Path
import sys
import tempfile
import unittest
import types
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
import services.queue_recovery_adapter as recovery_adapter


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.queue_recovery import QueueRecoveryJournal  # noqa: E402
from services.queue_recovery_adapter import (  # noqa: E402
    PromptEnhancementRecoveryConflictError,
    PromptEnhancementRecoveryCorruptionError,
    PromptEnhancementRecoveryStore,
    PromptEnhancementResultStore,
    QueueRecoveryAdapterError,
    QueueRecoveryCoordinator,
    _durable_order_key,
    owner_principal_digest,
    project_instance_digest,
    serialize_job,
)


SECRET = b"logical-reference-recovery-test-secret"
OWNER = owner_principal_digest(SECRET, "owner-session")
PROJECT = project_instance_digest(SECRET, "a" * 32)


def _serialize(job):
    return serialize_job(
        job,
        owner_digest=OWNER,
        project_digest=PROJECT,
        request_manifest={"kind": "reference-test"},
    )


class LogicalReferenceRecoveryTests(unittest.TestCase):
    def _studio_scope(self):
        return {"scope_digest": "a" * 64, "owner_digest": OWNER,
                "project_digest": PROJECT, "workspace": "project-a", "request_digest": "b" * 64}

    def _accept_studio(self, coordinator, request_id, job_id, *, held=False):
        job = {"id": job_id, "workspace": "project-a", "status": "queued", "queue_held": held,
               "kind": "studio_generation", "params": {"prompt": "PRIVATE CONTENT", "image_start": "/private/input"}}
        coordinator.register_job(job, owner_digest=OWNER, project_digest=PROJECT,
            request_manifest={"path": ".maestro-recovery/" + job_id + ".request.json", "sha256": "f" * 64, "size": 10, "schema": 1},
            global_state={"paused": True}, generation_request_id=request_id)
        return job

    def test_studio_concurrent_reservation_admits_once_and_replays_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            request_id, job_id = str(uuid.uuid4()), uuid.uuid4().hex
            barrier = threading.Barrier(4)
            def attempt():
                barrier.wait()
                return coordinator.reserve_studio_submission(request_id, job_id=job_id, **self._studio_scope())
            with ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(lambda _: attempt(), range(4)))
            self.assertEqual(sum(fresh for fresh, _ in outcomes), 1)
            self.assertEqual(len(journal.recover().jobs), 0)
            self._accept_studio(coordinator, request_id, job_id, held=True)
            fresh = QueueRecoveryCoordinator(journal)
            record = fresh.lookup_studio_submission(request_id, **self._studio_scope())
            self.assertTrue(record["accepted"])
            self.assertEqual(record["job_id"], job_id)
            self.assertTrue(record["snapshot"]["queue_held"])
            self.assertTrue(journal.recover().global_state["paused"])
            self.assertFalse(fresh.reserve_studio_submission(request_id, job_id=uuid.uuid4().hex, **self._studio_scope())[0])
            raw = json.dumps(journal.recover().global_state)
            self.assertNotIn("PRIVATE CONTENT", raw)
            self.assertNotIn("/private/input", raw)
            self.assertNotIn("PRIVATE CONTENT", (Path(directory) / "queue.jsonl").read_text())

    def test_studio_changed_payload_and_scope_never_replace_reserved_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(Path(directory) / "queue.jsonl"))
            request_id, job_id = str(uuid.uuid4()), uuid.uuid4().hex
            coordinator.reserve_studio_submission(request_id, job_id=job_id, **self._studio_scope())
            for field, value, error in (("request_digest", "c" * 64, recovery_adapter.StudioSubmissionConflict),
                                        ("scope_digest", "c" * 64, recovery_adapter.StudioSubmissionScopeError),
                                        ("project_digest", project_instance_digest(SECRET, "b" * 32), recovery_adapter.StudioSubmissionScopeError),
                                        ("owner_digest", owner_principal_digest(SECRET, "other-session"), recovery_adapter.StudioSubmissionScopeError)):
                scope = dict(self._studio_scope(), **{field: value})
                with self.subTest(field=field), self.assertRaises(error):
                    coordinator.reserve_studio_submission(request_id, job_id=uuid.uuid4().hex, **scope)
            self.assertEqual(coordinator.lookup_studio_submission(request_id, **self._studio_scope())["job_id"], job_id)

    def test_studio_terminal_receipts_survive_controls_dismissal_and_compaction(self):
        for status, retire in (("completed", "compact"), ("cancelled", "compact"), ("failed", "dismiss")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
                coordinator = QueueRecoveryCoordinator(journal)
                request_id, job_id = str(uuid.uuid4()), uuid.uuid4().hex
                coordinator.reserve_studio_submission(request_id, job_id=job_id, **self._studio_scope())
                job = self._accept_studio(coordinator, request_id, job_id)
                job["status"] = status
                coordinator.prospective_transition(types.SimpleNamespace(jobs=(job,), tombstones=(), global_state=None))
                coordinator.prospective_transition(types.SimpleNamespace(jobs=(), tombstones=(), global_state={"paused": False, "queue_order": []}))
                if retire == "dismiss":
                    coordinator.tombstone_terminal(job_id)
                coordinator.compact()
                fresh = QueueRecoveryCoordinator(journal)
                fresh.restore()
                record = fresh.lookup_studio_submission(request_id, **self._studio_scope())
                self.assertTrue(record["accepted"])
                self.assertEqual(record["status"], status)
                self.assertIsNone(record["snapshot"])
                self.assertFalse(fresh.reserve_studio_submission(request_id, job_id=uuid.uuid4().hex, **self._studio_scope())[0])
                self.assertEqual(journal.recover().jobs, {})

    def test_studio_ambiguous_reservation_and_registration_ack_reconcile_durable_authority(self):
        for stage, failure in ((stage, failure) for stage in ("reservation", "registration") for failure in ("append_ack", "cache_ack")):
            with self.subTest(stage=stage, failure=failure), tempfile.TemporaryDirectory() as directory:
                journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
                coordinator = QueueRecoveryCoordinator(journal)
                request_id, job_id = str(uuid.uuid4()), uuid.uuid4().hex
                real_commit = journal.commit_state
                def lost_ack(**kwargs):
                    real_commit(**kwargs)
                    raise OSError("injected post-fsync ACK loss")
                if stage == "registration":
                    coordinator.reserve_studio_submission(request_id, job_id=job_id, **self._studio_scope())
                patcher = (mock.patch.object(journal, "commit_state", side_effect=lost_ack) if failure == "append_ack"
                           else mock.patch.object(coordinator, "_accept_receipt", side_effect=OSError("injected cache ACK loss")))
                with patcher, self.assertRaises(OSError):
                    if stage == "reservation":
                        coordinator.reserve_studio_submission(request_id, job_id=job_id, **self._studio_scope())
                    else:
                        self._accept_studio(coordinator, request_id, job_id)
                record = coordinator.lookup_studio_submission(request_id, **self._studio_scope())
                self.assertEqual(record["accepted"], stage == "registration")
                self.assertEqual(record["job_id"], job_id)
                self.assertFalse(coordinator.reserve_studio_submission(request_id, job_id=uuid.uuid4().hex, **self._studio_scope())[0])
                self.assertEqual(len(journal.recover().jobs), int(stage == "registration"))

    def test_studio_capacity_rejects_new_ids_without_eviction_or_journal_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            request_id = str(uuid.uuid4())
            coordinator.reserve_studio_submission(request_id, job_id=uuid.uuid4().hex, **self._studio_scope())
            before = (Path(directory) / "queue.jsonl").read_bytes()
            for limits in ({"STUDIO_SUBMISSION_MAX_RECORDS": 1}, {"STUDIO_SUBMISSION_MAX_BYTES": len(json.dumps(journal.recover().global_state["studio_submissions"], sort_keys=True, separators=(",", ":")).encode()) + 1}):
                with self.subTest(limits=limits), mock.patch.multiple(recovery_adapter, **limits):
                    with self.assertRaisesRegex(recovery_adapter.StudioSubmissionCapacityError, "history is full"):
                        coordinator.reserve_studio_submission(str(uuid.uuid4()), job_id=uuid.uuid4().hex, **self._studio_scope())
                    self.assertIsNotNone(coordinator.lookup_studio_submission(request_id, **self._studio_scope()))
                    self.assertEqual((Path(directory) / "queue.jsonl").read_bytes(), before)

    def test_studio_corrupt_ledger_and_external_controls_replacement_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            request_id = str(uuid.uuid4())
            coordinator.reserve_studio_submission(request_id, job_id=uuid.uuid4().hex, **self._studio_scope())
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "cannot be replaced"):
                coordinator.prospective_transition(types.SimpleNamespace(jobs=(), tombstones=(), global_state={"studio_submissions": {"schema_version": 1, "records": {}}}))
            state = journal.recover().global_state
            state["studio_submissions"]["records"][request_id]["raw_prompt"] = "PRIVATE CONTENT"
            journal.commit_state(global_state=state, expected_epoch=journal.recover().epoch, expected_global_revision=journal.recover().global_revision)
            with self.assertRaises(QueueRecoveryAdapterError):
                coordinator.reserve_studio_submission(str(uuid.uuid4()), job_id=uuid.uuid4().hex, **self._studio_scope())
            self.assertEqual(len(journal.recover().global_state["studio_submissions"]["records"]), 1)

    def test_cancelled_tool_intent_survives_compaction_and_dismissal_until_settled(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QueueRecoveryCoordinator(QueueRecoveryJournal(Path(directory) / 'queue.jsonl'))
            pending = {'id': 'pending-tool', 'kind': 'tool_hflip', 'status': 'cancelled',
                       'recovery_cursor': {'processed_tool_publication': {'schema_version': 1}}}
            jobs = [pending,
                    {'id': 'pending-editor', 'kind': 'tool_editor_export', 'status': 'cancelled',
                     'recovery_cursor': {'editor_export_publication': {'schema_version': 1}}},
                    {'id': 'composition-cancel', 'kind': 'tool_editor_export', 'status': 'cancelled',
                     'recovery_cursor': {'composition': {}, 'editor_export_publication': {}}},
                    {'id': 'ordinary-cancel', 'kind': 'generation', 'status': 'cancelled'},
                    {'id': 'settled-tool', 'kind': 'tool_hflip', 'status': 'cancelled'},
                    {'id': 'completed-tool', 'kind': 'tool_hflip', 'status': 'completed',
                     'recovery_cursor': pending['recovery_cursor']}]
            for job in jobs:
                coordinator.register_job(job, owner_digest=OWNER, project_digest=PROJECT, request_manifest={'kind': 'test'})
            self.assertEqual(set(coordinator.compact().jobs), {'pending-tool', 'pending-editor'})
            for job_id in ('pending-tool', 'pending-editor'):
                with self.assertRaisesRegex(QueueRecoveryAdapterError, 'cleanup is pending'):
                    coordinator.tombstone_terminal(job_id)
            fresh = QueueRecoveryCoordinator(coordinator.journal)
            fresh.restore()
            pending['recovery_cursor'] = {}
            fresh.prospective_transition(types.SimpleNamespace(jobs=(pending,), tombstones=(), global_state=None))
            editor = next(job for job in jobs if job['id'] == 'pending-editor')
            editor['recovery_cursor'] = {}
            fresh.prospective_transition(types.SimpleNamespace(jobs=(editor,), tombstones=(), global_state=None))
            self.assertEqual(fresh.compact().jobs, {})

    def test_composition_confirmation_receipts_survive_terminal_compaction_bounded_and_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=QueueRecoveryJournal(Path(directory)/"queue.jsonl")
            coordinator=QueueRecoveryCoordinator(journal)
            def job(job_id, status, records):
                return {"id":job_id,"kind":"tool_editor_export","status":status,
                        "recovery_cursor":{"composition":{"schema":"maestro/composition/v1",
                            "package_sha256":"a"*64,"requests":records}}}
            def record(status="accepted"):
                return {"status":status,"expected_created_at":1.0,"expected_execution_attempt":1}
            accepted={uuid.uuid4().hex:record()}
            valid=[job("completed-recovery","completed",accepted),
                   job("cancelled-confirmation","cancelled",{uuid.uuid4().hex:record("pending")})]
            invalid=[job("empty","completed",{}),
                     job("wrong-id","completed",{"not-a-uuid":record()}),
                     job("too-many","completed",{uuid.uuid4().hex:record() for _ in range(9)}),
                     job("private-extra","completed",{uuid.uuid4().hex:dict(record(),raw_prompt="private")}),
                     job("boolean-attempt","completed",{uuid.uuid4().hex:dict(record(),expected_execution_attempt=True)}),
                     job("two-pending","cancelled",{uuid.uuid4().hex:record("pending") for _ in range(2)})]
            foreign=job("other-kind","completed",accepted);foreign["kind"]="studio_generation"
            for candidate in valid+invalid+[foreign]:
                coordinator.register_job(candidate,owner_digest=OWNER,project_digest=PROJECT,request_manifest={"kind":"test"})
            self.assertEqual(set(coordinator.compact().jobs),{candidate["id"] for candidate in valid})
            restored=QueueRecoveryCoordinator(journal).restore()
            self.assertEqual(restored.jobs["completed-recovery"]["recovery_cursor"],valid[0]["recovery_cursor"])
            self.assertEqual(restored.jobs["completed-recovery"]["owner_principal"],OWNER)
            self.assertEqual(restored.jobs["completed-recovery"]["project_instance"],PROJECT)
            # Explicit dismissal remains a separate owner action; automatic
            # compaction alone cannot discard the confirmed request receipt.
            coordinator.tombstone_terminal("completed-recovery")
            self.assertNotIn("completed-recovery",QueueRecoveryCoordinator(journal).restore().jobs)

    def test_legacy_read_only_replay_requires_exact_request_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy"
            writable = PromptEnhancementRecoveryStore(root, SECRET)
            request_id = "0f6f2163-42f7-41d0-a260-79b697081d96"
            writable.bind(
                request_id=request_id,
                account_key="account",
                project_instance_key="project",
                session_key="session",
                request_digest="a" * 64,
            )
            legacy = PromptEnhancementRecoveryStore(
                root, SECRET, read_only=True,
            )
            replay = legacy.replay(
                request_id=request_id,
                account_key="account",
                project_instance_key="project",
                session_key="session",
                request_digest="a" * 64,
            )
            self.assertEqual(replay["status"], "queued")
            with self.assertRaises(PromptEnhancementRecoveryConflictError):
                legacy.replay(
                    request_id=request_id,
                    account_key="account",
                    project_instance_key="project",
                    session_key="session",
                    request_digest="b" * 64,
                )

    def test_legacy_read_only_replay_fails_closed_on_corrupt_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy"
            root.mkdir()
            (root / "operations.json").write_text(
                "not a sealed ledger", encoding="ascii",
            )
            legacy = PromptEnhancementRecoveryStore(
                root, SECRET, read_only=True,
            )
            with self.assertRaises(PromptEnhancementRecoveryCorruptionError):
                legacy.replay(
                    request_id="0f6f2163-42f7-41d0-a260-79b697081d96",
                    account_key="account",
                    project_instance_key="project",
                    session_key="session",
                    request_digest="a" * 64,
                )

    def test_result_store_saturation_preserves_other_unread_result(self):
        with tempfile.TemporaryDirectory() as directory:
            results = PromptEnhancementResultStore(
                Path(directory) / "prompt-results", max_records=1,
            )
            first = results.write(
                "0f6f2163-42f7-41d0-a260-79b697081d96", {"value": "first"},
            )
            with self.assertRaises(Exception) as raised:
                results.write(
                    "1f6f2163-42f7-41d0-a260-79b697081d96",
                    {"value": "second"},
                )
            self.assertIn("full", str(raised.exception))
            self.assertEqual(
                results.read(
                    "0f6f2163-42f7-41d0-a260-79b697081d96", first,
                ),
                {"value": "first"},
            )
            results.remove(first)
            second = results.write(
                "1f6f2163-42f7-41d0-a260-79b697081d96",
                {"value": "second"},
            )
            self.assertEqual(
                results.read(
                    "1f6f2163-42f7-41d0-a260-79b697081d96", second,
                ),
                {"value": "second"},
            )

    def test_prompt_enhancement_queue_snapshot_is_content_free_and_result_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            results = PromptEnhancementResultStore(
                Path(directory) / "prompt-results",
            )
            reference = results.write(
                "0f6f2163-42f7-41d0-a260-79b697081d96",
                {"original": "private original", "enhanced": "private result"},
            )
            snapshot = _serialize({
                "id": "0f6f216342f741d0a26079b697081d96",
                "status": "completed",
                "kind": "prompt_enhancement",
                "logical_job_kind": "prompt_enhancement",
                "phase": "completed",
                "resource_intent": "text",
                "resource_execution": "standard",
                "resource_state": "released",
                "preemption_mode": "none",
                "execution_attempt": 1,
                "params": {
                    "_prompt_enhancement_operation": {
                        "body": {"prompt": "private original"},
                    },
                },
                "prompt_result_reference": reference,
            })
            self.assertNotIn("params", snapshot)
            self.assertNotIn("private original", repr(snapshot))
            self.assertNotIn("private result", repr(snapshot))
            self.assertEqual(
                snapshot["logical_job_kind"], "prompt_enhancement",
            )
            self.assertEqual(snapshot["prompt_result_reference"], reference)
            self.assertEqual(
                results.read(
                    "0f6f2163-42f7-41d0-a260-79b697081d96", reference,
                )["enhanced"],
                "private result",
            )
            with self.assertRaises(QueueRecoveryAdapterError):
                _serialize({
                    "id": "spoofed-prompt-kind",
                    "status": "queued",
                    "kind": "generation",
                    "logical_job_kind": "prompt_enhancement",
                    "resource_intent": "generation",
                })

    def test_atomic_registration_restores_both_held_sample_arms_or_neither(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            jobs = (
                {
                    "id": "sample-maestro",
                    "status": "queued",
                    "queue_class": "background_sample",
                    "queue_priority": -1000,
                    "queue_held": True,
                    "created_at": 1,
                },
                {
                    "id": "sample-control",
                    "status": "queued",
                    "queue_class": "background_sample",
                    "queue_priority": -1000,
                    "queue_held": True,
                    "created_at": 2,
                },
            )
            coordinator.register_jobs_atomic(tuple(
                (job, OWNER, PROJECT, {"kind": "sample-arm"})
                for job in jobs
            ))
            restored = QueueRecoveryCoordinator(journal).restore().jobs

            self.assertEqual(set(restored), {"sample-maestro", "sample-control"})
            for snapshot in restored.values():
                self.assertEqual(snapshot["queue_class"], "background_sample")
                self.assertEqual(snapshot["queue_priority"], -1000)
                self.assertTrue(snapshot["queue_held"])

        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "queue_class"):
                coordinator.register_jobs_atomic((
                    (
                        jobs[0], OWNER, PROJECT, {"kind": "sample-arm"},
                    ),
                    (
                        {**jobs[1], "queue_class": "invalid"},
                        OWNER,
                        PROJECT,
                        {"kind": "sample-arm"},
                    ),
                ))
            self.assertEqual(
                QueueRecoveryCoordinator(journal).restore().jobs,
                {},
            )

            with self.assertRaisesRegex(QueueRecoveryAdapterError, "job is invalid"):
                coordinator.register_jobs_atomic((
                    (
                        jobs[0], OWNER, PROJECT, {"kind": "sample-arm"},
                    ),
                    (
                        "not-a-job",  # type: ignore[arg-type]
                        OWNER,
                        PROJECT,
                        {"kind": "sample-arm"},
                    ),
                ))
            self.assertEqual(coordinator.restore().jobs, {})

    def test_h3_mixed_registration_disk_failure_and_duplicate_keep_parent_exact(self):
        from copy import deepcopy
        from unittest.mock import patch
        source = {"id": "aaaaaaaa", "status": "failed", "execution_attempt": 1}
        child_id = "b" * 32
        source_control = {"schema_version": 1, "role": "source", "manual_retry_count": 0, "manual_retry_limit": 2,
            "active_child_id": child_id, "active_action": "accept_native", "active_intent_digest": "c" * 64,
            "last_charged_child_id": None, "consumed": False, "completed_child_id": None, "completed_unit_id": None}
        child = {"id": child_id, "status": "queued", "kind": "studio_h3_delivery_recovery", "parent_job_id": source["id"],
            "h3_delivery_recovery_control": {"schema_version": 1, "role": "child", "source_job_id": source["id"],
                "action": "accept_native", "intent_digest": "c" * 64, "charged": False}}
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            coordinator.register_job(source, owner_digest=OWNER, project_digest=PROJECT, request_manifest={"kind": "source"})
            before = deepcopy(coordinator._snapshots)
            def register():
                coordinator.register_h3_delivery_child_atomic(dict(source, h3_delivery_recovery_control=source_control), child,
                    owner_digest=OWNER, project_digest=PROJECT, request_manifest={"kind": "child"}, expected_control=None)
            with patch.object(journal, "commit_state", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    register()
            self.assertEqual(coordinator._snapshots, before)
            self.assertEqual(QueueRecoveryCoordinator(journal).restore().jobs, before)
            register()
            restored = QueueRecoveryCoordinator(journal).restore().jobs
            self.assertEqual(set(restored), {source["id"], child_id})
            self.assertEqual(restored[source["id"]]["h3_delivery_recovery_control"], source_control)
            self.assertEqual(restored[child_id]["parent_job_id"], source["id"])
            with self.assertRaises(QueueRecoveryAdapterError):
                register()
            self.assertEqual(QueueRecoveryCoordinator(journal).restore().jobs, restored)
            for changed in ({**source_control, "manual_retry_count": True}, {**source_control, "unexpected": "field"}):
                with self.subTest(control=changed), self.assertRaises(QueueRecoveryAdapterError):
                    _serialize(dict(source, h3_delivery_recovery_control=changed))

    def test_atomic_registration_rejects_duplicate_or_existing_job_without_partial_commit(self):
        job = {
            "id": "sample-arm",
            "status": "queued",
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": True,
        }
        registration = (job, OWNER, PROJECT, {"kind": "sample-arm"})
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            coordinator = QueueRecoveryCoordinator(journal)
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "duplicate"):
                coordinator.register_jobs_atomic((registration, registration))
            self.assertEqual(coordinator.restore().jobs, {})

            coordinator.register_job(
                job,
                owner_digest=OWNER,
                project_digest=PROJECT,
                request_manifest={"kind": "sample-arm"},
            )
            other = (
                {**job, "id": "other-arm"},
                OWNER,
                PROJECT,
                {"kind": "sample-arm"},
            )
            with self.assertRaisesRegex(QueueRecoveryAdapterError, "already registered"):
                coordinator.register_jobs_atomic((registration, other))
            self.assertEqual(set(coordinator.restore().jobs), {"sample-arm"})

    def test_background_sample_queue_class_is_strict_and_orders_after_users(self):
        background = _serialize({
            "id": "background-local",
            "status": "queued",
            "queue_class": "background_sample",
            "source_remote": False,
            "queue_priority": 1_000_000,
            "_queue_manual_order": 99,
            "created_at": 0,
        })
        remote_user = _serialize({
            "id": "remote-user",
            "status": "queued",
            "queue_class": "user",
            "source_remote": True,
            "queue_priority": -1_000_000,
            "created_at": 1,
        })
        legacy_local_user = _serialize({
            "id": "legacy-local-user",
            "status": "queued",
            "source_remote": False,
            "queue_priority": -1_000_000,
            "created_at": 2,
        })
        ordered = sorted(
            (background, remote_user, legacy_local_user),
            key=lambda job: _durable_order_key(job, 0),
        )
        self.assertEqual(
            [job["id"] for job in ordered],
            ["legacy-local-user", "remote-user", "background-local"],
        )
        self.assertEqual(background["queue_class"], "background_sample")
        self.assertEqual(remote_user["queue_class"], "user")
        self.assertNotIn("queue_class", legacy_local_user)
        for invalid in ("background", "sample", "", None, 3):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                QueueRecoveryAdapterError,
                "queue_class",
            ):
                _serialize({
                    "id": "invalid-queue-class",
                    "status": "queued",
                    "queue_class": invalid,
                })

    def test_sample_retry_defaults_and_strict_allowlist(self):
        defaulted = _serialize({
            "id": "sample-default",
            "kind": "sample_campaign_generation",
            "status": "queued",
            "queue_class": "background_sample",
        })
        self.assertEqual(defaulted["sample_retry"], {
            "attempt": 0, "not_before": None,
        })
        scheduled = _serialize({
            **defaulted,
            "id": "sample-scheduled",
            "sample_retry": {"attempt": 2, "not_before": 1234.5},
        })
        self.assertEqual(scheduled["sample_retry"], {
            "attempt": 2, "not_before": 1234.5,
        })
        invalid = (
            ({"attempt": 1}, "sample_retry"),
            ({"attempt": 0, "not_before": 1.0}, "sample_retry"),
            ({"attempt": 1, "not_before": None}, "sample_retry"),
            ({"attempt": True, "not_before": 1.0}, "sample_retry"),
            ({"attempt": 1, "not_before": float("nan")}, "sample_retry"),
        )
        for retry, message in invalid:
            with self.subTest(retry=retry), self.assertRaisesRegex(
                QueueRecoveryAdapterError, message,
            ):
                _serialize({
                    "id": "sample-invalid",
                    "kind": "sample_campaign_generation",
                    "status": "queued",
                    "queue_class": "background_sample",
                    "sample_retry": retry,
                })
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "reserved"):
            _serialize({
                "id": "user-invalid",
                "kind": "generation",
                "status": "queued",
                "queue_class": "user",
                "sample_retry": {"attempt": 1, "not_before": 10.0},
            })

    def test_preemption_requested_sample_restores_same_job_held(self):
        job = {
            "id": "sample-preempted",
            "kind": "sample_campaign_generation",
            "status": "running",
            "queue_class": "background_sample",
            "queue_priority": -1000,
            "queue_held": False,
            "resource_intent": "generation",
            "resource_execution": "standard",
            "preemption_mode": "none",
            "resource_state": "preemption_requested",
            "execution_attempt": 7,
            "sample_retry": {"attempt": 3, "not_before": 2000.0},
            "progress": 91,
            "step": 17,
            "overall_progress": 80,
            "window_progress": 70,
            "clip_progress": 60,
            "output_files": ["committed.mp4"],
            "artifact_files": ["committed.mp4"],
            "recovery_cursor": {
                "sample_campaign": {
                    "peer_job_id": "sample-peer",
                    "arm": "control",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            journal = QueueRecoveryJournal(Path(directory) / "queue.json")
            QueueRecoveryCoordinator(journal).register_job(
                job,
                owner_digest=OWNER,
                project_digest=PROJECT,
                request_manifest={"kind": "sample-arm"},
            )
            recovered = QueueRecoveryCoordinator(journal).restore().jobs[
                job["id"]
            ]
        self.assertEqual(recovered["id"], job["id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertTrue(recovered["queue_held"])
        self.assertEqual(recovered["resource_state"], "queued")
        self.assertEqual(recovered["execution_attempt"], 8)
        for field in (
            "progress", "step", "overall_progress",
            "window_progress", "clip_progress",
        ):
            self.assertEqual(recovered[field], 0)
        self.assertEqual(recovered["sample_retry"], job["sample_retry"])
        self.assertEqual(recovered["output_files"], ["committed.mp4"])
        self.assertEqual(
            recovered["recovery_cursor"]["sample_campaign"]["peer_job_id"],
            "sample-peer",
        )

    def test_sample_preemption_restart_state_is_strict(self):
        base = {
            "id": "sample-invalid-preemption",
            "kind": "sample_campaign_generation",
            "status": "running",
            "queue_class": "background_sample",
            "resource_state": "preemption_requested",
            "execution_attempt": 1,
        }
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "preemption"):
            _serialize(base)
        with self.assertRaisesRegex(QueueRecoveryAdapterError, "preemption"):
            _serialize({
                **base,
                "status": "queued",
                "sample_retry": {"attempt": 1, "not_before": 50.0},
            })

    def test_resource_retry_state_is_complete_bounded_and_round_trips(self):
        retry = _serialize({
            "id": "resource-retry", "status": "queued",
            "resource_retry_attempt": 1,
            "resource_retry_limit": 2,
            "resource_retry_phase": "model_load",
            "resource_retry_reason": "host_memory_pressure",
        })
        self.assertEqual(retry["resource_retry_attempt"], 1)
        self.assertEqual(retry["resource_retry_limit"], 2)
        self.assertEqual(retry["resource_retry_phase"], "model_load")
        self.assertEqual(
            retry["resource_retry_reason"], "host_memory_pressure",
        )

        invalid = (
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "model_load",
            },
            {
                "resource_retry_attempt": 3,
                "resource_retry_limit": 2,
                "resource_retry_phase": "model_load",
                "resource_retry_reason": "host_memory_pressure",
            },
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 9,
                "resource_retry_phase": "model_load",
                "resource_retry_reason": "host_memory_pressure",
            },
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "unknown",
                "resource_retry_reason": "host_memory_pressure",
            },
            {
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "generation",
                "resource_retry_reason": "finalization_oom",
            },
        )
        for index, fields in enumerate(invalid):
            with self.subTest(index=index), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                _serialize({
                    "id": f"bad-resource-{index}",
                    "status": "queued",
                    **fields,
                })

    def test_gpu_resource_retry_reconstructs_only_safe_oom_info(self):
        private = "/private/models/secret.safetensors traceback"
        retry = _serialize({
            "id": "gpu-resource-retry",
            "status": "queued",
            "resource_retry_attempt": 1,
            "resource_retry_limit": 2,
            "resource_retry_phase": "generation",
            "resource_retry_reason": "generation_oom",
            "failure_details": {
                "code": "cuda_oom",
                "stage": "denoise",
                "detail": private,
                "exception_type": "OutOfMemoryError",
                "is_oom": True,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 10,
                    "private_path": private,
                },
            },
            "oom_info": {
                "is_oom": True,
                "stage": "denoise",
                "current_coefficient": 0.8,
                "suggested_coefficient": 0.1,
                "message": private,
                "allocator": {
                    "device_type": "cuda",
                    "free_bytes": 10,
                    "private_path": private,
                },
                "traceback": private,
            },
        })
        self.assertEqual(retry["oom_info"], {
            "is_oom": True,
            "stage": "denoise",
            "current_coefficient": 0.8,
            "suggested_coefficient": 0.7,
            "message": "The operation ran out of GPU memory.",
            "allocator": {"device_type": "cuda", "free_bytes": 10},
        })
        self.assertNotIn(private, repr(retry))

        invalid_jobs = (
            {
                "failure_details": {
                    "code": "cuda_oom", "stage": "denoise",
                    "exception_type": "RuntimeError", "is_oom": True,
                },
                "oom_info": "raw traceback",
            },
            {
                "failure_details": {
                    "code": "cuda_oom", "stage": "denoise",
                    "exception_type": "RuntimeError", "is_oom": True,
                },
                "oom_info": {
                    "is_oom": True, "current_coefficient": float("inf"),
                },
            },
            {
                "failure_details": {
                    "code": "generation_failed", "stage": "generation",
                    "exception_type": "RuntimeError", "is_oom": False,
                },
                "oom_info": {"is_oom": True, "current_coefficient": 0.8},
            },
        )
        for index, updates in enumerate(invalid_jobs):
            with self.subTest(index=index), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                _serialize({
                    "id": f"bad-gpu-retry-{index}",
                    "status": "queued",
                    "resource_retry_attempt": 1,
                    "resource_retry_limit": 2,
                    "resource_retry_phase": "generation",
                    "resource_retry_reason": "generation_oom",
                    **updates,
                })

        with self.assertRaises(QueueRecoveryAdapterError):
            _serialize({
                "id": "bad-host-retry-oom",
                "status": "queued",
                "resource_retry_attempt": 1,
                "resource_retry_limit": 2,
                "resource_retry_phase": "model_load",
                "resource_retry_reason": "host_memory_pressure",
                "failure_details": {
                    "code": "cuda_oom", "stage": "generation",
                    "exception_type": "RuntimeError", "is_oom": True,
                },
                "oom_info": {"is_oom": True, "current_coefficient": 0.8},
            })

    def test_marker_is_strict_and_requires_the_corresponding_relation(self):
        parent = _serialize({
            "id": "reference-parent", "status": "queued",
            "logical_job_kind": "reference_pack_parent",
        })
        child = _serialize({
            "id": "reference-child", "status": "queued",
            "logical_job_kind": "reference_pack_child",
            "parent_job_id": "reference-parent",
        })
        self.assertEqual(parent["logical_job_kind"], "reference_pack_parent")
        self.assertEqual(child["logical_job_kind"], "reference_pack_child")
        self.assertEqual(child["parent_job_id"], "reference-parent")

        invalid = (
            {"id": "bad-kind", "status": "queued", "logical_job_kind": "reference"},
            {
                "id": "parent-with-parent", "status": "queued",
                "logical_job_kind": "reference_pack_parent",
                "parent_job_id": "other",
            },
            {
                "id": "child-without-parent", "status": "queued",
                "logical_job_kind": "reference_pack_child",
            },
            {
                "id": "self-child", "status": "queued",
                "logical_job_kind": "reference_pack_child",
                "parent_job_id": "self-child",
            },
        )
        for job in invalid:
            with self.subTest(job=job["id"]), self.assertRaises(
                QueueRecoveryAdapterError,
            ):
                _serialize(job)

    def test_marker_survives_journal_restart_without_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            journal = QueueRecoveryJournal(Path(temporary) / "queue.jsonl")
            coordinator = QueueRecoveryCoordinator(journal)
            jobs = (
                {
                    "id": "reference-parent", "status": "queued",
                    "logical_job_kind": "reference_pack_parent",
                },
                {
                    "id": "reference-child", "status": "queued",
                    "logical_job_kind": "reference_pack_child",
                    "parent_job_id": "reference-parent",
                },
                {
                    "id": "legacy-reference-looking", "status": "queued",
                    "message": "Reference child", "parent_job_id": "reference-parent",
                },
            )
            for job in jobs:
                coordinator.register_job(
                    job,
                    owner_digest=OWNER,
                    project_digest=PROJECT,
                    request_manifest={"kind": "reference-test"},
                )

            restored = QueueRecoveryCoordinator(journal).restore().jobs

        self.assertEqual(
            restored["reference-parent"]["logical_job_kind"],
            "reference_pack_parent",
        )
        self.assertEqual(
            restored["reference-child"]["logical_job_kind"],
            "reference_pack_child",
        )
        self.assertNotIn(
            "logical_job_kind", restored["legacy-reference-looking"],
        )


if __name__ == "__main__":
    unittest.main()
