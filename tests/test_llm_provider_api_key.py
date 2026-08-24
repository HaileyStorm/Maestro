"""Continuum LLM-provider credential resolution contracts."""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from typing import Optional
from unittest import mock


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP = os.path.join(_ROOT, "app")
_LAUNCH_PATH = os.path.join(_APP, "launch.py")
_DIRECTOR_PATH = os.path.join(_APP, "services", "director_pipeline.py")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from services import llm_service


def _launch_provider_api_key():
    with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename="launch.py")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_llm_provider_api_key":
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            namespace = {}
            exec(compile(module, "launch.py", "exec"), namespace)
            return namespace["_llm_provider_api_key"]
    raise AssertionError("Continuum launch.py is missing _llm_provider_api_key")


def _load_functions(path: str, names: tuple[str, ...], namespace=None) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=os.path.basename(path))
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    if len(selected) != len(names):
        found = {node.name for node in selected}
        raise AssertionError(f"Missing functions: {set(names) - found}")
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    loaded = dict(namespace or {})
    exec(compile(module, os.path.basename(path), "exec"), loaded)
    return loaded


class _HttpError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def _services_update_endpoint(server_config: dict, persisted: list) -> object:
    with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source, filename="launch.py")
    node = copy.deepcopy(next(
        item for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "update_services_config"
    ))
    node.decorator_list = []
    mask = _load_functions(_LAUNCH_PATH, ("_mask_key",))["_mask_key"]
    namespace = {
        "Request": object,
        "HTTPException": _HttpError,
        "copy": copy,
        "threading": threading,
        "_services_config_lock": threading.RLock(),
        "_atomic_write_json": lambda _path, value: persisted.append(
            copy.deepcopy(value)
        ),
        "_mask_key": mask,
        "wgp": SimpleNamespace(
            server_config=server_config,
            server_config_filename="/config.json",
        ),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "launch.py", "exec"), namespace)
    return namespace["update_services_config"]


