"""Versioned host-notice authority and route-contract regressions."""

from __future__ import annotations

import ast
import asyncio
import copy
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.host_terms import (  # noqa: E402
    HOST_TERMS_CONFIG_KEY,
    LAWFUL_USE_TERM,
    REF2VA_TERM,
    StaleHostTermVersionError,
    UnknownHostTermError,
    accept_host_term,
    host_term_accepted,
    host_terms_status,
)


class HostTermsServiceTests(unittest.TestCase):
    def test_empty_and_stale_records_do_not_accept_current_versions(self):
        empty = host_terms_status({})
        self.assertFalse(empty[LAWFUL_USE_TERM]["accepted"])
        self.assertFalse(empty[REF2VA_TERM]["accepted"])

        stale = host_terms_status({
            HOST_TERMS_CONFIG_KEY: {
                LAWFUL_USE_TERM: {
                    "version": 0,
                    "accepted_at": "2026-08-08T00:00:00Z",
                },
            },
        })
        self.assertEqual(stale[LAWFUL_USE_TERM]["accepted_version"], 0)
        self.assertFalse(stale[LAWFUL_USE_TERM]["accepted"])

    def test_legacy_nsfw_timestamp_maps_only_to_lawful_use_v1(self):
        services = {"nsfw_accepted_at": "2026-08-08T00:00:00Z"}
        status = host_terms_status(services)
        self.assertTrue(status[LAWFUL_USE_TERM]["accepted"])
        self.assertEqual(status[LAWFUL_USE_TERM]["accepted_version"], 1)
        self.assertFalse(status[REF2VA_TERM]["accepted"])

    def test_acceptance_is_exact_version_idempotent_and_identity_free(self):
        services = {}
        first = accept_host_term(
            services,
            REF2VA_TERM,
            1,
            accepted_at="2026-08-09T01:02:03Z",
        )
        second = accept_host_term(
            services,
            REF2VA_TERM,
            1,
            accepted_at="2026-08-09T09:09:09Z",
        )
        self.assertTrue(first[REF2VA_TERM]["accepted"])
        self.assertEqual(second[REF2VA_TERM]["accepted_at"], "2026-08-09T01:02:03Z")
        self.assertEqual(
            services[HOST_TERMS_CONFIG_KEY][REF2VA_TERM],
            {"version": 1, "accepted_at": "2026-08-09T01:02:03Z"},
        )
        self.assertNotIn("session", repr(services).lower())
        self.assertNotIn("workspace", repr(services).lower())
        self.assertNotIn("user", repr(services).lower())

    def test_unknown_stale_and_boolean_versions_are_rejected(self):
        with self.assertRaises(UnknownHostTermError):
            accept_host_term({}, "not-a-document", 1)
        for version in (0, 2, True, "1", None):
            with self.subTest(version=version):
                with self.assertRaises(StaleHostTermVersionError):
                    accept_host_term({}, LAWFUL_USE_TERM, version)

    def test_policy_helper_requires_exact_current_version(self):
        services = {}
        self.assertFalse(host_term_accepted(services, LAWFUL_USE_TERM))
        accept_host_term(
            services,
            LAWFUL_USE_TERM,
            1,
            accepted_at="2026-08-09T01:02:03Z",
        )
        self.assertTrue(host_term_accepted(services, LAWFUL_USE_TERM))


class HostTermsRouteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (APP_ROOT / "launch.py").read_text(encoding="utf-8")
        cls.module = ast.parse(cls.source)

    class FakeHTTPException(Exception):
        def __init__(self, *, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class FakeRequest:
        def __init__(self, body, *, remote=False):
            self.body = body
            self.state = SimpleNamespace(maestro_remote=remote)

        async def json(self):
            return self.body

    def _endpoint(self, name, *, server_config, atomic_write):
        node = copy.deepcopy(next(
            item for item in self.module.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        ))
        node.decorator_list = []
        namespace = {
            "Request": object,
            "HTTPException": self.FakeHTTPException,
            "copy": copy,
            "threading": threading,
            "_services_config_lock": threading.RLock(),
            "_request_project_workspace": lambda _request, workspace: workspace or "default",
            "_existing_workspace_dir": lambda workspace: f"/existing/{workspace}",
            "_require_project_access": lambda _request, workspace: f"/existing/{workspace}",
            "_require_host_terms_project_access": lambda _request, workspace: f"/existing/{workspace}",
            "_atomic_write_json": atomic_write,
            "_mask_key": lambda value: value,
            "wgp": SimpleNamespace(
                server_config=server_config,
                server_config_filename="/config.json",
            ),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP_ROOT / "launch.py"), "exec"), namespace)
        return namespace[name]

    def test_status_and_acceptance_require_existing_project_authority(self):
        routes = self.source[
            self.source.index("def _require_host_terms_project_access"):
            self.source.index('@api.get("/api/v1/access-context")')
        ]
        self.assertEqual(routes.count("_require_project_access(request,"), 1)
        self.assertEqual(routes.count("_existing_workspace_dir("), 1)
        self.assertEqual(routes.count("_require_host_terms_project_access(request,"), 2)
        self.assertIn("_request_project_workspace(request, workspace)", routes)
        self.assertIn('_request_project_workspace(request, body.get("workspace"))', routes)
        self.assertIn("_services_config_lock", routes)
        self.assertIn("_atomic_write_json", routes)
        self.assertNotIn("maestro_session_id", routes)

    def test_fresh_local_default_is_known_without_creating_other_projects(self):
        node = copy.deepcopy(next(
            item for item in self.module.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_require_host_terms_project_access"
        ))
        node.decorator_list = []
        existing_calls = []
        namespace = {
            "Request": object,
            "_existing_workspace_dir": lambda workspace: existing_calls.append(workspace),
            "_require_project_access": lambda _request, workspace: f"/authorized/{workspace}",
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP_ROOT / "launch.py"), "exec"), namespace)
        require = namespace["_require_host_terms_project_access"]

        self.assertEqual(
            require(self.FakeRequest({}, remote=False), "default"),
            "/authorized/default",
        )
        self.assertEqual(existing_calls, [])
        require(self.FakeRequest({}, remote=False), "named")
        require(self.FakeRequest({}, remote=True), "default")
        self.assertEqual(existing_calls, ["named", "default"])

    def test_acceptance_publish_rolls_back_when_persistence_fails(self):
        config = {"services": {}}
        original = copy.deepcopy(config)

        def fail_write(*_args):
            raise OSError("disk full")

        endpoint = self._endpoint(
            "accept_host_terms",
            server_config=config,
            atomic_write=fail_write,
        )
        with self.assertRaisesRegex(OSError, "disk full"):
            asyncio.run(endpoint(self.FakeRequest({
                "workspace": "project",
                "term": LAWFUL_USE_TERM,
                "version": 1,
            })))
        self.assertEqual(config, original)

    def test_rejected_or_failed_services_updates_do_not_partially_publish(self):
        initial = {
            "services": {
                "llm_model_id": "old",
                "llm_provider": "local",
                "nsfw_mode": False,
            },
        }
        rejected_config = copy.deepcopy(initial)
        endpoint = self._endpoint(
            "update_services_config",
            server_config=rejected_config,
            atomic_write=lambda *_args: self.fail("rejected update must not persist"),
        )
        with self.assertRaises(self.FakeHTTPException) as raised:
            asyncio.run(endpoint(self.FakeRequest({
                "llm_model_id": "new",
                "nsfw_mode": True,
            })))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(rejected_config, initial)

        failed_config = copy.deepcopy(initial)

        def fail_write(*_args):
            raise OSError("read only")

        endpoint = self._endpoint(
            "update_services_config",
            server_config=failed_config,
            atomic_write=fail_write,
        )
        with self.assertRaisesRegex(OSError, "read only"):
            asyncio.run(endpoint(self.FakeRequest({"llm_model_id": "new"})))
        self.assertEqual(failed_config, initial)

    def test_services_update_canonicalizes_public_provider_before_coercion(self):
        config = {
            "services": {
                "llm_provider": "local",
                "nsfw_mode": True,
                HOST_TERMS_CONFIG_KEY: {
                    LAWFUL_USE_TERM: {
                        "version": 1,
                        "accepted_at": "2026-08-09T01:02:03Z",
                    },
                },
            },
        }
        persisted = []
        endpoint = self._endpoint(
            "update_services_config",
            server_config=config,
            atomic_write=lambda _path, value: persisted.append(copy.deepcopy(value)),
        )
        asyncio.run(endpoint(self.FakeRequest({"llm_provider": " Anthropic "})))
        self.assertEqual(config["services"]["llm_provider"], "anthropic")
        self.assertFalse(config["services"]["nsfw_mode"])
        self.assertEqual(persisted[-1], config)

    def test_host_acceptance_is_remote_capable_but_general_services_put_is_not(self):
        local_only = self.source[
            self.source.index("_REMOTE_LOCAL_ONLY_EXACT ="):
            self.source.index("def _remote_local_only_denial")
        ]
        self.assertIn('(\"PUT\", \"/api/v1/services-config\")', local_only)
        self.assertNotIn("host-terms", local_only)

    def test_browser_cannot_write_legacy_acceptance_timestamp(self):
        update = self.source[
            self.source.index('@api.put("/api/v1/services-config")'):
            self.source.index("# API Routes: Workspaces")
        ]
        allowed = update[update.index("ALLOWED_KEYS ="):update.index("with _services_config_lock")]
        self.assertNotIn("nsfw_accepted_at", allowed)
        self.assertIn("host_term_accepted", update)


if __name__ == "__main__":
    unittest.main()
