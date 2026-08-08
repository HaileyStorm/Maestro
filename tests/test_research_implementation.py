from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.research_implementation import (
    DirtyWorkspace,
    ImplementationBusy,
    ImplementationLeaseError,
    PacketIntegrityError,
    ResearchImplementationRunner,
    _ImplementationLease,
    implementation_command,
)
from services.research_store import ResearchNotReady, ResearchRunLocked, ResearchStore


HEAD = "a" * 40
EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


def make_packet(*, chunk_count: int = 1, forced: bool = False) -> dict:
    chunks = [
        {
            "finding_id": f"finding-{index}",
            "title": f"Public finding {index}",
            "decision": "extend",
            "target_area": "bounded test surface",
            "summary": "Add only the reconciled behavior.",
            "value": "Small verified improvement.",
            "risks": ["possible duplication"],
            "evidence": ["public release metadata"],
            "conflicts": [],
            "provider_provenance": [{"source_digest": "b" * 64}],
        }
        for index in range(chunk_count)
    ]
    basis = json.dumps(chunks, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "schema_version": 1,
        "packet_id": hashlib.sha256(basis.encode()).hexdigest(),
        "created_at": "2026-08-08T00:00:00Z",
        "forced_below_threshold": forced,
        "readiness_threshold": 3,
        "chunk_count": len(chunks),
        "contract": "review_and_plan_only_until_an_explicit_implementation_run_begins",
        "chunks": chunks,
    }


class FakeStore:
    def __init__(
        self,
        root: Path,
        packet: dict | None = None,
        *,
        legacy_terminal: bool = False,
        begin_failure_after_state: bool = False,
    ):
        self.root = root
        self.packet = packet or make_packet()
        self.legacy_terminal = legacy_terminal
        self.begin_failure_after_state = begin_failure_after_state
        self.build_calls: list[dict] = []
        self.begin_calls: list[dict] = []
        self.finish_calls: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.state = {
            "research_run": {"active": False},
            "implementation_run": {
                "active": False,
                "run_id": None,
                "packet_id": None,
                "status": "never_run",
            },
        }

    def build_implementation_packet(self, **kwargs):
        self.build_calls.append(dict(kwargs))
        return json.loads(json.dumps(self.packet))

    def begin_implementation_run(self, packet, *, run_id, now):
        self.begin_calls.append({"packet": packet, "run_id": run_id})
        self.state["implementation_run"] = {
            "active": True,
            "run_id": run_id,
            "packet_id": packet["packet_id"],
            "status": "running",
        }
        if self.begin_failure_after_state:
            raise OSError("TOKEN-private begin event failure /home/person")

    def finish_implementation_run(self, *, status, summary, now):
        if self.legacy_terminal and status == "interrupted_requires_review":
            raise ValueError("unsupported implementation terminal status")
        self.finish_calls.append({"status": status, "summary": summary})
        self.state["implementation_run"].update({
            "active": False,
            "status": status,
            "summary": summary,
        })
        return dict(self.state["implementation_run"])

    def load_state(self):
        return json.loads(json.dumps(self.state))

    def save_state(self, state):
        self.state = json.loads(json.dumps(state))

    def append_event(self, event_type, payload, *, now):
        self.events.append((event_type, dict(payload)))


class FakeCompleted:
    def __init__(self, returncode: int, stdout: bytes):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = b""


class FakeGit:
    def __init__(self, statuses: list[bytes] | None = None, *, heads: list[str] | None = None):
        self.statuses = list(statuses or [b""])
        self.heads = list(heads or [HEAD])
        self.status_index = 0
        self.head_index = 0
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if "rev-parse" in command:
            value = self.heads[min(self.head_index, len(self.heads) - 1)]
            self.head_index += 1
            return FakeCompleted(0, (value + "\n").encode())
        if "status" in command:
            value = self.statuses[min(self.status_index, len(self.statuses) - 1)]
            self.status_index += 1
            return FakeCompleted(0, value)
        raise AssertionError(command)


