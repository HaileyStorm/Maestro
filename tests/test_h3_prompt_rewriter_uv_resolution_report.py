"""Source-only tests for the pinned-uv H3 resolution-report producer."""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_prompt_rewriter_dependency_closure as closure
from services import h3_prompt_rewriter_uv_resolution_report as producer
from services import h3_prompt_rewriter_wheel_resolver as resolver

from scripts import produce_h3_prompt_rewriter_resolution_report as report_cli

PACKAGE_ROWS = (
    ("accelerate", "1.12.0", "py3-none-any", ()),
    ("nvidia-cublas-cu12", "12.8.4.1", "py3-none-manylinux_2_28_x86_64", ()),
    ("peft", "0.20.0", "py3-none-any", ()),
    ("pillow", "12.2.0", "cp312-cp312-manylinux_2_28_x86_64", ()),
    ("safetensors", "0.8.0", "cp312-cp312-manylinux_2_28_x86_64", ()),
    ("tokenizers", "0.22.1", "cp312-cp312-manylinux_2_28_x86_64", ()),
    (
        "torch",
        "2.10.0+cu128",
        "cp312-cp312-manylinux_2_28_x86_64",
        ("nvidia-cublas-cu12",),
    ),
    (
        "torchvision",
        "0.25.0+cu128",
        "cp312-cp312-manylinux_2_28_x86_64",
        ("torch",),
    ),
    ("transformers", "4.57.1", "py3-none-any", ()),
)


def _wheel_filename(name: str, version: str, tag: str) -> str:
    return f"{name.replace('-', '_')}-{version}-{tag}.whl"


def _pylock(
    *,
    mutate_package=None,
    extra_packages: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (),
    optional_empty_header: bool = False,
) -> bytes:
    lines = [
        'lock-version = "1.0"',
        'created-by = "uv"',
        'requires-python = ">=3.12.14,<3.13"',
        "",
    ]
    if optional_empty_header:
        lines[3:3] = [
            "environments = []",
            "extras = []",
            "dependency-groups = []",
            "default-groups = []",
        ]
    for ordinal, row in enumerate(PACKAGE_ROWS + extra_packages, start=1):
        name, version, tag, dependencies = row
        filename = _wheel_filename(name, version, tag)
        index = (
            resolver.PYTORCH_INDEX
            if name in {"torch", "torchvision"} or name.startswith("nvidia-")
            else resolver.PYPI_INDEX
        )
        url = (
            "https://download.pytorch.org/whl/cu128/"
            + urllib.parse.quote(filename, safe="-._~")
            if index == resolver.PYTORCH_INDEX
            else f"https://files.pythonhosted.org/packages/aa/{ordinal:02d}/{filename}"
        )
        package = {
            "name": name,
            "version": version,
            "index": index,
            "dependencies": list(dependencies),
            "wheels": [
                {
                    "name": filename,
                    "url": url,
                    "size": ordinal * 100,
                    "hash": hashlib.sha256(filename.encode()).hexdigest(),
                }
            ],
        }
        if mutate_package is not None:
            package = mutate_package(dict(package))
        lines.extend(
            [
                "[[packages]]",
                f"name = {json.dumps(package['name'])}",
                f"version = {json.dumps(package['version'])}",
                f"index = {json.dumps(package['index'])}",
            ]
        )
        if package.get("marker") is not None:
            lines.append(f"marker = {json.dumps(package['marker'])}")
        if package.get("sdist") is not None:
            lines.append(
                'sdist = {name = "bad.tar.gz", '
                'url = "https://files.pythonhosted.org/packages/bad.tar.gz"}'
            )
        dependency_values = []
        for dependency in package["dependencies"]:
            if isinstance(dependency, dict):
                values = ", ".join(
                    f"{key} = {json.dumps(value)}" for key, value in dependency.items()
                )
                dependency_values.append("{" + values + "}")
            else:
                dependency_values.append(f"{{name = {json.dumps(dependency)}}}")
        lines.append("dependencies = [" + ", ".join(dependency_values) + "]")
        wheels = []
        for wheel in package["wheels"]:
            wheels.append(
                "{name = "
                + json.dumps(wheel["name"])
                + ", url = "
                + json.dumps(wheel["url"])
                + f", size = {wheel['size']}, hashes = {{sha256 = "
                + json.dumps(wheel["hash"])
                + "}}"
            )
        lines.append("wheels = [" + ", ".join(wheels) + "]")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


# Bounded PEP 751 shape documented by uv 0.9.26; this is deliberately not
# represented as captured live resolution output.
UV_0926_DOCUMENTED_GOLDEN_PYLOCK = _pylock(optional_empty_header=True)


class _FakeProcess:
    pid = 9876

    def __init__(self, returncode: int = 0):
        self.returncode = returncode

    def wait(self, timeout):
        return self.returncode


class _SequencedProcess(_FakeProcess):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.wait_calls = 0

    def wait(self, timeout):
        self.wait_calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else self.returncode
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _ProcessFactory:
    def __init__(self, process, payload: bytes | None = None, grow_bytes: int = 0):
        self.process = process
        self.payload = payload
        self.grow_bytes = grow_bytes

    def __call__(self, command, **kwargs):
        destination = Path(command[command.index("--output-file") + 1])
        if self.payload is not None:
            destination.write_bytes(self.payload)
            destination.chmod(0o600)
        if self.grow_bytes:
            cache_file = Path(kwargs["cwd"]) / "cache" / "growth.bin"
            cache_file.write_bytes(b"x" * self.grow_bytes)
            cache_file.chmod(0o600)
        return self.process


