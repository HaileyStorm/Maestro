"""Continuum local model-filename helpers.

Locks leftover 1.9.0 `get_compatible_local_model_filename` /
`_variant_group_filenames(..., model_type=)` probes onto Continuum
`get_local_model_filename` and flatten-only `_variant_group_filenames`.
Do not invent leftover compatible-path remappers, and do not restore
that helper.
"""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_WGP_PATH = _ROOT / "app" / "wgp.py"
_LAUNCH_PATH = _ROOT / "app" / "launch.py"
_LOCATOR_PATH = _ROOT / "app" / "shared" / "utils" / "files_locator.py"

_LEFTOVER_HELPERS = (
    "get_compatible_local_model_filename",
)
_LEFTOVER_RECONNECTS = (
    "compatible_model_paths",
    "compatible_text_encoder_paths",
    "model_type=model_type",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_wgp_locator(locator):
    tree = ast.parse(_read(_WGP_PATH), filename=str(_WGP_PATH))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_local_model_filename"
    ]
    namespace = {
        "os": os,
        "fl": locator,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_WGP_PATH), "exec"), namespace)
    return namespace["get_local_model_filename"]


def _load_launch_variant_helpers(locator):
    tree = ast.parse(_read(_LAUNCH_PATH), filename=str(_LAUNCH_PATH))
    wanted = {"_variant_group_filenames", "_variant_group_downloaded"}
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "os": os,
        "wgp": SimpleNamespace(
            get_local_model_filename=_load_wgp_locator(locator),
        ),
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_LAUNCH_PATH), "exec"), namespace)
    return (
        namespace["_variant_group_filenames"],
        namespace["_variant_group_downloaded"],
    )


