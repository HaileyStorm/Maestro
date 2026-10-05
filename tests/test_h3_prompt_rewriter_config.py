"""CPU-only host configuration boundaries; no model or coordinator contact."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_prompt_rewriter_config as configuration


class HostConfigurationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.feature = self.root / "h3-prompt-rewriter"
        self.assets = self.root / "assets"
        self.coordinator = self.root / "coordinator"
        self.workspace = self.root / "workspace"
        for path in (self.feature, self.assets, self.coordinator, self.workspace):
            path.mkdir(mode=0o700)
        (self.feature / "state").mkdir(mode=0o700)
        self.generation = "1" * 32
        destination = self.feature / "generations" / self.generation
        destination.mkdir(parents=True, mode=0o700)
        source_sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.receipt = {"schema": "maestro.h3-prompt-rewriter.runtime-installation.v1",
            "generation": self.generation, "python_executable": str(destination / "venv/bin/python"),
            "python_version": "3.12.14", "runtime_inventory": {}, "interpreter_seal": {},
            "source_sha256": source_sha(APP / "scripts/install_h3_prompt_rewriter_runtime.py"),
            "worker_sha256": source_sha(APP / "services/h3_prompt_rewriter_worker.py"),
            "qualification": {"consistency_checked": True, "cuda_masked": True, "model_free": True},
            "model_execution_accepted": False, "gpu_execution_accepted": False}
        self.receipt_path = destination / "runtime-receipt.json"
        self.pointer_path = self.feature / "state/current-runtime.json"
        self.pointer = {"schema": "maestro.h3-prompt-rewriter.runtime-selection.v1",
                        "generation": self.generation, "receipt_sha256": self.save(self.receipt_path, self.receipt)}
        self.save(self.pointer_path, self.pointer)
        self.manifest_path = self.assets / "assets.json"
        manifest_sha = self.save(self.manifest_path, {"private_root": str(self.assets), "asset_seals": {}})
        self.config_path = self.root / "config.json"
        self.config = {"schema": configuration.SCHEMA, "enabled": True,
            "feature_root": str(self.feature), "artifact_root": str(self.assets),
            "asset_manifest": str(self.manifest_path), "asset_manifest_sha256": manifest_sha,
            "coordinator_root": str(self.coordinator), "project_id": "owned-project", "cuda_visible_devices": "0"}
        self.save(self.config_path, self.config)

    def save(self, path, document):
        payload = json.dumps(document, sort_keys=True).encode()
        path.write_bytes(payload)
        path.chmod(0o600)
        return hashlib.sha256(payload).hexdigest()

    def load(self):
        return configuration.load_runtime_snapshot(workspace=self.workspace, config_path=self.config_path)

    def test_load_is_passive_and_commitment_contains_no_path(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("no child at snapshot")):
            snapshot = self.load()
            self.assertEqual(snapshot.commitment, self.load().commitment)
            self.assertRegex(snapshot.commitment, r"^[0-9a-f]{64}$")
            binding = snapshot.gpu_binding()
        self.assertEqual(binding.workspace, self.workspace)
        self.assertEqual(binding.project_id, "owned-project")

    def test_selection_update_invalidates_existing_snapshot(self):
        snapshot = self.load()
        self.config["cuda_visible_devices"] = "1"
        self.save(self.config_path, self.config)
        self.assertNotEqual(snapshot.commitment, self.load().commitment)
        with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
            snapshot.recheck()

    def test_changed_pointer_or_manifest_cannot_reuse_frozen_snapshot(self):
        for path, document in ((self.pointer_path, self.pointer), (self.manifest_path,
                {"private_root": str(self.assets), "asset_seals": {}})):
            snapshot = self.load()
            self.save(path, dict(document, foreign=True))
            with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
                snapshot.recheck()
            self.save(path, document)

    def test_missing_disabled_or_additional_fields_do_not_fall_back(self):
        for changed in (dict(self.config, enabled=False), dict(self.config, request_override="/foreign")):
            self.save(self.config_path, changed)
            with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
                self.load()
        self.config_path.unlink()
        with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
            self.load()

    def test_symlink_or_shared_configuration_is_rejected(self):
        self.config_path.chmod(0o640)
        with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
            self.load()
        self.config_path.chmod(0o600)
        moved = self.config_path.with_name("original.json")
        self.config_path.rename(moved)
        self.config_path.symlink_to(moved)
        with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
            self.load()

    def test_manifest_must_belong_to_selected_asset_root(self):
        foreign = self.root / "foreign.json"
        self.config["asset_manifest_sha256"] = self.save(foreign, {"private_root": str(self.assets), "asset_seals": {}})
        self.config["asset_manifest"] = str(foreign)
        self.save(self.config_path, self.config)
        with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
            self.load()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO guard requires POSIX")
    def test_fifo_runtime_documents_are_rejected_without_blocking_or_removal(self):
        probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from services import h3_prompt_rewriter_config as configuration
try:
    configuration.load_runtime_snapshot(workspace=Path(sys.argv[2]), config_path=Path(sys.argv[3]))
except configuration.H3PromptRewriterConfigurationError:
    print('rejected')
else:
    raise AssertionError('FIFO runtime document was accepted')
"""
        for path in (self.config_path, self.pointer_path, self.receipt_path, self.manifest_path):
            with self.subTest(document=path.name):
                saved = path.with_name(path.name + ".saved")
                path.rename(saved)
                try:
                    os.mkfifo(path, 0o600)
                    result = subprocess.run(
                        [sys.executable, "-c", probe, str(APP), str(self.workspace), str(self.config_path)],
                        capture_output=True, text=True, timeout=3,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "rejected")
                    self.assertTrue(stat.S_ISFIFO(path.lstat().st_mode))
                finally:
                    path.unlink(missing_ok=True)
                    saved.rename(path)

    def test_installed_source_or_nonboolean_qualification_is_rejected(self):
        for key, value in (("worker_sha256", "0" * 64),
                ("qualification", {"consistency_checked": 1, "cuda_masked": True, "model_free": True}),
                ("gpu_execution_accepted", True)):
            self.pointer["receipt_sha256"] = self.save(self.receipt_path, dict(self.receipt, **{key: value}))
            self.save(self.pointer_path, self.pointer)
            with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
                self.load()

    def test_duplicate_configuration_fields_rejected(self):
        self.config_path.write_text('{"enabled":true,"enabled":false}')
        with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
            self.load()

    def test_detected_source_drift_never_imports_runtime_or_starts_probe(self):
        snapshot = self.load()
        foreign = self.root / "changed-source.py"
        foreign.write_text("raise AssertionError('changed source must not execute')")
        from dataclasses import replace
        snapshot = replace(snapshot, _source_inputs=((foreign, "0" * 64),))
        import builtins
        original_import = builtins.__import__
        observed = []

        def observe(name, *args, **kwargs):
            observed.append((name, kwargs.get("fromlist", args[2] if len(args) > 2 else ())))
            return original_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=observe), mock.patch(
                "subprocess.run", side_effect=AssertionError("No probe on source drift")):
            with self.assertRaises(configuration.H3PromptRewriterConfigurationError):
                snapshot.build_execution_admission("t2va")
        self.assertFalse(any(name == "services" for name, _ in observed))


if __name__ == "__main__":
    unittest.main()
