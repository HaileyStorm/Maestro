"""CPU checks for native Music3 catalog and model-admission boundaries."""

from __future__ import annotations

import ast
import copy
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from models.TTS.minimax_music3_handler import _model_definition
from services.host_terms import MUSIC3_REVIEW_TERM


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


def _launch_namespace(*names: str) -> dict:
    tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
    selected = []
    for name in names:
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == name
        )
        node = copy.deepcopy(node)
        node.decorator_list = []
        selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"Request": object, "HTTPException": _HTTPException}
    exec(compile(module, str(LAUNCH), "exec"), namespace)
    return namespace


def _native_definition() -> dict:
    definition = _model_definition()
    definition.update(json.loads(
        (APP / "defaults" / "minimax_music3.json").read_text(encoding="utf-8")
    )["model"])
    return definition


class _Registry:
    def __init__(self, *, music3_definition: dict | None = None) -> None:
        self.server_config = {"services": {}}
        self.displayed_model_types = ["ordinary_model", "minimax_music3"]
        self.models_def = {
            "ordinary_model": {"name": "Ordinary model"},
            "minimax_music3": music3_definition or _native_definition(),
        }
        self.families_infos = {"ordinary": (1, "Ordinary"), "tts": (200, "Audio")}

    def get_model_def(self, model_type):
        return self.models_def.get(model_type)

    def get_model_family(self, model_type, *, for_ui=False):
        return "tts" if model_type == "minimax_music3" else "ordinary"

    def get_base_model_type(self, model_type):
        return model_type

    def test_class_i2v(self, _model_type):
        return False

    def test_class_t2v(self, _model_type):
        return False


def _catalog(*, remote: bool, definition: dict | None = None):
    namespace = _launch_namespace("list_models")
    registry = _Registry(music3_definition=definition)
    readiness = []
    namespace.update({
        "wgp": registry,
        "_remote_visible_model_ids": (
            lambda _request: frozenset({"ordinary_model", "minimax_music3"})
            if remote else None
        ),
        "_versioned_model_updater": types.SimpleNamespace(
            apply_recorded=lambda *_args: None,
            apply_recorded_components=lambda *_args: None,
        ),
        "_versioned_model_update_status": {},
        "_check_model_downloaded": (
            lambda model_type: readiness.append(model_type) or False
        ),
        "_public_manual_installation_manifest": lambda _model_def: None,
        "h3_public_availability": lambda *_args, **_kwargs: {
            "execution_allowed": True,
        },
    })
    request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=remote))
    return namespace["list_models"](request), readiness


class Music3CatalogTests(unittest.TestCase):
    def test_native_model_is_visible_with_same_capability_on_local_and_remote(self):
        for remote in (False, True):
            with self.subTest(remote=remote):
                result, readiness = _catalog(remote=remote)
                models = {model["model_type"]: model for model in result["models"]}
                self.assertEqual(set(models), {"ordinary_model", "minimax_music3"})
                music3 = models["minimax_music3"]
                self.assertEqual(music3["name"], "MiniMax-Music3")
                self.assertEqual(music3["family"], "tts")
                self.assertEqual(music3["architecture"], "minimax_music3")
                self.assertTrue(music3["execution_allowed"])
                self.assertTrue(music3["downloadable"])
                self.assertTrue(music3["generates_audio"])
                self.assertFalse(music3["is_downloaded"])
                self.assertEqual(
                    [item["term"] for item in music3["required_host_terms"]],
                    [MUSIC3_REVIEW_TERM],
                )
                self.assertEqual(readiness, ["ordinary_model", "minimax_music3"])

    def test_modified_music3_source_is_absent_even_when_registered(self):
        modified = _native_definition()
        modified["URLs"] = ["https://example.invalid/other.safetensors"]
        result, readiness = _catalog(remote=False, definition=modified)
        self.assertEqual(
            [model["model_type"] for model in result["models"]],
            ["ordinary_model"],
        )
        self.assertEqual(readiness, ["ordinary_model"])

    def test_remote_model_requires_explicit_visibility(self):
        namespace = _launch_namespace("_require_remote_visible_models")
        namespace["_remote_visible_model_ids"] = (
            lambda request: frozenset({"minimax_music3"})
            if request.state.maestro_remote else None
        )
        local = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=False))
        remote = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=True))
        namespace["_require_remote_visible_models"](local, ["minimax_music3"])
        namespace["_require_remote_visible_models"](remote, ["minimax_music3"])
        with self.assertRaises(_HTTPException) as raised:
            namespace["_require_remote_visible_models"](remote, ["hidden"])
        self.assertEqual(raised.exception.status_code, 404)

    def test_director_checks_model_terms_before_registering_a_song(self):
        source = LAUNCH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAUNCH))
        function = next(
            item for item in tree.body
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == "director_generate_music"
        )
        body = ast.get_source_segment(source, function)
        self.assertLess(
            body.index("out_dir = _require_project_access("),
            body.index("_require_model_recipe_terms([model_type])"),
        )
        self.assertLess(
            body.index("_require_model_recipe_terms([model_type])"),
            body.index("_register_director_preparation"),
        )
        self.assertEqual(body.count("_require_model_recipe_terms([model_type])"), 2)

    def test_handler_is_registered_without_virtual_catalog(self):
        self.assertIn(
            '"models.TTS.minimax_music3_handler"',
            (APP / "wgp.py").read_text(encoding="utf-8"),
        )
        self.assertNotIn("_music3_virtual_catalog_model", LAUNCH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
