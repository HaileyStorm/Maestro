from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

try:
    import torch
except ModuleNotFoundError as error:  # lightweight CI intentionally omits Torch
    raise unittest.SkipTest("Torch is required for H3 acceleration tests") from error

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_acceleration  # noqa: E402


def _load_sage_installer():
    source_path = APP / "scripts" / "install_h3_sageattention.py"
    spec = importlib.util.spec_from_file_location("install_h3_sageattention_test", source_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load SageAttention installer helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evaluate_mutated_validation_record(record):
    raw = json.dumps(record, sort_keys=True).encode("utf-8")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "validation.json"
        path.write_bytes(raw)
        with patch.object(
            h3_acceleration,
            "SAGE2_VALIDATION_RECORD_SHA256",
            hashlib.sha256(raw).hexdigest(),
        ), patch.object(
            h3_acceleration,
            "_sage2_distribution_provenance",
            return_value=(
                h3_acceleration.SAGEATTENTION_VERSION,
                h3_acceleration.SAGEATTENTION_CHECKOUT,
                record["engine"]["distribution_sha256"],
            ),
        ), patch.object(
            h3_acceleration.torch, "__version__", record["runtime"]["torch"],
        ), patch.object(
            h3_acceleration.torch.version, "cuda", record["runtime"]["torch_cuda"],
        ), patch.object(
            h3_acceleration.torch.cuda, "get_device_name", return_value=record["runtime"]["gpu"],
        ), patch.object(
            h3_acceleration.torch.cuda,
            "get_device_capability",
            return_value=tuple(record["runtime"]["compute_capability"]),
        ), patch.object(
            h3_acceleration.importlib.metadata,
            "version",
            return_value=record["runtime"]["triton"],
        ):
            return h3_acceleration._sage2_validation_record_status(path)


def _load_launch_acceleration_guard():
    source_path = APP / "launch.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    names = {
        "_trusted_h3_prepared_plan",
        "_h3_effective_model_types",
        "_require_h3_acceleration_available",
    }
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("_H3_")
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            selected.append(node)
    namespace = {}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(source_path), "exec"),
        namespace,
    )
    return namespace["_require_h3_acceleration_available"]


