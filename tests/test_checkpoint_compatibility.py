"""Continuum CivitAI checkpoint-import gates.

Locks leftover 1.9.0 `ensure_allowed_checkpoint_target` /
`unsupported_checkpoint_reason` / `validate_checkpoint_file` /
`quarantine_incompatible_checkpoint_definitions` probes onto Continuum
`_scan_defaults_by_arch` membership, `_guess_arch_for_base`, and
`_validate_safetensors_payload`. Do not invent leftover base-model
checkpoint gates or leftover LTXV2→ltx2_19B generation splits.
"""
from __future__ import annotations

import ast
import glob
import json
import os
import struct
import tempfile
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LAUNCH_PATH = os.path.join(_ROOT, "app", "launch.py")
_DEFAULTS_DIR = os.path.join(_ROOT, "app", "defaults")
_LEFTOVER_MODULE = os.path.join(
    _ROOT, "app", "services", "checkpoint_compatibility.py"
)


def _parse_launch() -> tuple[ast.Module, str]:
    with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
        source = handle.read()
    return ast.parse(source, filename="app/launch.py"), source


def _function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found")


def _source_segment(tree: ast.AST, source: str, name: str) -> str:
    segment = ast.get_source_segment(source, _function(tree, name))
    if segment is None:
        raise AssertionError(f"Could not read source for {name!r}")
    return segment


def _literal_assignment(tree: ast.AST, name: str):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        assigned = {
            target.id for target in node.targets if isinstance(target, ast.Name)
        }
        if name in assigned:
            return ast.literal_eval(node.value)
    raise AssertionError(f"Assignment {name!r} not found")


def _load_continuum_helpers(tree: ast.Module) -> dict:
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "_scan_defaults_by_arch",
            "_guess_arch_for_base",
            "_validate_safetensors_payload",
        }
    ]
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "os": os,
        "glob": glob,
        "json": json,
        "struct": struct,
        "_DEFAULTS_DIR": _DEFAULTS_DIR,
        "CIVIT_TO_LOCAL_ARCH": _literal_assignment(tree, "CIVIT_TO_LOCAL_ARCH"),
        "_CIVIT_BASE_TO_ARCH_HINT": _literal_assignment(
            tree, "_CIVIT_BASE_TO_ARCH_HINT"
        ),
    }
    exec(compile(module, "app/launch.py", "exec"), namespace)
    return namespace


def _write_safetensors(path: str, *, file_size: int | None = None) -> None:
    header = {
        "img_in.weight": {
            "dtype": "F16",
            "shape": [3072, 64],
            "data_offsets": [0, 2],
        }
    }
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload = struct.pack("<Q", len(encoded)) + encoded + b"\0\0"
    if file_size is not None:
        if file_size < len(payload):
            raise AssertionError("requested file_size is smaller than header")
        payload = payload + b"\0" * (file_size - len(payload))
    with open(path, "wb") as handle:
        handle.write(payload)


class TestContinuumCheckpointGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree, cls.source = _parse_launch()
        cls.helpers = _load_continuum_helpers(cls.tree)

    def test_launch_uses_continuum_gates_not_leftover_base_model_helpers(self):
        # Leftover 1.9.0 gated imports with ensure_allowed_checkpoint_target
        # and listed architectures by leftover base_model. Continuum fail-closes
        # on the shipped defaults index and only suggests an architecture.
        self.assertNotIn("checkpoint_compatibility", self.source)
        self.assertNotIn("ensure_allowed_checkpoint_target", self.source)
        self.assertNotIn("unsupported_checkpoint_reason", self.source)
        self.assertNotIn("validate_checkpoint_file(", self.source)
        self.assertNotIn(
            "quarantine_incompatible_checkpoint_definitions", self.source
        )
        self.assertNotIn("_list_checkpoint_architectures(base_model)", self.source)
        self.assertIn("def _scan_defaults_by_arch", self.source)
        self.assertIn("def _guess_arch_for_base", self.source)
        self.assertIn("def _validate_safetensors_payload", self.source)
        self.assertIn("arch_index = _scan_defaults_by_arch()", self.source)
        self.assertIn("target_architecture not in arch_index", self.source)
        self.assertIn("_guess_arch_for_base(base_model", self.source)

    def test_scan_defaults_membership_replaces_leftover_allowlist(self):
        index = self.helpers["_scan_defaults_by_arch"]()
        for architecture in (
            "flux",
            "flux2_dev",
            "flux2_klein_4b",
            "flux2_klein_9b",
            "ltx2_19B",
            "ltx2_22B",
            "krea2_raw",
            "krea2_turbo",
            "qwen_image_20B",
            "z_image",
        ):
            with self.subTest(architecture=architecture):
                self.assertIn(architecture, index)
                template = os.path.basename(index[architecture])[:-5]
                with open(index[architecture], "r", encoding="utf-8") as handle:
                    definition = json.load(handle)
                self.assertEqual(
                    definition["model"]["architecture"], architecture
                )
                self.assertTrue(os.path.isfile(
                    os.path.join(_DEFAULTS_DIR, f"{template}.json")
                ))

    def test_guess_arch_uses_continuum_hints_not_leftover_ltx_split(self):
        guess = self.helpers["_guess_arch_for_base"]
        index = self.helpers["_scan_defaults_by_arch"]()

        # Leftover 1.9.0 mapped LTXV2 → ltx2_19B and LTXV 2.3 → ltx2_22B.
        # Continuum hints both CivitAI LTX-2 labels at ltx2_22B.
        self.assertEqual(guess("LTXV2", index), "ltx2_22B")
        self.assertEqual(guess("LTXV 2.3", index), "ltx2_22B")
        self.assertNotEqual(guess("LTXV2", index), "ltx2_19B")

        self.assertEqual(guess("Flux.1 D", index), "flux")
        self.assertEqual(guess("Flux.2 D", index), "flux2_dev")
        self.assertEqual(guess("Qwen", index), "qwen_image_20B")

        # Leftover 1.9.0 required a raw/turbo choice for Krea 2. Continuum
        # only suggests when the hint or lora-dir is a real architecture.
        self.assertIsNone(guess("Krea 2", index))
        self.assertIsNone(guess("", index))
        self.assertIsNone(guess("SDXL 1.0", index))
        self.assertIsNone(guess("Wan 2.2", index))

        hint = _source_segment(self.tree, self.source, "_guess_arch_for_base")
        self.assertNotIn("unsupported_checkpoint_reason", hint)
        self.assertNotIn("cannot safely choose a compatible pipeline", hint)

    def test_validate_safetensors_payload_is_size_and_header_only(self):
        validate = self.helpers["_validate_safetensors_payload"]
        worker = _source_segment(
            self.tree, self.source, "_validate_safetensors_payload"
        )
        # Leftover 1.9.0 used validate_checkpoint_file shape/base-model gates.
        # Continuum only checks minimum size and the safetensors header.
        self.assertNotIn("base_model", worker)
        self.assertNotIn("target_architecture", worker)
        self.assertNotIn("ensure_allowed_checkpoint_target", worker)
        self.assertIn("100 * 1024", worker)

        with tempfile.TemporaryDirectory() as directory:
            tiny = os.path.join(directory, "tiny.safetensors")
            _write_safetensors(tiny)
            with self.assertRaisesRegex(ValueError, "too small"):
                validate(tiny)

            valid = os.path.join(directory, "valid.safetensors")
            _write_safetensors(valid, file_size=100 * 1024)
            validate(valid)

            truncated = os.path.join(directory, "truncated.safetensors")
            claimed = 100 * 1024
            with open(truncated, "wb") as handle:
                handle.write(struct.pack("<Q", claimed))
                handle.write(b"{}" + b"\0" * (claimed - 2))
            with self.assertRaisesRegex(ValueError, "header claims"):
                validate(truncated)

    def test_leftover_checkpoint_module_is_not_the_live_import_path(self):
        # The leftover 1.9.0 helper module may still sit in services/. Continuum
        # CivitAI import must not restore those base-model gates into launch.
        if os.path.isfile(_LEFTOVER_MODULE):
            with open(_LEFTOVER_MODULE, "r", encoding="utf-8") as handle:
                leftover = handle.read()
            self.assertIn("def ensure_allowed_checkpoint_target", leftover)
            self.assertIn("def unsupported_checkpoint_reason", leftover)
            self.assertIn("def validate_checkpoint_file", leftover)
            self.assertIn(
                "def quarantine_incompatible_checkpoint_definitions", leftover
            )
        self.assertNotIn("from services.checkpoint_compatibility", self.source)
        self.assertNotIn("import checkpoint_compatibility", self.source)


if __name__ == "__main__":
    unittest.main()
