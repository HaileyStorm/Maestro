"""Behavioral launch-route contracts for the account-bound Support facade."""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH_PATH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from fastapi import HTTPException
from services.account_auth import AccountAuthError, AccountAuthStore
from services.entitlements import (
    ContributionConflict,
    ContributionLedger,
    EntitlementError,
    LedgerIntegrityError,
    ManualContributionConflict,
)
from services.responsible_use import (
    ResponsibleUseError,
    StaleResponsibleUseNoticeError,
)
from services.support_catalog import SupportCatalogError, load_support_catalog
from services.support_portal import (
    ResponsibleUseAcceptanceStore,
    ResponsibleUseStoreIntegrityError,
    SupportAuthorizationError,
    SupportPortal,
    SupportPortalError,
)


def _launch_nodes(*names: str, decorators: bool = False) -> tuple[ast.Module, Path]:
    tree = ast.parse(LAUNCH_PATH.read_text(encoding="utf-8"), filename=str(LAUNCH_PATH))
    selected = []
    for node in tree.body:
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ) and node.name in names:
            copied = copy.deepcopy(node)
            if not decorators:
                copied.decorator_list = []
            selected.append(copied)
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise AssertionError(f"Launch symbols not found: {sorted(missing)}")
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    return module, LAUNCH_PATH


class _Response:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code
        self.headers = {}


class _Request:
    def __init__(self, body=None):
        self._body = {} if body is None else body
        self.json_calls = 0
        self.state = types.SimpleNamespace(
            maestro_session_id="b" * 32,
            maestro_account_session_id="a" * 32,
            maestro_account_principal={"id": "1" * 32, "role": "user"},
            maestro_remote=True,
        )
        self.method = "GET"
        self.url = types.SimpleNamespace(path="/api/v1/support/self")
        self.headers = {}

    async def json(self):
        self.json_calls += 1
        return self._body


class _Portal:
    def __init__(self):
        self.calls = []

    def public_catalog_projection(self):
        self.calls.append(("catalog",))
        return {
            "provider_catalog": {
                "providers": [{
                    "provider_id": "buy_me_a_coffee",
                    "state": "disabled",
                    "support_url": None,
                }],
            },
            "benefit_availability": {
                "scheduler_enforcement_enabled": False,
                "effective_benefits": [],
            },
        }

    def self_projection(self, session_id, *, remote):
        self.calls.append(("self", session_id, remote))
        return {
            "account_support": {"recorded": {"event_count": 0}},
            "responsible_use": {
                "status": {"accepted": False, "state": "not_accepted"},
            },
        }

    def accept_responsible_use(
        self,
        session_id,
        *,
        remote,
        document_version,
        content_sha256,
    ):
        self.calls.append((
            "accept", session_id, remote, document_version, content_sha256,
        ))
        return {"accepted": True, "state": "accepted"}

    def owner_admin_projection(
        self,
        session_id,
        *,
        remote,
        target_account_id,
    ):
        self.calls.append(("admin", session_id, remote, target_account_id))
        return {
            "account_support": {
                "recorded": {"subject_key": "key_" + "f" * 64},
            },
        }

    def transition_owner_fulfillment(
        self,
        session_id,
        *,
        remote,
        target_account_id,
        target_event_id,
        item,
        status,
        idempotency_key,
        proof_reference,
    ):
        self.calls.append((
            "fulfillment", session_id, remote, target_account_id,
            target_event_id, item, status, idempotency_key, proof_reference,
        ))
        return {
            "account_support": {
                "recorded": {
                    "subject_key": "key_" + "f" * 64,
                    "fulfillment": [{"status": status}],
                },
            },
        }

    def record_owner_contribution(
        self,
        session_id,
        *,
        remote,
        target_account_id,
        source,
        kind,
        amount_minor,
        currency,
        target_event_id,
        idempotency_key,
    ):
        self.calls.append((
            "contribution", session_id, remote, target_account_id, source,
            kind, amount_minor, currency, target_event_id, idempotency_key,
        ))
        return {
            "account_support": {
                "recorded": {
                    "subject_key": "key_" + "f" * 64,
                    "currency_totals_minor": {currency: amount_minor},
                },
            },
        }


