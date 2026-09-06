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
    def __init__(self, content=None, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = dict(headers or {})


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
        "_verified_runtime_cloudflare_origin",
        "_cors_origin_allowed",
        "_exact_runtime_cors_middleware",
        "_request_is_cloudflare_remote",
        "_first_forwarded_value",
        "_canonical_http_origin",
        "_approved_local_origin",
        "_request_external_origins",
        "_matches_verified_stable_redirect_origin",
        "_account_exact_origin_allowed",
        "_registration_request_is_remote",
        "_public_registration_origin_allowed",
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
                    "_CORS_ALLOWED_METHODS",
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
        "re": __import__("re"),
        "ipaddress": ipaddress,
        "threading": threading,
        "urlsplit": urlsplit,
        "Request": object,
        "JSONResponse": _Response,
        "Response": _Response,
        "HTTPException": FakeHTTPException,
        "_runtime_share_url_lock": threading.Lock(),
        "_runtime_share_url": "",
        "_runtime_share_quick_tunnel_url": "",
        "_runtime_share_stable_verified": False,
        "_account_project_access_state": lambda: {"enforced": False},
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
        self.security["_account_project_access_state"] = lambda: {
            "enforced": False,
        }

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
        quick = "https://random.trycloudflare.com"
        request = _Request(
            origin=quick,
            x_forwarded_proto="https",
            x_forwarded_host="random.trycloudflare.com",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "true"}, clear=False):
            self._register_runtime_share(stable=quick, quick=quick)
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

    def test_credentialed_cors_tracks_only_the_registered_cloudflare_origins(self):
        allowed = self.security["_cors_origin_allowed"]
        stable = "https://maestro.account.workers.dev"
        quick = "https://current-tunnel.trycloudflare.com"
        self.assertFalse(allowed(stable))
        self.assertFalse(allowed(quick))
        self._register_runtime_share(stable=stable, quick=quick)
        self.assertTrue(allowed(stable))
        self.assertTrue(allowed(quick))
        self.assertFalse(allowed("https://other.account.workers.dev"))
        self.assertFalse(allowed("https://other-tunnel.trycloudflare.com"))

    def test_verified_stable_browser_preflight_and_post_receive_exact_cors_headers(self):
        middleware = self.security["_exact_runtime_cors_middleware"]
        stable = "https://maestro.account.workers.dev"
        quick = "https://current-tunnel.trycloudflare.com"

        preflight = _Request(
            method="OPTIONS",
            origin=stable,
            access_control_request_method="POST",
            access_control_request_headers="content-type",
        )

        async def unexpected_call_next(_request):
            raise AssertionError("preflight reached the application")

        rejected = asyncio.run(middleware(preflight, unexpected_call_next))
        self.assertEqual(rejected.status_code, 400)

        self._register_runtime_share(stable=stable, quick=quick)
        accepted = asyncio.run(middleware(preflight, unexpected_call_next))
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.headers["Access-Control-Allow-Origin"], stable)
        self.assertEqual(accepted.headers["Access-Control-Allow-Credentials"], "true")
        self.assertEqual(accepted.headers["Access-Control-Allow-Methods"], "POST")

        post = _Request(method="POST", origin=stable)

        async def application_response(_request):
            return _Response({"status": "ok"}, headers={"Vary": "Accept-Encoding"})

        response = asyncio.run(middleware(post, application_response))
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], stable)
        self.assertEqual(response.headers["Access-Control-Allow-Credentials"], "true")
        self.assertEqual(response.headers["Vary"], "Accept-Encoding, Origin")

    def test_public_registration_accepts_only_the_exact_registered_browser_origin(self):
        allowed = self.security["_public_registration_origin_allowed"]
        reject = self.security["_reject_cross_origin_mutation"]
        stable = "https://maestro.account.workers.dev"
        quick = "https://current-tunnel.trycloudflare.com"
        self._register_runtime_share(stable=stable, quick=quick)

        for origin in (stable, quick):
            request = _Request(
                method="POST",
                path="/api/v1/account/register",
                base_url=quick + "/",
                origin=origin,
                x_forwarded_proto="https",
                x_forwarded_host="current-tunnel.trycloudflare.com",
            )
            request.state = types.SimpleNamespace(maestro_remote=True)
            self.assertTrue(allowed(request, mutation=True))
            self.assertIsNone(reject(request))

        for origin in (
            "https://other.account.workers.dev",
            "https://other.trycloudflare.com",
            "*",
        ):
            request = _Request(
                method="POST",
                path="/api/v1/account/register",
                base_url=quick + "/",
                origin=origin,
                x_forwarded_proto="https",
                x_forwarded_host="current-tunnel.trycloudflare.com",
            )
            request.state = types.SimpleNamespace(maestro_remote=True)
            self.assertFalse(allowed(request, mutation=True))
            self.assertEqual(reject(request).status_code, 403)

        loopback = _Request(
            method="POST",
            path="/api/v1/account/register",
            base_url="http://127.0.0.1:7860/",
            origin="http://127.0.0.1:7860",
        )
        loopback.state = types.SimpleNamespace(maestro_remote=False)
        self.assertTrue(allowed(loopback, mutation=False))
        self.assertTrue(allowed(loopback, mutation=True))

        loopback_no_origin = _Request(
            method="POST",
            path="/api/v1/account/register",
            base_url="http://127.0.0.1:7860/",
        )
        loopback_no_origin.state = types.SimpleNamespace(maestro_remote=False)
        self.assertTrue(allowed(loopback_no_origin, mutation=False))
        self.assertFalse(allowed(loopback_no_origin, mutation=True))

        lan = _Request(
            method="POST",
            path="/api/v1/account/register",
            base_url="http://192.168.1.20:7860/",
            origin="http://192.168.1.20:7860",
            client_host="192.168.1.20",
        )
        lan.state = types.SimpleNamespace(maestro_remote=False)
        self.assertFalse(allowed(lan, mutation=False))
        self.assertFalse(allowed(lan, mutation=True))

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
        self.assertFalse(context["account_project_access_active"])
        self.assertFalse(context["account_project_creation_requires_account"])
        self.assertTrue(context["project_password_required"])
        self.assertEqual(
            context["share_flow"],
            "Select a project name, then enter that project's password",
        )
        self.assertFalse(context["machine_controls"])
        self.assertFalse(context["custom_model_sources"])
        self.assertFalse(context["classic_ui"])
        self.assertEqual(context["share_url"], "")

    def test_active_account_projects_remove_remote_project_password_requirement(self):
        request = _Request(base_url="https://maestro.account.workers.dev/")
        request.state = types.SimpleNamespace(maestro_remote=True)
        self.security["_account_project_access_state"] = lambda: {
            "state": "active",
            "enforced": True,
        }

        context = self.security["get_access_context"](request)

        self.assertTrue(context["remote"])
        self.assertTrue(context["account_project_access_active"])
        self.assertTrue(context["account_project_creation_requires_account"])
        self.assertFalse(context["project_password_required"])
        self.assertEqual(context["share_flow"], "Sign in and select a project")

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
            self._register_runtime_share(stable=quick, quick=quick)
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

    def test_registered_cloudflare_fails_closed_when_process_flag_is_missing(self):
        classify = self.security["_request_is_cloudflare_remote"]
        rewritten = _Request(
            base_url="http://127.0.0.1:7860/",
            x_forwarded_proto="http",
            x_forwarded_host="127.0.0.1:7860",
            cf_ray="abc123-DEN",
            cf_connecting_ip="203.0.113.7",
        )
        with patch.dict(os.environ, {"PINOKIO_SHARE_CLOUDFLARE": "false"}, clear=False):
            self._register_runtime_share(
                quick="https://registered.trycloudflare.com",
            )
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

    def test_director_pipeline_routes_register_without_runtime_imports(self):
        nodes = [
            item for item in self.tree.body
            if isinstance(item, ast.AsyncFunctionDef)
            and item.name in {"director_preflight", "director_pipeline_start"}
        ]

        class RouteRecorder:
            def __init__(self):
                self.routes = {}

            def post(self, path, **_kwargs):
                def register(function):
                    self.routes[path] = function
                    return function
                return register

        recorder = RouteRecorder()
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {"api": recorder, "Request": object}
        exec(compile(module, str(LAUNCH_PATH), "exec"), namespace)

        self.assertIn("/api/v1/director/pipeline/start", recorder.routes)
        self.assertEqual(
            recorder.routes["/api/v1/director/pipeline/start"].__name__,
            "director_pipeline_start",
        )
        self.assertIn("/api/v1/director/preflight", recorder.routes)
        self.assertEqual(
            recorder.routes["/api/v1/director/preflight"].__name__,
            "director_preflight",
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
        helper_start = client.index("async function throwDirectorRequestFailure")
        helper_end = client.index("export interface DirectorPreflightRequest", helper_start)
        helper = client[helper_start:helper_end]
        start = client.index("export async function startPipeline")
        end = client.index("export async function fetchPipelineStatus", start)
        implementation = client[start:end]

        self.assertIn("Director is not available in the running Maestro backend", helper)
        self.assertIn("Director access was denied", helper)
        self.assertIn("Unlock the selected Director project first", helper)
        self.assertIn("Director could not access a selected reference", helper)
        self.assertIn("directorStructuredFailure(payload)", helper)
        self.assertIn("payload.detail", helper)
        self.assertIn("throwDirectorRequestFailure(res", implementation)

    def test_generation_destinations_require_unlocked_project(self):
        ordinary = self._function_source("generate")
        music = self._function_source("director_generate_music")
        director = self._function_source("director_pipeline_start")

        self.assertIn('permission="project.generate"', ordinary)
        self.assertIn('permission="project.generate"', music)
        self.assertIn('body["_director_component_errors"] = True', director)
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
        self.assertIn("_exact_runtime_cors_middleware", self.source)
        self.assertIn('"Access-Control-Allow-Credentials": "true"', self.source)
        self.assertIn("_cors_origin_allowed(origin)", self.source)
        self.assertNotIn('allow_origins=["*"]', self.source)
        self.assertNotIn("allow_origin_regex", self.source)

    def test_public_health_is_minimal_and_cookie_free(self):
        health = self._function_source("public_health")
        self.assertIn('return {"status": "ok"}', health)
        self.assertIn('request.url.path == "/health"', self.source)

    def test_public_readiness_is_content_free_cookie_free_and_opaque(self):
        ready = self._function_source("public_ready")
        self.assertIn("_startup_recovery_state_value()", ready)
        self.assertIn('200 if', ready)
        self.assertIn('else 503', ready)
        self.assertIn('headers={"Cache-Control": "no-store"}', ready)
        self.assertIn('request.url.path == "/ready"', self.source)
        self.assertNotIn("JSONResponse", ready)
        for private_name in (
            "exception", "error", "path", "job", "prompt", "traceback",
        ):
            self.assertNotIn(private_name, ready.casefold())

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

    def test_account_membership_precedes_password_and_hides_nonmembers(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class MembershipError(Exception):
            pass

        record = {
            "project_instance": "project:v1:" + "a" * 64,
            "state": "active",
            "bindings": [{"account_id": "1" * 32, "role": "viewer"}],
        }
        store = types.SimpleNamespace(lookup=lambda **_kwargs: record)
        namespace = self._function_namespace(
            ("_require_account_project_permission",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "ProjectMembershipError": MembershipError,
                "ProjectMembershipStoreUnavailableError": MembershipError,
                "QueueRecoveryAdapterError": ValueError,
                "_account_project_access_state": lambda: {
                    "enforced": True,
                },
                "_require_account_store": lambda _request: object(),
                "_queue_recovery_existing_project_identity": (
                    lambda _path: record["project_instance"]
                ),
                "_account_project_membership_store": lambda: store,
                "_raise_project_setup_unavailable": (
                    lambda error: (_ for _ in ()).throw(error)
                ),
                "role_allows": lambda role, permission: (
                    role == "viewer" and permission in {
                        "project.list", "project.open", "project.read",
                    }
                ),
            },
        )
        guard = namespace["_require_account_project_permission"]

        anonymous = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_account_principal=None),
        )
        self.assertIsNone(
            guard(
                anonymous,
                "/outputs/project",
                "project.read",
                state={"enforced": False},
            ),
        )
        with self.assertRaises(FakeHTTPException) as hidden:
            guard(anonymous, "/outputs/project", "project.read")
        self.assertEqual(hidden.exception.status_code, 404)

        viewer = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_account_principal={"id": "1" * 32},
        ))
        self.assertIs(guard(viewer, "/outputs/project", "project.read"), record)
        with self.assertRaises(FakeHTTPException) as denied:
            guard(viewer, "/outputs/project", "project.delete")
        self.assertEqual(denied.exception.status_code, 404)

        outsider = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_account_principal={"id": "2" * 32},
        ))
        with self.assertRaises(FakeHTTPException) as hidden:
            guard(outsider, "/outputs/project", "project.read")
        self.assertEqual(hidden.exception.status_code, 404)

        require = self._function_source("_require_project_access")
        self.assertLess(
            require.index("_require_account_project_permission"),
            require.index("_project_access.authorize"),
        )

    def test_active_membership_removes_project_password_routes(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        member = {"state": "active"}
        calls = {
            "status": 0,
            "authorize": 0,
            "unlock": 0,
            "set_password": 0,
            "lock": 0,
            "lock_all": 0,
        }

        def password_call(kind):
            def fail(*_args, **_kwargs):
                calls[kind] += 1
                raise AssertionError(f"active membership called password {kind}")
            return fail

        project_access = types.SimpleNamespace(
            status=password_call("status"),
            authorize=password_call("authorize"),
            unlock=password_call("unlock"),
            set_password=password_call("set_password"),
            lock=password_call("lock"),
            lock_all=password_call("lock_all"),
        )
        access = self._function_namespace(
            ("_require_project_access",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_account_project_access_state": lambda: {
                    "state": "active", "enforced": True,
                },
                "_existing_workspace_dir": lambda _name: "/outputs/project-a",
                "_workspace_dir": lambda _name: (_ for _ in ()).throw(
                    AssertionError("active access used a creating path")
                ),
                "_require_account_project_permission": (
                    lambda *_args, **_kwargs: member
                ),
                "_project_access": project_access,
                "_STATE_CHANGING_METHODS": frozenset({"POST", "PUT", "DELETE"}),
            },
        )["_require_project_access"]
        for method in ("GET", "POST"):
            request = types.SimpleNamespace(
                method=method,
                state=types.SimpleNamespace(
                    maestro_remote=True,
                    maestro_session_id="account-browser",
                ),
            )
            self.assertEqual(
                access(request, "project-a"), "/outputs/project-a",
            )
        self.assertTrue(all(count == 0 for count in calls.values()))

        class NoBodyRequest:
            state = types.SimpleNamespace(
                maestro_remote=True,
                maestro_session_id="account-browser",
            )

            async def json(self):
                raise AssertionError("membership unlock read a password body")

        unlock = self._function_namespace(
            ("unlock_workspace",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_account_project_access_state": lambda: {
                    "state": "active", "enforced": True,
                },
                "_existing_workspace_dir": lambda _name: "/outputs/project-a",
                "_require_account_project_permission": (
                    lambda *_args, **_kwargs: member
                ),
                "_project_access": project_access,
            },
        )["unlock_workspace"]
        with self.assertRaises(FakeHTTPException) as unlock_removed:
            asyncio.run(unlock("project-a", NoBodyRequest()))
        self.assertEqual(unlock_removed.exception.status_code, 404)
        self.assertTrue(all(count == 0 for count in calls.values()))

        active_namespace = self._function_namespace(
            (
                "set_workspace_password",
                "lock_workspace",
                "lock_all_workspaces",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_account_project_access_state": lambda: {
                    "state": "active", "enforced": True,
                },
                "_existing_workspace_dir": lambda _name: "/outputs/project-a",
                "_workspace_dir": lambda _name: (_ for _ in ()).throw(
                    AssertionError("active password route used a creating path")
                ),
                "_require_account_project_permission": (
                    lambda *_args, **_kwargs: member
                ),
                "_project_access": project_access,
                "_remote_active_projects": {"account-browser": "project-a"},
                "_remote_active_projects_lock": threading.RLock(),
            },
        )
        for action in (
            lambda: asyncio.run(active_namespace["set_workspace_password"](
                "project-a", NoBodyRequest(),
            )),
            lambda: active_namespace["lock_workspace"](
                "project-a", NoBodyRequest(),
            ),
            lambda: active_namespace["lock_all_workspaces"](NoBodyRequest()),
        ):
            with self.assertRaises(FakeHTTPException) as removed:
                action()
            self.assertEqual(removed.exception.status_code, 404)
        self.assertTrue(all(count == 0 for count in calls.values()))

        class StaleCreateRequest:
            state = types.SimpleNamespace(
                maestro_remote=True,
                maestro_session_id="account-browser",
            )

            async def json(self):
                return {
                    "name": "stale-client-project",
                    "password": "legacy-password",
                    "remember": "device",
                }

        create = self._function_namespace(
            ("create_workspace",),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_account_project_access_state": lambda: {
                    "state": "active", "enforced": True,
                },
            },
        )["create_workspace"]
        with self.assertRaises(FakeHTTPException) as stale_create:
            asyncio.run(create(StaleCreateRequest()))
        self.assertEqual(stale_create.exception.status_code, 400)

        legacy_calls = {"lock": 0, "lock_all": 0}
        legacy_access = types.SimpleNamespace(
            lock=lambda *_args: (
                legacy_calls.__setitem__("lock", legacy_calls["lock"] + 1)
                or 1
            ),
            lock_all=lambda *_args: (
                legacy_calls.__setitem__(
                    "lock_all", legacy_calls["lock_all"] + 1,
                )
                or 2
            ),
        )
        legacy_namespace = self._function_namespace(
            ("lock_workspace", "lock_all_workspaces"),
            {
                "Request": object,
                "_account_project_access_state": lambda: {
                    "state": "needs_attention", "enforced": False,
                },
                "_project_access": legacy_access,
                "_remote_active_projects": {"account-browser": "project-a"},
                "_remote_active_projects_lock": threading.RLock(),
            },
        )
        self.assertFalse(legacy_namespace[
            "lock_workspace"
        ]("project-a", NoBodyRequest())["unlocked"])
        self.assertFalse(legacy_namespace[
            "lock_all_workspaces"
        ](NoBodyRequest())["unlocked"])
        self.assertEqual(legacy_calls, {"lock": 1, "lock_all": 1})

        create = self._function_source("create_workspace")
        delete = self._function_source("delete_workspace")
        self.assertIn(
            "remote and not account_projects_enforced and not password",
            create,
        )
        self.assertIn("password and not account_projects_enforced", create)
        self.assertIn("if membership is None:", delete)

    def test_pending_project_setup_keeps_legacy_password_access(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        class MembershipError(Exception):
            pass

        calls = {
            "legacy_path": 0,
            "existing_path": 0,
            "account": 0,
            "identity": 0,
            "password": 0,
        }
        current_state = {
            "state": "needs_attention",
            "enforced": False,
        }

        def legacy_path(_workspace):
            calls["legacy_path"] += 1
            return "/outputs/missing-marker"

        def existing_path(_workspace):
            calls["existing_path"] += 1
            return "/outputs/missing-marker"

        def require_account(_request):
            calls["account"] += 1
            return object()

        def project_identity(_path):
            calls["identity"] += 1
            raise AssertionError("pending access consulted membership identity")

        def password_status(*_args):
            calls["password"] += 1
            return types.SimpleNamespace(protected=True, unlocked=True)

        namespace = self._function_namespace(
            (
                "_require_account_project_permission",
                "_require_project_access",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "ProjectMembershipError": MembershipError,
                "ProjectMembershipStoreUnavailableError": MembershipError,
                "QueueRecoveryAdapterError": ValueError,
                "_account_project_access_state": lambda: current_state,
                "_require_account_store": require_account,
                "_queue_recovery_existing_project_identity": project_identity,
                "_account_project_membership_store": lambda: None,
                "_raise_project_setup_unavailable": (
                    lambda error: (_ for _ in ()).throw(error)
                ),
                "role_allows": lambda _role, _permission: False,
                "_workspace_dir": legacy_path,
                "_existing_workspace_dir": existing_path,
                "_project_access": types.SimpleNamespace(
                    status=password_status,
                    authorize=password_status,
                ),
                "_STATE_CHANGING_METHODS": frozenset({"POST", "PUT", "DELETE"}),
            },
        )
        request = types.SimpleNamespace(
            method="GET",
            state=types.SimpleNamespace(
                maestro_remote=False,
                maestro_session_id="legacy-browser",
                maestro_account_principal=None,
            ),
        )

        self.assertEqual(
            namespace["_require_project_access"](request, "missing-marker"),
            "/outputs/missing-marker",
        )
        self.assertEqual(calls, {
            "legacy_path": 1,
            "existing_path": 0,
            "account": 0,
            "identity": 0,
            "password": 1,
        })

        current_state = {"state": "active", "enforced": True}
        with self.assertRaises(FakeHTTPException) as hidden:
            namespace["_require_project_access"](
                request,
                "missing-marker",
            )
        self.assertEqual(hidden.exception.status_code, 404)
        self.assertEqual(calls, {
            "legacy_path": 1,
            "existing_path": 1,
            "account": 1,
            "identity": 0,
            "password": 1,
        })

    def test_account_project_cutover_is_explicit_and_lifecycle_fenced(self):
        state = self._function_source("_account_project_access_state")
        migration = self._function_source("migrate_account_projects")
        owner = self._function_source("_require_account_project_migration_owner")
        bootstrap = self._function_source("bootstrap_account_owner")
        create = self._function_source("create_workspace")
        delete = self._function_source("delete_workspace")

        self.assertNotIn("migrate_inventory", bootstrap)
        self.assertNotIn("initialize_from_ledger", bootstrap)
        self.assertIn('"enforced": not needs_attention', state)
        self.assertIn('"state": "needs_attention"', state)
        self.assertIn(
            '_request_has_account_capability(request, "owner.admin")',
            owner,
        )
        self.assertIn("_request_has_recent_account_reauth(request)", owner)
        self.assertIn('request.method == "GET"', owner)
        self.assertIn("_account_activation_read_allowed(request)", owner)
        self.assertIn("_account_local_bootstrap_allowed(request)", owner)
        self.assertIn("_workspace_creation_lock", migration)
        self.assertIn("_workspace_lifecycle_lock", migration)
        self.assertIn("inspect_inventory", migration)
        self.assertIn("project_migration_needs_attention", migration)
        self.assertLess(
            migration.index("inspect_inventory"),
            migration.index("migrate_inventory"),
        )
        self.assertLess(
            migration.index("migrate_inventory"),
            migration.index("initialize_from_ledger"),
        )
        self.assertIn("project_membership_store.bind", create)
        self.assertLess(
            create.index("ensure_project_instance_marker"),
            create.index("project_membership_store.bind"),
        )
        self.assertIn("safe_delete_dir(ws_dir)", create)
        self.assertLess(
            create.index("_project_access.set_password"),
            create.index("project_membership_store.bind"),
        )
        self.assertIn("membership_store.begin_deletion", delete)
        self.assertIn("membership_store.finish_deletion", delete)
        self.assertIn("membership_store.cancel_deletion", delete)
        self.assertIn(
            'membership["state"] in {"deleting", "deleted"}',
            delete,
        )
        self.assertIn("deletion_destructive = True", delete)
        self.assertLess(
            delete.index("membership_store.begin_deletion"),
            delete.index("_project_access.revoke_workspace(name)"),
        )
        self.assertLess(
            delete.index("_project_access.revoke_workspace(name)"),
            delete.index("safe_delete_dir(ws_dir)"),
        )
        self.assertLess(
            delete.index("membership_store.finish_deletion"),
            delete.index("safe_delete_dir(ws_dir)"),
        )

    def test_account_workspace_list_includes_only_memberships(self):
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=False,
            maestro_session_id="browser",
        ))
        namespace = self._function_namespace(
            ("_workspace_access_fields", "list_workspaces_endpoint"),
            {
                "Request": object,
                "QueueRecoveryAdapterError": ValueError,
                "_account_project_list_identities": lambda _request: {
                    "project-a": {
                        "role": "viewer",
                        "permissions": [
                            "project.list", "project.open", "project.read",
                        ],
                    },
                },
                "_queue_recovery_existing_project_identity": (
                    lambda path: os.path.basename(path)
                ),
                "_list_workspaces": lambda: [
                    {"name": "a", "path": "/outputs/project-a", "file_count": 1},
                    {"name": "b", "path": "/outputs/project-b", "file_count": 2},
                ],
                "_project_access": types.SimpleNamespace(
                    status=lambda *_args: (_ for _ in ()).throw(
                        AssertionError("membership listing checked a password")
                    ),
                ),
                "_remote_active_projects": {},
                "_remote_active_projects_lock": threading.RLock(),
                "_get_active_workspace": lambda: "b",
            },
        )
        listed = namespace["list_workspaces_endpoint"](request)
        self.assertEqual([item["name"] for item in listed["workspaces"]], ["a"])
        self.assertEqual(listed["workspaces"][0]["project_role"], "viewer")
        self.assertEqual(
            listed["workspaces"][0]["project_permissions"],
            ["project.list", "project.open", "project.read"],
        )
        self.assertEqual(listed["active"], "")

    def test_job_session_ownership_remains_conjunctive_with_membership(self):
        class FakeHTTPException(Exception):
            pass

        allowed = True

        def require_membership(_request, _path, permission):
            self.assertEqual(permission, "project.read")
            if not allowed:
                raise FakeHTTPException()

        namespace = self._function_namespace(
            (
                "_recovered_job_remote_project_accessible",
                "_job_owned_by_request",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "QueueRecoveryAdapterError": ValueError,
                "_accounts_enabled": lambda: True,
                "_existing_workspace_dir": lambda name: f"/outputs/{name}",
                "_require_account_project_permission": require_membership,
            },
        )
        request = types.SimpleNamespace(state=types.SimpleNamespace(
            maestro_remote=False,
            maestro_session_id="shared-browser-session",
        ))
        job = {
            "workspace": "project-a",
            "session_id": "shared-browser-session",
        }
        self.assertTrue(namespace["_job_owned_by_request"](job, request))
        ownerless = dict(job, session_id=None)
        self.assertTrue(
            namespace["_job_owned_by_request"](ownerless, request),
        )
        allowed = False
        self.assertFalse(namespace["_job_owned_by_request"](job, request))
        self.assertFalse(
            namespace["_job_owned_by_request"](ownerless, request),
        )

        for helper in (
            "_require_owned_job_project",
            "_require_h3_delivery_recovery_job",
            "_require_remote_queue_project",
        ):
            source = self._function_source(helper)
            self.assertIn("_require_account_project_permission", source)
            self.assertIn("if membership is None:", source)
            self.assertLess(
                source.index("if membership is None:"),
                source.index("_project_access.status"),
            )

    def test_generation_admission_uses_generate_permission(self):
        for route in (
            "generate_project_asset_references",
            "llm_chat",
            "llm_generate",
            "director_preparation_start",
            "director_generate_music",
            "llm_enhance_prompt",
            "llm_describe_image",
            "mix_audio",
            "director_plan_prompts",
            "director_plan_angle_prompts",
            "director_v2_plan",
            "plan_audio_structure",
            "director_classify_sections",
            "preview_generation_plan",
            "generate",
            "retake_video_endpoint",
            "edit_anything_endpoint",
            "repaint_endpoint",
            "recast_endpoint",
            "outpaint_endpoint",
            "blend_endpoint",
            "inpaint_endpoint",
            "tools_upscale",
            "tools_revoice",
            "_resume_recovered_job",
            "rejoin_clips",
        ):
            self.assertIn(
                'permission="project.generate"',
                self._function_source(route),
                route,
            )
        copy_variant = self._function_source("add_project_asset_variant")
        self.assertIn('permission="project.read"', copy_variant)
        for route in ("list_favorites", "list_outputs"):
            source = self._function_source(route)
            self.assertIn('permission="project.read"', source, route)
            self.assertNotIn('permission="project.generate"', source, route)

    def test_scoped_enhance_authorizes_before_provider_or_runtime_resolution(self):
        route = self._function_source("llm_enhance_prompt")
        self.assertLess(
            route.index("_promote_external_llm_request(request)"),
            route.index("_request_project_workspace("),
        )
        self.assertLess(
            route.index("_require_project_access("),
            route.index("_prompt_enhancement_runtime_snapshot("),
        )
        self.assertLess(
            route.index('globals().get("_resolve_prompt_enhancement_images")'),
            route.index("_prompt_enhancement_runtime_snapshot("),
        )
        self.assertIn("existing_only=True", route)
        runtime = self._function_source(
            "_prompt_enhancement_runtime_snapshot",
        )
        self.assertIn("_resolve_prompt_enhancer_runtime_selection(", runtime)
        resolver = self._function_source(
            "_resolve_prompt_enhancer_runtime_selection",
        )
        self.assertIn("_llm_model_catalog(request, configured_provider)", resolver)
        self.assertIn("_resolve_llm_chat_model(request, enhance_model)", resolver)
        self.assertIn("Configured Prompt Enhance model is unavailable", resolver)

    def test_llm_operation_identity_never_creates_and_changes_on_recreation(self):
        from fastapi import HTTPException

        with tempfile.TemporaryDirectory() as root:
            namespace = self._function_namespace(
                (
                    "_existing_workspace_dir",
                    "_require_project_access",
                    "_llm_project_instance_id",
                    "_llm_operation_scope",
                    "_llm_route_operation_scope_or_404",
                ),
                {
                    "Request": object,
                    "HTTPException": HTTPException,
                    "hashlib": hashlib,
                    "hmac": __import__("hmac"),
                    "os": os,
                    "stat": __import__("stat"),
                    "threading": threading,
                    "uuid": __import__("uuid"),
                    "wgp": types.SimpleNamespace(
                        server_config={"save_path": root},
                    ),
                    "_account_project_access_state": lambda: {
                        "state": "disabled", "enforced": False,
                    },
                    "_require_account_project_permission": (
                        lambda *_args, **_kwargs: None
                    ),
                    "_project_access": types.SimpleNamespace(
                        status=lambda *_args: types.SimpleNamespace(
                            protected=False, unlocked=True,
                        ),
                        authorize=lambda *_args: types.SimpleNamespace(
                            protected=False, unlocked=True,
                        ),
                    ),
                    "_STATE_CHANGING_METHODS": frozenset({"POST", "DELETE"}),
                    "_session_secret": lambda: b"project-instance-secret",
                    "_llm_project_instance_lock": threading.Lock(),
                    "_LLM_ROUTE_OPERATION_KINDS": frozenset({"enhance"}),
                    "_promote_external_llm_request": lambda _request: None,
                    "_request_project_workspace": (
                        lambda _request, workspace: workspace
                    ),
                },
            )
            request = types.SimpleNamespace(
                method="GET",
                state=types.SimpleNamespace(
                    maestro_remote=False,
                    maestro_session_id="owner-session",
                ),
            )
            missing = os.path.join(root, "missing-project")
            with self.assertRaises(HTTPException) as raised:
                namespace["_llm_route_operation_scope_or_404"](
                    request, "enhance", "missing-project",
                )
            self.assertEqual(raised.exception.status_code, 404)
            self.assertFalse(os.path.exists(missing))

            project = os.path.join(root, "project")
            os.mkdir(project)
            first = namespace["_llm_operation_scope"](
                request, "project",
            )[1]
            marker = os.path.join(project, ".llm-chat-instance")
            self.assertTrue(os.path.isfile(marker))
            os.remove(marker)

            os.mkdir(marker)
            with self.assertRaises(HTTPException) as nonregular:
                namespace["_llm_operation_scope"](request, "project")
            self.assertEqual(nonregular.exception.status_code, 500)
            os.rmdir(marker)

            shared_marker = os.path.join(root, "shared-marker")
            with open(shared_marker, "w", encoding="ascii") as handle:
                handle.write("c" * 32)
            try:
                os.link(shared_marker, marker)
            except OSError:
                pass
            else:
                with self.assertRaises(HTTPException) as hardlinked:
                    namespace["_llm_operation_scope"](request, "project")
                self.assertEqual(hardlinked.exception.status_code, 500)
                os.remove(marker)
            os.remove(shared_marker)

            os.rmdir(project)
            with self.assertRaises(HTTPException) as deleted:
                namespace["_llm_operation_scope"](request, "project")
            self.assertEqual(deleted.exception.status_code, 404)
            self.assertFalse(os.path.exists(project))

            os.mkdir(project)
            second = namespace["_llm_operation_scope"](
                request, "project",
            )[1]
            self.assertNotEqual(first, second)

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
                "_account_project_list_identities": lambda _request: None,
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
        self.assertIn('if remote or account_project_state["enforced"]', password)
        self.assertLess(
            password.index("_existing_workspace_dir(name)"),
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
        self.assertIn("authorized_jobs = [", queue)
        self.assertIn(
            "authorized_logical_queue_projection(\n"
            "        authorized_jobs, scheduler,",
            queue,
        )
        self.assertIn('logical_summary = dict(projection["summary"])', queue)
        self.assertNotIn('**scheduler["summary"]', queue)
        self.assertIn('"cpu_text_running": sum(', queue)
        self.assertIn('"cpu_text_waiting": sum(', queue)
        self.assertEqual(queue.count("for job in authorized_jobs"), 2)
        self.assertNotIn("aggregate_snapshot()", queue)
        self.assertLess(
            queue.index("_require_remote_queue_project(request)"),
            queue.index("queue_scheduler_snapshot(active_jobs)"),
        )
        self.assertLess(
            queue.index("queue_scheduler_snapshot(active_jobs)"),
            queue.index("_job_owned_by_request("),
        )
        self.assertLess(
            queue.index("_job_owned_by_request("),
            queue.index("authorized_logical_queue_projection("),
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
            "_require_account_project_permission": lambda *_args: None,
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

        project_access.status = lambda *_args: (_ for _ in ()).throw(
            AssertionError("active membership checked a project password")
        )
        namespace["_require_account_project_permission"] = (
            lambda *_args: {"state": "active"}
        )
        self.assertIsNone(gate(request))
        self.assertEqual(active_projects["owner"], "project")

        def deny_nonmember(*_args):
            raise FakeHTTPException(status_code=404, detail="Project not found")

        namespace["_require_account_project_permission"] = deny_nonmember
        with self.assertRaises(FakeHTTPException) as hidden:
            gate(request)
        self.assertEqual(hidden.exception.status_code, 404)
        self.assertNotIn("owner", active_projects)

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

        self.assertIn(
            "await createWorkspace(name, accountProjectAccessActive "
            "? undefined : newPassword || undefined)",
            selector,
        )
        self.assertIn(
            "const legacyProjectPasswordAccess = !accountProjectAccessActive",
            selector,
        )
        self.assertIn("newPassword.length > 0 && newPassword.length < 8", selector)
        self.assertIn("remote && !newPassword", selector)
        self.assertIn("Required password (8+ chars)", selector)
        self.assertIn("Create project", selector)
        self.assertIn("const needsProject = !activeWorkspace", generate)
        self.assertIn("Choose or create a project first.", generate)

    def test_remote_project_bootstrap_precedes_optional_welcome(self):
        app = APP_PATH.read_text(encoding="utf-8")
        selector = (
            ROOT / "ui/src/components/MainContent/MainContent.tsx"
        ).read_text(encoding="utf-8")
        welcome = (
            ROOT / "ui/src/components/WelcomeModal.tsx"
        ).read_text(encoding="utf-8")

        access_context = app.index("loadAccessContext(false)")
        account_gate = app.index("if (context.accounts?.enabled === true)")
        account_context = app.index("loadAccountContext(false)")
        workspaces = app.index("loadWorkspaces()")
        ready = app.index("setBootstrapState('ready')")
        self.assertIn("const loadWorkspaces = useStore(s => s.loadWorkspaces)", app)
        self.assertIn("await bootstrapWithin(\n          loadAccountContext(false)", app)
        self.assertIn("await bootstrapWithin(\n        loadWorkspaces()", app)
        self.assertLess(access_context, account_gate)
        self.assertLess(account_gate, account_context)
        self.assertLess(account_context, workspaces)
        self.assertLess(workspaces, ready)
        self.assertNotIn("api.fetchWorkspaces", app)
        self.assertIn("setBootstrapState('error')", app)
        self.assertIn("Try again", app)
        self.assertIn("BOOTSTRAP_TIMEOUT_MS", app)
        self.assertIn("taking too long to load your projects", app)
        self.assertIn("Checking your connection and projects", app)
        self.assertIn("couldn't open", app)
        self.assertIn(
            "!remoteProjectRequired && !accountAuthenticationRequired && <WelcomeModal />",
            app,
        )

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
        self.assertIn("Private previews start blurred", welcome)
        self.assertIn("Project access controls who can open the project", welcome)
        self.assertIn("Local access · on this computer", welcome)
        for capability in ("Supported generation controls", "Queue and resume", "Use Blender scenes"):
            self.assertIn(capability, welcome)
        self.assertIn("For supported models", welcome)
        self.assertIn("writing assistant chosen on the computer running", welcome)
        self.assertIn("remote access, and share links", welcome)
        self.assertIn(
            "the computer running {PRODUCT_NAME} downloads and prepares model files",
            welcome,
        )
        self.assertIn("Approved local and remote users can reuse them", welcome)
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
        self.assertIn("approved local and remote users can reuse them", text["welcome"])
        self.assertIn(
            "project access and private-preview settings still apply",
            text["welcome"],
        )
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
        self.assertIn("{machineControls && queue && <div", queue)
        self.assertIn("{machineControls && <>", queue)
        self.assertIn("info.held ? api.resumeQueueJob", queue)
        self.assertIn("job.recoveryActions?.includes(action)", queue)
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

    def test_director_remote_model_errors_identify_role_without_catalog_leaks(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        configured = {
            "configured": True,
            "enabled_models": [],
        }
        failure_codes = frozenset({
            "director_model_unavailable", "director_model_not_ready",
            "director_model_terms_required", "director_role_lora_unavailable",
            "director_reference_unavailable",
        })
        failure_components = frozenset({
            "video_model", "image_creator_model", "continuity_editor_model",
            "image_creator_lora", "continuity_editor_lora",
            "character_reference", "location_reference", "starting_image",
        })

        legal_detail = "A separate written MiniMax H3 license is required"

        def require_h3_legal(model_types):
            if any(
                str(model_type or "").startswith("minimax_h3")
                for model_type in model_types or ()
            ):
                raise FakeHTTPException(
                    status_code=451, detail=legal_detail,
                )

        namespace = self._function_namespace(
            (
                "_remote_visible_model_ids",
                "_require_remote_visible_models",
                "_director_component_error",
                "_director_require_visible_model",
            ),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_model_visibility_response": lambda: configured,
                "_require_h3_legal_execution": require_h3_legal,
                "H3_LEGAL_BLOCKED_DETAIL": legal_detail,
                "_DIRECTOR_FAILURE_CODES": failure_codes,
                "_DIRECTOR_FAILURE_COMPONENTS": failure_components,
            },
        )
        transports = {
            "local": types.SimpleNamespace(
                state=types.SimpleNamespace(
                    maestro_remote=False,
                    maestro_transport="local",
                ),
            ),
            "lan": types.SimpleNamespace(
                state=types.SimpleNamespace(
                    maestro_remote=True,
                    maestro_transport="lan",
                ),
            ),
            "stable": types.SimpleNamespace(
                state=types.SimpleNamespace(
                    maestro_remote=True,
                    maestro_transport="stable",
                ),
            ),
        }
        local = transports["local"]
        remote = transports["lan"]
        selected = (
            ("minimax_h3_pinkcherry_fl2va", "video_model"),
            ("krea2_moody_mix_v7_fp8", "image_creator_model"),
            ("qwen_image_edit_2511", "continuity_editor_model"),
        )
        require = namespace["_director_require_visible_model"]
        configured["enabled_models"] = [model for model, _component in selected]
        for model, component in selected:
            if model.startswith("minimax_h3"):
                for transport, request in transports.items():
                    with self.subTest(transport=transport):
                        with self.assertRaises(FakeHTTPException) as raised:
                            require(request, model, component)
                        self.assertEqual(raised.exception.status_code, 451)
                        self.assertEqual(raised.exception.detail, {
                            "code": "director_model_unavailable",
                            "component": component,
                            "message": legal_detail,
                        })
            else:
                require(remote, model, component)

        for omitted_model, omitted_component in selected:
            with self.subTest(omitted_component=omitted_component):
                configured["enabled_models"] = [
                    model for model, _component in selected
                    if model != omitted_model
                ]
                with self.assertRaises(FakeHTTPException) as raised:
                    require(remote, omitted_model, omitted_component)
                self.assertEqual(raised.exception.status_code, 404)
                self.assertEqual(raised.exception.detail, {
                    "code": "director_model_unavailable",
                    "component": omitted_component,
                    "message": "Selected Director model is unavailable in this session.",
                })
                if omitted_model.startswith("minimax_h3"):
                    with self.assertRaises(FakeHTTPException) as local_error:
                        require(local, omitted_model, omitted_component)
                    self.assertEqual(local_error.exception.status_code, 451)
                else:
                    require(local, omitted_model, omitted_component)

        configured["enabled_models"] = []
        public_errors = []
        for model in (selected[0][0], "unknown-private-model-id"):
            with self.assertRaises(FakeHTTPException) as raised:
                require(remote, model, "video_model")
            public_errors.append(raised.exception.detail)
        self.assertEqual(public_errors[0], public_errors[1])

    def test_director_reference_404_remains_distinct_from_model_404(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        failure_codes = frozenset({
            "director_model_unavailable", "director_model_not_ready",
            "director_model_terms_required", "director_role_lora_unavailable",
            "director_reference_unavailable",
        })
        failure_components = frozenset({
            "video_model", "image_creator_model", "continuity_editor_model",
            "image_creator_lora", "continuity_editor_lora",
            "character_reference", "location_reference", "starting_image",
        })
        namespace = self._function_namespace(
            ("_director_component_error", "_authorize_director_media_inputs"),
            {
                "Request": object,
                "HTTPException": FakeHTTPException,
                "_DIRECTOR_FAILURE_CODES": failure_codes,
                "_DIRECTOR_FAILURE_COMPONENTS": failure_components,
                "_get_active_workspace": lambda: "project",
                "_require_project_access": lambda request, workspace: None,
                "_director_image_role_wire_mode": lambda body: "roles",
                "_director_require_visible_model": lambda *args: None,
                "_require_remote_visible_models": lambda *args: None,
                "_resolve_authorized_request_media": lambda *args: None,
            },
        )
        for field, value, component in (
            ("reference_image_path", "opaque-selection", "starting_image"),
            ("character_ref_paths", ["opaque-character"], "character_reference"),
            ("location_ref_paths", ["opaque-location"], "location_reference"),
        ):
            with self.subTest(component=component):
                body = {
                    "video_model": "minimax_h3_pinkcherry_fl2va",
                    "image_creator_model": "krea2_moody_mix_v7_fp8",
                    "image_editor_model": "qwen_image_edit_2511",
                    field: value,
                }
                with self.assertRaises(FakeHTTPException) as raised:
                    namespace["_authorize_director_media_inputs"](
                        types.SimpleNamespace(state=types.SimpleNamespace()),
                        body,
                        component_errors=True,
                    )
                self.assertEqual(raised.exception.status_code, 404)
                self.assertEqual(raised.exception.detail, {
                    "code": "director_reference_unavailable",
                    "component": component,
                    "message": "Selected Director reference is unavailable.",
                })

        start = self._function_source("director_pipeline_start")
        resolver = self._function_source("_resolve_director_image_role_request")
        self.assertIn('body["_director_component_errors"] = True', start)
        self.assertIn('body.pop("_director_component_errors", None)', start)
        self.assertIn("_director_component_error_response(error)", start)
        self.assertIn("if component_errors:", resolver)
        self.assertIn("_director_validate_workflow_or_raise(body)", resolver)
        self.assertLess(
            start.index("_resolve_director_image_role_request(request, body)"),
            start.index("except HTTPException as error"),
        )

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

    def test_cuda_audio_analysis_waits_for_classic_native_gpu_lane(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        analysis_entered = threading.Event()
        release_analysis = threading.Event()
        progress = types.ModuleType("services.audio_analysis")

        def analyze(**_kwargs):
            analysis_entered.set()
            release_analysis.wait(2)
            return {"duration": 1.0}

        progress.analyze = analyze
        services = types.ModuleType("services")
        services.audio_analysis = progress
        native_gpu = threading.Lock()

        class NativeSlot:
            def __init__(self, enabled=True, **_kwargs):
                self.enabled = enabled
                self.acquired = False

            def __enter__(self):
                if self.enabled:
                    native_gpu.acquire()
                    self.acquired = True
                return self.acquired

            def __exit__(self, *_args):
                if self.acquired:
                    native_gpu.release()
                    self.acquired = False

        generation_lock = threading.Lock()
        namespace = self._function_namespace(
            (
                "_audio_analysis_workspace", "_audio_analysis_owner_key",
                "analyze_audio",
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
                "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(
                    is_available=lambda: True,
                    empty_cache=lambda: None,
                )),
                "wgp": types.SimpleNamespace(wan_model=None),
                "_WgpNativeGpuExecutionSlot": NativeSlot,
                "_release_wgp_model_with_native_gpu_exclusion": (
                    lambda: self.fail("unexpected model release")
                ),
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
                "_gen_lock": generation_lock,
            },
        )

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
                native_gpu.acquire()
                task = asyncio.create_task(
                    namespace["analyze_audio"](Request())
                )
                for _ in range(200):
                    if generation_lock.locked():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(generation_lock.locked())
                self.assertFalse(analysis_entered.is_set())
                native_gpu.release()
                self.assertTrue(
                    await asyncio.to_thread(analysis_entered.wait, 1)
                )
                release_analysis.set()
                result = await task
                self.assertEqual(result["duration"], 1.0)
                self.assertFalse(generation_lock.locked())
                self.assertFalse(native_gpu.locked())

        try:
            asyncio.run(exercise())
        finally:
            release_analysis.set()
            if native_gpu.locked():
                native_gpu.release()

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
                    "_require_account_project_permission": (
                        lambda *_args, **_kwargs: None
                    ),
                    "_account_project_access_state": lambda: {
                        "state": "disabled", "enforced": False,
                    },
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

    def test_active_delete_keeps_missing_and_nonmember_targets_opaque(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        def deny_nonmember(*_args, **_kwargs):
            raise FakeHTTPException(status_code=404, detail="Project not found")

        with tempfile.TemporaryDirectory() as root:
            existing = os.path.join(root, "existing-project")
            os.mkdir(existing)
            aliases = []
            try:
                os.symlink(existing, os.path.join(root, "project-alias"))
                aliases.append("project-alias")
            except (OSError, NotImplementedError):
                pass
            namespace = self._function_namespace(
                ("_safe_join", "delete_workspace"),
                {
                    "Request": object,
                    "HTTPException": FakeHTTPException,
                    "os": os,
                    "wgp": types.SimpleNamespace(
                        server_config={"save_path": root},
                    ),
                    "_workspace_creation_lock": threading.RLock(),
                    "_account_project_access_state": lambda: {
                        "state": "active", "enforced": True,
                    },
                    "_require_account_project_permission": deny_nonmember,
                },
            )
            request = types.SimpleNamespace(state=types.SimpleNamespace(
                maestro_remote=True,
                maestro_session_id="nonmember",
            ))

            errors = []
            for name in (
                "missing-project", "existing-project", *aliases,
            ):
                with self.assertRaises(FakeHTTPException) as raised:
                    namespace["delete_workspace"](name, request)
                errors.append(
                    (raised.exception.status_code, raised.exception.detail)
                )
            self.assertEqual(
                errors,
                [(404, "Project not found")] * (2 + len(aliases)),
            )


if __name__ == "__main__":
    unittest.main()
