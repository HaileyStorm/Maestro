"""Source-only tests for the blocked H3 dependency candidate document."""

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

from services import h3_prompt_rewriter as rewriter
from services import h3_prompt_rewriter_dependency_closure as closure


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _encode(value: object) -> bytes:
    return _canonical(value) + b"\n"


def _inventory_sha(value: dict[str, object]) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "runtime_target": value["runtime_target"],
                "root_requirements": value["root_requirements"],
                "packages": value["packages"],
            }
        )
    ).hexdigest()


class H3PromptRewriterDependencyClosureTests(unittest.TestCase):
    def seed(self) -> dict[str, object]:
        return json.loads(
            closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes().decode("ascii")
        )

    def candidate(self, name: str, version: str) -> dict[str, object]:
        normalized = name.replace("-", "_")
        binary = name in {
            "pillow",
            "safetensors",
            "tokenizers",
            "torch",
            "torchvision",
        }
        tags = "cp312-cp312-manylinux_2_28_x86_64" if binary else "py3-none-any"
        return {
            "wheel_name": f"{normalized}-{version}-{tags}.whl",
            "sha256": _sha(f"unreviewed:{name}:{version}"),
            "size_bytes": 1000 + len(name),
            "provenance": "unreviewed_candidate",
        }

    def fabricated_completion_claim(self) -> dict[str, object]:
        value = self.seed()
        for package in value["packages"]:
            package["dependency_metadata_complete"] = True
            package["wheel_candidates"] = [
                self.candidate(package["name"], package["version"])
            ]
        inventory_sha = _inventory_sha(value)
        value["resolution_claim"] = {
            "transitive_complete": True,
            "resolver": "uv",
            "resolver_version": "0.8.22",
            "resolver_report_sha256": _sha("fabricated-resolver-report"),
            "resolver_inventory_sha256": inventory_sha,
            "offline_replay_sha256": _sha("fabricated-offline-replay"),
            "offline_replay_inventory_sha256": inventory_sha,
        }
        return value

    def assert_rejected(self, value: object, pattern: str) -> None:
        with self.assertRaisesRegex(
            closure.H3PromptRewriterDependencyClosureError, pattern
        ):
            closure.build_h3_prompt_rewriter_dependency_closure_plan(_encode(value))

    def test_seed_is_deterministic_unresolved_and_always_blocked(self):
        first = closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes()
        second = closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes()
        self.assertEqual(first, second)
        plan = closure.build_h3_prompt_rewriter_dependency_closure_plan(first)
        document = plan.document
        self.assertEqual(document["schema"], closure.DEPENDENCY_PLAN_SCHEMA)
        self.assertEqual(document["status"], "blocked")
        self.assertFalse(document["mutation"])
        self.assertFalse(document["installability_claimed"])
        self.assertFalse(document["installation_authorized"])
        self.assertFalse(document["execution_authorized"])
        self.assertFalse(document["runtime_accepted"])
        self.assertFalse(document["gpu_accepted"])
        self.assertFalse(document["input_integrity_bound"])
        self.assertEqual(
            set(document["blockers"]),
            {
                "complete_hashed_transitive_wheel_closure_missing",
                "durable_reviewed_artifact_receipts_missing",
                "input_integrity_binding_missing",
                "offline_replay_evidence_missing",
                "target_wheel_candidate_inventory_incomplete",
            },
        )
        self.assertEqual(
            plan.sha256,
            closure.build_h3_prompt_rewriter_dependency_closure_plan(first).sha256,
        )

    def test_exact_roots_include_reviewed_upstream_image_dependencies(self):
        document = closure.build_h3_prompt_rewriter_dependency_closure_plan(
            closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes()
        ).document
        environment = document["environment_candidates"]
        self.assertEqual(environment["runtime_target"]["python_abi"], "cp312")
        self.assertEqual(environment["runtime_target"]["python_version"], "3.12.14")
        self.assertEqual(environment["runtime_target"]["glibc_minimum"], "2.35")
        self.assertEqual(
            environment["root_requirements"],
            [
                "accelerate==1.12.0",
                "peft==0.20.0",
                "pillow==12.2.0",
                "safetensors==0.8.0",
                "tokenizers==0.22.1",
                "torch==2.10.0+cu128",
                "torchvision==0.25.0+cu128",
                "transformers==4.57.1",
            ],
        )
        package_requirements = {item["requirement"] for item in environment["packages"]}
        self.assertEqual(package_requirements, set(environment["root_requirements"]))
        receipts = document["model_receipt_dependencies"]
        self.assertEqual(receipts["adapter"], rewriter.adapter_descriptor())
        self.assertEqual(receipts["base"], rewriter.base_descriptor())
        self.assertFalse(document["model_receipts_in_environment_candidates"])
        self.assertNotIn("model_receipt_dependencies", environment)
        encoded = json.dumps(document, sort_keys=True)
        self.assertNotIn(str(ROOT), encoded)
        self.assertNotIn("/home/", encoded)
        self.assertNotIn("/mnt/", encoded)

    def test_fabricated_completion_and_integrity_binding_never_authorize(self):
        value = self.fabricated_completion_claim()
        payload = _encode(value)
        unbound = closure.build_h3_prompt_rewriter_dependency_closure_plan(payload)
        self.assertEqual(unbound.document["status"], "blocked")
        self.assertIn(
            "durable_reviewed_artifact_receipts_missing",
            unbound.document["blockers"],
        )
        self.assertIn(
            "resolution_and_replay_claims_unreviewed", unbound.document["blockers"]
        )
        self.assertFalse(unbound.document["execution_authorized"])

        bound = closure.build_h3_prompt_rewriter_dependency_closure_plan(
            payload, expected_input_sha256=unbound.document["input_sha256"]
        )
        self.assertTrue(bound.document["input_integrity_bound"])
        self.assertEqual(bound.document["status"], "blocked")
        self.assertNotIn("input_integrity_binding_missing", bound.document["blockers"])
        self.assertIn(
            "durable_reviewed_artifact_receipts_missing", bound.document["blockers"]
        )
        self.assertFalse(bound.document["installability_claimed"])
        self.assertFalse(bound.document["installation_authorized"])
        self.assertFalse(bound.document["execution_authorized"])

    def test_wheel_candidates_reject_pure_binary_wrong_tags_and_multiple_selection(
        self,
    ):
        cases = [
            (
                "torch-2.10.0+cu128-py3-none-any.whl",
                "pure-Python wheel",
            ),
            (
                "torch-2.10.0+cu128-cp311-cp311-manylinux_2_28_x86_64.whl",
                "ABI is not compatible",
            ),
            (
                "torch-2.10.0+cu128-cp312-cp312-win_amd64.whl",
                "platform exceeds",
            ),
            (
                "torch-2.10.0+cu128-cp312-cp312-manylinux_2_36_x86_64.whl",
                "platform exceeds",
            ),
            (
                "torch-2.10.0+cu128-cp312-cp312-linux_x86_64.whl",
                "platform exceeds",
            ),
        ]
        for wheel_name, pattern in cases:
            with self.subTest(wheel_name=wheel_name):
                value = self.seed()
                torch = next(
                    item for item in value["packages"] if item["name"] == "torch"
                )
                candidate = self.candidate("torch", "2.10.0+cu128")
                candidate["wheel_name"] = wheel_name
                torch["wheel_candidates"] = [candidate]
                self.assert_rejected(value, pattern)

        pure_vision = self.seed()
        vision = next(
            item for item in pure_vision["packages"] if item["name"] == "torchvision"
        )
        candidate = self.candidate("torchvision", "0.25.0+cu128")
        candidate["wheel_name"] = "torchvision-0.25.0+cu128-py3-none-any.whl"
        vision["wheel_candidates"] = [candidate]
        self.assert_rejected(pure_vision, "pure-Python wheel")

        multiple = self.seed()
        torch = next(item for item in multiple["packages"] if item["name"] == "torch")
        candidate = self.candidate("torch", "2.10.0+cu128")
        torch["wheel_candidates"] = [candidate, copy.deepcopy(candidate)]
        self.assert_rejected(multiple, "at most one wheel candidate")

    def test_nvidia_transitive_py3_none_manylinux_is_narrowly_supported(self):
        value = self.seed()
        nvidia = {
            "name": "nvidia-cublas-cu12",
            "version": "12.8.4.1",
            "requirement": "nvidia-cublas-cu12==12.8.4.1",
            "dependencies": [],
            "dependency_metadata_complete": False,
            "wheel_candidates": [
                {
                    "wheel_name": (
                        "nvidia_cublas_cu12-12.8.4.1-py3-none-manylinux_2_27_x86_64.whl"
                    ),
                    "sha256": _sha("unreviewed:nvidia-cublas-cu12:12.8.4.1"),
                    "size_bytes": 1000,
                    "provenance": "unreviewed_candidate",
                }
            ],
        }
        value["packages"].append(nvidia)
        value["packages"].sort(
            key=lambda item: item["name"].replace("_", "-").casefold()
        )
        torch = next(item for item in value["packages"] if item["name"] == "torch")
        torch["dependencies"] = ["nvidia-cublas-cu12==12.8.4.1"]
        plan = closure.build_h3_prompt_rewriter_dependency_closure_plan(_encode(value))
        self.assertEqual(plan.document["status"], "blocked")
        self.assertIn(
            "durable_reviewed_artifact_receipts_missing", plan.document["blockers"]
        )

        pure_nvidia = copy.deepcopy(value)
        nvidia = next(
            item
            for item in pure_nvidia["packages"]
            if item["name"] == "nvidia-cublas-cu12"
        )
        nvidia["wheel_candidates"][0]["wheel_name"] = (
            "nvidia_cublas_cu12-12.8.4.1-py3-none-any.whl"
        )
        self.assert_rejected(pure_nvidia, "pure-Python wheel")

        arbitrary = self.seed()
        transformers = next(
            item for item in arbitrary["packages"] if item["name"] == "transformers"
        )
        candidate = self.candidate("transformers", "4.57.1")
        candidate["wheel_name"] = (
            "transformers-4.57.1-py3-none-manylinux_2_27_x86_64.whl"
        )
        transformers["wheel_candidates"] = [candidate]
        self.assert_rejected(arbitrary, "restricted to NVIDIA transitive packages")

        legacy = copy.deepcopy(value)
        nvidia = next(
            item for item in legacy["packages"] if item["name"] == "nvidia-cublas-cu12"
        )
        nvidia["wheel_candidates"][0]["wheel_name"] = (
            "nvidia_cublas_cu12-12.8.4.1-py3-none-"
            "manylinux2010_x86_64.manylinux_2_12_x86_64.whl"
        )
        closure.build_h3_prompt_rewriter_dependency_closure_plan(_encode(legacy))

        pure = self.seed()
        urllib3 = next(
            item for item in pure["packages"] if item["name"] == "transformers"
        )
        candidate = self.candidate("transformers", "4.57.1")
        candidate["wheel_name"] = "transformers-4.57.1-py2.py3-none-any.whl"
        urllib3["wheel_candidates"] = [candidate]
        closure.build_h3_prompt_rewriter_dependency_closure_plan(_encode(pure))

        fabricated = self.fabricated_completion_claim()
        vision = next(
            item for item in fabricated["packages"] if item["name"] == "torchvision"
        )
        self.assertEqual(vision["version"], "0.25.0+cu128")
        self.assertTrue(
            vision["wheel_candidates"][0]["wheel_name"].startswith(
                "torchvision-0.25.0+cu128-"
            )
        )

    def test_resolution_claim_must_bind_both_reports_to_exact_inventory(self):
        value = self.fabricated_completion_claim()
        value["resolution_claim"]["resolver_inventory_sha256"] = "0" * 64
        self.assert_rejected(value, "not bound to the inventory")

        value = self.fabricated_completion_claim()
        value["resolution_claim"]["offline_replay_inventory_sha256"] = "f" * 64
        self.assert_rejected(value, "not bound to the inventory")

        value = self.fabricated_completion_claim()
        value["packages"][0]["wheel_candidates"][0]["provenance"] = "source_bound"
        self.assert_rejected(value, "may not self-assert reviewed provenance")

    def test_omitted_image_roots_and_cycles_fail_closed(self):
        for name in ("pillow", "torchvision"):
            value = self.seed()
            requirement = next(
                item
                for item in value["root_requirements"]
                if item.startswith(name + "==")
            )
            value["root_requirements"].remove(requirement)
            value["packages"] = [
                item for item in value["packages"] if item["name"] != name
            ]
            with self.subTest(name=name):
                self.assert_rejected(value, "exact reviewed ordered pins")

        cycle = self.seed()
        transformers = next(
            item for item in cycle["packages"] if item["name"] == "transformers"
        )
        tokenizers = next(
            item for item in cycle["packages"] if item["name"] == "tokenizers"
        )
        transformers["dependencies"] = ["tokenizers==0.22.1"]
        tokenizers["dependencies"] = ["transformers==4.57.1"]
        self.assert_rejected(cycle, "cycle")

    def test_malformed_extra_bool_path_duplicate_and_order_fail_closed(self):
        seed = self.seed()
        hostile_payloads = [
            b'{"schema":"one","schema":"two"}\n',
            b'{"value":NaN}\n',
            b"\xef\xbb\xbf" + _encode(seed),
            b"[]\n",
            b"{}",
            json.dumps(seed, indent=2).encode("utf-8") + b"\n",
            b"{",
        ]
        for payload in hostile_payloads:
            with (
                self.subTest(payload=payload[:24]),
                self.assertRaises(closure.H3PromptRewriterDependencyClosureError),
            ):
                closure.build_h3_prompt_rewriter_dependency_closure_plan(payload)

        extra = copy.deepcopy(seed)
        extra["unexpected"] = False
        self.assert_rejected(extra, "fields are not exact")

        bool_complete = copy.deepcopy(seed)
        bool_complete["resolution_claim"]["transitive_complete"] = 1
        self.assert_rejected(bool_complete, "must be explicit")

        bool_receipt = copy.deepcopy(seed)
        bool_receipt["model_receipt_dependencies"]["base"]["runtime_accepted"] = 0
        self.assert_rejected(bool_receipt, "identity drifted")

        path_field = copy.deepcopy(seed)
        path_field["runtime_target"]["cwd"] = "/tmp/private"
        with self.assertRaisesRegex(
            closure.H3PromptRewriterDependencyClosureSecurityError, "path-like"
        ):
            closure.build_h3_prompt_rewriter_dependency_closure_plan(
                _encode(path_field)
            )

        duplicate = copy.deepcopy(seed)
        duplicate["packages"].append(copy.deepcopy(duplicate["packages"][0]))
        duplicate["packages"].sort(
            key=lambda item: item["name"].replace("_", "-").casefold()
        )
        self.assert_rejected(duplicate, "duplicate package")

        reversed_packages = self.seed()
        reversed_packages["packages"].reverse()
        self.assert_rejected(reversed_packages, "canonical order")

        reversed_roots = self.seed()
        reversed_roots["root_requirements"].reverse()
        self.assert_rejected(reversed_roots, "ordered pins")

    def test_separation_claim_is_narrow_and_source_has_no_runtime_authority(self):
        document = closure.build_h3_prompt_rewriter_dependency_closure_plan(
            closure.reviewed_h3_prompt_rewriter_dependency_seed_bytes()
        ).document
        self.assertEqual(
            document["separation_scope"],
            {
                "python_requirements_mutation_authorized": False,
                "dockerfile_reviewed": False,
                "repository_wide_separation_claimed": False,
            },
        )

        source = (
            APP / "services" / "h3_prompt_rewriter_dependency_closure.py"
        ).read_text(encoding="utf-8")
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
                {
                    "torch",
                    "torchvision",
                    "transformers",
                    "peft",
                    "safetensors",
                    "tokenizers",
                    "accelerate",
                    "PIL",
                    "subprocess",
                    "socket",
                    "requests",
                    "httpx",
                    "urllib",
                    "os",
                    "pathlib",
                    "shutil",
                    "importlib",
                }
            )
        )
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        self.assertTrue(
            called.isdisjoint(
                {
                    "open",
                    "read_text",
                    "read_bytes",
                    "write_text",
                    "write_bytes",
                    "download",
                    "fetch",
                    "install",
                    "run",
                    "probe",
                }
            )
        )
        self.assertNotIn("requirements.txt", source)
        requirements = (APP / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("peft==0.17.0", requirements)
        self.assertNotIn("peft==0.20.0", requirements)
        self.assertNotIn("torch==2.10.0+cu128", requirements)


if __name__ == "__main__":
    unittest.main()
