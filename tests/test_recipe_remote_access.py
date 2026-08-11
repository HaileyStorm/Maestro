"""Focused recipe visibility and mobile-navigation regression contracts."""
from __future__ import annotations

import ast
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "app" / "launch.py"
RECIPES = ROOT / "app" / "services" / "recipes.py"
APP = ROOT / "ui" / "src" / "App.tsx"
SIDEBAR = ROOT / "ui" / "src" / "components" / "Sidebar" / "Sidebar.tsx"
OVERLAY = ROOT / "ui" / "src" / "components" / "Recipes" / "RecipesOverlay.tsx"
WELCOME = ROOT / "ui" / "src" / "components" / "WelcomeModal.tsx"
STORE = ROOT / "ui" / "src" / "stores" / "useStore.ts"
CLIENT = ROOT / "ui" / "src" / "api" / "client.ts"


def _load_recipes_module():
    spec = importlib.util.spec_from_file_location("recipe_visibility_under_test", RECIPES)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launch_subset(*names: str, include_remote_constants: bool = False) -> dict:
    source = LAUNCH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            selected = copy.deepcopy(node)
            selected.decorator_list = []
            selected.body = [
                statement for statement in selected.body
                if not (
                    isinstance(statement, ast.ImportFrom)
                    and statement.module == "services"
                    and any(alias.name == "recipes" for alias in statement.names)
                )
            ]
            body.append(selected)
        elif include_remote_constants and isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id in {"_REMOTE_LOCAL_ONLY_PREFIXES", "_REMOTE_LOCAL_ONLY_EXACT"}
            for target in node.targets
        ):
            body.append(copy.deepcopy(node))
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)

    class Response:
        def __init__(self, content, status_code=200):
            self.content = content
            self.status_code = status_code

    class FileResponse:
        def __init__(self, path, media_type=None):
            self.path = path
            self.media_type = media_type

    class HTTPException(Exception):
        def __init__(self, *, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    namespace = {
        "Request": object,
        "JSONResponse": Response,
        "FileResponse": FileResponse,
        "HTTPException": HTTPException,
        "quote": quote,
        "_request_is_cloudflare_remote": lambda _request: True,
        "_STATE_CHANGING_METHODS": frozenset({"POST", "PUT", "PATCH", "DELETE"}),
    }
    exec(compile(module, str(LAUNCH), "exec"), namespace)
    return namespace


class RecipeServiceVisibilityTests(unittest.TestCase):
    def test_bundled_only_never_reads_or_shadows_from_user_directory(self):
        recipes = _load_recipes_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundled = root / "bundled"
            user = root / "user"
            bundled.mkdir()
            user.mkdir()
            (bundled / "shared.json").write_text(
                json.dumps({"name": "Bundled shared", "model_type": "base", "loras": []}),
                encoding="utf-8",
            )
            (user / "shared.json").write_text(
                json.dumps({"name": "Private shadow", "model_type": "private", "loras": []}),
                encoding="utf-8",
            )
            (user / "private-only.json").write_text(
                json.dumps({"name": "Private only", "model_type": "private", "loras": []}),
                encoding="utf-8",
            )
            (bundled / "shared.jpg").write_bytes(b"bundled-thumbnail")
            (user / "shared.jpg").write_bytes(b"private-thumbnail")
            recipes.BUNDLED_DIR = str(bundled)
            recipes.USER_DIR = str(user)

            self.assertEqual(
                [card["name"] for card in recipes.list_recipes(bundled_only=True)],
                ["Bundled shared"],
            )
            self.assertEqual(
                recipes.get_recipe("shared", bundled_only=True)["model_type"],
                "base",
            )
            self.assertIsNone(recipes.get_recipe("private-only", bundled_only=True))
            self.assertEqual(recipes.get_recipe("shared")["model_type"], "private")
            self.assertEqual(
                recipes.get_recipe_thumbnail_path("shared", bundled_only=True),
                str(bundled / "shared.jpg"),
            )
            self.assertEqual(
                recipes.get_recipe_thumbnail_path("shared"),
                str(user / "shared.jpg"),
            )

    def test_import_rejects_malformed_shapes_and_normalizes_lora_fields(self):
        recipes = _load_recipes_module()
        with tempfile.TemporaryDirectory() as temp:
            recipes.USER_DIR = temp
            invalid = [
                {"name": 7, "model_type": "base", "params": {}, "loras": []},
                {"name": "Bad", "model_type": "", "params": {}, "loras": []},
                {"name": "Bad", "model_type": "base", "mode": "tools", "params": {}, "loras": []},
                {"name": "Bad", "model_type": "base", "params": [], "loras": []},
                {"name": "Bad", "model_type": "base", "params": {}, "loras": [{}]},
                {"name": "Bad", "model_type": "base", "params": {}, "loras": [{"filename": "x.safetensors", "multiplier": "nan"}]},
            ]
            for payload in invalid:
                with self.subTest(payload=payload):
                    with self.assertRaises(ValueError):
                        recipes.import_recipe(payload)

            card = recipes.import_recipe({
                "name": "Portable",
                "model_type": "base",
                "mode": "video",
                "params": {"guidance_scale": 2.5, "not_allowed": "discard"},
                "loras": [{
                    "filename": "look.safetensors",
                    "multiplier": "0.7;1.0",
                    "source_url": "https://civitai.com/example",
                    "untrusted": "discard",
                }],
            })
            imported = recipes.get_recipe(card["id"])
            self.assertEqual(imported["params"], {"guidance_scale": 2.5})
            self.assertNotIn("untrusted", imported["loras"][0])


class RecipeRouteAuthorityTests(unittest.TestCase):
    def test_real_project_helpers_reject_missing_unprotected_and_locked_remote_scope(self):
        namespace = _launch_subset("_request_project_workspace", "_require_project_access")
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_remote=True, maestro_session_id="session-a")
        )
        namespace["_get_active_workspace"] = lambda: "host-global"
        namespace["_existing_workspace_dir"] = lambda workspace: f"/projects/{workspace}"

        with self.assertRaises(namespace["HTTPException"]) as missing:
            namespace["_request_project_workspace"](request, "")
        self.assertEqual(missing.exception.status_code, 400)

        for status, expected_detail in (
            (types.SimpleNamespace(protected=False, unlocked=True), "password-protected"),
            (types.SimpleNamespace(protected=True, unlocked=False), "password locked"),
        ):
            namespace["_project_access"] = types.SimpleNamespace(status=lambda *_args, value=status: value)
            with self.assertRaises(namespace["HTTPException"]) as rejected:
                namespace["_require_project_access"](request, "film-a")
            self.assertIn(expected_detail, rejected.exception.detail)

        namespace["_project_access"] = types.SimpleNamespace(
            status=lambda *_args: types.SimpleNamespace(protected=True, unlocked=True)
        )
        self.assertEqual(
            namespace["_require_project_access"](request, "film-a"),
            "/projects/film-a",
        )

    def test_remote_reads_require_an_explicit_authorized_project(self):
        namespace = _launch_subset("_recipe_read_scope")
        events = []
        namespace["_request_project_workspace"] = (
            lambda _request, workspace: events.append(("resolve", workspace)) or workspace
        )
        namespace["_require_project_access"] = (
            lambda _request, workspace: events.append(("authorize", workspace))
        )
        scope = namespace["_recipe_read_scope"]
        request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=True))

        self.assertEqual(scope(request, "film-a"), (True, "film-a"))
        self.assertEqual(events, [("resolve", "film-a"), ("authorize", "film-a")])

    def test_remote_recipe_gets_pass_but_mutations_are_local_only(self):
        namespace = _launch_subset("_remote_local_only_denial", include_remote_constants=True)
        deny = namespace["_remote_local_only_denial"]

        def request(method: str, path: str):
            return types.SimpleNamespace(method=method, url=types.SimpleNamespace(path=path))

        self.assertIsNone(deny(request("GET", "/api/v1/recipes")))
        self.assertIsNone(deny(request("GET", "/api/v1/recipes/cinematic-film")))
        self.assertEqual(deny(request("POST", "/api/v1/recipes/import")).status_code, 403)
        self.assertEqual(deny(request("POST", "/api/v1/recipes/save-from-output")).status_code, 403)
        self.assertEqual(deny(request("DELETE", "/api/v1/recipes/cinematic-film")).status_code, 403)
        self.assertEqual(deny(request("POST", "/api/v1/civitai/download")).status_code, 403)
        self.assertEqual(deny(request("POST", "/api/v1/loras/install")).status_code, 403)

        middleware = LAUNCH.read_text(encoding="utf-8")
        middleware = middleware[
            middleware.index('@api.middleware("http")'):
            middleware.index("capability_read =", middleware.index('@api.middleware("http")'))
        ]
        self.assertIn("remote_denial = _remote_local_only_denial(request)", middleware)
        self.assertIn("if remote_denial is not None:", middleware)

    def test_http_middleware_returns_recipe_denial_before_endpoint_dispatch(self):
        namespace = _launch_subset(
            "_remote_local_only_denial",
            "_maestro_session_middleware",
            include_remote_constants=True,
        )
        events = []
        namespace["_request_is_cloudflare_remote"] = lambda _request: True
        namespace["_research_local_only_denial"] = lambda _request: None
        namespace["_reject_cross_origin_mutation"] = lambda _request: None
        namespace["_stamp_recovery_no_store_response"] = lambda _request, response: response
        request = types.SimpleNamespace(
            method="POST",
            url=types.SimpleNamespace(path="/api/v1/recipes/save-from-output"),
        )

        async def call_next(_request):
            events.append("dispatched")
            return namespace["JSONResponse"]({}, status_code=200)

        response = asyncio.run(namespace["_maestro_session_middleware"](request, call_next))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(events, [])

    def test_remote_route_handlers_propagate_bundled_scope_and_thumbnail_workspace(self):
        namespace = _launch_subset(
            "_recipe_read_scope",
            "list_recipes_route",
            "get_recipe_route",
            "get_recipe_thumbnail_route",
        )
        authority = []
        calls = []
        namespace["_request_project_workspace"] = (
            lambda _request, workspace: authority.append(("resolve", workspace)) or workspace
        )
        namespace["_require_project_access"] = (
            lambda _request, workspace: authority.append(("authorize", workspace))
        )
        namespace["recipes"] = types.SimpleNamespace(
            list_recipes=lambda **kwargs: calls.append(("list", kwargs)) or [{
                "id": "starter",
                "source": "bundled",
                "thumbnail_url": "/api/v1/recipes/starter/thumbnail",
            }],
            get_recipe=lambda rid, **kwargs: calls.append(("get", rid, kwargs)) or {
                "id": rid, "source": "bundled",
            },
            get_recipe_thumbnail_path=lambda rid, **kwargs: (
                calls.append(("thumbnail", rid, kwargs)) or "/bundled/starter.jpg"
            ),
        )
        request = types.SimpleNamespace(state=types.SimpleNamespace(maestro_remote=True))

        cards = namespace["list_recipes_route"](request, "film a")["recipes"]
        recipe = namespace["get_recipe_route"](request, "starter", "film a")
        thumbnail = namespace["get_recipe_thumbnail_route"](request, "starter", "film a")

        self.assertEqual(cards[0]["thumbnail_url"], "/api/v1/recipes/starter/thumbnail?workspace=film%20a")
        self.assertEqual(recipe["source"], "bundled")
        self.assertEqual(thumbnail.path, "/bundled/starter.jpg")
        self.assertEqual(
            calls,
            [
                ("list", {"bundled_only": True}),
                ("get", "starter", {"bundled_only": True}),
                ("thumbnail", "starter", {"bundled_only": True}),
            ],
        )
        self.assertEqual(authority.count(("authorize", "film a")), 3)


