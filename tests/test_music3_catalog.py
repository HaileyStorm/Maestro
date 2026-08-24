"""Focused CPU-only tests for the informational Music 3 catalog entry."""

from __future__ import annotations

import ast
import copy
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services import music3_runtime as runtime
from services.minimax_music3_sglang_contract import (
    LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
    LOCAL_EXPERIMENT_REQUIRED_GATES,
)


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Updater:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def apply_recorded(self, model_type, _model_def):
        self.calls.append(("model", model_type))

    def apply_recorded_components(self, model_type, _model_def):
        self.calls.append(("components", model_type))


class _Registry:
    def __init__(self, *, music3_collision: bool = False) -> None:
        self.server_config = {"services": {}}
        self.displayed_model_types = ["ordinary_model"]
        self.models_def = {
            "ordinary_model": {
                "name": "Ordinary model",
                "description": "Existing WGP entry",
            },
        }
        if music3_collision:
            self.displayed_model_types.append("minimax_music3")
            self.models_def["minimax_music3"] = {
                "name": "Untrusted WGP collision",
                "downloadable": True,
            }
        self.families_infos = {
            "ordinary": (1, "Ordinary"),
            "tts": (200, "Audio"),
        }

    def get_model_def(self, model_type):
        return self.models_def.get(model_type)

    def get_model_family(self, _model_type, *, for_ui=False):
        self.last_for_ui = for_ui
        return "ordinary"

    def get_base_model_type(self, model_type):
        return model_type

    def test_class_i2v(self, _model_type):
        return False

    def test_class_t2v(self, _model_type):
        return True


def _launch_namespace(*names: str):
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
    namespace = {"Request": object}
    exec(compile(module, str(LAUNCH), "exec"), namespace)
    return namespace


def _catalog_namespace(*, remote: bool, music3_collision: bool = False):
    namespace = _launch_namespace(
        "_music3_virtual_catalog_model",
        "list_models",
        "_normalize_model_visibility_ids",
    )
    registry = _Registry(music3_collision=music3_collision)
    updater = _Updater()
    readiness_calls: list[str] = []
    namespace.update({
        "wgp": registry,
        "_remote_visible_model_ids": (
            lambda _request: (
                frozenset({"ordinary_model", "minimax_music3"})
                if remote else None
            )
        ),
        "_versioned_model_updater": updater,
        "_versioned_model_update_status": {},
        "_check_model_downloaded": (
            lambda model_type: readiness_calls.append(model_type) or True
        ),
        "_public_manual_installation_manifest": lambda _model_def: None,
        "h3_public_availability": lambda *_args, **_kwargs: {
            "execution_allowed": True,
        },
    })
    return namespace, registry, updater, readiness_calls


