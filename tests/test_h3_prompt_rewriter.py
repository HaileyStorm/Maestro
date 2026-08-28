"""CPU-only tests for the fail-closed H3 prompt-rewriter documents."""
from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from services.h3_prompt_rewriter import (  # noqa: E402
    ADAPTER_FILENAME,
    ADAPTER_REVISION,
    ADAPTER_SHA256,
    ADAPTER_SIZE_BYTES,
    BASE_REVISION,
    BASE_SHARDS,
    BASE_TENSOR_TOTAL_SIZE,
    adapter_descriptor,
    apply_preview_decision,
    base_descriptor,
    canonical_public_projection,
    create_apply_decision,
    create_rewrite_preview,
    create_rewrite_request,
    inspect_local_candidate,
    validate_rewrite_preview,
    validate_rewrite_request,
)


def request_for(mode: str = "t2va") -> dict:
    images = {
        "t2va": [],
        "i2va": [{"role": "first_frame", "input_id": "image-first"}],
        "l2va": [
            {"role": "last_frame", "input_id": "image-last"},
        ],
        "fl2va": [
            {"role": "first_frame", "input_id": "image-first"},
            {"role": "last_frame", "input_id": "image-last"},
        ],
    }[mode]
    return create_rewrite_request(
        original_prompt="Adult dancer says EXACT LINE at 4.25s.",
        mode=mode,
        image_roles=images,
        literal_anchors=[
            {"anchor_id": "dialogue", "literal": "EXACT LINE"},
            {"anchor_id": "timing", "literal": "4.25s"},
        ],
        role_commitments=[
            {"role_id": "performance", "commitment": "The dancer owns body performance."},
            {"role_id": "camera", "commitment": "The authored camera remains authoritative."},
        ],
    )


def preview_for(request: dict) -> dict:
    source = request["original_prompt"]
    return create_rewrite_preview(
        request,
        deterministic=source,
        base=f"BASE: {source}",
        adapted=f"ADAPTED: {source}",
    )


