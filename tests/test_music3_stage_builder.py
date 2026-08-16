"""Model-, network-, build-, GPU-, and publication-free Music 3 builder tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import music3_runtime as runtime
from services import music3_stage_builder as builder

from scripts import build_music3_stage as builder_cli

FILESYSTEM_CAPABILITY = {
    "schema": "maestro.music3.filesystem-capability.v1",
    "filesystem_type": "testfs",
    "cross_process_flock": True,
    "directory_fsync": True,
    "executable_mode": True,
    "atomic_same_filesystem_replace": True,
    "symlink_detection": True,
}


def _fake_sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


class Music3StageBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)
        self.pinokio = self.scratch / "pinokio"
        self.pinokio.mkdir(mode=0o700)
        self.location_patch = mock.patch.object(
            runtime, "_forbidden_runtime_location", return_value=False,
        )
        self.location_patch.start()
        self.addCleanup(self.location_patch.stop)
        self.space_patch = mock.patch.object(
            runtime.shutil,
            "disk_usage",
            return_value=shutil._ntuple_diskusage(
                total=4 * runtime.MIN_PROVISION_FREE_BYTES,
                used=0,
                free=4 * runtime.MIN_PROVISION_FREE_BYTES,
            ),
        )
        self.space_patch.start()
        self.addCleanup(self.space_patch.stop)
        self.runtime_plan = runtime.build_music3_provision_plan(
            self.pinokio,
            ucx_version=runtime.PINNED_UCX_VERSION,
            ucx_source_revision=runtime.PINNED_UCX_SOURCE_REVISION,
        )
        self.runtime_plan.layout.root.mkdir(parents=True, mode=0o700)
        self.runtime_plan.layout.root.chmod(0o700)

    def artifact(self, artifact_id, role, filename, *, sha256=None, size=100):
        return {
            "artifact_id": artifact_id,
            "role": role,
            "filename": filename,
            "url": f"https://artifacts.example.test/{filename}",
            "sha256": sha256 or _fake_sha(artifact_id),
            "size": size,
            "etag": f"etag-{artifact_id}",
        }

    def manifest(self):
        artifacts = [
            self.artifact("python", "python-runtime", "python.tar.zst"),
            self.artifact("cuda", "cuda-runtime", "cuda.tar.zst"),
            self.artifact("sglang", "sglang-source", "sglang.tar.gz"),
            self.artifact(
                "ucx",
                "ucx-source",
                "ucx-1.20.1.tar.gz",
                sha256=runtime.PINNED_UCX_TARBALL_SHA256,
                size=runtime.PINNED_UCX_TARBALL_SIZE,
            ),
            self.artifact("model", "model", "model.snapshot"),
        ]
        lock = []
        for index, line in enumerate(sorted(runtime.REQUIRED_RUNTIME_LOCK_LINES)):
            raw_name, version = line.split("==", 1)
            name = raw_name.partition("[")[0]
            artifact_id = f"wheel-{index}"
            filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
            artifact = self.artifact(artifact_id, "wheel", filename, size=200 + index)
            artifacts.append(artifact)
            lock.append({
                "name": name,
                "version": version,
                "requirement": line,
                "artifact_id": artifact_id,
                "filename": filename,
                "sha256": artifact["sha256"],
                "size": artifact["size"],
            })
        dependency_lock_bytes = (
            "\n".join(sorted(item["requirement"] for item in lock)) + "\n"
        ).encode("utf-8")
        tree_expectations = {
            key: _fake_sha(key)
            for key in builder._TREE_DIGEST_KEYS
        }
        tree_expectations["dependency_lock_sha256"] = (
            "sha256:" + hashlib.sha256(dependency_lock_bytes).hexdigest()
        )
        return {
            "schema": builder.BUILDER_INPUT_SCHEMA,
            "generation_id": "generation-1",
            "runtime_plan_sha256": self.runtime_plan.sha256,
            "pins": {
                "model_id": runtime.MUSIC3_MODEL_ID,
                "model_revision": runtime.PINNED_MODEL_REVISION,
                "sglang_source_revision": runtime.PINNED_SGLANG_SOURCE_REVISION,
                "ucx_version": runtime.PINNED_UCX_VERSION,
                "ucx_source_revision": runtime.PINNED_UCX_SOURCE_REVISION,
                "ucx_source_tarball_sha256": runtime.PINNED_UCX_TARBALL_SHA256,
                "ucx_source_tarball_size": runtime.PINNED_UCX_TARBALL_SIZE,
                "ucx_configure_flags": list(runtime.PINNED_UCX_CONFIGURE_FLAGS),
                "python_runtime": {
                    "implementation": "cpython",
                    "version": "3.12.9",
                    "abi": "cp312",
                    "artifact_id": "python",
                },
                "cuda_runtime": {
                    "version": "13.0.1",
                    "architecture": "linux-x86_64",
                    "artifact_id": "cuda",
                },
            },
            "artifacts": artifacts,
            "wheel_lock": lock,
            "tree_expectations": tree_expectations,
            "disk_budget": {
                "installed_environment_bytes": 10_000,
                "model_tree_bytes": 20_000,
                "source_tree_bytes": 30_000,
                "ucx_prefix_bytes": 4_000_000,
                "scratch_bytes": 4_000_000,
            },
        }

    def write_manifest(self, value=None, *, name="input.json"):
        value = self.manifest() if value is None else value
        directory = self.runtime_plan.layout.root / builder.REVIEWED_INPUT_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        path = directory / name
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path, builder._mapping_sha256(value)

    def plan(self, value=None, *, available_bytes=builder.MIN_FREE_AFTER_STAGE_BYTES + 100_000_000):
        path, expected = self.write_manifest(value)
        return builder.build_music3_stage_plan(
            self.pinokio,
            reviewed_manifest_path=path,
            expected_reviewed_manifest_sha256=expected,
            available_bytes=available_bytes,
            filesystem_capability_provider=lambda _layout: FILESYSTEM_CAPABILITY,
        )

    def resume_record(self, plan, phase):
        return {
            "schema": builder.RESUME_SCHEMA,
            "resume_identity": plan.document["resume_identity"],
            "plan_sha256": plan.sha256,
            "phase": phase,
            "generation_id": plan.document["generation_id"],
            "final_generation_path": plan.document["final_generation_path"],
            "reviewed_manifest_sha256": plan.document["reviewed_manifest_sha256"],
            "runtime_stage_manifest_sha256": plan.document["runtime_stage_manifest_sha256"],
        }

    def runtime_artifact_records(self, value=None):
        value = self.manifest() if value is None else value
        artifacts = {item["artifact_id"]: item for item in value["artifacts"]}
        python_pin = value["pins"]["python_runtime"]
        python_artifact = artifacts[python_pin["artifact_id"]]
        cuda_pin = value["pins"]["cuda_runtime"]
        cuda_artifact = artifacts[cuda_pin["artifact_id"]]
        return {
            "python_runtime": {
                "implementation": python_pin["implementation"],
                "version": python_pin["version"],
                "abi": python_pin["abi"],
                "artifact_filename": python_artifact["filename"],
                "artifact_sha256": python_artifact["sha256"],
                "artifact_size": python_artifact["size"],
            },
            "cuda_runtime": {
                "version": cuda_pin["version"],
                "architecture": cuda_pin["architecture"],
                "artifact_filename": cuda_artifact["filename"],
                "artifact_sha256": cuda_artifact["sha256"],
                "artifact_size": cuda_artifact["size"],
            },
        }

    def test_missing_review_inputs_are_explicitly_blocked_without_capability_probe(self):
        with mock.patch.object(runtime, "_filesystem_capability_evidence") as probe:
            plan = builder.build_music3_stage_plan(self.pinokio)
        self.assertFalse(plan.ready)
        self.assertEqual(plan.document["status"], "blocked")
        self.assertIn("reviewed_python_runtime_artifact_missing", plan.document["blockers"])
        self.assertIn("complete_hashed_transitive_wheel_lock_missing", plan.document["blockers"])
        self.assertFalse(plan.document["stage_execution_available"])
        probe.assert_not_called()

    def test_manifest_and_digest_must_arrive_together_before_any_live_probe(self):
        path, _expected = self.write_manifest()
        provider = mock.Mock(return_value=FILESYSTEM_CAPABILITY)
        with self.assertRaises(builder.Music3StageBuilderBlocked):
            builder.build_music3_stage_plan(
                self.pinokio,
                reviewed_manifest_path=path,
                filesystem_capability_provider=provider,
            )
        provider.assert_not_called()

    def test_manifest_schema_bounds_duplicate_keys_and_wrong_digest_fail_closed(self):
        value = self.manifest()
        value["unexpected"] = True
        with self.assertRaisesRegex(builder.Music3StageBuilderError, "fields"):
            self.plan(value)

        value = self.manifest()
        value["artifacts"][0]["etag"] = "x" * 513
        with self.assertRaisesRegex(builder.Music3StageBuilderError, "ETag"):
            self.plan(value)

        path, expected = self.write_manifest()
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "independently"):
            builder.build_music3_stage_plan(
                self.pinokio,
                reviewed_manifest_path=path,
                expected_reviewed_manifest_sha256=_fake_sha("wrong"),
                filesystem_capability_provider=lambda _layout: FILESYSTEM_CAPABILITY,
            )
        self.assertNotEqual(expected, _fake_sha("wrong"))

        duplicate = path.with_name("duplicate.json")
        duplicate.write_text('{"schema":"a","schema":"b"}\n', encoding="utf-8")
        duplicate.chmod(0o600)
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "duplicate"):
            builder.load_reviewed_music3_build_input(
                self.pinokio,
                duplicate,
                expected_manifest_sha256=_fake_sha("duplicate"),
            )

        deep = path.with_name("deep.json")
        deep.write_text(
            '{"schema":' + ("[" * 1100) + "0" + ("]" * 1100) + "}\n",
            encoding="utf-8",
        )
        deep.chmod(0o600)
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "invalid"):
            builder.load_reviewed_music3_build_input(
                self.pinokio,
                deep,
                expected_manifest_sha256=_fake_sha("deep"),
            )

    def test_blocked_plan_does_not_probe_free_space(self):
        with mock.patch.object(
            runtime.shutil,
            "disk_usage",
            side_effect=AssertionError("free-space probe is forbidden before review"),
        ):
            plan = builder.build_music3_stage_plan(self.pinokio)
        self.assertEqual(plan.document["status"], "blocked")
        self.assertFalse(plan.document["mutation"])
        self.assertEqual(plan.document["network_phases"], 0)

    def test_manifest_path_symlink_hardlink_permissions_and_escape_fail_closed(self):
        path, expected = self.write_manifest()
        outside = self.scratch / "outside.json"
        shutil.copyfile(path, outside)
        outside.chmod(0o600)
        with self.assertRaises(builder.Music3StageBuilderSecurityError):
            builder.load_reviewed_music3_build_input(
                self.pinokio, outside, expected_manifest_sha256=expected,
            )

        link = path.with_name("link.json")
        link.symlink_to(path)
        with self.assertRaises(builder.Music3StageBuilderSecurityError):
            builder.load_reviewed_music3_build_input(
                self.pinokio, link, expected_manifest_sha256=expected,
            )
        link.unlink()

        hardlink = path.with_name("hardlink.json")
        os.link(path, hardlink)
        with self.assertRaises(builder.Music3StageBuilderSecurityError):
            builder.load_reviewed_music3_build_input(
                self.pinokio, path, expected_manifest_sha256=expected,
            )
        hardlink.unlink()
        path.chmod(0o620)
        with self.assertRaises(builder.Music3StageBuilderSecurityError):
            builder.load_reviewed_music3_build_input(
                self.pinokio, path, expected_manifest_sha256=expected,
            )

    def test_fixed_pins_ucx_artifact_and_full_wheel_lock_are_not_arbitrary(self):
        cases = []
        wrong_model = self.manifest()
        wrong_model["pins"]["model_revision"] = "git:" + ("0" * 40)
        cases.append(wrong_model)

        wrong_ucx = self.manifest()
        next(item for item in wrong_ucx["artifacts"] if item["role"] == "ucx-source")["size"] += 1
        cases.append(wrong_ucx)

        wrong_python_abi = self.manifest()
        wrong_python_abi["pins"]["python_runtime"]["version"] = "3.11.9"
        cases.append(wrong_python_abi)

        missing_pin = self.manifest()
        removed = missing_pin["wheel_lock"].pop()
        missing_pin["artifacts"] = [
            item for item in missing_pin["artifacts"]
            if item["artifact_id"] != removed["artifact_id"]
        ]
        cases.append(missing_pin)

        sdist = self.manifest()
        entry = sdist["wheel_lock"][0]
        artifact = next(
            item for item in sdist["artifacts"]
            if item["artifact_id"] == entry["artifact_id"]
        )
        artifact["filename"] = "dependency.tar.gz"
        entry["filename"] = artifact["filename"]
        cases.append(sdist)

        unrelated_wheel = self.manifest()
        entry = unrelated_wheel["wheel_lock"][0]
        artifact = next(
            item for item in unrelated_wheel["artifacts"]
            if item["artifact_id"] == entry["artifact_id"]
        )
        artifact["filename"] = f"unrelated-{entry['version']}-py3-none-any.whl"
        entry["filename"] = artifact["filename"]
        cases.append(unrelated_wheel)

        wrong_abi = self.manifest()
        entry = wrong_abi["wheel_lock"][0]
        artifact = next(
            item for item in wrong_abi["artifacts"]
            if item["artifact_id"] == entry["artifact_id"]
        )
        distribution = entry["name"].replace("-", "_")
        artifact["filename"] = f"{distribution}-{entry['version']}-cp311-cp311-win_amd64.whl"
        entry["filename"] = artifact["filename"]
        cases.append(wrong_abi)

        deceptive_linux_tag = self.manifest()
        entry = deceptive_linux_tag["wheel_lock"][0]
        artifact = next(
            item for item in deceptive_linux_tag["artifacts"]
            if item["artifact_id"] == entry["artifact_id"]
        )
        distribution = entry["name"].replace("-", "_")
        artifact["filename"] = (
            f"{distribution}-{entry['version']}-cp312-cp312-notlinux_x86_64.whl"
        )
        entry["filename"] = artifact["filename"]
        cases.append(deceptive_linux_tag)

        malformed_build_tag = self.manifest()
        entry = malformed_build_tag["wheel_lock"][0]
        artifact = next(
            item for item in malformed_build_tag["artifacts"]
            if item["artifact_id"] == entry["artifact_id"]
        )
        distribution = entry["name"].replace("-", "_")
        artifact["filename"] = f"{distribution}-{entry['version']}-build-py3-none-any.whl"
        entry["filename"] = artifact["filename"]
        cases.append(malformed_build_tag)

        for unsupported_abi3_tag in ("cp27", "cp31"):
            unsupported_abi3 = self.manifest()
            entry = unsupported_abi3["wheel_lock"][0]
            artifact = next(
                item for item in unsupported_abi3["artifacts"]
                if item["artifact_id"] == entry["artifact_id"]
            )
            distribution = entry["name"].replace("-", "_")
            artifact["filename"] = (
                f"{distribution}-{entry['version']}-{unsupported_abi3_tag}-abi3-"
                "manylinux2014_x86_64.whl"
            )
            entry["filename"] = artifact["filename"]
            cases.append(unsupported_abi3)

        for value in cases:
            with self.subTest(case=cases.index(value)), self.assertRaises(builder.Music3StageBuilderError):
                self.plan(value)

    def test_duplicate_ids_names_content_and_lock_entries_fail_closed(self):
        variants = []
        duplicate_id = self.manifest()
        duplicate_id["artifacts"][1]["artifact_id"] = duplicate_id["artifacts"][0]["artifact_id"]
        variants.append(duplicate_id)
        duplicate_name = self.manifest()
        duplicate_name["artifacts"][1]["filename"] = duplicate_name["artifacts"][0]["filename"]
        variants.append(duplicate_name)
        duplicate_hash = self.manifest()
        duplicate_hash["artifacts"][1]["sha256"] = duplicate_hash["artifacts"][0]["sha256"]
        variants.append(duplicate_hash)
        duplicate_package = self.manifest()
        duplicate_package["wheel_lock"][1]["name"] = duplicate_package["wheel_lock"][0]["name"]
        variants.append(duplicate_package)
        for value in variants:
            with self.subTest(case=variants.index(value)), self.assertRaises(builder.Music3StageBuilderError):
                self.plan(value)

    def test_live_filesystem_capabilities_and_disk_budget_fail_closed(self):
        path, expected = self.write_manifest()
        evidence = dict(FILESYSTEM_CAPABILITY)
        evidence["directory_fsync"] = False
        with self.assertRaisesRegex(builder.Music3StageBuilderBlocked, "capabilities"):
            builder.build_music3_stage_plan(
                self.pinokio,
                reviewed_manifest_path=path,
                expected_reviewed_manifest_sha256=expected,
                available_bytes=1 << 50,
                filesystem_capability_provider=lambda _layout: evidence,
            )
        with self.assertRaisesRegex(builder.Music3StageBuilderBlocked, "disk budget"):
            builder.build_music3_stage_plan(
                self.pinokio,
                reviewed_manifest_path=path,
                expected_reviewed_manifest_sha256=expected,
                available_bytes=1,
                filesystem_capability_provider=lambda _layout: FILESYSTEM_CAPABILITY,
            )
        underreported = self.manifest()
        underreported["disk_budget"]["scratch_bytes"] = 1
        with self.assertRaisesRegex(builder.Music3StageBuilderError, "artifact floor"):
            self.plan(underreported)

    def test_ready_plan_is_deterministic_final_path_offline_and_generation_local(self):
        first = self.plan()
        second = self.plan()
        third = self.plan(available_bytes=builder.MIN_FREE_AFTER_STAGE_BYTES + 200_000_000)
        self.assertEqual(first.to_mapping(), second.to_mapping())
        self.assertEqual(first.to_mapping(), third.to_mapping())
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.sha256, third.sha256)
        self.assertTrue(first.ready)
        self.assertEqual(first.document["fetch_phase"]["phase_count"], 1)
        self.assertTrue(first.document["fetch_phase"]["network_allowed"])
        self.assertFalse(first.document["stage_phase"]["network_allowed"])
        self.assertFalse(first.document["execution_implemented"])
        generation = Path(first.document["final_generation_path"])
        self.assertTrue(generation.is_absolute())
        self.assertEqual(generation.parent, self.runtime_plan.layout.generations)
        self.assertEqual(
            Path(first.document["stage_phase"]["ucx_prefix"]),
            generation / "env",
        )
        self.assertEqual(
            Path(first.document["stage_phase"]["ucx_info_path"]),
            generation / "env" / "bin" / "ucx_info",
        )
        self.assertEqual(
            Path(first.document["stage_phase"]["ucx_library_path"]),
            generation / "env" / "lib",
        )
        self.assertEqual(
            Path(first.document["stage_phase"]["generation_lock"]),
            generation / runtime.GENERATION_LOCK_NAME,
        )
        seal = first.document["stage_phase"]["write_seal_protocol"]
        self.assertEqual(seal["lock_mode"], "0600")
        self.assertTrue(seal["exclusive_flock_required"])
        self.assertTrue(seal["hold_through_tree_hash_manifest_write_and_fsync"])
        self.assertFalse(seal["post_seal_writes_allowed"])
        self.assertEqual(
            first.document["runtime_stage_manifest"]["generation_lock"],
            runtime.GENERATION_LOCK_NAME,
        )
        records = self.runtime_artifact_records()
        self.assertEqual(
            first.document["runtime_stage_manifest"]["python_runtime"],
            records["python_runtime"],
        )
        self.assertEqual(
            first.document["stage_phase"]["python_runtime_record"],
            {
                "path": str(generation / runtime.PYTHON_RUNTIME_RECORD),
                "mode": "0600",
                "value": records["python_runtime"],
            },
        )
        self.assertEqual(
            first.document["runtime_stage_manifest"]["cuda_runtime"],
            records["cuda_runtime"],
        )
        for value in first.document["stage_phase"]["environment"].values():
            self.assertIn(value, {"1", "*"})
        self.assertFalse(first.document["publication"]["builder_may_switch_current"])
        self.assertFalse(first.document["publication"]["builder_may_switch_previous"])

    def test_download_descriptors_are_content_addressed_and_bind_partial_identity(self):
        plan = self.plan()
        item = plan.document["fetch_phase"]["downloads"][0]
        self.assertIn(str(item["expected_sha256"]).removeprefix("sha256:"), item["completed_path"])
        self.assertTrue(str(item["partial_path"]).endswith(".part"))
        self.assertEqual(
            plan.document["fetch_phase"]["partial_binding_fields"],
            [
                "url",
                "expected_size",
                "expected_etag",
                "expected_sha256",
                "partial_sha256",
            ],
        )
        partial = Path(item["partial_path"])
        runtime._ensure_private_runtime_directory(self.runtime_plan.layout, partial.parent)
        partial.write_bytes(b"partial")
        partial.chmod(0o600)
        record = {
            "schema": builder.PARTIAL_DOWNLOAD_SCHEMA,
            "artifact_id": item["artifact_id"],
            "url": item["url"],
            "expected_size": item["expected_size"],
            "expected_etag": item["expected_etag"],
            "expected_sha256": item["expected_sha256"],
            "bytes_present": len(b"partial"),
            "partial_sha256": (
                "sha256:" + hashlib.sha256(b"partial").hexdigest()
            ),
            "resume_identity": plan.document["resume_identity"],
        }
        record_path = Path(item["partial_record_path"])
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)
        self.assertEqual(
            builder.validate_music3_partial_download(plan, item["artifact_id"]),
            record,
        )
        record["expected_etag"] = "different"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "stale"):
            builder.validate_music3_partial_download(plan, item["artifact_id"])
        record["expected_etag"] = item["expected_etag"]
        record_path.write_text(json.dumps(record), encoding="utf-8")
        partial.write_bytes(b"changed")
        self.assertEqual(len(b"changed"), len(b"partial"))
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "stale"):
            builder.validate_music3_partial_download(plan, item["artifact_id"])

    def test_file_and_tree_content_hashes_reject_mutation_symlink_and_hardlink(self):
        tree = self.runtime_plan.layout.root / "hash-tree"
        tree.mkdir(mode=0o700)
        artifact = tree / "artifact.bin"
        artifact.write_bytes(b"reviewed bytes")
        artifact.chmod(0o600)
        expected = "sha256:" + hashlib.sha256(b"reviewed bytes").hexdigest()
        self.assertEqual(
            builder._regular_file_sha256(
                artifact,
                expected_device=self.pinokio.stat().st_dev,
                expected_size=len(b"reviewed bytes"),
            ),
            expected,
        )
        before_stat = os.lstat(artifact)
        changed_fields = list(before_stat)
        changed_fields[3] = 2
        changed_stat = os.stat_result(changed_fields)
        with mock.patch.object(
            builder.os,
            "lstat",
            side_effect=[before_stat, changed_stat],
        ), self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "changed"):
            builder._regular_file_sha256(
                artifact,
                expected_device=self.pinokio.stat().st_dev,
                expected_size=len(b"reviewed bytes"),
            )
        with mock.patch.object(
            builder.os,
            "lstat",
            side_effect=[before_stat, changed_stat],
        ), self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "changed"):
            builder._read_regular(
                artifact,
                expected_device=self.pinokio.stat().st_dev,
                limit=100,
            )
        before = runtime.music3_tree_sha256(tree)
        artifact.write_bytes(b"changed bytes")
        self.assertNotEqual(runtime.music3_tree_sha256(tree), before)
        hardlink = tree / "hardlink.bin"
        os.link(artifact, hardlink)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.music3_tree_sha256(tree)
        hardlink.unlink()
        artifact.unlink()
        outside = self.runtime_plan.layout.root / "outside.bin"
        outside.write_bytes(b"outside")
        outside.chmod(0o600)
        artifact.symlink_to(outside)
        with self.assertRaises(runtime.Music3RuntimeSecurityError):
            runtime.music3_tree_sha256(tree)

    def test_cache_verification_detects_partial_and_wrong_content(self):
        plan = self.plan()
        item = plan.document["fetch_phase"]["downloads"][0]
        partial = Path(item["partial_path"])
        runtime._ensure_private_runtime_directory(self.runtime_plan.layout, partial.parent)
        partial.write_bytes(b"partial")
        partial.chmod(0o600)
        with self.assertRaisesRegex(builder.Music3StageBuilderBlocked, "partial"):
            builder.verify_music3_download_cache(plan)
        partial.unlink()

        expected_by_path = {
            str(entry["completed_path"]): entry["expected_sha256"]
            for entry in plan.document["fetch_phase"]["downloads"]
        }
        with mock.patch.object(
            builder,
            "_regular_file_sha256",
            side_effect=lambda path, **_kwargs: expected_by_path[str(path)],
        ):
            result = builder.verify_music3_download_cache(plan)
        self.assertTrue(result["verified"])
        self.assertFalse(result["network_used"])

        with mock.patch.object(
            builder,
            "_regular_file_sha256",
            return_value=_fake_sha("wrong"),
        ), self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "digest"):
            builder.verify_music3_download_cache(plan)

    def test_runtime_stage_manifest_is_exact_and_requires_independent_digest_before_probe(self):
        plan = self.plan()
        expected = runtime.build_music3_stage_manifest(
            self.runtime_plan,
            generation_id="generation-1",
            **self.manifest()["tree_expectations"],
            **self.runtime_artifact_records(),
        )
        self.assertEqual(plan.document["runtime_stage_manifest"], expected)
        self.assertEqual(
            plan.document["runtime_stage_manifest_sha256"],
            builder._mapping_sha256(expected),
        )
        validate = mock.Mock(return_value=(expected, builder._mapping_sha256(expected)))
        lock = mock.MagicMock()
        lock.__enter__.return_value = None
        lock.__exit__.return_value = False
        with mock.patch.object(runtime, "_validate_stage", validate), mock.patch.object(
            runtime,
            "_generation_lock",
            return_value=lock,
        ) as generation_lock, mock.patch.object(
            builder,
            "_tree_total_bytes",
            return_value=0,
        ):
            with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "approved"):
                builder.verify_music3_offline_stage(
                    plan,
                    expected_stage_manifest_sha256=_fake_sha("wrong"),
                    ucx_probe_output=b"not used",
                )
            validate.assert_not_called()
            result = builder.verify_music3_offline_stage(
                plan,
                expected_stage_manifest_sha256=plan.document["runtime_stage_manifest_sha256"],
                ucx_probe_output=b"already captured UCX evidence",
            )
        generation_lock.assert_called_once_with(
            self.runtime_plan.layout,
            Path(plan.document["final_generation_path"]),
            exclusive=False,
        )
        self.assertTrue(result["verified"])
        self.assertFalse(result["network_used"])
        self.assertFalse(result["published"])

        validate.reset_mock()
        with mock.patch.object(runtime, "_validate_stage", validate), mock.patch.object(
            runtime,
            "_generation_lock",
            return_value=lock,
        ), mock.patch.object(
            builder,
            "_tree_total_bytes",
            return_value=1 << 50,
        ), self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "disk budget"):
            builder.verify_music3_offline_stage(
                plan,
                expected_stage_manifest_sha256=plan.document["runtime_stage_manifest_sha256"],
                ucx_probe_output=b"already captured UCX evidence",
            )

    def test_resume_identity_and_crash_windows_are_fail_closed(self):
        plan = self.plan()
        for phase in ("fetching", "fetched", "staging"):
            with self.subTest(phase=phase):
                record = self.resume_record(plan, phase)
                self.assertEqual(builder.validate_music3_resume_record(plan, record), record)
                status = builder.music3_stage_recovery_status(plan, record)
                self.assertEqual(status["state"], f"resume_{phase}")
        bad = self.resume_record(plan, "fetching")
        bad["resume_identity"] = _fake_sha("other-plan")
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "resume"):
            builder.validate_music3_resume_record(plan, bad)

        generation = Path(plan.document["final_generation_path"])
        generation.mkdir(parents=True, mode=0o700)
        self.runtime_plan.layout.generations.chmod(0o700)
        generation.chmod(0o700)
        generation_lock = generation / runtime.GENERATION_LOCK_NAME
        generation_lock.write_bytes(b"")
        generation_lock.chmod(0o600)
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "unbound"):
            builder.music3_stage_recovery_status(plan)
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "overlaps"):
            builder.music3_stage_recovery_status(plan, self.resume_record(plan, "fetched"))
        staged = builder.music3_stage_recovery_status(plan, self.resume_record(plan, "staged"))
        self.assertEqual(staged["next_phase"], "verify-stage")

        generation_lock.unlink()
        generation.rmdir()
        outside = self.scratch / "outside-generation"
        outside.mkdir(mode=0o700)
        generation.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(builder.Music3StageBuilderSecurityError):
            builder.music3_stage_recovery_status(plan, self.resume_record(plan, "staging"))

    def test_resume_record_path_and_runtime_owned_generation_are_exact(self):
        plan = self.plan()
        record = self.resume_record(plan, "staging")
        record_path = Path(plan.document["resume_record_path"])
        runtime._ensure_private_runtime_directory(self.runtime_plan.layout, record_path.parent)
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)
        self.assertEqual(builder.load_music3_resume_record(plan, record_path), record)
        with self.assertRaisesRegex(builder.Music3StageBuilderSecurityError, "path"):
            builder.load_music3_resume_record(plan, record_path.with_name("other.json"))

        current = {
            "schema": runtime.RUNTIME_SCHEMA,
            "plan": self.runtime_plan.to_mapping(),
            "plan_sha256": self.runtime_plan.sha256,
            "filesystem_capability": FILESYSTEM_CAPABILITY,
            "current": {
                "path": plan.document["final_generation_path"],
                "stage_manifest_sha256": plan.document["runtime_stage_manifest_sha256"],
                "generation_id": plan.document["generation_id"],
            },
            "previous": None,
        }
        generation = Path(plan.document["final_generation_path"])
        generation.mkdir(parents=True, mode=0o700)
        self.runtime_plan.layout.generations.chmod(0o700)
        generation.chmod(0o700)
        self.runtime_plan.layout.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime._atomic_json(self.runtime_plan.layout.current_marker, current)
        with self.assertRaisesRegex(builder.Music3StageBuilderBlocked, "runtime-owned"):
            builder.music3_stage_recovery_status(plan, record)

        current["current"]["path"] = str(
            self.runtime_plan.layout.generations / "another-generation"
        )
        current["current"]["generation_id"] = "another-generation"
        Path(current["current"]["path"]).mkdir(mode=0o700)
        Path(current["current"]["path"]).chmod(0o700)
        current["previous"] = {
            "path": plan.document["final_generation_path"],
            "stage_manifest_sha256": plan.document["runtime_stage_manifest_sha256"],
            "generation_id": plan.document["generation_id"],
        }
        runtime._atomic_json(self.runtime_plan.layout.current_marker, current)
        with self.assertRaisesRegex(builder.Music3StageBuilderBlocked, "runtime-owned"):
            builder.music3_stage_recovery_status(plan, record)

    def test_cli_surface_is_plan_and_verify_only(self):
        source = (APP / "scripts" / "build_music3_stage.py").read_text(encoding="utf-8")
        for command in ("plan", "verify-cache", "resume-status"):
            self.assertIn(f'add_parser("{command}")', source)
        for forbidden in (
            "download", "fetch", "build", "install", "promote", "publish", "verify-stage",
        ):
            self.assertNotIn(f'add_parser("{forbidden}")', source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("urllib", source)

        with mock.patch.object(builder_cli, "_print") as output:
            self.assertEqual(builder_cli.main([
                "plan", "--pinokio-root", str(self.pinokio),
            ]), 0)
        rendered = output.call_args.args[0]
        self.assertEqual(rendered["plan"]["status"], "blocked")

        plan = self.plan()
        record = self.resume_record(plan, "staging")
        status = {
            "state": "resume_staging",
            "resume_identity": plan.document["resume_identity"],
            "next_phase": "staging",
            "mutation": False,
        }
        with mock.patch.object(builder_cli, "_plan", return_value=plan), mock.patch.object(
            builder_cli, "load_music3_resume_record", return_value=record,
        ) as load, mock.patch.object(
            builder_cli, "music3_stage_recovery_status", return_value=status,
        ) as recovery, mock.patch.object(builder_cli, "_print") as output:
            self.assertEqual(builder_cli.main([
                "resume-status",
                "--pinokio-root", str(self.pinokio),
                "--reviewed-manifest", "reviewed.json",
                "--expected-reviewed-manifest-sha256", _fake_sha("reviewed"),
                "--resume-record", "resume.json",
            ]), 0)
        load.assert_called_once_with(plan, "resume.json")
        recovery.assert_called_once_with(plan, record)
        output.assert_called_once_with(status)


if __name__ == "__main__":
    unittest.main()
