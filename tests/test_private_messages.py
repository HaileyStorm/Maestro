"""Durability, authorization, idempotency, and privacy tests for message threads."""

from __future__ import annotations

import inspect
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.private_messages import (
    AttachmentDescriptor,
    GrantDescriptor,
    PrivateMessageAuthorizationError,
    PrivateMessageCapacityError,
    PrivateMessageConflictError,
    PrivateMessageCorruptionError,
    PrivateMessageError,
    PrivateMessageOwnerUnavailableError,
    PrivateMessageStore,
    PrivateMessageValidationError,
)

SECRET = b"private-message-test-secret-at-least-32-bytes"
OWNER = "a" * 32
CREATOR = "b" * 32
OTHER = "c" * 32
PRIVACY_CANARY = "PRIVATE-MESSAGE-CANARY-7c609bc1"


class Clock:
    def __init__(self, value: float = 1_000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


def _process_create(path: str, barrier, results) -> None:
    store = PrivateMessageStore(
        path,
        integrity_key=SECRET,
        owner_account_id=OWNER,
        allow_test_path=True,
    )
    barrier.wait()
    try:
        receipt = store.create_thread(
            actor_account_id=CREATOR,
            request_id="parallel-create-0001",
            subject="parallel subject",
            body="parallel body",
        )
        results.put(("ok", receipt.thread_id, receipt.revision))
    except PrivateMessageError as error:  # pragma: no cover - parent asserts type
        results.put((type(error).__name__, "", 0))


class PrivateMessageStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "private" / "threads.json"
        self.clock = Clock()
        self.store = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, request_id: str = "create-request-0001", **updates):
        values = {
            "actor_account_id": CREATOR,
            "request_id": request_id,
            "subject": "A private subject",
            "body": "A private body",
        }
        values.update(updates)
        return self.store.create_thread(**values)

    def test_create_restart_replay_is_one_thread_and_identical_result(self):
        first = self.create()
        restarted = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
        )
        replay = restarted.create_thread(
            actor_account_id=CREATOR,
            request_id="create-request-0001",
            subject="A private subject",
            body="A private body",
        )
        self.assertEqual(first, replay)
        self.assertEqual(len(restarted.list_cards(actor_account_id=OWNER)), 1)
        detail = restarted.thread_detail(
            actor_account_id=CREATOR, thread_id=first.thread_id,
        )
        self.assertEqual(detail.subject, "A private subject")
        self.assertEqual([message.body for message in detail.messages], ["A private body"])

    def test_request_rebinding_conflicts_before_revision_or_capacity(self):
        first = self.create()
        before = self.path.read_bytes()
        with self.assertRaises(PrivateMessageConflictError):
            self.create(body="changed private body")
        self.assertEqual(self.path.read_bytes(), before)

        reply = self.store.reply(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="reply-request-0001",
            expected_revision=1,
            body="owner reply",
        )
        self.assertEqual(reply.revision, 2)
        with self.assertRaises(PrivateMessageConflictError) as raised:
            self.store.reply(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="reply-request-0001",
                expected_revision=1,
                body="different reply",
            )
        self.assertEqual(raised.exception.code, "request_conflict")

    def test_exact_replay_is_free_at_mutation_quota(self):
        store = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
            max_mutations=1,
            max_mutations_per_actor=1,
        )
        first = store.create_thread(
            actor_account_id=CREATOR,
            request_id="quota-replay-0001",
            subject="subject",
            body="body",
        )
        replay = store.create_thread(
            actor_account_id=CREATOR,
            request_id="quota-replay-0001",
            subject="subject",
            body="body",
        )
        self.assertEqual(first, replay)
        with self.assertRaises(PrivateMessageCapacityError):
            store.mark_read(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="quota-read-00001",
                expected_revision=1,
            )

    def test_every_existing_thread_mutation_replays_without_new_events(self):
        first = self.create()
        operations = [
            lambda: self.store.reply(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="replay-reply-0001",
                expected_revision=1,
                body="reply",
            ),
            lambda: self.store.mark_read(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="replay-read-00001",
                expected_revision=2,
            ),
            lambda: self.store.set_archived(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="replay-archive-01",
                expected_revision=3,
                archived=True,
            ),
            lambda: self.store.set_muted(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="replay-mute-0001",
                expected_revision=4,
                muted=True,
            ),
            lambda: self.store.update_stage(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="replay-stage-0001",
                expected_revision=5,
                stage="failed",
                triage_state="failed",
                reason="model_unavailable",
            ),
            lambda: self.store.retry(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="replay-retry-0001",
                expected_revision=6,
            ),
            lambda: self.store.cancel(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="replay-cancel-001",
                expected_revision=7,
            ),
        ]
        for operation in operations:
            receipt = operation()
            self.assertEqual(operation(), receipt)
        detail = self.store.thread_detail(
            actor_account_id=OWNER, thread_id=first.thread_id,
        )
        self.assertEqual(detail.card.revision, 8)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["mutations"]), 8)
        self.assertEqual(len(payload["threads"][0]["events"]), 8)

    def test_two_processes_serialize_same_create_without_duplicates(self):
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [
            context.Process(
                target=_process_create,
                args=(str(self.path), barrier, results),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            self.assertEqual(process.exitcode, 0)
        observed = [results.get(timeout=2) for _ in processes]
        self.assertEqual({item[0] for item in observed}, {"ok"})
        self.assertEqual(len({item[1] for item in observed}), 1)
        self.assertEqual(len(self.store.list_cards(actor_account_id=OWNER)), 1)

    def test_two_instances_serialize_distinct_writers_without_lost_updates(self):
        other = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
        )
        errors: list[BaseException] = []

        def create(store, request_id, subject):
            try:
                store.create_thread(
                    actor_account_id=CREATOR,
                    request_id=request_id,
                    subject=subject,
                    body="body",
                )
            except PrivateMessageError as error:
                errors.append(error)

        threads = [
            threading.Thread(
                target=create, args=(self.store, "thread-write-0001", "one"),
            ),
            threading.Thread(
                target=create, args=(other, "thread-write-0002", "two"),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(errors)
        self.assertEqual(len(self.store.list_cards(actor_account_id=OWNER)), 2)

    def test_cross_account_isolation_and_owner_visibility(self):
        first = self.create()
        second = self.create(
            "other-create-0001",
            actor_account_id=OTHER,
            subject="other subject",
            body="other body",
        )
        self.assertEqual(len(self.store.list_cards(actor_account_id=OWNER)), 2)
        self.assertEqual(
            [card.thread_id for card in self.store.list_cards(actor_account_id=CREATOR)],
            [first.thread_id],
        )
        self.assertEqual(
            [card.thread_id for card in self.store.list_cards(actor_account_id=OTHER)],
            [second.thread_id],
        )
        with self.assertRaises(PrivateMessageAuthorizationError):
            self.store.thread_detail(
                actor_account_id=OTHER, thread_id=first.thread_id,
            )
        with self.assertRaises(PrivateMessageAuthorizationError):
            self.store.reply(
                actor_account_id=OTHER,
                thread_id=first.thread_id,
                request_id="cross-reply-0001",
                expected_revision=1,
                body="not allowed",
            )

    def test_disabled_owner_blocks_owner_access_and_new_threads_only(self):
        first = self.create()
        disabled = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            owner_enabled=False,
            allow_test_path=True,
            clock=self.clock,
        )
        self.assertEqual(
            disabled.thread_detail(
                actor_account_id=CREATOR, thread_id=first.thread_id,
            ).subject,
            "A private subject",
        )
        with self.assertRaises(PrivateMessageOwnerUnavailableError):
            disabled.list_cards(actor_account_id=OWNER)
        with self.assertRaises(PrivateMessageOwnerUnavailableError):
            disabled.create_thread(
                actor_account_id=OTHER,
                request_id="disabled-create-01",
                subject="subject",
                body="body",
            )

    def test_different_owner_binding_cannot_take_over_store(self):
        self.create()
        wrong = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id="d" * 32,
            allow_test_path=True,
        )
        with self.assertRaises(PrivateMessageCorruptionError):
            wrong.list_cards(actor_account_id="d" * 32)

    def test_unread_is_participant_scoped_and_deterministically_bumps_cards(self):
        first = self.create("unread-create-0001", subject="one")
        self.clock.value += 1
        second = self.create("unread-create-0002", subject="two")
        owner_cards = self.store.list_cards(actor_account_id=OWNER)
        self.assertEqual([card.thread_id for card in owner_cards], [
            second.thread_id, first.thread_id,
        ])
        self.assertEqual([card.unread_count for card in owner_cards], [1, 1])

        read = self.store.mark_read(
            actor_account_id=OWNER,
            thread_id=second.thread_id,
            request_id="owner-read-000001",
            expected_revision=1,
        )
        self.assertEqual(read.revision, 2)
        owner_cards = self.store.list_cards(actor_account_id=OWNER)
        self.assertEqual(owner_cards[0].thread_id, first.thread_id)
        self.assertEqual(owner_cards[-1].unread_count, 0)

        reply = self.store.reply(
            actor_account_id=OWNER,
            thread_id=second.thread_id,
            request_id="owner-reply-00001",
            expected_revision=2,
            body="owner response",
        )
        creator_cards = self.store.list_cards(actor_account_id=CREATOR)
        self.assertEqual(creator_cards[0].thread_id, second.thread_id)
        self.assertEqual(creator_cards[0].unread_count, 1)
        self.assertEqual(creator_cards[0].unread_bump_order, 4)
        self.assertEqual(reply.revision, 3)

    def test_archive_and_mute_are_participant_scoped_append_only_events(self):
        first = self.create()
        archived = self.store.set_archived(
            actor_account_id=CREATOR,
            thread_id=first.thread_id,
            request_id="archive-request-01",
            expected_revision=1,
            archived=True,
        )
        muted = self.store.set_muted(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="mute-request-0001",
            expected_revision=2,
            muted=True,
        )
        self.assertEqual((archived.revision, muted.revision), (2, 3))
        creator_card = self.store.list_cards(actor_account_id=CREATOR)[0]
        owner_card = self.store.list_cards(actor_account_id=OWNER)[0]
        self.assertTrue(creator_card.archived)
        self.assertFalse(creator_card.muted)
        self.assertFalse(owner_card.archived)
        self.assertTrue(owner_card.muted)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            [event["sequence"] for event in payload["threads"][0]["events"]],
            [1, 2, 3],
        )

    def test_stage_substates_change_independently_without_collapsing(self):
        first = self.create()
        waiting = self.store.update_stage(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="stage-wait-00001",
            expected_revision=1,
            stage="waiting_for_model",
            triage_state="waiting_for_model",
            reason="model_unavailable",
            progress_current=0,
            progress_total=4,
        )
        triaging = self.store.update_stage(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="stage-triage-001",
            expected_revision=2,
            stage="triaging",
            triage_state="triaging",
            reason="none",
            progress_current=1,
            progress_total=4,
        )
        scanning = self.store.update_stage(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="stage-scan-00001",
            expected_revision=3,
            stage="scanning_attachments",
            triage_state="complete",
            scan_state="scanning",
            progress_current=2,
            progress_total=4,
        )
        queued = self.store.update_stage(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="stage-queue-00001",
            expected_revision=4,
            stage="queued_for_delivery",
            scan_state="clean",
            delivery_state="queued",
            progress_current=3,
            progress_total=4,
        )
        delivered = self.store.update_stage(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="stage-deliver-001",
            expected_revision=5,
            stage="delivered",
            delivery_state="delivered",
            reason="none",
            progress_current=4,
            progress_total=4,
        )
        self.assertEqual(
            [item.stage for item in (waiting, triaging, scanning, queued, delivered)],
            [
                "waiting_for_model", "triaging", "scanning_attachments",
                "queued_for_delivery", "delivered",
            ],
        )
        card = self.store.list_cards(actor_account_id=CREATOR)[0]
        self.assertEqual((card.triage_state, card.scan_state, card.delivery_state), (
            "complete", "clean", "delivered",
        ))
        self.assertFalse(card.can_cancel)
        progressed = self.store.update_stage(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="delivered-progress-1",
            expected_revision=6,
            progress_current=4,
            progress_total=5,
        )
        self.assertEqual(progressed.stage, "delivered")
        with self.assertRaises(PrivateMessageConflictError):
            self.store.cancel(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="late-cancel-00001",
                expected_revision=7,
            )

    def test_delivery_readiness_and_cancellation_provenance_fail_closed(self):
        first = self.create()
        before = self.path.read_bytes()
        with self.assertRaises(PrivateMessageConflictError):
            self.store.update_stage(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="premature-queue-01",
                expected_revision=1,
                stage="queued_for_delivery",
                delivery_state="queued",
                progress_current=1,
                progress_total=2,
            )
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(PrivateMessageValidationError):
            self.store.update_stage(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="forged-cancel-0001",
                expected_revision=1,
                stage="cancelled",
                delivery_state="cancelled",
                reason="cancelled_by_creator",
            )
        self.store.cancel(
            actor_account_id=CREATOR,
            thread_id=first.thread_id,
            request_id="valid-cancel-0001",
            expected_revision=1,
        )
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        thread = payload["threads"][0]
        event = thread["events"][-1]
        event["reason"] = "cancelled_by_owner"
        event["event_hmac"] = self.store._event_hmac({
            key: value for key, value in event.items() if key != "event_hmac"
        })
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store._validate_thread(thread)

    def test_cancel_then_retry_resets_revalidation_substates(self):
        first = self.create()
        cancelled = self.store.cancel(
            actor_account_id=CREATOR,
            thread_id=first.thread_id,
            request_id="cancel-request-001",
            expected_revision=1,
        )
        self.assertEqual(cancelled.stage, "cancelled")
        card = self.store.list_cards(actor_account_id=CREATOR)[0]
        self.assertTrue(card.can_retry)
        retried = self.store.retry(
            actor_account_id=CREATOR,
            thread_id=first.thread_id,
            request_id="retry-request-0001",
            expected_revision=2,
        )
        self.assertEqual(retried.stage, "waiting_for_model")
        card = self.store.list_cards(actor_account_id=CREATOR)[0]
        self.assertEqual((card.triage_state, card.scan_state, card.delivery_state), (
            "waiting_for_model", "not_started", "not_started",
        ))

    def test_creator_cannot_publish_processing_transitions(self):
        first = self.create()
        with self.assertRaises(PrivateMessageAuthorizationError):
            self.store.update_stage(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="creator-stage-0001",
                expected_revision=1,
                stage="delivered",
                delivery_state="delivered",
                reason="none",
            )

    def test_attachment_and_grant_descriptors_are_opaque_and_bounded(self):
        attachment = AttachmentDescriptor(
            attachment_id="attachment_001",
            byte_count=1234,
            sha256="1" * 64,
        )
        grant = GrantDescriptor(
            grant_id="grant_001",
            object_type="reference",
            object_id="reference_001",
            revision_id="revision_001",
        )
        first = self.create(attachments=[attachment], grants=[grant])
        card = self.store.list_cards(actor_account_id=OWNER)[0]
        self.assertEqual((card.attachment_count, card.attachment_bytes), (1, 1234))
        detail = self.store.thread_detail(
            actor_account_id=OWNER, thread_id=first.thread_id,
        )
        self.assertEqual(detail.messages[0].attachments, (attachment,))
        self.assertEqual(detail.messages[0].grants, (grant,))
        for invalid in (
            {"attachment_id": "ok_id", "byte_count": 1, "sha256": "2" * 64,
             "path": "/private/path"},
            {"attachment_id": "ok_id", "byte_count": 1, "sha256": "2" * 64,
             "filename": "private.png"},
        ):
            with self.assertRaises(PrivateMessageValidationError):
                self.create("invalid-opaque-01", attachments=[invalid])

    def test_cards_receipts_reprs_and_errors_never_project_private_content(self):
        first = self.create(
            subject=PRIVACY_CANARY,
            body=f"body {PRIVACY_CANARY}",
            attachments=[AttachmentDescriptor(
                "attachment_canary", 7, "3" * 64,
            )],
        )
        card = self.store.list_cards(actor_account_id=OWNER)[0]
        detail = self.store.thread_detail(
            actor_account_id=OWNER, thread_id=first.thread_id,
        )
        self.assertNotIn(PRIVACY_CANARY, repr(first))
        self.assertNotIn(PRIVACY_CANARY, repr(card))
        self.assertNotIn(PRIVACY_CANARY, repr(detail))
        self.assertNotIn(PRIVACY_CANARY, repr(detail.messages[0]))
        descriptor = detail.messages[0].attachments[0]
        self.assertNotIn(descriptor.attachment_id, repr(descriptor))
        self.assertNotIn(descriptor.sha256, repr(descriptor))
        with self.assertRaises(PrivateMessageConflictError) as raised:
            self.create(subject=PRIVACY_CANARY, body="changed")
        self.assertNotIn(PRIVACY_CANARY, repr(raised.exception))
        forbidden = {
            "subject", "body", "recipient", "reply_to", "email", "filename",
            "path", "url", "header", "transcript", "model_label",
        }
        self.assertTrue(forbidden.isdisjoint(card.__dict__))
        invalid_private = f"{PRIVACY_CANARY}\ud800"
        with self.assertRaises(PrivateMessageValidationError) as invalid:
            self.store.reply(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="invalid-unicode-01",
                expected_revision=1,
                body=invalid_private,
            )
        self.assertIsNone(invalid.exception.__cause__)
        self.assertNotIn(PRIVACY_CANARY, repr(invalid.exception))

    def test_store_is_sealed_strict_and_private_but_not_misrepresented_as_encrypted(self):
        self.create(subject=PRIVACY_CANARY, body=f"body {PRIVACY_CANARY}")
        raw = self.path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        self.assertEqual(payload["version"], 1)
        self.assertRegex(payload["seal"], r"^[0-9a-f]{64}$")
        self.assertNotIn(CREATOR, raw)
        self.assertNotIn(OWNER, raw)
        self.assertIn(PRIVACY_CANARY, raw)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(Path(str(self.path) + ".lock").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(str(self.path) + ".anchor").stat().st_mode), 0o600)

    def test_tamper_wrong_key_duplicate_keys_and_malformed_shape_fail_closed(self):
        self.create()
        original = self.path.read_bytes()
        payload = json.loads(original)
        payload["threads"][0]["events"][0]["body"] = "tampered"
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)

        self.path.write_bytes(original)
        os.chmod(self.path, 0o600)
        wrong_key = PrivateMessageStore(
            self.path,
            integrity_key=b"wrong-private-message-secret-32-bytes!",
            owner_account_id=OWNER,
            allow_test_path=True,
        )
        with self.assertRaises(PrivateMessageCorruptionError):
            wrong_key.list_cards(actor_account_id=OWNER)

        self.path.write_text('{"version":1,"version":1}', encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)

    def test_valid_snapshot_rollback_and_store_deletion_fail_closed(self):
        first = self.create()
        old_store = self.path.read_bytes()
        old_anchor = Path(str(self.path) + ".anchor").read_bytes()
        self.store.reply(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="rollback-snapshot-1",
            expected_revision=1,
            body="newer reply",
        )

        self.path.write_bytes(old_store)
        os.chmod(self.path, 0o600)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)

        Path(str(self.path) + ".anchor").write_bytes(old_anchor)
        os.chmod(Path(str(self.path) + ".anchor"), 0o600)
        restarted = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
        )
        with self.assertRaises(PrivateMessageCorruptionError):
            restarted.list_cards(actor_account_id=OWNER)

        self.path.unlink()
        Path(str(self.path) + ".anchor").unlink()
        restarted = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
        )
        with self.assertRaises(PrivateMessageCorruptionError):
            restarted.list_cards(actor_account_id=OWNER)

    def test_damaged_lock_slot_cannot_reset_to_empty_genesis(self):
        path = Path(self.temporary.name) / "damaged-genesis" / "threads.json"
        store = PrivateMessageStore(
            path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
        )
        self.assertEqual(store.list_cards(actor_account_id=OWNER), ())
        Path(str(path) + ".anchor").unlink()
        with Path(str(path) + ".lock").open("r+b") as lock_file:
            lock_file.seek(8_192)
            lock_file.write(b"damaged-nonblank-slot")
            lock_file.flush()
            os.fsync(lock_file.fileno())
        restarted = PrivateMessageStore(
            path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
        )
        with self.assertRaises(PrivateMessageCorruptionError):
            restarted.list_cards(actor_account_id=OWNER)

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_unsafe_modes_symlinks_and_hardlinks_fail_closed_without_tightening(self):
        self.create()
        original = self.path.read_bytes()

        os.chmod(self.path, 0o644)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o644)
        os.chmod(self.path, 0o600)

        os.chmod(self.path.parent, 0o755)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o755)
        os.chmod(self.path.parent, 0o700)

        linked = Path(self.temporary.name) / "linked-store.json"
        os.link(self.path, linked)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)
        linked.unlink()

        self.path.unlink()
        target = Path(self.temporary.name) / "target.json"
        target.write_bytes(original)
        os.chmod(target, 0o600)
        self.path.symlink_to(target)
        with self.assertRaises(PrivateMessageCorruptionError):
            self.store.list_cards(actor_account_id=OWNER)

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_ancestor_symlink_and_temporary_hardlink_publication_fail_closed(self):
        real_parent = Path(self.temporary.name) / "real"
        real_parent.mkdir(mode=0o700)
        alias = Path(self.temporary.name) / "alias"
        alias.symlink_to(real_parent, target_is_directory=True)
        via_alias = PrivateMessageStore(
            alias / "private" / "threads.json",
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
        )
        with self.assertRaises(PrivateMessageCorruptionError):
            via_alias.create_thread(
                actor_account_id=CREATOR,
                request_id="ancestor-link-0001",
                subject="subject",
                body="body",
            )

        first = self.create("temp-link-create-1")
        before = self.path.read_bytes()
        leaked = Path(self.temporary.name) / "temporary-hardlink.json"
        real_replace = os.replace

        def link_before_replace(source, destination):
            if Path(destination) == self.path:
                os.link(source, leaked)
            return real_replace(source, destination)

        with (
            mock.patch("services.private_messages.os.replace", link_before_replace),
            self.assertRaises(PrivateMessageCorruptionError),
        ):
            self.store.reply(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="temp-link-reply-01",
                expected_revision=1,
                body="reply",
            )
        self.assertTrue(leaked.exists())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(leaked.read_bytes(), before)
        leaked.unlink()
        restarted = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
        )
        self.assertEqual(
            restarted.thread_detail(
                actor_account_id=OWNER, thread_id=first.thread_id,
            ).card.revision,
            1,
        )

    def test_atomic_publication_failure_preserves_exact_previous_bytes(self):
        first = self.create()
        before = self.path.read_bytes()
        real_replace = os.replace

        def fail_private_store_replace(source, destination):
            if Path(destination) == self.path:
                raise OSError("synthetic replace failure")
            return real_replace(source, destination)

        with (
            mock.patch(
                "services.private_messages.os.replace", fail_private_store_replace,
            ),
            self.assertRaises(PrivateMessageCorruptionError),
        ):
            self.store.reply(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="replace-fail-00001",
                expected_revision=1,
                body="not published",
            )
        self.assertEqual(self.path.read_bytes(), before)
        restarted = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
        )
        self.assertEqual(
            restarted.thread_detail(
                actor_account_id=OWNER, thread_id=first.thread_id,
            ).card.revision,
            1,
        )

    def test_partial_lock_anchor_write_recovers_committed_idempotent_result(self):
        first = self.create()
        lock_path = Path(str(self.path) + ".lock")
        real_write = os.write
        lock_writes = 0

        def fail_second_lock_slot(descriptor, value):
            nonlocal lock_writes
            try:
                is_lock = os.path.samestat(
                    os.fstat(descriptor), lock_path.lstat(),
                )
            except (FileNotFoundError, OSError):
                is_lock = False
            if is_lock and os.lseek(descriptor, 0, os.SEEK_CUR) >= 1:
                lock_writes += 1
                if lock_writes == 1:
                    return real_write(descriptor, value[: len(value) // 2])
                raise OSError("synthetic partial lock-anchor write")
            return real_write(descriptor, value)

        with (
            mock.patch("services.private_messages.os.write", fail_second_lock_slot),
            self.assertRaises(PrivateMessageCorruptionError),
        ):
            self.store.reply(
                actor_account_id=OWNER,
                thread_id=first.thread_id,
                request_id="partial-lock-reply-1",
                expected_revision=1,
                body="committed before anchor failure",
            )
        restarted = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=self.clock,
        )
        recovered = restarted.thread_detail(
            actor_account_id=OWNER, thread_id=first.thread_id,
        )
        self.assertEqual(recovered.card.revision, 2)
        self.assertEqual(len(recovered.messages), 2)
        before_replay = self.path.read_bytes()
        replay = restarted.reply(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="partial-lock-reply-1",
            expected_revision=1,
            body="committed before anchor failure",
        )
        self.assertEqual(replay.revision, 2)
        self.assertEqual(self.path.read_bytes(), before_replay)
        self.assertEqual(
            restarted.thread_detail(
                actor_account_id=OWNER, thread_id=first.thread_id,
            ).card.revision,
            2,
        )

    def test_clock_rollback_cannot_reverse_timestamps_and_stale_revision_is_rejected(self):
        first = self.create()
        created = self.store.list_cards(actor_account_id=CREATOR)[0].created_at
        self.clock.value = 10.0
        reply = self.store.reply(
            actor_account_id=OWNER,
            thread_id=first.thread_id,
            request_id="rollback-reply-001",
            expected_revision=1,
            body="reply",
        )
        card = self.store.list_cards(actor_account_id=CREATOR)[0]
        self.assertGreaterEqual(card.updated_at, created)
        before = self.path.read_bytes()
        with self.assertRaises(PrivateMessageConflictError) as raised:
            self.store.mark_read(
                actor_account_id=CREATOR,
                thread_id=first.thread_id,
                request_id="stale-read-00001",
                expected_revision=1,
            )
        self.assertEqual(raised.exception.code, "revision_conflict")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(reply.revision, 2)

    def test_huge_json_number_is_a_bounded_corruption_error_without_cause(self):
        self.create()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["clock_high_water"] = 10**1_000
        unsigned = {key: value for key, value in payload.items() if key != "seal"}
        payload["seal"] = self.store._seal(unsigned)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaises(PrivateMessageCorruptionError) as raised:
            self.store.list_cards(actor_account_id=OWNER)
        self.assertIsNone(raised.exception.__cause__)

    def test_integrity_key_surrogate_is_a_bounded_validation_error(self):
        with self.assertRaises(PrivateMessageValidationError) as raised:
            PrivateMessageStore(
                self.path,
                integrity_key="\ud800" * 32,
                owner_account_id=OWNER,
                allow_test_path=True,
            )
        self.assertIsNone(raised.exception.__cause__)

    def test_each_mutation_samples_clock_once_before_atomic_save(self):
        calls = 0

        def stateful_clock():
            nonlocal calls
            calls += 1
            return 1_000.0 if calls == 1 else 10**1_000

        path = Path(self.temporary.name) / "single-clock" / "threads.json"
        store = PrivateMessageStore(
            path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            clock=stateful_clock,
        )
        result = store.create_thread(
            actor_account_id=CREATOR,
            request_id="single-clock-0001",
            subject="subject",
            body="body",
        )
        self.assertEqual(result.revision, 1)
        self.assertEqual(calls, 1)

    def test_thread_message_event_and_store_limits_preserve_prior_state(self):
        thread_limited = PrivateMessageStore(
            self.path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            max_threads=1,
            max_threads_per_creator=1,
        )
        first = thread_limited.create_thread(
            actor_account_id=CREATOR,
            request_id="limit-create-0001",
            subject="subject",
            body="body",
        )
        before = self.path.read_bytes()
        with self.assertRaises(PrivateMessageCapacityError):
            thread_limited.create_thread(
                actor_account_id=CREATOR,
                request_id="limit-create-0002",
                subject="subject",
                body="body",
            )
        self.assertEqual(self.path.read_bytes(), before)

        event_path = Path(self.temporary.name) / "events" / "threads.json"
        event_limited = PrivateMessageStore(
            event_path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            max_events_per_thread=1,
        )
        event_first = event_limited.create_thread(
            actor_account_id=CREATOR,
            request_id="event-create-0001",
            subject="subject",
            body="body",
        )
        before_event = event_path.read_bytes()
        with self.assertRaises(PrivateMessageCapacityError):
            event_limited.reply(
                actor_account_id=OWNER,
                thread_id=event_first.thread_id,
                request_id="event-reply-00001",
                expected_revision=1,
                body="reply",
            )
        self.assertEqual(event_path.read_bytes(), before_event)
        self.assertEqual(first.revision, 1)

    def test_message_and_store_byte_limits_fail_without_partial_publication(self):
        with self.assertRaises(PrivateMessageValidationError):
            self.create(
                "long-subject-0001",
                subject="x" * 513,
            )
        with self.assertRaises(PrivateMessageValidationError):
            self.create(
                "long-body-000001",
                body="x" * (128 * 1024 + 1),
            )

        small_path = Path(self.temporary.name) / "small" / "threads.json"
        small = PrivateMessageStore(
            small_path,
            integrity_key=SECRET,
            owner_account_id=OWNER,
            allow_test_path=True,
            max_store_bytes=1_024,
        )
        with self.assertRaises(PrivateMessageCapacityError):
            small.create_thread(
                actor_account_id=CREATOR,
                request_id="small-store-00001",
                subject="subject",
                body="x" * 900,
            )
        self.assertFalse(small_path.exists())

    def test_owner_processing_reason_codes_are_content_neutral_closed_enums(self):
        source = inspect.getsource(sys.modules["services.private_messages"])
        for forbidden in (
            "moderation", "sexual", "explicit", "violent", "profanity",
            "underage", "prompt_scan", "recipient_email", "reply_to",
        ):
            self.assertNotIn(forbidden, source.lower())
        signature = inspect.signature(PrivateMessageStore.create_thread)
        self.assertNotIn("email", signature.parameters)
        self.assertNotIn("transport", signature.parameters)
        self.assertNotIn("model", signature.parameters)


if __name__ == "__main__":
    unittest.main()
