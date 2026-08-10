"""Offline security contracts for Pinokio remote access."""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import importlib.util
import ipaddress
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
LAUNCH_PATH = ROOT / "app" / "launch.py"
APP_PATH = ROOT / "ui" / "src" / "App.tsx"
CLIENT_PATH = ROOT / "ui" / "src" / "api" / "client.ts"


class _Response:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class _Request:
    def __init__(
        self,
        method="POST",
        base_url="http://127.0.0.1:7860/",
        path="/",
        json_body=None,
        **headers,
    ):
        client_host = headers.pop("client_host", "127.0.0.1")
        self.method = method
        self.base_url = base_url
        self.headers = {key.replace("_", "-"): value for key, value in headers.items()}
        self.url = types.SimpleNamespace(scheme=urlsplit(base_url).scheme, path=path)
        self.client = types.SimpleNamespace(host=client_host)
        self._json_body = json_body or {}

    async def json(self):
        return self._json_body


def _security_namespace():
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    names = {
        "_env_flag_enabled",
        "_remote_sharing_enabled",
        "_cloudflare_origin_has_suffix",
        "_is_quick_tunnel_origin",
        "_is_workers_dev_origin",
        "_runtime_share_registration",
        "_request_is_cloudflare_remote",
        "_first_forwarded_value",
        "_canonical_http_origin",
        "_approved_local_origin",
        "_request_external_origins",
        "_matches_verified_stable_redirect_origin",
        "_request_is_https",
        "_reject_cross_origin_mutation",
        "_remote_local_only_denial",
        "_local_recovery_control_denial",
        "_is_loopback_request_client",
        "_runtime_share_registration_is_local",
        "get_access_context",
        "register_runtime_share_url",
    }
    body = []
    for node in tree.body:
        selected = (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name)
                and target.id in {
                    "_STATE_CHANGING_METHODS", "_TRUE_ENV_VALUES",
                    "_REMOTE_LOCAL_ONLY_PREFIXES", "_REMOTE_LOCAL_ONLY_EXACT",
                }
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        ) or (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        )
        if selected:
            selected_node = copy.deepcopy(node)
            if isinstance(selected_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                selected_node.decorator_list = []
            body.append(selected_node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)

    class FakeHTTPException(Exception):
        def __init__(self, *, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    namespace = {
        "os": os,
        "ipaddress": ipaddress,
        "threading": threading,
        "urlsplit": urlsplit,
        "Request": object,
        "JSONResponse": _Response,
        "HTTPException": FakeHTTPException,
        "_runtime_share_url_lock": threading.Lock(),
        "_runtime_share_url": "",
        "_runtime_share_quick_tunnel_url": "",
        "_runtime_share_stable_verified": False,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    namespace["FakeHTTPException"] = FakeHTTPException
    return namespace


def _turbo_benchmark_namespace():
    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    names = {
        "_is_loopback_request_client",
        "_authorize_h3_turbo_benchmark_request",
        "_materialize_h3_turbo_validation_reference",
        "_restore_h3_turbo_validation_reference",
    }
    constants = {
        "_H3_TURBO_BENCHMARK_HEADER",
        "_H3_TURBO_BENCHMARK_VERSION",
        "_H3_TURBO_BENCHMARK_PROMPT_SHA256",
        "_H3_TURBO_BENCHMARK_REFERENCE_SHA256",
        "_H3_TURBO_BENCHMARK_REFERENCE_BYTES",
        "_H3_TURBO_BENCHMARK_BODY_KEYS",
    }
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants
            for target in node.targets
        ):
            body.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)

    class FakeHTTPException(Exception):
        def __init__(self, *, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    namespace = {
        "Request": object,
        "HTTPException": FakeHTTPException,
        "hashlib": hashlib,
        "ipaddress": ipaddress,
        "os": os,
    }
    exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
    namespace["FakeHTTPException"] = FakeHTTPException
    return namespace


class OriginPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.security = _security_namespace()

    def setUp(self):
        with self.security["_runtime_share_url_lock"]:
            self.security["_runtime_share_url"] = ""
            self.security["_runtime_share_quick_tunnel_url"] = ""
            self.security["_runtime_share_stable_verified"] = False

    def _register_runtime_share(
        self,
        *,
        stable="https://maestro.account.workers.dev",
        quick="https://current-tunnel.trycloudflare.com",
        stable_verified=True,
    ):
        request = _Request(
            method="PUT",
            origin="http://127.0.0.1:7860",
            json_body={
                "share_url": stable,
                "quick_tunnel_url": quick,
                "stable_verified": stable_verified,
            },
        )
        return asyncio.run(self.security["register_runtime_share_url"](request))

    def test_origin_parser_rejects_null_file_credentials_and_evil_subdomains(self):
        canonical = self.security["_canonical_http_origin"]
        approved = self.security["_approved_local_origin"]

        self.assertIsNone(canonical("null"))
        self.assertIsNone(canonical("file:///tmp/index.html", allow_path=True))
        self.assertIsNone(canonical("https://user@example.com"))
        self.assertEqual(
            canonical("https://localhost.evil.example"),
            "https://localhost.evil.example",
        )
        self.assertTrue(approved("https://7860.localhost"))
        self.assertTrue(approved("https://maestro.localhost"))
        self.assertFalse(approved("https://evil.maestro.localhost"))
        self.assertFalse(approved("https://localhost.evil.example"))

    def test_forwarded_cloudflare_same_origin_is_allowed(self):
        reject = self.security["_reject_cross_origin_mutation"]
        request = _Request(
            origin="https://random.trycloudflare.com",
            x_forwarded_proto="https",
            x_forwarded_host="random.trycloudflare.com",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertIsNone(reject(request))

    def test_verified_stable_origin_is_accepted_on_its_registered_quick_target(self):
        reject = self.security["_reject_cross_origin_mutation"]
        classify = self.security["_request_is_cloudflare_remote"]
        stable = "https://maestro.account.workers.dev"
        quick = "https://current-tunnel.trycloudflare.com"
        request = _Request(
            base_url=quick + "/",
            origin=stable,
            x_forwarded_proto="https",
            x_forwarded_host="current-tunnel.trycloudflare.com",
            cf_ray="abc123-DEN",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            result = self._register_runtime_share(stable=stable, quick=quick)
            self.assertEqual(result, {"status": "ok", "share_url": stable})
            self.assertTrue(classify(request))
            self.assertIsNone(reject(request))

    def test_verified_stable_proxy_is_remote_without_machine_controls_and_keeps_csrf_pair(self):
        reject = self.security["_reject_cross_origin_mutation"]
        classify = self.security["_request_is_cloudflare_remote"]
        access_context = self.security["get_access_context"]
        stable = "https://maestro.account.workers.dev"
        quick = "https://current-tunnel.trycloudflare.com"
        request = _Request(
            base_url=quick + "/",
            origin=stable,
            x_forwarded_proto="https",
            x_forwarded_host="current-tunnel.trycloudflare.com",
            cf_ray="abc123-DEN",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self._register_runtime_share(stable=stable, quick=quick)
            remote = classify(request)
            request.state = types.SimpleNamespace(maestro_remote=remote)
            context = access_context(request)
            self.assertTrue(remote)
            self.assertIsNone(reject(request))

        self.assertTrue(context["remote"])
        self.assertTrue(context["project_password_required"])
        self.assertFalse(context["machine_controls"])
        self.assertFalse(context["custom_model_sources"])
        self.assertFalse(context["classic_ui"])
        self.assertEqual(context["share_url"], "")

    def test_other_workers_dev_origin_is_rejected_on_registered_quick_target(self):
        reject = self.security["_reject_cross_origin_mutation"]
        quick = "https://current-tunnel.trycloudflare.com"
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self._register_runtime_share(quick=quick)
            response = reject(_Request(
                base_url=quick + "/",
                origin="https://other.account.workers.dev",
                x_forwarded_proto="https",
                x_forwarded_host="current-tunnel.trycloudflare.com",
            ))
        self.assertEqual(response.status_code, 403)

    def test_stable_origin_is_rejected_when_registration_is_missing_or_stale(self):
        reject = self.security["_reject_cross_origin_mutation"]
        stable = "https://maestro.account.workers.dev"

        def redirected_request(host):
            return _Request(
                base_url=f"https://{host}/",
                origin=stable,
                x_forwarded_proto="https",
                x_forwarded_host=host,
            )

        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertEqual(
                reject(redirected_request("current-tunnel.trycloudflare.com")).status_code,
                403,
            )
            self._register_runtime_share(
                stable=stable,
                quick="https://old-tunnel.trycloudflare.com",
            )
            self.assertEqual(
                reject(redirected_request("current-tunnel.trycloudflare.com")).status_code,
                403,
            )

    def test_direct_quick_tunnel_origin_remains_same_origin(self):
        reject = self.security["_reject_cross_origin_mutation"]
        quick = "https://current-tunnel.trycloudflare.com"
        request = _Request(
            base_url=quick + "/",
            origin=quick,
            x_forwarded_proto="https",
            x_forwarded_host="current-tunnel.trycloudflare.com",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertIsNone(reject(request))

    def test_unverified_stable_registration_is_rejected(self):
        error = self.security["FakeHTTPException"]
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            with self.assertRaises(error) as raised:
                self._register_runtime_share(stable_verified=False)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.security["_runtime_share_registration"](), ("", "", False))

    def test_loopback_origin_proof_allows_local_share_registration(self):
        reject = self.security["_reject_cross_origin_mutation"]
        request = _Request(origin="http://127.0.0.1:7860")
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertIsNone(reject(request))

    def test_cross_origin_and_conflicting_referer_are_rejected(self):
        reject = self.security["_reject_cross_origin_mutation"]
        evil = reject(_Request(origin="https://maestro.evil.example"))
        null = reject(_Request(origin="null"))
        conflict = reject(_Request(
            origin="http://127.0.0.1:7860",
            referer="https://evil.example/form",
        ))

        self.assertEqual(evil.status_code, 403)
        self.assertEqual(null.status_code, 403)
        self.assertEqual(conflict.status_code, 403)

    def test_remote_request_does_not_trust_an_unrelated_localhost_origin(self):
        reject = self.security["_reject_cross_origin_mutation"]
        request = _Request(
            base_url="http://random.trycloudflare.com/",
            origin="http://localhost:5173",
            x_forwarded_proto="https",
            x_forwarded_host="random.trycloudflare.com",
        )
        self.assertEqual(reject(request).status_code, 403)

    def test_headerless_clients_are_local_only_when_remote_sharing_is_enabled(self):
        reject = self.security["_reject_cross_origin_mutation"]
        disabled = {
            "PINOKIO_SHARE_CLOUDFLARE": "false",
            "PINOKIO_SHARE_LOCAL": "false",
        }
        with patch.dict(os.environ, disabled, clear=False):
            self.assertIsNone(reject(_Request()))
        with patch.dict(os.environ, {
            "PINOKIO_SHARE_CLOUDFLARE": "false",
            "PINOKIO_SHARE_LOCAL": "true",
        }, clear=False):
            self.assertEqual(reject(_Request()).status_code, 403)

    def test_runtime_share_registration_rejects_other_localhost_apps(self):
        allowed = self.security["_runtime_share_registration_is_local"]
        self.assertTrue(allowed(_Request(origin="http://127.0.0.1:7860")))
        self.assertFalse(allowed(_Request(origin="https://evil.localhost")))
        self.assertFalse(allowed(_Request(
            origin="http://127.0.0.1:7860", client_host="192.0.2.1",
        )))

    def test_forwarded_https_marks_session_cookie_secure(self):
        is_https = self.security["_request_is_https"]

        self.assertTrue(is_https(_Request(x_forwarded_proto="https")))
        self.assertTrue(is_https(_Request(forwarded='for=1.2.3.4;proto="https"')))
        self.assertFalse(is_https(_Request()))

    def test_cloudflare_remote_classification_only_activates_in_opt_in_mode(self):
        classify = self.security["_request_is_cloudflare_remote"]
        request = _Request(
            base_url="https://random.trycloudflare.com/",
            x_forwarded_proto="https",
            x_forwarded_host="random.trycloudflare.com",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "false"}, clear=False):
            self.assertFalse(classify(request))
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertTrue(classify(request))
            self.assertFalse(classify(_Request()))

    def test_cloudflare_marker_fails_closed_even_when_proxy_rewrites_host_local(self):
        classify = self.security["_request_is_cloudflare_remote"]
        rewritten = _Request(
            base_url="http://127.0.0.1:7860/",
            x_forwarded_proto="http",
            x_forwarded_host="127.0.0.1:7860",
            cf_ray="abc123-DEN",
            cf_connecting_ip="203.0.113.7",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertTrue(classify(rewritten))

    def test_lan_peer_is_remote_and_cannot_gain_local_admin_bypass(self):
        classify = self.security["_request_is_cloudflare_remote"]
        deny = self.security["_remote_local_only_denial"]
        request = _Request(
            path="/api/v1/system-config",
            base_url="http://192.168.1.20:7860/",
            origin="http://192.168.1.20:7860",
            client_host="192.168.1.44",
        )
        with patch.dict(os.environ, {
            "PINOKIO_SHARE_CLOUDFLARE": "false",
            "PINOKIO_SHARE_LOCAL": "true",
        }, clear=False):
            self.assertTrue(classify(request))
            self.assertEqual(deny(request).status_code, 403)

    def test_local_recovery_requires_direct_loopback_exact_origin(self):
        deny = self.security["_local_recovery_control_denial"]
        local = _Request(
            method="GET",
            path="/api/v1/local-recovery/h3/discovery",
            origin="http://127.0.0.1:7860",
        )
        self.assertIsNone(deny(local))
        self.assertEqual(deny(_Request(
            method="GET",
            path="/api/v1/local-recovery/h3/discovery",
        )).status_code, 403)
        self.assertEqual(deny(_Request(
            path="/api/v1/local-recovery/h3/job/start",
            origin="http://127.0.0.1:7860",
            client_host="192.0.2.9",
        )).status_code, 403)

        rewritten_tunnel = _Request(
            path="/api/v1/local-recovery/h3/job/start",
            origin="http://127.0.0.1:7860",
            cf_ray="recovery-DEN",
            cf_connecting_ip="203.0.113.9",
        )
        with patch.dict(
            os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False,
        ):
            self.assertEqual(deny(rewritten_tunnel).status_code, 403)
            self.assertEqual(
                self.security["_remote_local_only_denial"](
                    rewritten_tunnel,
                ).status_code,
                403,
            )

    def test_remote_control_plane_denies_custom_sources_but_allows_catalog_download(self):
        deny = self.security["_remote_local_only_denial"]
        remote_headers = {
            "x_forwarded_proto": "https",
            "x_forwarded_host": "random.trycloudflare.com",
        }
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self.assertEqual(deny(_Request(
                path="/api/v1/huggingface/import-lora", **remote_headers,
            )).status_code, 403)
            self.assertEqual(deny(_Request(
                path="/api/v1/llm/load", **remote_headers,
            )).status_code, 403)
            self.assertEqual(deny(_Request(
                path="/api/v1/llm/unload", **remote_headers,
            )).status_code, 403)
            self.assertEqual(deny(_Request(
                path="/api/v1/llm/refusal-literals", **remote_headers,
            )).status_code, 403)
            self.assertEqual(deny(_Request(
                path="/api/v1/llm/stream-status", method="GET", **remote_headers,
            )).status_code, 403)
            self.assertIsNone(deny(_Request(
                path="/api/v1/llm/models", method="GET", **remote_headers,
            )))
            self.assertIsNone(deny(_Request(
                path="/api/v1/llm/chat", **remote_headers,
            )))
            self.assertEqual(deny(_Request(
                path="/classic/", method="GET", **remote_headers,
            )).status_code, 403)
            self.assertEqual(deny(_Request(
                path="/api/v1/downloads/active", method="GET", **remote_headers,
            )).status_code, 403)
            self.assertIsNone(deny(_Request(
                path="/api/v1/blender/status", method="GET", **remote_headers,
            )))
            self.assertEqual(deny(_Request(
                path="/api/v1/queue/owned-job/start-next", **remote_headers,
            )).status_code, 403)
            self.assertIsNone(deny(_Request(
                path="/api/v1/models/minimax_h3/download", **remote_headers,
            )))


class LaunchSecurityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = LAUNCH_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(LAUNCH_PATH))

    def _function_source(self, name):
        node = next(
            item for item in self.tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        return ast.get_source_segment(self.source, node)

    def _function_namespace(self, names, namespace):
        nodes = []
        for name in names:
            node = next(
                item for item in self.tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            )
            selected = copy.deepcopy(node)
            selected.decorator_list = []
            nodes.append(selected)
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        return namespace

    def test_director_pipeline_start_route_registers_without_runtime_imports(self):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name == "director_pipeline_start"
        )

        class RouteRecorder:
            def __init__(self):
                self.routes = {}

            def post(self, path, **_kwargs):
                def register(function):
                    self.routes[path] = function
                    return function
                return register

        recorder = RouteRecorder()
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"api": recorder, "Request": object}
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)

        self.assertIn("/api/v1/director/pipeline/start", recorder.routes)
        self.assertEqual(
            recorder.routes["/api/v1/director/pipeline/start"].__name__,
            "director_pipeline_start",
        )

    def test_ui_bootstraps_one_session_before_mounting_pollers(self):
        app = APP_PATH.read_text(encoding="utf-8")
        client = CLIENT_PATH.read_text(encoding="utf-8")

        gate = app.index("if (bootstrapState !== 'ready')")
        self.assertLess(gate, app.index("<Sidebar />"))
        self.assertLess(gate, app.index("<MainContent />"))
        self.assertIn("let accessContextRequest: Promise<AccessContext> | null", client)
        self.assertIn("if (accessContextRequest) return accessContextRequest", client)
        self.assertIn("credentials: 'same-origin'", client)

    def test_director_client_surfaces_route_project_and_media_failures(self):
        client = CLIENT_PATH.read_text(encoding="utf-8")
        start = client.index("export async function startPipeline")
        end = client.index("export async function fetchPipelineStatus", start)
        implementation = client[start:end]

        self.assertIn("Director is not available in the running Maestro backend", implementation)
        self.assertIn("Director access was denied", implementation)
        self.assertIn("Unlock the selected Director project first", implementation)
        self.assertIn("Director could not access a selected reference", implementation)
        self.assertIn("payload.detail", implementation)

    def test_generation_destinations_require_unlocked_project(self):
        ordinary = self._function_source("generate")
        music = self._function_source("director_generate_music")
        director = self._function_source("director_pipeline_start")

        self.assertIn("job_out_dir = _require_project_access(request, workspace)", ordinary)
        self.assertIn("out_dir = _require_project_access(request, workspace)", music)
        self.assertIn("_authorize_director_media_inputs(request, body)", director)
        self.assertIn('body["workspace"] = workspace', director)

    def test_generate_authorizes_all_attachment_shapes_before_probing(self):
        generate = self._function_source("generate")
        plan = self._function_source("preview_generation_plan")
        planner = self._function_source("_plan_generation_submission")
        reject_at = generate.index("_reject_client_h3_internal_state")
        authorize_at = generate.index("_authorize_generation_media_inputs")
        planner_at = generate.index("_plan_generation_submission")

        self.assertLess(reject_at, authorize_at)
        self.assertLess(authorize_at, planner_at)
        self.assertIn("_validate_h3_explicit_multiclip_request", planner)
        self.assertLess(
            plan.index("_reject_client_h3_internal_state"),
            plan.index("_authorize_generation_media_inputs"),
        )
        self.assertIn("_GENERATION_MEDIA_INPUTS", self.source)
        for field in (
            "image_start", "image_end", "image_refs", "video_guide",
            "video_source", "video_end", "audio_guide", "audio_source",
            "image_guide", "video_mask", "custom_guide",
        ):
            self.assertIn(f'"{field}"', self.source)
        self.assertIn("_resolve_authorized_project_asset_media", self.source)

    def test_remote_nested_h3_worker_plan_cannot_inject_host_media(self):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_reject_client_h3_internal_state"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        namespace = {"HTTPException": FakeHTTPException}
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        reject = namespace["_reject_client_h3_internal_state"]
        malicious = {
            "model_type": "minimax_h3_ref2va",
            "_h3_longform": {
                "clip_count": 2,
                "original_image_start": "/etc/passwd",
                "segment_models": [
                    {"model_type": "minimax_h3_ref2va"},
                ],
            },
        }
        with self.assertRaises(FakeHTTPException) as raised:
            reject(malicious)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("_h3_longform", raised.exception.detail)

    def test_turbo_validation_override_is_server_owned_and_not_echoed(self):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_reject_client_h3_turbo_validation_controls"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        namespace = {
            "HTTPException": FakeHTTPException,
        }
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        reject = namespace["_reject_client_h3_turbo_validation_controls"]
        for hidden_key in (
            "h3_turbo_validation_mode",
            "_h3_turbo_validation_authorized",
        ):
            body = {
                "custom_settings": {
                    "h3_turbo_profile": "h3_turbo_v4",
                    hidden_key: "synthetic_ref2va",
                },
            }
            with self.subTest(hidden_key=hidden_key):
                with self.assertRaises(FakeHTTPException) as raised:
                    reject(body)
                self.assertEqual(raised.exception.status_code, 400)
                self.assertNotIn(hidden_key, raised.exception.detail)

        internal_node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_reject_client_h3_internal_state"
        )
        internal_module = ast.Module(body=[internal_node], type_ignores=[])
        ast.fix_missing_locations(internal_module)
        internal_namespace = {"HTTPException": FakeHTTPException}
        exec(compile(internal_module, str(LAUNCH_PATH), "exec"), internal_namespace)
        with self.assertRaises(FakeHTTPException) as raised:
            internal_namespace["_reject_client_h3_internal_state"]({
                "_h3_turbo_validation_authorized": True,
            })
        self.assertEqual(raised.exception.status_code, 400)

        generate = self._function_source("generate")
        plan = self._function_source("preview_generation_plan")
        planner = self._function_source("_plan_generation_submission")
        for source in (generate, plan):
            guard_at = source.index("_reject_client_h3_turbo_validation_controls")
            planner_at = source.index("_plan_generation_submission")
            self.assertLess(guard_at, planner_at)
        self.assertIn("_validate_h3_sampling_steps", planner)

    def test_ref2va_turbo_benchmark_capability_is_fixed_local_and_content_pinned(self):
        namespace = _turbo_benchmark_namespace()
        authorize = namespace["_authorize_h3_turbo_benchmark_request"]
        error_type = namespace["FakeHTTPException"]
        runner_path = ROOT / "app" / "scripts" / "benchmark_h3_profiles.py"
        spec = importlib.util.spec_from_file_location(
            "_remote_access_benchmark_fixture", runner_path,
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)

        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "procedural.png"
            reference.write_bytes(runner.procedural_reference_png())
            body = runner.build_generation_payload(
                runner.build_matrix(["ref2va_turbo_4_sdpa"])[0],
                project="synthetic-project",
                seed=7,
                reference_path=str(reference),
            )
            body.pop("workspace")

            def request(*, remote=False, host="127.0.0.1", version="synthetic-v1"):
                return types.SimpleNamespace(
                    headers={"X-Maestro-H3-Benchmark": version},
                    state=types.SimpleNamespace(maestro_remote=remote),
                    client=types.SimpleNamespace(host=host),
                )

            captured_reference = authorize(request(), dict(body))
            self.assertIsInstance(captured_reference, bytes)
            for denied in (
                request(remote=True),
                request(host="192.0.2.10"),
            ):
                with self.subTest(denied=denied), self.assertRaises(error_type) as raised:
                    authorize(denied, dict(body))
                self.assertEqual(raised.exception.status_code, 403)

            mutations = (
                {"resolution": "864x480"},
                {"num_inference_steps": 6},
                {"repeat_generation": 2},
                {"private_output": False},
                {"video_prompt_type": ""},
                {"activated_loras": ["user.safetensors"]},
                {"tea_cache": 1},
                {"prompt": "different synthetic prompt"},
                {"image_refs": [str(reference), str(reference)]},
                {"multi_prompts_gen_type": 3, "per_clip_prompts": ["a", "b"]},
                {"per_clip_frames": [345, 345]},
                {"_duration_seconds": 30},
                {"trim_tail_frames": 17},
                {"force_fps": "12"},
                {"prompt_enhancer": "rewrite"},
                {"temporal_upsampling": "rife"},
                {"spatial_upsampling": "2x"},
            )
            for mutation in mutations:
                candidate = {**body, **mutation}
                with self.subTest(mutation=mutation), self.assertRaises(error_type) as raised:
                    authorize(request(), candidate)
                self.assertEqual(raised.exception.status_code, 400)

            headerless = request()
            headerless.headers = {}
            self.assertFalse(authorize(headerless, dict(body)))

            reference.unlink()
            task_params = {"image_refs": [str(reference)]}
            restoration = namespace[
                "_materialize_h3_turbo_validation_reference"
            ](captured_reference, task_params)
            self.assertEqual(len(task_params["image_refs"]), 1)
            self.assertFalse(isinstance(task_params["image_refs"][0], str))
            namespace["_restore_h3_turbo_validation_reference"](
                task_params, restoration,
            )
            self.assertEqual(task_params["image_refs"], [str(reference)])
            self.assertNotIn("_h3_turbo_validation_authorized", task_params)
            self.assertNotIn("_h3_turbo_validation_reference_bytes", task_params)

        generate = self._function_source("generate")
        self.assertLess(
            generate.index("_authorize_generation_media_inputs"),
            generate.index("_authorize_h3_turbo_benchmark_request"),
        )
        self.assertNotIn(
            "_h3_turbo_validation_authorized",
            self._function_source("get_status"),
        )
        self.assertNotIn(
            "_h3_turbo_validation_authorized",
            self._function_source("list_jobs"),
        )
        self.assertNotIn(
            "_h3_turbo_validation_reference_bytes",
            self._function_source("get_status"),
        )
        self.assertNotIn(
            "_h3_turbo_validation_reference_bytes",
            self._function_source("list_jobs"),
        )
        output_count = self._function_source("set_job_output_count")
        self.assertIn("_h3_turbo_validation_authorized", output_count)
        self.assertLess(
            output_count.index("_h3_turbo_validation_authorized"),
            output_count.index("update_requested_outputs"),
        )

    def test_existing_workspace_cannot_be_recreated_to_reset_its_lock(self):
        create = self._function_source("create_workspace")

        self.assertIn("if os.path.exists(existing):", create)
        self.assertIn("_require_project_access(request, name)", create)
        self.assertIn("status_code=409", create)
        self.assertLess(
            create.index("status_code=409"),
            create.index("_project_access.set_password"),
        )

    def test_derived_outputs_keep_authorization_metadata(self):
        mix = self._function_source("mix_audio")
        rejoin = self._function_source("rejoin_clips")
        move = self._function_source("move_output")

        self.assertIn("stamp_sidecar_policy(sidecar, policy", mix)
        self.assertIn("os.replace(sidecar_temp, sidecar_path)", mix)
        self.assertIn("_resolve_authorized_request_media", rejoin)
        self.assertNotIn("os.path.isfile(ag)", rejoin)
        self.assertIn("_move_lineage_files(", move)
        self.assertNotIn("shutil.move", move)

    def test_cors_is_credentialed_without_wildcard_origin(self):
        self.assertIn("allow_credentials=True", self.source)
        self.assertIn("allow_origin_regex=_cors_origin_regex", self.source)
        self.assertNotIn('allow_origins=["*"]', self.source)

    def test_public_health_is_minimal_and_cookie_free(self):
        health = self._function_source("public_health")
        self.assertIn('return {"status": "ok"}', health)
        self.assertIn('request.url.path == "/health"', self.source)

    def test_remote_projects_are_password_gated_and_do_not_change_global_active_project(self):
        require = self._function_source("_require_project_access")
        listing = self._function_source("list_workspaces_endpoint")
        switch = self._function_source("set_active_workspace")
        create = self._function_source("create_workspace")
        unlock = self._function_source("unlock_workspace")

        self.assertIn("Remote access requires a password-protected project", require)
        self.assertIn('if not remote or entry["unlocked"]', listing)
        self.assertIn("_remote_active_projects", switch)
        self.assertLess(switch.index("_remote_active_projects"), switch.index("_persist_active_workspace"))
        self.assertIn("Remote projects require a password", create)
        self.assertIn("_existing_workspace_dir(name)", unlock)
        self.assertIn("not enabled for remote access", unlock)
        self.assertNotIn("_remote_active_projects", unlock)

    def test_remote_default_workspace_is_hidden_and_cannot_be_selected(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=True,
            maestro_session_id="remote-session",
        ))
        project_access = types.SimpleNamespace(status=lambda *_args: (
            types.SimpleNamespace(protected=True, unlocked=True)
        ))
        namespace = self._function_namespace(
            (
                "_workspace_access_fields", "list_workspaces_endpoint",
                "_request_project_workspace",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_list_workspaces": lambda: [
                    {"name": "default", "path": "/outputs", "file_count": 8},
                    {"name": "x_test", "path": "/outputs/x_test", "file_count": 2},
                ],
                "_project_access": project_access,
                "_remote_active_projects": {"remote-session": "default"},
                "_remote_active_projects_lock": threading.RLock(),
                "_get_active_workspace": lambda: "default",
            },
        )
        listed = namespace["list_workspaces_endpoint"](request)
        self.assertEqual(
            [item["name"] for item in listed["workspaces"]], ["x_test"],
        )
        self.assertEqual(listed["active"], "")
        for selected in ("", "default"):
            with self.subTest(selected=selected):
                with self.assertRaises(FakeHTTPException) as raised:
                    namespace["_request_project_workspace"](request, selected)
                self.assertIn(raised.exception.status_code, {400, 404})

        require_source = self._function_source("_require_project_access")
        unlock_source = self._function_source("unlock_workspace")
        self.assertLess(
            require_source.index('workspace == "default"'),
            require_source.index("_existing_workspace_dir(workspace)"),
        )
        self.assertLess(
            unlock_source.index('name == "default"'),
            unlock_source.index("_existing_workspace_dir(name)"),
        )

    def test_remote_mutations_require_an_unlocked_protected_project(self):
        node = next(
            item for item in self.tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_require_remote_project_mutation_access"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)

        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code

        state = types.SimpleNamespace(
            maestro_session_id="remote-session", maestro_remote=True,
        )
        request = types.SimpleNamespace(state=state)
        project_access = types.SimpleNamespace()
        namespace = {
            "Request": object,
            "HTTPException": FakeHTTPException,
            "_project_access": project_access,
        }
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        guard = namespace["_require_remote_project_mutation_access"]

        for protected, unlocked in ((False, True), (True, False), (False, False)):
            project_access.authorize = lambda *_args, p=protected, u=unlocked: types.SimpleNamespace(
                protected=p, unlocked=u,
            )
            with self.assertRaises(FakeHTTPException) as raised:
                guard(request, "project", "/outputs/project")
            self.assertEqual(raised.exception.status_code, 423)

        project_access.authorize = lambda *_args: types.SimpleNamespace(
            protected=True, unlocked=True,
        )
        allowed = guard(request, "project", "/outputs/project")
        self.assertTrue(allowed.protected)
        self.assertTrue(allowed.unlocked)

        password = self._function_source("set_workspace_password")
        delete = self._function_source("delete_workspace")
        self.assertIn("_require_remote_project_mutation_access", password)
        self.assertIn("_existing_workspace_dir(name) if remote", password)
        self.assertLess(
            password.index("_existing_workspace_dir(name) if remote"),
            password.index("_require_remote_project_mutation_access"),
        )
        self.assertIn("_require_remote_project_mutation_access", delete)
        self.assertLess(
            delete.index("_output_share_manager().revoke_workspace(name)"),
            delete.index("_project_asset_store().delete_project(name)"),
        )
        self.assertLess(
            delete.index("_output_share_manager().revoke_workspace(name)"),
            delete.index("safe_delete_dir(ws_dir)"),
        )

    def test_project_unlock_grants_are_class_scoped_revocable_and_poll_safe(self):
        require = self._function_source("_require_project_access")
        listing = self._function_source("list_workspaces_endpoint")
        unlock = self._function_source("unlock_workspace")
        lock_one = self._function_source("lock_workspace")
        lock_all = self._function_source("lock_all_workspaces")
        password = self._function_source("set_workspace_password")
        delete = self._function_source("delete_workspace")

        self.assertIn("remote,", require)
        self.assertIn("_project_access.authorize", require)
        self.assertIn("else _project_access.status", require)
        self.assertIn("remote,", listing)
        self.assertIn("_workspace_remember_policy(body)", unlock)
        self.assertIn("_workspace_access_fields(status)", unlock)
        self.assertIn("_project_access.lock(", lock_one)
        self.assertIn("_project_access.lock_all(", lock_all)
        self.assertIn("_workspace_remember_policy(body)", password)
        self.assertIn("_project_access.revoke_workspace(name)", delete)
        self.assertLess(
            delete.index("_project_access.revoke_workspace(name)"),
            delete.index("safe_delete_dir(ws_dir)"),
        )
        # CSRF remains centralized for every state-changing workspace route.
        self.assertIn('"POST", "PUT", "PATCH", "DELETE"', self.source)
        hard_remote_helpers = (
            "_recovered_job_remote_project_accessible",
            "_require_remote_queue_project",
        )
        for helper in hard_remote_helpers:
            source = self._function_source(helper)
            self.assertIn("session_id, True,", source, helper)
        request_class_helpers = (
            "_require_owned_job_project",
            "_require_h3_delivery_recovery_job",
        )
        for helper in request_class_helpers:
            source = self._function_source(helper)
            self.assertIn('maestro_remote", False)', source, helper)
            self.assertIn("remote,", source, helper)

    def test_remote_jobs_hide_sessionless_legacy_records(self):
        helper = self._function_source("_job_owned_by_request")
        for name in ("get_status", "cancel_job", "list_jobs", "_require_owned_job", "get_queue_state"):
            self.assertIn("_job_owned_by_request", self._function_source(name))
        self.assertIn("owner is None", helper)
        self.assertIn("maestro_remote", helper)

        queue = self._function_source("get_queue_state")
        self.assertIn("_require_remote_queue_project(request)", queue)
        self.assertIn("queue_scheduler_snapshot(active_jobs)", queue)
        self.assertIn('**scheduler["summary"]', queue)
        self.assertIn("aggregate_snapshot()", queue)
        self.assertLess(
            queue.index("_require_remote_queue_project(request)"),
            queue.index("queue_scheduler_snapshot(active_jobs)"),
        )
        self.assertLess(
            queue.index("queue_scheduler_snapshot(active_jobs)"),
            queue.index("_job_owned_by_request(snapshot, request)"),
        )

        gate = self._function_source("_require_remote_queue_project")
        self.assertIn("_remote_active_projects.get(session_id", gate)
        self.assertIn("_existing_workspace_dir(project)", gate)
        self.assertIn("access.protected", gate)
        self.assertIn("access.unlocked", gate)

    def test_remote_queue_requires_selected_unlocked_protected_project(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        source = LAUNCH_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAUNCH_PATH))
        helper = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_require_remote_queue_project"
        )
        active_projects = {}
        project_access = types.SimpleNamespace()
        namespace = {
            "Request": object,
            "HTTPException": FakeHTTPException,
            "_remote_active_projects": active_projects,
            "_remote_active_projects_lock": threading.RLock(),
            "_existing_workspace_dir": lambda project: f"/outputs/{project}",
            "_project_access": project_access,
        }
        module = ast.Module(body=[copy.deepcopy(helper)], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        gate = namespace["_require_remote_queue_project"]
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=True, maestro_session_id="owner",
        ))

        with self.assertRaises(FakeHTTPException) as missing:
            gate(request)
        self.assertEqual(missing.exception.status_code, 423)

        for protected, unlocked in ((False, True), (True, False), (False, False)):
            active_projects["owner"] = "project"
            project_access.status = (
                lambda *_args, p=protected, u=unlocked: types.SimpleNamespace(
                    protected=p, unlocked=u,
                )
            )
            with self.assertRaises(FakeHTTPException) as denied:
                gate(request)
            self.assertEqual(denied.exception.status_code, 423)
            self.assertNotIn("owner", active_projects)

        active_projects["owner"] = "project"
        project_access.status = lambda *_args: types.SimpleNamespace(
            protected=True, unlocked=True,
        )
        self.assertIsNone(gate(request))
        self.assertEqual(active_projects["owner"], "project")

    def test_remote_uploads_are_forced_private_and_paths_are_redacted(self):
        for name in ("upload_audio", "upload_image"):
            source = self._function_source(name)
            self.assertIn("maestro_remote", source)
            self.assertIn("private = True", source)
            self.assertIn("else filepath", source)

    def test_first_time_remote_user_can_create_a_password_protected_project(self):
        selector = (
            ROOT / "ui/src/components/MainContent/MainContent.tsx"
        ).read_text(encoding="utf-8")
        generate = (
            ROOT / "ui/src/components/Sidebar/GenerateButton.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("await createWorkspace(name, newPassword || undefined)", selector)
        self.assertIn("newPassword.length > 0 && newPassword.length < 8", selector)
        self.assertIn("remote && !newPassword", selector)
        self.assertIn("Required password (8+ chars)", selector)
        self.assertIn("Create project", selector)
        self.assertIn("const needsProject = !activeWorkspace", generate)
        self.assertIn("Select or create a password-protected project", generate)

    def test_remote_project_bootstrap_precedes_optional_welcome(self):
        app = APP_PATH.read_text(encoding="utf-8")
        selector = (
            ROOT / "ui/src/components/MainContent/MainContent.tsx"
        ).read_text(encoding="utf-8")
        welcome = (
            ROOT / "ui/src/components/WelcomeModal.tsx"
        ).read_text(encoding="utf-8")

        self.assertLess(
            app.index("api.fetchWorkspaces()"),
            app.index("setBootstrapState('ready')"),
        )
        self.assertIn("setBootstrapState('error')", app)
        self.assertIn("Try again", app)
        self.assertIn("BOOTSTRAP_TIMEOUT_MS", app)
        self.assertIn("did not respond while loading projects", app)
        self.assertIn("!remoteProjectRequired && <WelcomeModal />", app)

        self.assertIn("if (requiredProject) return", selector)
        self.assertIn('role="dialog"', selector)
        self.assertIn("aria-modal={requiredProject ? 'true' : undefined}", selector)
        self.assertIn("projectDialogRef.current?.focus()", selector)
        self.assertIn("event.key === 'Escape'", selector)
        self.assertIn("projectTriggerLabel", selector)
        self.assertIn("projectTriggerAccessibleLabel", selector)
        self.assertIn("activeWorkspace || 'Select project'", selector)
        self.assertIn("Current project: ${activeWorkspace}. Open project selector", selector)
        self.assertIn("await switchWorkspace(ws.name)", selector)
        self.assertIn("useStore.getState().activeWorkspace === ws.name", selector)
        self.assertIn("[open, requiredProject, workspaces.length]", selector)

        for section in ("Studio", "Director", "Chat", "Projects"):
            self.assertIn(f'title="{section}"', welcome)
        self.assertIn("Private outputs start blurred", welcome)
        self.assertIn("Project access controls", welcome)
        self.assertIn("Local studio · this machine is home", welcome)
        for capability in ("H3 control", "Queue + resume", "Blender guidance"):
            self.assertIn(capability, welcome)
        self.assertIn("Cloudflare sessions, and share links", welcome)
        self.assertIn("this {PRODUCT_NAME} host downloads and prepares model files", welcome)
        self.assertIn("Allowed local and remote users reuse that host cache", welcome)
        self.assertIn('aria-modal="true"', welcome)
        self.assertNotIn("PG-13", welcome)
        self.assertNotIn("content generation", welcome)
        self.assertNotIn("first time you use a model", welcome)

    def test_model_preparation_copy_is_host_shared_not_per_user(self):
        sources = {
            "main": ROOT / "ui/src/components/MainContent/MainContent.tsx",
            "welcome": ROOT / "ui/src/components/WelcomeModal.tsx",
            "h3_profiles": ROOT / "ui/src/components/Sidebar/H3PerformanceProfiles.tsx",
            "llm_chat": ROOT / "ui/src/components/LlmChat.tsx",
            "tools": ROOT / "ui/src/components/Sidebar/ToolsPanel.tsx",
            "services": ROOT / "ui/src/components/SettingsDrawer/ServicesSettingsPanel.tsx",
            "inpaint": ROOT / "ui/src/components/Sidebar/InpaintControls.tsx",
            "wgp": ROOT / "app/wgp.py",
            "audio": ROOT / "app/services/audio_analysis.py",
            "preprocessors": ROOT / "app/services/managed_preprocessors.py",
            "llm_service": ROOT / "app/services/llm_service.py",
            "launch": ROOT / "app/launch.py",
            "loras_docs": ROOT / "app/docs/LORAS.md",
            "readme": ROOT / "README.md",
        }
        text = {
            name: path.read_text(encoding="utf-8").casefold()
            for name, path in sources.items()
        }
        stale_phrases = {
            "main": (
                "first time you use a model",
                "progress shows at the bottom-right",
                "waiting behind other users",
                "passwords are optional locally",
                "local project data is unchanged",
                "without the current password",
                "password recovery",
            ),
            "welcome": ("downloads it once", "adjust priority"),
            "h3_profiles": ("first download required", "estimated end-to-end run time"),
            "llm_chat": ("downloads when used",),
            "tools": ("weights download on first use",),
            "services": ("first use downloads",),
            "inpaint": ("one-time setup", "about 5 min"),
            "wgp": ("first use, may take several minutes",),
            "audio": ("first use downloads",),
            "preprocessors": ("one-time setup",),
            "llm_service": ("one-time setup",),
            "launch": ("one-time setup",),
            "loras_docs": ("downloaded automatically the first time",),
            "readme": (
                "auto-downloads `llama-server` and the selected gguf on first use",
                "first generation in each model triggers a one-time weight download",
                "weights download on first use",
                "pause the global queue",
                "exact anonymous host-wide",
                "are admitted before remote jobs",
                "reprioritized",
                "started next",
                "global queue controls",
                "passwords are optional locally",
                "without the current password",
                "password recovery",
            ),
        }
        for name, phrases in stale_phrases.items():
            for phrase in phrases:
                self.assertNotIn(phrase, text[name], f"stale copy in {sources[name]}")

        self.assertIn("shared host cache", text["main"])
        self.assertIn("follow preparation status on the generation card", text["main"])
        self.assertIn("waiting for another generation on this host", text["main"])
        self.assertIn("allowed local and remote users reuse that host cache", text["welcome"])
        self.assertIn("project access and private-preview rules still apply", text["welcome"])
        self.assertIn("preparing transcription model on this host", text["audio"])
        self.assertIn("a download may be needed", text["audio"])

    def test_local_owner_can_manage_existing_project_passwords(self):
        selector = (
            ROOT / "ui/src/components/MainContent/MainContent.tsx"
        ).read_text(encoding="utf-8")
        client = CLIENT_PATH.read_text(encoding="utf-8")

        self.assertIn("await api.setWorkspacePassword(passwordTarget.name", selector)
        self.assertIn("await loadWorkspaces()", selector)
        self.assertIn("passwordValue.length < 8", selector)
        self.assertIn("passwordValue !== passwordConfirm", selector)
        self.assertIn("!remote && passwordTarget", selector)
        self.assertIn("Confirm removal", selector)
        self.assertIn("manage the password used for Cloudflare project access", selector)
        self.assertIn("closes Cloudflare access to the project", selector)
        self.assertIn("/api/v1/workspaces/${encodeURIComponent(name)}/password", client)

    def test_remote_queue_hides_only_machine_wide_controls(self):
        queue = (
            ROOT / "ui/src/components/MainContent/MainContent.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("accessContext?.machine_controls === true", queue)
        self.assertIn("{machineControls && <div", queue)
        self.assertIn("{machineControls && <>", queue)
        self.assertIn("job.logEvents", queue)
        self.assertIn("setLogEvents(job.logEvents)", queue)

    def test_remote_model_catalog_is_a_server_side_allowlist(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        configured = {
            "configured": True,
            "enabled_models": ["visible-video"],
        }
        namespace = self._function_namespace(
            ("_remote_visible_model_ids", "_require_remote_visible_models"),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_model_visibility_response": lambda: configured,
            },
        )
        remote = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=True),
        )
        local = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=False),
        )
        require = namespace["_require_remote_visible_models"]
        require(remote, ["visible-video"])
        require(local, ["hidden-video"])
        with self.assertRaises(FakeHTTPException) as raised:
            require(remote, ["hidden-video"])
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Model not found")

        configured["configured"] = False
        with self.assertRaises(FakeHTTPException) as raised:
            require(remote, ["visible-video"])
        self.assertEqual(raised.exception.status_code, 404)

        listing = self._function_source("list_models")
        download = self._function_source("download_model")
        download_status = self._function_source("model_downloads_status")
        model_options = self._function_source("get_model_options")
        plan = self._function_source("preview_generation_plan")
        generate = self._function_source("generate")
        planner = self._function_source("_plan_generation_submission")
        self.assertIn("remote_visible = _remote_visible_model_ids(request)", listing)
        self.assertLess(
            download.index("_require_remote_visible_models"),
            download.index("wgp.get_model_def"),
        )
        self.assertIn("remote_visible = _remote_visible_model_ids(request)", download_status)
        self.assertIn("_require_remote_visible_models(request, [model_type])", model_options)
        self.assertIn("Model download failed", download_status)
        for implementation in (plan, generate):
            self.assertGreaterEqual(
                implementation.count("_require_remote_visible_models"), 1,
            )
            self.assertIn("_plan_generation_submission", implementation)
        self.assertIn("_require_remote_visible_models", planner)
        self.assertIn("_h3_effective_model_types", planner)
        self.assertIn('requirements["checkpoint_options"]', plan)

        director_auth = self._function_source("_authorize_director_media_inputs")
        asset_generate = self._function_source("generate_project_asset_references")
        music_generate = self._function_source("director_generate_music")
        estimate = self._function_source("h3_estimate")
        self.assertIn('body.get("image_model")', director_auth)
        for implementation in (asset_generate, music_generate, estimate):
            self.assertIn("_require_remote_visible_models", implementation)

    def test_remote_origin_job_registry_rejects_hidden_child_checkpoints(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class Context:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        class_node = next(
            copy.deepcopy(item) for item in self.tree.body
            if isinstance(item, ast.ClassDef) and item.name == "_JobRegistry"
        )
        helper_node = next(
            copy.deepcopy(item) for item in self.tree.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_require_remote_visible_job_models"
        )
        module = ast.Module(body=[helper_node, class_node], type_ignores=[])
        ast.fix_missing_locations(module)
        remote_context = Context(True)
        namespace = {
            "threading": threading,
            "HTTPException": FakeHTTPException,
            "_request_remote": remote_context,
            "_request_session_id": Context(None),
            "_workspace_lifecycle_lock": threading.RLock(),
            "_require_job_workspace_available": lambda _job: None,
            "_model_visibility_response": lambda: {
                "configured": True,
                "enabled_models": ["visible-video"],
            },
            "wgp": types.SimpleNamespace(
                server_config={"services": {}},
                get_model_def=lambda _model: {},
            ),
        }
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)
        registry = namespace["_JobRegistry"]()
        registry["visible"] = {
            "model_type": "visible-video", "params": {},
        }
        explicitly_downgraded = {
            "model_type": "visible-video", "params": {},
            "source_remote": False,
        }
        registry["explicit-false"] = explicitly_downgraded
        self.assertTrue(explicitly_downgraded["source_remote"])
        with self.assertRaises(FakeHTTPException) as raised:
            registry["hidden-direct"] = {
                "model_type": "hidden-video", "params": {},
            }
        self.assertEqual(raised.exception.status_code, 404)
        with self.assertRaises(FakeHTTPException):
            registry["hidden-segment"] = {
                "model_type": "visible-video",
                "params": {"_h3_longform": {"segment_models": [
                    {"model_type": "hidden-ref2va"},
                ]}},
            }

        remote_context.value = False
        registry["local-hidden"] = {
            "model_type": "hidden-video", "params": {},
        }

    def test_audio_analysis_status_is_opaque_owner_and_project_scoped(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        state_lock = threading.RLock()
        remote_projects = {
            "owner-session": "project-a",
            "foreign-session": "project-a",
        }
        namespace = self._function_namespace(
            (
                "_audio_analysis_workspace",
                "_audio_analysis_owner_key",
                "audio_analyze_status",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "hmac": __import__("hmac"),
                "hashlib": hashlib,
                "_session_secret": lambda: b"test-session-secret",
                "_get_active_workspace": lambda: "local-project",
                "_remote_active_projects": remote_projects,
                "_remote_active_projects_lock": threading.RLock(),
                "_audio_analysis_state_lock": state_lock,
                "_audio_analysis_active": None,
            },
        )

        def request(session):
            return types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_remote=True,
                maestro_session_id=session,
            ))

        owner = request("owner-session")
        foreign = request("foreign-session")
        owner_key = namespace["_audio_analysis_owner_key"](
            owner, "project-a",
        )
        namespace["_audio_analysis_active"] = {
            "analysis_id": "4f" * 16,
            "owner_key": owner_key,
            "workspace": "project-a",
        }
        progress = types.ModuleType("services.audio_analysis")
        progress.get_progress = lambda: {
            "step": "transcribing",
            "detail": "Loading private progress",
        }
        with patch.dict(sys.modules, {"services.audio_analysis": progress}):
            result = namespace["audio_analyze_status"](
                owner, analysis_id="4f" * 16, workspace="project-a",
            )
            self.assertEqual(result["step"], "transcribing")
            self.assertEqual(result["analysis_id"], "4f" * 16)
            self.assertNotIn("owner-session", repr(result))
            for denied_request, denied_id in (
                (foreign, "4f" * 16),
                (owner, "5a" * 16),
            ):
                with self.subTest(denied_request=denied_request, denied_id=denied_id):
                    with self.assertRaises(FakeHTTPException) as raised:
                        namespace["audio_analyze_status"](
                            denied_request,
                            analysis_id=denied_id,
                            workspace="project-a",
                        )
                    self.assertEqual(raised.exception.status_code, 404)

        namespace["_audio_analysis_active"] = None
        self.assertEqual(
            namespace["audio_analyze_status"](owner, workspace="project-a"),
            {"step": "", "detail": ""},
        )
        with self.assertRaises(FakeHTTPException) as raised:
            namespace["audio_analyze_status"](
                owner, analysis_id="4f" * 16, workspace="project-a",
            )
        self.assertEqual(raised.exception.status_code, 404)

        analyze = self._function_source("analyze_audio")
        self.assertIn("analysis_id = uuid.uuid4().hex", analyze)
        self.assertIn("_audio_analysis_gate.acquire(blocking=False)", analyze)
        self.assertIn("hmac.compare_digest", analyze)
        self.assertIn("await asyncio.shield(worker_task)", analyze)
        self.assertIn("release_analysis_lease()", analyze)

    def test_audio_analysis_progress_is_pollable_while_worker_runs(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        started = threading.Event()
        release = threading.Event()
        progress = types.ModuleType("services.audio_analysis")

        def analyze(**_kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test analysis release timed out")
            return {"duration": 1.0}

        progress.analyze = analyze
        progress.get_progress = lambda: {
            "step": "transcribing", "detail": "Working",
        }
        services = types.ModuleType("services")
        services.audio_analysis = progress
        namespace = self._function_namespace(
            (
                "_audio_analysis_workspace", "_audio_analysis_owner_key",
                "analyze_audio", "audio_analyze_status",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "asyncio": asyncio,
                "threading": threading,
                "uuid": __import__("uuid"),
                "hmac": __import__("hmac"),
                "hashlib": hashlib,
                "traceback": __import__("traceback"),
                "_session_secret": lambda: b"test-session-secret",
                "_get_active_workspace": lambda: "project-a",
                "_remote_active_projects": {"owner": "project-a"},
                "_remote_active_projects_lock": threading.RLock(),
                "_audio_analysis_gate": threading.Lock(),
                "_audio_analysis_state_lock": threading.RLock(),
                "_audio_analysis_active": None,
                "_resolve_authorized_request_media": (
                    lambda _request, path, _workspace: path
                ),
                # Keep the test on the branch that does not touch CUDA/model state.
                "_gen_lock": threading.Lock(),
            },
        )
        namespace["_gen_lock"].acquire()

        class Request:
            state = types.SimpleNamespace(
                maestro_remote=True, maestro_session_id="owner",
            )

            async def json(self):
                return {"audio_path": "owned.wav", "workspace": "project-a"}

        async def exercise():
            with patch.dict(sys.modules, {
                "services": services,
                "services.audio_analysis": progress,
            }):
                task = asyncio.create_task(namespace["analyze_audio"](Request()))
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                first = namespace["audio_analyze_status"](
                    Request(), workspace="project-a",
                )
                self.assertEqual(first["step"], "transcribing")
                self.assertEqual(len(first["analysis_id"]), 32)
                second = namespace["audio_analyze_status"](
                    Request(), analysis_id=first["analysis_id"],
                    workspace="project-a",
                )
                self.assertEqual(second["detail"], "Working")
                release.set()
                result = await task
                self.assertEqual(result["analysis_id"], first["analysis_id"])
                self.assertIsNone(namespace["_audio_analysis_active"])

                # Saturate the default executor, then cancel the request while
                # its shielded analysis worker is still queued. The lease must
                # remain held until that worker actually runs and cleans up.
                from concurrent.futures import ThreadPoolExecutor
                executor = ThreadPoolExecutor(max_workers=1)
                asyncio.get_running_loop().set_default_executor(executor)
                blocker_started = threading.Event()
                blocker_release = threading.Event()

                def block_executor():
                    blocker_started.set()
                    blocker_release.wait(5)

                blocker = asyncio.get_running_loop().run_in_executor(
                    None, block_executor,
                )
                while not blocker_started.is_set():
                    await asyncio.sleep(0.005)
                started.clear()
                cancelled = asyncio.create_task(
                    namespace["analyze_audio"](Request()),
                )
                while namespace["_audio_analysis_active"] is None:
                    await asyncio.sleep(0.005)
                cancelled.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await cancelled
                self.assertFalse(namespace["_audio_analysis_gate"].acquire(False))
                blocker_release.set()
                await blocker
                for _ in range(200):
                    if namespace["_audio_analysis_gate"].acquire(False):
                        namespace["_audio_analysis_gate"].release()
                        break
                    await asyncio.sleep(0.005)
                else:
                    self.fail("cancelled queued worker did not release analysis lease")
                self.assertTrue(started.is_set())
                self.assertIsNone(namespace["_audio_analysis_active"])

        try:
            asyncio.run(exercise())
        finally:
            release.set()
            namespace["_gen_lock"].release()

    def test_workspace_busy_checks_are_target_scoped_and_admission_is_reserved(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        with tempfile.TemporaryDirectory() as root:
            project_a = os.path.join(root, "project-a")
            project_b = os.path.join(root, "project-b")
            os.mkdir(project_a)
            os.mkdir(project_b)
            jobs = {
                "job-b": {
                    "status": "running",
                    "workspace": "project-b",
                    "out_dir": project_b,
                    "params": {},
                },
            }
            namespace = self._function_namespace(
                (
                    "_path_targets_workspace",
                    "_job_targets_workspace",
                    "_workspace_has_busy_jobs",
                    "_require_workspace_not_deleting",
                    "_begin_workspace_operation",
                    "_end_workspace_operation",
                    "_require_job_workspace_available",
                ),
                {
                    "os": os,
                    "HTTPException": FakeHTTPException,
                    "_jobs": jobs,
                    "_active_gen_states": {},
                    "_workspace_lifecycle_lock": threading.RLock(),
                    "_workspaces_deleting": set(),
                    "_workspace_operations": {},
                    "_existing_workspace_dir": lambda workspace: {
                        "project-a": project_a,
                        "project-b": project_b,
                    }[workspace],
                },
            )
            self.assertFalse(namespace["_workspace_has_busy_jobs"](
                "project-a", project_a,
            ))
            self.assertTrue(namespace["_workspace_has_busy_jobs"](
                "project-b", project_b,
            ))

            namespace["_begin_workspace_operation"]("project-a")
            self.assertEqual(namespace["_workspace_operations"], {"project-a": 1})
            namespace["_end_workspace_operation"]("project-a")
            self.assertEqual(namespace["_workspace_operations"], {})

            namespace["_workspaces_deleting"].add("project-a")
            with self.assertRaises(FakeHTTPException) as raised:
                namespace["_require_job_workspace_available"]({
                    "workspace": "project-a",
                    "out_dir": project_a,
                    "params": {},
                })
            self.assertEqual(raised.exception.status_code, 409)

        deletion = self._function_source("delete_workspace")
        self.assertIn("_workspace_has_busy_jobs(name, ws_dir)", deletion)
        self.assertIn("_workspace_has_busy_director_pipeline(name, ws_dir)", deletion)
        self.assertIn("_workspaces_deleting.add(name)", deletion)
        self.assertIn("os.path.realpath(lexical)", deletion)
        self.assertNotIn("any_pipeline_active", deletion)
        asset_generate = self._function_source("generate_project_asset_references")
        self.assertLess(
            asset_generate.rindex("_begin_workspace_operation(project_id)"),
            asset_generate.index("_queue_recovery_register_and_publish("),
        )

    def test_workspace_delete_allows_unrelated_job_and_rejects_symlink_alias(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        with tempfile.TemporaryDirectory() as root:
            project_a = os.path.join(root, "project-a")
            project_b = os.path.join(root, "project-b")
            os.mkdir(project_a)
            os.mkdir(project_b)
            Path(project_a, "media.mp4").write_bytes(b"a")
            Path(project_b, "media.mp4").write_bytes(b"b")
            assets_deleted = []
            shares_revoked = []
            namespace = self._function_namespace(
                (
                    "_safe_join",
                    "_path_targets_workspace",
                    "_job_targets_workspace",
                    "_workspace_has_busy_jobs",
                    "delete_workspace",
                ),
                {
                    "api": types.SimpleNamespace(delete=lambda *_a, **_k: lambda fn: fn),
                    "Request": object,
                    "HTTPException": FakeHTTPException,
                    "os": os,
                    "wgp": types.SimpleNamespace(server_config={"save_path": root}),
                    "_jobs": {
                        "job-b": {
                            "status": "running",
                            "workspace": "project-b",
                            "out_dir": project_b,
                            "params": {},
                        },
                    },
                    "_active_gen_states": {"job-b": object()},
                    "_workspace_creation_lock": threading.RLock(),
                    "_workspace_lifecycle_lock": threading.RLock(),
                    "_workspaces_deleting": set(),
                    "_workspace_operations": {},
                    "_workspace_has_busy_director_pipeline": lambda *_args: False,
                    "_require_remote_project_mutation_access": (
                        lambda *_args: types.SimpleNamespace(unlocked=True)
                    ),
                    "_get_active_workspace": lambda: "default",
                    "_persist_active_workspace": lambda *_args, **_kwargs: root,
                    "_project_asset_store": lambda: types.SimpleNamespace(
                        delete_project=lambda name: assets_deleted.append(name),
                    ),
                    "_output_share_manager": lambda: types.SimpleNamespace(
                        revoke_workspace=lambda name: shares_revoked.append(name),
                    ),
                    "_project_access": types.SimpleNamespace(
                        revoke_workspace=lambda _name: None,
                    ),
                    "_remote_active_projects": {},
                    "_remote_active_projects_lock": threading.RLock(),
                },
            )
            request = types.SimpleNamespace(
                state=types.SimpleNamespace(
                    maestro_remote=False,
                    maestro_session_id="owner",
                ),
            )
            namespace["_workspace_operations"]["project-a"] = 1
            with self.assertRaises(FakeHTTPException) as raised:
                namespace["delete_workspace"]("project-a", request)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertTrue(os.path.isdir(project_a))
            self.assertEqual(assets_deleted, [])
            namespace["_workspace_operations"].clear()
            result = namespace["delete_workspace"]("project-a", request)
            self.assertEqual(result["status"], "ok")
            self.assertFalse(os.path.exists(project_a))
            self.assertTrue(os.path.isdir(project_b))
            self.assertEqual(assets_deleted, ["project-a"])
            self.assertEqual(shares_revoked, ["project-a"])

            alias = os.path.join(root, "project-link")
            try:
                os.symlink(project_b, alias)
            except (OSError, NotImplementedError):
                return
            with self.assertRaises(FakeHTTPException) as raised:
                namespace["delete_workspace"]("project-link", request)
            self.assertEqual(raised.exception.status_code, 400)
            self.assertTrue(os.path.isdir(project_b))


if __name__ == "__main__":
    unittest.main()
