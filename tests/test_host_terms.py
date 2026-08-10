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
    BFL_FLUX1_REVIEW_TERM,
    BFL_FLUX2_REVIEW_TERM,
    CIVITAI_PORNMASTER_V4_CREATOR_TERM,
    CURRENT_HOST_TERM_BINDINGS,
    CURRENT_HOST_TERM_VERSIONS,
    HOST_TERMS_CONFIG_KEY,
    LAWFUL_USE_TERM,
    KREA2_REVIEW_TERM,
    KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
    KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH,
    KREA2_MOODY_CUTIE_V4_RECIPE_ID,
    KREA2_MOODY_MIX_V7_CREATOR_TERM,
    KREA2_MOODY_MIX_V7_RECIPE_GRAPH,
    KREA2_MOODY_MIX_V7_RECIPE_ID,
    PONPOKE_FLUX2_KLEIN4B_TERM,
    PONPOKE_FLUX2_KLEIN9B_TERM,
    PORNMASTER_V4_RECIPE_GRAPH,
    PORNMASTER_V4_RECIPE_ID,
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

        for term in (
            PONPOKE_FLUX2_KLEIN4B_TERM,
            PONPOKE_FLUX2_KLEIN9B_TERM,
            CIVITAI_PORNMASTER_V4_CREATOR_TERM,
            KREA2_MOODY_MIX_V7_CREATOR_TERM,
            KREA2_MOODY_CUTIE_V4_CREATOR_TERM,
        ):
            with self.subTest(term=term):
                with self.assertRaises(StaleHostTermVersionError):
                    accept_host_term({}, term, 0)

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

    def test_bound_term_requires_exact_license_repository_and_revision(self):
        for term in (
            BFL_FLUX1_REVIEW_TERM,
            BFL_FLUX2_REVIEW_TERM,
            KREA2_REVIEW_TERM,
        ):
            with self.subTest(term=term):
                services = {}
                accept_host_term(
                    services,
                    term,
                    CURRENT_HOST_TERM_VERSIONS[term],
                    accepted_at="2026-08-10T00:00:00Z",
                )
                record = services[HOST_TERMS_CONFIG_KEY][term]
                self.assertEqual(
                    record["binding"],
                    CURRENT_HOST_TERM_BINDINGS[term],
                )
                self.assertTrue(host_term_accepted(services, term))

                record["binding"]["revision"] = "stale"
                status = host_terms_status(services)[term]
                self.assertFalse(status["accepted"])
                self.assertEqual(
                    status["binding"],
                    CURRENT_HOST_TERM_BINDINGS[term],
                )

    def test_krea_notice_v2_and_moody_creator_bindings_are_exact(self):
        with self.assertRaises(StaleHostTermVersionError):
            accept_host_term({}, KREA2_REVIEW_TERM, 1)
        self.assertEqual(CURRENT_HOST_TERM_VERSIONS[KREA2_REVIEW_TERM], 2)
        expected = {
            KREA2_MOODY_MIX_V7_CREATOR_TERM: (
                KREA2_MOODY_MIX_V7_RECIPE_ID,
                KREA2_MOODY_MIX_V7_RECIPE_GRAPH,
                2731187,
                3209007,
                3090691,
                "moodyKrea2Mix_v70.safetensors",
                "405DB6A1D060075D176C3578063B6FA2FEB07B58BB61DDB403DDBA0669A35A6D",
            ),
            KREA2_MOODY_CUTIE_V4_CREATOR_TERM: (
                KREA2_MOODY_CUTIE_V4_RECIPE_ID,
                KREA2_MOODY_CUTIE_V4_RECIPE_GRAPH,
                2764429,
                3211049,
                3092831,
                "moodyCutieMixKrea2_v40.safetensors",
                "6C54D783AAAAB1A6924FAFCFA3AFA9F36ABE72A59723D424E932484A8C98316A",
            ),
        }
        for term, (
            recipe_id, graph, model_id, version_id, file_id, filename, sha256,
        ) in expected.items():
            with self.subTest(term=term):
                binding = CURRENT_HOST_TERM_BINDINGS[term]
                self.assertEqual(binding["recipe_graph"], graph)
                self.assertEqual(graph["model_type"], recipe_id)
                self.assertEqual(graph["required_host_terms"], [
                    term, KREA2_REVIEW_TERM,
                ])
                self.assertEqual(graph["required_host_term_versions"], {
                    term: 1,
                    KREA2_REVIEW_TERM: 2,
                })
                self.assertEqual(binding["creator"], "catlover1937")
                self.assertEqual(binding["model_id"], model_id)
                self.assertEqual(binding["model_version_id"], version_id)
                self.assertEqual(binding["file_id"], file_id)
                self.assertEqual(binding["filename"], filename)
                self.assertEqual(binding["file_size_bytes"], 14125457032)
                self.assertEqual(binding["file_sha256"], sha256)
                self.assertEqual(binding["creator_restrictions"], {
                    "allowNoCredit": False,
                    "allowDerivatives": False,
                    "allowCommercialUse": ["RentCivit"],
                })
                services = {}
                accept_host_term(
                    services, term, 1,
                    accepted_at="2026-08-10T00:00:00Z",
                )
                services[HOST_TERMS_CONFIG_KEY][term]["binding"][
                    "file_id"
                ] = 0
                self.assertFalse(host_term_accepted(services, term))

    def test_bound_acceptance_requires_binding_and_stores_only_public_metadata(self):
        for term, binding in CURRENT_HOST_TERM_BINDINGS.items():
            with self.subTest(term=term):
                missing_binding = {
                    HOST_TERMS_CONFIG_KEY: {
                        term: {
                            "version": CURRENT_HOST_TERM_VERSIONS[term],
                            "accepted_at": "2026-08-10T00:00:00Z",
                        },
                    },
                }
                self.assertFalse(host_term_accepted(missing_binding, term))

                services = {}
                accept_host_term(
                    services, term, CURRENT_HOST_TERM_VERSIONS[term],
                    accepted_at="2026-08-10T00:00:00Z",
                )
                record = services[HOST_TERMS_CONFIG_KEY][term]
                self.assertEqual(set(record), {"version", "accepted_at", "binding"})
                self.assertEqual(record["binding"], binding)
                serialized = repr(record).lower()
                for private_key in (
                    "user", "session", "workspace", "project", "prompt",
                    "media", "access_token", "/home/", "/media/",
                ):
                    self.assertNotIn(private_key, serialized)

    def test_flux2_binding_covers_each_gated_upstream_recipe_revision(self):
        binding = CURRENT_HOST_TERM_BINDINGS[BFL_FLUX2_REVIEW_TERM]
        self.assertEqual(
            binding["covered_repositories"],
            [
                {
                    "repository": "black-forest-labs/FLUX.2-dev",
                    "revision": "0cb56aa",
                },
                {
                    "repository": "black-forest-labs/FLUX.2-klein-9B",
                    "revision": "07c5ac6",
                },
            ],
        )

    def test_pornmaster_creator_binding_is_exact_and_permission_narrow(self):
        binding = CURRENT_HOST_TERM_BINDINGS[
            CIVITAI_PORNMASTER_V4_CREATOR_TERM
        ]
        self.assertEqual(binding["creator"], "iamddtla")
        self.assertEqual(binding["model_id"], 2382648)
        self.assertEqual(binding["model_version_id"], 2973304)
        self.assertEqual(
            binding["source_url"],
            "https://civitai.com/models/2382648?modelVersionId=2973304",
        )
        self.assertEqual(
            binding["file_sha256"],
            "E90EEB50140A10806341B7521C340214C6F76CEC2F8F8DAE7A443C5806072DF7",
        )
        self.assertEqual(binding["file_size_bytes"], 9433104872)
        self.assertEqual(binding["creator_restrictions"], {
            "allowNoCredit": False,
            "allowDerivatives": True,
            "allowCommercialUse": ["RentCivit"],
        })
        self.assertEqual(
            binding["underlying_base_license"], "FLUX non-commercial",
        )
        graph = binding["recipe_graph"]
        self.assertEqual(graph, PORNMASTER_V4_RECIPE_GRAPH)
        self.assertEqual(graph["model_type"], PORNMASTER_V4_RECIPE_ID)
        self.assertEqual(graph["required_host_terms"], [
            CIVITAI_PORNMASTER_V4_CREATOR_TERM,
            BFL_FLUX2_REVIEW_TERM,
            PONPOKE_FLUX2_KLEIN9B_TERM,
        ])
        self.assertEqual(
            (graph["base"]["repository"], graph["base"]["revision"]),
            ("black-forest-labs/FLUX.2-klein-9B", "07c5ac6"),
        )
        self.assertEqual(
            (graph["encoder"]["repository"], graph["encoder"]["revision"]),
            (
                "ponpoke/flux2-klein-9b-uncensored-text-encoder",
                "fba36e796aac081246708dd30392a401ba44922e",
            ),
        )

        services = {}
        accept_host_term(
            services,
            CIVITAI_PORNMASTER_V4_CREATOR_TERM,
            1,
            accepted_at="2026-08-10T00:00:00Z",
        )
        record = services[HOST_TERMS_CONFIG_KEY][
            CIVITAI_PORNMASTER_V4_CREATOR_TERM
        ]
        record["binding"]["model_version_id"] = 0
        self.assertFalse(
            host_term_accepted(
                services, CIVITAI_PORNMASTER_V4_CREATOR_TERM,
            ),
        )

    def test_nested_binding_metadata_is_copied_before_storage_and_status(self):
        services = {}
        accept_host_term(
            services,
            BFL_FLUX2_REVIEW_TERM,
            1,
            accepted_at="2026-08-10T00:00:00Z",
        )
        stored = services[HOST_TERMS_CONFIG_KEY][BFL_FLUX2_REVIEW_TERM]
        stored["binding"]["covered_repositories"][1]["revision"] = "stale"
        self.assertEqual(
            CURRENT_HOST_TERM_BINDINGS[BFL_FLUX2_REVIEW_TERM][
                "covered_repositories"
            ][1]["revision"],
            "07c5ac6",
        )
        self.assertFalse(host_term_accepted(services, BFL_FLUX2_REVIEW_TERM))

        status = host_terms_status({})[BFL_FLUX2_REVIEW_TERM]
        status["binding"]["covered_repositories"][1]["revision"] = "client"
        self.assertEqual(
            CURRENT_HOST_TERM_BINDINGS[BFL_FLUX2_REVIEW_TERM][
                "covered_repositories"
            ][1]["revision"],
            "07c5ac6",
        )


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

    def _endpoint(
        self,
        name,
        *,
        server_config,
        atomic_write,
        reconcile=lambda **_kwargs: None,
        workspace_lock=None,
        require_access=None,
    ):
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
            "_workspace_lifecycle_lock": workspace_lock or threading.RLock(),
            "_request_project_workspace": lambda _request, workspace: workspace or "default",
            "_existing_workspace_dir": lambda workspace: f"/existing/{workspace}",
            "_require_project_access": lambda _request, workspace: f"/existing/{workspace}",
            "_require_host_terms_project_access": (
                require_access
                or (lambda _request, workspace: f"/existing/{workspace}")
            ),
            "_atomic_write_json": atomic_write,
            "_reconcile_ref2va_waiting_plan_reviews": reconcile,
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
        self.assertEqual(routes.count("_require_project_access("), 1)
        self.assertIn("existing_only=True", routes)
        self.assertNotIn("_existing_workspace_dir(", routes)
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
        access_calls = []

        def require_access(_request, workspace, *, existing_only=False):
            access_calls.append((workspace, existing_only))
            return f"/authorized/{workspace}"

        namespace = {
            "Request": object,
            "_workspace_lifecycle_lock": threading.RLock(),
            "_require_workspace_not_deleting": lambda workspace: None,
            "_require_project_access": require_access,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP_ROOT / "launch.py"), "exec"), namespace)
        require = namespace["_require_host_terms_project_access"]

        self.assertEqual(
            require(self.FakeRequest({}, remote=False), "default"),
            "/authorized/default",
        )
        self.assertEqual(access_calls, [("default", True)])
        require(self.FakeRequest({}, remote=False), "named")
        require(self.FakeRequest({}, remote=True), "default")
        self.assertEqual(
            access_calls,
            [("default", True), ("named", True), ("default", True)],
        )

    def test_deletion_first_rejects_acceptance_without_persisting(self):
        config = {"services": {}}
        writes = []

        def deleting(_request, _workspace):
            raise self.FakeHTTPException(
                status_code=409, detail="This project is being deleted",
            )

        endpoint = self._endpoint(
            "accept_host_terms",
            server_config=config,
            atomic_write=lambda *_args: writes.append(True),
            require_access=deleting,
        )
        with self.assertRaises(self.FakeHTTPException) as raised:
            asyncio.run(endpoint(self.FakeRequest({
                "workspace": "project",
                "term": LAWFUL_USE_TERM,
                "version": 1,
            })))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(writes, [])
        self.assertEqual(config, {"services": {}})

        helper = self.source[
            self.source.index("def _require_host_terms_project_access"):
            self.source.index('@api.get("/api/v1/host-terms")')
        ]
        self.assertLess(
            helper.index("_require_workspace_not_deleting(workspace)"),
            helper.index("_require_project_access("),
        )

    def test_acceptance_holds_project_lifecycle_fence_through_persistence(self):
        config = {"services": {}}
        lifecycle_lock = threading.RLock()
        deletion_entered = threading.Event()
        deletion_threads = []

        def attempt_delete():
            with lifecycle_lock:
                deletion_entered.set()

        def atomic_write(_path, _staged):
            thread = threading.Thread(target=attempt_delete, daemon=True)
            deletion_threads.append(thread)
            thread.start()
            self.assertFalse(
                deletion_entered.wait(0.05),
                "project deletion entered before terms persistence completed",
            )

        endpoint = self._endpoint(
            "accept_host_terms",
            server_config=config,
            atomic_write=atomic_write,
            workspace_lock=lifecycle_lock,
        )
        result = asyncio.run(endpoint(self.FakeRequest({
            "workspace": "project",
            "term": LAWFUL_USE_TERM,
            "version": 1,
        })))
        for thread in deletion_threads:
            thread.join(timeout=1)
        self.assertTrue(deletion_entered.is_set())
        self.assertTrue(result["terms"][LAWFUL_USE_TERM]["accepted"])

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

    def test_ref2va_acceptance_reconciles_only_after_durable_publish(self):
        config = {"services": {}}
        events = []

        def atomic_write(_path, staged):
            self.assertTrue(
                staged["services"][HOST_TERMS_CONFIG_KEY][REF2VA_TERM]
            )
            events.append("persisted")

        def reconcile(*, request, workspace):
            self.assertIsInstance(request, self.FakeRequest)
            self.assertEqual(workspace, "project")
            self.assertTrue(
                host_term_accepted(config["services"], REF2VA_TERM)
            )
            self.assertEqual(events[-1], "persisted")
            events.append("reconciled")

        endpoint = self._endpoint(
            "accept_host_terms",
            server_config=config,
            atomic_write=atomic_write,
            reconcile=reconcile,
        )
        result = asyncio.run(endpoint(self.FakeRequest({
            "workspace": "project",
            "term": REF2VA_TERM,
            "version": 1,
        })))
        self.assertEqual(events, ["persisted", "reconciled"])
        self.assertTrue(result["terms"][REF2VA_TERM]["accepted"])

        # Idempotent retries intentionally reconcile again so a crash between
        # host persistence and queue persistence can heal without extending
        # deadlines already armed by the lifecycle fence.
        asyncio.run(endpoint(self.FakeRequest({
            "workspace": "project",
            "term": REF2VA_TERM,
            "version": 1,
        })))
        self.assertEqual(events.count("reconciled"), 2)

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