class SupportPortalRouteTests(unittest.TestCase):
    @staticmethod
    def _route_namespace(portal):
        names = (
            "_account_request_body",
            "_support_request_context",
            "get_support_catalog",
            "get_account_support",
            "get_account_responsible_use",
            "accept_account_responsible_use",
            "get_admin_account_support",
            "transition_admin_account_fulfillment",
            "record_admin_account_contribution",
        )
        module, path = _launch_nodes(*names)
        namespace = {
            "asyncio": asyncio,
            "Request": object,
            "HTTPException": HTTPException,
            "AccountAuthError": AccountAuthError,
            "EntitlementError": EntitlementError,
            "ResponsibleUseError": ResponsibleUseError,
            "SupportCatalogError": SupportCatalogError,
            "SupportPortalError": SupportPortalError,
            "_require_support_portal": lambda _request: portal,
            "_public_support_catalog_projection": portal.public_catalog_projection,
            "_require_account_principal": lambda request: (
                request.state.maestro_account_principal
            ),
            "_raise_support_http_error": lambda error: (_ for _ in ()).throw(error),
        }
        exec(compile(module, str(path), "exec"), namespace)
        return namespace

    def test_routes_register_exact_public_self_acceptance_and_admin_paths(self):
        names = (
            "get_support_catalog",
            "get_account_support",
            "get_account_responsible_use",
            "accept_account_responsible_use",
            "get_admin_account_support",
            "transition_admin_account_fulfillment",
            "record_admin_account_contribution",
        )
        module, path = _launch_nodes(*names, decorators=True)

        class _Api:
            def __init__(self):
                self.routes = []

            def get(self, route):
                return self._register("GET", route)

            def post(self, route):
                return self._register("POST", route)

            def _register(self, method, route):
                def register(function):
                    self.routes.append((method, route, function.__name__))
                    return function
                return register

        api = _Api()
        namespace = {"api": api, "Request": object}
        exec(compile(module, str(path), "exec"), namespace)
        self.assertEqual(set(api.routes), {
            ("GET", "/api/v1/support/catalog", "get_support_catalog"),
            ("GET", "/api/v1/support/self", "get_account_support"),
            (
                "GET", "/api/v1/support/responsible-use",
                "get_account_responsible_use",
            ),
            (
                "POST", "/api/v1/support/responsible-use/accept",
                "accept_account_responsible_use",
            ),
            (
                "GET", "/api/v1/support/admin/accounts/{account_id}",
                "get_admin_account_support",
            ),
            (
                "POST",
                "/api/v1/support/admin/accounts/{account_id}/fulfillment",
                "transition_admin_account_fulfillment",
            ),
            (
                "POST",
                "/api/v1/support/admin/accounts/{account_id}/contributions",
                "record_admin_account_contribution",
            ),
        })

    def test_route_envelopes_use_only_live_account_session_and_preserve_browser(self):
        portal = _Portal()
        namespace = self._route_namespace(portal)
        request = _Request({
            "document_version": 1,
            "content_sha256": "d" * 64,
        })
        browser_session = request.state.maestro_session_id

        catalog = namespace["get_support_catalog"]()
        self_projection = namespace["get_account_support"](request)
        responsible = namespace["get_account_responsible_use"](request)
        accepted = asyncio.run(
            namespace["accept_account_responsible_use"](request)
        )
        admin = namespace["get_admin_account_support"]("2" * 32, request)
        request._body = {
            "target_event_id": "evt_" + "3" * 32,
            "item": "one_time_credit_grant",
            "status": "pending",
            "idempotency_key": "key_" + "9" * 64,
            "proof_reference": None,
        }
        fulfillment = asyncio.run(
            namespace["transition_admin_account_fulfillment"](
                "2" * 32, request,
            )
        )
        request._body = {
            "source": "patreon",
            "kind": "one_time_contribution",
            "amount_minor": 500,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": "key_" + "8" * 64,
        }
        contribution = asyncio.run(
            namespace["record_admin_account_contribution"](
                "2" * 32, request,
            )
        )

        self.assertEqual(catalog["provider_catalog"]["providers"][0]["state"], "disabled")
        self.assertEqual(self_projection["account_support"]["recorded"]["event_count"], 0)
        self.assertFalse(responsible["status"]["accepted"])
        self.assertTrue(accepted["status"]["accepted"])
        self.assertRegex(
            admin["account_support"]["recorded"]["subject_key"],
            r"^key_[0-9a-f]{64}$",
        )
        self.assertEqual(
            fulfillment["account_support"]["recorded"]["fulfillment"][0][
                "status"
            ],
            "pending",
        )
        self.assertEqual(
            contribution["account_support"]["recorded"][
                "currency_totals_minor"
            ],
            {"USD": 500},
        )
        self.assertEqual(request.state.maestro_session_id, browser_session)
        self.assertEqual(portal.calls, [
            ("catalog",),
            ("self", "a" * 32, True),
            ("self", "a" * 32, True),
            ("accept", "a" * 32, True, 1, "d" * 64),
            ("admin", "a" * 32, True, "2" * 32),
            (
                "fulfillment", "a" * 32, True, "2" * 32,
                "evt_" + "3" * 32, "one_time_credit_grant", "pending",
                "key_" + "9" * 64, None,
            ),
            (
                "contribution", "a" * 32, True, "2" * 32, "patreon",
                "one_time_contribution", 500, "USD", None,
                "key_" + "8" * 64,
            ),
        ])

    def test_fulfillment_body_is_exact_and_rejects_client_derived_fields(self):
        portal = _Portal()
        namespace = self._route_namespace(portal)
        valid = {
            "target_event_id": "evt_" + "3" * 32,
            "item": "one_time_credit_grant",
            "status": "pending",
            "idempotency_key": "key_" + "9" * 64,
            "proof_reference": None,
        }
        for body in (
            {key: value for key, value in valid.items() if key != "proof_reference"},
            {**valid, "provider": "client_provider"},
            {**valid, "actor_key": "key_" + "f" * 64},
            {**valid, "notes": "private text"},
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                asyncio.run(namespace["transition_admin_account_fulfillment"](
                    "2" * 32, _Request(body),
                ))
            self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(portal.calls, [])

    def test_manual_contribution_body_is_exact_and_rejects_derived_fields(self):
        portal = _Portal()
        namespace = self._route_namespace(portal)
        valid = {
            "source": "buy_me_a_coffee",
            "kind": "one_time_contribution",
            "amount_minor": 500,
            "currency": "USD",
            "target_event_id": None,
            "idempotency_key": "key_" + "9" * 64,
        }
        for body in (
            {key: value for key, value in valid.items() if key != "target_event_id"},
            {**valid, "occurred_at": "2020-01-01T00:00:00Z"},
            {**valid, "actor_key": "key_" + "f" * 64},
            {**valid, "notes": "private text"},
            {**valid, "contract_reference": "private"},
            {**valid, "email": "private@example.test"},
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                asyncio.run(namespace["record_admin_account_contribution"](
                    "2" * 32, _Request(body),
                ))
            self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(portal.calls, [])

    def test_acceptance_body_is_exact_and_rejects_client_subjects(self):
        portal = _Portal()
        namespace = self._route_namespace(portal)
        for body in (
            {"document_version": 1},
            {
                "document_version": 1,
                "content_sha256": "d" * 64,
                "subject_key": "key_" + "f" * 64,
            },
            {
                "document_version": 1,
                "content_sha256": "d" * 64,
                "email": "private@example.test",
            },
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                asyncio.run(namespace["accept_account_responsible_use"](_Request(body)))
            self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(portal.calls, [])

    def test_support_mutation_requires_exact_origin_before_body_read(self):
        module, path = _launch_nodes("_reject_cross_origin_mutation")
        namespace = {
            "_STATE_CHANGING_METHODS": frozenset({"POST", "PUT", "PATCH", "DELETE"}),
            "JSONResponse": _Response,
            "Request": object,
            "_account_exact_origin_allowed": lambda request: request.headers.get("origin")
            == "https://maestro.example",
            "_remote_sharing_enabled": lambda: True,
            "_request_external_origins": lambda _request: {"https://maestro.example"},
            "_approved_local_origin": lambda _origin: False,
            "_canonical_http_origin": lambda value, allow_path=False: value,
            "_matches_verified_stable_redirect_origin": lambda *_args: False,
        }
        exec(compile(module, str(path), "exec"), namespace)
        reject = namespace["_reject_cross_origin_mutation"]
        request = _Request({"email": "must-not-be-read@example.test"})
        request.method = "POST"
        request.url.path = "/api/v1/support/responsible-use/accept"
        request.headers = {"origin": "https://evil.example"}
        denial = reject(request)
        self.assertEqual(denial.status_code, 403)
        self.assertEqual(
            denial.body,
            {"detail": "Support changes require this app's exact origin"},
        )
        self.assertEqual(request.json_calls, 0)
        request.headers = {"origin": "https://maestro.example"}
        self.assertIsNone(reject(request))

        fulfillment_request = _Request({"notes": "must-not-be-read"})
        fulfillment_request.method = "POST"
        fulfillment_request.url.path = (
            "/api/v1/support/admin/accounts/" + "1" * 32 + "/fulfillment"
        )
        fulfillment_request.headers = {"origin": "https://evil.example"}
        fulfillment_denial = reject(fulfillment_request)
        self.assertEqual(fulfillment_denial.status_code, 403)
        self.assertEqual(fulfillment_request.json_calls, 0)

        contribution_request = _Request({"notes": "must-not-be-read"})
        contribution_request.method = "POST"
        contribution_request.url.path = (
            "/api/v1/support/admin/accounts/" + "1" * 32 + "/contributions"
        )
        contribution_request.headers = {"origin": "https://evil.example"}
        contribution_denial = reject(contribution_request)
        self.assertEqual(contribution_denial.status_code, 403)
        self.assertEqual(contribution_request.json_calls, 0)

        account_request = _Request()
        account_request.method = "POST"
        account_request.url.path = "/api/v1/account/login"
        account_request.headers = {"origin": "https://evil.example"}
        account_denial = reject(account_request)
        self.assertEqual(
            account_denial.body,
            {"detail": "Account changes require this app's exact origin"},
        )

    def test_public_catalog_middleware_is_sessionless_and_transport_identical(self):
        module, path = _launch_nodes("_maestro_session_middleware")
        namespace = {
            "Request": object,
            "_request_is_cloudflare_remote": lambda request: (
                request.headers.get("x-test-transport") == "cloudflare"
            ),
            "_research_local_only_denial": lambda _request: None,
            "_local_recovery_control_denial": lambda _request: None,
            "_reject_cross_origin_mutation": lambda _request: None,
            "_remote_local_only_denial": lambda _request: None,
            "_REMOTE_OWNER_REAUTH_ALLOWED_EXACT": frozenset(),
            "_call_next_with_recovery_no_store": (
                lambda request, call_next: _call_next_and_stamp(
                    request, call_next,
                )
            ),
        }
        exec(compile(module, str(path), "exec"), namespace)
        async def _call_next_and_stamp(actual_request, downstream):
            response = await downstream(actual_request)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        responses = []
        for transport in ("loopback", "lan", "cloudflare"):
            request = _Request()
            request.url.path = "/api/v1/support/catalog"
            request.headers = {"x-test-transport": transport}
            observed = {}

            async def call_next(actual_request):
                observed["browser"] = actual_request.state.maestro_session_id
                observed["account"] = (
                    actual_request.state.maestro_account_session_id
                )
                observed["principal"] = (
                    actual_request.state.maestro_account_principal
                )
                return _Response({"catalog": "same"})

            response = asyncio.run(
                namespace["_maestro_session_middleware"](request, call_next)
            )
            self.assertEqual(observed, {
                "browser": "",
                "account": "",
                "principal": None,
            })
            self.assertEqual(
                response.headers["Cache-Control"], "private, no-store",
            )
            self.assertEqual(response.headers["Pragma"], "no-cache")
            self.assertNotIn("Set-Cookie", response.headers)
            responses.append((
                response.status_code,
                response.body,
                dict(response.headers),
            ))
        self.assertEqual(responses[1:], [responses[0], responses[0]])

        async def error_response(_actual_request):
            return _Response({"detail": "unavailable"}, status_code=503)

        request = _Request()
        request.url.path = "/api/v1/support/catalog"
        response = asyncio.run(
            namespace["_maestro_session_middleware"](request, error_response)
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")

    def test_every_support_success_and_error_response_is_private_no_store(self):
        module, path = _launch_nodes(
            "_recovery_response_requires_no_store",
            "_stamp_recovery_no_store_response",
        )
        namespace = {"Request": object, "Response": object}
        exec(compile(module, str(path), "exec"), namespace)
        for route in (
            "/api/v1/support/catalog",
            "/api/v1/support/self",
            "/api/v1/support/responsible-use/accept",
            "/api/v1/support/admin/accounts/" + "1" * 32,
        ):
            for status_code in (200, 400, 401, 403, 409, 503):
                response = _Response({}, status_code=status_code)
                request = types.SimpleNamespace(
                    url=types.SimpleNamespace(path=route),
                )
                stamped = namespace["_stamp_recovery_no_store_response"](
                    request, response,
                )
                self.assertEqual(
                    stamped.headers["Cache-Control"], "private, no-store",
                )
                self.assertEqual(stamped.headers["Pragma"], "no-cache")

    def test_safe_error_mapping_is_bounded_and_never_reflects_private_details(self):
        module, path = _launch_nodes("_raise_support_http_error")
        namespace = {
            "HTTPException": HTTPException,
            "AccountAuthError": AccountAuthError,
            "EntitlementError": EntitlementError,
            "ContributionConflict": ContributionConflict,
            "ManualContributionConflict": ManualContributionConflict,
            "LedgerIntegrityError": LedgerIntegrityError,
            "ResponsibleUseError": ResponsibleUseError,
            "StaleResponsibleUseNoticeError": StaleResponsibleUseNoticeError,
            "SupportAuthorizationError": SupportAuthorizationError,
            "SupportCatalogError": SupportCatalogError,
            "SupportPortalError": SupportPortalError,
            "ResponsibleUseStoreIntegrityError": ResponsibleUseStoreIntegrityError,
            "_raise_account_http_error": lambda error: (_ for _ in ()).throw(error),
        }
        exec(compile(module, str(path), "exec"), namespace)
        cases = (
            (SupportAuthorizationError("Recent owner authentication is required"), 403),
            (StaleResponsibleUseNoticeError("private notice detail"), 409),
            (ResponsibleUseStoreIntegrityError("/private/store/path"), 503),
            (OSError("/private/store/path"), 503),
            (SupportCatalogError("private@example.test?token=secret"), 503),
            (SupportPortalError("private malformed value"), 400),
            (ContributionConflict("private conflict detail"), 409),
            (ManualContributionConflict("private manual conflict"), 409),
        )
        for error, status in cases:
            with self.subTest(error=type(error).__name__), self.assertRaises(
                HTTPException,
            ) as raised:
                namespace["_raise_support_http_error"](error)
            self.assertEqual(raised.exception.status_code, status)
            serialized = json.dumps(raised.exception.detail)
            for private in ("/private/store/path", "private@example.test", "token=secret"):
                self.assertNotIn(private, serialized)

    def test_lazy_factory_uses_account_store_and_domain_separated_host_keys(self):
        module, path = _launch_nodes(
            "_support_domain_key",
            "_support_catalog_config_path",
            "_load_server_support_catalog",
            "_support_portal",
        )
        secret = b"server-identity-secret-for-route-tests" * 2
        account_store = object()
        captured = {}

        class _CatalogLoader:
            def __call__(self, **kwargs):
                captured["catalog_kwargs"] = kwargs
                return "catalog"

        class _Ledger:
            def __init__(self, *, integrity_key):
                captured["ledger_key"] = integrity_key

        class _Acceptance:
            def __init__(self, *, integrity_key):
                captured["acceptance_key"] = integrity_key

        class _Support:
            def __init__(self, **kwargs):
                captured["portal"] = kwargs

        namespace = {
            "os": os,
            "hmac": hmac,
            "hashlib": hashlib,
            "_app_dir": str(APP),
            "_session_secret": lambda: secret,
            "_account_auth_store": lambda: account_store,
            "_support_portal_value": None,
            "_support_portal_lock": threading.Lock(),
            "load_support_catalog": _CatalogLoader(),
            "ContributionLedger": _Ledger,
            "ResponsibleUseAcceptanceStore": _Acceptance,
            "SupportPortal": _Support,
        }
        with mock.patch.dict(os.environ, {}, clear=True):
            exec(compile(module, str(path), "exec"), namespace)
            portal = namespace["_support_portal"]()
        self.assertIs(portal, namespace["_support_portal_value"])
        self.assertIs(captured["portal"]["account_store"], account_store)
        self.assertNotIn("catalog", captured["portal"])
        self.assertIs(
            captured["portal"]["catalog_loader"],
            namespace["_load_server_support_catalog"],
        )
        self.assertEqual(captured["portal"]["catalog_loader"](), "catalog")
        self.assertEqual(captured["catalog_kwargs"], {})
        keys = {
            captured["ledger_key"],
            captured["acceptance_key"],
            captured["portal"]["identity_key"],
        }
        self.assertEqual(len(keys), 3)
        self.assertTrue(all(len(key) == 32 for key in keys))

    def test_disabled_factory_does_not_initialize_catalog_or_stores(self):
        module, path = _launch_nodes("_support_portal")
        created = []

        def forbidden(*_args, **_kwargs):
            created.append(True)
            raise AssertionError("disabled Support must not initialize stores")

        namespace = {
            "_account_auth_store": lambda: None,
            "_support_portal_value": None,
            "_support_portal_lock": threading.Lock(),
            "_support_catalog_config_path": forbidden,
            "load_support_catalog": forbidden,
            "ContributionLedger": forbidden,
            "ResponsibleUseAcceptanceStore": forbidden,
            "SupportPortal": forbidden,
        }
        exec(compile(module, str(path), "exec"), namespace)
        self.assertIsNone(namespace["_support_portal"]())
        self.assertEqual(created, [])

    def test_public_catalog_is_account_independent_truthful_and_has_no_outbound(self):
        module, path = _launch_nodes(
            "_PublicSupportCatalogProjectionAdapter",
            "_support_catalog_config_path",
            "_load_server_support_catalog",
            "_public_support_catalog_projection",
            "_raise_support_http_error",
            "get_support_catalog",
        )
        forbidden_calls = []

        def forbidden(*_args, **_kwargs):
            forbidden_calls.append(True)
            raise AssertionError("public catalog touched account-bound state")

        with tempfile.TemporaryDirectory() as directory:
            namespace = {
                "os": os,
                "_app_dir": str(APP),
                "load_support_catalog": load_support_catalog,
                "SupportPortal": SupportPortal,
                "_account_auth_store": forbidden,
                "ContributionLedger": forbidden,
                "ResponsibleUseAcceptanceStore": forbidden,
                "HTTPException": HTTPException,
                "AccountAuthError": AccountAuthError,
                "ContributionConflict": ContributionConflict,
                "ManualContributionConflict": ManualContributionConflict,
                "EntitlementError": EntitlementError,
                "LedgerIntegrityError": LedgerIntegrityError,
                "ResponsibleUseError": ResponsibleUseError,
                "StaleResponsibleUseNoticeError": StaleResponsibleUseNoticeError,
                "ResponsibleUseStoreIntegrityError": (
                    ResponsibleUseStoreIntegrityError
                ),
                "SupportAuthorizationError": SupportAuthorizationError,
                "SupportCatalogError": SupportCatalogError,
                "SupportPortalError": SupportPortalError,
                "_raise_account_http_error": lambda error: (
                    (_ for _ in ()).throw(error)
                ),
            }
            exec(compile(module, str(path), "exec"), namespace)
            disabled_config = str(Path(directory) / "missing-support.json")
            base_env = {
                "MAESTRO_ACCOUNTS_ENABLED": "false",
                "MAESTRO_SUPPORT_CATALOG_PATH": disabled_config,
            }
            egress_targets = (
                "socket.create_connection",
                "socket.socket",
                "os.system",
                "subprocess.run",
                "subprocess.Popen",
                "urllib.request.urlopen",
                "requests.sessions.Session.request",
            )

            def project_with_env(environment):
                with ExitStack() as stack:
                    stack.enter_context(mock.patch.dict(
                        os.environ, environment, clear=True,
                    ))
                    for target in egress_targets:
                        stack.enter_context(mock.patch(
                            target, side_effect=AssertionError("outbound call"),
                        ))
                    return namespace["get_support_catalog"]()

            disabled = project_with_env(base_env)
            unconfigured = project_with_env({
                **base_env,
                "MAESTRO_SUPPORT_PATREON_ENABLED": "true",
            })
            available = project_with_env({
                **base_env,
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": (
                    "true"
                ),
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                    "https://support.operator.com/maestro"
                ),
            })
            malformed_env = {
                **base_env,
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": (
                    "true"
                ),
                "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                    "https://[bad"
                ),
            }
            with self.assertRaises(HTTPException) as malformed:
                project_with_env(malformed_env)

            secret = b"support-route-projection-parity-secret" * 2
            catalog = load_support_catalog(
                env={
                    key: value for key, value in {
                        **base_env,
                        "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_ENABLED": (
                            "true"
                        ),
                        "MAESTRO_SUPPORT_DIRECT_COMPUTE_SPONSORSHIP_URL": (
                            "https://support.operator.com/maestro"
                        ),
                    }.items()
                    if key != "MAESTRO_ACCOUNTS_ENABLED"
                },
                local_config_path=disabled_config,
            )
            expected = SupportPortal(
                account_store=AccountAuthStore(
                    str(Path(directory) / "parity-account.json"), secret,
                ),
                ledger=ContributionLedger(
                    Path(directory) / "parity-ledger.json",
                    integrity_key=secret,
                    allow_test_path=True,
                ),
                acceptance_store=ResponsibleUseAcceptanceStore(
                    Path(directory) / "parity-acceptance.json",
                    integrity_key=secret,
                    allow_test_path=True,
                ),
                identity_key=secret,
                catalog=catalog,
            ).public_catalog_projection()

        def provider(projection, provider_id):
            return next(
                item for item in projection["provider_catalog"]["providers"]
                if item["provider_id"] == provider_id
            )

        self.assertEqual(
            [
                item["provider_id"]
                for item in disabled["provider_catalog"]["providers"]
            ],
            [
                "buy_me_a_coffee",
                "patreon",
                "direct_compute_sponsorship",
            ],
        )
        self.assertEqual(
            provider(unconfigured, "patreon")["state"], "unconfigured",
        )
        direct = provider(available, "direct_compute_sponsorship")
        self.assertEqual(direct["state"], "available")
        self.assertEqual(
            direct["support_url"], "https://support.operator.com/maestro",
        )
        self.assertFalse(available["benefit_availability"][
            "scheduler_enforcement_enabled"
        ])
        self.assertEqual(available, expected)
        self.assertEqual(malformed.exception.status_code, 503)
        malformed_detail = json.dumps(malformed.exception.detail)
        self.assertNotIn("https://[bad", malformed_detail)
        self.assertNotIn(disabled_config, malformed_detail)
        self.assertEqual(forbidden_calls, [])


if __name__ == "__main__":
    unittest.main()
