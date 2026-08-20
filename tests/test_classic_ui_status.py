"""Continuum Classic UI generation-status helpers.

Locks leftover 1.9.0 `initialize_gen_info` / required-field factory
expectations to Continuum `get_gen_info` plus fail-open `prompt_no`
reads. Partial queue state must not KeyError.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WGP_PATH = ROOT / "app" / "wgp.py"
WGP_SOURCE = WGP_PATH.read_text(encoding="utf-8")


def _load_status_helpers() -> dict:
    tree = ast.parse(WGP_SOURCE, filename=str(WGP_PATH))
    function_names = {
        "get_gen_info",
        "get_generation_status",
        "get_new_refresh_id",
        "merge_status_context",
        "get_latest_status",
        "update_status",
    }
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected.append(node)
        elif isinstance(node, ast.Assign):
            assigned_names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            if assigned_names & {"refresh_id"}:
                selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict = {}
    exec(compile(module, str(WGP_PATH), "exec"), namespace)
    return namespace


class ClassicUiStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = _load_status_helpers()

    def test_continuum_has_no_initialize_gen_info(self):
        tree = ast.parse(WGP_SOURCE, filename=str(WGP_PATH))
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("initialize_gen_info", names)
        self.assertIn("get_gen_info", names)
        self.assertIn("prompt_no = gen.get(\"prompt_no\", 0)", WGP_SOURCE)
        self.assertNotIn('prompt_no = gen["prompt_no"]', WGP_SOURCE)

    def test_get_gen_info_creates_empty_cache_without_invented_defaults(self):
        state = {"queue": []}
        gen = self.helpers["get_gen_info"](state)

        self.assertIs(gen, state["gen"])
        self.assertEqual({}, gen)
        self.assertNotIn("prompt_no", gen)
        self.assertNotIn("progress_status", gen)

    def test_add_to_queue_status_accepts_legacy_partial_state(self):
        state = {"gen": {"queue": [], "prompts_max": 2}}

        self.helpers["update_status"](state)

        self.assertEqual(0, state["gen"].get("prompt_no", 0))
        self.assertEqual("Prompt 0/2", state["gen"]["progress_status"])
        self.assertGreater(state["gen"]["refresh"], 0)

    def test_missing_gen_dictionary_is_initialized_safely(self):
        state = {}

        status = self.helpers["get_latest_status"](state)

        self.assertEqual("Prompt 0/0", status)
        self.assertIn("gen", state)
        self.assertEqual(0, state["gen"].get("prompt_no", 0))


if __name__ == "__main__":
    unittest.main()