class RecordingInput(io.BytesIO):
    def __init__(self):
        super().__init__()
        self.saved = b""

    def close(self):
        self.saved = self.getvalue()
        super().close()


class BlockingInput:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.saved = b""
        self.closed = False

    def write(self, value):
        self.entered.set()
        self.release.wait(timeout=5)
        if self.closed:
            raise BrokenPipeError("closed by timeout controller")
        self.saved += bytes(value)
        return len(value)

    def flush(self):
        return None

    def close(self):
        self.closed = True
        self.release.set()


class FakeProcess:
    def __init__(self, returncode: int = 0, *, wait_forever: bool = False, input_stream=None, pid=None):
        self.input_stream = input_stream or RecordingInput()
        self.stdin = self.input_stream
        self.pid = pid
        self.returncode = None
        self.exit_code = returncode
        self.wait_forever = wait_forever
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self.wait_forever and not (self.terminated or self.killed):
            raise subprocess.TimeoutExpired("codex", timeout)
        if self.killed:
            self.returncode = -9
        elif self.terminated:
            self.returncode = -15
        else:
            self.returncode = self.exit_code
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class PopenRecorder:
    def __init__(self, process: FakeProcess | None = None, *, error: Exception | None = None):
        self.process = process or FakeProcess()
        self.error = error
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if self.error is not None:
            raise self.error
        return self.process


class RunnerFixture:
    def __init__(
        self,
        root: Path,
        *,
        packet: dict | None = None,
        statuses: list[bytes] | None = None,
        heads: list[str] | None = None,
        process: FakeProcess | None = None,
        popen_error: Exception | None = None,
        busy=lambda: False,
        legacy_terminal: bool = False,
        begin_failure_after_state: bool = False,
        monotonic=lambda: 0.0,
        timeout_seconds: float = 60.0,
    ):
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        self.store = FakeStore(
            self.repo / "app" / "storage" / "research",
            packet,
            legacy_terminal=legacy_terminal,
            begin_failure_after_state=begin_failure_after_state,
        )
        self.git = FakeGit(statuses, heads=heads)
        self.popen = PopenRecorder(process, error=popen_error)
        self.runner = ResearchImplementationRunner(
            self.store,
            self.repo,
            busy_predicate=busy,
            git_runner=self.git,
            popen_factory=self.popen,
            monotonic=monotonic,
            timeout_seconds=timeout_seconds,
            poll_seconds=0.01,
            terminate_grace_seconds=0.01,
        )