class TestMiniMaxH3AssetSharing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.maestro_app = base / "Maestro" / "app"
        self.primary = self.maestro_app / "ckpts"
        self.linked = base / "wan.git" / "app" / "ckpts"
        self.primary.mkdir(parents=True)
        self.linked.mkdir(parents=True)
        self.locator = _load_module(
            _LOCATOR_PATH,
            f"maestro_files_locator_sharing_{id(self)}",
        )
        self.locator._APP_DIR = str(self.maestro_app)
        self.locator.set_checkpoints_paths([str(self.primary), str(self.linked)])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wgp_does_not_restore_leftover_compatible_resolver(self):
        source = _read(_WGP_PATH)
        launch = _read(_LAUNCH_PATH)

        # Leftover 1.9.0 remapped canonical filenames through
        # `compatible_model_paths` / `compatible_text_encoder_paths` in
        # `get_compatible_local_model_filename`. Continuum dropped that
        # helper and kept locate-only `get_local_model_filename`.
        for name in _LEFTOVER_HELPERS:
            with self.subTest(leftover=name):
                self.assertNotIn(f"def {name}(", source)
                self.assertNotIn(name, launch)

    def test_continuum_helpers_keep_local_locator_not_leftover_aliases(self):
        source = _read(_WGP_PATH)
        launch = _read(_LAUNCH_PATH)
        start = source.index("def get_local_model_filename(")
        end = source.index("\n_MANUAL_CHECKPOINT_INTEGRITY_CONTRACTS", start)
        hook = source[start:end]
        self.assertIn("def get_local_model_filename(", source)
        self.assertIn("def _variant_group_filenames(", launch)
        self.assertIn("def _variant_group_downloaded(", launch)
        self.assertNotIn("def get_compatible_local_model_filename(", source)
        for leftover in _LEFTOVER_HELPERS + _LEFTOVER_RECONNECTS:
            with self.subTest(leftover=leftover):
                self.assertNotIn(leftover, hook)

    def test_exact_canonical_checkpoint_is_located_without_leftover_alias(self):
        canonical = "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
        alternate = "MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors"
        canonical_path = self.primary / canonical
        alternate_path = self.linked / alternate
        canonical_path.write_bytes(b"maestro")
        alternate_path.write_bytes(b"wangp")
        resolve = _load_wgp_locator(self.locator)

        result = resolve(f"https://huggingface.invalid/{canonical}")

        self.assertEqual(Path(result), canonical_path)
        self.assertNotIn("def get_compatible_local_model_filename(", _read(_WGP_PATH))

    def test_alias_only_checkpoint_fail_closed_without_leftover_remapper(self):
        # Leftover `get_compatible_local_model_filename` accepted a linked
        # alias when the canonical name was missing. Continuum locates the
        # requested basename only.
        canonical = "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
        alternate = "MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors"
        alternate_path = self.linked / alternate
        alternate_path.write_bytes(b"wangp")
        resolve = _load_wgp_locator(self.locator)

        result = resolve(f"https://huggingface.invalid/{canonical}")

        self.assertIsNone(result)
        self.assertFalse(os.path.isfile(self.primary / canonical))

    def test_legacy_fp8_does_not_satisfy_int8_name_without_leftover_alias(self):
        canonical = "MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors"
        legacy = "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
        legacy_path = self.primary / legacy
        legacy_path.write_bytes(b"legacy maestro fp8")
        resolve = _load_wgp_locator(self.locator)

        result = resolve(f"https://huggingface.invalid/{canonical}")

        self.assertIsNone(result)
        self.assertEqual(legacy_path.read_bytes(), b"legacy maestro fp8")

    def test_model_readiness_fail_closed_without_leftover_alias_walk(self):
        # Leftover `_variant_group_filenames(..., model_type=)` walked
        # `compatible_model_paths`. Continuum flattens declared URLs only.
        canonical = "minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
        alternate = "MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors"
        alternate_path = self.linked / alternate
        alternate_path.write_bytes(b"wangp")
        filenames, downloaded = _load_launch_variant_helpers(self.locator)
        urls = [f"https://huggingface.invalid/{canonical}"]

        self.assertEqual(filenames(urls), [canonical])
        self.assertFalse(downloaded(urls))
        self.assertIn("def _variant_group_filenames(urls) -> list:", _read(_LAUNCH_PATH))
        self.assertNotIn(
            "def _variant_group_filenames(urls, model_type",
            _read(_LAUNCH_PATH),
        )

    def test_folder_layout_fail_closed_without_leftover_encoder_alias(self):
        # Leftover remapped a bare Qwen filename through
        # `compatible_text_encoder_paths`. Continuum extra_paths still
        # locate an exact folder prefix; leftover alias maps stay unrestored.
        filename = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        relative = os.path.join("Qwen3-VL-32B-Instruct", filename)
        linked_path = self.linked / relative
        linked_path.parent.mkdir(parents=True)
        linked_path.write_bytes(b"same published qwen artifact")
        resolve = _load_wgp_locator(self.locator)

        leftover_alias = resolve(
            f"https://huggingface.invalid/{filename}",
            extra_paths="minimax_h3",
        )
        exact = resolve(
            f"https://huggingface.invalid/{filename}",
            extra_paths="Qwen3-VL-32B-Instruct",
        )

        self.assertIsNone(leftover_alias)
        self.assertEqual(Path(exact), linked_path)

    def test_different_wangp_vae_name_is_not_treated_as_compatible(self):
        wangp_vae = self.linked / "MiniMax-H3-video_vae_fp16.safetensors"
        wangp_vae.write_bytes(b"different tensor artifact")
        resolve = _load_wgp_locator(self.locator)

        result = resolve("minimax_h3/vae/minimax_h3_video_vae_fp16.safetensors")

        self.assertIsNone(result)

    def test_loader_uses_continuum_locator_not_leftover_compatible_resolver(self):
        source = _read(_WGP_PATH)
        self.assertIn("def get_local_model_filename(", source)
        self.assertNotIn("def get_compatible_local_model_filename(", source)
        self.assertIn(
            "local_model_filename = get_local_model_filename(",
            source,
        )
        self.assertIn(
            "text_encoder_filename =  get_local_model_filename(",
            source,
        )

        launch_source = _read(_LAUNCH_PATH)
        self.assertIn("def _variant_group_filenames(urls) -> list:", launch_source)
        self.assertIn("wgp.get_local_model_filename(", launch_source)
        self.assertNotIn("get_compatible_local_model_filename", launch_source)
        self.assertNotIn(
            "_variant_group_filenames(group, model_type=model_type)",
            launch_source,
        )


if __name__ == "__main__":
    unittest.main()
