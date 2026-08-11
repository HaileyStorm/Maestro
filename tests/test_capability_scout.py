"""Offline contracts for the pure capability-scout state core."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ast
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.capability_scout import (  # noqa: E402
    BENCHMARK_RECEIPT_SCHEMA_VERSION,
    CANDIDATE_KINDS,
    CapabilityGateBlocked,
    CapabilityScoutConflict,
    CapabilityScoutCorrupt,
    CapabilityScoutError,
    CapabilityScoutLocked,
    CapabilityScoutStore,
    POST_TEST_GATE_FIELDS,
    PRE_TEST_GATE_FIELDS,
)


NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
SOURCE_DIGEST = "1" * 64
RESULT_DIGEST = "2" * 64
PRIVACY_CANARY = "PRIVATE-CANARY-PROMPT-MEDIA-LOG-CREDENTIAL-92d32a"
OWNER_KEYS = {
    "owner_key": "owner-alpha",
    "session_key": "session-alpha",
    "authority_key": "authority-alpha",
}


def test_store(root: Path, *, token: str = "owner-secret-nonce") -> CapabilityScoutStore:
    return CapabilityScoutStore(
        root,
        allow_test_root=True,
        token_factory=lambda: token,
    )


def add_candidate(
    store: CapabilityScoutStore,
    *,
    candidate_id: str = "candidate-one",
    kind: str = "workflow",
    private_notes: str = "",
    dimensions: tuple[dict, ...] | None = None,
    source_uri: str | None = None,
) -> dict:
    return store.add_candidate(
        candidate_id=candidate_id,
        kind=kind,
        canonical_id=f"canonical-{candidate_id}",
        artifact_id=f"artifact-{candidate_id}",
        source_uri=source_uri or f"https://example.invalid/{candidate_id}",
        source_revision="source-revision-1",
        capability_dimensions=dimensions or (
            {
                "dimension_id": "general",
                "fixture_ids": ["fixture-general-01"],
                "required_metrics": [
                    {"metric_id": "latency_seconds", "unit": "seconds"},
                    {"metric_id": "quality_score", "unit": "ratio"},
                ],
            },
            {
                "dimension_id": "mature_explicit",
                "fixture_ids": ["fixture-opaque-92"],
                "required_metrics": [
                    {"metric_id": "latency_seconds", "unit": "seconds"},
                    {"metric_id": "quality_score", "unit": "ratio"},
                ],
            },
        ),
        private_notes=private_notes,
        now=NOW,
    )


def add_evidence(
    store: CapabilityScoutStore,
    candidate: dict,
    *,
    private_notes: str = "",
    source_uri: str = "https://example.invalid/source/commit/1",
) -> dict:
    return store.add_evidence_claim(
        candidate["candidate_id"],
        expected_revision=candidate["revision"],
        claim_id="claim-primary-1",
        claim_type="primary_source",
        summary="Pinned primary-source evidence for the exact revision.",
        source_uri=source_uri,
        source_revision="commit-1",
        evidence_revision="evidence-1",
        source_sha256=SOURCE_DIGEST,
        observed_at=NOW,
        private_notes=private_notes,
        now=NOW + timedelta(seconds=1),
    )


def transition(store: CapabilityScoutStore, candidate: dict, target: str, seconds: int) -> dict:
    return store.transition(
        candidate["candidate_id"],
        target,
        expected_revision=candidate["revision"],
        now=NOW + timedelta(seconds=seconds),
    )


def gate_decisions(fields: tuple[str, ...], *, owner_binding: str | None = None) -> dict:
    result = {
        field: {"status": "approved", "basis_revision": f"basis-{field}"}
        for field in fields
    }
    if owner_binding is not None:
        result["owner_selected_revision"] = {
            "status": "approved", "basis_revision": owner_binding,
        }
    return result


def recommendation_ready(store: CapabilityScoutStore) -> dict:
    candidate = add_candidate(store)
    candidate = transition(store, candidate, "researching", 1)
    candidate = add_evidence(store, candidate)
    candidate = transition(store, candidate, "evidence_ready", 3)
    candidate = transition(store, candidate, "recommended", 4)
    candidate = store.update_gate(
        candidate["candidate_id"],
        expected_revision=candidate["revision"],
        stage="pre_test",
        decisions=gate_decisions(PRE_TEST_GATE_FIELDS),
        now=NOW + timedelta(seconds=5),
    )
    candidate = transition(store, candidate, "benchmark_queued", 6)
    candidate = store.attach_benchmark_receipt(
        candidate["candidate_id"],
        expected_revision=candidate["revision"],
        receipt=benchmark_receipt(
            candidate, receipt_id="receipt-general", fixture_indexes=[0],
        ),
        now=NOW + timedelta(seconds=7),
    )
    candidate = store.attach_benchmark_receipt(
        candidate["candidate_id"],
        expected_revision=candidate["revision"],
        receipt=benchmark_receipt(
            candidate, receipt_id="receipt-mature", fixture_indexes=[1],
        ),
        now=NOW + timedelta(seconds=8),
    )
    return transition(store, candidate, "selection_ready", 9)


def benchmark_receipt(
    candidate: dict,
    *,
    receipt_id: str = "receipt-1",
    fixture_indexes: list[int] | None = None,
) -> dict:
    requirement = candidate["benchmark_requirement"]
    indexes = fixture_indexes if fixture_indexes is not None else list(
        range(len(requirement["fixture_matrix"]))
    )
    return {
        "schema_version": BENCHMARK_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "candidate_id": candidate["candidate_id"],
        "candidate_revision": requirement["candidate_revision"],
        "evidence_set_revision": requirement["evidence_set_revision"],
        "requirement_revision": requirement["requirement_revision"],
        "pre_test_gate_revision": requirement["pre_test_gate_revision"],
        "pre_test_gate_sha256": requirement["pre_test_gate_sha256"],
        "environment_revision": "environment-v1",
        "fixture_results": [
            {
                "dimension_id": requirement["fixture_matrix"][index]["dimension_id"],
                "fixture_id": requirement["fixture_matrix"][index]["fixture_id"],
                "metrics": [
                    {
                        "metric_id": metric_id,
                        "unit": "seconds" if metric_id == "latency_seconds" else "ratio",
                        "value": 4.25 if metric_id == "latency_seconds" else 0.91,
                        "sample_count": 3,
                    }
                    for metric_id in (
                        metric["metric_id"]
                        for metric in requirement["fixture_matrix"][index]["required_metrics"]
                    )
                ],
            }
            for index in indexes
        ],
        "started_at": "2026-08-11T09:01:00Z",
        "completed_at": "2026-08-11T09:02:00Z",
        "result_sha256": RESULT_DIGEST,
        "private_artifact_ref": f"artifact:{PRIVACY_CANARY}",
    }


def refresh_integrity(document: dict) -> None:
    without_integrity = {
        key: value for key, value in document.items() if key != "integrity"
    }
    document["integrity"] = {
        "algorithm": "sha256",
        "digest": hashlib.sha256(json.dumps(
            without_integrity,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")).hexdigest(),
    }


class CapabilityScoutSchemaTests(unittest.TestCase):
    def test_all_immutable_candidate_kinds_are_accepted_and_unknown_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            for index, kind in enumerate(sorted(CANDIDATE_KINDS)):
                candidate = add_candidate(
                    store,
                    candidate_id=f"candidate-{index}",
                    kind=kind,
                )
                self.assertEqual(candidate["kind"], kind)
            with self.assertRaises(ValueError):
                add_candidate(store, candidate_id="candidate-unknown", kind="tool")

    def test_identity_source_revision_parents_and_evidence_revision_are_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            parent = add_candidate(store, candidate_id="parent")
            child = store.add_candidate(
                candidate_id="child",
                kind="lora",
                canonical_id="repo/model/lora",
                artifact_id="weights.safetensors",
                source_uri="https://example.invalid/repo/model",
                source_revision="commit-exact-123",
                parent_relations=[{
                    "relation": "extends",
                    "candidate_id": parent["candidate_id"],
                    "candidate_revision": parent["revision"],
                }],
                capability_dimensions=[{
                    "dimension_id": "general", "fixture_ids": ["opaque-1"],
                    "required_metrics": [{"metric_id": "quality_score", "unit": "ratio"}],
                }],
                now=NOW,
            )
            original_identity = json.loads(json.dumps(child["identity"]))
            child = transition(store, child, "researching", 1)
            child = add_evidence(store, child)
            self.assertEqual(child["identity"], original_identity)
            self.assertEqual(child["evidence_claims"][0]["source_revision"], "commit-1")
            self.assertEqual(child["evidence_claims"][0]["evidence_revision"], "evidence-1")
            self.assertEqual(child["identity"]["parent_relations"][0]["candidate_revision"], 1)
            self.assertRegex(child["identity"]["identity_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(child["evidence_set_revision"], r"^[0-9a-f]{64}$")
            with self.assertRaises(CapabilityScoutConflict):
                store.add_candidate(
                    candidate_id="bad-child",
                    kind="config",
                    canonical_id="bad-child",
                    artifact_id="bad-child",
                    source_uri="https://example.invalid/bad-child",
                    source_revision="revision-1",
                    parent_relations=[{
                        "relation": "extends",
                        "candidate_id": parent["candidate_id"],
                        "candidate_revision": parent["revision"] + 1,
                    }],
                    capability_dimensions=[{
                        "dimension_id": "general", "fixture_ids": ["opaque-1"],
                        "required_metrics": [{"metric_id": "quality_score", "unit": "ratio"}],
                    }],
                )

    def test_mature_explicit_is_an_ordinary_opaque_fixture_dimension(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            dimensions = candidate["capability_dimensions"]
            self.assertEqual(
                set(dimensions[0]), set(dimensions[1]),
                "mature/explicit must not have a special schema or policy path",
            )
            self.assertEqual(dimensions[1]["dimension_id"], "mature_explicit")
            self.assertEqual(dimensions[1]["fixture_ids"], ["fixture-opaque-92"])
            source = (APP / "services" / "capability_scout.py").read_text(encoding="utf-8").lower()
            self.assertNotIn("prompt_classifier", source)
            self.assertNotIn("classify_prompt", source)

    def test_candidate_inputs_and_schema_are_bounded_and_strict(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            with self.assertRaises(ValueError):
                store.add_candidate(
                    candidate_id="candidate-long",
                    kind="style",
                    canonical_id="canonical-long",
                    artifact_id="artifact-long",
                    source_uri="x" * 501,
                    source_revision="revision-1",
                    capability_dimensions=[{
                        "dimension_id": "general", "fixture_ids": ["fixture-1"],
                        "required_metrics": [{"metric_id": "quality_score", "unit": "ratio"}],
                    }],
                )
            candidate = add_candidate(store)
            with self.assertRaises(ValueError):
                store.add_evidence_claim(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    claim_id="claim-too-long",
                    claim_type="primary",
                    summary="x" * 501,
                    source_uri="https://example.invalid",
                    source_revision="revision-1",
                    evidence_revision="evidence-1",
                    source_sha256=SOURCE_DIGEST,
                    observed_at=NOW,
                )


class CapabilityScoutLifecycleTests(unittest.TestCase):
    def test_lifecycle_allows_issue_contract_paths_and_rejects_skips(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            with self.assertRaises(CapabilityScoutConflict):
                transition(store, candidate, "evidence_ready", 1)
            candidate = transition(store, candidate, "researching", 2)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 4)
            watching = transition(store, candidate, "watching", 5)
            self.assertEqual(watching["lifecycle"], "watching")
            researching = transition(store, watching, "researching", 6)
            self.assertEqual(researching["lifecycle"], "researching")

    def test_pre_test_gate_is_separate_and_blocks_benchmark_admission(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 3)
            with self.assertRaises(CapabilityGateBlocked) as blocked:
                transition(store, candidate, "benchmark_queued", 4)
            self.assertEqual(blocked.exception.stage, "pre_test")
            self.assertEqual(set(blocked.exception.blockers), set(PRE_TEST_GATE_FIELDS))
            unchanged = store.load_state()["candidates"][candidate["candidate_id"]]
            self.assertEqual(unchanged["revision"], candidate["revision"])
            self.assertEqual(unchanged["lifecycle"], "evidence_ready")
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="pre_test",
                decisions=gate_decisions(PRE_TEST_GATE_FIELDS),
                now=NOW + timedelta(seconds=5),
            )
            candidate = transition(store, candidate, "benchmark_queued", 6)
            self.assertEqual(candidate["lifecycle"], "benchmark_queued")
            self.assertTrue(all(
                candidate["post_test_gate"]["decisions"][field]["status"] == "unknown"
                for field in POST_TEST_GATE_FIELDS
            ))

    def test_evidence_sufficient_recommendation_reaches_selection_without_benchmark(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 3)
            candidate = transition(store, candidate, "recommended", 4)
            selected = transition(store, candidate, "selection_ready", 5)
            self.assertEqual(selected["evaluation_disposition"], "evidence_sufficient")
            self.assertIsNone(selected["benchmark_requirement"])
            self.assertEqual(selected["benchmark_receipts"], [])

    def test_benchmark_receipt_is_metadata_only_and_revision_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 3)
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="pre_test",
                decisions=gate_decisions(PRE_TEST_GATE_FIELDS),
                now=NOW + timedelta(seconds=4),
            )
            candidate = transition(store, candidate, "benchmark_queued", 5)
            non_finite = benchmark_receipt(candidate)
            non_finite["fixture_results"][0]["metrics"][0]["value"] = float("inf")
            with self.assertRaises(CapabilityScoutCorrupt):
                store.attach_benchmark_receipt(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    receipt=non_finite,
                )
            stale = benchmark_receipt(candidate)
            stale["candidate_revision"] -= 1
            with self.assertRaises(CapabilityScoutConflict):
                store.attach_benchmark_receipt(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    receipt=stale,
                )
            unknown_fixture = benchmark_receipt(candidate, fixture_indexes=[0])
            unknown_fixture["fixture_results"][0]["fixture_id"] = "fixture-not-approved"
            with self.assertRaises(CapabilityScoutConflict):
                store.attach_benchmark_receipt(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    receipt=unknown_fixture,
                )
            candidate = store.attach_benchmark_receipt(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                receipt=benchmark_receipt(candidate),
                now=NOW + timedelta(seconds=6),
            )
            self.assertEqual(candidate["lifecycle"], "benchmarked")
            self.assertEqual(
                candidate["benchmark_receipts"][0]["fixture_results"][0]["metrics"][0]["sample_count"],
                3,
            )
            source = (APP / "services" / "capability_scout.py").read_text(encoding="utf-8")
            self.assertNotIn("subprocess", source)
            self.assertNotIn("requests", source)

    def test_benchmark_admission_disposition_is_revision_bound_and_cannot_be_forged_sufficient(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 3)
            candidate = transition(store, candidate, "recommended", 4)
            sufficient_binding = candidate["evaluation_disposition_binding"]
            self.assertEqual(
                sufficient_binding["transition_revision"], candidate["revision"],
            )
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="pre_test",
                decisions=gate_decisions(PRE_TEST_GATE_FIELDS),
                now=NOW + timedelta(seconds=5),
            )
            candidate = transition(store, candidate, "benchmark_queued", 6)
            benchmark_binding = candidate["evaluation_disposition_binding"]
            self.assertEqual(
                benchmark_binding["transition_revision"], candidate["revision"],
            )
            self.assertEqual(
                benchmark_binding["transition_revision"],
                candidate["benchmark_requirement"]["candidate_revision"] + 1,
            )
            invalid_receipt = benchmark_receipt(
                candidate, receipt_id="remote-artifact", fixture_indexes=[0],
            )
            for invalid_ref in (
                "https://private.invalid/result.json",
                "private://benchmarks/result.json",
            ):
                with self.subTest(invalid_ref=invalid_ref):
                    invalid_receipt["private_artifact_ref"] = invalid_ref
                    with self.assertRaises(CapabilityScoutCorrupt):
                        store.attach_benchmark_receipt(
                            candidate["candidate_id"],
                            expected_revision=candidate["revision"],
                            receipt=invalid_receipt,
                        )
            candidate = store.attach_benchmark_receipt(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                receipt=benchmark_receipt(
                    candidate, receipt_id="partial", fixture_indexes=[0],
                ),
                now=NOW + timedelta(seconds=7),
            )
            document = json.loads(store.state_path.read_text(encoding="utf-8"))
            persisted = document["candidates"][candidate["candidate_id"]]
            forged_binding = {
                "disposition": "evidence_sufficient",
                "transition_target": "recommended",
                "transition_revision": sufficient_binding["transition_revision"],
                "evidence_set_revision": persisted["evidence_set_revision"],
                "identity_sha256": persisted["identity"]["identity_sha256"],
            }
            forged_binding["binding_revision"] = hashlib.sha256(json.dumps(
                forged_binding,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")).hexdigest()
            persisted["evaluation_disposition"] = "evidence_sufficient"
            persisted["evaluation_disposition_binding"] = forged_binding
            refresh_integrity(document)
            store.state_path.write_text(json.dumps(document), encoding="utf-8")
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

    def test_action_required_blocker_and_resume_survive_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 3)
            action = store.require_action(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                blocker_kind="download_approval",
                resume_state="benchmark_queued",
                detail="Approve the exact pinned artifact download.",
                private_detail=PRIVACY_CANARY,
                now=NOW + timedelta(seconds=4),
            )
            restarted = test_store(root)
            durable = restarted.load_state()["candidates"][candidate["candidate_id"]]
            self.assertEqual(durable["lifecycle"], "action_required")
            self.assertEqual(durable["action_required"]["action_id"], action["action_id"])
            with self.assertRaises(CapabilityGateBlocked):
                restarted.resolve_action(
                    action["action_id"],
                    resolution="resolved",
                    expected_candidate_revision=durable["revision"],
                    now=NOW + timedelta(seconds=5),
                )
            durable = restarted.update_gate(
                candidate["candidate_id"],
                expected_revision=durable["revision"],
                stage="pre_test",
                decisions=gate_decisions(PRE_TEST_GATE_FIELDS),
                now=NOW + timedelta(seconds=6),
            )
            resumed = restarted.resolve_action(
                action["action_id"],
                resolution="resolved",
                expected_candidate_revision=durable["revision"],
                now=NOW + timedelta(seconds=7),
            )
            self.assertEqual(resumed["lifecycle"], "benchmark_queued")
            saved_action = restarted.load_state()["actions"][action["action_id"]]
            self.assertEqual(saved_action["status"], "resolved")

    def test_action_resume_cannot_bypass_receipt_nonce_or_promotion_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = recommendation_ready(store)
            for protected in ("owner_selected", "benchmarked", "promoted"):
                with self.subTest(protected=protected), self.assertRaises(CapabilityScoutConflict):
                    store.require_action(
                        candidate["candidate_id"],
                        expected_revision=candidate["revision"],
                        blocker_kind="human_judgment",
                        resume_state=protected,
                        detail="This cannot bypass the dedicated gate.",
                    )

    def test_action_resume_rechecks_evidence_ready_invariant(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            action = store.require_action(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                blocker_kind="evidence_required",
                resume_state="evidence_ready",
                detail="Exact primary evidence must be recorded first.",
                now=NOW + timedelta(seconds=2),
            )
            with self.assertRaises(CapabilityScoutConflict):
                store.resolve_action(
                    action["action_id"],
                    resolution="resolved",
                    expected_candidate_revision=candidate["revision"] + 1,
                    now=NOW + timedelta(seconds=3),
                )
            durable = store.load_state()["candidates"][candidate["candidate_id"]]
            self.assertEqual(durable["lifecycle"], "action_required")
            self.assertEqual(durable["evidence_claims"], [])

    def test_benchmark_requires_frozen_complete_fixture_metric_matrix_and_rechecks_revocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate)
            candidate = transition(store, candidate, "evidence_ready", 3)
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="pre_test",
                decisions=gate_decisions(PRE_TEST_GATE_FIELDS),
                now=NOW + timedelta(seconds=4),
            )
            candidate = transition(store, candidate, "benchmark_queued", 5)
            requirement = candidate["benchmark_requirement"]
            self.assertEqual(
                [item["dimension_id"] for item in requirement["fixture_matrix"]],
                ["general", "mature_explicit"],
            )

            first = store.attach_benchmark_receipt(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                receipt=benchmark_receipt(
                    candidate, receipt_id="receipt-general", fixture_indexes=[0],
                ),
                now=NOW + timedelta(seconds=6),
            )
            self.assertEqual(first["lifecycle"], "benchmark_queued")

            missing_metric = benchmark_receipt(
                first, receipt_id="receipt-mature-bad", fixture_indexes=[1],
            )
            missing_metric["fixture_results"][0]["metrics"].pop()
            with self.assertRaises(CapabilityScoutConflict):
                store.attach_benchmark_receipt(
                    first["candidate_id"],
                    expected_revision=first["revision"],
                    receipt=missing_metric,
                )
            wrong_unit = benchmark_receipt(
                first, receipt_id="receipt-mature-unit", fixture_indexes=[1],
            )
            wrong_unit["fixture_results"][0]["metrics"][0]["unit"] = "milliseconds"
            with self.assertRaises(CapabilityScoutConflict):
                store.attach_benchmark_receipt(
                    first["candidate_id"],
                    expected_revision=first["revision"],
                    receipt=wrong_unit,
                )

            revoked = store.update_gate(
                first["candidate_id"],
                expected_revision=first["revision"],
                stage="pre_test",
                decisions={
                    "download_approval": {
                        "status": "denied", "basis_revision": "revocation-1",
                    },
                },
                now=NOW + timedelta(seconds=7),
            )
            old_receipt = benchmark_receipt(
                first, receipt_id="receipt-mature-old", fixture_indexes=[1],
            )
            with self.assertRaises(CapabilityGateBlocked):
                store.attach_benchmark_receipt(
                    revoked["candidate_id"],
                    expected_revision=revoked["revision"],
                    receipt=old_receipt,
                )
            reapproved = store.update_gate(
                revoked["candidate_id"],
                expected_revision=revoked["revision"],
                stage="pre_test",
                decisions={
                    "download_approval": {
                        "status": "approved", "basis_revision": "approval-2",
                    },
                },
                now=NOW + timedelta(seconds=8),
            )
            with self.assertRaises(CapabilityGateBlocked):
                store.attach_benchmark_receipt(
                    reapproved["candidate_id"],
                    expected_revision=reapproved["revision"],
                    receipt=old_receipt,
                )
            refreshed = store.freeze_benchmark_requirement(
                reapproved["candidate_id"],
                expected_revision=reapproved["revision"],
                now=NOW + timedelta(seconds=9),
            )
            refreshed = store.attach_benchmark_receipt(
                refreshed["candidate_id"],
                expected_revision=refreshed["revision"],
                receipt=benchmark_receipt(
                    refreshed, receipt_id="receipt-general-2", fixture_indexes=[0],
                ),
                now=NOW + timedelta(seconds=10),
            )
            completed = store.attach_benchmark_receipt(
                refreshed["candidate_id"],
                expected_revision=refreshed["revision"],
                receipt=benchmark_receipt(
                    refreshed, receipt_id="receipt-mature-2", fixture_indexes=[1],
                ),
                now=NOW + timedelta(seconds=11),
            )
            self.assertEqual(completed["lifecycle"], "benchmarked")
            self.assertEqual(len(completed["benchmark_receipts"]), 3)

    def test_owner_nonce_is_exact_revision_bound_expiring_single_use_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = recommendation_ready(store)
            issued = store.issue_owner_selection_nonce(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                **OWNER_KEYS,
                ttl_seconds=10,
                now=NOW + timedelta(seconds=6),
            )
            restarted = test_store(root)
            with self.assertRaises(CapabilityScoutConflict):
                restarted.select_candidate(
                    candidate["candidate_id"],
                    nonce=issued["nonce"],
                    candidate_revision=candidate["revision"] + 1,
                    evidence_set_revision=issued["evidence_set_revision"],
                    **OWNER_KEYS,
                    now=NOW + timedelta(seconds=7),
                )
            selected = restarted.select_candidate(
                candidate["candidate_id"],
                nonce=issued["nonce"],
                candidate_revision=issued["candidate_revision"],
                evidence_set_revision=issued["evidence_set_revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=8),
            )
            self.assertEqual(selected["lifecycle"], "owner_selected")
            self.assertEqual(selected["owner_selection"]["candidate_revision"], candidate["revision"])
            with self.assertRaises(CapabilityScoutConflict):
                restarted.select_candidate(
                    candidate["candidate_id"],
                    nonce=issued["nonce"],
                    candidate_revision=selected["revision"],
                    evidence_set_revision=issued["evidence_set_revision"],
                    **OWNER_KEYS,
                    now=NOW + timedelta(seconds=9),
                )

            second_root = Path(temporary) / "expired"
            expiring = test_store(second_root, token="short-lived")
            ready = recommendation_ready(expiring)
            short = expiring.issue_owner_selection_nonce(
                ready["candidate_id"], expected_revision=ready["revision"],
                **OWNER_KEYS,
                ttl_seconds=1, now=NOW + timedelta(seconds=20),
            )
            with self.assertRaises(CapabilityScoutConflict):
                expiring.select_candidate(
                    ready["candidate_id"], nonce=short["nonce"],
                    candidate_revision=short["candidate_revision"],
                    evidence_set_revision=short["evidence_set_revision"],
                    **OWNER_KEYS,
                    now=NOW + timedelta(seconds=21),
                )

    def test_owner_nonce_rejects_foreign_owner_session_and_authority_then_allows_exact_owner_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = recommendation_ready(store)
            issued = store.issue_owner_selection_nonce(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=20),
            )
            variants = (
                {**OWNER_KEYS, "owner_key": "owner-foreign"},
                {**OWNER_KEYS, "session_key": "session-foreign"},
                {**OWNER_KEYS, "authority_key": "authority-foreign"},
            )
            for foreign in variants:
                with self.subTest(foreign=foreign), self.assertRaises(CapabilityScoutConflict):
                    store.select_candidate(
                        candidate["candidate_id"],
                        nonce=issued["nonce"],
                        candidate_revision=issued["candidate_revision"],
                        evidence_set_revision=issued["evidence_set_revision"],
                        **foreign,
                        now=NOW + timedelta(seconds=21),
                    )
            selected = store.select_candidate(
                candidate["candidate_id"],
                nonce=issued["nonce"],
                candidate_revision=issued["candidate_revision"],
                evidence_set_revision=issued["evidence_set_revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=22),
            )
            self.assertEqual(
                selected["owner_selection"]["owner_audit_commitment"],
                issued["owner_audit_commitment"],
            )
            saved = json.dumps(store.load_state(), sort_keys=True)
            self.assertNotIn(OWNER_KEYS["owner_key"], saved)
            self.assertNotIn(OWNER_KEYS["session_key"], saved)
            self.assertNotIn(OWNER_KEYS["authority_key"], saved)
            with self.assertRaises(CapabilityScoutConflict):
                store.select_candidate(
                    candidate["candidate_id"],
                    nonce=issued["nonce"],
                    candidate_revision=selected["revision"],
                    evidence_set_revision=issued["evidence_set_revision"],
                    **OWNER_KEYS,
                    now=NOW + timedelta(seconds=23),
                )

    def test_post_test_gate_owner_binding_regression_and_rollback_gate_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = recommendation_ready(store)
            issued = store.issue_owner_selection_nonce(
                candidate["candidate_id"], expected_revision=candidate["revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=6),
            )
            candidate = store.select_candidate(
                candidate["candidate_id"], nonce=issued["nonce"],
                candidate_revision=issued["candidate_revision"],
                evidence_set_revision=issued["evidence_set_revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=7),
            )
            with self.assertRaises(CapabilityScoutConflict):
                store.add_rollback_lineage(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    implementation_revision="too-early",
                    previous_revision="implementation-v1",
                    artifact_ids=["artifact-main"],
                    reason="Cannot bind before implementation starts.",
                )
            with self.assertRaises(CapabilityScoutConflict):
                store.update_gate(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    stage="post_test",
                    decisions={
                        "hosting": {
                            "status": "approved", "basis_revision": "too-early",
                        },
                    },
                )
            candidate = transition(store, candidate, "implementing", 8)
            with self.assertRaises(CapabilityScoutConflict):
                store.update_gate(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    stage="post_test",
                    decisions={
                        "hosting": {
                            "status": "approved", "basis_revision": "still-too-early",
                        },
                    },
                )
            candidate = store.add_rollback_lineage(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                implementation_revision="implementation-v2",
                previous_revision="implementation-v1",
                artifact_ids=["artifact-main", "artifact-config"],
                reason="Restore the complete prior implementation set.",
                private_notes=PRIVACY_CANARY,
                now=NOW + timedelta(seconds=9),
            )
            candidate = transition(store, candidate, "verification_required", 10)
            candidate = transition(store, candidate, "promotion_ready", 11)
            with self.assertRaises(CapabilityGateBlocked) as blocked:
                store.promote(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    now=NOW + timedelta(seconds=12),
                )
            self.assertIn("hosting", blocked.exception.blockers)
            self.assertIn("regression", blocked.exception.blockers)
            decisions = gate_decisions(
                POST_TEST_GATE_FIELDS,
                owner_binding=candidate["owner_selection"]["binding_revision"],
            )
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="post_test",
                decisions=decisions,
                now=NOW + timedelta(seconds=13),
            )
            first_binding = candidate["post_test_gate"]["decisions"]["hosting"][
                "implementation_binding_revision"
            ]
            candidate = transition(store, candidate, "verification_required", 14)
            with self.assertRaises(CapabilityScoutConflict):
                store.add_rollback_lineage(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    implementation_revision="implementation-v3",
                    previous_revision="implementation-v1",
                    artifact_ids=["artifact-main"],
                    reason="Discontinuous rollback must fail.",
                )
            candidate = store.add_rollback_lineage(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                implementation_revision="implementation-v3",
                previous_revision="implementation-v2",
                artifact_ids=["artifact-main", "artifact-config"],
                reason="Bind the replacement implementation and its rollback set.",
                now=NOW + timedelta(seconds=15),
            )
            candidate = transition(store, candidate, "promotion_ready", 16)
            with self.assertRaises(CapabilityGateBlocked):
                store.promote(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    now=NOW + timedelta(seconds=17),
                )
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="post_test",
                decisions=decisions,
                now=NOW + timedelta(seconds=18),
            )
            self.assertNotEqual(
                candidate["post_test_gate"]["decisions"]["hosting"][
                    "implementation_binding_revision"
                ],
                first_binding,
            )
            promoted = store.promote(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                now=NOW + timedelta(seconds=19),
            )
            self.assertEqual(promoted["lifecycle"], "promoted")
            self.assertEqual(promoted["rollback_lineage"][0]["previous_revision"], "implementation-v1")


class CapabilityScoutPersistenceTests(unittest.TestCase):
    def test_missing_path_is_allowed_but_parent_root_and_state_symlinks_including_dangling_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            missing = test_store(base / "ordinary-missing")
            self.assertEqual(missing.load_state()["store_revision"], 0)

            actual_parent = base / "actual-parent"
            actual_parent.mkdir()
            linked_parent = base / "linked-parent"
            linked_parent.symlink_to(actual_parent, target_is_directory=True)
            with self.assertRaises(CapabilityScoutCorrupt):
                test_store(linked_parent / "scout").load_state()

            dangling_root = base / "dangling-root"
            dangling_root.symlink_to(base / "absent-root", target_is_directory=True)
            with self.assertRaises(CapabilityScoutCorrupt):
                test_store(dangling_root).load_state()

            root = base / "state-symlink"
            store = test_store(root)
            root.mkdir(mode=0o700)
            store.state_path.symlink_to(base / "absent-state")
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

    def test_terminal_candidate_rollover_archives_immutable_provenance_and_frees_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            first = add_candidate(store, candidate_id="terminal-one")
            first = transition(store, first, "researching", 1)
            first = add_evidence(store, first)
            first = transition(store, first, "evidence_ready", 3)
            first = transition(store, first, "rejected", 4)
            with mock.patch("services.capability_scout.MAX_CANDIDATES", 1):
                second = add_candidate(store, candidate_id="active-two")
            state = store.load_state()
            self.assertEqual(set(state["candidates"]), {second["candidate_id"]})
            archives = list((root / "archive").glob("*.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(stat.S_IMODE((root / "archive").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(archives[0].stat().st_mode), 0o600)
            seen_records = list((root / "seen").glob("*.json"))
            self.assertEqual(len(seen_records), 2)
            self.assertTrue(all(
                stat.S_IMODE(path.stat().st_mode) == 0o600
                for path in seen_records
            ))
            archived = json.loads(archives[0].read_text(encoding="utf-8"))
            self.assertEqual(archived["candidate"]["candidate_id"], first["candidate_id"])
            self.assertEqual(archived["candidate"]["identity"], first["identity"])
            projection = store.archive_projection(limit=1)
            self.assertEqual(projection["items"][0]["kind"], "candidate")
            self.assertNotIn("fixture-general-01", json.dumps(projection))
            self.assertEqual(
                test_store(root).load_state()["candidates"][second["candidate_id"]]["identity"],
                second["identity"],
            )
            with self.assertRaises(CapabilityScoutConflict):
                add_candidate(store, candidate_id="terminal-one")
            with self.assertRaises(CapabilityScoutConflict):
                store.add_candidate(
                    candidate_id="different-id-same-identity",
                    kind=first["kind"],
                    canonical_id=first["identity"]["canonical_id"],
                    artifact_id=first["identity"]["artifact_id"],
                    source_uri=first["identity"]["source_uri"],
                    source_revision=first["identity"]["source_revision"],
                    capability_dimensions=first["capability_dimensions"],
                )

    def test_resolved_action_rollover_frees_lifetime_capacity_for_active_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            with mock.patch("services.capability_scout.MAX_ACTIONS", 1):
                first = store.require_action(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    blocker_kind="authorization",
                    resume_state="researching",
                    detail="First bounded owner action.",
                    private_detail=PRIVACY_CANARY,
                    now=NOW + timedelta(seconds=2),
                )
                candidate = store.resolve_action(
                    first["action_id"],
                    resolution="resolved",
                    expected_candidate_revision=candidate["revision"] + 1,
                    now=NOW + timedelta(seconds=3),
                )
                second = store.require_action(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    blocker_kind="spend_approval",
                    resume_state="researching",
                    detail="Second bounded owner action.",
                    now=NOW + timedelta(seconds=4),
                )
            state = store.load_state()
            self.assertEqual(set(state["actions"]), {second["action_id"]})
            self.assertEqual(
                state["candidates"][candidate["candidate_id"]]["action_history"],
                [second["action_id"]],
            )
            archived = list((root / "archive").glob("action-*.json"))
            self.assertEqual(len(archived), 1)
            record = json.loads(archived[0].read_text(encoding="utf-8"))
            self.assertEqual(record["action"]["action_id"], first["action_id"])
            projection_text = json.dumps(store.archive_projection(), sort_keys=True)
            self.assertNotIn(PRIVACY_CANARY, projection_text)

    def test_archive_projection_exact_validates_action_and_candidate_binding_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = add_candidate(store)
            candidate = transition(store, candidate, "researching", 1)
            with mock.patch("services.capability_scout.MAX_ACTIONS", 1):
                first = store.require_action(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    blocker_kind="authorization",
                    resume_state="researching",
                    detail="Bounded owner action.",
                    now=NOW + timedelta(seconds=2),
                )
                candidate = store.resolve_action(
                    first["action_id"],
                    resolution="resolved",
                    expected_candidate_revision=candidate["revision"] + 1,
                    now=NOW + timedelta(seconds=3),
                )
                store.require_action(
                    candidate["candidate_id"],
                    expected_revision=candidate["revision"],
                    blocker_kind="spend_approval",
                    resume_state="researching",
                    detail="Force resolved action archival.",
                    now=NOW + timedelta(seconds=4),
                )
            archive_path = next((root / "archive").glob("action-*.json"))
            original = json.loads(archive_path.read_text(encoding="utf-8"))
            for target in ("action", "candidate_binding"):
                with self.subTest(target=target):
                    forged = json.loads(json.dumps(original))
                    forged[target]["arbitrary_private_field"] = PRIVACY_CANARY
                    refresh_integrity(forged)
                    archive_path.write_text(json.dumps(forged), encoding="utf-8")
                    os.chmod(archive_path, 0o600)
                    with self.assertRaises(CapabilityScoutCorrupt):
                        store.archive_projection()
            archive_path.write_text(json.dumps(original), encoding="utf-8")
            os.chmod(archive_path, 0o600)
            self.assertEqual(store.archive_projection()["items"][0]["kind"], "action")

    def test_archive_descriptor_closes_on_validation_or_write_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            captured: list[int] = []

            def fail_after_open(
                archive_fd: int, archive_name: str, document: dict,
            ) -> None:
                captured.append(archive_fd)
                raise RuntimeError("synthetic archive failure")

            with store._writer() as root_fd:
                with mock.patch.object(
                    store, "_write_archive_document_to_fd",
                    side_effect=fail_after_open,
                ):
                    with self.assertRaisesRegex(RuntimeError, "synthetic archive"):
                        store._write_archive_document_unlocked(
                            "action-synthetic.json", {}, root_fd=root_fd,
                        )
            self.assertEqual(len(captured), 1)
            with self.assertRaises(OSError):
                os.fstat(captured[0])

    def test_root_and_subdirectory_descriptors_close_on_post_open_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            real_open = os.open
            real_close = os.close

            root_opened: list[int] = []
            root_closed: list[int] = []

            def track_root_open(*args, **kwargs):
                descriptor = real_open(*args, **kwargs)
                root_opened.append(descriptor)
                return descriptor

            def track_root_close(descriptor: int) -> None:
                root_closed.append(descriptor)
                real_close(descriptor)

            with (
                mock.patch(
                    "services.capability_scout.os.open",
                    side_effect=track_root_open,
                ),
                mock.patch(
                    "services.capability_scout.os.close",
                    side_effect=track_root_close,
                ),
                mock.patch(
                    "services.capability_scout.os.fchmod",
                    side_effect=OSError("synthetic root chmod failure"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "synthetic root chmod"):
                    with store._root_fd(create=True):
                        self.fail("root context must not yield")
            self.assertIn(root_opened[-1], root_closed)

            with store._root_fd(create=True) as root_fd:
                child_opened: list[int] = []
                child_closed: list[int] = []

                def track_child_open(*args, **kwargs):
                    descriptor = real_open(*args, **kwargs)
                    child_opened.append(descriptor)
                    return descriptor

                def track_child_close(descriptor: int) -> None:
                    child_closed.append(descriptor)
                    real_close(descriptor)

                with (
                    mock.patch(
                        "services.capability_scout.os.open",
                        side_effect=track_child_open,
                    ),
                    mock.patch(
                        "services.capability_scout.os.close",
                        side_effect=track_child_close,
                    ),
                    mock.patch(
                        "services.capability_scout.os.fstat",
                        side_effect=OSError("synthetic subdir stat failure"),
                    ),
                ):
                    with self.assertRaisesRegex(OSError, "synthetic subdir stat"):
                        store._subdir_fd(root_fd, "archive", create=True)
                self.assertIn(child_opened[-1], child_closed)

    def test_unsupported_platform_contract_fails_before_path_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch(
                "services.capability_scout._SECURE_ROOTED_IO_SUPPORTED", False,
            ):
                with self.assertRaisesRegex(CapabilityScoutError, "POSIX rooted"):
                    test_store(Path(temporary) / "scout")

    def test_archive_catalog_is_bounded_and_failed_rollover_preserves_active_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            first = add_candidate(store, candidate_id="first")
            first = transition(store, first, "researching", 1)
            first = add_evidence(store, first)
            first = transition(store, first, "evidence_ready", 3)
            transition(store, first, "rejected", 4)
            with mock.patch("services.capability_scout.MAX_CANDIDATES", 1):
                second = add_candidate(store, candidate_id="second")
                second = transition(store, second, "researching", 5)
                second = add_evidence(store, second)
                second = transition(store, second, "evidence_ready", 7)
                second = transition(store, second, "rejected", 8)
                with mock.patch("services.capability_scout.MAX_ARCHIVE_FILES", 1):
                    with self.assertRaises(CapabilityScoutError):
                        add_candidate(store, candidate_id="third")
            durable = store.load_state()
            self.assertEqual(set(durable["candidates"]), {second["candidate_id"]})
            page = store.archive_projection(limit=1)
            self.assertEqual(len(page["items"]), 1)
            self.assertIsNone(page["next_cursor"])

    def test_hostile_lazy_iterables_are_rejected_without_iteration(self):
        class HostileIterable:
            iterated = False

            def __iter__(self):
                self.iterated = True
                raise AssertionError("must not iterate")

        class HostileList(list):
            iterated = False

            def __iter__(self):
                self.iterated = True
                raise AssertionError("list subclasses must be rejected before iteration")

        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            hostile = HostileIterable()
            with self.assertRaises(ValueError):
                store.add_candidate(
                    candidate_id="hostile",
                    kind="style",
                    canonical_id="hostile",
                    artifact_id="hostile",
                    source_uri="https://example.invalid/hostile",
                    source_revision="revision-1",
                    capability_dimensions=hostile,
                )
            self.assertFalse(hostile.iterated)
            hostile_list = HostileList()
            with self.assertRaises(ValueError):
                store.add_candidate(
                    candidate_id="hostile-list",
                    kind="style",
                    canonical_id="hostile-list",
                    artifact_id="hostile-list",
                    source_uri="https://example.invalid/hostile-list",
                    source_revision="revision-1",
                    capability_dimensions=hostile_list,
                )
            self.assertFalse(hostile_list.iterated)

    def test_aggregate_fixture_matrix_is_rejected_at_admission_before_benchmarking(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            dimensions = [
                {
                    "dimension_id": f"dimension-{index}",
                    "fixture_ids": [
                        f"fixture-{index}-{fixture}" for fixture in range(43)
                    ],
                    "required_metrics": [
                        {"metric_id": "quality", "unit": "ratio"},
                    ],
                }
                for index in range(3)
            ]
            with self.assertRaises(ValueError):
                add_candidate(store, dimensions=tuple(dimensions))

    def test_recomputed_state_cannot_bypass_aggregate_fixture_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store)
            document = json.loads(store.state_path.read_text(encoding="utf-8"))
            dimension = document["candidates"][candidate["candidate_id"]][
                "capability_dimensions"
            ][0]
            dimension["fixture_ids"] = [
                f"fixture-{index}" for index in range(129)
            ]
            without_integrity = {
                key: value for key, value in document.items() if key != "integrity"
            }
            document["integrity"]["digest"] = hashlib.sha256(json.dumps(
                without_integrity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")).hexdigest()
            store.state_path.write_text(json.dumps(document), encoding="utf-8")
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

    def test_public_and_admin_source_provenance_removes_credentials_query_fragment_and_private_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            source = (
                f"https://user:{PRIVACY_CANARY}@example.invalid/private/{PRIVACY_CANARY}"
                f"?credential={PRIVACY_CANARY}#fragment-{PRIVACY_CANARY}"
            )
            candidate = add_candidate(store, source_uri=source)
            candidate = transition(store, candidate, "researching", 1)
            add_evidence(store, candidate, source_uri=source)
            add_candidate(
                store,
                candidate_id="path-canary",
                source_uri=f"https://example.invalid/repos/{PRIVACY_CANARY}/revision",
            )
            public_text = json.dumps(store.public_projection(), sort_keys=True)
            admin_text = json.dumps(store.admin_projection(), sort_keys=True)
            for projected in (public_text, admin_text):
                self.assertNotIn(PRIVACY_CANARY, projected)
                self.assertNotIn("user:", projected)
                self.assertNotIn("credential=", projected)
                self.assertNotIn("#fragment", projected)
                self.assertNotIn("/private/", projected)
                self.assertIn("https://example.invalid [provenance:", projected)

    def test_recomputed_integrity_cannot_bypass_cross_state_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = recommendation_ready(store)
            issued = store.issue_owner_selection_nonce(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=20),
            )
            candidate = store.select_candidate(
                candidate["candidate_id"],
                nonce=issued["nonce"],
                candidate_revision=issued["candidate_revision"],
                evidence_set_revision=issued["evidence_set_revision"],
                **OWNER_KEYS,
                now=NOW + timedelta(seconds=21),
            )
            candidate = transition(store, candidate, "implementing", 22)
            candidate = store.add_rollback_lineage(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                implementation_revision="implementation-v2",
                previous_revision="implementation-v1",
                artifact_ids=["artifact-main"],
                reason="Exact rollback.",
                now=NOW + timedelta(seconds=23),
            )
            candidate = transition(store, candidate, "verification_required", 24)
            candidate = transition(store, candidate, "promotion_ready", 25)
            candidate = store.update_gate(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                stage="post_test",
                decisions=gate_decisions(
                    POST_TEST_GATE_FIELDS,
                    owner_binding=candidate["owner_selection"]["binding_revision"],
                ),
                now=NOW + timedelta(seconds=26),
            )
            store.promote(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                now=NOW + timedelta(seconds=27),
            )
            document = json.loads(store.state_path.read_text(encoding="utf-8"))
            document["candidates"][candidate["candidate_id"]]["rollback_lineage"] = []
            without_integrity = {
                key: value for key, value in document.items() if key != "integrity"
            }
            document["integrity"]["digest"] = hashlib.sha256(json.dumps(
                without_integrity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")).hexdigest()
            store.state_path.write_text(json.dumps(document), encoding="utf-8")
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

    def test_no_content_classifier_or_moderation_dependency_exists_in_core_ast(self):
        source = (APP / "services" / "capability_scout.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        banned = {
            "moderation", "moderator", "classifier", "classify", "prompt_scanner",
            "content_policy", "responsible_use", "mature_policy", "nsfw_policy",
            "age_heuristic", "llm_service",
        }
        imported_modules = set()
        referenced_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.add(node.module or "")
            elif isinstance(node, ast.Name):
                referenced_names.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                referenced_names.add(node.attr.lower())
        self.assertFalse(any(module.startswith("services") for module in imported_modules))
        self.assertFalse(banned & referenced_names)
        self.assertFalse(any(
            token in module.lower()
            for module in imported_modules
            for token in banned
        ))

    def test_state_is_atomic_private_restart_durable_and_permissions_are_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = add_candidate(store)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.state_path.stat().st_mode), 0o600)
            restarted = test_store(root)
            self.assertEqual(
                restarted.load_state()["candidates"][candidate["candidate_id"]]["identity"],
                candidate["identity"],
            )
            os.chmod(root, 0o755)
            with self.assertRaises(CapabilityScoutCorrupt):
                restarted.load_state()
            add_candidate(restarted, candidate_id="candidate-two")
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_atomic_replace_failure_preserves_last_committed_state_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            add_candidate(store)
            before = store.state_path.read_bytes()
            with mock.patch("services.capability_scout.os.replace", side_effect=OSError("crash")):
                with self.assertRaises(OSError):
                    add_candidate(store, candidate_id="candidate-two")
            self.assertEqual(store.state_path.read_bytes(), before)
            self.assertEqual(list(root.glob(".state.json.*.tmp")), [])

    def test_recovery_discards_orphan_temp_only_beside_valid_committed_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            candidate = add_candidate(store)
            orphan = root / (".state.json." + "a" * 32 + ".tmp")
            orphan.write_text("partial", encoding="utf-8")
            recovered = test_store(root).recover()
            self.assertFalse(orphan.exists())
            self.assertIn(candidate["candidate_id"], recovered["candidates"])

            incomplete_root = Path(temporary) / "incomplete"
            incomplete_root.mkdir(mode=0o700)
            (incomplete_root / (".state.json." + "b" * 32 + ".tmp")).write_text(
                "partial", encoding="utf-8",
            )
            with self.assertRaises(CapabilityScoutCorrupt):
                test_store(incomplete_root).recover()

    def test_malformed_tampered_oversize_and_unsafe_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            add_candidate(store)
            original = store.state_path.read_bytes()

            document = json.loads(original)
            document["candidates"]["candidate-one"]["lifecycle"] = "promoted"
            store.state_path.write_text(json.dumps(document), encoding="utf-8")
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

            store.state_path.write_bytes(b'{"schema_version": 1,')
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

            store.state_path.write_bytes(original)
            os.chmod(store.state_path, 0o644)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

            strict = json.loads(original)
            strict["unexpected_private_bucket"] = {"value": PRIVACY_CANARY}
            without_integrity = {
                key: value for key, value in strict.items() if key != "integrity"
            }
            strict["integrity"]["digest"] = hashlib.sha256(json.dumps(
                without_integrity,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")).hexdigest()
            store.state_path.write_text(json.dumps(strict), encoding="utf-8")
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

            store.state_path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            os.chmod(store.state_path, 0o600)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

            store.state_path.unlink()
            target = Path(temporary) / "external-state"
            target.write_bytes(original)
            store.state_path.symlink_to(target)
            with self.assertRaises(CapabilityScoutCorrupt):
                store.load_state()

    def test_existing_writer_lock_fails_closed_without_reclamation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "scout"
            store = test_store(root)
            root.mkdir(mode=0o700)
            lock = root / ".state.lock"
            lock.write_text("unknown writer", encoding="utf-8")
            with self.assertRaises(CapabilityScoutLocked):
                add_candidate(store)
            self.assertEqual(lock.read_text(encoding="utf-8"), "unknown writer")

    def test_public_and_admin_projections_whitelist_and_drop_all_private_canaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = test_store(Path(temporary) / "scout")
            candidate = add_candidate(store, private_notes=PRIVACY_CANARY)
            candidate = transition(store, candidate, "researching", 1)
            candidate = add_evidence(store, candidate, private_notes=PRIVACY_CANARY)
            candidate = transition(store, candidate, "evidence_ready", 3)
            action = store.require_action(
                candidate["candidate_id"],
                expected_revision=candidate["revision"],
                blocker_kind="authorization",
                resume_state="evidence_ready",
                detail="Owner authorization is required.",
                private_detail=PRIVACY_CANARY,
                now=NOW + timedelta(seconds=4),
            )
            self.assertTrue(action["private_detail"])
            public = store.public_projection()
            admin = store.admin_projection()
            self.assertNotIn(PRIVACY_CANARY, json.dumps(public, sort_keys=True))
            self.assertNotIn(PRIVACY_CANARY, json.dumps(admin, sort_keys=True))
            self.assertNotIn("fixture-opaque-92", json.dumps(public, sort_keys=True))
            self.assertNotIn("fixture-opaque-92", json.dumps(admin, sort_keys=True))
            self.assertNotIn("selection_nonces", public)
            self.assertNotIn("selection_nonces", admin)
            self.assertNotIn("integrity", public)
            self.assertNotIn("integrity", admin)
            self.assertEqual(set(public), {"schema_version", "store_revision", "updated_at", "candidates"})
            self.assertEqual(
                set(admin),
                {"schema_version", "store_revision", "updated_at", "candidates", "actions"},
            )


if __name__ == "__main__":
    unittest.main()