class ResearchImplementationTests(unittest.TestCase):
    def test_exact_sol_high_command_packet_binding_and_no_output_retention(self):
        inherited_secret = "must-not-reach-child"
        with mock.patch.dict(os.environ, {
            "NOUS_API_KEY": inherited_secret,
            "PINOKIO_PRIVATE_SENTINEL": inherited_secret,
            "CLOUDFLARE_API_TOKEN": inherited_secret,
            "OPENAI_API_KEY": "required-codex-auth",
        }, clear=False):
            with tempfile.TemporaryDirectory() as temporary:
                fixture = RunnerFixture(Path(temporary), statuses=[b"", b"", b"? changed.py\0"])
                result = fixture.runner.run(force=True, readiness_threshold=9, finding_ids=["finding-0"])

        command, kwargs = fixture.popen.calls[0]
        self.assertEqual(command, [
            "codex", "exec", "-m", "gpt-5.6-sol", "-c",
            'model_reasoning_effort="high"', "--sandbox", "workspace-write",
            "--approve-for-me", "-C", str(fixture.repo.resolve()),
            "--ephemeral", "--json",
        ])
        self.assertEqual(command, implementation_command(fixture.repo.resolve()))
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["start_new_session"] if os.name == "posix" else not kwargs["start_new_session"])
        self.assertNotIn("NOUS_API_KEY", kwargs["env"])
        self.assertNotIn("PINOKIO_PRIVATE_SENTINEL", kwargs["env"])
        self.assertNotIn("CLOUDFLARE_API_TOKEN", kwargs["env"])
        self.assertEqual(kwargs["env"]["OPENAI_API_KEY"], "required-codex-auth")
        prompt = fixture.popen.process.input_stream.saved.decode()
        packet = fixture.store.packet
        safe_scope = {
            "schema_version": 1,
            "packet_id": packet["packet_id"],
            "chunk_count": packet["chunk_count"],
            "chunks": [
                {key: value for key, value in chunk.items() if key != "provider_provenance"}
                for chunk in packet["chunks"]
            ],
        }
        self.assertIn(f"packet_sha256={packet['packet_id']}", prompt)
        self.assertIn(json.dumps(safe_scope, sort_keys=True, ensure_ascii=True, separators=(",", ":")), prompt)
        self.assertNotIn("provider_provenance", prompt)
        self.assertNotIn(inherited_secret, prompt)
        self.assertIn("Never inspect, read, quote, summarize, or modify Maestro prompts", prompt)
        self.assertIn("Do not access the network or any external service", prompt)
        self.assertIn("never commit, merge, rebase, pull, push", prompt)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)
        self.assertEqual(fixture.store.build_calls[0]["force"], True)
        self.assertEqual(fixture.store.build_calls[0]["readiness_threshold"], 9)
        self.assertEqual(fixture.store.build_calls[0]["finding_ids"], ("finding-0",))
        self.assertEqual(len(fixture.store.build_calls), 2)
        self.assertEqual(
            {key: fixture.store.build_calls[0][key] for key in ("force", "readiness_threshold", "finding_ids")},
            {key: fixture.store.build_calls[1][key] for key in ("force", "readiness_threshold", "finding_ids")},
        )

    def test_force_bypasses_only_store_size_threshold_not_busy_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary), busy=lambda: True)
            with self.assertRaises(ImplementationBusy):
                fixture.runner.run(force=True)
        self.assertEqual(fixture.store.build_calls, [])
        self.assertEqual(fixture.popen.calls, [])

    def test_force_does_not_bypass_active_research_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary))
            fixture.store.state["research_run"]["active"] = True
            with self.assertRaises(ImplementationBusy):
                fixture.runner.run(force=True)
        self.assertEqual(fixture.store.build_calls, [])
        self.assertEqual(fixture.store.begin_calls, [])
        self.assertEqual(fixture.popen.calls, [])

    def test_cleanliness_uses_porcelain_v2_and_excludes_only_research_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary), statuses=[b"? .working\0"])
            with self.assertRaises(DirtyWorkspace):
                fixture.runner.run(force=True)
        status_command, status_kwargs = next(call for call in fixture.git.calls if "status" in call[0])
        self.assertIn("--porcelain=v2", status_command)
        self.assertIn("--ignore-submodules=none", status_command)
        excludes = [value for value in status_command if value.startswith(":(exclude)")]
        self.assertEqual(excludes, [
            ":(exclude)app/storage/research",
            ":(exclude)app/storage/research/**",
        ])
        self.assertFalse(status_kwargs["shell"])
        self.assertEqual(fixture.store.begin_calls, [])

    def test_dirty_or_head_race_immediately_after_lease_fails_before_begin(self):
        for statuses, heads in (
            ([b"", b"1 M. N... changed.py\0"], None),
            ([b"", b""], [HEAD, "b" * 40]),
        ):
            with self.subTest(statuses=statuses, heads=heads):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = RunnerFixture(Path(temporary), statuses=statuses, heads=heads)
                    with self.assertRaises(DirtyWorkspace):
                        fixture.runner.run(force=True)
                    self.assertEqual(fixture.store.begin_calls, [])
                    self.assertFalse((fixture.store.root / ".implementation-run.lock").exists())

    def test_cross_process_lock_race_is_fail_closed_and_lock_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary))
            fixture.store.root.mkdir(parents=True)
            lock = fixture.store.root / ".implementation-run.lock"
            lock.write_text("other owner", encoding="utf-8")
            with self.assertRaises(ImplementationBusy):
                fixture.runner.run(force=True)
            self.assertEqual(lock.read_text(encoding="utf-8"), "other owner")
        self.assertEqual(fixture.store.begin_calls, [])

    def test_active_research_lease_race_blocks_and_releases_implementation_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary))
            fixture.store.root.mkdir(parents=True)
            research_lock = fixture.store.root / ".research-run.lock"
            research_lock.write_text("active research owner", encoding="utf-8")
            with self.assertRaises(ImplementationBusy):
                fixture.runner.run(force=True)
            self.assertTrue(research_lock.exists())
            self.assertFalse((fixture.store.root / ".implementation-run.lock").exists())
        self.assertEqual(fixture.store.begin_calls, [])

    def test_research_store_cannot_reclaim_runner_reciprocal_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            root = repo / "app" / "storage" / "research"
            store = ResearchStore(root, allow_test_root=True)
            with _ImplementationLease(repo, root, ".research-run.lock"):
                with self.assertRaises(ResearchRunLocked):
                    with store.lock("research-run"):
                        self.fail("ResearchStore reclaimed an active reciprocal lease")
                self.assertTrue((root / ".research-run.lock").exists())
            self.assertFalse((root / ".research-run.lock").exists())

    def test_partial_lease_persistence_failure_removes_owned_lock_for_both_names(self):
        for lock_name in (".implementation-run.lock", ".research-run.lock"):
            for failing_operation in ("write", "fsync"):
                with self.subTest(lock_name=lock_name, failing_operation=failing_operation):
                    with tempfile.TemporaryDirectory() as temporary:
                        repo = Path(temporary) / "repo"
                        repo.mkdir()
                        (repo / ".git").mkdir()
                        root = repo / "app" / "storage" / "research"
                        if failing_operation == "write":
                            failure = mock.patch(
                                "services.research_implementation.os.write",
                                side_effect=OSError("simulated lease write failure"),
                            )
                        else:
                            fsync_calls = 0

                            def fail_first_fsync(_descriptor):
                                nonlocal fsync_calls
                                fsync_calls += 1
                                if fsync_calls == 1:
                                    raise OSError("simulated lease fsync failure")

                            failure = mock.patch(
                                "services.research_implementation.os.fsync",
                                side_effect=fail_first_fsync,
                            )
                        with failure:
                            with self.assertRaises(ImplementationLeaseError):
                                with _ImplementationLease(repo, root, lock_name):
                                    self.fail("lease unexpectedly survived persistence failure")
                        self.assertFalse((root / lock_name).exists())

    def test_symlink_lock_is_rejected_without_following_or_removing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary))
            fixture.store.root.mkdir(parents=True)
            target = Path(temporary) / "sensitive.txt"
            target.write_text("do not touch", encoding="utf-8")
            lock = fixture.store.root / ".implementation-run.lock"
            lock.symlink_to(target)
            with self.assertRaises(ImplementationBusy):
                fixture.runner.run(force=True)
            self.assertTrue(lock.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "do not touch")

    def test_child_crash_cleans_lease_and_persists_review_state_with_legacy_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(
                Path(temporary),
                statuses=[b"", b"", b"?? partial.py\0"],
                process=FakeProcess(returncode=7),
                legacy_terminal=True,
            )
            result = fixture.runner.run(force=True)
            lock = fixture.store.root / ".implementation-run.lock"
            self.assertFalse(lock.exists())
        self.assertEqual(result["status"], "interrupted_requires_review")
        self.assertEqual(result["agent_outcome"], "crashed")
        self.assertEqual(result["return_code"], 7)
        self.assertEqual(fixture.store.state["implementation_run"]["status"], "interrupted_requires_review")
        self.assertFalse(fixture.store.state["implementation_run"]["active"])
        self.assertEqual(fixture.store.finish_calls[0]["status"], "failed")
        self.assertIn("implementation_requires_review", [event[0] for event in fixture.store.events])

    def test_timeout_terminates_process_and_never_marks_complete(self):
        values = iter([0.0, 2.0])
        with tempfile.TemporaryDirectory() as temporary:
            process = FakeProcess(wait_forever=True)
            fixture = RunnerFixture(
                Path(temporary),
                statuses=[b"", b"", b""],
                process=process,
                monotonic=lambda: next(values),
                timeout_seconds=1.0,
            )
            result = fixture.runner.run(force=True)
        self.assertTrue(process.terminated)
        self.assertEqual(result["status"], "interrupted_requires_review")
        self.assertEqual(result["agent_outcome"], "timed_out")

    def test_timeout_starts_before_pipe_feed_and_unblocks_a_backpressured_writer(self):
        stream = BlockingInput()
        calls = 0

        def clock():
            nonlocal calls
            calls += 1
            if calls == 1:
                return 0.0
            stream.entered.wait(timeout=1)
            return 2.0

        with tempfile.TemporaryDirectory() as temporary:
            process = FakeProcess(wait_forever=True, input_stream=stream)
            fixture = RunnerFixture(
                Path(temporary),
                statuses=[b"", b"", b""],
                process=process,
                monotonic=clock,
                timeout_seconds=1.0,
            )
            result = fixture.runner.run(force=True)
        self.assertTrue(stream.entered.is_set())
        self.assertTrue(stream.closed)
        self.assertTrue(process.terminated)
        self.assertEqual(result["agent_outcome"], "timed_out")

    @unittest.skipUnless(os.name == "posix", "POSIX process-group containment")
    def test_success_cleans_any_surviving_child_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            process = FakeProcess(pid=43210)
            fixture = RunnerFixture(Path(temporary), statuses=[b"", b"", b""], process=process)
            with mock.patch("services.research_implementation.os.killpg") as kill_group:
                result = fixture.runner.run(force=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [call.args for call in kill_group.call_args_list],
            [(43210, signal.SIGTERM), (43210, signal.SIGKILL)],
        )

    def test_in_process_cancel_is_drained_and_is_cancelled_only_without_mutation(self):
        states = iter([False, True])
        with tempfile.TemporaryDirectory() as temporary:
            process = FakeProcess(wait_forever=True)
            fixture = RunnerFixture(
                Path(temporary),
                statuses=[b"", b"", b""],
                process=process,
            )
            result = fixture.runner.run(force=True, cancel=lambda: next(states))
        self.assertTrue(process.terminated)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["agent_outcome"], "cancelled")
        self.assertEqual(fixture.store.finish_calls[-1]["status"], "cancelled")

    def test_spawn_error_and_hostile_diagnostics_are_not_retained_or_exposed(self):
        secret = "TOKEN-super-secret /private/user/path"
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(
                Path(temporary),
                statuses=[b"", b"", b""],
                popen_error=RuntimeError(secret),
            )
            result = fixture.runner.run(force=True)
        serialized = json.dumps({
            "result": result,
            "state": fixture.store.state,
            "events": fixture.store.events,
        })
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("/private/user/path", serialized)
        self.assertEqual(result["status"], "interrupted_requires_review")

    def test_packet_digest_mismatch_fails_before_lease_or_agent(self):
        packet = make_packet()
        packet["chunks"][0]["summary"] = "tampered"
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary), packet=packet)
            with self.assertRaises(PacketIntegrityError):
                fixture.runner.run(force=True)
        self.assertEqual(fixture.store.begin_calls, [])
        self.assertEqual(fixture.popen.calls, [])

    def test_conflict_reopened_during_lease_invalidates_preflight_packet(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary))
            initial_build = fixture.store.build_implementation_packet

            def reopen_conflict(**kwargs):
                if not fixture.store.build_calls:
                    return initial_build(**kwargs)
                self.assertTrue((fixture.store.root / ".implementation-run.lock").exists())
                self.assertTrue((fixture.store.root / ".research-run.lock").exists())
                fixture.store.build_calls.append(dict(kwargs))
                raise ResearchNotReady("finding became conflicted and is no longer eligible")

            fixture.store.build_implementation_packet = reopen_conflict
            with self.assertRaisesRegex(
                PacketIntegrityError,
                "packet changed while acquiring the research lease",
            ):
                fixture.runner.run(
                    force=True,
                    readiness_threshold=7,
                    finding_ids=["finding-0"],
                )
            self.assertFalse((fixture.store.root / ".implementation-run.lock").exists())
            self.assertFalse((fixture.store.root / ".research-run.lock").exists())
        self.assertEqual(len(fixture.store.build_calls), 2)
        for call in fixture.store.build_calls:
            self.assertTrue(call["force"])
            self.assertEqual(call["readiness_threshold"], 7)
            self.assertEqual(call["finding_ids"], ("finding-0",))
        self.assertEqual(fixture.store.begin_calls, [])
        self.assertEqual(fixture.popen.calls, [])

    def test_secret_shaped_or_private_path_packet_text_never_reaches_agent(self):
        for unsafe in (
            "api_key=do-not-send",
            "standalone sk-proj-abcdefgh12345678",
            "local evidence /home/person/private.txt",
            "root key /root/.ssh/id_rsa",
            "workspace /media/person/project",
            "macOS /private/var/private.txt",
            r"Windows \\server\private\secret.txt",
            "path=/home/person/private.txt",
            "(/root/.ssh/id_rsa)",
            "workspace:/media/person/project",
            r"path=C:\Users\person\secret.txt",
            r"path=\\server\private\secret.txt",
        ):
            with self.subTest(unsafe=unsafe):
                packet = make_packet()
                packet["chunks"][0]["summary"] = unsafe
                basis = json.dumps(packet["chunks"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                packet["packet_id"] = hashlib.sha256(basis.encode()).hexdigest()
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = RunnerFixture(Path(temporary), packet=packet)
                    with self.assertRaises(PacketIntegrityError):
                        fixture.runner.run(force=True)
                self.assertEqual(fixture.popen.calls, [])

    def test_partial_begin_failure_is_terminalized_and_lease_is_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary), begin_failure_after_state=True)
            with self.assertRaisesRegex(Exception, "state could not be started safely"):
                fixture.runner.run(force=True)
            self.assertFalse((fixture.store.root / ".implementation-run.lock").exists())
            self.assertFalse((fixture.store.root / ".research-run.lock").exists())
        self.assertFalse(fixture.store.state["implementation_run"]["active"])
        self.assertEqual(
            fixture.store.state["implementation_run"]["status"],
            "interrupted_requires_review",
        )

    def test_successful_child_that_changes_head_requires_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(
                Path(temporary),
                statuses=[b"", b"", b""],
                heads=[HEAD, HEAD, HEAD, HEAD, "c" * 40, "c" * 40],
            )
            result = fixture.runner.run(force=True)
        self.assertEqual(result["agent_outcome"], "completed")
        self.assertEqual(result["status"], "interrupted_requires_review")

    def test_successful_child_that_stages_changes_requires_review(self):
        staged = b"1 M. N... 100644 100644 100644 " + (b"a" * 40) + b" " + (b"b" * 40) + b" changed.py\0"
        with tempfile.TemporaryDirectory() as temporary:
            fixture = RunnerFixture(Path(temporary), statuses=[b"", b"", staged])
            result = fixture.runner.run(force=True)
        self.assertEqual(result["agent_outcome"], "completed")
        self.assertEqual(result["status"], "interrupted_requires_review")


if __name__ == "__main__":
    unittest.main()
