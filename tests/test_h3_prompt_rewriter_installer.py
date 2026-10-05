"""CPU-only qualification of the offline isolated installer, using tiny wheels."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.parse
import zipfile

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from scripts import install_h3_prompt_rewriter_runtime as installer
from services import h3_prompt_rewriter_dependency_closure as closure
from services import h3_prompt_rewriter_wheel_resolver as resolver


class H3PromptRewriterInstallerTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / "h3-prompt-rewriter"
        self.root.mkdir(mode=0o700)
        stage = self.root / "wheel-stage"
        stage.mkdir(mode=0o700)
        self.wheels = stage / "wheels"
        self.wheels.mkdir(mode=0o700)
        packages = []
        for name, version in closure.ROOT_PACKAGE_PINS:
            binary = name in {"pillow", "safetensors", "tokenizers", "torch", "torchvision"}
            tags = "cp312-cp312-manylinux_2_28_x86_64" if binary else "py3-none-any"
            filename = f"{name}-{version}-{tags}.whl"
            path = self.wheels / filename
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(f"{name}-{version}.dist-info/METADATA",
                    f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\n")
            path.chmod(0o600)
            index = resolver.PYTORCH_INDEX if name in {"torch", "torchvision"} else resolver.PYPI_INDEX
            host = "https://download.pytorch.org/whl/cu128/" if index == resolver.PYTORCH_INDEX else "https://files.pythonhosted.org/packages/aa/bb/"
            packages.append({"name": name, "version": version, "requirement": f"{name}=={version}",
                "dependencies": [], "wheel": {"filename": filename, "size_bytes": path.stat().st_size,
                    "sha256": installer._sha(path.read_bytes()), "index": index,
                    "source_url": host + urllib.parse.quote(filename, safe="/-._~")}})
        self.inventory = {name: version for name, version in closure.ROOT_PACKAGE_PINS}
        report_document = {"schema": resolver.WHEEL_RESOLUTION_REPORT_SCHEMA,
            "target": resolver.build_h3_prompt_rewriter_wheel_resolution_plan().document["target"],
            "root_requirements": list(closure.ROOT_REQUIREMENTS), "packages": packages}
        self.report = stage / "wheel-report.json"
        self.report.write_bytes(installer._canonical(report_document))
        self.report.chmod(0o600)
        self.report_sha = installer._sha(self.report.read_bytes())
        report = resolver._load_report(self.report.read_bytes(), self.report_sha)
        plan = resolver.build_h3_prompt_rewriter_wheel_resolution_plan()
        manifest = resolver._manifest(plan, report, {p["name"]: p["wheel"] for p in packages})
        self.manifest = stage / resolver.MANIFEST_NAME
        self.manifest.write_bytes(installer._canonical(manifest))
        self.manifest.chmod(0o600)
        self.manifest_sha = installer._sha(self.manifest.read_bytes())
        self.python = Path(sys.executable).resolve()
        self.seal = {"size_bytes": self.python.stat().st_size,
                     "sha256": installer._sha(self.python.read_bytes())}
        self.commands = []
        self.fake_uv = Path("/usr/bin/pinned-uv")
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(installer.producer, "_inspect_uv",
                          return_value=SimpleNamespace(path=self.fake_uv)).start()
        mock.patch.object(installer.producer, "_inspect_python", return_value=SimpleNamespace(
            version="3.12.14", **self.seal)).start()
        self.real_run = installer._run
        self.run_mock = mock.patch.object(installer, "_run", side_effect=self.fake_run).start()

    def fake_run(self, command, *, generation, **kwargs):
        self.commands.append(command)
        environment = installer._environment(generation)
        self.assertEqual(environment["CUDA_VISIBLE_DEVICES"], "")
        self.assertEqual(environment["UV_OFFLINE"], "1")
        self.assertFalse(any("TOKEN" in key or "SECRET" in key for key in environment))
        if "venv" in command:
            (generation / "venv").mkdir(mode=0o700)
            (generation / "venv/bin").mkdir(mode=0o700)
            (generation / "venv/bin/python").symlink_to(self.python)
        if "-c" in command:
            return json.dumps({"implementation": "CPython", "python_version": "3.12.14",
                "system": "Linux", "machine": "x86_64", "libc": ["glibc", "2.35"], "prefix": str(generation / "venv"),
                "base_prefix": "/private/base-python", "runtime_inventory": self.inventory}).encode()
        return b""

    def install(self, generation="1" * 32, **options):
        return installer.install_runtime(feature_root=self.root, generation=generation,
            uv_executable=self.fake_uv, python_executable=self.python,
            expected_python_sha256=self.seal["sha256"], report_path=self.report,
            expected_report_sha256=self.report_sha, manifest_path=self.manifest,
            expected_manifest_sha256=self.manifest_sha, **options)

    def pointer(self):
        return self.root / "state" / installer.POINTER_NAME

    def test_complete_offline_install_and_explicit_promotion_bind_final_generation(self):
        result = self.install(promote=True)
        destination = self.root / "generations" / result["generation"]
        receipt_payload = (destination / installer.RECEIPT_NAME).read_bytes()
        receipt = json.loads(receipt_payload)
        self.assertEqual(installer._sha(receipt_payload), result["receipt_sha256"])
        self.assertEqual(receipt["runtime_inventory"], self.inventory)
        self.assertEqual(receipt["python_executable"], str(destination / "venv/bin/python"))
        self.assertEqual(receipt["interpreter_seal"], self.seal)
        self.assertEqual(receipt["resolution_report_sha256"], self.report_sha)
        self.assertEqual(receipt["wheel_manifest_sha256"], self.manifest_sha)
        self.assertFalse(receipt["gpu_execution_accepted"])
        self.assertFalse(receipt["model_execution_accepted"])
        self.assertEqual(receipt["qualification"], installer._QUALIFICATION)
        self.assertEqual(self.pointer().stat().st_mode & 0o777, 0o600)
        install_command = next(command for command in self.commands if "install" in command)
        for flag in ("--offline", "--no-config", "--no-index", "--require-hashes", "--only-binary=:all:"):
            self.assertIn(flag, install_command)
        requirements = (destination / "requirements.txt").read_text().splitlines()
        self.assertEqual(len(requirements), len(self.inventory))
        self.assertTrue(all(" @ file:///" in line and " --hash=sha256:" in line for line in requirements))
        probe = next(command for command in self.commands if "-c" in command)
        self.assertIn("-I", probe)
        self.assertNotIn("import torch", probe[-1])

    def test_corrupt_or_incomplete_wheel_evidence_stops_before_generation(self):
        path = next(self.wheels.iterdir())
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(resolver.H3PromptRewriterWheelResolverError):
            self.install()
        self.assertFalse((self.root / "generations").exists())
        self.run_mock.assert_not_called()

    def test_manifest_tampering_fails_before_commands(self):
        value = json.loads(self.manifest.read_bytes())
        value["dependency_inventory_sha256"] = "0" * 64
        self.manifest.write_bytes(installer._canonical(value))
        self.manifest_sha = installer._sha(self.manifest.read_bytes())
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "manifest evidence"):
            self.install()
        self.run_mock.assert_not_called()

    def test_wrong_exact_interpreter_stops_before_mutation(self):
        installer.producer._inspect_python.return_value = SimpleNamespace(version="3.12.13", **self.seal)
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "3.12.14"):
            self.install()
        self.assertFalse((self.root / "generations").exists())

    def test_failed_consistency_preserves_previous_pointer_and_failed_generation(self):
        first = self.install(promote=True)
        previous = self.pointer().read_bytes()
        normal = self.fake_run

        def fail_check(command, **kwargs):
            if "check" in command:
                raise installer.H3PromptRewriterInstallationError("failed consistency")
            return normal(command, **kwargs)

        self.run_mock.side_effect = fail_check
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "consistency"):
            self.install("2" * 32, promote=True, expected_current_sha256=installer._sha(previous))
        self.assertEqual(self.pointer().read_bytes(), previous)
        failed = self.root / "generations" / ("2" * 32)
        self.assertTrue((failed / "venv").is_dir())
        self.assertFalse((failed / installer.RECEIPT_NAME).exists())
        self.assertTrue((self.root / "generations" / first["generation"]).exists())

    def test_inventory_mismatch_is_not_qualified(self):
        self.inventory = {**self.inventory, "unexpected": "1.0"}
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "inventory differs"):
            self.install()
        self.assertFalse(self.pointer().exists())

    def test_rollback_requalifies_and_retains_both_generations(self):
        first = self.install(promote=True)
        old_pointer = self.pointer().read_bytes()
        second = self.install("2" * 32, promote=True,
                              expected_current_sha256=installer._sha(old_pointer))
        second_pointer = self.pointer().read_bytes()
        result = installer.activate_generation(feature_root=self.root, generation=first["generation"],
            uv_executable=self.fake_uv, expected_receipt_sha256=first["receipt_sha256"],
            expected_current_sha256=installer._sha(second_pointer))
        self.assertTrue(result["promoted"])
        self.assertEqual(json.loads(self.pointer().read_bytes())["generation"], first["generation"])
        self.assertTrue((self.root / "generations" / second["generation"] / "venv").exists())
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "hash changed"):
            installer.activate_generation(feature_root=self.root, generation=second["generation"],
                uv_executable=self.fake_uv, expected_receipt_sha256=second["receipt_sha256"],
                expected_current_sha256=installer._sha(second_pointer))

    def test_existing_generation_and_selection_need_exact_ownership(self):
        self.install(promote=True)
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "exact hash"):
            self.install("2" * 32, promote=True)
        with self.assertRaises(FileExistsError):
            self.install()
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "32 lowercase"):
            self.install("../env")

    def test_symlink_root_is_rejected(self):
        alias = Path(self.scratch.name) / "alias"
        alias.symlink_to(self.root)
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "links"):
            installer._layout(alias)

    def test_owned_cpu_subprocess_projects_environment_and_obeys_deadline(self):
        generation = self.root / "cpu-runner"
        generation.mkdir(mode=0o700)
        for name in ("home", "tmp", "cache"):
            (generation / name).mkdir(mode=0o700)
        with mock.patch.dict(os.environ, {"PRIVATE_TEST_SECRET": "must-not-inherit"}):
            output = self.real_run([str(self.python), "-I", "-c",
                "import os; print(os.environ.get('PRIVATE_TEST_SECRET', 'absent')); "
                "print(repr(os.environ.get('CUDA_VISIBLE_DEVICES')))"], generation=generation)
        self.assertEqual(output, b"absent\n''\n")
        with self.assertRaisesRegex(installer.H3PromptRewriterInstallationError, "deadline"):
            self.real_run([str(self.python), "-I", "-c", "import time; time.sleep(30)"],
                           generation=generation, timeout=0.05)


if __name__ == "__main__":
    unittest.main()