class H3AccelerationTests(unittest.TestCase):
    def setUp(self):
        for key in h3_acceleration._stats:
            h3_acceleration._stats[key] = 0
        h3_acceleration._sage2_error = None
        h3_acceleration._sage2_last_fallback_reason = None

    def test_dense_policy_never_invokes_optional_kernel(self):
        query = torch.zeros((1, 128, 2, 128), dtype=torch.bfloat16)
        with patch.object(h3_acceleration, "_load_sol_kernel") as loader:
            result = h3_acceleration.maybe_sol_attention(
                query, query, query,
                attention_mask=None,
                step_index=0,
                block_index=7,
                tau=1.0,
                dense_steps=10,
                dense_blocks=2,
                min_tokens=64,
                sink_tokens=32,
            )
        self.assertIsNone(result)
        loader.assert_not_called()
        self.assertEqual(h3_acceleration._stats["dense_policy"], 1)

    def test_unsupported_device_falls_back_exactly(self):
        query = torch.zeros((1, 128, 2, 128), dtype=torch.bfloat16)
        result = h3_acceleration.maybe_sol_attention(
            query, query, query,
            attention_mask=None,
            step_index=11,
            block_index=3,
            tau=1.0,
            dense_steps=10,
            dense_blocks=2,
            min_tokens=64,
            sink_tokens=32,
        )
        self.assertIsNone(result)
        self.assertEqual(h3_acceleration._stats["dense_fallback"], 1)

    def test_status_exposes_validated_w4a8_only_as_opt_in_fl2va(self):
        with patch.object(h3_acceleration, "_w4a8_capability", return_value=(True, "validated")), patch.object(
            h3_acceleration.torch.cuda, "is_available", return_value=True,
        ), patch.object(
            h3_acceleration.torch.cuda, "get_device_capability", return_value=(8, 9),
        ):
            status = h3_acceleration.get_h3_acceleration_status(probe_kernel=False)
        self.assertFalse(status["dense_sdpa"]["default"])
        self.assertTrue(status["sol_attn"]["approximate"])
        self.assertEqual(
            status["sol_attn"]["required_revision"],
            h3_acceleration.KIJAI_SOL_REVISION,
        )
        self.assertTrue(status["w4a8"]["available"])
        self.assertFalse(status["w4a8"]["default"])
        self.assertEqual(status["w4a8"]["conditioning_mode"], "first_last_frames")
        self.assertEqual(status["w4a8"]["compatible_models"], ["minimax_h3_w4a8_fl2va"])

    def test_sage2_rejects_mask_and_layout_before_optional_kernel(self):
        query = torch.zeros((1, 128, 2, 128), dtype=torch.bfloat16)
        with patch.object(h3_acceleration, "_load_sage2_kernel") as loader:
            masked = h3_acceleration.maybe_sage2_attention(
                query, query, query,
                attention_mask=torch.ones((128, 128), dtype=torch.bool),
                tensor_layout="NHD",
                is_causal=False,
                allow_sdpa_fallback=True,
            )
            wrong_layout = h3_acceleration.maybe_sage2_attention(
                query, query, query,
                attention_mask=None,
                tensor_layout="HND",
                is_causal=False,
                allow_sdpa_fallback=True,
            )
        self.assertIsNone(masked)
        self.assertIsNone(wrong_layout)
        loader.assert_not_called()
        self.assertEqual(h3_acceleration._stats["sage2_fallback"], 2)
        self.assertIn("layout", h3_acceleration._sage2_last_fallback_reason)

    def test_explicit_sage2_never_silently_becomes_sdpa(self):
        query = torch.zeros((1, 128, 2, 128), dtype=torch.bfloat16)
        with self.assertRaisesRegex(RuntimeError, "Select Dense SDPA explicitly"):
            h3_acceleration.maybe_sage2_attention(
                query, query, query,
                attention_mask=torch.ones((128, 128), dtype=torch.bool),
                tensor_layout="NHD",
                is_causal=False,
            )
        self.assertEqual(h3_acceleration._stats["sage2_fallback"], 0)
        self.assertEqual(h3_acceleration._stats["errors"], 1)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA is required for the Sage2 success seam")
    def test_sage2_success_preserves_native_nhd_shape_dtype_and_device(self):
        if tuple(torch.cuda.get_device_capability(0)) != (12, 0):
            self.skipTest("the H3 Sage2 runtime is gated to SM120")
        query = torch.zeros((1, 64, 2, 128), dtype=torch.bfloat16, device="cuda")
        seen = {}

        def kernel(q, k, v, **kwargs):
            seen.update(kwargs)
            return q.clone()

        with patch.object(h3_acceleration, "_load_sage2_kernel", return_value=kernel):
            output = h3_acceleration.maybe_sage2_attention(
                query, query, query,
                attention_mask=None,
                tensor_layout="NHD",
                is_causal=False,
            )
        self.assertEqual(output.shape, query.shape)
        self.assertEqual(output.dtype, query.dtype)
        self.assertEqual(output.device, query.device)
        self.assertEqual(seen["tensor_layout"], "NHD")
        self.assertFalse(seen["is_causal"])
        self.assertEqual(h3_acceleration._stats["sage2_calls"], 1)

    def test_status_exposes_sage2_as_manual_unvalidated_sm120_path(self):
        with patch.object(
            h3_acceleration, "_sage2_capability",
            return_value=(True, "installed but unvalidated", h3_acceleration.SAGEATTENTION_REVISION),
        ), patch.object(
            h3_acceleration.torch.cuda, "is_available", return_value=True,
        ), patch.object(
            h3_acceleration.torch.cuda, "get_device_capability", return_value=(12, 0),
        ), patch.object(
            h3_acceleration.torch.version, "cuda", "12.8",
        ), patch.object(
            h3_acceleration,
            "_sage2_validation_record_status",
            return_value={"passed": False, "reason": "not reviewed"},
        ):
            status = h3_acceleration.get_h3_acceleration_status(probe_kernel=False)
        self.assertTrue(status["sage2"]["available"])
        self.assertFalse(status["sage2"]["default"])
        self.assertFalse(status["sage2"]["validated"])
        self.assertEqual(status["sage2"]["turbo_status"], "ready_for_live_4_8_validation")
        self.assertEqual(
            status["sage2"]["model_status"]["minimax_h3_ref2va"],
            "structurally_reachable_unvalidated",
        )

    def test_release_bound_sage2_validation_matches_exact_runtime_and_artifacts(self):
        with patch.object(
            h3_acceleration,
            "_sage2_distribution_provenance",
            return_value=(
                h3_acceleration.SAGEATTENTION_VERSION,
                h3_acceleration.SAGEATTENTION_CHECKOUT,
                "081f5bcb3695416a0ece908245d10b318e02f5db45ab362303894450679d41b8",
            ),
        ), patch.object(
            h3_acceleration.torch, "__version__", "2.7.0+cu128",
        ), patch.object(
            h3_acceleration.torch.version, "cuda", "12.8",
        ), patch.object(
            h3_acceleration.torch.cuda, "get_device_name", return_value="NVIDIA GeForce RTX 5090",
        ), patch.object(
            h3_acceleration.torch.cuda, "get_device_capability", return_value=(12, 0),
        ), patch.object(
            h3_acceleration.importlib.metadata, "version", return_value="3.3.1",
        ):
            status = h3_acceleration._sage2_validation_record_status()
        self.assertTrue(status["passed"])
        self.assertEqual(status["validated_profiles"], ["draft", "fast"])
        self.assertEqual(
            status["record_sha256"],
            h3_acceleration.SAGE2_VALIDATION_RECORD_SHA256,
        )

    def test_output_success_alone_cannot_create_sage2_validation(self):
        record = json.loads(h3_acceleration.SAGE2_VALIDATION_RECORD.read_text(encoding="utf-8"))
        record["review"] = {"output_success": True}
        status = _evaluate_mutated_validation_record(record)
        self.assertFalse(status["passed"])
        self.assertIn("visual/audio review", status["reason"])

    def test_sage2_validation_rejects_wrong_base_repository_or_checkpoint(self):
        for field, value in (
            ("repository", "untrusted/MiniMax-H3"),
            ("checkpoint", "different.safetensors"),
        ):
            with self.subTest(field=field):
                record = json.loads(
                    h3_acceleration.SAGE2_VALIDATION_RECORD.read_text(encoding="utf-8")
                )
                record["model"][field] = value
                status = _evaluate_mutated_validation_record(record)
                self.assertFalse(status["passed"])
                self.assertIn("approved Base checkpoint", status["reason"])

    def test_fast_validation_cannot_present_its_cold_sdpa_baseline_as_comparable(self):
        record = json.loads(
            h3_acceleration.SAGE2_VALIDATION_RECORD.read_text(encoding="utf-8")
        )
        record["cases"]["fast_864_turbo_8"]["timing_comparable"] = True
        status = _evaluate_mutated_validation_record(record)
        self.assertFalse(status["passed"])
        self.assertIn("cold baseline", status["reason"])

    def test_sage2_checkout_provenance_rejects_local_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
            subprocess.run(
                ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(checkout), "config", "user.name", "Maestro Test"],
                check=True,
            )
            source = checkout / "sageattention.py"
            source.write_text("VERSION = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(checkout), "add", "sageattention.py"], check=True)
            subprocess.run(
                ["git", "-C", str(checkout), "commit", "--quiet", "-m", "fixture"],
                check=True,
            )
            self.assertTrue(h3_acceleration._checkout_source_clean(checkout))
            source.write_text("VERSION = 2\n", encoding="utf-8")
            self.assertFalse(h3_acceleration._checkout_source_clean(checkout))
            source.write_text("VERSION = 1\n", encoding="utf-8")
            untracked = checkout / "sageattention_shadow.py"
            untracked.write_text("SHADOW = True\n", encoding="utf-8")
            self.assertFalse(h3_acceleration._checkout_source_clean(checkout))

    def test_sage2_distribution_digest_detects_installed_file_replacement(self):
        prefix = Path(sys.prefix).resolve()
        with tempfile.TemporaryDirectory(dir=prefix) as directory:
            root = Path(directory)
            package = root / "sageattention.py"
            package.write_text("VERSION = 1\n", encoding="utf-8")

            class FakeDistribution:
                version = h3_acceleration.SAGEATTENTION_VERSION
                files = [Path(root.name) / package.name]

                @staticmethod
                def read_text(name):
                    if name != "direct_url.json":
                        return None
                    return json.dumps({"url": h3_acceleration.SAGEATTENTION_CHECKOUT.resolve().as_uri()})

                @staticmethod
                def locate_file(relative):
                    return prefix / relative

            with patch.object(
                h3_acceleration.importlib.metadata,
                "distribution",
                return_value=FakeDistribution(),
            ):
                version, source, first_digest = h3_acceleration._sage2_distribution_provenance()
                package.write_text("VERSION = 2\n", encoding="utf-8")
                _version, _source, second_digest = h3_acceleration._sage2_distribution_provenance()
            self.assertEqual(version, h3_acceleration.SAGEATTENTION_VERSION)
            self.assertEqual(source, h3_acceleration.SAGEATTENTION_CHECKOUT.resolve())
            self.assertIsNotNone(first_digest)
            self.assertNotEqual(first_digest, second_digest)

    def test_sage2_kernel_loader_rejects_shadow_module(self):
        shadow = ModuleType("sageattention")
        shadow.__file__ = str(Path(sys.prefix) / "shadow" / "sageattention" / "__init__.py")
        shadow.sageattn = lambda *_args, **_kwargs: None
        verified_file = Path(sys.prefix).resolve() / "official" / "sageattention" / "__init__.py"
        with patch.object(
            h3_acceleration,
            "_sage2_capability",
            return_value=(True, "verified", h3_acceleration.SAGEATTENTION_REVISION),
        ), patch.object(
            h3_acceleration,
            "_sage2_distribution_files",
            return_value=frozenset({verified_file}),
        ), patch.dict(sys.modules, {"sageattention": shadow}):
            kernel = h3_acceleration._load_sage2_kernel()
        self.assertIsNone(kernel)
        self.assertIn("outside the verified installed distribution", h3_acceleration._sage2_error or "")

    def test_prepared_segment_w4a8_cannot_bypass_runtime_gate(self):
        guard = _load_launch_acceleration_guard()
        body = {"model_type": "minimax_h3_ref2va"}
        plan = {"segment_models": [
            {"model_type": "minimax_h3_w4a8_fl2va"},
            {"model_type": "minimax_h3_ref2va"},
        ]}
        with patch.object(
            h3_acceleration,
            "get_h3_acceleration_status",
            return_value={"w4a8": {"available": False, "reason": "test gate"}},
        ):
            with self.assertRaisesRegex(ValueError, "test gate"):
                guard(body, plan)

    def test_pinokio_builds_only_the_pinned_official_sage2_source(self):
        installer = (ROOT / "h3_acceleration_install.js").read_text(encoding="utf-8")
        helper = (APP / "scripts/install_h3_sageattention.py").read_text(encoding="utf-8")
        self.assertIn("https://github.com/thu-ml/SageAttention.git", installer)
        self.assertIn("--branch v2.2.0", installer)
        self.assertIn(h3_acceleration.SAGEATTENTION_REVISION, installer)
        self.assertIn(h3_acceleration.SAGEATTENTION_REVISION, helper)
        self.assertIn('"TORCH_CUDA_ARCH_LIST": "12.0"', helper)
        self.assertIn('CUDA_TOOLKIT_VERSION = "12.8.1"', helper)
        self.assertIn('nvidia/label/cuda-', helper)
        self.assertIn('"--no-build-isolation"', helper)
        self.assertNotIn("wheel", installer.lower())

    def test_cuda_toolkit_uses_pinned_nvidia_then_dependency_channel_only(self):
        helper = _load_sage_installer()
        with tempfile.TemporaryDirectory() as directory:
            toolkit = Path(directory) / "cuda-12.8.1"
            helper.CUDA_TOOLKIT = toolkit
            with patch.object(
                helper,
                "_nvcc_version",
                side_effect=[(0, 0), (12, 8)],
            ), patch.object(
                helper,
                "_conda_executable",
                return_value="/pinokio/conda",
            ), patch.object(helper.subprocess, "run") as run:
                result = helper._ensure_cuda_toolkit()
        self.assertEqual(result, toolkit)
        run.assert_called_once_with([
            "/pinokio/conda", "create", "--yes", "--prefix", str(toolkit),
            "--override-channels",
            "--channel", "nvidia/label/cuda-12.8.1",
            "--channel", "conda-forge",
            "cuda-toolkit=12.8.1",
        ], check=True)


if __name__ == "__main__":
    unittest.main()