class TestProviderApiKey(unittest.TestCase):
    def setUp(self):
        self.pick_key = _launch_provider_api_key()
        self.services = {
            "llm_remote_api_key": "sk-remote",
            "openai_api_key": "sk-openai",
            "anthropic_api_key": "sk-anthropic",
        }

    def test_launch_keeps_the_single_credential_mapping(self):
        self.assertFalse(hasattr(llm_service, "provider_api_key"))
        self.assertFalse(hasattr(llm_service, "PROVIDER_API_KEY_SETTING"))
        self.assertFalse(hasattr(llm_service, "_llm_api_key_for_provider"))

    def test_each_provider_uses_only_its_own_credential(self):
        self.assertEqual(self.pick_key("remote", self.services), "sk-remote")
        self.assertEqual(self.pick_key(" Remote ", self.services), "sk-remote")
        self.assertEqual(self.pick_key("openai", self.services), "sk-openai")
        self.assertEqual(self.pick_key("anthropic", self.services), "sk-anthropic")
        self.assertEqual(self.pick_key("remote", {}), "")
        self.assertEqual(self.pick_key("openai", {}), "")
        self.assertEqual(self.pick_key("anthropic", {}), "")

    def test_local_and_unknown_providers_send_no_credential(self):
        for provider in ("local", "", "bogus", None):
            with self.subTest(provider=provider):
                self.assertEqual(self.pick_key(provider, self.services), "")

    def test_direct_load_normalizes_provider_before_selecting_credential(self):
        with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        load_source = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "llm_load"
        )) or ""
        self.assertIn(").strip().lower()", load_source)
        self.assertIn("_llm_provider_api_key(provider, services)", load_source)

    def test_all_provider_settings_are_persistable(self):
        with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        update_source = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "update_services_config"
        )) or ""
        for setting in (
            "llm_remote_api_key",
            "openai_api_key",
            "anthropic_api_key",
        ):
            with self.subTest(setting=setting):
                self.assertIn(f'"{setting}"', update_source)
        helper = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_llm_provider_api_key"
        ))
        self.assertIsNotNone(helper)
        self.assertIn("llm_remote_api_key", helper)

    def test_selection_revision_rotates_only_for_actual_selection_changes(self):
        baseline = {
            "llm_provider": "remote",
            "llm_model_id": "model-a",
            "llm_device": "cpu",
            "llm_remote_url": "https://one.example.test",
            "llm_remote_api_key": "secret-a",
            "openai_api_key": "",
            "anthropic_api_key": "",
            "llm_selection_revision": "stable-revision",
        }

        same_config = {"services": dict(baseline)}
        persisted = []
        endpoint = _services_update_endpoint(same_config, persisted)
        asyncio.run(endpoint(_Request({"llm_model_id": "model-a"})))
        asyncio.run(endpoint(_Request({"llm_remote_api_key": "secret-a"})))
        self.assertEqual(
            same_config["services"]["llm_selection_revision"],
            "stable-revision",
        )

        for masked_value in ("***", "abcd...wxyz"):
            masked_config = {"services": dict(baseline)}
            persisted = []
            endpoint = _services_update_endpoint(masked_config, persisted)
            with self.subTest(masked_value=masked_value):
                with self.assertRaises(_HttpError):
                    asyncio.run(endpoint(_Request({
                        "llm_remote_api_key": masked_value,
                    })))
                self.assertEqual(
                    masked_config["services"]["llm_selection_revision"],
                    "stable-revision",
                )
                self.assertEqual(persisted, [])

        for field, value in (
            ("llm_provider", "openai"),
            ("llm_model_id", "model-b"),
            ("llm_device", "cuda"),
            ("llm_remote_url", "https://two.example.test"),
            ("llm_remote_api_key", "secret-b"),
            ("openai_api_key", "openai-secret"),
            ("anthropic_api_key", "anthropic-secret"),
        ):
            config = {"services": dict(baseline)}
            persisted = []
            endpoint = _services_update_endpoint(config, persisted)
            with self.subTest(field=field):
                asyncio.run(endpoint(_Request({field: value})))
                self.assertNotEqual(
                    config["services"]["llm_selection_revision"],
                    "stable-revision",
                )
                self.assertEqual(persisted[-1], config)

        missing_config = {"services": {
            key: value for key, value in baseline.items()
            if key != "llm_selection_revision"
        }}
        endpoint = _services_update_endpoint(missing_config, [])
        asyncio.run(endpoint(_Request({"llm_model_id": "model-a"})))
        self.assertTrue(missing_config["services"]["llm_selection_revision"])

    def test_every_nonempty_mask_round_trips_as_preserved(self):
        helpers = _load_functions(
            _LAUNCH_PATH,
            ("_mask_key",),
        )
        mask = helpers["_mask_key"]
        for secret in ("x", "123456789", "1234567890", "x" * 80):
            with self.subTest(length=len(secret)):
                masked = mask(secret)
                self.assertTrue(masked)
        self.assertEqual(mask(""), "")

        with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        update_source = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "update_services_config"
        )) or ""
        self.assertIn("is_masked_key(value)", update_source)

    def test_director_maps_the_remote_credential(self):
        with open(_DIRECTOR_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("llm_service.provider_api_key(", source)
        self.assertIn('"remote": "llm_remote_api_key"', source)
        self.assertIn("_director_llm_selection_from_snapshot", source)

    def test_director_selection_binds_one_current_snapshot_and_rejects_drift(self):
        services = {
            "llm_model_id": "current-model",
            "llm_device": "cpu",
            "llm_provider": "remote",
            "llm_remote_url": "https://current.example.test",
            "llm_remote_api_key": "current-remote-secret",
            "openai_api_key": "different-provider-secret",
            "llm_selection_revision": "revision-one",
        }
        namespace = {
            "_wgp": SimpleNamespace(server_config={"services": services}),
            "copy": copy,
            "re": re,
            "Optional": Optional,
            "_DIRECTOR_LEGACY_SECRET_FIELDS": {
                "_director_llm_selection_digest",
                "api_key",
                "authorization",
                "authorization_header",
                "bearer_token",
                "access_token",
                "api_token",
                "client_secret",
                "secret_key",
                "password",
            },
            "_DIRECTOR_LLM_SELECTION_RECEIPT_KEY": (
                "_director_llm_selection_receipt"
            ),
            "_DEFAULT_DIRECTOR_LLM_MODEL": (
                "Abhiray/gemma-4-E4B-it-heretic-GGUF"
            ),
        }
        loaded = _load_functions(
            _DIRECTOR_PATH,
            (
                "_director_llm_services_snapshot",
                "_director_llm_selection_from_snapshot",
                "_director_llm_selection_receipt",
                "scrub_director_public_credentials",
                "_strip_director_llm_secrets",
                "_bind_director_llm_selection",
                "_missing_director_llm_receipt_is_safe",
                "_director_llm_selection",
            ),
            namespace,
        )
        select = loaded["_director_llm_selection"]
        bind = loaded["_bind_director_llm_selection"]
        params = {
            "llm_model_id": "queued-model",
            "llm_device": "cuda",
            "llm_provider": "remote",
        }
        bind(params)
        receipt = params["_director_llm_selection_receipt"]
        self.assertEqual(receipt, {
            "provider": "remote",
            "model_id": "current-model",
            "device": "cpu",
            "endpoint": "https://current.example.test",
            "revision": "revision-one",
        })
        self.assertNotIn("current-remote-secret", json.dumps(params))
        self.assertNotIn("api_key", json.dumps(receipt))
        selection = select(params)
        self.assertEqual(selection["model_id"], "current-model")
        self.assertEqual(selection["device"], "cpu")
        self.assertEqual(selection["provider"], "remote")
        self.assertEqual(
            selection["remote_url"],
            "https://current.example.test",
        )
        self.assertEqual(selection["api_key"], "current-remote-secret")

        for key, value in (
            ("llm_model_id", "changed-model"),
            ("llm_device", "cuda"),
            ("llm_remote_url", "https://changed.example.test"),
        ):
            original = services[key]
            services[key] = value
            with self.subTest(drift=key):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "settings changed after this run was queued",
                ):
                    select(params)
            services[key] = original

        services["llm_remote_api_key"] = "changed-secret"
        services["llm_selection_revision"] = "revision-two"
        with self.assertRaisesRegex(
            RuntimeError,
            "settings changed after this run was queued",
        ):
            select(params)
        services["llm_remote_api_key"] = "current-remote-secret"
        services["llm_selection_revision"] = "revision-one"

        services["llm_provider"] = "openai"
        with self.assertRaisesRegex(RuntimeError, "changed after this run was queued"):
            select(params)

    def test_director_missing_receipt_compatibility_is_local_only(self):
        services = {
            "llm_model_id": "current-model",
            "llm_device": "cpu",
            "llm_provider": "remote",
            "llm_remote_url": "https://current.example.test",
            "llm_remote_api_key": "secret",
            "llm_selection_revision": "remote-revision",
        }
        namespace = {
            "_wgp": SimpleNamespace(server_config={"services": services}),
            "Optional": Optional,
            "_DIRECTOR_LLM_SELECTION_RECEIPT_KEY": (
                "_director_llm_selection_receipt"
            ),
            "_DEFAULT_DIRECTOR_LLM_MODEL": (
                "Abhiray/gemma-4-E4B-it-heretic-GGUF"
            ),
        }
        loaded = _load_functions(
            _DIRECTOR_PATH,
            (
                "_director_llm_services_snapshot",
                "_director_llm_selection_from_snapshot",
                "_director_llm_selection_receipt",
                "_missing_director_llm_receipt_is_safe",
                "_director_llm_selection",
            ),
            namespace,
        )
        select = loaded["_director_llm_selection"]
        with self.assertRaisesRegex(RuntimeError, "receipt is unavailable"):
            select({"llm_provider": "remote", "llm_model_id": "old-model"})
        with self.assertRaisesRegex(RuntimeError, "receipt is unavailable"):
            select({})

        namespace["_wgp"].server_config = {"services": {
            "llm_provider": "local",
            "llm_model_id": "local-model",
            "llm_device": "cpu",
            "llm_selection_revision": "local-revision",
        }}
        self.assertEqual(
            select({"llm_provider": "local", "llm_model_id": "old-local"})[
                "provider"
            ],
            "local",
        )

        namespace["_wgp"].server_config["services"]["openai_api_key"] = "saved"
        with self.assertRaisesRegex(RuntimeError, "receipt is unavailable"):
            select({"llm_provider": "local"})

        with open(_DIRECTOR_PATH, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        start_source = ast.get_source_segment(source, next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "start_pipeline"
        )) or ""
        self.assertIn("_bind_director_llm_selection(", start_source)

    def test_held_receipt_persists_restores_and_guards_execution(self):
        from services import director_pipeline as pipeline

        services = {
            "llm_provider": "remote",
            "llm_model_id": "held-model",
            "llm_device": "cpu",
            "llm_remote_url": "https://held.example.test",
            "llm_remote_api_key": "held-secret",
            "llm_selection_revision": "held-revision",
        }
        originals = (
            pipeline._wgp,
            pipeline._pipelines,
            pipeline._director_queue_state,
            pipeline._director_queue_base,
            pipeline._director_queue_worker,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                pipeline._wgp = SimpleNamespace(
                    save_path=temp_dir,
                    server_config={"services": services},
                )
                pipeline._pipelines = {}
                pipeline._director_queue_state = None
                pipeline._director_queue_base = None
                pipeline._director_queue_worker = None
                source_params = {
                    "scene_description": "held project",
                    "pipeline_type": "music_video",
                    "llm_remote_api_key": "must-not-persist",
                    "provider_api_key": "provider-alias-secret",
                    "_director_llm_selection_digest": "legacy-digest",
                    "nested": {
                        "openaiApiKey": "nested-openai-secret",
                        "authorization": "Bearer nested-token",
                        "client_secret": "nested-client-secret",
                    },
                }
                source_before = copy.deepcopy(source_params)
                public = pipeline.enqueue_director_pipeline(
                    temp_dir,
                    source_params,
                )
                self.assertEqual(source_params, source_before)
                self.assertEqual(public["entries"][0]["status"], "held")

                queue_path = os.path.join(temp_dir, "_director_queue.json")
                with open(queue_path, "r", encoding="utf-8") as handle:
                    persisted_text = handle.read()
                self.assertNotIn("held-secret", persisted_text)
                self.assertNotIn("must-not-persist", persisted_text)
                for forbidden in (
                    "provider-alias-secret",
                    "legacy-digest",
                    "nested-openai-secret",
                    "nested-token",
                    "nested-client-secret",
                    '"provider_api_key"',
                    '"_director_llm_selection_digest"',
                    '"openaiApiKey"',
                    '"authorization"',
                    '"client_secret"',
                ):
                    self.assertNotIn(forbidden, persisted_text)
                persisted = json.loads(persisted_text)
                frozen = persisted["entries"][0]["params"]
                self.assertEqual(
                    frozen["_director_llm_selection_receipt"]["revision"],
                    "held-revision",
                )

                pipeline._director_queue_state = None
                pipeline._director_queue_base = None
                restored = pipeline.get_director_queue_entry(
                    temp_dir,
                    persisted["entries"][0]["id"],
                )
                restored_params = restored["params"]
                self.assertEqual(
                    pipeline._director_llm_selection(restored_params)["model_id"],
                    "held-model",
                )
                with (
                    mock.patch.object(pipeline, "_validate_director_models"),
                    mock.patch.object(
                        pipeline,
                        "_create_director_video_execution_profile",
                        return_value={"is_minimax_h3": False},
                    ),
                    mock.patch.object(pipeline, "_start_pipeline_worker") as worker,
                ):
                    pid = pipeline.start_pipeline(copy.deepcopy(restored_params))
                worker.assert_called_once_with(pid)
                with open(
                    os.path.join(temp_dir, f"_director_pipeline_{pid}.json"),
                    "r",
                    encoding="utf-8",
                ) as handle:
                    persisted_pipeline_text = handle.read()
                for forbidden in (
                    "provider-alias-secret",
                    "legacy-digest",
                    "nested-openai-secret",
                    "nested-token",
                    "nested-client-secret",
                ):
                    self.assertNotIn(forbidden, persisted_pipeline_text)

                services["llm_selection_revision"] = "changed-revision"
                with self.assertRaisesRegex(
                    RuntimeError,
                    "settings changed after this run was queued",
                ):
                    pipeline._director_llm_selection(restored_params)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "settings changed after this run was queued",
                ):
                    pipeline.start_pipeline(copy.deepcopy(restored_params))
            finally:
                (
                    pipeline._wgp,
                    pipeline._pipelines,
                    pipeline._director_queue_state,
                    pipeline._director_queue_base,
                    pipeline._director_queue_worker,
                ) = originals

    def test_legacy_public_status_and_queue_reads_scrub_without_mutation(self):
        from services import director_pipeline as pipeline

        receipt = {
            "provider": "remote",
            "model_id": "safe-model",
            "device": "cpu",
            "endpoint": "https://safe.example.test",
            "revision": "safe-revision",
        }
        legacy_params = {
            "scene_description": "legacy",
            "api_key": "plain-api-key",
            "llm_remote_api_key": "plain-remote-key",
            "openai_api_key": "plain-openai-key",
            "anthropic_api_key": "plain-anthropic-key",
            "provider_api_key": "client-alias-key",
            "openaiApiKey": "camel-client-alias-key",
            "authorization": "Bearer plain-token",
            "access_token": "plain-access-token",
            "client_secret": "plain-client-secret",
            "clientCredentials": "plain-client-credentials",
            "_director_llm_selection_digest": "former-secret-derived-digest",
            "_director_llm_selection_receipt": copy.deepcopy(receipt),
            "llm_selection_revision": "safe-revision",
        }

        status_source = {
            "pipeline_id": "legacy-status",
            "status": "paused",
            "api_key": "root-secret",
            "_params_snapshot": copy.deepcopy(legacy_params),
            "llm_log": "historical content",
        }
        durable_status = copy.deepcopy(status_source)
        with open(_LAUNCH_PATH, "r", encoding="utf-8") as handle:
            launch_source = handle.read()
        tree = ast.parse(launch_source, filename="launch.py")
        node = copy.deepcopy(next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_public_pipeline_state"
        ))
        node.decorator_list = []
        namespace = {
            "_CREDIT_INTERNAL_PARAMS": set(),
            "_strip_director_image_role_internals": lambda value: value,
            "_redact_local_paths": lambda value: value,
            "_sanitize_director_public_failures": lambda value: value,
        }
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), "launch.py", "exec"),
            namespace,
        )
        public_status = namespace["_public_pipeline_state"](status_source)
        rendered_status = json.dumps(public_status)
        for secret in (
            "plain-api-key",
            "plain-remote-key",
            "plain-openai-key",
            "plain-anthropic-key",
            "client-alias-key",
            "camel-client-alias-key",
            "plain-token",
            "plain-access-token",
            "plain-client-secret",
            "plain-client-credentials",
            "former-secret-derived-digest",
            "root-secret",
        ):
            self.assertNotIn(secret, rendered_status)
        self.assertEqual(
            public_status["_params_snapshot"]["_director_llm_selection_receipt"],
            receipt,
        )
        self.assertEqual(status_source, durable_status)

        queue_state = {
            "version": 1,
            "paused": True,
            "running": False,
            "entries": [{
                "id": "legacy-entry",
                "status": "held",
                "api_key": "top-level-queue-secret",
                "params": copy.deepcopy(legacy_params),
            }],
        }
        durable_queue = copy.deepcopy(queue_state)
        originals = (
            pipeline._director_queue_state,
            pipeline._director_queue_base,
            pipeline._director_queue_worker,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                pipeline._director_queue_state = queue_state
                pipeline._director_queue_base = os.path.realpath(temp_dir)
                pipeline._director_queue_worker = None
                public_list = pipeline.list_director_queue(temp_dir)
                public_single = pipeline.get_director_queue_entry(
                    temp_dir,
                    "legacy-entry",
                )
            finally:
                (
                    pipeline._director_queue_state,
                    pipeline._director_queue_base,
                    pipeline._director_queue_worker,
                ) = originals

        rendered_list = json.dumps(public_list)
        rendered_single = json.dumps(public_single)
        for secret in (
            "top-level-queue-secret",
            "plain-api-key",
            "plain-remote-key",
            "plain-openai-key",
            "plain-anthropic-key",
            "client-alias-key",
            "camel-client-alias-key",
            "plain-token",
            "plain-access-token",
            "plain-client-secret",
            "plain-client-credentials",
            "former-secret-derived-digest",
        ):
            self.assertNotIn(secret, rendered_list)
            self.assertNotIn(secret, rendered_single)
        self.assertEqual(
            public_single["params"]["_director_llm_selection_receipt"],
            receipt,
        )
        self.assertEqual(queue_state, durable_queue)


class TestApiHeaders(unittest.TestCase):
    def setUp(self):
        self.saved = (llm_service._provider, llm_service._api_key)

    def tearDown(self):
        llm_service._provider, llm_service._api_key = self.saved

    def _headers_for(self, provider, key):
        llm_service._provider = provider
        llm_service._api_key = key
        return llm_service._api_headers()

    def test_remote_uses_bearer_token(self):
        self.assertEqual(
            self._headers_for("remote", "sk-remote").get("Authorization"),
            "Bearer sk-remote",
        )

    def test_anthropic_uses_its_native_headers(self):
        headers = self._headers_for("anthropic", "sk-anthropic")
        self.assertEqual(headers.get("x-api-key"), "sk-anthropic")
        self.assertEqual(headers.get("anthropic-version"), "2023-06-01")
        self.assertNotIn("Authorization", headers)

    def test_local_and_keyless_remote_send_no_auth(self):
        self.assertNotIn("Authorization", self._headers_for("local", ""))
        self.assertNotIn("Authorization", self._headers_for("remote", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
