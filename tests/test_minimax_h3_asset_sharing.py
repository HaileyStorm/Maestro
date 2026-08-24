"""Regressions for exact MiniMax H3 asset aliases across linked installs."""

from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1]
_WGP_PATH = _ROOT / "app" / "wgp.py"
_LAUNCH_PATH = _ROOT / "app" / "launch.py"
_LOCATOR_PATH = _ROOT / "app" / "shared" / "utils" / "files_locator.py"
_HANDLER_PATH = (
    _ROOT / "app" / "models" / "minimax_h3" / "minimax_h3_handler.py"
)
_H3_MAIN_PATH = _ROOT / "app" / "models" / "minimax_h3" / "minimax_h3_main.py"
_DROPDOWNS_PATH = _ROOT / "app" / "shared" / "model_dropdowns.py"
_MODELS_MANAGER_PATH = (
    _ROOT / "app" / "plugins" / "wan2gp-models-manager" / "plugin.py"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_registered_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


def _load_models_manager_path_helpers(locator):
    tree = ast.parse(_read(_MODELS_MANAGER_PATH), filename=str(_MODELS_MANAGER_PATH))
    plugin_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "modelsManagerPlugin"
    )
    wanted = {
        "_add_file",
        "_delete_files_for_node",
        "_resolve_path",
        "_resolve_expected_entry_path",
    }
    functions = [
        node
        for node in plugin_class.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"os": os, "fl": locator}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
            str(_MODELS_MANAGER_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace


def _load_wgp_resolver(model_def: dict, locator):
    tree = ast.parse(_read(_WGP_PATH), filename=str(_WGP_PATH))
    wanted = {"get_local_model_filename", "get_compatible_local_model_filename"}
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "os": os,
        "fl": locator,
        "get_model_def": lambda _model_type: model_def,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_WGP_PATH), "exec"), namespace)
    return namespace["get_compatible_local_model_filename"]


def _load_handler(name: str):
    torch_stub = ModuleType("torch")
    torch_stub.bfloat16 = object()
    with patch.dict(sys.modules, {"torch": torch_stub}):
        return _load_module(_HANDLER_PATH, name)


def _load_required_runtime_assets_ready():
    tree = ast.parse(_read(_DROPDOWNS_PATH), filename=str(_DROPDOWNS_PATH))
    wanted = {"_required_runtime_asset_paths", "required_runtime_assets_ready"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {"os": os}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])),
            str(_DROPDOWNS_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace["required_runtime_assets_ready"]


def _load_launch_variant_helpers(model_def: dict, locator):
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
            get_model_def=lambda _model_type: model_def,
            get_compatible_local_model_filename=_load_wgp_resolver(
                model_def,
                locator,
            ),
        ),
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_LAUNCH_PATH), "exec"), namespace)
    return (
        namespace["_variant_group_filenames"],
        namespace["_variant_group_downloaded"],
    )


def _load_launch_model_check(model_def: dict, locator):
    tree = ast.parse(_read(_LAUNCH_PATH), filename=str(_LAUNCH_PATH))
    wanted = {
        "_variant_group_filenames",
        "_variant_group_downloaded",
        "_model_weight_groups",
        "_check_model_downloaded",
    }
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    resolver = _load_wgp_resolver(model_def, locator)
    namespace = {
        "os": os,
        "required_runtime_assets_ready": _load_required_runtime_assets_ready(),
        "wgp": SimpleNamespace(
            get_model_def=lambda _model_type: model_def,
            get_model_recursive_prop=(
                lambda _model_type, prop, **_kwargs: model_def.get(prop, [])
            ),
            get_compatible_local_model_filename=resolver,
            get_local_model_filename=lambda filename, extra_paths=None: resolver(
                filename,
                "minimax_h3",
                extra_paths=extra_paths,
            ),
            resolve_lora_path=lambda *_args, **_kwargs: "",
            models_def={"minimax_h3": model_def},
            fl=locator,
        ),
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_LAUNCH_PATH), "exec"), namespace)
    return namespace["_check_model_downloaded"]


def _load_qkv_layout_helper():
    tree = ast.parse(_read(_H3_MAIN_PATH), filename=str(_H3_MAIN_PATH))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_restore_interleaved_transformer_qkv"
    )
    namespace = {}
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(_H3_MAIN_PATH), "exec"), namespace)
    return namespace["_restore_interleaved_transformer_qkv"]


