"""Source-, network-, build-, filesystem-, runtime-, and GPU-free closure tests."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import music3_dependency_closure as closure
from services import music3_runtime as runtime


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _encode(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )


class Music3DependencyClosureTests(unittest.TestCase):
    def seed(self):
        return json.loads(closure.reviewed_music3_dependency_seed_bytes().decode("ascii"))

    def package(self, name, version, *, provenance="index-wheel"):
        normalized = name.replace("-", "_")
        return {
            "name": name,
            "version": version,
            "requirement": f"{name}=={version}",
            "artifact_id": f"wheel-{name}-{version}",
            "filename": f"{normalized}-{version}-py3-none-any.whl",
            "url": f"https://artifacts.example.test/{normalized}-{version}-py3-none-any.whl",
            "sha256": _sha(f"wheel:{name}:{version}"),
            "size": 1000 + len(name),
            "dependencies": [],
            "dependency_metadata_complete": True,
            "provenance": provenance,
            "build_source_sha256": _sha(f"source:{name}") if provenance == "source-built-wheel" else None,
        }

    def complete(self):
        value = self.seed()
        by_name = {item["name"]: item for item in value["packages"]}
        for item in value["packages"]:
            item["dependency_metadata_complete"] = True

        source_packages = [
            self.package("logger", "1.4", provenance="source-built-wheel"),
            self.package("s3prl", "0.4.18", provenance="source-built-wheel"),
            self.package("openai-whisper", "20250625", provenance="source-built-wheel"),
        ]
        value["packages"].extend(source_packages)
        reviewed_roots = {
            requirement.partition("==")[0].partition("[")[0].replace("_", "-")
            for requirement in runtime.REQUIRED_RUNTIME_LOCK_LINES
        }
        known_transitives = [
            item["requirement"]
            for item in value["packages"]
            if item["name"].replace("_", "-") not in reviewed_roots
            and item["name"] not in {package["name"] for package in source_packages}
        ]
        by_name["sglang"]["dependencies"] = [
            item["requirement"] for item in source_packages
        ] + known_transitives
        value["source_build_blockers"] = []

        cuda_packages = []
        providers = []
        for component in value["cuda_closure"]["required_components"]:
            package = self.package(f"nvidia-{component}", "1.0.0")
            cuda_packages.append(package)
            providers.append({
                "component": component,
                "requirement": package["requirement"],
            })
        value["packages"].extend(cuda_packages)
        by_name["torch"]["dependencies"] = [
            item["requirement"] for item in cuda_packages
        ]
        value["cuda_closure"].update({
            "status": "complete",
            "providers": providers,
            "unresolved_components": [],
            "evidence_sha256": _sha("cuda-closure"),
        })
        value["resolution"] = {
            "transitive_complete": True,
            "resolver": "uv",
            "resolver_version": "0.8.22",
            "report_sha256": _sha("resolver-report"),
            "offline_replay_sha256": _sha("offline-replay"),
        }
        return value

    def assert_rejected(self, value, pattern):
        with self.assertRaisesRegex(closure.Music3DependencyClosureError, pattern):
            closure.build_music3_dependency_closure_plan(_encode(value))

    def test_reviewed_seed_is_deterministic_truthful_and_blocked(self):
        first_payload = closure.reviewed_music3_dependency_seed_bytes()
        second_payload = closure.reviewed_music3_dependency_seed_bytes()
        self.assertEqual(first_payload, second_payload)
        plan = closure.build_music3_dependency_closure_plan(first_payload)
        self.assertEqual(plan.document["schema"], closure.DEPENDENCY_PLAN_SCHEMA)
        self.assertEqual(plan.document["status"], "blocked")
        self.assertFalse(plan.stage_builder_handoff_ready)
        self.assertFalse(plan.document["mutation"])
        self.assertFalse(plan.document["installability_claimed"])
        self.assertFalse(plan.document["stage_execution_authorized"])
        self.assertEqual(
            plan.document["source_revision"],
            "git:573ce7963fa7b95596459957a195c87cf60cda19",
        )
        self.assertEqual(plan.document["target"]["python_abi"], "cp312")
        self.assertEqual(plan.document["target"]["glibc_minimum"], "2.35")
        self.assertEqual(
            set(plan.document["blockers"]),
            {
                "complete_hashed_transitive_wheel_lock_missing",
                "full_torch_sglang_cuda_wheel_closure_missing",
                "source_built_wheel_missing:logger",
                "source_built_wheel_missing:openai-whisper",
                "source_built_wheel_missing:s3prl",
            },
        )
        self.assertEqual(plan.sha256, closure.build_music3_dependency_closure_plan(first_payload).sha256)
        with self.assertRaises(closure.Music3DependencyClosureBlocked):
            closure.validate_complete_music3_dependency_closure(
                first_payload,
                expected_complete_input_sha256=plan.document["input_sha256"],
            )

    def test_seed_binds_reviewed_python_and_known_wheel_metadata(self):
        plan = closure.build_music3_dependency_closure_plan(
            closure.reviewed_music3_dependency_seed_bytes()
        ).document
        python = plan["python_artifact"]
        self.assertEqual(python["version"], "3.12.14")
        self.assertEqual(python["size"], 34_143_739)
        self.assertEqual(
            python["sha256"],
            "sha256:5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc",
        )
        packages = {item["name"]: item for item in plan["packages"]}
        expected = {
            "torch": ("2.11.0", 530_712_279, "0f68f4ac6d95d12e896c3b7a912b5871619542ec54d3649cf48cc1edd4dd2756"),
            "sglang": ("0.5.16", 14_614_041, "b8ed16e72c7d6a643e31ba52e3ff106439e9dbb78543950e4c810158b826ea8e"),
            "mooncake-transfer-engine-cuda13": ("0.3.10", 42_772_945, "5632c0f97a0cd5db639cf97e33f3fc47cbcb1b8fb0b1cc415e959f814c5de672"),
        }
        for name, (version, size, digest) in expected.items():
            with self.subTest(name=name):
                self.assertEqual(packages[name]["version"], version)
                self.assertEqual(packages[name]["size"], size)
                self.assertEqual(packages[name]["sha256"], "sha256:" + digest)
        self.assertEqual(
            {item["name"] for item in plan["source_build_blockers"]},
            {"logger", "s3prl", "openai-whisper"},
        )
        self.assertEqual(
            plan["cuda_closure"]["unresolved_components"],
            plan["cuda_closure"]["required_components"],
        )

    def test_complete_graph_is_only_reviewable_dependency_evidence(self):
        value = self.complete()
        payload = _encode(value)
        draft = closure.build_music3_dependency_closure_plan(payload)
        self.assertIn("independent_complete_input_review_missing", draft.document["blockers"])
        self.assertFalse(draft.stage_builder_handoff_ready)
        plan = closure.validate_complete_music3_dependency_closure(
            payload,
            expected_complete_input_sha256=draft.document["input_sha256"],
        )
        document = plan.document
        self.assertTrue(plan.stage_builder_handoff_ready)
        self.assertEqual(document["status"], "dependency-evidence-complete")
        self.assertFalse(document["installability_claimed"])
        self.assertFalse(document["stage_execution_authorized"])
        self.assertEqual(document["blockers"], [])
        self.assertEqual(
            document["stage_builder_handoff"]["compatible_input_schema"],
            "maestro.music3.stage-builder-input.v1",
        )
        requirements = sorted(item["requirement"] for item in value["packages"])
        expected_lock = _sha("\n".join(requirements) + "\n")
        self.assertEqual(
            document["stage_builder_handoff"]["dependency_lock_sha256"],
            expected_lock,
        )
        self.assertTrue(runtime.REQUIRED_RUNTIME_LOCK_LINES.issubset(set(requirements)))
        self.assertGreater(len(document["edges"]), 0)

    def test_equivalent_orderings_produce_identical_plan(self):
        first = self.complete()
        second = copy.deepcopy(first)
        second["packages"].reverse()
        second["roots"].reverse()
        second["cuda_closure"]["providers"].reverse()
        for item in second["packages"]:
            item["dependencies"].reverse()
        left = closure.build_music3_dependency_closure_plan(_encode(first))
        right = closure.build_music3_dependency_closure_plan(_encode(second))
        self.assertEqual(left.document, right.document)
        self.assertEqual(left.sha256, right.sha256)
        self.assertEqual(left.document["closure_sha256"], right.document["closure_sha256"])

    def test_hostile_or_noncanonical_json_is_rejected(self):
        valid = closure.reviewed_music3_dependency_seed_bytes()
        hostile = [
            b'{"schema":"one","schema":"two"}\n',
            b'{"value":NaN}\n',
            b"\xef\xbb\xbf" + valid,
            b"[]\n",
            b"{}",
            json.dumps(self.seed(), indent=2).encode("utf-8") + b"\n",
            b"{",
        ]
        for payload in hostile:
            with self.subTest(payload=payload[:24]), self.assertRaises(
                closure.Music3DependencyClosureError
            ):
                closure.build_music3_dependency_closure_plan(payload)
        with self.assertRaisesRegex(closure.Music3DependencyClosureError, "bytes"):
            closure.build_music3_dependency_closure_plan(bytearray(valid))
        with self.assertRaisesRegex(closure.Music3DependencyClosureError, "bound"):
            closure.build_music3_dependency_closure_plan(b" " * (closure.MAX_INPUT_BYTES + 1))

    def test_identity_target_and_python_artifact_drift_fail_closed(self):
        mutations = [
            ("source_revision", "git:" + "0" * 40, "reviewed commit"),
            ("target.python_abi", "cp311", "CPython 3.12"),
            ("target.glibc_minimum", "2.34", "glibc 2.35"),
            ("python_artifact.size", 1, "reviewed CPython"),
            ("python_artifact.sha256", _sha("wrong-python"), "reviewed CPython"),
        ]
        for path, replacement, pattern in mutations:
            value = self.seed()
            target = value
            parts = path.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = replacement
            with self.subTest(path=path):
                self.assert_rejected(value, pattern)

    def test_unpinned_sdist_foreign_abi_platform_and_known_drift_are_rejected(self):
        cases = [
            ("requirement", "torch>=2.11.0", "exact pinned"),
            ("filename", "torch-2.11.0.tar.gz", "wheels, never sdists"),
            ("filename", "torch-2.11.0-cp311-cp311-manylinux_2_28_x86_64.whl", "CPython 3.12"),
            ("filename", "torch-2.11.0-cp312-cp312-musllinux_1_2_x86_64.whl", "glibc 2.35"),
            ("filename", "torch-2.11.0-cp312-cp312-linux_x86_64.whl", "glibc 2.35"),
            ("filename", "torch-2.11.0-cp312-cp312-manylinux_2_36_x86_64.whl", "glibc 2.35"),
            ("sha256", _sha("drift"), "primary-source evidence"),
        ]
        for field, replacement, pattern in cases:
            value = self.seed()
            package = next(item for item in value["packages"] if item["name"] == "torch")
            package[field] = replacement
            with self.subTest(field=field, replacement=replacement):
                self.assert_rejected(value, pattern)

    def test_duplicate_package_artifact_filename_and_digest_are_rejected(self):
        cases = []
        value = self.seed()
        value["packages"].append(copy.deepcopy(value["packages"][0]))
        cases.append((value, "duplicate normalized packages"))
        for field in ("artifact_id", "filename", "sha256"):
            value = self.seed()
            value["packages"][1][field] = value["packages"][0][field]
            cases.append((value, f"duplicate {field}"))
        for value, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_rejected(value, pattern)

    def test_graph_rejects_unknown_self_duplicate_cycle_and_unreachable_nodes(self):
        cases = []
        value = self.complete()
        value["packages"][0]["dependencies"] = ["missing==1.0.0"]
        cases.append((value, "unresolved"))

        value = self.complete()
        item = next(package for package in value["packages"] if package["name"] == "torch")
        item["dependencies"] = ["torch==2.11.0"]
        cases.append((value, "self edge"))

        value = self.complete()
        item = next(package for package in value["packages"] if package["name"] == "torch")
        item["dependencies"].append(item["dependencies"][0])
        cases.append((value, "duplicate edges"))

        value = self.complete()
        torch = next(package for package in value["packages"] if package["name"] == "torch")
        cuda = next(package for package in value["packages"] if package["name"] == "nvidia-cuda-bindings")
        cuda["dependencies"] = ["torch==2.11.0"]
        self.assertIn(cuda["requirement"], torch["dependencies"])
        cases.append((value, "cycle"))

        value = self.complete()
        value["packages"].append(self.package("orphan", "1.0.0"))
        cases.append((value, "unreachable"))

        value = self.complete()
        value["roots"] = sorted(package["requirement"] for package in value["packages"])
        cases.append((value, "reviewed runtime root set"))

        for value, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_rejected(value, pattern)

    def test_incomplete_claim_blockers_and_cudart_alone_fail_closed(self):
        value = self.seed()
        value["source_build_blockers"].pop()
        self.assert_rejected(value, "complete or resolved")

        value = self.seed()
        value["source_build_blockers"] = []
        self.assert_rejected(value, "mandatory source-built wheel is unresolved")

        value = self.complete()
        value["source_build_blockers"] = [
            copy.deepcopy(self.seed()["source_build_blockers"][0])
        ]
        self.assert_rejected(value, "complete or resolved")

        value = self.complete()
        value["packages"][0]["dependency_metadata_complete"] = False
        self.assert_rejected(value, "incomplete transitive metadata")

        value = self.complete()
        value["cuda_closure"]["providers"] = [
            provider
            for provider in value["cuda_closure"]["providers"]
            if provider["component"] == "cudart"
        ]
        self.assert_rejected(value, "CUDA wheel closure is incomplete")

        value = self.complete()
        cudart_requirement = next(
            provider["requirement"]
            for provider in value["cuda_closure"]["providers"]
            if provider["component"] == "cudart"
        )
        for provider in value["cuda_closure"]["providers"]:
            provider["requirement"] = cudart_requirement
        self.assert_rejected(value, "distinct selected wheel")

        value = self.complete()
        cudart_requirement = next(
            provider["requirement"]
            for provider in value["cuda_closure"]["providers"]
            if provider["component"] == "cudart"
        )
        value["cuda_closure"]["providers"][0]["requirement"] = cudart_requirement.replace(
            "-", "_"
        )
        self.assert_rejected(value, "distinct selected wheel|exactly match")

        value = self.complete()
        value["cuda_closure"]["status"] = "unresolved"
        value["cuda_closure"]["evidence_sha256"] = None
        value["cuda_closure"]["unresolved_components"] = ["cudart"]
        self.assert_rejected(value, "contradictory")

    def test_complete_claim_requires_source_built_wheels_and_resolver_evidence(self):
        value = self.complete()
        value["packages"] = [
            package for package in value["packages"] if package["name"] != "logger"
        ]
        sglang = next(package for package in value["packages"] if package["name"] == "sglang")
        sglang["dependencies"] = [
            dependency for dependency in sglang["dependencies"] if not dependency.startswith("logger==")
        ]
        self.assert_rejected(value, "source-built wheel is unresolved")

        for field, replacement, pattern in (
            ("resolver", "pip", "reviewed resolver"),
            ("resolver_version", None, "version is not exact"),
            ("report_sha256", None, "SHA-256"),
            ("offline_replay_sha256", None, "SHA-256"),
        ):
            value = self.complete()
            value["resolution"][field] = replacement
            with self.subTest(field=field):
                self.assert_rejected(value, pattern)

    def test_long_acyclic_graph_is_bounded_without_python_recursion(self):
        value = self.seed()
        chain = [self.package(f"chain{index:04d}", "1.0.0") for index in range(1100)]
        for index, package in enumerate(chain[:-1]):
            package["dependencies"] = [chain[index + 1]["requirement"]]
            package["dependency_metadata_complete"] = False
        chain[-1]["dependency_metadata_complete"] = False
        torch = next(package for package in value["packages"] if package["name"] == "torch")
        torch["dependencies"] = [chain[0]["requirement"]]
        value["packages"].extend(chain)
        plan = closure.build_music3_dependency_closure_plan(_encode(value))
        self.assertEqual(plan.document["status"], "blocked")
        self.assertEqual(len(plan.document["edges"]), 1100)

    def test_complete_handoff_requires_matching_independent_review_digest(self):
        payload = _encode(self.complete())
        draft = closure.build_music3_dependency_closure_plan(payload)
        self.assertFalse(draft.document["independent_review_bound"])
        self.assertFalse(draft.document["stage_builder_handoff"]["ready"])
        with self.assertRaisesRegex(
            closure.Music3DependencyClosureSecurityError,
            "independently reviewed digest",
        ):
            closure.validate_complete_music3_dependency_closure(
                payload,
                expected_complete_input_sha256=_sha("wrong-input"),
            )

    def test_source_has_no_execution_network_filesystem_or_installer_authority(self):
        source = (APP / "services" / "music3_dependency_closure.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(
            imports.isdisjoint(
                {"subprocess", "socket", "requests", "httpx", "urllib", "os", "pathlib", "shutil"}
            )
        )
        forbidden_names = {
            "download", "fetch", "install", "build_wheel", "execute", "run",
            "start", "publish", "promote", "probe", "open", "write_text", "write_bytes",
        }
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        self.assertTrue(called.isdisjoint(forbidden_names), called & forbidden_names)


if __name__ == "__main__":
    unittest.main()