class _GroupSignals:
    def __init__(self, *, survive_term: bool = True):
        self.alive = True
        self.survive_term = survive_term
        self.calls: list[int] = []

    def __call__(self, pid, signal_number):
        self.calls.append(signal_number)
        if signal_number == 0:
            if not self.alive:
                raise ProcessLookupError
            return
        if signal_number == producer.signal.SIGTERM and not self.survive_term:
            self.alive = False
        if signal_number == producer.signal.SIGKILL:
            self.alive = False


class _FakeUv:
    def __init__(self, payload: bytes, *, returncode: int = 0):
        self.payload = payload
        self.returncode = returncode
        self.commands: list[list[str]] = []
        self.kwargs = None

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        self.kwargs = kwargs
        destination = Path(command[command.index("--output-file") + 1])
        destination.write_bytes(self.payload)
        destination.chmod(0o600)
        return _FakeProcess(self.returncode)


class H3PromptRewriterUvResolutionReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.feature = self.root / "feature"
        self.feature.mkdir(mode=0o700)
        self.state = self.feature / "resolution"
        self.uv = self.root / "uv"
        self.uv.write_bytes(b"fake uv")
        self.uv.chmod(0o700)
        info = self.uv.stat()
        self.uv_receipt = producer._UvReceipt(
            path=self.uv,
            sha256=producer.PINNED_UV_SHA256,
            size_bytes=producer.PINNED_UV_SIZE_BYTES,
            stat_identity=producer._stat_identity(info),
        )
        self.python = self.root / "python3.12"
        self.python.write_bytes(b"fake Python")
        self.python.chmod(0o700)
        python_info = self.python.stat()
        self.python_receipt = producer._PythonReceipt(
            path=self.python,
            sha256=hashlib.sha256(b"fake Python").hexdigest(),
            size_bytes=len(b"fake Python"),
            version="3.12.3",
            stat_identity=producer._stat_identity(python_info),
        )

    def plan(self, **kwargs):
        with (
            mock.patch.object(producer, "_inspect_uv", return_value=self.uv_receipt),
            mock.patch.object(
                producer, "_inspect_python", return_value=self.python_receipt
            ),
        ):
            return producer.build_h3_prompt_rewriter_uv_resolution_plan(
                self.uv, self.python, **kwargs
            )

    def execute(self, fake, *, plan=None, monotonic=lambda: 0.0):
        plan = plan or self.plan()
        with (
            mock.patch.object(producer, "_inspect_uv", return_value=self.uv_receipt),
            mock.patch.object(
                producer, "_inspect_python", return_value=self.python_receipt
            ),
        ):
            return producer.execute_h3_prompt_rewriter_uv_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                expected_input_sha256=hashlib.sha256(
                    producer.reviewed_requirements_input_bytes()
                ).hexdigest(),
                expected_uv_sha256=producer.PINNED_UV_SHA256,
                expected_python_sha256=self.python_receipt.sha256,
                uv_executable=self.uv,
                python_executable=self.python,
                private_feature_root=self.feature,
                state_root=self.state,
                process_factory=fake,
                monotonic=monotonic,
            )

    def test_plan_is_path_free_network_free_and_exactly_bound(self):
        with (
            mock.patch.object(producer, "_inspect_uv", return_value=self.uv_receipt),
            mock.patch.object(
                producer, "_inspect_python", return_value=self.python_receipt
            ),
            mock.patch.object(socket, "socket", side_effect=AssertionError("network")),
            mock.patch.object(
                subprocess, "Popen", side_effect=AssertionError("process")
            ),
        ):
            first = producer.build_h3_prompt_rewriter_uv_resolution_plan(
                self.uv, self.python
            )
            second = producer.build_h3_prompt_rewriter_uv_resolution_plan(
                self.uv, self.python
            )
        self.assertEqual(first.sha256, second.sha256)
        self.assertFalse(first.document["planning_mutation"])
        self.assertFalse(first.document["planning_network"])
        self.assertTrue(first.document["execution_requires_network"])
        self.assertTrue(first.document["execution_writes_private_state"])
        self.assertTrue(first.document["execution_requires_expected_python_sha256"])
        self.assertNotIn("mutation", first.document)
        self.assertNotIn("network", first.document)
        self.assertEqual(first.document["uv"]["version"], "0.9.26")
        self.assertEqual(first.document["bootstrap_python"]["version"], "3.12.3")
        self.assertEqual(
            first.document["bootstrap_python"]["canonical_path_sha256"],
            hashlib.sha256(str(self.python).encode()).hexdigest(),
        )
        self.assertEqual(first.document["target"]["python_full_version"], "3.12.14")
        self.assertEqual(first.document["target"]["python_abi"], "cp312")
        self.assertEqual(first.document["resources"]["cpu_cores"], 2)
        self.assertEqual(first.document["resources"]["nice"], 15)
        self.assertEqual(first.document["resources"]["metadata_byte_cap"], 1024**3)
        self.assertEqual(
            first.document["resources"]["child_file_size_bytes"], 16 * 1024**2
        )
        self.assertEqual(
            first.document["resources"]["state_depth_cap"],
            producer.MAX_STATE_DEPTH,
        )
        cache_lock_contract = first.document["resources"][
            "uv_internal_lock_compatibility"
        ]
        self.assertEqual(
            cache_lock_contract["relative_paths"],
            [
                "cache/.lock",
                "home/.local/share/uv/credentials/credentials.toml.lock",
            ],
        )
        self.assertEqual(cache_lock_contract["mode"], "0666")
        self.assertEqual(
            cache_lock_contract["maximum_bytes"],
            producer.MAX_UV_INTERNAL_LOCK_BYTES,
        )
        self.assertTrue(cache_lock_contract["owner_private_ancestors"])
        self.assertEqual(first.document["resolver"]["only_binary"], ":all:")
        self.assertEqual(
            first.document["resolver"]["candidate_output_name"],
            producer.PYLOCK_CANDIDATE_NAME,
        )
        self.assertEqual(
            first.document["resolver"]["canonical_output_name"],
            producer.PYLOCK_NAME,
        )
        self.assertFalse(first.document["resolver"]["no_build_flag_used"])
        self.assertFalse(first.document["resolver"]["source_distributions_permitted"])
        self.assertFalse(first.document["resolver"]["builds_permitted"])
        self.assertNotIn(str(self.root), json.dumps(first.document, sort_keys=True))

    def test_documented_uv_0926_golden_variant_is_accepted(self):
        report = producer.parse_uv_pylock_to_wheel_report(
            UV_0926_DOCUMENTED_GOLDEN_PYLOCK
        )
        self.assertEqual(len(report["packages"]), len(PACKAGE_ROWS))

    def test_bootstrap_python_rejects_symlink_and_identity_drift(self):
        symlink = self.root / "python-link"
        symlink.symlink_to(self.python)
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._inspect_python(symlink)

        def mutate_during_version_check(*_args, **_kwargs):
            self.python.write_bytes(b"changed Python")
            self.python.chmod(0o700)
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"Python 3.12.3\n", stderr=b""
            )

        with (
            mock.patch.object(
                producer.subprocess,
                "run",
                side_effect=mutate_during_version_check,
            ),
            self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
        ):
            producer._inspect_python(self.python)

    def test_bootstrap_python_same_bytes_at_another_path_cannot_retarget(self):
        plan = self.plan()
        moved = self.root / "other-python3.12"
        moved.write_bytes(b"fake Python")
        moved.chmod(0o700)
        moved_receipt = producer._PythonReceipt(
            path=moved,
            sha256=self.python_receipt.sha256,
            size_bytes=self.python_receipt.size_bytes,
            version=self.python_receipt.version,
            stat_identity=producer._stat_identity(moved.stat()),
        )
        with (
            mock.patch.object(producer, "_inspect_uv", return_value=self.uv_receipt),
            mock.patch.object(producer, "_inspect_python", return_value=moved_receipt),
            self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
        ):
            producer.execute_h3_prompt_rewriter_uv_resolution(
                plan,
                expected_plan_sha256=plan.sha256,
                expected_input_sha256=hashlib.sha256(
                    producer.reviewed_requirements_input_bytes()
                ).hexdigest(),
                expected_uv_sha256=producer.PINNED_UV_SHA256,
                expected_python_sha256=self.python_receipt.sha256,
                uv_executable=self.uv,
                python_executable=moved,
                private_feature_root=self.feature,
                state_root=self.state,
                process_factory=_FakeUv(_pylock()),
            )

    def test_parse_emits_exact_downstream_report_schema(self):
        report = producer.parse_uv_pylock_to_wheel_report(_pylock())
        self.assertEqual(report["schema"], resolver.WHEEL_RESOLUTION_REPORT_SCHEMA)
        self.assertEqual(report["root_requirements"], list(closure.ROOT_REQUIREMENTS))
        self.assertEqual(
            [item["name"] for item in report["packages"]],
            sorted(item[0] for item in PACKAGE_ROWS),
        )
        torch = next(item for item in report["packages"] if item["name"] == "torch")
        self.assertEqual(torch["wheel"]["index"], resolver.PYTORCH_INDEX)
        self.assertEqual(torch["dependencies"], ["nvidia-cublas-cu12==12.8.4.1"])

    def test_execution_uses_one_scrubbed_bounded_fake_uv_and_private_outputs(self):
        fake = _FakeUv(_pylock())
        result = self.execute(fake)
        self.assertEqual(len(fake.commands), 1)
        command = fake.commands[0]
        for option in (
            "--no-config",
            "--no-python-downloads",
            "--no-managed-python",
            "--only-binary",
            "--no-sources",
        ):
            self.assertIn(option, command)
        self.assertNotIn("--no-build", command)
        self.assertEqual(command[command.index("--only-binary") + 1], ":all:")
        self.assertEqual(command[command.index("--python") + 1], str(self.python))
        self.assertEqual(
            Path(command[command.index("--output-file") + 1]).name,
            producer.PYLOCK_CANDIDATE_NAME,
        )
        self.assertEqual(command[command.index("--index-strategy") + 1], "first-index")
        self.assertEqual(command[command.index("--python-version") + 1], "3.12.14")
        self.assertEqual(
            command[command.index("--python-platform") + 1],
            "x86_64-manylinux_2_28",
        )
        self.assertFalse(fake.kwargs["shell"])
        self.assertTrue(fake.kwargs["start_new_session"])
        self.assertEqual(fake.kwargs["preexec_fn"].__name__, "_apply_child_limits")
        environment = fake.kwargs["env"]
        self.assertNotIn("PATH", environment)
        self.assertFalse(any(key.startswith("PIP_") for key in environment))
        self.assertFalse(any("PROXY" in key for key in environment))
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["NVIDIA_VISIBLE_DEVICES"], "void")
        self.assertEqual(result["package_count"], len(PACKAGE_ROWS))
        for name in (
            producer.INPUT_NAME,
            producer.PYLOCK_NAME,
            producer.REPORT_NAME,
            producer.PROVENANCE_NAME,
        ):
            path = self.state / name
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
        provenance = json.loads((self.state / producer.PROVENANCE_NAME).read_text())
        self.assertEqual(provenance["status"], "unreviewed_candidate")
        self.assertFalse(provenance["installation_authorized"])

    def test_execution_requires_all_four_expected_hashes(self):
        plan = self.plan()
        for field in ("plan", "input", "uv", "python"):
            values = {
                "expected_plan_sha256": plan.sha256,
                "expected_input_sha256": hashlib.sha256(
                    producer.reviewed_requirements_input_bytes()
                ).hexdigest(),
                "expected_uv_sha256": producer.PINNED_UV_SHA256,
                "expected_python_sha256": self.python_receipt.sha256,
            }
            values[f"expected_{field}_sha256"] = "0" * 64
            with (
                self.subTest(field=field),
                mock.patch.object(
                    producer, "_inspect_uv", return_value=self.uv_receipt
                ),
                mock.patch.object(
                    producer, "_inspect_python", return_value=self.python_receipt
                ),
                self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
            ):
                producer.execute_h3_prompt_rewriter_uv_resolution(
                    plan,
                    **values,
                    uv_executable=self.uv,
                    python_executable=self.python,
                    private_feature_root=self.feature,
                    state_root=self.state,
                    process_factory=_FakeUv(_pylock()),
                )

    def test_rejects_sdist_direct_marker_prerelease_and_wrong_registry(self):
        mutations = {
            "sdist": lambda item: {**item, "sdist": True},
            "marker": lambda item: {**item, "marker": "sys_platform == 'linux'"},
            "dependency_marker": lambda item: {
                **item,
                "dependencies": (
                    [{"name": "torch", "marker": "python_version == '3.12'"}]
                    if item["name"] == "torchvision"
                    else item["dependencies"]
                ),
            },
            "prerelease": lambda item: (
                {**item, "version": "1.12.0rc1"}
                if item["name"] == "accelerate"
                else item
            ),
            "wrong_registry": lambda item: (
                {**item, "index": resolver.PYPI_INDEX}
                if item["name"] == "torch"
                else item
            ),
        }
        for name, mutation in mutations.items():
            with (
                self.subTest(name=name),
                self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
            ):
                producer.parse_uv_pylock_to_wheel_report(
                    _pylock(mutate_package=mutation)
                )

    def test_rejects_ambiguous_and_too_new_wheels(self):
        def ambiguous(item):
            if item["name"] == "accelerate":
                item["wheels"] = item["wheels"] * 2
            return item

        def too_new(item):
            if item["name"] == "pillow":
                wheel = item["wheels"][0]
                wheel["name"] = wheel["name"].replace(
                    "manylinux_2_28", "manylinux_2_35"
                )
                wheel["url"] = wheel["url"].replace("manylinux_2_28", "manylinux_2_35")
            return item

        for name, mutation in (("ambiguous", ambiguous), ("too_new", too_new)):
            with (
                self.subTest(name=name),
                self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
            ):
                producer.parse_uv_pylock_to_wheel_report(
                    _pylock(mutate_package=mutation)
                )

    def test_rejects_duplicate_cycle_unreachable_and_unresolved(self):
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer.parse_uv_pylock_to_wheel_report(
                _pylock(extra_packages=(PACKAGE_ROWS[0],))
            )

        def cycle(item):
            if item["name"] == "nvidia-cublas-cu12":
                item["dependencies"] = ["torch"]
            return item

        def unresolved(item):
            if item["name"] == "accelerate":
                item["dependencies"] = ["not-present"]
            return item

        for name, payload in (
            ("cycle", _pylock(mutate_package=cycle)),
            ("unresolved", _pylock(mutate_package=unresolved)),
            (
                "unreachable",
                _pylock(extra_packages=(("orphan", "1.0", "py3-none-any", ()),)),
            ),
        ):
            with (
                self.subTest(name=name),
                self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
            ):
                producer.parse_uv_pylock_to_wheel_report(payload)

    def test_failed_fake_uv_preserves_temporary_state_and_emits_no_report(self):
        fake = _FakeUv(_pylock(), returncode=9)
        with self.assertRaises(producer.H3PromptRewriterUvResolutionExecutionError):
            self.execute(fake)
        self.assertTrue((self.state / producer.PYLOCK_CANDIDATE_NAME).exists())
        self.assertFalse((self.state / producer.REPORT_NAME).exists())
        failure = json.loads((self.state / producer.FAILURE_NAME).read_text())
        self.assertEqual(failure["phase"], "process_nonzero")
        self.assertTrue(failure["process_spawned"])
        self.assertEqual(failure["returncode"], 9)
        self.assertFalse(failure["validated_pylock_candidate_observed"])
        self.assertEqual(failure["failure_category"], "execution_boundary")
        self.assertFalse(failure["retry_authorized"])
        failure_path = self.state / producer.FAILURE_NAME
        self.assertEqual(failure_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(failure_path.stat().st_nlink, 1)

    def test_terminal_failure_receipt_requires_fresh_state_for_every_retry(self):
        plan = self.plan()
        with self.assertRaises(producer.H3PromptRewriterUvResolutionExecutionError):
            self.execute(_FakeUv(_pylock(), returncode=9), plan=plan)
        failure_path = self.state / producer.FAILURE_NAME
        preserved_receipt = failure_path.read_bytes()

        for label, returncode in (("success", 0), ("different_failure", 17)):
            uv_inspection = mock.Mock(side_effect=AssertionError("uv inspection"))
            python_inspection = mock.Mock(
                side_effect=AssertionError("Python inspection")
            )
            version_probe = mock.Mock(side_effect=AssertionError("version probe"))
            process_factory = mock.Mock(wraps=_FakeUv(_pylock(), returncode=returncode))
            with (
                self.subTest(label=label),
                mock.patch.object(producer, "_inspect_uv", uv_inspection),
                mock.patch.object(producer, "_inspect_python", python_inspection),
                mock.patch.object(producer.subprocess, "run", version_probe),
                self.assertRaises(
                    producer.H3PromptRewriterUvResolutionSecurityError
                ) as raised,
            ):
                producer.execute_h3_prompt_rewriter_uv_resolution(
                    plan,
                    expected_plan_sha256=plan.sha256,
                    expected_input_sha256=hashlib.sha256(
                        producer.reviewed_requirements_input_bytes()
                    ).hexdigest(),
                    expected_uv_sha256=producer.PINNED_UV_SHA256,
                    expected_python_sha256=self.python_receipt.sha256,
                    uv_executable=self.uv,
                    python_executable=self.python,
                    private_feature_root=self.feature,
                    state_root=self.state,
                    process_factory=process_factory,
                )
            self.assertIn("fresh private state root", str(raised.exception))
            uv_inspection.assert_not_called()
            python_inspection.assert_not_called()
            version_probe.assert_not_called()
            process_factory.assert_not_called()
            self.assertEqual(failure_path.read_bytes(), preserved_receipt)

    def test_popen_failure_emits_content_free_private_terminal_receipt(self):
        def broken_factory(*_args, **_kwargs):
            raise OSError("private popen detail")

        with self.assertRaises(
            producer.H3PromptRewriterUvResolutionExecutionError
        ) as raised:
            self.execute(broken_factory)
        self.assertNotIn("private popen detail", str(raised.exception))
        failure_path = self.state / producer.FAILURE_NAME
        failure_text = failure_path.read_text()
        failure = json.loads(failure_text)
        self.assertNotIn("private popen detail", failure_text)
        self.assertEqual(failure["phase"], "spawn")
        self.assertFalse(failure["process_spawned"])
        self.assertIsNone(failure["returncode"])
        self.assertFalse(failure["validated_pylock_candidate_observed"])
        self.assertEqual(failure["failure_category"], "execution_boundary")

    def test_invalid_pylock_emits_schema_phase_private_terminal_receipt(self):
        fake = _FakeUv(b"not valid toml")
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            self.execute(fake)
        failure = json.loads((self.state / producer.FAILURE_NAME).read_text())
        self.assertEqual(failure["phase"], "pylock_schema")
        self.assertTrue(failure["process_spawned"])
        self.assertEqual(failure["returncode"], 0)
        self.assertTrue(failure["validated_pylock_candidate_observed"])
        self.assertEqual(failure["failure_category"], "security_boundary")

    def test_metadata_cap_kills_process_group_before_evidence(self):
        plan = self.plan(metadata_byte_cap=512)
        process = _SequencedProcess([0, 0, 0])
        factory = _ProcessFactory(process, grow_bytes=1024)
        signals = _GroupSignals(survive_term=True)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
        ):
            self.execute(factory, plan=plan)
        self.assertIn(producer.signal.SIGTERM, signals.calls)
        self.assertIn(producer.signal.SIGKILL, signals.calls)
        self.assertFalse(signals.alive)
        self.assertFalse((self.state / producer.REPORT_NAME).exists())

    def test_state_monitor_rejects_symlink_and_child_limit_binds_file_size(self):
        process = _SequencedProcess([0, 0, 0])

        def symlink_factory(_command, **kwargs):
            (Path(kwargs["cwd"]) / "cache" / "escape").symlink_to(self.uv)
            return process

        signals = _GroupSignals(survive_term=True)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
        ):
            self.execute(symlink_factory)
        self.assertIn(producer.signal.SIGKILL, signals.calls)

        limits = []
        library = types.SimpleNamespace(syscall=lambda *_args: 0)
        with (
            mock.patch.object(producer.os, "umask"),
            mock.patch.object(producer.os, "nice"),
            mock.patch.object(producer.os, "sched_getaffinity", return_value={0, 1}),
            mock.patch.object(
                producer.resource,
                "setrlimit",
                side_effect=lambda kind, value: limits.append((kind, value)),
            ),
            mock.patch.object(producer.ctypes, "CDLL", return_value=library),
        ):
            producer._apply_child_limits()
        self.assertIn(
            (
                producer.resource.RLIMIT_FSIZE,
                (producer.MAX_CHILD_FILE_BYTES, producer.MAX_CHILD_FILE_BYTES),
            ),
            limits,
        )

    def test_growing_private_file_is_accepted_accounted_and_capped(self):
        producer._layout(self.feature, self.state)
        growing = self.state / "cache" / "growing.bin"
        growing.write_bytes(b"a" * 100)
        growing.chmod(0o600)
        real_open = producer.os.open
        appended = False

        def append_before_open(path, flags, *args, **kwargs):
            nonlocal appended
            if path == "growing.bin" and not appended:
                appended = True
                with growing.open("ab") as stream:
                    stream.write(b"b" * 200)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(producer.os, "open", side_effect=append_before_open):
            usage = producer._scan_private_state(
                self.state,
                byte_cap=400,
                entry_cap=100,
            )
        self.assertTrue(appended)
        self.assertGreaterEqual(usage.bytes, 300)
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._scan_private_state(
                self.state,
                byte_cap=299,
                entry_cap=100,
            )

    def test_exact_bounded_uv_cache_lock_is_accepted_and_accounted(self):
        producer._layout(self.feature, self.state)
        lock = self.state / "cache" / ".lock"
        payload = b"uv-lock"
        lock.write_bytes(b"")
        lock.chmod(0o666)
        real_open = producer.os.open
        appended = False

        def append_before_open(path, flags, *args, **kwargs):
            nonlocal appended
            if path == ".lock" and not appended:
                appended = True
                with lock.open("ab") as stream:
                    stream.write(payload)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(producer.os, "open", side_effect=append_before_open):
            usage = producer._scan_private_state(
                self.state,
                byte_cap=len(payload),
                entry_cap=100,
            )
        self.assertTrue(appended)
        self.assertGreaterEqual(usage.bytes, len(payload))
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._scan_private_state(
                self.state,
                byte_cap=len(payload) - 1,
                entry_cap=100,
            )
        lock.write_bytes(b"x" * (producer.MAX_UV_INTERNAL_LOCK_BYTES + 1))
        lock.chmod(0o666)
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._scan_private_state(
                self.state,
                byte_cap=producer.MAX_UV_INTERNAL_LOCK_BYTES + 1,
                entry_cap=100,
            )

    def test_uv_cache_lock_exception_does_not_expand_writable_surface(self):
        cases = (
            ("same_name_elsewhere", Path("home/.lock"), 0o666),
            ("nested_same_name", Path("cache/nested/.lock"), 0o666),
            ("different_cache_name", Path("cache/other.lock"), 0o666),
            ("allowed_top_level_name", Path(producer.INPUT_NAME), 0o666),
            ("wrong_exact_lock_mode", Path("cache/.lock"), 0o664),
        )
        for ordinal, (label, relative, mode) in enumerate(cases):
            state = self.feature / f"lock-case-{ordinal}"
            producer._layout(self.feature, state)
            candidate = state / relative
            candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            candidate.write_bytes(b"")
            candidate.chmod(mode)
            with (
                self.subTest(label=label),
                self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
            ):
                producer._scan_private_state(
                    state,
                    byte_cap=1024,
                    entry_cap=100,
                )

        symlink_state = self.feature / "lock-case-symlink"
        producer._layout(self.feature, symlink_state)
        (symlink_state / "cache" / ".lock").symlink_to(self.uv)
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._scan_private_state(
                symlink_state,
                byte_cap=1024,
                entry_cap=100,
            )

        ancestor_state = self.feature / "lock-case-ancestor"
        producer._layout(self.feature, ancestor_state)
        ancestor_lock = ancestor_state / "cache" / ".lock"
        ancestor_lock.write_bytes(b"")
        ancestor_lock.chmod(0o666)
        (ancestor_state / "cache").chmod(0o755)
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._scan_private_state(
                ancestor_state,
                byte_cap=1024,
                entry_cap=100,
            )

        linked_state = self.feature / "lock-case-hardlink"
        producer._layout(self.feature, linked_state)
        linked_lock = linked_state / "cache" / ".lock"
        linked_lock.write_bytes(b"")
        linked_lock.chmod(0o666)
        os.link(linked_lock, linked_state / "cache" / "alias")
        with self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError):
            producer._scan_private_state(
                linked_state,
                byte_cap=1024,
                entry_cap=100,
            )

    def test_exact_uv_credentials_lock_is_accepted_but_variants_are_rejected(self):
        producer._layout(self.feature, self.state)
        baseline = producer._scan_private_state(
            self.state,
            byte_cap=1024,
            entry_cap=100,
        )
        credentials_parent = self.state / "home"
        for component in (".local", "share", "uv", "credentials"):
            credentials_parent = credentials_parent / component
            credentials_parent.mkdir(mode=0o700)
        credentials_lock = credentials_parent / "credentials.toml.lock"
        payload = b"uv-credentials-lock"
        credentials_lock.write_bytes(payload)
        credentials_lock.chmod(0o666)
        usage = producer._scan_private_state(
            self.state,
            byte_cap=len(payload),
            entry_cap=100,
        )
        self.assertGreaterEqual(usage.bytes, len(payload))
        self.assertGreater(usage.entries, baseline.entries)

        variants = (
            Path("home/.local/share/uv/credentials.toml.lock"),
            Path("home/.local/share/uv/credentials/credentials.toml.lock.alias"),
            Path("home/.local/share/uv/Credentials/credentials.toml.lock"),
            Path("home/.local/share/uv/credentials/sub/credentials.toml.lock"),
            Path("cache/credentials.toml.lock"),
        )
        for ordinal, relative in enumerate(variants):
            state = self.feature / f"credentials-lock-variant-{ordinal}"
            producer._layout(self.feature, state)
            candidate = state / relative
            parent = state
            for component in relative.parts[:-1]:
                parent = parent / component
                parent.mkdir(mode=0o700, exist_ok=True)
            candidate.write_bytes(b"")
            candidate.chmod(0o666)
            with (
                self.subTest(relative=str(relative)),
                self.assertRaises(producer.H3PromptRewriterUvResolutionSecurityError),
            ):
                producer._scan_private_state(
                    state,
                    byte_cap=1024,
                    entry_cap=100,
                )

    def test_atomic_rename_disappearance_retries_within_bound(self):
        producer._layout(self.feature, self.state)
        candidate = self.state / "cache" / "atomic.tmp"
        candidate.write_bytes(b"candidate")
        candidate.chmod(0o600)
        real_open = producer.os.open
        disappeared = False

        def disappear_once(path, flags, *args, **kwargs):
            nonlocal disappeared
            if path == "atomic.tmp" and not disappeared:
                disappeared = True
                raise FileNotFoundError
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(producer.os, "open", side_effect=disappear_once):
            usage = producer._scan_private_state(
                self.state,
                byte_cap=1024,
                entry_cap=100,
            )
        self.assertTrue(disappeared)
        self.assertGreaterEqual(usage.bytes, len(b"candidate"))

    def test_deep_private_tree_is_rejected_content_free(self):
        producer._layout(self.feature, self.state)
        directory = self.state / "cache"
        for ordinal in range(producer.MAX_STATE_DEPTH + 2):
            directory = directory / f"d{ordinal}"
            directory.mkdir(mode=0o700)
        with self.assertRaises(
            producer.H3PromptRewriterUvResolutionSecurityError
        ) as raised:
            producer._scan_private_state(
                self.state,
                byte_cap=1024,
                entry_cap=100,
            )
        self.assertEqual(
            str(raised.exception),
            "private resolution state exceeds its depth cap",
        )

    def test_nonzero_with_descendant_forces_term_kill_and_final_reap(self):
        process = _SequencedProcess([9, 9, 9, 9])
        factory = _ProcessFactory(process, payload=_pylock())
        signals = _GroupSignals(survive_term=True)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            self.assertRaises(producer.H3PromptRewriterUvResolutionExecutionError),
        ):
            self.execute(factory)
        self.assertIn(producer.signal.SIGTERM, signals.calls)
        self.assertIn(producer.signal.SIGKILL, signals.calls)
        self.assertGreaterEqual(process.wait_calls, 4)
        self.assertFalse(signals.alive)

    def test_late_success_is_rejected_and_group_is_cleaned(self):
        values = iter((0.0, 0.0, 2.0))
        process = _SequencedProcess([0, 0, 0, 0])
        factory = _ProcessFactory(process, payload=_pylock())
        signals = _GroupSignals(survive_term=True)
        plan = self.plan(deadline_seconds=1)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            self.assertRaises(producer.H3PromptRewriterUvResolutionExecutionError),
        ):
            self.execute(factory, plan=plan, monotonic=lambda: next(values))
        self.assertIn(producer.signal.SIGKILL, signals.calls)
        self.assertFalse((self.state / producer.REPORT_NAME).exists())

    def test_running_process_is_polled_until_deadline_then_killed(self):
        values = iter((0.0, 0.0, 2.0))
        process = _SequencedProcess([subprocess.TimeoutExpired("uv", 0.25), 0, 0, 0])
        factory = _ProcessFactory(process, payload=_pylock())
        signals = _GroupSignals(survive_term=True)
        plan = self.plan(deadline_seconds=1)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            self.assertRaises(producer.H3PromptRewriterUvResolutionExecutionError),
        ):
            self.execute(factory, plan=plan, monotonic=lambda: next(values))
        self.assertGreaterEqual(process.wait_calls, 4)
        self.assertIn(producer.signal.SIGTERM, signals.calls)
        self.assertIn(producer.signal.SIGKILL, signals.calls)
        self.assertFalse(signals.alive)

    def test_popen_and_wait_errors_are_content_free_and_cleanup_wait_descendants(self):
        def broken_factory(*_args, **_kwargs):
            raise OSError("private popen detail")

        with self.assertRaises(
            producer.H3PromptRewriterUvResolutionExecutionError
        ) as raised:
            self.execute(broken_factory)
        self.assertNotIn("private popen detail", str(raised.exception))

        self.state = self.feature / "wait-resolution"
        process = _SequencedProcess([OSError("private wait detail"), 0, 0, 0])
        factory = _ProcessFactory(process, payload=_pylock())
        signals = _GroupSignals(survive_term=True)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            self.assertRaises(
                producer.H3PromptRewriterUvResolutionExecutionError
            ) as raised,
        ):
            self.execute(factory)
        self.assertNotIn("private wait detail", str(raised.exception))
        self.assertIn(producer.signal.SIGKILL, signals.calls)
        self.assertFalse(signals.alive)

    def test_postprocess_error_still_cleans_descendants(self):
        process = _SequencedProcess([0, 0, 0, 0, 0, 0])
        factory = _ProcessFactory(process, payload=_pylock())
        signals = _GroupSignals(survive_term=True)
        with (
            mock.patch.object(producer.os, "killpg", side_effect=signals),
            mock.patch.object(
                producer,
                "parse_uv_pylock_to_wheel_report",
                side_effect=OSError("private parse detail"),
            ),
            self.assertRaises(producer.H3PromptRewriterUvResolutionExecutionError),
        ):
            self.execute(factory)
        self.assertIn(producer.signal.SIGKILL, signals.calls)
        self.assertFalse(signals.alive)
        self.assertFalse((self.state / producer.REPORT_NAME).exists())

    def test_downstream_invalid_url_error_is_translated(self):
        with (
            mock.patch.object(
                resolver,
                "_load_report",
                side_effect=resolver.H3PromptRewriterWheelResolverSecurityError(
                    "private invalid URL detail"
                ),
            ),
            self.assertRaises(
                producer.H3PromptRewriterUvResolutionSecurityError
            ) as raised,
        ):
            producer.parse_uv_pylock_to_wheel_report(_pylock())
        self.assertNotIn("private invalid URL detail", str(raised.exception))

    def test_cli_defaults_to_plan_and_rejects_execute_arguments_without_flag(self):
        buffer = io.StringIO()
        with (
            mock.patch.object(producer, "_inspect_uv", return_value=self.uv_receipt),
            mock.patch.object(
                producer, "_inspect_python", return_value=self.python_receipt
            ),
            mock.patch.object(
                report_cli,
                "build_h3_prompt_rewriter_uv_resolution_plan",
                side_effect=lambda *args, **kwargs: (
                    producer.build_h3_prompt_rewriter_uv_resolution_plan(
                        *args, **kwargs
                    )
                ),
            ),
            redirect_stdout(buffer),
        ):
            result = report_cli.main(
                [
                    "--uv-executable",
                    str(self.uv),
                    "--python-executable",
                    str(self.python),
                ]
            )
        self.assertEqual(result, 0)
        output = json.loads(buffer.getvalue())
        self.assertFalse(output["plan"]["planning_mutation"])
        self.assertTrue(output["plan"]["execution_writes_private_state"])

        buffer = io.StringIO()
        with (
            mock.patch.object(producer, "_inspect_uv", return_value=self.uv_receipt),
            mock.patch.object(
                producer, "_inspect_python", return_value=self.python_receipt
            ),
            mock.patch.object(
                report_cli,
                "build_h3_prompt_rewriter_uv_resolution_plan",
                side_effect=lambda *args, **kwargs: (
                    producer.build_h3_prompt_rewriter_uv_resolution_plan(
                        *args, **kwargs
                    )
                ),
            ),
            redirect_stdout(buffer),
        ):
            result = report_cli.main(
                [
                    "--uv-executable",
                    str(self.uv),
                    "--python-executable",
                    str(self.python),
                    "--expected-plan-sha256",
                    "0" * 64,
                ]
            )
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(buffer.getvalue()),
            {"error": "H3PromptRewriterUvResolutionError"},
        )

    def test_cli_execute_failure_remains_content_free(self):
        plan = self.plan()
        buffer = io.StringIO()
        with (
            mock.patch.object(
                report_cli,
                "build_h3_prompt_rewriter_uv_resolution_plan",
                return_value=plan,
            ),
            mock.patch.object(
                report_cli,
                "execute_h3_prompt_rewriter_uv_resolution",
                side_effect=producer.H3PromptRewriterUvResolutionExecutionError(
                    "private uv parser detail"
                ),
            ),
            redirect_stdout(buffer),
        ):
            result = report_cli.main(
                [
                    "--uv-executable",
                    str(self.uv),
                    "--python-executable",
                    str(self.python),
                    "--execute",
                    "--expected-plan-sha256",
                    plan.sha256,
                    "--expected-input-sha256",
                    "0" * 64,
                    "--expected-uv-sha256",
                    producer.PINNED_UV_SHA256,
                    "--expected-python-sha256",
                    self.python_receipt.sha256,
                    "--private-feature-root",
                    str(self.feature),
                    "--state-root",
                    str(self.state),
                ]
            )
        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(buffer.getvalue()),
            {"error": "H3PromptRewriterUvResolutionError"},
        )

    def test_pinned_uv_offline_command_reaches_resolution_not_cli_validation(self):
        executable = os.environ.get("MAESTRO_H3_PINNED_UV_PROBE")
        python_executable = os.environ.get("MAESTRO_H3_PINNED_PYTHON_PROBE")
        if executable is None or python_executable is None:
            self.skipTest("exact pinned uv/Python offline probe was not requested")
        receipt = producer._inspect_uv(Path(executable))
        python_receipt = producer._inspect_python(Path(python_executable))
        producer._layout(self.feature, self.state)
        producer._atomic_write(
            self.state / producer.INPUT_NAME,
            producer.reviewed_requirements_input_bytes(),
        )
        environment = producer._child_environment(self.state)
        environment["UV_OFFLINE"] = "1"
        completed = subprocess.run(
            producer._command(receipt.path, python_receipt.path, self.state),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            shell=False,
            close_fds=True,
            cwd=self.state,
            env=environment,
            timeout=15,
            check=False,
        )
        terminal = (completed.stdout + completed.stderr)[: 64 * 1024].lower()
        self.assertEqual(completed.returncode, 1, terminal.decode(errors="replace"))
        self.assertIn(b"cache", terminal)
        for cli_validation_error in (
            b"cannot be used with",
            b"must start with `pylock.`",
            b"must end with `.toml`",
            b"unexpected argument",
        ):
            self.assertNotIn(cli_validation_error, terminal)


if __name__ == "__main__":
    unittest.main()
