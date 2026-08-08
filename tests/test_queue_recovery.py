"""Durability, fencing, and filesystem-adversary tests for queue recovery."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from services import queue_recovery  # noqa: E402
from services.queue_recovery import (  # noqa: E402
    QueueRecoveryCorruptionError,
    QueueRecoveryJournal,
    QueueRecoveryPersistenceError,
    QueueRecoveryValidationError,
)


def _canonical(value):
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _signed(record):
    unsigned = dict(record)
    unsigned.pop("checksum", None)
    value = dict(unsigned)
    value["checksum"] = hashlib.sha256(_canonical(unsigned)).hexdigest()
    return _canonical(value) + b"\n"


def _multiprocess_writer(path, prefix, count, result_queue):
    try:
        journal = QueueRecoveryJournal(path)
        for index in range(count):
            job_id = f"{prefix}-{index}"
            journal.commit_job(
                job_id,
                {"id": job_id, "status": "queued", "worker": prefix},
                expected_revision=0,
                expected_epoch=0,
            )
        result_queue.put("ok")
    except Exception as exc:  # pragma: no cover - parent asserts the type
        result_queue.put(type(exc).__name__)


class QueueRecoveryJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "queue-recovery.jsonl"
        self.journal = QueueRecoveryJournal(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def _job(self, job_id, status="queued", **updates):
        job = {
            "id": job_id,
            "status": status,
            "project_id": "synthetic-project",
            "prompt": "synthetic prompt",
            "settings": {"steps": 20, "resolution": "960x544"},
        }
        job.update(updates)
        return job

    def _lines(self, path=None):
        return (path or self.path).read_bytes().splitlines()

    def _assert_quarantined(self, path=None):
        path = path or self.path
        self.assertFalse(path.exists())
        matches = list(path.parent.glob(f".{path.name}.corrupt-*"))
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_atomic_state_batch_commits_correlated_jobs_and_global_once(self):
        receipt = self.journal.commit_state(
            jobs={
                "job-a": self._job("job-a"),
                "job-b": self._job("job-b", "held"),
            },
            global_state={"paused": False, "order": ["job-a", "job-b"]},
            expected_job_revisions={"job-a": 0, "job-b": 0},
            expected_global_revision=0,
            expected_epoch=0,
        )
        self.assertEqual(receipt.sequence, 1)
        self.assertEqual(receipt.epoch, 0)
        self.assertEqual(receipt.job_revisions, {"job-a": 1, "job-b": 1})
        self.assertEqual(receipt.global_revision, 1)
        self.assertEqual(len(self._lines()), 1)

        recovered = self.journal.recover()
        self.assertEqual(set(recovered.jobs), {"job-a", "job-b"})
        self.assertEqual(recovered.job_revisions, {"job-a": 1, "job-b": 1})
        self.assertEqual(recovered.global_revision, 1)
        self.assertEqual(recovered.global_state["order"], ["job-a", "job-b"])
        self.assertEqual(recovered.event_count, 1)
        self.assertEqual(recovered.epoch, 0)

    def test_batch_revision_conflict_writes_nothing_from_the_batch(self):
        self.journal.commit_job(
            "job-a", self._job("job-a"), expected_revision=0, expected_epoch=0,
        )
        before = self.path.read_bytes()
        with self.assertRaises(QueueRecoveryValidationError):
            self.journal.commit_state(
                jobs={
                    "job-a": self._job("job-a", "running"),
                    "job-b": self._job("job-b"),
                },
                global_state={"paused": True},
                expected_job_revisions={"job-a": 0, "job-b": 0},
                expected_global_revision=0,
                expected_epoch=0,
            )
        self.assertEqual(self.path.read_bytes(), before)
        recovered = self.journal.recover()
        self.assertEqual(set(recovered.jobs), {"job-a"})
        self.assertIsNone(recovered.global_state)

    def test_tombstone_revision_fence_blocks_stale_resurrection_across_compaction(self):
        created = self.journal.commit_job(
            "job-a", self._job("job-a"), expected_revision=0, expected_epoch=0,
        )
        self.assertEqual(created.job_revisions["job-a"], 1)
        stale_epoch = created.epoch
        stale_revision = created.job_revisions["job-a"]
        removed = self.journal.tombstone(
            "job-a", expected_revision=1, expected_epoch=0,
        )
        self.assertEqual(removed.job_revisions["job-a"], 2)
        compacted = self.journal.compact()
        self.assertNotIn("job-a", compacted.jobs)
        self.assertNotIn("job-a", compacted.job_revisions)
        self.assertEqual(compacted.epoch, 1)

        before = self.path.read_bytes()
        with self.assertRaises(QueueRecoveryValidationError):
            self.journal.commit_job(
                "job-a", self._job("job-a", "running"),
                expected_revision=stale_revision,
                expected_epoch=stale_epoch,
            )
        self.assertEqual(self.path.read_bytes(), before)

        resurrected = self.journal.commit_job(
            "job-a", self._job("job-a", "queued", retry=1),
            expected_revision=0, expected_epoch=1,
        )
        self.assertEqual(resurrected.job_revisions["job-a"], 1)
        self.assertEqual(self.journal.recover().jobs["job-a"]["retry"], 1)

    def test_global_revision_fences_stale_queue_control_commits(self):
        self.journal.commit_global(
            {"paused": False}, expected_revision=0, expected_epoch=0,
        )
        before = self.path.read_bytes()
        with self.assertRaises(QueueRecoveryValidationError):
            self.journal.commit_global(
                {"paused": True}, expected_revision=0, expected_epoch=0,
            )
        self.assertEqual(self.path.read_bytes(), before)
        receipt = self.journal.commit_global(
            {"paused": True}, expected_revision=1, expected_epoch=0,
        )
        self.assertEqual(receipt.global_revision, 2)

    def test_latest_full_snapshots_replay_idempotently(self):
        self.journal.commit_state(
            jobs={"job-1": self._job("job-1")},
            global_state={"paused": False},
            expected_job_revisions={"job-1": 0},
            expected_global_revision=0,
            expected_epoch=0,
        )
        self.journal.commit_job(
            "job-1", self._job("job-1", "running", step=4),
            expected_revision=1, expected_epoch=0,
        )
        first = self.journal.recover()
        second = self.journal.recover()
        self.assertEqual(first, second)
        self.assertEqual(first.job_revisions["job-1"], 2)
        self.assertEqual(first.jobs["job-1"]["step"], 4)

    def test_record_checksum_covers_the_full_unsigned_state_envelope(self):
        self.journal.commit_job(
            "job-1", self._job("job-1"), expected_revision=0, expected_epoch=0,
        )
        record = json.loads(self._lines()[0])
        checksum = record.pop("checksum")
        self.assertEqual(checksum, hashlib.sha256(_canonical(record)).hexdigest())
        self.assertEqual(record["schema"], 2)
        self.assertEqual(record["event"], "state_commit")

    def test_only_unterminated_bounded_final_line_is_discarded_and_repaired(self):
        self.journal.commit_job(
            "job-1", self._job("job-1"), expected_revision=0, expected_epoch=0,
        )
        committed = self.path.read_bytes()
        with self.path.open("ab") as handle:
            handle.write(b'{"schema":2,"prompt":"tail fragment"')
        recovered = self.journal.recover()
        self.assertTrue(recovered.discarded_torn_tail)
        self.assertEqual(self.path.read_bytes(), committed)
        self.journal.commit_global(
            {"paused": True}, expected_revision=0, expected_epoch=0,
        )
        self.assertEqual(self.journal.recover().global_revision, 1)

    def test_oversized_unterminated_line_is_quarantined_before_json_parse(self):
        path = self.root / "bounded.jsonl"
        path.write_bytes(b"{" + b"x" * 300)
        journal = QueueRecoveryJournal(
            path,
            max_record_bytes=256,
            max_journal_bytes=1024,
        )
        with mock.patch.object(queue_recovery, "_parse_json") as parser:
            with self.assertRaises(QueueRecoveryCorruptionError):
                journal.recover()
        parser.assert_not_called()
        self._assert_quarantined(path)

    def test_malformed_interior_complete_final_and_deep_json_quarantine(self):
        cases = {
            "interior": b"not-json\n" + b"{}\n",
            "complete-final": b"not-json\n",
            "deep": (b"[" * 1500) + (b"]" * 1500) + b"\n",
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                path = self.root / f"{name}.jsonl"
                path.write_bytes(payload)
                with self.assertRaises(QueueRecoveryCorruptionError):
                    QueueRecoveryJournal(path).recover()
                self._assert_quarantined(path)

    def test_nonfinite_parsed_number_is_corruption_not_validation_escape(self):
        record = {
            "checksum": "0" * 64,
            "epoch": 0,
            "event": "state_commit",
            "global": None,
            "jobs": [{
                "job_id": "job-a",
                "payload": {"id": "job-a", "value": 1.0},
                "revision": 1,
            }],
            "schema": 2,
            "sequence": 1,
            "tombstones": [],
        }
        raw = _canonical(record).replace(b'"value":1.0', b'"value":1e999') + b"\n"
        self.path.write_bytes(raw)
        with self.assertRaises(QueueRecoveryCorruptionError):
            self.journal.recover()
        self._assert_quarantined()

    def test_checksum_schema_sequence_and_semantic_revision_corruption_quarantine(self):
        mutations = {
            "checksum": lambda record: record.update(checksum="0" * 64),
            "schema": lambda record: record.update(schema=999),
            "sequence": lambda record: record.update(sequence=7),
            "epoch": lambda record: record.update(epoch=1),
            "revision": lambda record: record["jobs"][0].update(revision=7),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                path = self.root / f"{name}.jsonl"
                journal = QueueRecoveryJournal(path)
                journal.commit_job(
                    "job-a", self._job("job-a"),
                    expected_revision=0, expected_epoch=0,
                )
                record = json.loads(path.read_bytes())
                mutation(record)
                path.write_bytes(
                    _canonical(record) + b"\n" if name == "checksum" else _signed(record)
                )
                with self.assertRaises(QueueRecoveryCorruptionError):
                    journal.recover()
                self._assert_quarantined(path)

    def test_duplicate_keys_and_duplicate_job_ids_quarantine(self):
        duplicate_key = self.root / "duplicate-key.jsonl"
        duplicate_key.write_bytes(
            b'{"checksum":"' + b"0" * 64
            + b'","epoch":0,"event":"state_commit","event":"state_commit",'
              b'"global":null,"jobs":[],"schema":2,"sequence":1,"tombstones":[]}\n'
        )
        with self.assertRaises(QueueRecoveryCorruptionError):
            QueueRecoveryJournal(duplicate_key).recover()

        duplicate_job = self.root / "duplicate-job.jsonl"
        entry = {"job_id": "job-a", "payload": self._job("job-a"), "revision": 1}
        duplicate_job.write_bytes(_signed({
            "epoch": 0,
            "event": "state_commit",
            "global": None,
            "jobs": [entry, entry],
            "schema": 2,
            "sequence": 1,
            "tombstones": [],
        }))
        with self.assertRaises(QueueRecoveryCorruptionError):
            QueueRecoveryJournal(duplicate_job).recover()

    def test_snapshot_chunks_must_be_contiguous_and_globally_job_sorted(self):
        def job_entry(job_id, revision=1):
            return {
                "job_id": job_id,
                "payload": {"id": job_id, "status": "queued"},
                "revision": revision,
            }

        impossible_orders = {
            "chunk-after-commit": [
                {
                    "epoch": 1,
                    "event": "state_snapshot",
                    "global": None,
                    "jobs": [job_entry("job-b")],
                    "schema": 2,
                    "sequence": 1,
                    "tombstones": [],
                },
                {
                    "epoch": 1,
                    "event": "state_commit",
                    "global": None,
                    "jobs": [job_entry("job-c")],
                    "schema": 2,
                    "sequence": 2,
                    "tombstones": [],
                },
                {
                    "epoch": 1,
                    "event": "state_snapshot_chunk",
                    "global": None,
                    "jobs": [job_entry("job-d", revision=99)],
                    "schema": 2,
                    "sequence": 3,
                    "tombstones": [],
                },
            ],
            "chunk-job-order-regression": [
                {
                    "epoch": 1,
                    "event": "state_snapshot",
                    "global": None,
                    "jobs": [job_entry("job-b")],
                    "schema": 2,
                    "sequence": 1,
                    "tombstones": [],
                },
                {
                    "epoch": 1,
                    "event": "state_snapshot_chunk",
                    "global": None,
                    "jobs": [job_entry("job-a")],
                    "schema": 2,
                    "sequence": 2,
                    "tombstones": [],
                },
            ],
            "chunk-job-duplicate": [
                {
                    "epoch": 1,
                    "event": "state_snapshot",
                    "global": None,
                    "jobs": [job_entry("job-b")],
                    "schema": 2,
                    "sequence": 1,
                    "tombstones": [],
                },
                {
                    "epoch": 1,
                    "event": "state_snapshot_chunk",
                    "global": None,
                    "jobs": [job_entry("job-b")],
                    "schema": 2,
                    "sequence": 2,
                    "tombstones": [],
                },
            ],
        }
        for name, records in impossible_orders.items():
            with self.subTest(name=name):
                path = self.root / f"{name}.jsonl"
                path.write_bytes(b"".join(_signed(record) for record in records))
                with self.assertRaises(QueueRecoveryCorruptionError):
                    QueueRecoveryJournal(path).recover()
                self._assert_quarantined(path)

    def test_camelcase_generic_secret_token_and_session_fields_are_rejected(self):
        fields = [
            {"hf_token": "private-hf"},
            {"api_key": "private-api"},
            {"API-Key": "private-api-dash"},
            {"apiKey": "private-api-camel"},
            {"apikey": "private-apikey"},
            {"NOUS_API_KEY": "private-provider-api"},
            {"private_key": "private-key"},
            {"privateKey": "private-key-camel"},
            {"clientSecret": "private-client"},
            {"clientSecrets": "private-clients"},
            {"accessToken": "private-access"},
            {"accessTokens": "private-access-list"},
            {"owner_session": "private-session"},
            {"ownerSessionId": "private-session-id"},
            {"nested": {"apiCredential": "private-credential"}},
            {"worker_thread": "runtime-only"},
            {"path": Path("/private/source.mov")},
            {"image": object()},
            {"value": float("nan")},
        ]
        for index, update in enumerate(fields):
            with self.subTest(index=index):
                job_id = f"job-{index}"
                snapshot = self._job(job_id)
                snapshot.update(update)
                with self.assertRaises(QueueRecoveryValidationError) as caught:
                    self.journal.commit_job(
                        job_id, snapshot, expected_revision=0, expected_epoch=0,
                    )
                self.assertNotIn("private-", str(caught.exception))
                self.assertNotIn("/private/source.mov", str(caught.exception))
        self.assertFalse(self.path.exists())

    def test_revision_argument_is_mandatory_and_batch_expected_set_is_exact(self):
        with self.assertRaises(TypeError):
            self.journal.commit_job("job-a", self._job("job-a"))
        with self.assertRaises(QueueRecoveryValidationError):
            self.journal.commit_state(
                jobs={"job-a": self._job("job-a")},
                expected_job_revisions={},
                expected_epoch=0,
            )
        with self.assertRaises(QueueRecoveryValidationError):
            self.journal.commit_state(
                jobs={"job-a": self._job("job-a")},
                expected_job_revisions={"job-a": 0, "job-b": 0},
                expected_epoch=0,
            )

    def test_partial_append_and_fsync_failure_roll_back_previous_bytes(self):
        for stage in ("write", "fsync"):
            with self.subTest(stage=stage):
                path = self.root / f"append-{stage}.jsonl"
                journal = QueueRecoveryJournal(path)
                journal.commit_job(
                    "job-a", self._job("job-a"),
                    expected_revision=0, expected_epoch=0,
                )
                before = path.read_bytes()
                if stage == "write":
                    real_write = os.write
                    calls = 0

                    def fail_after_partial(descriptor, payload):
                        nonlocal calls
                        calls += 1
                        if calls == 1:
                            return real_write(descriptor, payload[:9])
                        raise OSError("private write /private/path")

                    patcher = mock.patch.object(
                        queue_recovery.os, "write", side_effect=fail_after_partial,
                    )
                else:
                    patcher = mock.patch.object(
                        queue_recovery.os,
                        "fsync",
                        side_effect=OSError("private fsync /private/path"),
                    )
                with patcher:
                    with self.assertRaises(QueueRecoveryPersistenceError) as caught:
                        journal.commit_global(
                            {"paused": True}, expected_revision=0, expected_epoch=0,
                        )
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(journal.recover().event_count, 1)

    def test_first_journal_creation_fsyncs_parent_directory(self):
        calls = []
        original = queue_recovery._fsync_directory

        def record(directory):
            calls.append(Path(directory))
            return original(directory)

        with mock.patch.object(queue_recovery, "_fsync_directory", side_effect=record):
            self.journal.commit_job(
                "job-a", self._job("job-a"),
                expected_revision=0, expected_epoch=0,
            )
        # One call durably creates the lock file and another durably creates
        # the journal entry itself.
        self.assertGreaterEqual(calls.count(self.root.resolve()), 2)

    def test_compaction_preserves_active_revisions_and_drops_removed_ids_in_new_epoch(self):
        self.journal.commit_state(
            jobs={
                "active": self._job("active", "running"),
                "done": self._job("done", "completed"),
                "gone": self._job("gone"),
            },
            global_state={"paused": True},
            expected_job_revisions={"active": 0, "done": 0, "gone": 0},
            expected_global_revision=0,
            expected_epoch=0,
        )
        self.journal.tombstone("gone", expected_revision=1, expected_epoch=0)
        compacted = self.journal.compact()
        self.assertEqual(set(compacted.jobs), {"active"})
        self.assertEqual(compacted.job_revisions, {"active": 1})
        self.assertEqual(compacted.epoch, 1)
        self.assertEqual(compacted.event_count, 1)
        self.assertEqual(json.loads(self._lines()[0])["event"], "state_snapshot")

    def test_fenced_replacement_compacts_sanitized_state_at_event_limit(self):
        path = self.root / "replacement-at-limit.jsonl"
        journal = QueueRecoveryJournal(path, max_events=1)
        journal.commit_state(
            jobs={
                "active": {
                    "id": "active",
                    "status": "queued",
                    "prompt": "raw field",
                },
            },
            tombstones=("retired",),
            global_state={"paused": False},
            expected_job_revisions={"active": 0, "retired": 0},
            expected_global_revision=0,
            expected_epoch=0,
        )
        before = path.read_bytes()
        with self.assertRaises(QueueRecoveryValidationError):
            journal.compact(
                replacement_jobs={
                    "active": {"id": "active", "status": "queued"},
                },
                replacement_global_state={"paused": False},
                expected_job_revisions={"active": 0, "retired": 1},
                expected_global_revision=1,
                expected_epoch=0,
            )
        self.assertEqual(path.read_bytes(), before)
        compacted = journal.compact(
            replacement_jobs={
                "active": {"id": "active", "status": "queued"},
            },
            replacement_global_state={"paused": False},
            expected_job_revisions={"active": 1, "retired": 1},
            expected_global_revision=1,
            expected_epoch=0,
        )
        self.assertEqual(compacted.epoch, 1)
        self.assertEqual(compacted.job_revisions, {"active": 2})
        self.assertEqual(compacted.global_revision, 2)
        self.assertNotIn("prompt", compacted.jobs["active"])
        self.assertNotIn("raw field", path.read_text(encoding="utf-8"))

    def test_compaction_epoch_discards_more_than_ten_thousand_lifetime_fences(self):
        count = 10_050
        job_ids = [f"old-{index:05d}" for index in range(count)]
        self.journal.commit_state(
            tombstones=job_ids,
            expected_job_revisions={job_id: 0 for job_id in job_ids},
            expected_epoch=0,
        )
        before = self.journal.recover()
        self.assertEqual(len(before.job_revisions), count)
        self.assertEqual(before.jobs, {})

        compacted = self.journal.compact()
        self.assertEqual(compacted.epoch, 1)
        self.assertEqual(compacted.job_revisions, {})
        self.assertEqual(compacted.jobs, {})
        self.assertEqual(compacted.event_count, 1)

        receipt = self.journal.commit_job(
            "new-job",
            self._job("new-job"),
            expected_revision=0,
            expected_epoch=1,
        )
        self.assertEqual(receipt.epoch, 1)
        self.assertEqual(receipt.job_revisions, {"new-job": 1})

    def test_compaction_chunks_active_state_larger_than_one_record(self):
        path = self.root / "chunked-compaction.jsonl"
        journal = QueueRecoveryJournal(
            path,
            max_record_bytes=900,
            max_journal_bytes=128 * 1024,
        )
        count = 24
        for index in range(count):
            job_id = f"active-{index:03d}"
            journal.commit_job(
                job_id,
                {
                    "id": job_id,
                    "status": "queued",
                    "payload": "x" * 320,
                },
                expected_revision=0,
                expected_epoch=0,
            )
        compacted = journal.compact()
        lines = self._lines(path)
        self.assertGreater(len(lines), 1)
        self.assertGreater(path.stat().st_size, journal.max_record_bytes)
        self.assertTrue(all(len(line) + 1 <= journal.max_record_bytes for line in lines))
        self.assertEqual(json.loads(lines[0])["event"], "state_snapshot")
        self.assertTrue(all(
            json.loads(line)["event"] == "state_snapshot_chunk"
            for line in lines[1:]
        ))
        self.assertEqual(compacted.epoch, 1)
        self.assertEqual(len(compacted.jobs), count)
        self.assertEqual(len(compacted.job_revisions), count)
        self.assertEqual(journal.recover(), compacted)

    def test_compaction_write_fsync_replace_failures_preserve_old_journal(self):
        for stage in ("write", "fsync", "replace"):
            with self.subTest(stage=stage):
                path = self.root / f"compact-{stage}.jsonl"
                journal = QueueRecoveryJournal(path)
                journal.commit_job(
                    "job-a", self._job("job-a"),
                    expected_revision=0, expected_epoch=0,
                )
                journal.commit_job(
                    "job-a", self._job("job-a", "running"),
                    expected_revision=1, expected_epoch=0,
                )
                before = path.read_bytes()
                patcher = (
                    mock.patch.object(queue_recovery.os, "write", side_effect=OSError("write"))
                    if stage == "write"
                    else mock.patch.object(queue_recovery.os, "fsync", side_effect=OSError("fsync"))
                    if stage == "fsync"
                    else mock.patch.object(queue_recovery.os, "replace", side_effect=OSError("replace"))
                )
                with patcher:
                    with self.assertRaises(QueueRecoveryPersistenceError):
                        journal.compact()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(journal.recover().jobs["job-a"]["status"], "running")
                self.assertEqual(list(self.root.glob(f".{path.name}.*.tmp")), [])

    def test_directory_fsync_failure_after_replace_leaves_recoverable_snapshot(self):
        self.journal.commit_job(
            "job-a", self._job("job-a"), expected_revision=0, expected_epoch=0,
        )
        self.journal.commit_job(
            "job-a", self._job("job-a", "running"),
            expected_revision=1, expected_epoch=0,
        )
        with mock.patch.object(
            queue_recovery,
            "_fsync_directory",
            side_effect=OSError("directory sync"),
        ):
            with self.assertRaises(QueueRecoveryPersistenceError):
                self.journal.compact()
        recovered = self.journal.recover()
        self.assertEqual(recovered.event_count, 1)
        self.assertEqual(recovered.job_revisions["job-a"], 2)

    def test_symlink_journal_parent_alias_lock_symlink_and_hardlink_are_rejected(self):
        target = self.root / "target.jsonl"
        target.write_bytes(b"")
        symlink = self.root / "journal-link.jsonl"
        try:
            symlink.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            QueueRecoveryJournal(symlink)

        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        parent_alias = self.root / "parent-alias"
        parent_alias.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            QueueRecoveryJournal(parent_alias / "journal.jsonl")

        hardlink = self.root / "journal-hardlink.jsonl"
        os.link(target, hardlink)
        with self.assertRaises(ValueError):
            QueueRecoveryJournal(target)

        lock_target = self.root / "lock-target"
        lock_target.write_bytes(b"\0")
        lock_link = self.path.with_name(self.path.name + ".lock")
        lock_link.symlink_to(lock_target)
        with self.assertRaises(QueueRecoveryPersistenceError):
            self.journal.recover()
        lock_link.unlink()
        os.link(lock_target, lock_link)
        with self.assertRaises(QueueRecoveryPersistenceError):
            self.journal.recover()

    def test_descriptor_scan_detects_path_replacement_before_quarantine(self):
        self.path.write_bytes(b"not-json\n")
        original_parse = queue_recovery._parse_json
        swapped = False

        def replace_while_descriptor_is_open(raw):
            nonlocal swapped
            if not swapped:
                replacement = self.root / "swapped"
                replacement.write_bytes(b"")
                os.replace(replacement, self.path)
                swapped = True
            return original_parse(raw)

        with mock.patch.object(
            queue_recovery, "_parse_json", side_effect=replace_while_descriptor_is_open,
        ):
            with self.assertRaises(QueueRecoveryCorruptionError) as caught:
                self.journal.recover()
        # The descriptor identity check refuses to treat the replacement as the
        # corrupt file. No user content or path is exposed.
        self.assertFalse(caught.exception.quarantined)
        self.assertNotIn(str(self.path), str(caught.exception))

    def test_threads_and_real_processes_keep_sequences_contiguous(self):
        thread_count = 6
        barrier = threading.Barrier(thread_count)
        failures = []
        journals = [QueueRecoveryJournal(self.path), QueueRecoveryJournal(self.path)]

        def write_thread(index):
            try:
                barrier.wait(timeout=5)
                job_id = f"thread-{index}"
                journals[index % 2].commit_job(
                    job_id, self._job(job_id), expected_revision=0,
                    expected_epoch=0,
                )
            except Exception as exc:  # pragma: no cover - assertion reports type
                failures.append(type(exc).__name__)

        threads = [threading.Thread(target=write_thread, args=(index,)) for index in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_multiprocess_writer,
                args=(str(self.path), f"process-{index}", 3, result_queue),
            )
            for index in range(3)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([result_queue.get(timeout=3) for _ in processes], ["ok"] * 3)

        recovered = QueueRecoveryJournal(self.path).recover()
        expected = thread_count + 9
        self.assertEqual(len(recovered.jobs), expected)
        sequences = [json.loads(line)["sequence"] for line in self._lines()]
        self.assertEqual(sequences, list(range(1, expected + 1)))

    def test_real_processes_serialize_first_lock_and_journal_creation(self):
        path = self.root / "fresh-process-journal.jsonl"
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_multiprocess_writer,
                args=(str(path), f"fresh-{index}", 2, result_queue),
            )
            for index in range(4)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        self.assertEqual([result_queue.get(timeout=3) for _ in processes], ["ok"] * 4)
        recovered = QueueRecoveryJournal(path).recover()
        self.assertEqual(len(recovered.jobs), 8)
        self.assertEqual(
            [json.loads(line)["sequence"] for line in self._lines(path)],
            list(range(1, 9)),
        )

    @unittest.skipIf(os.name == "nt", "POSIX directory fsync branch")
    def test_posix_directory_fsync_uses_a_real_directory_descriptor(self):
        queue_recovery._fsync_directory(self.root)

    @unittest.skipUnless(os.name == "nt", "Windows directory durability branch")
    def test_windows_directory_fsync_branch_is_explicitly_nonthrowing(self):
        queue_recovery._fsync_directory(self.root)


if __name__ == "__main__":
    unittest.main()
