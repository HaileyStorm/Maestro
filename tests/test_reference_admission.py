"""Exact durability and privacy tests for Reference request admission."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _ROOT / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from services.reference_admission import (  # noqa: E402
    ReferenceAdmissionCapacityError,
    ReferenceAdmissionCorruptionError,
    ReferenceAdmissionMismatchError,
    ReferenceAdmissionStore,
    ReferenceAdmissionValidationError,
    normalize_request_id,
)


SECRET = b"reference-admission-test-secret-32-bytes"


def _cross_process_begin(root: str, barrier, results) -> None:
    store = ReferenceAdmissionStore(root, SECRET)
    barrier.wait()
    try:
        result = store.begin(
            "parallel-request-0001",
            owner_principal="owner-a",
            project_instance="project-instance-a",
            operation="reference.create.v2",
            payload={"description": "private parallel prompt"},
            proposed_job_id=f"job-{os.getpid()}",
            proposed_asset_id=f"asset-{os.getpid()}",
        )
        results.put((result.disposition, result.job_id, result.asset_id))
    except Exception as error:  # pragma: no cover - parent asserts type
        results.put((type(error).__name__, "", ""))


class ReferenceAdmissionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "admissions"
        self.store = ReferenceAdmissionStore(self.root, SECRET)
        self.scope = {
            "owner_principal": "owner-session-principal-a",
            "project_instance": "project-instance-digest-a",
            "operation": "reference.create.v2",
            "payload": {
                "description": "PRIVATE CLOCKWORK REFERENCE PROMPT",
                "name": "PRIVATE DISPLAY LABEL",
                "tags": ["PRIVATE TAG"],
                "nested": {"seed": -1, "review": True},
            },
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _begin(self, request_id="request-token-0001", **updates):
        arguments = {
            **self.scope,
            "proposed_job_id": "job-opaque-1",
            "proposed_asset_id": "asset-opaque-1",
        }
        arguments.update(updates)
        return self.store.begin(request_id, **arguments)

    def _finish_scope(self, reservation):
        return {
            **self.scope,
            "lease_token": reservation.lease_token,
        }

    def _records(self):
        return list(self.root.glob("*.json"))

    def test_request_id_is_optional_to_legacy_callers_but_strict_when_present(self):
        self.assertIsNone(normalize_request_id(None))
        self.assertIsNone(normalize_request_id(""))
        self.assertEqual(
            normalize_request_id("opaque-request_01"), "opaque-request_01",
        )
        for invalid in (
            1, "short", " padded-token-01", "path/token-01",
            "x" * 129,
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                ReferenceAdmissionValidationError
            ):
                normalize_request_id(invalid)

    def test_first_acceptance_and_replay_keep_one_job_and_asset_identity(self):
        first = self._begin()
        self.assertEqual(first.disposition, "new")
        self.assertTrue(first.owns_lease)
        accepted = self.store.accept(
            "request-token-0001", **self._finish_scope(first),
        )
        self.assertEqual(accepted.disposition, "accepted")

        replay = self._begin(
            proposed_job_id="different-job",
            proposed_asset_id="different-asset",
        )
        self.assertEqual(replay.disposition, "replay")
        self.assertEqual(replay.job_id, first.job_id)
        self.assertEqual(replay.asset_id, first.asset_id)
        self.assertEqual(len(self._records()), 1)

    def test_name_is_payload_bound_but_never_an_identity_key(self):
        first = self._begin("same-private-request-01")
        self.store.accept(
            "same-private-request-01", **self._finish_scope(first),
        )
        different_id = self._begin(
            "same-name-new-request-02",
            proposed_job_id="job-opaque-2",
            proposed_asset_id="asset-opaque-2",
        )
        self.assertEqual(different_id.disposition, "new")
        self.assertNotEqual(first.job_id, different_id.job_id)

        changed = dict(self.scope["payload"])
        changed["name"] = "A DIFFERENT PRIVATE DISPLAY LABEL"
        with self.assertRaises(ReferenceAdmissionMismatchError):
            self._begin("same-private-request-01", payload=changed)

    def test_scope_and_canonical_private_payload_are_bound(self):
        first = self._begin("scope-bound-request-01")
        self.store.accept(
            "scope-bound-request-01", **self._finish_scope(first),
        )
        reordered = {
            "nested": {"review": True, "seed": -1},
            "tags": ["PRIVATE TAG"],
            "name": "PRIVATE DISPLAY LABEL",
            "description": "PRIVATE CLOCKWORK REFERENCE PROMPT",
        }
        replay = self._begin("scope-bound-request-01", payload=reordered)
        self.assertEqual(replay.disposition, "replay")

        # A distinct owner/project scope receives an independent namespace.
        other = self._begin(
            "scope-bound-request-01",
            owner_principal="owner-session-principal-b",
            proposed_job_id="job-other-owner",
            proposed_asset_id="asset-other-owner",
        )
        self.assertEqual(other.disposition, "new")
        self.assertEqual(len(self._records()), 2)

        with self.assertRaises(ReferenceAdmissionMismatchError):
            self._begin(
                "scope-bound-request-01",
                payload={**self.scope["payload"], "description": "changed"},
            )

    def test_record_is_content_free_bounded_and_mode_0600(self):
        reservation = self._begin("privacy-record-token-01")
        record_path = self._records()[0]
        raw = record_path.read_bytes()
        self.assertLessEqual(len(raw), 4096)
        self.assertEqual(stat.S_IMODE(record_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for private in (
            "PRIVATE CLOCKWORK REFERENCE PROMPT",
            "PRIVATE DISPLAY LABEL",
            "PRIVATE TAG",
            "owner-session-principal-a",
            "project-instance-digest-a",
            "reference.create.v2",
            "privacy-record-token-01",
            reservation.lease_token,
        ):
            self.assertNotIn(private, raw.decode("utf-8"))
        record = json.loads(raw)
        self.assertEqual(record["job_id"], reservation.job_id)
        self.assertEqual(record["asset_id"], reservation.asset_id)
        self.assertFalse(any(
            term in key
            for key in record
            for term in ("prompt", "path", "label", "name", "session")
        ))

    def test_live_lease_waits_and_expired_lease_resumes_same_identity(self):
        clock = [1_000.0]
        store = ReferenceAdmissionStore(
            self.root, SECRET, lease_seconds=2.0, clock=lambda: clock[0],
        )
        first = store.begin(
            "crash-resume-token-01",
            **self.scope,
            proposed_job_id="crash-job",
            proposed_asset_id="crash-asset",
        )
        pending = store.begin(
            "crash-resume-token-01",
            **self.scope,
            proposed_job_id="duplicate-job",
            proposed_asset_id="duplicate-asset",
        )
        self.assertEqual(pending.disposition, "pending")
        self.assertEqual(pending.job_id, first.job_id)

        clock[0] += 2.1
        resumed = store.begin(
            "crash-resume-token-01",
            **self.scope,
            proposed_job_id="replacement-job",
            proposed_asset_id="replacement-asset",
        )
        self.assertEqual(resumed.disposition, "resume")
        self.assertEqual(resumed.job_id, first.job_id)
        self.assertEqual(resumed.asset_id, first.asset_id)
        self.assertNotEqual(resumed.lease_token, first.lease_token)

    def test_failed_reservation_is_durable_and_fail_closed(self):
        reservation = self._begin("fail-closed-token-01")
        failed = self.store.fail(
            "fail-closed-token-01", **self._finish_scope(reservation),
        )
        self.assertEqual(failed.disposition, "failed")
        retry = self._begin("fail-closed-token-01")
        self.assertEqual(retry.disposition, "failed")
        self.assertEqual(retry.job_id, reservation.job_id)

    def test_restart_replays_the_same_accepted_identity(self):
        first = self._begin("restart-token-0001")
        self.store.accept(
            "restart-token-0001", **self._finish_scope(first),
        )
        restarted = ReferenceAdmissionStore(self.root, SECRET)
        replay = restarted.begin(
            "restart-token-0001",
            **self.scope,
            proposed_job_id="new-process-job",
            proposed_asset_id="new-process-asset",
        )
        self.assertEqual(replay.disposition, "replay")
        self.assertEqual((replay.job_id, replay.asset_id), (
            first.job_id, first.asset_id,
        ))

    def test_cross_process_first_writer_wins_one_o_excl_reservation(self):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_cross_process_begin,
                args=(str(self.root), barrier, results),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        outcomes = [results.get(timeout=2) for _ in processes]
        self.assertEqual(
            sorted(item[0] for item in outcomes), ["new", "pending"],
        )
        self.assertEqual(len({item[1] for item in outcomes}), 1)
        self.assertEqual(len({item[2] for item in outcomes}), 1)
        self.assertEqual(len(self._records()), 1)

    def test_record_bound_and_corruption_fail_closed(self):
        bounded_root = Path(self.temporary.name) / "bounded"
        bounded = ReferenceAdmissionStore(
            bounded_root, SECRET, max_records=1,
        )
        bounded.begin(
            "bounded-request-0001",
            **self.scope,
            proposed_job_id="bounded-job-1",
            proposed_asset_id="bounded-asset-1",
        )
        with self.assertRaises(ReferenceAdmissionCapacityError):
            bounded.begin(
                "bounded-request-0002",
                **self.scope,
                proposed_job_id="bounded-job-2",
                proposed_asset_id="bounded-asset-2",
            )

        record_path = next(bounded_root.glob("*.json"))
        record_path.write_text('{"schema":1,"prompt":"PRIVATE"}', encoding="utf-8")
        os.chmod(record_path, 0o600)
        with self.assertRaises(ReferenceAdmissionCorruptionError):
            bounded.begin(
                "bounded-request-0001",
                **self.scope,
                proposed_job_id="replacement-job",
                proposed_asset_id="replacement-asset",
            )

    def test_integrity_tampering_and_hardlinks_fail_closed(self):
        self._begin("integrity-token-0001")
        record_path = self._records()[0]
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["job_id"] = "attacker-selected-job"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        os.chmod(record_path, 0o600)
        with self.assertRaises(ReferenceAdmissionCorruptionError):
            self._begin("integrity-token-0001")

        record_path.unlink()
        clean = self._begin(
            "integrity-token-0002",
            proposed_job_id="clean-job",
            proposed_asset_id="clean-asset",
        )
        self.assertEqual(clean.disposition, "new")
        clean_path = self._records()[0]
        if os.name != "nt":
            os.chmod(clean_path, 0o1600)
            with self.assertRaises(ReferenceAdmissionCorruptionError):
                self._begin("integrity-token-0002")
            os.chmod(clean_path, 0o600)
        hardlink = self.root / "linked-copy.json"
        os.link(clean_path, hardlink)
        with self.assertRaises(ReferenceAdmissionCorruptionError):
            self._begin("integrity-token-0002")


if __name__ == "__main__":
    unittest.main()
