"""CPU-only behavioral contracts for account-backed project sharing routes."""

from __future__ import annotations

import ast
import asyncio
import copy
import sys
import types
import unittest
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH = APP / "launch.py"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from fastapi import HTTPException
from services.account_auth import (
    AccountAuthError,
    AccountAuthStore,
    AccountStoreCorruptError,
)
from services.account_project_membership import (
    AccountProjectMembershipStore,
    ProjectMembershipConflictError,
    ProjectMembershipError,
    ProjectMembershipNotFoundError,
    ProjectMembershipStoreUnavailableError,
)


ROUTE_SYMBOLS = (
    "_account_request_body",
    "_project_membership_route_context",
    "_project_membership_projection",
    "_project_membership_expected_revision",
    "_project_membership_authorized_revision",
    "_project_membership_role",
    "_bind_project_member",
    "list_project_members",
    "add_project_member",
    "set_project_member",
    "remove_project_member",
)


def _launch_nodes(*names: str, decorators: bool = False) -> tuple[ast.Module, Path]:
    tree = ast.parse(LAUNCH.read_text(encoding="utf-8"), filename=str(LAUNCH))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            copied = copy.deepcopy(node)
            if not decorators:
                copied.decorator_list = []
            selected.append(copied)
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise AssertionError(f"Launch symbols not found: {sorted(missing)}")
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    return module, LAUNCH


class _Request:
    def __init__(self, body=None, *, recent=True, remote=False):
        self._body = {} if body is None else body
        self.state = types.SimpleNamespace(
            maestro_account_principal={
                "id": "a" * 32,
                "role": "user",
                "recently_reauthenticated": recent,
            },
            maestro_account_session_id="s" * 32,
            maestro_remote=remote,
        )

    async def json(self):
        return self._body


class _AuthStore:
    def __init__(self):
        self.accounts = {
            "a" * 32: {
                "id": "a" * 32,
                "username": "owner",
                "role": "owner",
                "disabled": False,
                "created_at": 1.0,
                "has_email": True,
            },
            "b" * 32: {
                "id": "b" * 32,
                "username": "editor",
                "role": "user",
                "disabled": False,
                "created_at": 2.0,
                "has_email": True,
            },
            "c" * 32: {
                "id": "c" * 32,
                "username": "disabled-user",
                "role": "user",
                "disabled": True,
                "created_at": 3.0,
                "has_email": False,
            },
        }
        self.resolve_calls = []
        self.username_resolve_calls = []

    def resolve_account(self, account_id):
        self.resolve_calls.append(account_id)
        account = self.accounts.get(account_id)
        return None if account is None else dict(account)

    def resolve_account_username(self, username):
        self.username_resolve_calls.append(username)
        if not isinstance(username, str):
            raise AccountAuthError("Username is required.", code="invalid_username")
        normalized = unicodedata.normalize("NFKC", username)
        if normalized != normalized.strip() or not 3 <= len(normalized) <= 64:
            raise AccountAuthError("Username is invalid.", code="invalid_username")
        key = normalized.casefold()
        account = next(
            (
                item for item in self.accounts.values()
                if item["username"].casefold() == key
            ),
            None,
        )
        return None if account is None else dict(account)

    def list_accounts(self, _session_id):
        raise AssertionError("project membership routes must not enumerate accounts")


class _MembershipStore:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.bind_calls = []
        self.unbind_calls = []
        self.error = None

    def bind(self, account_id, role, **kwargs):
        self.bind_calls.append((account_id, role, kwargs))
        if self.error is not None:
            raise self.error
        updated = copy.deepcopy(self.record)
        current = next(
            (item for item in updated["bindings"] if item["account_id"] == account_id),
            None,
        )
        if current is None:
            updated["bindings"].append({"account_id": account_id, "role": role})
        else:
            current["role"] = role
        updated["revision"] += 1
        self.record = updated
        return copy.deepcopy(updated)

    def unbind(self, account_id, **kwargs):
        self.unbind_calls.append((account_id, kwargs))
        if self.error is not None:
            raise self.error
        updated = copy.deepcopy(self.record)
        updated["bindings"] = [
            item for item in updated["bindings"] if item["account_id"] != account_id
        ]
        updated["revision"] += 1
        self.record = updated
        return copy.deepcopy(updated)


class ProjectMembershipRouteTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "project_instance": "project:v1:" + "1" * 64,
            "state": "active",
            "revision": 7,
            "bindings": [
                {"account_id": "a" * 32, "role": "owner"},
                {"account_id": "b" * 32, "role": "editor"},
            ],
        }
        self.auth_store = _AuthStore()
        self.membership_store = _MembershipStore(self.record)
        self.permission_calls = []
        self.permission_error = None
        self.state = {"enforced": True}
        self.workspace_error = None

    def _namespace(self):
        module, path = _launch_nodes(*ROUTE_SYMBOLS)

        def existing_workspace(workspace):
            if self.workspace_error is not None:
                raise self.workspace_error
            return "/synthetic/projects/" + workspace

        def require_permission(request, workspace_dir, permission, *, state):
            self.permission_calls.append((request, workspace_dir, permission, state))
            if self.permission_error is not None:
                raise self.permission_error
            return copy.deepcopy(self.record)

        namespace = {
            "asyncio": asyncio,
            "Request": object,
            "HTTPException": HTTPException,
            "AccountAuthError": AccountAuthError,
            "AccountAuthStore": AccountAuthStore,
            "AccountStoreCorruptError": AccountStoreCorruptError,
            "AccountProjectMembershipStore": AccountProjectMembershipStore,
            "ProjectMembershipError": ProjectMembershipError,
            "ProjectMembershipConflictError": ProjectMembershipConflictError,
            "ProjectMembershipNotFoundError": ProjectMembershipNotFoundError,
            "ProjectMembershipStoreUnavailableError": ProjectMembershipStoreUnavailableError,
            "_require_account_store": lambda _request: self.auth_store,
            "_account_project_access_state": lambda: dict(self.state),
            "_existing_workspace_dir": existing_workspace,
            "_require_account_project_permission": require_permission,
            "_request_has_recent_account_reauth": lambda request: bool(
                request.state.maestro_account_principal.get("recently_reauthenticated")
            ),
            "_account_project_membership_store": lambda: self.membership_store,
            "_raise_account_http_error": lambda error: (_ for _ in ()).throw(error),
            "_raise_project_setup_unavailable": lambda error: (_ for _ in ()).throw(
                HTTPException(status_code=503, detail="Project account access is unavailable")
            ),
        }
        exec(compile(module, str(path), "exec"), namespace)
        return namespace

    def test_routes_register_only_scoped_member_paths(self):
        module, path = _launch_nodes(
            "list_project_members",
            "add_project_member",
            "set_project_member",
            "remove_project_member",
            decorators=True,
        )

        class _Api:
            def __init__(self):
                self.routes = []

            def get(self, route):
                return self._register("GET", route)

            def put(self, route):
                return self._register("PUT", route)

            def post(self, route):
                return self._register("POST", route)

            def delete(self, route):
                return self._register("DELETE", route)

            def _register(self, method, route):
                def register(function):
                    self.routes.append((method, route, function.__name__))
                    return function
                return register

        api = _Api()
        namespace = {
            "api": api,
            "Request": object,
        }
        exec(compile(module, str(path), "exec"), namespace)
        self.assertEqual(api.routes, [
            (
                "GET", "/api/v1/workspaces/{workspace}/members",
                "list_project_members",
            ),
            (
                "POST", "/api/v1/workspaces/{workspace}/members",
                "add_project_member",
            ),
            (
                "PUT", "/api/v1/workspaces/{workspace}/members/{account_id}",
                "set_project_member",
            ),
            (
                "DELETE", "/api/v1/workspaces/{workspace}/members/{account_id}",
                "remove_project_member",
            ),
        ])

    def test_list_requires_exact_manage_permission_and_projects_only_bound_accounts(self):
        namespace = self._namespace()
        request = _Request(remote=True)
        result = namespace["list_project_members"]("film", request)
        self.assertEqual(
            self.permission_calls[0][1:],
            (
                "/synthetic/projects/film",
                "project.membership.manage",
                {"enforced": True},
            ),
        )
        self.assertEqual(result, {
            "workspace": "film",
            "revision": 7,
            "members": [
                {"account_id": "a" * 32, "username": "owner", "role": "owner"},
                {"account_id": "b" * 32, "username": "editor", "role": "editor"},
            ],
        })
        self.assertEqual(self.auth_store.resolve_calls, ["a" * 32, "b" * 32])
        self.assertNotIn("created_at", result["members"][0])
        self.assertNotIn("has_email", result["members"][0])
        self.assertNotIn("disabled", result["members"][0])

    def test_context_hides_inactive_and_invalid_projects_as_not_found(self):
        namespace = self._namespace()
        self.state = {"enforced": False}
        with self.assertRaises(HTTPException) as inactive:
            namespace["list_project_members"]("film", _Request())
        self.assertEqual(inactive.exception.status_code, 404)

        self.state = {"enforced": True}
        self.workspace_error = HTTPException(status_code=400, detail="bad name")
        with self.assertRaises(HTTPException) as invalid:
            namespace["list_project_members"]("../film", _Request())
        self.assertEqual(invalid.exception.status_code, 404)
        self.assertEqual(invalid.exception.detail, "Project not found")

        self.workspace_error = None
        self.permission_error = HTTPException(status_code=404, detail="Project not found")
        with self.assertRaises(HTTPException) as unauthorized:
            namespace["list_project_members"]("film", _Request())
        self.assertEqual(unauthorized.exception.status_code, 404)

    def test_set_member_requires_recent_reauth_and_exact_body(self):
        namespace = self._namespace()
        with self.assertRaises(HTTPException) as stale:
            asyncio.run(namespace["set_project_member"](
                "film", "b" * 32,
                _Request({"role": "viewer", "expected_revision": 7}, recent=False),
            ))
        self.assertEqual(stale.exception.status_code, 403)
        self.assertEqual(self.membership_store.bind_calls, [])

        for body in (
            {"role": "viewer"},
            {"role": "viewer", "expected_revision": 7, "nonce": "unused"},
            {"role": "admin", "expected_revision": 7},
            {"role": ["viewer"], "expected_revision": 7},
            {"role": "viewer", "expected_revision": True},
            {"role": "viewer", "expected_revision": 0},
        ):
            with self.subTest(body=body), self.assertRaises(HTTPException) as invalid:
                asyncio.run(namespace["set_project_member"](
                    "film", "b" * 32, _Request(body),
                ))
            self.assertEqual(invalid.exception.status_code, 400)
        self.assertEqual(self.membership_store.bind_calls, [])

    def test_add_member_resolves_one_exact_username_for_ordinary_project_owner(self):
        namespace = self._namespace()
        for username in ("EDITOR", "ｅｄｉｔｏｒ"):
            with self.subTest(username=username):
                self.membership_store.bind_calls.clear()
                self.membership_store.record = copy.deepcopy(self.record)
                result = asyncio.run(namespace["add_project_member"](
                    "film",
                    _Request({
                        "username": username,
                        "role": "viewer",
                        "expected_revision": 7,
                    }, remote=True),
                ))
                self.assertEqual(self.membership_store.bind_calls, [(
                    "b" * 32,
                    "viewer",
                    {
                        "project_instance": self.record["project_instance"],
                        "expected_revision": 7,
                    },
                )])
                self.assertEqual(result["revision"], 8)
        self.assertEqual(self.auth_store.username_resolve_calls, [
            "EDITOR", "ｅｄｉｔｏｒ",
        ])
        self.assertTrue(all(
            request.state.maestro_account_principal["role"] == "user"
            for request, _directory, _permission, _state in self.permission_calls
        ))

    def test_add_member_rejects_revision_not_bound_to_authorized_record(self):
        namespace = self._namespace()
        with self.assertRaises(HTTPException) as conflict:
            asyncio.run(namespace["add_project_member"](
                "film",
                _Request({
                    "username": "editor",
                    "role": "viewer",
                    "expected_revision": 8,
                }),
            ))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(
            conflict.exception.detail,
            "Project members changed; refresh and try again",
        )
        self.assertEqual(self.auth_store.username_resolve_calls, [])
        self.assertEqual(self.membership_store.bind_calls, [])

    def test_add_member_hides_unknown_disabled_and_invalid_usernames(self):
        namespace = self._namespace()
        bodies = (
            {"username": "missing", "role": "viewer", "expected_revision": 7},
            {"username": "disabled-user", "role": "viewer", "expected_revision": 7},
            {"username": " editor ", "role": "viewer", "expected_revision": 7},
        )
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(HTTPException) as missing:
                asyncio.run(namespace["add_project_member"](
                    "film", _Request(body),
                ))
            self.assertEqual(missing.exception.status_code, 404)
            self.assertEqual(missing.exception.detail, "Account not found")
        self.assertEqual(self.membership_store.bind_calls, [])

        with self.assertRaises(HTTPException) as extra:
            asyncio.run(namespace["add_project_member"](
                "film",
                _Request({
                    "username": "editor",
                    "role": "viewer",
                    "expected_revision": 7,
                    "query": "ed",
                }),
            ))
        self.assertEqual(extra.exception.status_code, 400)

    def test_set_member_binds_exact_existing_active_account_and_revision(self):
        namespace = self._namespace()
        result = asyncio.run(namespace["set_project_member"](
            "film",
            "b" * 32,
            _Request({"role": "viewer", "expected_revision": 7}, remote=True),
        ))
        self.assertEqual(self.membership_store.bind_calls, [(
            "b" * 32,
            "viewer",
            {
                "project_instance": self.record["project_instance"],
                "expected_revision": 7,
            },
        )])
        self.assertEqual(result["revision"], 8)
        self.assertEqual(result["members"][1]["role"], "viewer")

        for account_id in ("d" * 32, "c" * 32):
            with self.subTest(account_id=account_id), self.assertRaises(HTTPException) as missing:
                asyncio.run(namespace["set_project_member"](
                    "film",
                    account_id,
                    _Request({"role": "viewer", "expected_revision": 7}),
                ))
            self.assertEqual(missing.exception.status_code, 404)

    def test_set_member_rejects_revision_not_bound_to_authorized_record(self):
        namespace = self._namespace()
        with self.assertRaises(HTTPException) as conflict:
            asyncio.run(namespace["set_project_member"](
                "film",
                "b" * 32,
                _Request({"role": "viewer", "expected_revision": 8}),
            ))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(
            conflict.exception.detail,
            "Project members changed; refresh and try again",
        )
        self.assertEqual(self.auth_store.resolve_calls, [])
        self.assertEqual(self.membership_store.bind_calls, [])

    def test_remove_member_uses_exact_account_and_preserves_store_owner_guard(self):
        namespace = self._namespace()
        result = asyncio.run(namespace["remove_project_member"](
            "film",
            "b" * 32,
            _Request({"expected_revision": 7}, remote=True),
        ))
        self.assertEqual(self.membership_store.unbind_calls, [(
            "b" * 32,
            {
                "project_instance": self.record["project_instance"],
                "expected_revision": 7,
            },
        )])
        self.assertEqual(result["revision"], 8)
        self.assertEqual([item["account_id"] for item in result["members"]], ["a" * 32])

        self.membership_store.error = ProjectMembershipConflictError(
            "a project must retain at least one owner",
        )
        with self.assertRaises(HTTPException) as conflict:
            asyncio.run(namespace["remove_project_member"](
                "film", "a" * 32, _Request({"expected_revision": 7}),
            ))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertNotIn("retain", conflict.exception.detail.lower())

    def test_remove_member_rejects_revision_not_bound_to_authorized_record(self):
        namespace = self._namespace()
        with self.assertRaises(HTTPException) as conflict:
            asyncio.run(namespace["remove_project_member"](
                "film",
                "b" * 32,
                _Request({"expected_revision": 8}),
            ))
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(
            conflict.exception.detail,
            "Project members changed; refresh and try again",
        )
        self.assertEqual(self.auth_store.resolve_calls, [])
        self.assertEqual(self.membership_store.unbind_calls, [])

    def test_store_failures_are_bounded_and_do_not_leak_internal_details(self):
        namespace = self._namespace()
        self.membership_store.error = ProjectMembershipNotFoundError("secret path")
        with self.assertRaises(HTTPException) as missing:
            asyncio.run(namespace["set_project_member"](
                "film", "b" * 32,
                _Request({"role": "editor", "expected_revision": 7}),
            ))
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(missing.exception.detail, "Project not found")

        self.membership_store.error = ProjectMembershipError("sealed store path")
        with self.assertRaises(HTTPException) as unavailable:
            asyncio.run(namespace["remove_project_member"](
                "film", "b" * 32, _Request({"expected_revision": 7}),
            ))
        self.assertEqual(unavailable.exception.status_code, 503)
        self.assertNotIn("path", str(unavailable.exception.detail).lower())


if __name__ == "__main__":
    unittest.main()
