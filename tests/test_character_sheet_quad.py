"""CPU checks for the exact Quad FLUX LoRA's terms and native load boundary."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import character_sheet_quad as quad  # noqa: E402
from services.host_terms import (  # noqa: E402
    BFL_FLUX2_REVIEW_TERM,
    CHARACTER_SHEET_QUAD_CREATOR_TERM,
    CURRENT_HOST_TERM_BINDINGS,
    accept_host_term,
)
from services.model_terms import (  # noqa: E402
    ModelTermsContractError,
    ModelTermsRequiredError,
    model_terms_manifest_valid,
    require_model_terms,
    required_model_terms,
)


def _load_managed_lora_helper(directory: Path, services: dict, response=None):
    tree = ast.parse((APP / "launch.py").read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_ensure_managed_loras_present"
    )
    model = SimpleNamespace(
        get_lora_dir=lambda _model_type: str(directory),
        resolve_lora_path=lambda _model_type, name: str(directory / name),
        server_config={"services": services},
        models_def={},
    )
    spec = {
        "repo_id": quad.QUAD_FLUX_LORA_REPOSITORY,
        "revision": quad.QUAD_FLUX_LORA_REVISION,
        "remote_path": quad.QUAD_FLUX_LORA_FILENAME,
        "sha256": quad.QUAD_FLUX_LORA_SHA256,
        "size": quad.QUAD_FLUX_LORA_SIZE,
        "label": "Quad Character Sheet",
    }
    get = mock.Mock(return_value=response)
    namespace = {
        "os": os,
        "time": __import__("time"),
        "uuid": uuid,
        "requests": SimpleNamespace(get=get),
        "wgp": model,
        "_MANAGED_LORAS": {quad.QUAD_FLUX_LORA_FILENAME: spec},
        "_civitai_download_lock": threading.Lock(),
        "_civitai_downloads": {},
        "QUAD_FLUX_LORA_FILENAME": quad.QUAD_FLUX_LORA_FILENAME,
        "QUAD_FLUX_RECIPE_ID": quad.QUAD_FLUX_RECIPE_ID,
        "quad_lora_name_matches": quad.quad_lora_name_matches,
        "require_quad_lora_artifact": quad.require_quad_lora_artifact,
        "require_quad_lora_base": quad.require_quad_lora_base,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP / "launch.py"), "exec"), namespace)
    return namespace["_ensure_managed_loras_present"], namespace["_MANAGED_LORAS"], get


def _accepted_terms():
    services = {}
    for term in (CHARACTER_SHEET_QUAD_CREATOR_TERM, BFL_FLUX2_REVIEW_TERM):
        accept_host_term(services, term, 1)
    return services


def _load_wgp_function(name: str, namespace: dict):
    tree = ast.parse((APP / "wgp.py").read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP / "wgp.py"), "exec"), namespace)
    return namespace[name]


class QuadLoraTests(unittest.TestCase):
    def test_case_only_filename_alias_is_recognized(self):
        self.assertTrue(quad.quad_lora_name_matches("quadview_klein9b_v1.safetensors"))
        self.assertTrue(quad.quad_lora_name_matches(r"folder\QUADVIEW_KLEIN9B_V1.SAFETENSORS"))
        self.assertFalse(quad.quad_lora_name_matches("another.safetensors"))

    def test_virtual_recipe_requires_creator_and_base_without_changing_base(self):
        self.assertTrue(model_terms_manifest_valid(quad.QUAD_FLUX_RECIPE_ID, {}))
        self.assertEqual(required_model_terms(quad.QUAD_FLUX_RECIPE_ID, {}), (
            CHARACTER_SHEET_QUAD_CREATOR_TERM, BFL_FLUX2_REVIEW_TERM,
        ))
        self.assertEqual(required_model_terms(quad.QUAD_FLUX_BASE_MODEL, {}), (
            BFL_FLUX2_REVIEW_TERM,
        ))
        with self.assertRaises(ModelTermsRequiredError):
            require_model_terms({}, quad.QUAD_FLUX_RECIPE_ID, {})
        require_model_terms(_accepted_terms(), quad.QUAD_FLUX_RECIPE_ID, {})

    def test_changed_creator_graph_fails_before_existing_acceptance_is_used(self):
        services = _accepted_terms()
        with mock.patch.dict(CURRENT_HOST_TERM_BINDINGS, {
            CHARACTER_SHEET_QUAD_CREATOR_TERM: {
                **CURRENT_HOST_TERM_BINDINGS[CHARACTER_SHEET_QUAD_CREATOR_TERM],
                "file_sha256": "0" * 64,
            },
        }):
            self.assertFalse(model_terms_manifest_valid(quad.QUAD_FLUX_RECIPE_ID, {}))
            with self.assertRaises(ModelTermsContractError):
                require_model_terms(services, quad.QUAD_FLUX_RECIPE_ID, {})

    def test_installed_file_is_hashed_not_trusted_by_name_or_size(self):
        payload = b"verified Quad LoRA test payload"
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            quad, "QUAD_FLUX_LORA_SIZE", len(payload)
        ), mock.patch.object(
            quad, "QUAD_FLUX_LORA_SHA256", hashlib.sha256(payload).hexdigest()
        ):
            path = Path(temp) / quad.QUAD_FLUX_LORA_FILENAME
            with self.assertRaises(quad.QuadLoraArtifactError):
                quad.require_quad_lora_artifact(path)
            path.write_bytes(b"x" * len(payload))
            with self.assertRaisesRegex(quad.QuadLoraArtifactError, "SHA-256"):
                quad.require_quad_lora_artifact(path)
            path.write_bytes(payload)
            quad.require_quad_lora_artifact(path)

    def test_managed_download_requires_terms_base_and_verified_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ensure, _, get = _load_managed_lora_helper(directory, {})
            with self.assertRaises(ModelTermsRequiredError):
                ensure([quad.QUAD_FLUX_LORA_FILENAME], quad.QUAD_FLUX_BASE_MODEL)
            get.assert_not_called()

            ensure, _, get = _load_managed_lora_helper(directory, _accepted_terms())
            with self.assertRaises(quad.QuadLoraArtifactError):
                ensure([quad.QUAD_FLUX_LORA_FILENAME], "flux2_klein_4b")
            get.assert_not_called()

            path = directory / quad.QUAD_FLUX_LORA_FILENAME
            path.write_bytes(b"untrusted file")
            with self.assertRaises(quad.QuadLoraArtifactError):
                ensure([quad.QUAD_FLUX_LORA_FILENAME], quad.QUAD_FLUX_BASE_MODEL)
            get.assert_not_called()
            self.assertEqual(path.read_bytes(), b"untrusted file")

            lower_name = "quadview_klein9b_v1.safetensors"
            lower_path = directory / lower_name
            lower_path.write_bytes(b"untrusted file")
            with self.assertRaises(quad.QuadLoraArtifactError):
                ensure([lower_name], quad.QUAD_FLUX_BASE_MODEL)
            get.assert_not_called()

    def test_valid_existing_file_skips_download_and_unknown_directory_fails_closed(self):
        payload = b"verified installed Quad artifact"
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            quad, "QUAD_FLUX_LORA_SIZE", len(payload)
        ), mock.patch.object(
            quad, "QUAD_FLUX_LORA_SHA256", hashlib.sha256(payload).hexdigest()
        ):
            directory = Path(temp)
            path = directory / quad.QUAD_FLUX_LORA_FILENAME
            path.write_bytes(payload)
            ensure, _, get = _load_managed_lora_helper(directory, _accepted_terms())
            self.assertEqual(
                ensure([quad.QUAD_FLUX_LORA_FILENAME], quad.QUAD_FLUX_BASE_MODEL),
                [],
            )
            get.assert_not_called()

            ensure.__globals__["wgp"].get_lora_dir = mock.Mock(
                side_effect=RuntimeError("unknown LoRA directory")
            )
            with self.assertRaisesRegex(RuntimeError, "Could not resolve"):
                ensure([quad.QUAD_FLUX_LORA_FILENAME], quad.QUAD_FLUX_BASE_MODEL)
            get.assert_not_called()

    def test_managed_download_uses_pinned_revision_and_publishes_only_valid_bytes(self):
        class Response:
            headers = {"content-length": "4"}

            def raise_for_status(self):
                pass

            def iter_content(self, chunk_size):
                yield b"quad"

        payload = b"quad"
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ensure, registry, get = _load_managed_lora_helper(
                directory, _accepted_terms(), Response()
            )
            spec = registry[quad.QUAD_FLUX_LORA_FILENAME]
            spec["size"] = len(payload)
            spec["sha256"] = hashlib.sha256(payload).hexdigest()
            self.assertEqual(
                ensure([quad.QUAD_FLUX_LORA_FILENAME], quad.QUAD_FLUX_BASE_MODEL),
                [quad.QUAD_FLUX_LORA_FILENAME],
            )
            self.assertEqual((directory / quad.QUAD_FLUX_LORA_FILENAME).read_bytes(), payload)
            self.assertIn(f"/resolve/{quad.QUAD_FLUX_LORA_REVISION}/", get.call_args.args[0])
            self.assertEqual(list(directory.glob("*.part")), [])

            (directory / quad.QUAD_FLUX_LORA_FILENAME).unlink()
            spec["sha256"] = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                ensure([quad.QUAD_FLUX_LORA_FILENAME], quad.QUAD_FLUX_BASE_MODEL)
            self.assertEqual(list(directory.iterdir()), [])

    def test_shared_classic_load_gate_rechecks_terms_and_existing_artifact(self):
        payload = b"shared WGP activation proof"
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            quad, "QUAD_FLUX_LORA_SIZE", len(payload)
        ), mock.patch.object(
            quad, "QUAD_FLUX_LORA_SHA256", hashlib.sha256(payload).hexdigest()
        ):
            path = Path(temp) / quad.QUAD_FLUX_LORA_FILENAME
            path.write_bytes(payload)
            namespace = {
                "os": os,
                "server_config": {"services": {}},
                "models_def": {},
                "resolve_lora_path": lambda _model_type, _name: str(path),
                "QUAD_FLUX_LORA_FILENAME": quad.QUAD_FLUX_LORA_FILENAME,
                "QUAD_FLUX_RECIPE_ID": quad.QUAD_FLUX_RECIPE_ID,
                "quad_lora_name_matches": quad.quad_lora_name_matches,
                "require_quad_lora_base": quad.require_quad_lora_base,
                "require_quad_lora_artifact": quad.require_quad_lora_artifact,
                "require_model_terms": require_model_terms,
            }
            gate = _load_wgp_function("_require_quad_lora_activation", namespace)
            with self.assertRaises(ModelTermsRequiredError):
                gate(quad.QUAD_FLUX_BASE_MODEL, [quad.QUAD_FLUX_LORA_FILENAME])
            namespace["server_config"]["services"] = _accepted_terms()
            with self.assertRaises(quad.QuadLoraArtifactError):
                gate("flux2_klein_4b", [quad.QUAD_FLUX_LORA_FILENAME])
            gate(quad.QUAD_FLUX_BASE_MODEL, [quad.QUAD_FLUX_LORA_FILENAME])
            gate(quad.QUAD_FLUX_BASE_MODEL, ["quadview_klein9b_v1.safetensors"])
            path.write_bytes(b"x" * len(payload))
            with self.assertRaisesRegex(quad.QuadLoraArtifactError, "SHA-256"):
                gate(quad.QUAD_FLUX_BASE_MODEL, ["quadview_klein9b_v1.safetensors"])

    def test_civitai_route_rejects_exact_quad_before_download(self):
        tree = ast.parse((APP / "launch.py").read_text(encoding="utf-8"))
        endpoint = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "civitai_download"
        )
        endpoint.decorator_list = []

        class Rejected(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        namespace = {
            "HTTPException": Rejected,
            "Request": object,
            "_is_safe_civitai_url": lambda _url: True,
            "_is_safe_path_component": lambda _name: True,
            "_civitai_lora_arch": lambda _base: "",
            "quad_lora_name_matches": quad.quad_lora_name_matches,
            "wgp": SimpleNamespace(server_config={"services": {}}),
        }
        exec(compile(ast.Module(body=[endpoint], type_ignores=[]), str(APP / "launch.py"), "exec"), namespace)

        base = {
            "download_url": "https://civitai.com/api/download/models/3128511",
            "filename": "other.safetensors",
            "kind": "lora",
            "model_id": 2764727,
            "version_id": 3128511,
        }
        for body in (base, {**base, "model_id": 1, "version_id": 2,
            "filename": "quadview_klein9b_v1.safetensors"}):
            with self.subTest(body=body), self.assertRaises(Rejected) as caught:
                request = SimpleNamespace(json=mock.AsyncMock(return_value=body))
                asyncio.run(namespace["civitai_download"](request))
            self.assertEqual(caught.exception.status_code, 409)
            self.assertIn("creator terms", caught.exception.detail)

        downloader = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run_civitai_download"
        )
        verifier = next(
            node.lineno for node in ast.walk(downloader)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "require_quad_lora_artifact"
        )
        publisher = next(
            node.lineno for node in ast.walk(downloader)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os" and node.func.attr == "replace"
        )
        self.assertLess(verifier, publisher)

        wgp_tree = ast.parse((APP / "wgp.py").read_text(encoding="utf-8"))
        generation = next(
            node for node in wgp_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_generate_video_impl"
        )
        calls = {
            node.func.id: node.lineno
            for node in ast.walk(generation)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"_require_quad_lora_activation", "check_loras_exist"}
        }
        self.assertLess(
            calls["_require_quad_lora_activation"], calls["check_loras_exist"]
        )

    def test_classic_url_download_requires_terms_and_atomic_hash_check(self):
        pinned_url = (
            f"https://huggingface.co/{quad.QUAD_FLUX_LORA_REPOSITORY}/resolve/"
            f"{quad.QUAD_FLUX_LORA_REVISION}/{quad.QUAD_FLUX_LORA_FILENAME}"
        )
        payload = b"Classic pinned Quad artifact"
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(
            quad, "QUAD_FLUX_LORA_SIZE", len(payload)
        ), mock.patch.object(
            quad, "QUAD_FLUX_LORA_SHA256", hashlib.sha256(payload).hexdigest()
        ):
            directory = Path(temp)
            info = mock.Mock()
            fetch = mock.Mock(side_effect=lambda _url, path: Path(path).write_bytes(payload))
            cache = mock.Mock()
            namespace = {
                "os": os,
                "tempfile": tempfile,
                "gr": SimpleNamespace(Progress=lambda **_kwargs: None, Info=info, update=lambda: None),
                "get_state_model_type": lambda _state: quad.QUAD_FLUX_BASE_MODEL,
                "get_lora_dir": lambda _model_type: str(directory),
                "download_file": fetch,
                "update_loras_url_cache": cache,
                "server_config": {"services": {}},
                "models_def": {},
                "QUAD_FLUX_LORA_FILENAME": quad.QUAD_FLUX_LORA_FILENAME,
                "QUAD_FLUX_LORA_REPOSITORY": quad.QUAD_FLUX_LORA_REPOSITORY,
                "QUAD_FLUX_LORA_REVISION": quad.QUAD_FLUX_LORA_REVISION,
                "QUAD_FLUX_RECIPE_ID": quad.QUAD_FLUX_RECIPE_ID,
                "quad_lora_name_matches": quad.quad_lora_name_matches,
                "require_quad_lora_base": quad.require_quad_lora_base,
                "require_quad_lora_artifact": quad.require_quad_lora_artifact,
                "require_model_terms": require_model_terms,
            }
            download = _load_wgp_function("download_lora", namespace)
            download({}, pinned_url)
            fetch.assert_not_called()
            self.assertEqual(list(directory.iterdir()), [])

            namespace["server_config"]["services"] = _accepted_terms()
            download({}, f"https://other.invalid/{quad.QUAD_FLUX_LORA_FILENAME}")
            fetch.assert_not_called()
            download({}, pinned_url)
            self.assertEqual((directory / quad.QUAD_FLUX_LORA_FILENAME).read_bytes(), payload)
            self.assertEqual(list(directory.glob("*.part")), [])
            self.assertEqual(cache.call_count, 1)

            (directory / quad.QUAD_FLUX_LORA_FILENAME).unlink()
            fetch.side_effect = lambda _url, path: Path(path).write_bytes(b"x" * len(payload))
            download({}, pinned_url)
            self.assertFalse((directory / quad.QUAD_FLUX_LORA_FILENAME).exists())
            self.assertEqual(list(directory.iterdir()), [])
            self.assertGreaterEqual(info.call_count, 2)


if __name__ == "__main__":
    unittest.main()
