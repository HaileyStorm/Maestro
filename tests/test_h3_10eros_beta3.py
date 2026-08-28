"""CPU-only contracts for the 10Eros MiniMax H3 Beta3 scaffold."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import h3_10eros_beta3 as beta3
from services import h3_checkpoint_receipts as receipts
from services import h3_evaluation

_RUNNER_PATH = ROOT / "app" / "scripts" / "benchmark_h3_profiles.py"
_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "h3_10eros_beta3_benchmark_parity", _RUNNER_PATH,
)
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_RUNNER_SPEC)
sys.modules[_RUNNER_SPEC.name] = benchmark
_RUNNER_SPEC.loader.exec_module(benchmark)

_HANDLER_PATH = APP / "models" / "minimax_h3" / "minimax_h3_handler.py"
_DEFINITION_PATHS = {
    beta3.TEN_EROS_BETA3_SKIP_ID: (
        APP / "defaults" / "minimax_h3_10eros_beta3_skip_edges.json"
    ),
    beta3.TEN_EROS_BETA3_FULL_ID: (
        APP / "defaults" / "minimax_h3_10eros_beta3_full.json"
    ),
}


def _load_handler_module():
    spec = importlib.util.spec_from_file_location(
        "models.minimax_h3._beta3_registration_handler", _HANDLER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    torch_stub = types.ModuleType("torch")
    torch_stub.bfloat16 = "bfloat16"
    with mock.patch.dict(sys.modules, {"torch": torch_stub}):
        spec.loader.exec_module(module)
    return module


_HANDLER = _load_handler_module()

_MARKER = json.dumps({
    "format": "int8_tensorwise",
    "convrot": True,
    "convrot_groupsize": 256,
}).encode("utf-8")


def _write_fixture(
    path: Path,
    *,
    artifact_id: str = beta3.TEN_EROS_BETA3_SKIP_ID,
    bad_marker: bool = False,
    omit_marker: bool = False,
    invalid_json: bool = False,
    undersized_scale: bool = False,
) -> None:
    contract = beta3._copy(beta3._artifact(artifact_id))
    blocks = contract["layer_policy"]["quantized_blocks"]
    edges = contract["layer_policy"]["bf16_edge_blocks"]
    header: dict[str, object] = {"__metadata__": {"format": "pt"}}
    payload = bytearray()

    def add(name: str, dtype: str, shape: list[int], data: bytes) -> None:
        start = len(payload)
        payload.extend(data)
        header[name] = {
            "dtype": dtype,
            "shape": shape,
            "data_offsets": [start, len(payload)],
        }

    for block in blocks:
        for index in range(4):
            base = f"blocks.{block}.unit{index}"
            add(f"{base}.weight", "I8", [2, 2], b"\x00" * 4)
            scale_bytes = (
                b"\x00" * 4
                if undersized_scale and block == blocks[0] and index == 0
                else b"\x00" * 8
            )
            add(f"{base}.weight_scale", "F32", [2, 1], scale_bytes)
            if not (omit_marker and block == blocks[-1] and index == 3):
                marker = b'{}' if bad_marker and block == blocks[0] and index == 0 else _MARKER
                add(f"{base}.comfy_quant", "U8", [len(marker)], marker)
    for block in edges:
        add(f"blocks.{block}.edge.weight", "BF16", [2, 2], b"\x00" * 8)
        add(f"blocks.{block}.edge.bias", "BF16", [2], b"\x00" * 4)

    raw_header = b"{" if invalid_json else json.dumps(
        header, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw_header)) + raw_header + payload)


def _patched_contract(path: Path, artifact_id: str):
    catalog = {
        key: beta3._copy(value) for key, value in beta3._ARTIFACT_CATALOG.items()
    }
    chosen = catalog[artifact_id]
    chosen["size"] = path.stat().st_size
    chosen["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    frozen = MappingProxyType({
        key: beta3._freeze(value) for key, value in catalog.items()
    })
    return mock.patch.object(beta3, "_ARTIFACT_CATALOG", frozen)


class H310ErosBeta3Tests(unittest.TestCase):
    def test_catalog_is_immutable_exact_and_skip_first(self):
        catalog = beta3.get_10eros_beta3_catalog()
        self.assertEqual(catalog["repository"], beta3.TEN_EROS_REPOSITORY)
        self.assertEqual(
            catalog["repository_head"],
            "dbdd87944063bc01d8062bae1dba12212ca4061f",
        )
        skip, full = catalog["artifacts"]
        self.assertEqual(skip["artifact_id"], beta3.TEN_EROS_BETA3_SKIP_ID)
        self.assertEqual(full["artifact_id"], beta3.TEN_EROS_BETA3_FULL_ID)
        self.assertEqual(
            skip["filename"],
            "10Eros_Max_h3_TURBO-hybrid_beta3_int8_convrot_skip_edges.safetensors",
        )
        self.assertEqual(skip["revision"], "09beb98782a6feb2f44c39c46179743ca8607c6c")
        self.assertEqual(skip["size"], 22_513_576_472)
        self.assertEqual(
            skip["sha256"],
            "a5ae4559cf19b0830adc1de6e8355d10eaf10524f78e9851a189a80990e6963a",
        )
        self.assertEqual(skip["layer_policy"]["marker_count"], 184)
        self.assertEqual(skip["layer_policy"]["quantized_blocks"], list(range(2, 48)))
        self.assertEqual(skip["layer_policy"]["bf16_edge_blocks"], [0, 1, 48, 49])
        self.assertEqual(full["revision"], "84ea7a6ec06e0cb5f2f35615e25e3529c5ec6c02")
        self.assertEqual(full["size"], 20_973_147_816)
        self.assertEqual(
            full["sha256"],
            "ebd0cb25273253213028bea0289da4c5c94929027ed9191fbb24fc924d4a8f0d",
        )
        self.assertEqual(full["layer_policy"]["marker_count"], 200)
        self.assertEqual(full["layer_policy"]["quantized_blocks"], list(range(50)))
        for artifact in (skip, full):
            self.assertEqual(artifact["mode"], "turbo_hybrid")
            self.assertNotIn("fl2va", artifact["mode"])
            self.assertNotIn("ref2va", artifact["mode"])
            self.assertEqual(artifact["quantization"], {
                "format": "int8_tensorwise",
                "scale_method": "per_channel_absmax",
                "convrot": True,
                "convrot_groupsize": 256,
                "source_dtype": "bfloat16",
            })
            policy = artifact["maestro_experiment_policy"]
            self.assertEqual(
                policy["evidence_class"],
                "provisional_maestro_experiment_policy",
            )
            self.assertEqual(policy["schedule"]["steps"], 6)
            self.assertFalse(artifact["execution_available"])
            self.assertFalse(artifact["enabled_by_default"])
            self.assertFalse(artifact["automatic_fallback"])
        catalog["artifacts"][0]["size"] = 1
        self.assertEqual(
            beta3.get_10eros_beta3_catalog()["artifacts"][0]["size"],
            22_513_576_472,
        )
        with self.assertRaises(TypeError):
            beta3._ARTIFACT_CATALOG[beta3.TEN_EROS_BETA3_SKIP_ID]["size"] = 1

    def test_stage2a_definitions_match_catalog_and_remain_opt_in(self):
        artifacts = {
            item["artifact_id"]: item
            for item in beta3.get_10eros_beta3_catalog()["artifacts"]
        }
        for artifact_id, path in _DEFINITION_PATHS.items():
            with self.subTest(artifact_id=artifact_id):
                defaults = json.loads(path.read_text(encoding="utf-8"))
                model = defaults["model"]
                artifact = artifacts[artifact_id]
                self.assertEqual(model["architecture"], "minimax_h3_10eros_beta3")
                self.assertEqual(model["URLs"], [
                    "https://huggingface.co/"
                    f"{artifact['repository']}/resolve/{artifact['revision']}/"
                    f"{artifact['filename']}"
                ])
                self.assertEqual(
                    model["h3_10eros_beta3_profile_id"], artifact["profile_id"]
                )
                self.assertEqual(
                    model["h3_10eros_beta3_repository_head"],
                    artifact["repository_head"],
                )
                self.assertEqual(
                    model["h3_10eros_beta3_revision"], artifact["revision"]
                )
                self.assertEqual(
                    model["h3_10eros_beta3_filename"], artifact["filename"]
                )
                self.assertEqual(model["h3_10eros_beta3_size"], artifact["size"])
                self.assertEqual(
                    model["h3_10eros_beta3_sha256"], artifact["sha256"]
                )
                self.assertEqual(model["h3_10eros_beta3_mode"], "turbo_hybrid")
                self.assertTrue(model["experimental"])
                self.assertTrue(model["opt_in_only"])
                self.assertTrue(model["scaffold_only"])
                self.assertTrue(model["h3_convrot"])
                self.assertEqual(model["minimax_h3_qkv_layout"], "contiguous")
                self.assertEqual(model["compatible_model_paths"], {})
                self.assertEqual(model["compatible_model_qkv_layouts"], {})
                self.assertFalse(model["execution_available"])
                self.assertFalse(model["enabled_by_default"])
                self.assertFalse(model["automatic_fallback"])
                self.assertEqual(defaults["num_inference_steps"], 6)
                self.assertEqual(
                    defaults["h3_10eros_beta3_sampler"], "er_sde/simple"
                )
                self.assertEqual(
                    defaults["custom_settings"], {"h3_attention_engine": "sdpa"}
                )
                self.assertEqual(defaults["tea_cache"], 0)
                self.assertEqual(defaults["skip_steps_cache_type"], "")
                self.assertEqual(defaults["activated_loras"], [])
                self.assertEqual(defaults["loras_multipliers"], "")

    def test_stage2a_handler_neutralizes_fl2va_and_rejects_execution(self):
        handler = _HANDLER.family_handler
        self.assertEqual(
            handler.query_supported_types(),
            ["minimax_h3", "minimax_h3_ref2va", "minimax_h3_10eros_beta3"],
        )
        self.assertNotIn(
            "minimax_h3_10eros_beta3", handler.query_family_maps()[0]
        )
        artifacts = {
            item["artifact_id"]: item
            for item in beta3.get_10eros_beta3_catalog()["artifacts"]
        }
        for artifact_id, path in _DEFINITION_PATHS.items():
            with self.subTest(artifact_id=artifact_id):
                artifact = artifacts[artifact_id]
                raw = json.loads(path.read_text(encoding="utf-8"))["model"]
                inherited = handler.query_model_def(
                    "minimax_h3_10eros_beta3", raw
                )
                merged = {**inherited, **raw}
                self.assertTrue(merged["t2v_class"])
                self.assertFalse(merged["i2v_class"])
                self.assertEqual(merged["image_prompt_types_allowed"], "")
                self.assertFalse(merged["end_frames_always_enabled"])
                self.assertEqual(merged["minimax_h3_conditioning_mode"], "unwired")
                self.assertEqual(merged["required_runtime_assets"], {})
                self.assertEqual(merged["text_encoder_URLs"], [])
                self.assertEqual(merged["compatible_model_paths"], {})
                self.assertEqual(merged["compatible_model_qkv_layouts"], {})
                self.assertEqual(merged["compatible_text_encoder_paths"], {})
                self.assertEqual(
                    merged["h3_10eros_beta3_contract"], artifacts[artifact_id]
                )
                self.assertEqual(
                    handler.validate_generative_settings(
                        path.stem, merged, {}
                    ),
                    _HANDLER._BETA3_UNWIRED_MESSAGE,
                )
                self.assertEqual(
                    handler.query_model_files(
                        [], "minimax_h3_10eros_beta3", merged
                    ),
                    [],
                )
                with self.assertRaisesRegex(
                    _HANDLER.H310ErosBeta3UnwiredError,
                    "runtime execution remains unavailable",
                ):
                    handler.load_model(
                        artifact["filename"],
                        model_type=path.stem,
                        base_model_type="minimax_h3_10eros_beta3",
                        model_def=merged,
                    )
                defaults = {}
                handler.update_default_settings(
                    "minimax_h3_10eros_beta3", merged, defaults
                )
                self.assertEqual(defaults["num_inference_steps"], 6)
                self.assertEqual(
                    defaults["custom_settings"], {"h3_attention_engine": "sdpa"}
                )
                self.assertEqual(defaults["image_prompt_type"], "")
                self.assertEqual(defaults["video_prompt_type"], "")
                self.assertEqual(defaults["audio_prompt_type"], "")

    def test_stage2a_handler_rejects_identity_and_policy_drift(self):
        handler = _HANDLER.family_handler
        raw = json.loads(
            _DEFINITION_PATHS[beta3.TEN_EROS_BETA3_SKIP_ID].read_text(
                encoding="utf-8"
            )
        )["model"]
        corruptions = {
            "artifact": {"h3_10eros_beta3_artifact_id": "unknown"},
            "architecture": {"architecture": "minimax_h3"},
            "profile": {"h3_10eros_beta3_profile_id": "wrong"},
            "revision": {"h3_10eros_beta3_revision": "0" * 40},
            "size": {"h3_10eros_beta3_size": 1},
            "sha": {"h3_10eros_beta3_sha256": "0" * 64},
            "url": {"URLs": ["https://example.invalid/model.safetensors"]},
            "convrot": {"h3_convrot": False},
            "qkv": {"minimax_h3_qkv_layout": "interleaved"},
            "alias": {"compatible_model_qkv_layouts": {"wrong": "interleaved"}},
            "execution": {"execution_available": True},
            "default": {"enabled_by_default": True},
            "fallback": {"automatic_fallback": True},
        }
        for label, patch in corruptions.items():
            with self.subTest(label=label), self.assertRaises(
                _HANDLER.H310ErosBeta3UnwiredError
            ):
                handler.query_model_def(
                    "minimax_h3_10eros_beta3", {**raw, **patch}
                )

        legacy_architecture = {**raw, "architecture": "minimax_h3"}
        self.assertTrue(
            handler._is_beta3_definition(
                "minimax_h3_10eros_beta3_skip_edges", legacy_architecture
            )
        )
        with self.assertRaises(_HANDLER.H310ErosBeta3UnwiredError):
            handler.load_model(
                raw["h3_10eros_beta3_filename"],
                model_type="minimax_h3_10eros_beta3_skip_edges",
                base_model_type="minimax_h3",
                model_def=legacy_architecture,
            )

        stripped = {
            key: value
            for key, value in raw.items()
            if key not in _HANDLER._BETA3_DEFINITION_MARKERS
        }
        stripped["architecture"] = "minimax_h3"
        self.assertTrue(
            handler._is_beta3_definition(
                "minimax_h3_10eros_beta3_skip_edges", stripped
            )
        )
        with self.assertRaises(_HANDLER.H310ErosBeta3UnwiredError):
            handler.load_model(
                raw["h3_10eros_beta3_filename"],
                model_type="minimax_h3_10eros_beta3_skip_edges",
                base_model_type="minimax_h3",
                model_def=stripped,
            )

    def test_service_evaluation_and_benchmark_descriptors_remain_in_parity(self):
        evaluation = h3_evaluation.get_h3_profile_catalog()["profiles"]
        cases = {case.model_type: case for case in benchmark.DEFAULT_CASES}
        for artifact in beta3.get_10eros_beta3_catalog()["artifacts"]:
            profile = evaluation[artifact["profile_id"]]
            case = cases[artifact["profile_id"]]
            projection = case.public_config()
            with self.subTest(artifact_id=artifact["artifact_id"]):
                self.assertEqual(profile["repository"], artifact["repository"])
                self.assertEqual(profile["repository_head"], artifact["repository_head"])
                self.assertEqual(profile["revision"], artifact["revision"])
                self.assertEqual(profile["artifact"], artifact["filename"])
                self.assertEqual(profile["artifact_size_bytes"], artifact["size"])
                self.assertEqual(profile["artifact_sha256"], artifact["sha256"])
                self.assertEqual(profile["mode"], artifact["mode"])
                self.assertEqual(profile["quantization"], artifact["quantization"])
                self.assertEqual(profile["layer_policy"], artifact["layer_policy"])
                self.assertEqual(
                    profile["maestro_experiment_policy"],
                    artifact["maestro_experiment_policy"],
                )
                self.assertEqual(projection["artifact_id"], artifact["artifact_id"])
                self.assertEqual(projection["checkpoint_filename"], artifact["filename"])
                self.assertEqual(projection["artifact_revision"], artifact["revision"])
                self.assertEqual(projection["artifact_size_bytes"], artifact["size"])
                self.assertEqual(projection["artifact_sha256"], artifact["sha256"])
                self.assertEqual(projection["artifact_mode"], artifact["mode"])
                self.assertEqual(projection["quantization"], artifact["quantization"])
                self.assertEqual(
                    projection["quantized_layer_count"],
                    artifact["layer_policy"]["marker_count"],
                )
                self.assertEqual(
                    projection["quantized_blocks"],
                    artifact["layer_policy"]["quantized_blocks"],
                )
                self.assertEqual(
                    projection["bf16_edge_blocks"],
                    artifact["layer_policy"]["bf16_edge_blocks"],
                )
                self.assertEqual(
                    projection["maestro_experiment_policy"],
                    artifact["maestro_experiment_policy"],
                )

    def test_header_validator_accepts_exact_skip_and_full_marker_policies(self):
        for artifact_id in (
            beta3.TEN_EROS_BETA3_SKIP_ID,
            beta3.TEN_EROS_BETA3_FULL_ID,
        ):
            with self.subTest(artifact_id=artifact_id), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                filename = beta3._artifact(artifact_id)["filename"]
                path = root / filename
                _write_fixture(path, artifact_id=artifact_id)
                with _patched_contract(path, artifact_id):
                    result = beta3.validate_10eros_beta3_header(path, artifact_id)
            expected = 184 if artifact_id == beta3.TEN_EROS_BETA3_SKIP_ID else 200
            self.assertEqual(result["marker_count"], expected)
            self.assertTrue(result["marker_policy_validated"])
            self.assertTrue(result["per_channel_scale_contract_validated"])

    def test_wrong_filename_size_sha_header_and_marker_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected_name = beta3._artifact(beta3.TEN_EROS_BETA3_SKIP_ID)["filename"]
            path = root / expected_name
            _write_fixture(path)
            wrong_name = root / "wrong.safetensors"
            wrong_name.write_bytes(path.read_bytes())
            with self.assertRaisesRegex(beta3.H310ErosBeta3Error, "filename"):
                beta3.validate_10eros_beta3_header(
                    wrong_name, beta3.TEN_EROS_BETA3_SKIP_ID,
                )
            with self.assertRaisesRegex(beta3.H310ErosBeta3Error, "size"):
                beta3.validate_10eros_beta3_header(
                    path, beta3.TEN_EROS_BETA3_SKIP_ID,
                )

            with _patched_contract(path, beta3.TEN_EROS_BETA3_SKIP_ID):
                patched = beta3._copy(beta3._artifact(beta3.TEN_EROS_BETA3_SKIP_ID))
                patched["sha256"] = "0" * 64
                catalog = MappingProxyType({
                    **beta3._ARTIFACT_CATALOG,
                    beta3.TEN_EROS_BETA3_SKIP_ID: beta3._freeze(patched),
                })
                with mock.patch.object(
                    beta3, "_ARTIFACT_CATALOG", catalog,
                ), self.assertRaisesRegex(beta3.H310ErosBeta3Error, "integrity"):
                    beta3.verify_10eros_beta3_artifact(
                        path, beta3.TEN_EROS_BETA3_SKIP_ID,
                        receipt_root=root / "wrong-sha-receipts",
                    )

            for kind in ("header", "marker", "count", "scale_length"):
                with self.subTest(kind=kind):
                    if kind == "header":
                        _write_fixture(path, invalid_json=True)
                    elif kind == "marker":
                        _write_fixture(path, bad_marker=True)
                    elif kind == "count":
                        _write_fixture(path, omit_marker=True)
                    else:
                        _write_fixture(path, undersized_scale=True)
                    with _patched_contract(
                        path, beta3.TEN_EROS_BETA3_SKIP_ID,
                    ), self.assertRaises(beta3.H310ErosBeta3Error):
                        beta3.validate_10eros_beta3_header(
                            path, beta3.TEN_EROS_BETA3_SKIP_ID,
                        )

    def test_passive_status_never_hashes_or_creates_receipt_and_is_path_free(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / beta3._artifact(beta3.TEN_EROS_BETA3_SKIP_ID)["filename"]
            _write_fixture(path)
            receipt_root = root / "receipts"
            with _patched_contract(path, beta3.TEN_EROS_BETA3_SKIP_ID), mock.patch.object(
                receipts, "_stream_sha256",
                side_effect=AssertionError("passive status hashed content"),
            ):
                status = beta3.beta3_candidate_status(
                    beta3.TEN_EROS_BETA3_SKIP_ID,
                    root=root,
                    receipt_root=receipt_root,
                )
            self.assertTrue(status["candidate"])
            self.assertTrue(status["present"])
            self.assertFalse(status["downloaded"])
            self.assertFalse(status["installed"])
            self.assertFalse(status["verified"])
            self.assertFalse(status["execution_available"])
            self.assertFalse(receipt_root.exists())
            serialized = json.dumps(status)
            self.assertNotIn(str(root), serialized)
            for field in ("path", "dev", "ino", "uid", "mtime_ns", "ctime_ns"):
                self.assertNotIn(field, status)

    def test_explicit_verification_hashes_once_then_reuses_exact_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / beta3._artifact(beta3.TEN_EROS_BETA3_SKIP_ID)["filename"]
            _write_fixture(path)
            receipt_root = root / "receipts"
            with _patched_contract(path, beta3.TEN_EROS_BETA3_SKIP_ID), mock.patch.object(
                receipts, "_stream_sha256", wraps=receipts._stream_sha256,
            ) as stream:
                first = beta3.verify_10eros_beta3_artifact(
                    path, beta3.TEN_EROS_BETA3_SKIP_ID,
                    receipt_root=receipt_root,
                )
                second = beta3.verify_10eros_beta3_artifact(
                    path, beta3.TEN_EROS_BETA3_SKIP_ID,
                    receipt_root=receipt_root,
                )
                status = beta3.beta3_candidate_status(
                    beta3.TEN_EROS_BETA3_SKIP_ID,
                    root=root,
                    receipt_root=receipt_root,
                )
            self.assertEqual(stream.call_count, 1)
            self.assertFalse(first["receipt"]["receipt_reused"])
            self.assertTrue(second["receipt"]["receipt_reused"])
            self.assertTrue(status["verified"])
            self.assertTrue(status["receipt_reused"])
            self.assertTrue(status["downloaded"])
            self.assertTrue(status["installed"])
            serialized = json.dumps({"first": first, "status": status})
            self.assertNotIn(str(root), serialized)


if __name__ == "__main__":
    unittest.main()