class Music3CatalogTests(unittest.TestCase):
    def test_local_catalog_projects_one_fixed_non_wgp_entry(self):
        namespace, registry, updater, readiness_calls = _catalog_namespace(
            remote=False,
        )
        response = namespace["list_models"](
            types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=False)),
        )
        model_ids = [item["model_type"] for item in response["models"]]
        catalog = {
            item["model_type"]: item for item in response["models"]
        }

        self.assertEqual(set(catalog), {"ordinary_model", "minimax_music3"})
        self.assertEqual(model_ids.count("minimax_music3"), 1)
        music3 = catalog["minimax_music3"]
        self.assertNotIn("minimax_music3", registry.displayed_model_types)
        self.assertNotIn("minimax_music3", registry.models_def)
        self.assertEqual(updater.calls, [
            ("model", "ordinary_model"),
            ("components", "ordinary_model"),
        ])
        self.assertEqual(readiness_calls, ["ordinary_model"])
        self.assertEqual(music3["attribution"], {
            "creator": "MiniMaxAI",
            "model_id": runtime.MUSIC3_MODEL_ID,
            "source_url": (
                "https://huggingface.co/" + runtime.MUSIC3_MODEL_ID
            ),
            "required": True,
        })
        self.assertEqual(
            music3["license"]["authorization_scope"],
            LOCAL_EXPERIMENT_AUTHORIZATION_SCOPE,
        )
        self.assertEqual(
            music3["license"]["status"],
            "owner_approval_required",
        )
        self.assertEqual(
            music3["license"]["required_approvals"],
            list(LOCAL_EXPERIMENT_REQUIRED_GATES),
        )
        self.assertEqual(
            music3["local_experiment"]["model_revision"],
            runtime.PINNED_MODEL_REVISION,
        )
        self.assertEqual(
            music3["local_experiment"]["runtime_source_revision"],
            runtime.PINNED_SGLANG_SOURCE_REVISION,
        )
        self.assertEqual(
            {
                key: music3["local_experiment"][key]
                for key in (
                    "local_only", "lan", "cloudflare", "hosted_service",
                    "runtime_attested",
                )
            },
            {
                "local_only": True,
                "lan": False,
                "cloudflare": False,
                "hosted_service": False,
                "runtime_attested": False,
            },
        )

    def test_wgp_registry_collision_cannot_replace_virtual_contract(self):
        namespace, _registry, updater, readiness_calls = _catalog_namespace(
            remote=False,
            music3_collision=True,
        )
        response = namespace["list_models"](
            types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=False)),
        )
        music3 = [
            item for item in response["models"]
            if item["model_type"] == "minimax_music3"
        ]

        self.assertEqual(len(music3), 1)
        self.assertEqual(music3[0]["name"], "MiniMax Music 3")
        self.assertFalse(music3[0]["downloadable"])
        self.assertFalse(music3[0]["execution_allowed"])
        self.assertNotIn(
            "minimax_music3",
            [model_type for _kind, model_type in updater.calls],
        )
        self.assertNotIn("minimax_music3", readiness_calls)

    def test_remote_catalog_omits_music3_even_if_whitelisted(self):
        requests = (
            types.SimpleNamespace(
                state=types.SimpleNamespace(maestro_remote=True),
                client=types.SimpleNamespace(host="192.168.1.50"),
                headers={},
            ),
            types.SimpleNamespace(
                state=types.SimpleNamespace(maestro_remote=True),
                client=types.SimpleNamespace(host="127.0.0.1"),
                headers={"cf-ray": "edge-request"},
            ),
        )
        for request in requests:
            with self.subTest(host=request.client.host):
                namespace, _registry, updater, readiness_calls = (
                    _catalog_namespace(remote=True)
                )
                response = namespace["list_models"](request)

                self.assertEqual(
                    [item["model_type"] for item in response["models"]],
                    ["ordinary_model"],
                )
                self.assertEqual(updater.calls, [
                    ("model", "ordinary_model"),
                    ("components", "ordinary_model"),
                ])
                self.assertEqual(readiness_calls, ["ordinary_model"])

    def test_missing_or_corrupt_runtime_stays_unavailable_without_status_work(self):
        helper = _launch_namespace("_music3_virtual_catalog_model")[
            "_music3_virtual_catalog_model"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            marker = (
                Path(temporary)
                / "runtime/maestro/minimax-music3/state/current.json"
            )
            marker.parent.mkdir(parents=True)
            marker.write_text("{corrupt", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PINOKIO_HOME": temporary}), \
                    mock.patch.object(
                        runtime,
                        "verify_music3_runtime",
                        side_effect=AssertionError("catalog must not verify"),
                    ), mock.patch.object(
                        runtime,
                        "music3_runtime_status",
                        side_effect=AssertionError("catalog must not probe"),
                    ):
                music3 = helper()

        self.assertFalse(music3["is_downloaded"])
        self.assertFalse(music3["execution_allowed"])
        self.assertFalse(music3["local_experiment"]["runtime_attested"])
        self.assertEqual(
            music3["availability_status"],
            "local_runtime_attestation_required",
        )

    def test_projection_has_no_enable_download_execution_or_default_authority(self):
        namespace = _launch_namespace(
            "_music3_virtual_catalog_model",
            "_normalize_model_visibility_ids",
        )
        music3 = namespace["_music3_virtual_catalog_model"]()

        for field in (
            "is_downloaded",
            "downloadable",
            "manual_installation_ready",
            "execution_allowed",
            "enabled",
            "default",
            "automatic_routing",
            "verified",
        ):
            self.assertIs(music3[field], False, field)
        self.assertEqual(music3["supported_operations"], [])
        self.assertEqual(music3["default_for_operations"], [])
        self.assertNotIn("manual_installation", music3)
        self.assertEqual(
            namespace["_normalize_model_visibility_ids"]([
                "ordinary_model", "minimax_music3", "ordinary_model",
            ]),
            ["ordinary_model"],
        )

        serialized = json.dumps(music3, sort_keys=True)
        for private_fragment in (
            str(ROOT),
            "/media/",
            "/home/",
            "PINOKIO_HOME",
            "api_key",
            "token=",
        ):
            self.assertNotIn(private_fragment, serialized)

    def test_reserved_id_is_rejected_by_generic_model_authority(self):
        namespace = _launch_namespace(
            "_require_remote_visible_models",
            "_require_remote_visible_job_models",
            "_director_recovery_runtime_admission",
            "_require_job_runtime_model_admission",
            "_download_model_files",
            "debug_model",
            "delete_model",
            "verify_manual_checkpoint",
        )
        authority_calls: list[str] = []

        class _ExplosiveRegistry:
            def get_model_def(self, _model_type):
                authority_calls.append("wgp")
                raise AssertionError("reserved ID reached WGP")

        class _JSONResponse:
            def __init__(self, content, *, status_code=200):
                self.content = content
                self.status_code = status_code

        namespace.update({
            "HTTPException": _HTTPException,
            "JSONResponse": _JSONResponse,
            "ModelDownloadUnavailableError": RuntimeError,
            "wgp": _ExplosiveRegistry(),
            "_remote_visible_model_ids": lambda _request: None,
            "_h3_job_model_types": lambda _job: ("minimax_music3",),
            "_request_is_cloudflare_remote": lambda _request: False,
            "_runtime_share_registration_is_local": lambda _request: True,
            "_require_h3_legal_execution": (
                lambda _models: authority_calls.append("legal")
            ),
        })
        local = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )

        for operation in (
            lambda: namespace["_require_remote_visible_models"](
                local, ["minimax_music3"],
            ),
            lambda: namespace["_require_remote_visible_job_models"]({
                "source_remote": False,
                "model_type": "minimax_music3",
                "params": {},
            }),
            lambda: namespace["_require_job_runtime_model_admission"]({
                "model_type": "minimax_music3",
                "params": {},
            }),
            lambda: namespace["_director_recovery_runtime_admission"]({
                "model_type": "minimax_music3",
            }),
            lambda: namespace["verify_manual_checkpoint"](
                "minimax_music3", local,
            ),
        ):
            with self.assertRaises(_HTTPException) as raised:
                operation()
            self.assertEqual(raised.exception.status_code, 404)

        with self.assertRaisesRegex(RuntimeError, "not a WGP checkpoint"):
            namespace["_download_model_files"]("minimax_music3")
        self.assertEqual(
            namespace["debug_model"]("minimax_music3"),
            {"error": "Model not found"},
        )
        deleted = namespace["delete_model"]("minimax_music3")
        self.assertEqual(deleted.status_code, 404)
        self.assertEqual(deleted.content, {"error": "Model not found"})
        self.assertEqual(authority_calls, [])

    def test_background_updater_skips_reserved_wgp_collision(self):
        update_loop = _launch_namespace("_versioned_model_update_loop")[
            "_versioned_model_update_loop"
        ]
        events: list[str] = []

        class _Stop:
            stopped = False

            def is_set(self):
                return self.stopped

            def wait(self, timeout):
                if timeout == 30:
                    return False
                if timeout == 3600:
                    self.stopped = True
                    return True
                raise AssertionError(f"unexpected wait: {timeout}")

        class _Lock:
            def acquire(self, *, blocking):
                self.blocking = blocking
                return True

            def release(self):
                events.append("release")

        class _ExplosiveWgp:
            displayed_model_types = ("minimax_music3",)

            def get_model_def(self, _model_type):
                raise AssertionError("reserved ID reached updater WGP")

        namespace = update_loop.__globals__
        namespace.update({
            "_startup_recovery_finished": types.SimpleNamespace(
                wait=lambda _timeout: True,
            ),
            "_startup_recovery_state_value": lambda: "ready",
            "_model_update_stop": _Stop(),
            "_h3_skill_catalog_updater": types.SimpleNamespace(
                refresh=lambda: events.append("refresh"),
            ),
            "_gen_lock": _Lock(),
            "wgp": _ExplosiveWgp(),
            "_versioned_model_updater": types.SimpleNamespace(
                apply_recorded=lambda *_args: events.append("update"),
                apply_recorded_components=lambda *_args: events.append(
                    "components",
                ),
            ),
        })

        update_loop()
        self.assertEqual(events, ["refresh", "release"])

    def test_director_music_rejects_before_wgp_or_durable_registration(self):
        tree = ast.parse(
            LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH),
        )
        function = next(
            item for item in tree.body
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == "director_generate_music"
        )
        source = ast.get_source_segment(
            LAUNCH.read_text(encoding="utf-8"), function,
        )
        first_admission = source.index(
            "_require_remote_visible_models(request, [model_type])",
        )
        self.assertLess(first_admission, source.index("wgp.get_model_def"))
        self.assertLess(first_admission, source.index(
            "_register_director_preparation",
        ))

    def test_ordinary_catalog_projection_is_unchanged(self):
        namespace, _registry, _updater, _readiness = _catalog_namespace(
            remote=False,
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        with_music3 = namespace["list_models"](request)
        music3_factory = namespace.pop("_music3_virtual_catalog_model")
        try:
            without_music3 = namespace["list_models"](request)
        finally:
            namespace["_music3_virtual_catalog_model"] = music3_factory

        ordinary_with = next(
            item for item in with_music3["models"]
            if item["model_type"] == "ordinary_model"
        )
        self.assertEqual(without_music3["models"], [ordinary_with])
        self.assertEqual(without_music3["families"], with_music3["families"])


if __name__ == "__main__":
    unittest.main()