class H3PromptRewriterTests(unittest.TestCase):
    def test_exact_immutable_source_identity_and_false_acceptance(self):
        adapter = adapter_descriptor()
        self.assertEqual(adapter["repo_id"], "lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B")
        self.assertEqual(adapter["revision"], ADAPTER_REVISION)
        self.assertEqual(adapter["artifact"], {
            "name": ADAPTER_FILENAME,
            "size_bytes": 2_793_483_400,
            "sha256": ADAPTER_SHA256,
        })
        self.assertEqual(adapter["structure"]["tensor_count"], 504)
        self.assertEqual(adapter["structure"]["complete_lora_pairs"], 252)
        self.assertEqual(adapter["structure"]["rank"], 256)
        self.assertEqual(adapter["structure"]["layer_count"], 36)
        self.assertEqual(len(adapter["structure"]["target_modules"]), 7)
        self.assertEqual(adapter["structure"]["peft_version"], "0.20.0")
        self.assertFalse(adapter["runtime_accepted"])
        self.assertFalse(adapter["gpu_accepted"])
        self.assertFalse(adapter["human_accepted"])
        adapter["revision"] = "tampered"
        self.assertEqual(adapter_descriptor()["revision"], ADAPTER_REVISION)

    def test_base_descriptor_has_exact_revision_and_four_shard_tuple(self):
        base = base_descriptor()
        self.assertEqual(base["repo_id"], "Qwen/Qwen3-VL-8B-Instruct")
        self.assertEqual(base["revision"], BASE_REVISION)
        self.assertEqual(
            [(x["name"], x["size_bytes"], x["lfs_sha256"]) for x in base["shards"]],
            list(BASE_SHARDS),
        )
        self.assertEqual(base["tensor_total_size"], 17_534_247_392)
        self.assertNotEqual(
            BASE_TENSOR_TOTAL_SIZE,
            sum(size for _name, size, _digest in BASE_SHARDS),
            "index tensor bytes deliberately exclude safetensors container overhead",
        )
        self.assertTrue(base["metadata_offline_load_observed"])
        self.assertTrue(base["processor_offline_load_observed"])
        self.assertTrue(base["adapter_shapes_match_base"])
        self.assertFalse(base["runtime_accepted"])

    def test_all_modes_enforce_exact_image_cardinality_and_first_last_order(self):
        for mode in ("t2va", "i2va", "l2va", "fl2va"):
            with self.subTest(mode=mode):
                self.assertEqual(validate_rewrite_request(request_for(mode))["mode"], mode)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            create_rewrite_request(original_prompt="x", mode="ref2va")
        with self.assertRaisesRegex(ValueError, "exactly 1"):
            create_rewrite_request(original_prompt="x", mode="i2va", image_roles=[])
        with self.assertRaisesRegex(ValueError, "must be last_frame"):
            create_rewrite_request(
                original_prompt="x", mode="l2va",
                image_roles=[
                    {"role": "first_frame", "input_id": "first"},
                ],
            )
        with self.assertRaisesRegex(ValueError, "distinct"):
            create_rewrite_request(
                original_prompt="x", mode="fl2va",
                image_roles=[
                    {"role": "first_frame", "input_id": "same"},
                    {"role": "last_frame", "input_id": "same"},
                ],
            )

    def test_request_schema_types_extras_and_path_fields_fail_closed(self):
        request = request_for()
        bad = copy.deepcopy(request)
        bad["extra"] = True
        with self.assertRaises(ValueError):
            validate_rewrite_request(bad)
        with self.assertRaises(ValueError):
            create_rewrite_request(original_prompt=7, mode="t2va")
        for schema in (True, 1.0):
            bad = copy.deepcopy(request)
            bad["schema_version"] = schema
            with self.assertRaises(ValueError):
                validate_rewrite_request(bad)
        bad = copy.deepcopy(request)
        bad["execution_policy"]["auto_apply"] = 0
        with self.assertRaises(ValueError):
            validate_rewrite_request(bad)
        with self.assertRaises(ValueError):
            create_rewrite_request(
                original_prompt="x", mode="i2va",
                image_roles=[{"role": "first_frame", "input_id": "x", "path": "/tmp/x"}],
            )
        with self.assertRaisesRegex(ValueError, "path fields"):
            canonical_public_projection({"schema_version": 1, "output_path": "/tmp/out"})
        with self.assertRaisesRegex(ValueError, "path fields"):
            canonical_public_projection({"unknown": ({"output_path": "/tmp/out"},)})
        with self.assertRaisesRegex(ValueError, "known"):
            canonical_public_projection({"schema_version": 1, "execution_available": True})
        bad_preview = preview_for(request)
        bad_preview["candidates"][0]["produced_by_runtime"] = 0
        with self.assertRaises(ValueError):
            canonical_public_projection(bad_preview)
        bad_descriptor = adapter_descriptor()
        bad_descriptor["runtime_accepted"] = 0
        with self.assertRaisesRegex(ValueError, "known"):
            canonical_public_projection(bad_descriptor)

    def test_request_and_preview_preserve_original_bytes_and_literal_anchors(self):
        source = "  Adult says EXACT LINE.\r\nAt exactly 4.25s.  "
        request = create_rewrite_request(
            original_prompt=source, mode="t2va",
            literal_anchors=[
                {"anchor_id": "line", "literal": "EXACT LINE"},
                {"anchor_id": "time", "literal": "4.25s"},
            ],
        )
        self.assertEqual(request["original_prompt"].encode(), source.encode())
        with self.assertRaisesRegex(ValueError, "preserve"):
            create_rewrite_preview(request, deterministic=source, base=source, adapted="changed")
        preview = create_rewrite_preview(request, deterministic=source, base=source, adapted=source)
        self.assertEqual(preview["original_prompt"].encode(), source.encode())
        self.assertIsNone(preview["selection"])
        self.assertFalse(preview["runtime_evidence"]["execution_available"])
        self.assertTrue(all(not item["produced_by_runtime"] for item in preview["candidates"]))

    def test_anchors_must_originate_and_preserve_exact_count_and_order(self):
        with self.assertRaisesRegex(ValueError, "original"):
            create_rewrite_request(
                original_prompt="Only FIRST appears.", mode="t2va",
                literal_anchors=[{"anchor_id": "missing", "literal": "SECOND"}],
            )
        request = create_rewrite_request(
            original_prompt="FIRST then SECOND then FIRST", mode="t2va",
            literal_anchors=[
                {"anchor_id": "first", "literal": "FIRST"},
                {"anchor_id": "second", "literal": "SECOND"},
            ],
        )
        for changed in (
            "FIRST then SECOND",  # missing one
            "FIRST then SECOND then FIRST then FIRST",  # introduced duplicate
            "SECOND then FIRST then FIRST",  # reordered
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "preserve"):
                create_rewrite_preview(request, deterministic=changed, base=request["original_prompt"], adapted=request["original_prompt"])

    def test_commitments_detect_request_preview_and_decision_tampering(self):
        request = request_for()
        preview = preview_for(request)
        validate_rewrite_preview(request, preview)
        bad_request = copy.deepcopy(request)
        bad_request["original_prompt"] += " tamper"
        with self.assertRaisesRegex(ValueError, "commitment"):
            validate_rewrite_request(bad_request)
        bad_preview = copy.deepcopy(preview)
        bad_preview["candidates"][0]["text"] += " tamper"
        with self.assertRaisesRegex(ValueError, "commitment"):
            validate_rewrite_preview(request, bad_preview)
        decision = create_apply_decision(request, preview, "adapted")
        bad_decision = copy.deepcopy(decision)
        bad_decision["selected_kind"] = "base"
        with self.assertRaisesRegex(ValueError, "invalid"):
            apply_preview_decision(request, preview, bad_decision)

    def test_apply_is_explicit_returns_new_text_and_never_mutates_source_documents(self):
        request = request_for()
        preview = preview_for(request)
        request_before = copy.deepcopy(request)
        preview_before = copy.deepcopy(preview)
        decision = create_apply_decision(request, preview, "adapted")
        selected = apply_preview_decision(request, preview, decision)
        self.assertEqual(selected, f"ADAPTED: {request['original_prompt']}")
        self.assertEqual(request, request_before)
        self.assertEqual(preview, preview_before)
        self.assertRegex(decision["decision_token"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            apply_preview_decision(request, preview, decision),
            apply_preview_decision(request, preview, decision),
            "decision integrity tokens are deliberately reusable, not single-use authorization",
        )

    def test_canonical_public_projection_is_stable_path_free_and_tamper_visible(self):
        request = request_for("fl2va")
        first = canonical_public_projection(request)
        second = canonical_public_projection(json.loads(first))
        self.assertEqual(first, second)
        self.assertNotIn("/mnt/", first)
        self.assertNotIn("\\\\", first)
        digest = hashlib.sha256(first.encode()).hexdigest()
        tampered = json.loads(first)
        tampered["original_prompt"] += "x"
        with self.assertRaises(ValueError):
            canonical_public_projection(tampered)
        self.assertNotEqual(digest, hashlib.sha256(json.dumps(tampered).encode()).hexdigest())

    def test_content_neutral_twins_follow_identical_document_path(self):
        prompts = (
            "Two adults embrace and say EXACT LINE at 4.25s.",
            "Two adults discuss a recipe and say EXACT LINE at 4.25s.",
        )
        documents = []
        for prompt in prompts:
            request = create_rewrite_request(
                original_prompt=prompt, mode="t2va",
                literal_anchors=[
                    {"anchor_id": "line", "literal": "EXACT LINE"},
                    {"anchor_id": "time", "literal": "4.25s"},
                ],
            )
            documents.append(preview_for(request))
        for preview in documents:
            self.assertEqual([x["kind"] for x in preview["candidates"]], ["deterministic", "base", "adapted"])
            self.assertFalse(preview["runtime_evidence"]["fallback_used"])

    def test_passive_inspection_stats_weights_but_never_opens_or_hashes_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "adapter"
            base = root / "base"
            adapter.mkdir()
            base.mkdir()
            (adapter / ADAPTER_FILENAME).write_bytes(b"")
            os.truncate(adapter / ADAPTER_FILENAME, ADAPTER_SIZE_BYTES)
            (adapter / "adapter_model.maestro-source.json").write_text(json.dumps({
                "repo_id": "lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B",
                "revision": ADAPTER_REVISION,
                "filename": ADAPTER_FILENAME,
                "size_bytes": ADAPTER_SIZE_BYTES,
                "sha256": ADAPTER_SHA256,
                "tensor_count": 504,
            }), encoding="utf-8")
            (adapter / "adapter_config.json").write_text(json.dumps({
                "base_model_name_or_path": "Qwen/Qwen3-VL-8B-Instruct",
                "peft_version": "0.20.0",
                "r": 256,
                "target_modules": ["gate_proj", "k_proj", "down_proj", "v_proj", "o_proj", "up_proj", "q_proj"],
            }), encoding="utf-8")
            (base / ".cache/huggingface/download").mkdir(parents=True)
            weight_paths = set()
            for name, size, digest in BASE_SHARDS:
                path = base / name
                path.write_bytes(b"")
                os.truncate(path, size)
                weight_paths.add(str(path))
                (base / ".cache/huggingface/download" / f"{name}.metadata").write_text(
                    f"{BASE_REVISION} {digest} 0\n", encoding="utf-8",
                )
            weight_paths.add(str(adapter / ADAPTER_FILENAME))
            (base / "config.json").write_text(json.dumps({
                "model_type": "qwen3_vl",
                "architectures": ["Qwen3VLForConditionalGeneration"],
            }), encoding="utf-8")
            (base / "preprocessor_config.json").write_text(json.dumps({
                "image_processor_type": "Qwen2VLImageProcessorFast",
                "processor_class": "Qwen3VLProcessor",
            }), encoding="utf-8")
            (base / "model.safetensors.index.json").write_text(json.dumps({
                "metadata": {"total_size": BASE_TENSOR_TOTAL_SIZE},
                "weight_map": {f"tensor_{index}": name for index, (name, _size, _digest) in enumerate(BASE_SHARDS)},
            }), encoding="utf-8")

            real_open = builtins.open
            opened = []
            def guarded_open(file, *args, **kwargs):
                opened.append(os.fspath(file))
                if os.fspath(file) in weight_paths:
                    raise AssertionError("large weight bytes were opened")
                return real_open(file, *args, **kwargs)

            with mock.patch("builtins.open", side_effect=guarded_open), mock.patch(
                "hashlib.sha256", wraps=hashlib.sha256,
            ) as sha:
                status = inspect_local_candidate(adapter, base)
            self.assertTrue(status["adapter_metadata_compatible"])
            self.assertTrue(status["base_metadata_compatible"])
            self.assertTrue(status["base_shards_compatible"])
            self.assertFalse(status["execution_available"])
            self.assertFalse(any(path in opened for path in weight_paths))
            self.assertEqual(sha.call_count, 0)
            self.assertNotIn(str(root), json.dumps(status))

    def test_passive_inspection_rejects_symlinks_lstat_errors_and_bad_base_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "adapter"
            base = root / "base"
            adapter.mkdir()
            base.mkdir()
            target = root / "target"
            target.write_bytes(b"")
            os.truncate(target, ADAPTER_SIZE_BYTES)
            (adapter / ADAPTER_FILENAME).symlink_to(target)
            status = inspect_local_candidate(adapter, base)
            self.assertFalse(status["adapter_metadata_compatible"])
            self.assertFalse(status["base_metadata_compatible"])
            self.assertFalse(status["base_shards_compatible"])
            self.assertFalse(status["execution_available"])

            with mock.patch("pathlib.Path.lstat", side_effect=OSError("opaque local failure")):
                status = inspect_local_candidate(adapter, base)
            self.assertFalse(status["adapter_metadata_compatible"])
            self.assertNotIn("opaque", json.dumps(status))

            (base / "config.json").write_text(json.dumps({
                "model_type": "wrong", "architectures": ["Qwen3VLForConditionalGeneration"],
            }), encoding="utf-8")
            (base / "preprocessor_config.json").write_text(json.dumps({
                "image_processor_type": "Qwen2VLImageProcessorFast", "processor_class": "Qwen3VLProcessor",
            }), encoding="utf-8")
            (base / "model.safetensors.index.json").write_text(json.dumps({
                "metadata": {"total_size": True}, "weight_map": {"tensor": BASE_SHARDS[0][0]},
            }), encoding="utf-8")
            self.assertFalse(inspect_local_candidate(adapter, base)["base_metadata_compatible"])

            regular = adapter / ADAPTER_FILENAME
            regular.unlink()
            regular.write_bytes(b"")
            os.truncate(regular, ADAPTER_SIZE_BYTES)
            (adapter / "adapter_model.maestro-source.json").write_text(json.dumps({
                "repo_id": "lightx2v/MiniMax-H3-Prompt-Rewriter-LoRA-8B",
                "revision": ADAPTER_REVISION,
                "filename": ADAPTER_FILENAME,
                "size_bytes": True,
                "sha256": ADAPTER_SHA256,
                "tensor_count": 504.0,
            }), encoding="utf-8")
            (adapter / "adapter_config.json").write_text(json.dumps({
                "base_model_name_or_path": "Qwen/Qwen3-VL-8B-Instruct",
                "peft_version": "0.20.0", "r": 256,
                "target_modules": ["gate_proj", "k_proj", "down_proj", "v_proj", "o_proj", "up_proj", "q_proj"],
            }), encoding="utf-8")
            self.assertFalse(inspect_local_candidate(adapter, base)["adapter_metadata_compatible"])

    def test_module_is_model_free_and_has_no_runtime_wiring(self):
        module = Path(ROOT, "app/services/h3_prompt_rewriter.py").read_text(encoding="utf-8")
        lowered = module.lower()
        self.assertNotIn("import torch", lowered)
        self.assertNotIn("import transformers", lowered)
        self.assertNotIn("import peft", lowered)
        self.assertNotIn("wgp", lowered)
        self.assertNotIn("llm_service", lowered)
        self.assertNotIn("profiles", lowered)
        self.assertNotIn("subprocess", lowered)


if __name__ == "__main__":
    unittest.main()
