"""Responsible-use notice and durable acknowledgement regressions."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.responsible_use import (  # noqa: E402
    CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
    CURRENT_RESPONSIBLE_USE_VERSION,
    InvalidResponsibleUseAcceptanceError,
    MAX_ACCEPTANCE_RECORD_BYTES,
    RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION,
    RESPONSIBLE_USE_DOCUMENT_ID,
    StaleResponsibleUseNoticeError,
    UnknownResponsibleUseVersionError,
    accept_responsible_use,
    create_acceptance_record,
    deserialize_acceptance_record,
    normalize_acceptance_record,
    responsible_use_binding,
    responsible_use_notice,
    responsible_use_status,
    serialize_acceptance_record,
)


ACCEPTED_AT = datetime(2026, 8, 11, 7, 8, 9, 123456, tzinfo=timezone.utc)


class ResponsibleUseNoticeTests(unittest.TestCase):
    def test_public_copy_and_digest_are_exactly_pinned(self):
        notice = responsible_use_notice()
        self.assertEqual(notice, {
            "document_id": "maestro_responsible_use",
            "version": 1,
            "content_sha256": (
                "16f9456299c1aa9f8219f09e60924fa40f2838c1f14b87dc5b6d5aef5f185985"
            ),
            "digest_algorithm": "sha256",
            "title": "Responsible use",
            "paragraphs": [
                (
                    "Use Maestro lawfully in your jurisdiction. Make sure "
                    "you have the rights and permissions needed for the "
                    "material you provide, the work you request, and how "
                    "you use the results."
                ),
                (
                    "Obtain consent when another person's identity, likeness, "
                    "or other protected interests are involved. Follow the "
                    "terms shown for each selected model and provider."
                ),
                (
                    "Payments and donations do not authorize prohibited "
                    "content or use, or change those responsibilities. "
                    "Mature-content capability is optional, varies by model "
                    "and setup, and is not assumed for every user. Mature "
                    "examples are shown only after you choose to view or "
                    "work with them."
                ),
                (
                    "This acknowledgement does not remove legal duties that "
                    "remain with Maestro's operator or its providers."
                ),
            ],
        })
        digest_basis = {
            key: notice[key]
            for key in ("document_id", "paragraphs", "title", "version")
        }
        calculated = hashlib.sha256(json.dumps(
            digest_basis,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(calculated, CURRENT_RESPONSIBLE_USE_CONTENT_SHA256)

    def test_notice_covers_the_bounded_responsibility_contract(self):
        copy_text = " ".join(responsible_use_notice()["paragraphs"]).lower()
        for required in (
            "lawfully in your jurisdiction",
            "rights and permissions",
            "obtain consent",
            "selected model and provider",
            "payments and donations do not authorize prohibited content or use",
            "mature-content capability is optional",
            "not assumed for every user",
            "only after you choose",
            "does not remove legal duties",
        ):
            with self.subTest(required=required):
                self.assertIn(required, copy_text)
        for unsupported in (
            "end-to-end encrypted",
            "all users create mature",
            "woman means",
            "man means",
            "gender means",
            "over 18",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertNotIn(unsupported, copy_text)

    def test_notice_and_binding_are_detached_server_owned_values(self):
        notice = responsible_use_notice()
        notice["paragraphs"].append("client mutation")
        notice["title"] = "client mutation"
        fresh = responsible_use_notice()
        self.assertNotIn("client mutation", fresh["paragraphs"])
        self.assertEqual(fresh["title"], "Responsible use")
        self.assertEqual(responsible_use_binding(), {
            "document_id": RESPONSIBLE_USE_DOCUMENT_ID,
            "document_version": CURRENT_RESPONSIBLE_USE_VERSION,
            "content_sha256": CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
        })

    def test_unknown_and_boolean_notice_versions_are_rejected(self):
        for version in (0, 2, True, "1"):
            with self.subTest(version=version):
                with self.assertRaises(UnknownResponsibleUseVersionError):
                    responsible_use_notice(version)  # type: ignore[arg-type]


class ResponsibleUseAcceptanceTests(unittest.TestCase):
    def _record(self) -> dict[str, object]:
        return create_acceptance_record(
            CURRENT_RESPONSIBLE_USE_VERSION,
            CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=ACCEPTED_AT,
        )

    def test_record_binds_exact_notice_and_contains_no_principal_or_content(self):
        record = self._record()
        self.assertEqual(set(record), {
            "schema_version",
            "document_id",
            "document_version",
            "content_sha256",
            "accepted_at",
            "record_sha256",
        })
        self.assertEqual(
            record["schema_version"],
            RESPONSIBLE_USE_ACCEPTANCE_SCHEMA_VERSION,
        )
        self.assertEqual(record["document_id"], RESPONSIBLE_USE_DOCUMENT_ID)
        self.assertEqual(
            record["content_sha256"],
            CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
        )
        self.assertEqual(record["accepted_at"], "2026-08-11T07:08:09.123456Z")
        serialized = json.dumps(record, sort_keys=True).lower()
        for private_field in (
            "account",
            "email",
            "identity",
            "session",
            "project",
            "prompt",
            "media",
            "output",
            "password",
            "token",
            "/home/",
            "/media/",
        ):
            with self.subTest(private_field=private_field):
                self.assertNotIn(private_field, serialized)

    def test_exact_current_version_and_digest_are_required(self):
        invalid_bindings = (
            (0, CURRENT_RESPONSIBLE_USE_CONTENT_SHA256),
            (2, CURRENT_RESPONSIBLE_USE_CONTENT_SHA256),
            (True, CURRENT_RESPONSIBLE_USE_CONTENT_SHA256),
            ("1", CURRENT_RESPONSIBLE_USE_CONTENT_SHA256),
            (1, "0" * 64),
            (1, CURRENT_RESPONSIBLE_USE_CONTENT_SHA256.upper()),
            (1, None),
        )
        for version, digest in invalid_bindings:
            with self.subTest(version=version, digest=digest):
                with self.assertRaises(StaleResponsibleUseNoticeError):
                    create_acceptance_record(
                        version,
                        digest,
                        now=ACCEPTED_AT,
                    )

    def test_acceptance_is_idempotent_and_keeps_the_first_time(self):
        first = self._record()
        second = accept_responsible_use(
            first,
            CURRENT_RESPONSIBLE_USE_VERSION,
            CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=ACCEPTED_AT + timedelta(days=1),
        )
        self.assertEqual(second, first)
        self.assertIsNot(second, first)
        self.assertEqual(second["accepted_at"], "2026-08-11T07:08:09.123456Z")

    def test_server_time_is_canonical_utc_and_naive_time_is_rejected(self):
        offset_time = datetime(
            2026,
            8,
            11,
            1,
            8,
            9,
            123456,
            tzinfo=timezone(timedelta(hours=-6)),
        )
        record = create_acceptance_record(
            1,
            CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
            now=offset_time,
        )
        self.assertEqual(record["accepted_at"], "2026-08-11T07:08:09.123456Z")
        with self.assertRaises(InvalidResponsibleUseAcceptanceError):
            create_acceptance_record(
                1,
                CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
                now=datetime(2026, 8, 11),
            )

    def test_schema_violations_and_unrecomputed_mutations_fail_closed(self):
        record = self._record()
        mutations = {
            "schema": {**record, "schema_version": 2},
            "boolean_schema": {**record, "schema_version": True},
            "document": {**record, "document_id": "other"},
            "version": {**record, "document_version": 2},
            "content": {**record, "content_sha256": "0" * 64},
            "time": {**record, "accepted_at": "2026-08-11T07:08:10.123456Z"},
            "checksum": {**record, "record_sha256": "0" * 64},
            "extra_identity": {**record, "account_id": "someone"},
        }
        for name, invalid in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(InvalidResponsibleUseAcceptanceError):
                    normalize_acceptance_record(invalid)
                self.assertEqual(
                    responsible_use_status(invalid)["state"],
                    "invalid",
                )
                with self.assertRaises(InvalidResponsibleUseAcceptanceError):
                    accept_responsible_use(
                        invalid,
                        1,
                        CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
                        now=ACCEPTED_AT,
                    )

    def test_recomputed_public_checksum_is_not_authenticity_evidence(self):
        """External keyed storage must protect record plus principal binding."""

        original = self._record()
        forged = {
            **original,
            "accepted_at": "2026-08-12T07:08:09.123456Z",
        }
        unsigned = {
            key: forged[key]
            for key in forged
            if key != "record_sha256"
        }
        forged["record_sha256"] = hashlib.sha256(json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()

        # The schema codec can prove canonical consistency, not who accepted.
        self.assertEqual(normalize_acceptance_record(forged), forged)
        self.assertTrue(responsible_use_status(forged)["accepted"])
        self.assertEqual(
            accept_responsible_use(
                forged,
                1,
                CURRENT_RESPONSIBLE_USE_CONTENT_SHA256,
                now=ACCEPTED_AT + timedelta(days=2),
            ),
            forged,
        )
        self.assertNotEqual(forged["accepted_at"], original["accepted_at"])
        self.assertNotEqual(forged["record_sha256"], original["record_sha256"])

    def test_stored_timestamp_must_be_exact_canonical_utc(self):
        record = self._record()
        for value in (
            "2026-08-11T07:08:09Z",
            "2026-08-11T01:08:09.123456-06:00",
            "2026-08-11 07:08:09.123456Z",
            "not-a-time",
            None,
        ):
            with self.subTest(value=value):
                changed = {**record, "accepted_at": value}
                unsigned = {
                    key: changed[key]
                    for key in changed
                    if key != "record_sha256"
                }
                changed["record_sha256"] = hashlib.sha256(json.dumps(
                    unsigned,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")).hexdigest()
                with self.assertRaises(InvalidResponsibleUseAcceptanceError):
                    normalize_acceptance_record(changed)

    def test_status_distinguishes_absent_accepted_and_invalid(self):
        absent = responsible_use_status(None)
        self.assertEqual(absent["state"], "not_accepted")
        self.assertFalse(absent["accepted"])
        self.assertIsNone(absent["accepted_at"])

        accepted = responsible_use_status(self._record())
        self.assertEqual(accepted["state"], "accepted")
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["accepted_at"], "2026-08-11T07:08:09.123456Z")

        invalid = responsible_use_status({})
        self.assertEqual(invalid["state"], "invalid")
        self.assertFalse(invalid["accepted"])

    def test_restart_round_trip_is_bounded_deterministic_and_detached(self):
        record = self._record()
        encoded = serialize_acceptance_record(record)
        self.assertLess(len(encoded.encode("utf-8")), MAX_ACCEPTANCE_RECORD_BYTES)
        self.assertEqual(encoded, serialize_acceptance_record(record))
        restored_text = deserialize_acceptance_record(encoded)
        restored_bytes = deserialize_acceptance_record(encoded.encode("utf-8"))
        self.assertEqual(restored_text, record)
        self.assertEqual(restored_bytes, record)
        self.assertIsNot(restored_text, record)

        changed = copy.deepcopy(restored_text)
        changed["accepted_at"] = "client mutation"
        self.assertNotEqual(changed, record)
        self.assertEqual(deserialize_acceptance_record(encoded), record)

    def test_restart_loader_rejects_malformed_duplicate_and_oversize_json(self):
        record = self._record()
        encoded = serialize_acceptance_record(record)
        duplicate = encoded.replace(
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
        )
        for payload in (
            "",
            "[]",
            "{",
            duplicate,
            b"\xff",
            "x" * (MAX_ACCEPTANCE_RECORD_BYTES + 1),
            None,
        ):
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(InvalidResponsibleUseAcceptanceError):
                    deserialize_acceptance_record(payload)


if __name__ == "__main__":
    unittest.main()
