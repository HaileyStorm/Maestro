import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "services" / "storage_tier_plan.py"
SPEC = importlib.util.spec_from_file_location("storage_tier_plan", MODULE_PATH)
storage_tier_plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(storage_tier_plan)


class StorageTierPlanTests(unittest.TestCase):
    def _plan(self, root: Path, *, identities=False):
        tiers = {}
        for role in storage_tier_plan.STORAGE_TIER_ROLES:
            tier_root = root / role
            tier_root.mkdir()
            tier = {
                "root": str(tier_root),
                "write_intent": "read_only" if role == "cold" else "read_write",
            }
            if identities:
                tier["identity"] = {"filesystem_uuid": f"uuid-{role}"}
            tiers[role] = tier
        return {"schema_version": 1, "tiers": tiers}

    def _write(self, folder: Path, payload) -> Path:
        path = folder / "storage-plan.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_absent_configuration_is_a_clean_noop(self):
        report = storage_tier_plan.inspect_storage_tier_plan(environ={})
        self.assertEqual(report["status"], "not_configured")
        self.assertFalse(report["configured"])
        self.assertFalse(report["applied"])
        self.assertEqual(report["issues"], [])

    def test_valid_plan_reports_only_existing_bindings_without_creating_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = self._plan(root)
            hf_path = root / "warm_models" / "caches" / "huggingface"
            plan_file = self._write(root, plan)
            report = storage_tier_plan.inspect_storage_tier_plan(plan_file)

            self.assertEqual(report["status"], "ready")
            self.assertFalse(report["applied"])
            hf = report["proposed_bindings"]["environment"]["HF_HOME"]
            self.assertEqual(hf["path"], str(hf_path))
            self.assertEqual(hf["state"], "missing")
            self.assertFalse(hf["apply"])
            self.assertFalse(hf_path.exists())
            wgp = report["proposed_bindings"]["wgp"]
            self.assertEqual(wgp["checkpoint_primary"]["tier"], "warm_models")
            self.assertEqual(wgp["checkpoint_primary"]["write_intent"], "read_write")
            self.assertEqual(wgp["checkpoint_linked"][0]["tier"], "cold")
            self.assertEqual(wgp["checkpoint_linked"][0]["write_intent"], "read_only")
            self.assertEqual(wgp["save_path"]["tier"], "warm_bulk")

    def test_unbound_roles_are_reported_without_becoming_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = {
                "schema_version": 1,
                "tiers": {
                    role: {
                        "root": None,
                        "write_intent": "read_only" if role == "cold" else "read_write",
                    }
                    for role in storage_tier_plan.STORAGE_TIER_ROLES
                },
            }
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            self.assertEqual(report["status"], "unbound")
            self.assertTrue(all(tier["state"] == "unbound" for tier in report["tiers"].values()))
            self.assertTrue(all(
                binding["state"] == "unbound"
                for binding in report["proposed_bindings"]["environment"].values()
            ))

    def test_relative_and_missing_roots_are_invalid_but_not_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            missing = root / "future-ssd"
            payload["tiers"]["warm_models"]["root"] = str(missing)
            payload["tiers"]["warm_bulk"]["root"] = "relative-drive"
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            codes = {(item["code"], item.get("role")) for item in report["issues"]}
            self.assertIn(("missing_root", "warm_models"), codes)
            self.assertIn(("invalid_root", "warm_bulk"), codes)
            self.assertFalse(missing.exists())

    def test_duplicate_alias_and_nested_roots_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            payload["tiers"]["warm_models"]["root"] = payload["tiers"]["hot"]["root"]
            nested = Path(payload["tiers"]["warm_bulk"]["root"]) / "nested"
            nested.mkdir()
            payload["tiers"]["cold"]["root"] = str(nested)
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            overlaps = [item for item in report["issues"] if item["code"] == "overlapping_roots"]
            self.assertEqual(len(overlaps), 2)

    def test_symlink_alias_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            alias = root / "hot-alias"
            try:
                alias.symlink_to(Path(payload["tiers"]["hot"]["root"]), target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            payload["tiers"]["cold"]["root"] = str(alias)
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            self.assertIn("symlink_root", {item["code"] for item in report["issues"]})

    def test_symlink_to_otherwise_unconfigured_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            target = root / "not-another-tier"
            target.mkdir()
            alias = root / "future-drive-alias"
            try:
                alias.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            payload["tiers"]["hot"]["root"] = str(alias)
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            self.assertIn(
                ("symlink_root", "hot"),
                {(item["code"], item.get("role")) for item in report["issues"]},
            )

    def test_stable_identity_is_verified_when_supplied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root, identities=True)
            plan_file = self._write(root, payload)

            def probe(path):
                return {"filesystem_uuid": f"uuid-{path.name}"}

            ready = storage_tier_plan.inspect_storage_tier_plan(plan_file, identity_probe=probe)
            self.assertEqual(ready["status"], "ready")
            mismatch = storage_tier_plan.inspect_storage_tier_plan(
                plan_file,
                identity_probe=lambda _path: {"filesystem_uuid": "wrong"},
            )
            self.assertEqual(mismatch["status"], "invalid")
            self.assertEqual(
                {item["code"] for item in mismatch["issues"]},
                {"identity_mismatch"},
            )

    def test_write_targets_cannot_be_declared_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            payload["tiers"]["warm_models"]["write_intent"] = "read_only"
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            self.assertIn(
                ("invalid_write_intent", "warm_models"),
                {(item["code"], item.get("role")) for item in report["issues"]},
            )

    def test_cold_role_must_be_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            payload["tiers"]["cold"]["write_intent"] = "read_write"
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            self.assertIn(
                ("invalid_write_intent", "cold"),
                {(item["code"], item.get("role")) for item in report["issues"]},
            )

    def test_symlink_in_proposed_child_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            caches = root / "warm_models" / "caches"
            target = root / "unconfigured-cache-target"
            target.mkdir()
            try:
                caches.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            report = storage_tier_plan.inspect_storage_tier_plan(self._write(root, payload))
            self.assertEqual(report["status"], "invalid")
            self.assertIn("symlink_binding", {item["code"] for item in report["issues"]})
            self.assertEqual(
                report["proposed_bindings"]["environment"]["HF_HOME"]["state"],
                "unsafe_symlink",
            )

    def test_proposed_child_must_remain_within_its_tier_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self._plan(root)
            plan_file = self._write(root, payload)
            unsafe_layout = dict(storage_tier_plan._ENVIRONMENT_LAYOUT)
            unsafe_layout["HF_HOME"] = ("warm_models", "../escape")
            with mock.patch.object(storage_tier_plan, "_ENVIRONMENT_LAYOUT", unsafe_layout):
                report = storage_tier_plan.inspect_storage_tier_plan(plan_file)
            self.assertEqual(report["status"], "invalid")
            self.assertIn("escaping_binding", {item["code"] for item in report["issues"]})
            self.assertEqual(
                report["proposed_bindings"]["environment"]["HF_HOME"]["state"],
                "unsafe_escape",
            )

    def test_cli_no_configuration_exits_zero_and_prints_json(self):
        env = dict(os.environ)
        env.pop(storage_tier_plan.PLAN_ENVIRONMENT_KEY, None)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "scripts/storage_tier_plan.py"],
            cwd=ROOT / "app",
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