class RecipeUiSourceHarnessTests(unittest.TestCase):
    """Executable source/DOM-shape checks without adding a browser dependency."""

    def test_remote_mobile_navigation_and_overlay_are_reachable_and_bounded(self):
        app = APP.read_text(encoding="utf-8")
        sidebar = SIDEBAR.read_text(encoding="utf-8")
        overlay = OVERLAY.read_text(encoding="utf-8")

        self.assertIn("!remoteProjectRequired ? <button", app)
        self.assertIn("<RecipesOverlay />", app)
        self.assertNotIn("machineControls && <RecipesOverlay />", app)
        self.assertIn('aria-controls="maestro-mobile-sidebar"', app)
        self.assertIn('aria-label="Open machine settings"', app)

        self.assertIn("setSidebarMode('director')", sidebar)
        self.assertIn("setSidebarMode('studio')", sidebar)
        self.assertIn("setSidebarMode('reference')", sidebar)
        self.assertIn("aria-pressed={sidebarMode === 'studio'}", sidebar)
        self.assertIn("aria-pressed={isDirector}", sidebar)
        self.assertIn("aria-pressed={isReference}", sidebar)
        self.assertIn('aria-label="Generate, Director, and Reference menu"', sidebar)
        self.assertIn("<ProjectReferenceLibrary active={isReference} />", sidebar)
        self.assertIn('h-[100dvh]', sidebar)
        self.assertIn('role="dialog"', sidebar)
        self.assertIn("event.key === 'Escape'", sidebar)
        self.assertIn("overscroll-contain", sidebar)

        self.assertIn('aria-modal="true"', overlay)
        self.assertIn('z-[100]', overlay)
        self.assertIn("closeButtonRef.current?.focus()", overlay)
        self.assertIn("event.key === 'Escape'", overlay)
        self.assertIn("machineControls && <button", overlay)
        self.assertIn("host owner", overlay)
        self.assertIn("applyingRef.current", overlay)
        self.assertIn("disabled={applying !== null}", overlay)
        self.assertIn('role="status" aria-live="polite"', overlay)
        self.assertIn("min-h-11 min-w-11", overlay)
        self.assertIn("!dialogRef.current.contains(document.activeElement)", overlay)
        self.assertIn("confirmDelete !== card.id", overlay)
        self.assertIn("Confirm delete recipe", overlay)

    def test_recipe_api_is_project_scoped_and_apply_uses_shared_studio_transition(self):
        client = CLIENT.read_text(encoding="utf-8")
        store = STORE.read_text(encoding="utf-8")
        welcome = WELCOME.read_text(encoding="utf-8")

        self.assertIn("fetchRecipes(workspace: string)", client)
        self.assertIn("fetchRecipe(id: string, workspace: string)", client)
        self.assertIn("workspace=${encodeURIComponent(workspace)}", client)
        self.assertIn("api.fetchRecipes(workspace)", store)
        self.assertIn("const recipeWorkspace = get().activeWorkspace", store)
        self.assertIn("api.fetchRecipe(id, recipeWorkspace)", store)
        self.assertIn("get().setSidebarMode('studio')", store)
        self.assertIn("get().setGenerationMode(mode)", store)
        self.assertIn("await get().loadModelOptions(recipe.model_type)", store)
        self.assertIn("await api.fetchLoras(recipe.model_type)", store)
        self.assertIn("const seq = ++_recipesLoadSeq", store)
        self.assertIn("const seq = ++_loraLoadSeq", store)
        self.assertIn("get().params.model_type !== modelType", store)
        self.assertGreaterEqual(store.count("++_h3ProfileApplySeq"), 2)
        self.assertIn("restoreInterruptedApply()", store)
        self.assertIn("get().modelOptions?.model_type !== recipe.model_type", store)
        self.assertIn("_saveSettings({", store)
        self.assertIn("flex-1 min-h-0 overflow-y-auto", welcome)
        self.assertIn("shrink-0 border-t", welcome)
        self.assertIn("setSidebarMode('studio')", welcome)
        self.assertIn("onClick={enterStudio}", welcome)


if __name__ == "__main__":
    unittest.main()
