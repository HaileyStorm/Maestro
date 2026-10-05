"""CPU lifecycle proof; no installed coordinator CLI or GPU workload is invoked."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
from services import h3_prompt_rewriter_gpu_lease as gpu


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class Clock:
    def __init__(self):
        self.tick, self.now = 0.0, 1800000000.0

    def advance(self, seconds):
        self.tick += seconds
        self.now += seconds


class Client:
    def __init__(self, binding, clock):
        self.binding, self.clock = binding, clock
        self.requests, self.withdrawals, self.minimums = [], [], []
        self.polls = [{"request_id": "request-1", "type": "granted"}]
        self.request_error = self.withdraw_error = self.proof_error = None
        self.confirmed = True
        self.owned = True
        self.poll_count = 0
        self.receipt = {"schema": "gpu-coord/client-authority-snapshot/v1", "authorized": True,
            "request_id": "request-1", "lease_id": "lease-1", "project_id": binding.project_id,
            "agent_id": "agent-1", "workspace": str(binding.workspace), "authority_epoch": "epoch-1",
            "decision_generation": 1, "start": iso(clock.now - 10), "end": iso(clock.now + 1800)}

    def validate_binding(self):
        pass

    def request(self, intent):
        self.requests.append(intent)
        if self.request_error:
            raise self.request_error

    def poll(self, request_id):
        self.poll_count += 1
        return self.polls.pop(0) if self.polls else None

    def owns_request(self, intent, *, deadline):
        return self.owned

    def events(self, request_id):
        client = self
        class Events:
            event_driven = True
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def wait(self, seconds):
                client.clock.advance(seconds)
                return bool(client.polls)
        return Events()

    def authority(self, intent, minimum):
        self.minimums.append(minimum)
        return dict(self.receipt)

    def withdraw(self, request_id, *, deadline):
        self.withdrawals.append(request_id)
        if self.withdraw_error:
            raise self.withdraw_error

    def no_authority(self, request_id, *, deadline):
        if self.proof_error:
            raise self.proof_error
        return self.confirmed


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.binding = gpu.H3GpuLeaseBinding(root, "maestro-local", root)
        self.clock = Clock()
        self.client = Client(self.binding, self.clock)
        self.saved = []

    def persist(self, value):
        self.saved.append(value)
        return True

    def make(self, **options):
        return gpu.H3PromptRewriterGpuLease(self.binding, request_id="request-1", agent_id="agent-1",
            persist_intent=options.pop("persist_intent", self.persist), _client=self.client,
            _monotonic=lambda: self.clock.tick, _wall=lambda: self.clock.now,
            _sleep=self.clock.advance, **options)

    def test_import_has_no_coordinator_or_process_contact(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("CLI contact")), \
                patch.object(Path, "lstat", side_effect=AssertionError("coordinator read")):
            spec = importlib.util.spec_from_file_location("h3_gpu_import_test", Path(gpu.__file__))
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {spec.name: module}):
                spec.loader.exec_module(module)

    def test_durable_intent_precedes_publication_and_failure_prevents_request(self):
        lease = self.make(persist_intent=lambda value: False)
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.acquire()
        self.assertEqual(self.client.requests, [])
        lease = self.make()
        self.client.request = lambda value: self.assertEqual(self.saved[-1], value)
        lease.acquire()
        self.assertEqual(self.saved[0]["binding_sha256"], self.binding.commitment())
        self.assertNotIn(str(self.binding.workspace), json.dumps(self.saved[0]))
        self.assertEqual(self.saved[0]["project_id"], self.binding.project_id)

    def test_cancel_while_queued_and_cleanup_after_no_child(self):
        self.client.polls = [{"request_id": "request-1", "type": "ack"}]
        lease = self.make()
        with self.assertRaises(gpu.H3GpuLeaseCancelled):
            lease.acquire(cancel_check=lambda: self.clock.tick >= 0.5)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.minimums, [])
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.acquire()
        self.assertTrue(lease.close(child_reaped=True))
        self.assertEqual(self.client.withdrawals, ["request-1"])

    def test_ack_never_authorizes_and_wait_retry_never_republishes(self):
        self.client.polls = [{"request_id": "request-1", "type": "ack"}]
        lease = self.make()
        for _ in range(2):
            with self.assertRaises(gpu.H3GpuLeaseError):
                lease.acquire(wait_seconds=0.5)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.minimums, [])
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.before_start()

    def test_ambiguous_request_is_cleanup_only_and_withdraws_once(self):
        self.client.request_error = TimeoutError("published then timed out")
        lease = self.make()
        with self.assertRaises(gpu.H3GpuLeasePublicationUncertain):
            lease.acquire()
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.acquire()
        self.assertEqual(len(self.client.requests), 1)
        lease.close(child_reaped=True)
        lease.close(child_reaped=True)
        self.assertEqual(self.client.withdrawals, ["request-1"])

    def test_collision_and_ambiguous_foreign_request_never_withdraw(self):
        self.client.request_error = gpu.H3GpuLeaseNotPublished("atomic publication collision")
        lease = self.make()
        with self.assertRaises(gpu.H3GpuLeaseNotPublished):
            lease.acquire()
        self.assertEqual(self.saved[-1]["state"], "unpublished")
        self.assertTrue(lease.close(child_reaped=True))
        restored = gpu.H3PromptRewriterGpuLease.restore_from_intent(self.binding, self.saved[-1],
            persist_intent=self.persist, _client=self.client)
        self.assertTrue(restored.close(child_reaped=True))
        self.client.request_error = TimeoutError("ambiguous publication")
        self.client.owned = False
        lease = self.make()
        with self.assertRaises(gpu.H3GpuLeasePublicationUncertain):
            lease.acquire()
        with self.assertRaises(gpu.H3GpuLeaseWithdrawalUnconfirmed):
            lease.close(child_reaped=True)
        restored = gpu.H3PromptRewriterGpuLease.restore_from_intent(self.binding, self.saved[-1],
            persist_intent=self.persist, _client=self.client)
        with self.assertRaises(gpu.H3GpuLeaseWithdrawalUnconfirmed):
            restored.close(child_reaped=True)
        self.assertEqual(self.client.withdrawals, [])

    def test_idle_wait_uses_events_and_bounded_refresh_then_grant_wakes(self):
        lease = self.make()
        self.client.polls = [{"request_id": "request-1", "type": "ack"}]
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.acquire(wait_seconds=120)
        self.assertEqual(self.client.poll_count, 2)
        self.clock = Clock()
        self.client = Client(self.binding, self.clock)
        self.client.polls.insert(0, {"request_id": "request-1", "type": "ack"})
        self.make().acquire()
        self.assertEqual(self.clock.tick, 0.25)

    def test_confirmation_finishing_after_deadline_never_closes(self):
        lease = self.make().acquire()
        def late_confirmation(request_id, *, deadline):
            self.clock.advance(2)
            return True
        self.client.no_authority = late_confirmation
        with self.assertRaises(gpu.H3GpuLeaseWithdrawalUnconfirmed):
            lease.close(child_reaped=True, confirm_seconds=1)
        self.assertEqual(self.saved[-1]["state"], "withdraw_attempted")
        self.assertFalse(lease.closed)

    def test_reading_intent_cannot_redirect_cleanup_to_another_request(self):
        lease = self.make().acquire()
        lease.intent["request_id"] = "foreign"
        lease.close(child_reaped=True)
        self.assertEqual(self.client.withdrawals, ["request-1"])

    def test_guard_checks_at_two_seconds_and_immediately_before_start(self):
        lease = self.make(planned_seconds=800, stop_margin_seconds=150).acquire()
        self.assertEqual(self.client.minimums, [950])
        self.clock.advance(1.99)
        self.assertTrue(lease())
        self.assertEqual(self.client.minimums, [950])
        self.clock.advance(0.01)
        self.assertTrue(lease())
        lease.before_start()
        self.assertEqual(self.client.minimums, [950, 150, 950])

    def test_identity_changes_and_authority_failure_are_sticky(self):
        for field, changed in {"request_id": "foreign", "agent_id": "foreign", "workspace": "/foreign",
                "lease_id": "lease-2", "authority_epoch": "epoch-2", "decision_generation": 2,
                "start": iso(self.clock.now - 5), "end": iso(self.clock.now + 2000), "authorized": False}.items():
            with self.subTest(field=field):
                self.client = Client(self.binding, self.clock)
                lease = self.make().acquire()
                self.client.receipt[field] = changed
                with self.assertRaises(gpu.H3GpuLeaseError):
                    lease.before_start()
                self.client.receipt[field] = "restored"
                with self.assertRaises(gpu.H3GpuLeaseError):
                    lease()

    def test_expiry_and_clock_rollback_fail_even_between_refreshes(self):
        for wall in (self.clock.now + 1800, self.clock.now - 11):
            with self.subTest(wall=wall):
                self.clock = Clock()
                self.client = Client(self.binding, self.clock)
                lease = self.make().acquire()
                self.clock.now = wall
                with self.assertRaises(gpu.H3GpuLeaseError):
                    lease()

    def test_close_requires_reap_and_durable_intent_before_withdraw(self):
        lease = self.make().acquire()
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.close(child_reaped=False)
        lease.persist_intent = lambda value: False
        with self.assertRaises(gpu.H3GpuLeaseError):
            lease.close(child_reaped=True)
        self.assertEqual(self.client.withdrawals, [])

    def test_unconfirmed_or_ambiguous_withdrawal_is_never_resent(self):
        lease = self.make().acquire()
        self.client.withdraw_error = TimeoutError("uncertain publication")
        self.client.proof_error = ValueError("status failure is not proof")
        with self.assertRaises(gpu.H3GpuLeaseWithdrawalUnconfirmed):
            lease.close(child_reaped=True, confirm_seconds=0.5)
        self.assertEqual(self.saved[-1]["state"], "withdraw_attempted")
        self.client.proof_error = None
        self.assertTrue(lease.close(child_reaped=True))
        self.assertEqual(self.client.withdrawals, ["request-1"])

    def test_startup_restore_is_cleanup_only_and_preserves_publication_state(self):
        lease = self.make().acquire()
        self.client.confirmed = False
        with self.assertRaises(gpu.H3GpuLeaseWithdrawalUnconfirmed):
            lease.close(child_reaped=True, confirm_seconds=0.25)
        saved = self.saved[-1]
        restored = gpu.H3PromptRewriterGpuLease.restore_from_intent(self.binding, saved,
            persist_intent=self.persist, _client=self.client, _monotonic=lambda: self.clock.tick,
            _wall=lambda: self.clock.now, _sleep=self.clock.advance)
        with self.assertRaises(gpu.H3GpuLeaseError):
            restored.acquire()
        with self.assertRaises(gpu.H3GpuLeaseError):
            restored.before_start()
        self.client.confirmed = True
        restored.close(child_reaped=True)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.withdrawals, ["request-1"])
        for key in ("binding_sha256", "project_id"):
            corrupted = {**saved, key: "foreign"}
            with self.assertRaises(gpu.H3GpuLeaseError):
                gpu.H3PromptRewriterGpuLease.restore_from_intent(self.binding, corrupted, persist_intent=self.persist)
        other_root = self.binding.coordinator_root / "other-root"
        other_root.mkdir()
        drifted = gpu.H3GpuLeaseBinding(other_root, self.binding.project_id, self.binding.workspace)
        with self.assertRaises(gpu.H3GpuLeaseError):
            gpu.H3PromptRewriterGpuLease.restore_from_intent(drifted, saved,
                persist_intent=self.persist, _client=self.client)
        self.assertEqual(self.client.withdrawals, ["request-1"])

    def test_platform_is_actionable_unavailable(self):
        with patch.object(sys, "platform", "win32"):
            with self.assertRaises(gpu.H3GpuLeaseUnavailable):
                self.make()


class WithdrawalProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        (root / "schedule").mkdir()
        self.path = root / "schedule/leases.json"
        self.client = gpu._InstalledClient(gpu.H3GpuLeaseBinding(root, "maestro-local", root))
        self.now = 1800000000.0
        self.ledger = {"schema": "gpu-coord/leases/v1", "authority_epoch": "epoch-1",
            "updated_at": iso(self.now), "leases": [{"request_id": "request-1", "status": "cancelled"}]}
        self.status = {"live": True, "authority": {"live": True, "epoch": "epoch-1", "error": None},
            "coordinator": {"schema": "gpu-coord/coordinator-state/v1", "updated_at": iso(self.now),
                "leader": True, "leader_pid": 123, "telemetry": {"healthy": True}}}
        self.denied = {"request_id": "request-1", "type": "denied", "lease_id": None}
        self.client.poll = lambda request: dict(self.denied)
        self.client._command = lambda *args, **kwargs: copy.deepcopy(self.status)

    def proof(self):
        self.path.write_text(json.dumps(self.ledger))
        with patch.object(gpu.time, "time", return_value=self.now):
            return self.client.no_authority("request-1", deadline=gpu.time.monotonic() + 10)

    def test_fresh_stable_denial_and_negative_ledger_prove_withdrawal(self):
        self.assertTrue(self.proof())
        self.ledger["leases"] = []
        self.assertTrue(self.proof())

    def test_stale_error_epoch_and_active_duplicate_never_prove_absence(self):
        cases = [("stale", lambda: self.status["coordinator"].update(updated_at=iso(self.now - 16))),
            ("error", lambda: self.status["coordinator"].update(lease_ledger_error="broken")),
            ("epoch", lambda: self.ledger.update(authority_epoch="foreign")),
            ("duplicate", lambda: self.ledger["leases"].append({"request_id": "request-1", "status": "active"})),
            ("grant", lambda: self.denied.update(type="granted", lease_id="lease-1")),
            ("ambiguous denial", lambda: self.denied.pop("lease_id"))]
        initial = copy.deepcopy((self.ledger, self.status, self.denied))
        for label, change in cases:
            with self.subTest(label=label):
                self.ledger, self.status, self.denied = copy.deepcopy(initial)
                change()
                self.assertFalse(self.proof())

    def test_ledger_replacement_or_denial_change_during_confirmation_rejects(self):
        def command(*args, **kwargs):
            replacement = self.path.with_suffix(".new")
            replacement.write_text(json.dumps(self.ledger))
            replacement.replace(self.path)
            return self.status
        self.client._command = command
        self.assertFalse(self.proof())
        self.client._command = lambda *args, **kwargs: self.status
        responses = iter([self.denied, {**self.denied, "updated_at": "changed"}])
        self.client.poll = lambda request: next(responses)
        self.assertFalse(self.proof())

    def test_negative_proof_rechecks_freshness_after_final_observation(self):
        self.path.write_text(json.dumps(self.ledger))
        with patch.object(gpu.time, "time", side_effect=[self.now, self.now + 16]):
            self.assertFalse(self.client.no_authority("request-1", deadline=gpu.time.monotonic() + 10))

    def test_remaining_deadline_bounds_cli_and_rejects_late_observation(self):
        client = gpu._InstalledClient(self.client.binding)
        result = subprocess.CompletedProcess([], 0, stdout=b"{}", stderr=b"")
        with patch.object(gpu.time, "monotonic", return_value=100), \
                patch.object(client, "_load"), \
                patch.object(subprocess, "run", return_value=result) as run:
            client._command(["status"], read=True, deadline=100.4)
        self.assertAlmostEqual(run.call_args.kwargs["timeout"], 0.4)
        self.path.write_text(json.dumps(self.ledger))
        clock = Clock()
        def slow_status(*args, **kwargs):
            clock.advance(11)
            return self.status
        self.client._command = slow_status
        with patch.object(gpu.time, "monotonic", side_effect=lambda: clock.tick), \
                patch.object(gpu.time, "time", return_value=self.now):
            with self.assertRaises(gpu.H3GpuLeaseWithdrawalUnconfirmed):
                self.client.no_authority("request-1", deadline=10)

    def test_malformed_duplicate_ledger_and_symlink_are_not_negative_proof(self):
        self.path.write_text('{"schema":"gpu-coord/leases/v1","leases":[],"leases":[]}')
        with self.assertRaises(gpu.H3GpuLeaseUnavailable):
            self.client.no_authority("request-1", deadline=gpu.time.monotonic() + 10)
        target = self.path.with_suffix(".target")
        self.path.replace(target)
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.client.no_authority("request-1", deadline=gpu.time.monotonic() + 10)

    def test_supported_cli_transport_uses_server_binding_and_exact_one_reply(self):
        result = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        intent = {"request_id": "request-1", "project_id": self.client.binding.project_id,
            "agent_id": "agent-1", "minimum_remaining_seconds": 750}
        client = gpu._InstalledClient(self.client.binding)
        with patch.object(client, "_load"), patch.object(subprocess, "run", return_value=result) as run:
            client.request(intent)
            client.withdraw("request-1", deadline=gpu.time.monotonic() + 10)
        calls = run.call_args_list
        self.assertEqual(len(calls), 2)
        argv = calls[0].args[0]
        self.assertEqual(argv[:3], [sys.executable, "-I",
            str(self.client.binding.coordinator_root / "scripts/gpu_coord_cli.py")])
        self.assertEqual(argv[argv.index("--callback-workspace") + 1], str(self.client.binding.workspace))
        self.assertEqual(argv[argv.index("--minutes") + 1], "13")
        self.assertEqual(calls[0].kwargs["env"]["GPU_COORD_ROOT"], str(self.client.binding.coordinator_root))
        self.assertEqual(calls[1].args[0][3:8], ["reply", "request-1", "--action", "withdraw", "--message"])

    def test_native_atomic_collision_exit_is_definitively_unpublished(self):
        client = gpu._InstalledClient(self.client.binding)
        intent = {"request_id": "request-1", "project_id": "maestro-local", "agent_id": "agent-1",
            "minimum_remaining_seconds": 750}
        for code in (2, 3):
            with self.subTest(code=code), patch.object(client, "_load"), patch.object(subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, stdout=b"", stderr=b"")):
                with self.assertRaises(gpu.H3GpuLeaseNotPublished):
                    client.request(intent)

    def test_ownership_matches_immutable_request_inbox_or_archive(self):
        root = self.client.binding.coordinator_root
        (root / "inbox/archive").mkdir(parents=True)
        path = root / "inbox/request-1.json"
        request = {"schema": "gpu-coord/request/v1", "request_id": "request-1",
            "project_id": "maestro-local", "agent_id": "agent-1", "callback_workspace": str(root)}
        intent = {key: request[key] for key in ("request_id", "project_id", "agent_id")}
        path.write_text(json.dumps(request))
        self.assertTrue(self.client.owns_request(intent, deadline=gpu.time.monotonic() + 10))
        path = path.replace(root / "inbox/archive/20261005T120000-request-1.json")
        self.assertTrue(self.client.owns_request(intent, deadline=gpu.time.monotonic() + 10))
        for key in ("project_id", "agent_id", "callback_workspace"):
            path.write_text(json.dumps({**request, key: "foreign"}))
            self.assertFalse(self.client.owns_request(intent, deadline=gpu.time.monotonic() + 10))

    def test_installed_owner_group_modes_are_supported_but_foreign_or_world_writers_reject(self):
        path = self.client.binding.coordinator_root / "client.py"
        path.write_text("# CPU test fixture\n")
        for mode in (0o664, 0o775):
            path.chmod(mode)
            gpu._trusted_source(path)
        with patch.object(gpu.pwd, "getpwall", return_value=[SimpleNamespace(pw_uid=9999, pw_gid=path.stat().st_gid)]):
            with self.assertRaises(gpu.H3GpuLeaseUnavailable):
                gpu._trusted_source(path)
        path.chmod(0o666)
        with self.assertRaises(gpu.H3GpuLeaseUnavailable):
            gpu._trusted_source(path)


if __name__ == "__main__":
    unittest.main()
