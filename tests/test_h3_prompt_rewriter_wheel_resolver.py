"""Source-only tests for the H3 prompt-rewriter wheel staging boundary."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_prompt_rewriter_dependency_closure as closure
from services import h3_prompt_rewriter_wheel_resolver as resolver

from scripts import resolve_h3_prompt_rewriter_wheels as resolver_cli

PACKAGES = {
    "accelerate": ("1.12.0", "py3-none-any", ()),
    "nvidia-cublas-cu12": (
        "12.8.4.1",
        "py3-none-manylinux_2_28_x86_64",
        (),
    ),
    "peft": ("0.20.0", "py3-none-any", ()),
    "pillow": ("12.2.0", "cp312-cp312-manylinux_2_28_x86_64", ()),
    "safetensors": ("0.8.0", "cp312-cp312-manylinux_2_28_x86_64", ()),
    "tokenizers": ("0.22.1", "cp312-cp312-manylinux_2_28_x86_64", ()),
    "torch": (
        "2.10.0+cu128",
        "cp312-cp312-manylinux_2_28_x86_64",
        ("nvidia-cublas-cu12==12.8.4.1",),
    ),
    "torchvision": (
        "0.25.0+cu128",
        "cp312-cp312-manylinux_2_28_x86_64",
        ("torch==2.10.0+cu128",),
    ),
    "transformers": ("4.57.1", "py3-none-any", ()),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _make_wheel(
    directory: Path,
    name: str,
    version: str,
    tag: str,
    dependencies: tuple[str, ...],
) -> Path:
    filename = f"{name.replace('-', '_')}-{version}-{tag}.whl"
    path = directory / filename
    metadata = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    metadata.extend(f"Requires-Dist: {item}" for item in dependencies)
    metadata.append("")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
            "\n".join(metadata).encode("utf-8"),
        )
    path.chmod(0o600)
    return path


class _FakePip:
    def __init__(self, sources: dict[str, Path], *, fail_call: int | None = None):
        self.sources = sources
        self.fail_call = fail_call
        self.commands: list[list[str]] = []
        self.active = 0
        self.max_active = 0

    def __call__(self, command, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.commands.append(list(command))
            inspect.signature(resolver.subprocess.Popen).bind(command, **kwargs)
            case = unittest.TestCase()
            case.assertFalse(kwargs["shell"])
            case.assertEqual(
                set(kwargs["env"]), {"PATH", "HOME", "TMPDIR", "PIP_CONFIG_FILE"}
            )
            case.assertEqual(kwargs["env"]["PIP_CONFIG_FILE"], "/dev/null")
            case.assertNotIn("PYTHONPATH", kwargs["env"])
            case.assertEqual(kwargs["preexec_fn"].__name__, "_apply_child_limits")
            case.assertIn("--isolated", command)
            case.assertIn("--no-deps", command)
            case.assertIn("--only-binary=:all:", command)
            case.assertIn("--no-index", command)
            case.assertNotIn("--index-url", command)
            case.assertNotIn("--extra-index-url", command)
            case.assertNotIn("--find-links", command)
            source_url = command[-1]
            case.assertRegex(source_url, r"^https://[^/]+/.+\.whl$")
            if self.fail_call == len(self.commands):
                return _FakeProcess(returncode=9)
            destination = Path(command[command.index("--dest") + 1])
            shutil.copyfile(
                self.sources[source_url], destination / self.sources[source_url].name
            )
            (destination / self.sources[source_url].name).chmod(0o600)
            return _FakeProcess(returncode=0)
        finally:
            self.active -= 1


class _FakeProcess:
    _next_pid = 5000

    def __init__(self, *, returncode: int):
        self.returncode = returncode
        self.pid = self._next_pid
        type(self)._next_pid += 1

    def communicate(self, timeout):
        return b"", b""

    def wait(self, timeout):
        return self.returncode


class _HungProcess:
    def __init__(self):
        self.pid = 7001
        self.returncode = None
        self.waits = 0

    def communicate(self, timeout):
        raise resolver.subprocess.TimeoutExpired("pip", timeout)

    def wait(self, timeout):
        self.waits += 1
        if self.waits == 1:
            raise resolver.subprocess.TimeoutExpired("pip", timeout)
        self.returncode = -9
        return self.returncode


class _HungFactory:
    def __init__(self, process=None):
        self.kwargs = None
        self.process = process or _HungProcess()

    def __call__(self, command, **kwargs):
        self.kwargs = kwargs
        return self.process


class _Clock:
    def __init__(self, values: list[float], fallback: float = 0):
        self.values = list(values)
        self.fallback = fallback

    def __call__(self) -> float:
        return self.values.pop(0) if self.values else self.fallback


class H3PromptRewriterWheelResolverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.feature = self.root / "feature"
        self.feature.mkdir(mode=0o700)
        self.stage = self.feature / "stage"
        source = self.root / "source"
        source.mkdir(mode=0o700)
        self.python_executable = source / "python"
        self.python_executable.write_bytes(b"")
        self.python_executable.chmod(0o700)
        self.sources: dict[str, Path] = {}
        rows = []
        for name in sorted(PACKAGES):
            version, tag, dependencies = PACKAGES[name]
            path = _make_wheel(source, name, version, tag, dependencies)
            raw = path.read_bytes()
            requirement = f"{name}=={version}"
            index = (
                resolver.PYTORCH_INDEX
                if name in {"torch", "torchvision"} or name.startswith("nvidia-")
                else resolver.PYPI_INDEX
            )
            host_path = (
                "https://download.pytorch.org/whl/cu128/"
                f"{urllib.parse.quote(path.name, safe='-._~')}"
                if index == resolver.PYTORCH_INDEX
                else "https://files.pythonhosted.org/packages/aa/bb/"
                f"{urllib.parse.quote(path.name, safe='-._~')}"
            )
            self.sources[host_path] = path
            rows.append(
                {
                    "name": name,
                    "version": version,
                    "requirement": requirement,
                    "dependencies": list(dependencies),
                    "wheel": {
                        "filename": path.name,
                        "size_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "index": index,
                        "source_url": host_path,
                    },
                }
            )
        self.report = {
            "schema": resolver.WHEEL_RESOLUTION_REPORT_SCHEMA,
            "target": {
                "python_implementation": "cpython",
                "python_version": "3.12",
                "python_abi": "cp312",
                "platform": "manylinux_2_28_x86_64",
                "binary_wheels_only": True,
            },
            "root_requirements": list(closure.ROOT_REQUIREMENTS),
            "packages": rows,
        }
        self.report_payload = _canonical(self.report) + b"\n"
        self.report_sha = hashlib.sha256(self.report_payload).hexdigest()

    def execute(self, plan, fake, *, clock=None):
        return resolver.execute_h3_prompt_rewriter_wheel_resolution(
            plan,
            expected_plan_sha256=plan.sha256,
            resolution_report_payload=self.report_payload,
            expected_resolution_report_sha256=self.report_sha,
            private_feature_root=self.feature,
            staging_root=self.stage,
            python_executable=self.python_executable,
            process_factory=fake,
            monotonic=clock or _Clock([], 0),
            apply_parent_limits=lambda: None,
        )

    def test_plan_is_network_free_path_free_and_binds_limits(self):
        with (
            mock.patch.object(socket, "socket", side_effect=AssertionError("network")),
            mock.patch("subprocess.run", side_effect=AssertionError("subprocess")),
        ):
            first = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
            second = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        self.assertEqual(first.sha256, second.sha256)
        self.assertFalse(first.document["mutation"])
        self.assertEqual(first.document["wheel_per_subprocess"], 1)
        self.assertEqual(first.document["resource_limits"]["nice"], 15)
        self.assertEqual(first.document["resource_limits"]["ionice"], "idle")
        self.assertEqual(first.document["resource_limits"]["cpu_cores"], 2)
        self.assertEqual(first.document["resource_limits"]["rss_bytes"], 1536 * 1024**2)
        self.assertNotIn(str(ROOT), json.dumps(first.document, sort_keys=True))

    def test_one_exact_wheel_per_process_and_atomic_manifest(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        fake = _FakePip(self.sources)
        manifest = self.execute(plan, fake)
        self.assertEqual(len(fake.commands), len(PACKAGES))
        self.assertEqual(fake.max_active, 1)
        self.assertTrue(all(command[-2] == "--no-deps" for command in fake.commands))
        for command in fake.commands:
            source_url = command[-1]
            package = next(
                row
                for row in self.report["packages"]
                if row["wheel"]["source_url"] == source_url
            )
            self.assertEqual(source_url, package["wheel"]["source_url"])
        self.assertEqual(manifest["wheel_count"], len(PACKAGES))
        self.assertEqual(manifest["resolution_report_sha256"], self.report_sha)
        self.assertFalse(manifest["installation_authorized"])
        self.assertTrue((self.stage / resolver.MANIFEST_NAME).is_file())
        self.assertFalse((self.stage / ".wheel-manifest.json.tmp").exists())

    def test_child_boundary_enforces_nice_io_cpu_memory_and_file_size(self):
        libc = mock.Mock()
        libc.syscall.return_value = 0
        with (
            mock.patch.object(resolver.os, "umask") as umask,
            mock.patch.object(resolver.os, "setpriority") as setpriority,
            mock.patch.object(
                resolver.os, "sched_getaffinity", return_value={7, 3, 11}
            ),
            mock.patch.object(resolver.os, "sched_setaffinity") as setaffinity,
            mock.patch.object(resolver.resource, "setrlimit") as setrlimit,
            mock.patch.object(resolver.ctypes, "CDLL", return_value=libc),
        ):
            resolver._child_limit_callback(12345)()
        umask.assert_called_once_with(0o077)
        setpriority.assert_called_once_with(resolver.os.PRIO_PROCESS, 0, 15)
        setaffinity.assert_called_once_with(0, {3, 7})
        self.assertIn(
            mock.call(
                resolver.resource.RLIMIT_AS,
                (resolver.MAX_RSS_BYTES, resolver.MAX_RSS_BYTES),
            ),
            setrlimit.call_args_list,
        )
        self.assertIn(
            mock.call(resolver.resource.RLIMIT_FSIZE, (12345, 12345)),
            setrlimit.call_args_list,
        )
        libc.syscall.assert_called_once_with(251, 1, 0, 3 << 13)

    def test_report_size_gate_precedes_staging_and_subprocess(self):
        total = sum(row["wheel"]["size_bytes"] for row in self.report["packages"])
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan(
            byte_cap=total - 1
        )
        fake = _FakePip(self.sources)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError, "before download"
        ):
            self.execute(plan, fake)
        self.assertFalse(self.stage.exists())
        self.assertEqual(fake.commands, [])

    def test_plan_and_report_hashes_precede_mutation(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError, "expected SHA-256"
        ):
            resolver.execute_h3_prompt_rewriter_wheel_resolution(
                plan,
                expected_plan_sha256="0" * 64,
                resolution_report_payload=self.report_payload,
                expected_resolution_report_sha256=self.report_sha,
                private_feature_root=self.feature,
                staging_root=self.stage,
                python_executable=self.python_executable,
                process_factory=_FakePip(self.sources),
                apply_parent_limits=lambda: None,
            )
        self.assertFalse(self.stage.exists())
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError, "report"
        ):
            resolver.execute_h3_prompt_rewriter_wheel_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                resolution_report_payload=self.report_payload,
                expected_resolution_report_sha256="0" * 64,
                private_feature_root=self.feature,
                staging_root=self.stage,
                python_executable=self.python_executable,
                process_factory=_FakePip(self.sources),
                apply_parent_limits=lambda: None,
            )
        self.assertFalse(self.stage.exists())

    def test_report_hash_precedes_bounded_deep_json_validation(self):
        payload = b"[" * 2000 + b"0" + b"]" * 2000 + b"\n"
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError,
            "expected SHA-256",
        ):
            resolver._load_report(payload, "0" * 64)

        digest = hashlib.sha256(payload).hexdigest()
        with self.assertRaises(
            resolver.H3PromptRewriterWheelResolverSecurityError
        ) as raised:
            resolver._load_report(payload, digest)
        self.assertNotIsInstance(raised.exception, RecursionError)
        self.assertNotIn("maximum recursion", str(raised.exception).casefold())

    def test_report_rejects_wide_json_node_bomb(self):
        payload = _canonical([0] * (closure.MAX_JSON_NODES + 1)) + b"\n"
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError,
            "structure bound",
        ):
            resolver._load_report(payload, hashlib.sha256(payload).hexdigest())

    def test_report_source_url_is_bound_before_mutation(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        changed = json.loads(self.report_payload)
        changed["packages"][0]["wheel"]["source_url"] = (
            "https://unreviewed.invalid/packages/artifact.whl"
        )
        payload = _canonical(changed) + b"\n"
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError, "source URL"
        ):
            resolver.execute_h3_prompt_rewriter_wheel_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                resolution_report_payload=payload,
                expected_resolution_report_sha256=hashlib.sha256(payload).hexdigest(),
                private_feature_root=self.feature,
                staging_root=self.stage,
                python_executable=self.python_executable,
                process_factory=_FakePip(self.sources),
                apply_parent_limits=lambda: None,
            )
        self.assertFalse(self.stage.exists())

    def test_source_url_rejects_ports_traversal_and_encoded_separators(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        filename = self.report["packages"][0]["wheel"]["filename"]
        cases = (
            f"https://files.pythonhosted.org:444/packages/aa/bb/{filename}",
            f"https://files.pythonhosted.org/packages/../bb/{filename}",
            f"https://files.pythonhosted.org/packages/%2E%2E/bb/{filename}",
            f"https://files.pythonhosted.org/packages/aa%2Fbb/{filename}",
            f"https://files.pythonhosted.org/packages/aa%5Cbb/{filename}",
            f"https://files.pythonhosted.org/packages/%252e%252e/bb/{filename}",
        )
        for source_url in cases:
            with self.subTest(source_url=source_url):
                changed = json.loads(self.report_payload)
                changed["packages"][0]["wheel"]["source_url"] = source_url
                payload = _canonical(changed) + b"\n"
                with self.assertRaisesRegex(
                    resolver.H3PromptRewriterWheelResolverSecurityError,
                    "source URL",
                ):
                    resolver.execute_h3_prompt_rewriter_wheel_resolution(
                        plan,
                        expected_plan_sha256=plan.sha256,
                        resolution_report_payload=payload,
                        expected_resolution_report_sha256=hashlib.sha256(
                            payload
                        ).hexdigest(),
                        private_feature_root=self.feature,
                        staging_root=self.stage,
                        python_executable=self.python_executable,
                        process_factory=_FakePip(self.sources),
                        apply_parent_limits=lambda: None,
                    )
                self.assertFalse(self.stage.exists())

    def test_source_url_accepts_only_reviewed_uv_registry_artifact_hosts(self):
        cases = (
            (
                "torch-2.10.0+cu128-cp312-cp312-manylinux_2_28_x86_64.whl",
                "torch",
                resolver.PYTORCH_INDEX,
                (
                    "https://download-r2.pytorch.org/whl/cu128/"
                    "torch-2.10.0%2Bcu128-cp312-cp312-manylinux_2_28_x86_64.whl"
                ),
            ),
            (
                "cuda_bindings-12.9.4-cp312-cp312-manylinux_2_28_x86_64.whl",
                "cuda-bindings",
                resolver.PYTORCH_INDEX,
                (
                    "https://files.pythonhosted.org/packages/aa/bb/"
                    "cuda_bindings-12.9.4-cp312-cp312-manylinux_2_28_x86_64.whl"
                ),
            ),
            (
                "nvidia_cublas_cu12-12.8.4.1-py3-none-manylinux_2_27_x86_64.whl",
                "nvidia-cublas-cu12",
                resolver.PYTORCH_INDEX,
                (
                    "https://pypi.nvidia.com/nvidia-cublas-cu12/"
                    "nvidia_cublas_cu12-12.8.4.1-py3-none-manylinux_2_27_x86_64.whl"
                ),
            ),
        )
        for filename, package, index, url in cases:
            with self.subTest(package=package):
                self.assertEqual(
                    resolver._source_url(filename, package, index, url), url
                )
        with self.assertRaises(resolver.H3PromptRewriterWheelResolverSecurityError):
            resolver._source_url(
                "accelerate-1.12.0-py3-none-any.whl",
                "accelerate",
                resolver.PYPI_INDEX,
                "https://download.pytorch.org/whl/accelerate-1.12.0-py3-none-any.whl",
            )
        with self.assertRaises(resolver.H3PromptRewriterWheelResolverSecurityError):
            resolver._source_url(
                "accelerate-1.12.0-py3-none-any.whl",
                "accelerate",
                resolver.PYTORCH_INDEX,
                "https://pypi.nvidia.com/accelerate/accelerate-1.12.0-py3-none-any.whl",
            )

    def test_deadline_stops_between_wheels_and_complete_attempt_resumes(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan(
            deadline_seconds=3
        )
        first = _FakePip(self.sources)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverExecutionError, "deadline"
        ):
            self.execute(plan, first, clock=_Clock([0, 1, 2, 4], 4))
        self.assertEqual(len(first.commands), 1)
        self.assertFalse((self.stage / resolver.MANIFEST_NAME).exists())
        second = _FakePip(self.sources)
        manifest = self.execute(plan, second, clock=_Clock([], 0))
        self.assertEqual(len(second.commands), len(PACKAGES) - 1)
        self.assertEqual(manifest["wheel_count"], len(PACKAGES))

    def test_hung_child_gets_group_term_then_kill_at_remaining_deadline(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan(
            deadline_seconds=3
        )
        factory = _HungFactory()
        signals = []
        group_alive = True

        def kill_group(pid, sig):
            nonlocal group_alive
            if sig == 0 and not group_alive:
                raise ProcessLookupError
            signals.append((pid, sig))
            if sig == resolver.signal.SIGKILL:
                group_alive = False

        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverExecutionError,
            "hard deadline",
        ):
            resolver.execute_h3_prompt_rewriter_wheel_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                resolution_report_payload=self.report_payload,
                expected_resolution_report_sha256=self.report_sha,
                private_feature_root=self.feature,
                staging_root=self.stage,
                python_executable=self.python_executable,
                process_factory=factory,
                monotonic=_Clock([0, 1, 2], 2),
                apply_parent_limits=lambda: None,
                kill_process_group=kill_group,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(
            signals,
            [
                (factory.process.pid, resolver.signal.SIGTERM),
                (factory.process.pid, 0),
                (factory.process.pid, resolver.signal.SIGKILL),
            ],
        )
        self.assertTrue(factory.kwargs["start_new_session"])
        self.assertTrue(factory.kwargs["close_fds"])
        self.assertFalse((self.stage / resolver.MANIFEST_NAME).exists())

    def test_timeout_kills_surviving_group_after_leader_exits_on_term(self):
        class LeaderExited(_HungProcess):
            def wait(self, timeout):
                self.returncode = -15
                return self.returncode

        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan(
            deadline_seconds=3
        )
        factory = _HungFactory(LeaderExited())
        signals = []
        group_alive = True

        def kill_group(pid, sig):
            nonlocal group_alive
            if sig == 0 and not group_alive:
                raise ProcessLookupError
            signals.append((pid, sig))
            if sig == resolver.signal.SIGKILL:
                group_alive = False

        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverExecutionError,
            "hard deadline",
        ):
            resolver.execute_h3_prompt_rewriter_wheel_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                resolution_report_payload=self.report_payload,
                expected_resolution_report_sha256=self.report_sha,
                private_feature_root=self.feature,
                staging_root=self.stage,
                python_executable=self.python_executable,
                process_factory=factory,
                monotonic=_Clock([0, 1, 2], 2),
                apply_parent_limits=lambda: None,
                kill_process_group=kill_group,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(
            signals,
            [
                (factory.process.pid, resolver.signal.SIGTERM),
                (factory.process.pid, 0),
                (factory.process.pid, resolver.signal.SIGKILL),
            ],
        )

    def test_ambiguous_interrupted_output_fails_closed(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        partial = self.stage / ".partial"
        attempts = partial / "attempts"
        attempt = attempts / "accelerate"
        for directory in (self.stage, partial, attempts, attempt):
            directory.mkdir(mode=0o700)
        (attempt / "pip-partial.tmp").write_bytes(b"partial")
        (attempt / "pip-partial.tmp").chmod(0o600)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverSecurityError,
            "owner-reviewed removal",
        ):
            self.execute(plan, _FakePip(self.sources))

    def test_subprocess_failure_is_content_free_and_preserves_state(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        fake = _FakePip(self.sources, fail_call=2)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverExecutionError,
            "subprocess failed",
        ) as raised:
            self.execute(plan, fake)
        self.assertNotIn(str(self.stage), str(raised.exception))
        state = json.loads((self.stage / ".partial" / "state.json").read_text())
        self.assertEqual(len(state["verified"]), 1)
        self.assertFalse((self.stage / resolver.MANIFEST_NAME).exists())

    def test_popen_constructor_type_error_is_content_free(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()

        def rejecting_constructor(command, **kwargs):
            inspect.signature(resolver.subprocess.Popen).bind(command, **kwargs)
            raise TypeError("private constructor detail")

        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverExecutionError,
            "subprocess failed",
        ) as raised:
            resolver.execute_h3_prompt_rewriter_wheel_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                resolution_report_payload=self.report_payload,
                expected_resolution_report_sha256=self.report_sha,
                private_feature_root=self.feature,
                staging_root=self.stage,
                python_executable=self.python_executable,
                process_factory=rejecting_constructor,
                monotonic=_Clock([], 0),
                apply_parent_limits=lambda: None,
            )
        self.assertNotIn("private constructor detail", str(raised.exception))

    def test_equal_atomic_leftovers_reconcile_without_redownload(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        first = _FakePip(self.sources)
        expected = self.execute(plan, first)
        manifest = self.stage / resolver.MANIFEST_NAME
        temporary_manifest = self.stage / ".wheel-manifest.json.tmp"
        shutil.copyfile(manifest, temporary_manifest)
        temporary_manifest.chmod(0o600)
        state = self.stage / ".partial" / "state.json"
        temporary_state = state.parent / ".state.json.tmp"
        shutil.copyfile(state, temporary_state)
        temporary_state.chmod(0o600)
        second = _FakePip(self.sources)
        actual = self.execute(plan, second)
        self.assertEqual(actual, expected)
        self.assertEqual(second.commands, [])
        self.assertFalse(temporary_manifest.exists())
        self.assertFalse(temporary_state.exists())

    def test_malformed_dependency_types_and_bad_wheel_are_content_free(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        cases = []
        mixed = json.loads(self.report_payload)
        mixed["packages"][0]["dependencies"] = [1]
        cases.append(mixed)
        bad_wheel = json.loads(self.report_payload)
        bad_wheel["packages"][0]["wheel"]["filename"] = "not-a-wheel.tar.gz"
        bad_wheel["packages"][0]["wheel"]["source_url"] = (
            "https://files.pythonhosted.org/packages/aa/bb/not-a-wheel.tar.gz"
        )
        cases.append(bad_wheel)
        for value in cases:
            with self.subTest(filename=value["packages"][0]["wheel"]["filename"]):
                payload = _canonical(value) + b"\n"
                with self.assertRaises(
                    resolver.H3PromptRewriterWheelResolverSecurityError
                ) as raised:
                    resolver.execute_h3_prompt_rewriter_wheel_resolution(
                        plan,
                        expected_plan_sha256=plan.sha256,
                        resolution_report_payload=payload,
                        expected_resolution_report_sha256=hashlib.sha256(
                            payload
                        ).hexdigest(),
                        private_feature_root=self.feature,
                        staging_root=self.stage,
                        python_executable=self.python_executable,
                        process_factory=_FakePip(self.sources),
                        apply_parent_limits=lambda: None,
                    )
                self.assertNotIn("not-a-wheel", str(raised.exception))
                self.assertFalse(self.stage.exists())

    def test_cli_report_reader_rejects_special_oversize_and_hardlink(self):
        oversized = self.root / "oversized.json"
        oversized.write_bytes(b"x" * (resolver.MAX_REPORT_BYTES + 1))
        oversized.chmod(0o600)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverError, "private-file"
        ):
            resolver_cli._read_private_report(str(oversized))

        report = self.root / "report.json"
        report.write_bytes(self.report_payload)
        report.chmod(0o600)
        hardlink = self.root / "report-hardlink.json"
        os.link(report, hardlink)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverError, "private-file"
        ):
            resolver_cli._read_private_report(str(report))

        fifo = self.root / "report.fifo"
        os.mkfifo(fifo, mode=0o600)
        with self.assertRaisesRegex(
            resolver.H3PromptRewriterWheelResolverError, "private-file"
        ):
            resolver_cli._read_private_report(str(fifo))

    def test_cli_report_reader_wraps_oserror_and_detects_read_race(self):
        report = self.root / "private-report.json"
        report.write_bytes(self.report_payload)
        report.chmod(0o600)
        with (
            mock.patch.object(
                resolver_cli.os, "open", side_effect=OSError("private detail")
            ),
            self.assertRaisesRegex(
                resolver.H3PromptRewriterWheelResolverError,
                "private-file read failed",
            ) as raised,
        ):
            resolver_cli._read_private_report(str(report))
        self.assertNotIn("private detail", str(raised.exception))

        original_read = os.read
        changed = False

        def racing_read(descriptor, count):
            nonlocal changed
            if not changed:
                changed = True
                with report.open("ab") as stream:
                    stream.write(b"x")
            return original_read(descriptor, count)

        with (
            mock.patch.object(resolver_cli.os, "read", side_effect=racing_read),
            self.assertRaisesRegex(
                resolver.H3PromptRewriterWheelResolverError,
                "grew during read|changed during read",
            ),
        ):
            resolver_cli._read_private_report(str(report))

    def test_cli_applies_parent_limits_before_report_read(self):
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        order = []

        def limits():
            order.append("limits")

        def read_report(_path):
            self.assertEqual(order, ["limits"])
            order.append("read")
            return self.report_payload

        manifest = {
            "wheel_count": len(PACKAGES),
            "total_size_bytes": sum(
                row["wheel"]["size_bytes"] for row in self.report["packages"]
            ),
        }
        with (
            mock.patch.object(
                resolver_cli,
                "apply_h3_prompt_rewriter_parent_limits",
                side_effect=limits,
            ),
            mock.patch.object(
                resolver_cli, "_read_private_report", side_effect=read_report
            ),
            mock.patch.object(
                resolver_cli,
                "execute_h3_prompt_rewriter_wheel_resolution",
                return_value=manifest,
            ),
            mock.patch("builtins.print"),
        ):
            result = resolver_cli.main(
                [
                    "--execute",
                    "--expected-plan-sha256",
                    plan.sha256,
                    "--resolution-report",
                    "/private/report.json",
                    "--expected-resolution-report-sha256",
                    self.report_sha,
                    "--private-feature-root",
                    "/private/feature",
                    "--staging-root",
                    "/private/feature/stage",
                    "--python-executable",
                    sys.executable,
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(order, ["limits", "read"])


if __name__ == "__main__":
    unittest.main()