class _FakeTensor:
    """Small tensor protocol sufficient for the pure QKV row converter."""

    def __init__(self, data, shape):
        self.data = list(data)
        self.shape = tuple(int(value) for value in shape)
        size = 1
        for value in self.shape:
            size *= value
        if size != len(self.data):
            raise ValueError("Fake tensor data does not match its shape")

    @property
    def ndim(self):
        return len(self.shape)

    def reshape(self, *shape):
        return _FakeTensor(self.data, shape)

    def permute(self, dimensions):
        dimensions = tuple(dimensions)
        output_shape = tuple(self.shape[index] for index in dimensions)

        def unravel(index, shape):
            coordinates = [0] * len(shape)
            for axis in range(len(shape) - 1, -1, -1):
                coordinates[axis] = index % shape[axis]
                index //= shape[axis]
            return coordinates

        def ravel(coordinates, shape):
            index = 0
            for coordinate, width in zip(coordinates, shape):
                index = index * width + coordinate
            return index

        output = []
        for flat_index in range(len(self.data)):
            output_coordinates = unravel(flat_index, output_shape)
            input_coordinates = [0] * self.ndim
            for output_axis, input_axis in enumerate(dimensions):
                input_coordinates[input_axis] = output_coordinates[output_axis]
            output.append(self.data[ravel(input_coordinates, self.shape)])
        return _FakeTensor(output, output_shape)

    def contiguous(self):
        return self


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
        self.handler = _load_handler(
            f"maestro_h3_handler_sharing_{id(self)}",
        )
        self.dropdowns = _load_registered_module(
            _DROPDOWNS_PATH,
            f"maestro_model_dropdowns_sharing_{id(self)}",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _transformer_cases():
        return {
            "minimax_h3": (
                "minimax_h3_fl2va_pruned_fp8_scaled.safetensors",
                "MiniMax-H3-FL2VA-pruned_rank8_int8_convrot.safetensors",
                "MiniMax-H3-FL2VA-pruned_rank8_bf16.safetensors",
            ),
            "minimax_h3_ref2va": (
                "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
                "MiniMax-H3-Ref2VA-pruned_rank8_int8_convrot.safetensors",
                "MiniMax-H3-Ref2VA-pruned_rank8_bf16.safetensors",
            ),
        }

    @staticmethod
    def _required_asset_paths(model_def):
        manifest = model_def["required_runtime_assets"]
        return [
            path
            for value in manifest.values()
            for path in (value if isinstance(value, list) else [value])
        ]

    def _write_required_assets(self, root, model_def, *, omit=None):
        omit = set(omit or ())
        for relative_path in self._required_asset_paths(model_def):
            if relative_path in omit:
                continue
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"exact published H3 auxiliary asset")

    def test_handler_declares_only_exact_known_transformer_aliases(self):
        for model_type, (canonical, alternate, bf16) in self._transformer_cases().items():
            with self.subTest(model_type=model_type):
                model_def = self.handler.family_handler.query_model_def(model_type, {})
                self.assertEqual(
                    model_def["compatible_model_paths"],
                    {
                        canonical: [alternate],
                        alternate: [canonical],
                    },
                )
                self.assertEqual(
                    model_def["compatible_model_qkv_layouts"],
                    {
                        alternate: "interleaved",
                        bf16: "interleaved",
                    },
                )
                self.assertFalse(
                    any(
                        "vae" in name.lower()
                        for name in model_def["compatible_model_paths"]
                    )
                )

    def test_handler_manifest_is_exact_and_download_plan_is_derived_from_it(self):
        model_def = self.handler.family_handler.query_model_def("minimax_h3", {})
        manifest = model_def["required_runtime_assets"]
        self.assertEqual(
            manifest,
            {
                "video_vae": os.path.join(
                    "minimax_h3", "vae", "minimax_h3_video_vae_fp16.safetensors"
                ),
                "audio_vae": os.path.join(
                    "minimax_h3", "vae", "minimax_h3_audio_vae_fp32.safetensors"
                ),
                "text_encoder_config": os.path.join(
                    "minimax_h3", "text_encoder", "config.json"
                ),
                "processor": [
                    os.path.join("minimax_h3", "processor", filename)
                    for filename in (
                        "chat_template.json",
                        "merges.txt",
                        "preprocessor_config.json",
                        "tokenizer.json",
                        "tokenizer_config.json",
                        "video_preprocessor_config.json",
                        "vocab.json",
                    )
                ],
            },
        )
        planned = set()
        for download_def in self.handler.family_handler.query_model_files(
            lambda value: value,
            "minimax_h3",
            model_def,
        ):
            for source, target, files in zip(
                download_def["sourceFolderList"],
                download_def["targetFolderList"],
                download_def["fileList"],
            ):
                planned.update(os.path.join(target, source, filename) for filename in files)
        self.assertEqual(planned, set(self._required_asset_paths(model_def)))

    def test_each_required_runtime_asset_gates_api_and_classic_readiness(self):
        text_encoder = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        for model_type, (canonical, _alternate, _bf16) in self._transformer_cases().items():
            model_def = self.handler.family_handler.query_model_def(model_type, {})
            model_def["URLs"] = [canonical]
            for index, missing_path in enumerate(self._required_asset_paths(model_def)):
                with self.subTest(model_type=model_type, missing=missing_path):
                    root = Path(self.temp_dir.name) / "missing" / model_type / str(index)
                    root.mkdir(parents=True)
                    self.locator.set_checkpoints_paths([str(root)])
                    (root / canonical).write_bytes(b"transformer")
                    encoder = root / "minimax_h3" / text_encoder
                    encoder.parent.mkdir(parents=True)
                    encoder.write_bytes(b"conditioner")
                    self._write_required_assets(root, model_def, omit={missing_path})

                    check_downloaded = _load_launch_model_check(model_def, self.locator)
                    self.assertFalse(check_downloaded(model_type))
                    self.assertEqual(
                        self.dropdowns.get_model_download_status(
                            self._dropdown_deps(model_type, model_def), model_type
                        ),
                        self.dropdowns.MODEL_FILE_STATUS_PARTIAL,
                    )
        self.locator.set_checkpoints_paths([str(self.primary), str(self.linked)])

    def test_complete_linked_asset_folder_is_ready_for_both_h3_variants(self):
        text_encoder = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        for model_type, (canonical, alternate, _bf16) in self._transformer_cases().items():
            with self.subTest(model_type=model_type):
                (self.linked / alternate).write_bytes(b"linked transformer")
                encoder = self.linked / "Qwen3-VL-32B-Instruct" / text_encoder
                encoder.parent.mkdir(parents=True, exist_ok=True)
                encoder.write_bytes(b"linked conditioner")
                model_def = self.handler.family_handler.query_model_def(model_type, {})
                model_def["URLs"] = [canonical]
                self._write_required_assets(self.linked, model_def)

                self.assertTrue(
                    _load_launch_model_check(model_def, self.locator)(model_type)
                )
                self.assertEqual(
                    self.dropdowns.get_model_download_status(
                        self._dropdown_deps(model_type, model_def), model_type
                    ),
                    self.dropdowns.MODEL_FILE_STATUS_EXPECTED,
                )

    def test_split_processor_folder_is_partial_and_runtime_requires_full_folder(self):
        model_type = "minimax_h3"
        canonical, _alternate, _bf16 = self._transformer_cases()[model_type]
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        model_def["URLs"] = [canonical]
        (self.primary / canonical).write_bytes(b"transformer")
        encoder = self.primary / "minimax_h3" / "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        encoder.parent.mkdir(parents=True)
        encoder.write_bytes(b"conditioner")
        processor_paths = model_def["required_runtime_assets"]["processor"]
        non_processor = set(self._required_asset_paths(model_def)) - set(processor_paths)
        self._write_required_assets(
            self.primary,
            model_def,
            omit=set(processor_paths[3:]),
        )
        self._write_required_assets(
            self.linked,
            model_def,
            omit=non_processor | set(processor_paths[:3]),
        )

        self.assertFalse(_load_launch_model_check(model_def, self.locator)(model_type))
        self.assertEqual(
            self.dropdowns.get_model_download_status(
                self._dropdown_deps(model_type, model_def), model_type
            ),
            self.dropdowns.MODEL_FILE_STATUS_PARTIAL,
        )
        source = _read(_H3_MAIN_PATH)
        self.assertIn("required_files=processor_files", source)
        self.assertIn("len(processor_relative) == 7", source)

    def test_exact_canonical_checkpoint_wins_over_declared_alias(self):
        model_type = "minimax_h3"
        canonical, alternate, _ = self._transformer_cases()[model_type]
        canonical_path = self.primary / canonical
        alternate_path = self.linked / alternate
        canonical_path.write_bytes(b"maestro")
        alternate_path.write_bytes(b"wangp")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        resolve = _load_wgp_resolver(model_def, self.locator)

        result = resolve(
            f"https://huggingface.invalid/{canonical}",
            model_type,
            file_type=0,
        )

        self.assertEqual(Path(result), canonical_path)

    def test_declared_linked_alias_is_reused_for_both_h3_variants(self):
        for model_type, (canonical, alternate, _) in self._transformer_cases().items():
            with self.subTest(model_type=model_type):
                alternate_path = self.linked / alternate
                alternate_path.write_bytes(b"wangp")
                model_def = self.handler.family_handler.query_model_def(model_type, {})
                resolve = _load_wgp_resolver(model_def, self.locator)

                result = resolve(
                    f"https://huggingface.invalid/{canonical}",
                    model_type,
                    file_type=0,
                )

                self.assertEqual(Path(result), alternate_path)
                self.assertFalse((self.primary / canonical).exists())
                source = self.locator.describe_file_source(result)
                self.assertEqual(source["kind"], "linked")
                self.assertEqual(source["installation"], "wan.git")

    def test_launch_readiness_accepts_linked_declared_transformer_alias(self):
        model_type = "minimax_h3"
        canonical, alternate, _ = self._transformer_cases()[model_type]
        alternate_path = self.linked / alternate
        alternate_path.write_bytes(b"wangp")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        filenames, downloaded = _load_launch_variant_helpers(
            model_def,
            self.locator,
        )
        urls = [f"https://huggingface.invalid/{canonical}"]

        self.assertEqual(
            filenames(urls, model_type=model_type),
            [canonical, alternate],
        )
        self.assertTrue(downloaded(urls, model_type=model_type))

    def test_declared_aliases_are_bidirectional(self):
        for model_type, (canonical, alternate, _) in self._transformer_cases().items():
            with self.subTest(model_type=model_type):
                canonical_path = self.primary / canonical
                canonical_path.write_bytes(b"existing maestro")
                model_def = self.handler.family_handler.query_model_def(model_type, {})
                resolve = _load_wgp_resolver(model_def, self.locator)

                result = resolve(
                    f"https://huggingface.invalid/{alternate}",
                    model_type,
                    file_type=0,
                )

                self.assertEqual(Path(result), canonical_path)

    def test_unknown_transformer_alias_fails_closed(self):
        model_type = "minimax_h3"
        canonical, _, _ = self._transformer_cases()[model_type]
        unknown = "MiniMax-H3-FL2VA-pruned_rank8_int8.safetensors"
        (self.linked / unknown).write_bytes(b"not a declared alias")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        resolve = _load_wgp_resolver(model_def, self.locator)

        result = resolve(
            f"https://huggingface.invalid/{canonical}",
            model_type,
            file_type=0,
        )

        self.assertIsNone(result)
        self.assertFalse((self.primary / canonical).exists())

    def test_qwen_encoder_resolves_exact_wangp_folder_layout(self):
        model_type = "minimax_h3"
        filename = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        relative = os.path.join("Qwen3-VL-32B-Instruct", filename)
        linked_path = self.linked / relative
        linked_path.parent.mkdir(parents=True)
        linked_path.write_bytes(b"same published qwen artifact")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        self.assertEqual(
            model_def["compatible_text_encoder_paths"],
            {filename: [relative]},
        )
        resolve = _load_wgp_resolver(model_def, self.locator)

        result = resolve(
            f"https://huggingface.invalid/{filename}",
            model_type,
            file_type=2,
            extra_paths="minimax_h3",
        )

        self.assertEqual(Path(result), linked_path)

    def test_launch_readiness_accepts_qwen_folder_alias(self):
        model_type = "minimax_h3"
        filename = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        relative = os.path.join("Qwen3-VL-32B-Instruct", filename)
        linked_path = self.linked / relative
        linked_path.parent.mkdir(parents=True)
        linked_path.write_bytes(b"same published qwen artifact")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        _filenames, downloaded = _load_launch_variant_helpers(
            model_def,
            self.locator,
        )

        self.assertTrue(
            downloaded(
                [f"https://huggingface.invalid/{filename}"],
                model_type=model_type,
                file_type=2,
                extra_paths="minimax_h3",
            )
        )

    def test_model_download_check_accepts_both_linked_h3_aliases(self):
        model_type = "minimax_h3"
        canonical, alternate, _ = self._transformer_cases()[model_type]
        (self.linked / alternate).write_bytes(b"wangp transformer")
        text_encoder = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        text_encoder_path = self.linked / "Qwen3-VL-32B-Instruct" / text_encoder
        text_encoder_path.parent.mkdir(parents=True)
        text_encoder_path.write_bytes(b"wangp qwen")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        model_def["URLs"] = [f"https://huggingface.invalid/{canonical}"]
        self._write_required_assets(self.linked, model_def)
        check_downloaded = _load_launch_model_check(model_def, self.locator)

        self.assertTrue(check_downloaded(model_type))

    def test_qwen_canonical_path_wins_over_folder_alias(self):
        model_type = "minimax_h3"
        filename = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        canonical_path = self.primary / "minimax_h3" / filename
        alias_path = self.linked / "Qwen3-VL-32B-Instruct" / filename
        canonical_path.parent.mkdir(parents=True)
        alias_path.parent.mkdir(parents=True)
        canonical_path.write_bytes(b"maestro")
        alias_path.write_bytes(b"wangp")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        resolve = _load_wgp_resolver(model_def, self.locator)

        result = resolve(
            f"https://huggingface.invalid/{filename}",
            model_type,
            file_type=2,
            extra_paths="minimax_h3",
        )

        self.assertEqual(Path(result), canonical_path)

    def _dropdown_deps(self, model_type, model_def, include_compatible=True):
        resolver = _load_wgp_resolver(model_def, self.locator)

        def get_local(filename, extra_paths=None):
            local_name = (
                os.path.basename(filename)
                if str(filename).startswith("http")
                else filename
            )
            folders = extra_paths or []
            if not isinstance(folders, list):
                folders = [folders]
            for folder in folders:
                located = self.locator.locate_file(
                    os.path.join(folder, local_name), error_if_none=False
                )
                if located is not None:
                    return located
            return self.locator.locate_file(local_name, error_if_none=False)

        def recursive_prop(_model_type, prop, **_kwargs):
            if prop == "modules":
                return []
            return model_def.get(prop, [])

        kwargs = dict(
            transformer_types=[model_type],
            displayed_model_types=[model_type],
            transformer_type=model_type,
            three_levels_hierarchy=True,
            families_infos={},
            server_config={},
            transformer_quantization="fp8",
            transformer_dtype_policy="bf16",
            text_encoder_quantization="int8",
            get_model_def=lambda _model_type: model_def,
            get_model_recursive_prop=recursive_prop,
            get_model_filename=lambda *args, **kwargs: (
                "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
                if "URLs" in kwargs
                and kwargs.get("URLs") == model_def.get("text_encoder_URLs")
                else self._transformer_cases()[model_type][0]
            ),
            get_local_model_filename=get_local,
            get_lora_dir=lambda _model_type: str(self.primary / "loras"),
            get_parent_model_type=lambda value: value,
            get_base_model_type=lambda value: value,
            get_model_family=lambda *_args, **_kwargs: "minimax_h3",
            get_model_name=lambda value: value,
            get_transformer_dtype=lambda *_args: "bf16",
            locate_model_folder=self.locator.locate_folder,
        )
        if include_compatible:
            kwargs["get_compatible_local_model_filename"] = resolver
        return self.dropdowns.DropdownDeps(**kwargs)

    def test_dropdown_deps_without_compatibility_callback_uses_canonical_fallback(self):
        model_type = "minimax_h3"
        canonical, _, _ = self._transformer_cases()[model_type]
        canonical_path = self.primary / canonical
        canonical_path.write_bytes(b"canonical transformer")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        model_def.update(
            {
                "URLs": [canonical],
                "text_encoder_URLs": None,
                "VAE_URLs": [],
                "preload_URLs": [],
                "loras": [],
                "modules": [],
            }
        )
        self._write_required_assets(self.primary, model_def)

        deps = self._dropdown_deps(
            model_type, model_def, include_compatible=False
        )

        self.assertIsNone(deps.get_compatible_local_model_filename)
        self.assertEqual(
            self.dropdowns.get_model_download_status(deps, model_type),
            self.dropdowns.MODEL_FILE_STATUS_EXPECTED,
        )

    def test_classic_dropdown_status_accepts_exact_h3_transformer_and_text_aliases(self):
        model_type = "minimax_h3"
        canonical, alternate, _ = self._transformer_cases()[model_type]
        (self.linked / alternate).write_bytes(b"wangp transformer")
        text_encoder = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
        linked_encoder = self.linked / "Qwen3-VL-32B-Instruct" / text_encoder
        linked_encoder.parent.mkdir(parents=True)
        linked_encoder.write_bytes(b"wangp encoder")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        model_def.update(
            {
                "URLs": [canonical],
                "text_encoder_URLs": [text_encoder],
                "text_encoder_folder": "minimax_h3",
            }
        )
        self._write_required_assets(self.linked, model_def)

        status = self.dropdowns.get_model_download_status(
            self._dropdown_deps(model_type, model_def), model_type
        )

        self.assertEqual(status, self.dropdowns.MODEL_FILE_STATUS_EXPECTED)

    def test_classic_dropdown_status_rejects_unknown_alias_and_alternate_vae(self):
        model_type = "minimax_h3"
        canonical, _, _ = self._transformer_cases()[model_type]
        unknown = "MiniMax-H3-FL2VA-pruned_rank8_int8.safetensors"
        (self.linked / unknown).write_bytes(b"undeclared transformer")
        (self.primary / canonical).write_bytes(b"canonical transformer")
        (self.linked / "MiniMax-H3-video_vae_fp16.safetensors").write_bytes(
            b"different vae"
        )
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        model_def.update(
            {
                "URLs": [canonical],
                "VAE_URLs": [
                    "minimax_h3/vae/minimax_h3_video_vae_fp16.safetensors"
                ],
            }
        )
        self._write_required_assets(
            self.primary,
            model_def,
            omit={model_def["required_runtime_assets"]["video_vae"]},
        )
        deps = self._dropdown_deps(model_type, model_def)

        self.assertEqual(
            self.dropdowns.get_model_download_status(deps, model_type),
            self.dropdowns.MODEL_FILE_STATUS_PARTIAL,
        )
        (self.primary / canonical).unlink()
        self.assertEqual(
            self.dropdowns.get_model_download_status(deps, model_type),
            self.dropdowns.MODEL_FILE_STATUS_MISSING,
        )

    def test_dropdown_module_slots_remain_canonical_only(self):
        model_type = "minimax_h3"
        canonical, _, _ = self._transformer_cases()[model_type]
        module_name = "h3_auxiliary_module.safetensors"
        module_alias = "shared_transformer_alias.safetensors"
        (self.primary / canonical).write_bytes(b"canonical transformer")
        (self.linked / module_alias).write_bytes(b"wrong slot alias")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        model_def.update(
            {
                "URLs": [canonical],
                "modules": ["h3_auxiliary_module"],
                "compatible_model_paths": {module_name: [module_alias]},
            }
        )
        self._write_required_assets(self.primary, model_def)
        deps = self._dropdown_deps(model_type, model_def)
        deps.get_model_recursive_prop = lambda _model_type, prop, **_kwargs: (
            ["h3_auxiliary_module"] if prop == "modules" else model_def.get(prop, [])
        )
        deps.get_model_filename = lambda *args, **kwargs: (
            module_name if kwargs.get("module_type") else canonical
        )

        entries = self.dropdowns.get_expected_core_file_entries_for_status(
            deps, model_type
        )
        module_entry = next(
            entry for entry in entries if entry["filename"] == module_name
        )
        self.assertEqual(module_entry["file_type"], 1)
        self.assertEqual(
            self.dropdowns.get_model_download_status(deps, model_type),
            self.dropdowns.MODEL_FILE_STATUS_PARTIAL,
        )
        (self.primary / module_name).write_bytes(b"canonical module")
        self.assertEqual(
            self.dropdowns.get_model_download_status(deps, model_type),
            self.dropdowns.MODEL_FILE_STATUS_EXPECTED,
        )

    def test_models_manager_expected_entry_resolves_to_actual_linked_alias(self):
        model_type = "minimax_h3"
        canonical, alternate, _ = self._transformer_cases()[model_type]
        linked_alias = self.linked / alternate
        linked_alias.write_bytes(b"wangp transformer")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        resolver = _load_wgp_resolver(model_def, self.locator)
        helpers = _load_models_manager_path_helpers(self.locator)
        manager = SimpleNamespace(
            get_compatible_local_model_filename=resolver,
            get_local_model_filename=self.locator.locate_file,
            _normalize_path=lambda path: os.path.normpath(os.path.abspath(path)),
            _resolve_repo_relative_path=lambda _path: None,
        )
        manager._resolve_path = helpers["_resolve_path"].__get__(manager)
        manager._add_file = helpers["_add_file"].__get__(manager)
        manager._delete_files_for_node = helpers[
            "_delete_files_for_node"
        ].__get__(manager)
        manager._resolve_expected_entry_path = helpers[
            "_resolve_expected_entry_path"
        ].__get__(manager)

        resolved = manager._resolve_expected_entry_path(
            {"filename": canonical, "extra_paths": None, "file_type": 0},
            model_type,
        )

        self.assertEqual(Path(resolved), linked_alias)
        self.assertNotEqual(Path(resolved), self.primary / canonical)
        self.assertEqual(
            self.locator.describe_file_source(resolved)["installation"], "wan.git"
        )
        displayed_files = set()
        manager._add_file(
            displayed_files, canonical, model_type=model_type, file_type=0
        )
        self.assertEqual(displayed_files, {os.path.normpath(str(linked_alias))})
        manager._node_map = {
            "model::minimax_h3": {
                "files": set(displayed_files),
                "unique_files": set(displayed_files),
            }
        }
        shared_package = ModuleType("shared")
        shared_package.__path__ = []
        utils_package = ModuleType("shared.utils")
        utils_package.__path__ = []
        with patch.dict(
            sys.modules,
            {
                "shared": shared_package,
                "shared.utils": utils_package,
                "shared.utils.files_locator": self.locator,
            },
        ):
            removed, missing, errors = manager._delete_files_for_node(
                "model::minimax_h3", delete_shared=True
            )
        self.assertEqual(removed, [])
        self.assertEqual(missing, [])
        self.assertEqual(
            errors,
            [
                (
                    os.path.normpath(str(linked_alias)),
                    "linked read-only folder, not deleted",
                )
            ],
        )
        self.assertTrue(linked_alias.is_file())

    def test_different_wangp_vae_name_is_not_treated_as_compatible(self):
        model_type = "minimax_h3"
        wangp_vae = self.linked / "MiniMax-H3-video_vae_fp16.safetensors"
        wangp_vae.write_bytes(b"different tensor artifact")
        model_def = self.handler.family_handler.query_model_def(model_type, {})
        resolve = _load_wgp_resolver(model_def, self.locator)

        result = resolve(
            "minimax_h3/vae/minimax_h3_video_vae_fp16.safetensors",
            model_type,
            file_type=0,
        )

        self.assertIsNone(result)

    def test_download_and_load_paths_use_compatible_resolver(self):
        source = _read(_WGP_PATH)
        self.assertIn("def get_compatible_local_model_filename(", source)
        self.assertIn(
            "local_model_filename = get_compatible_local_model_filename(",
            source,
        )
        self.assertIn(
            "local_file_name = get_compatible_local_model_filename(",
            source,
        )
        self.assertIn(
            "text_encoder_filename = get_compatible_local_model_filename(",
            source,
        )
        self.assertIn(
            "download_models(text_encoder_filename, model_type, 2, -1,",
            source,
        )
        download_start = source.index("def download_models(")
        download_end = source.index("\noffload.default_verboseLevel", download_start)
        download_source = source[download_start:download_end]
        self.assertLess(
            download_source.index("get_compatible_local_model_filename("),
            download_source.index(
                "fl.get_smart_download_location(os.path.basename(model_filename)"
            ),
        )

    def test_transformer_loader_consumes_declared_qkv_layout(self):
        source = _read(_H3_MAIN_PATH)
        self.assertIn(
            'qkv_layout = str(model_def.get("minimax_h3_qkv_layout") or "contiguous")',
            source,
        )
        self.assertIn(
            'model_def.get("compatible_model_qkv_layouts", {}).get(',
            source,
        )
        self.assertIn("os.path.basename(transformer_path)", source)
        self.assertIn('qkv_layout: str = "contiguous"', source)
        self.assertIn('if qkv_layout != "interleaved":', source)
        self.assertIn("_restore_interleaved_transformer_qkv(", source)
        self.assertIn("qkv_layout=qkv_layout", source)
        self.assertIn("transformer.h3_qkv_layout = qkv_layout", source)

        selection = source.index(
            'model_def.get("compatible_model_qkv_layouts", {}).get('
        )
        load = source.index("self.transformer = _load_transformer(", selection)
        self.assertLess(selection, load)

    def test_qkv_layout_conversion_reorders_only_interleaved_fused_rows(self):
        restore = _load_qkv_layout_helper()
        heads = 56
        head_dim = 128
        rows = heads * 3 * head_dim
        weight = _FakeTensor((index * 2 for index in range(rows)), (rows, 1))
        scale = _FakeTensor((index + 0.5 for index in range(rows)), (rows, 1))
        unrelated = _FakeTensor([10, 11, 12, 13], (2, 2))
        state = {
            "blocks.0.attn.qkv_proj.weight": weight,
            "blocks.0.attn.qkv_proj.weight_scale": scale,
            "blocks.0.attn.out_proj.weight": unrelated,
        }

        restored = restore(state, qkv_layout="interleaved")
        expected_rows = [
            head * (3 * head_dim) + projection * head_dim + channel
            for projection in range(3)
            for head in range(heads)
            for channel in range(head_dim)
        ]
        self.assertEqual(
            restored["blocks.0.attn.qkv_proj.weight"].data,
            [index * 2 for index in expected_rows],
        )
        self.assertEqual(
            restored["blocks.0.attn.qkv_proj.weight_scale"].data,
            [index + 0.5 for index in expected_rows],
        )
        self.assertIs(restored["blocks.0.attn.out_proj.weight"], unrelated)

        contiguous_weight = _FakeTensor(range(rows), (rows, 1))
        contiguous_state = {
            "blocks.0.attn.qkv_proj.weight": contiguous_weight,
            "blocks.0.attn.out_proj.weight": unrelated,
        }
        unchanged = restore(contiguous_state, qkv_layout="contiguous")
        self.assertIs(unchanged, contiguous_state)
        self.assertIs(
            unchanged["blocks.0.attn.qkv_proj.weight"],
            contiguous_weight,
        )
        self.assertIs(unchanged["blocks.0.attn.out_proj.weight"], unrelated)

    def test_launch_readiness_and_predownload_share_alias_resolver(self):
        source = _read(_LAUNCH_PATH)
        self.assertIn(
            "_variant_group_downloaded(g, model_type=model_type)",
            source,
        )
        self.assertIn(
            "for filename in _variant_group_filenames(urls, model_type=model_type)",
            source,
        )
        self.assertIn("wgp.get_compatible_local_model_filename(", source)
        self.assertGreaterEqual(
            source.count("_variant_group_filenames(group, model_type="),
            3,
        )

        predownload_start = source.index("def _download_model_files(")
        predownload_end = source.index(
            '\n@api.post("/api/v1/models/{model_type}/download")',
            predownload_start,
        )
        predownload = source[predownload_start:predownload_end]
        self.assertIn("wgp.get_compatible_local_model_filename(", predownload)
        self.assertIn("text_encoder_filename,\n                model_type,", predownload)
        self.assertIn("file_type=2", predownload)
        self.assertIn("extra_paths=text_encoder_folder", predownload)


if __name__ == "__main__":
    unittest.main()
