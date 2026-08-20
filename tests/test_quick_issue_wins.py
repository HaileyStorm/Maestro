"""Model-free regressions for the August 2026 GitHub quick-win batch."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import tempfile
import unittest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LAUNCH_PATH = os.path.join(_ROOT, "app", "launch.py")
_LLM_SERVICE_PATH = os.path.join(_ROOT, "app", "services", "llm_service.py")
_WIN_SAFE_FILES_PATH = os.path.join(_ROOT, "app", "services", "win_safe_files.py")
_ADVANCED_PATH = os.path.join(
    _ROOT, "ui", "src", "components", "Sidebar", "AdvancedSettings.tsx",
)
_PROMPT_PATH = os.path.join(
    _ROOT, "ui", "src", "components", "Sidebar", "PromptInput.tsx",
)
_SERVICES_UI_PATH = os.path.join(
    _ROOT,
    "ui",
    "src",
    "components",
    "SettingsDrawer",
    "ServicesSettingsPanel.tsx",
)
_INDEX_HTML_PATH = os.path.join(_ROOT, "ui", "index.html")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _load_functions(path: str, names: tuple[str, ...]) -> dict:
    tree = ast.parse(_read(path), filename=os.path.relpath(path, _ROOT))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    if len(selected) != len(names):
        found = {node.name for node in selected}
        raise AssertionError(f"Missing functions: {set(names) - found}")
    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    loaded: dict = {}
    exec(compile(module, os.path.relpath(path, _ROOT), "exec"), loaded)
    return loaded


class TestRemoteOpenAICompatibleCredentials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pick_key = staticmethod(
            _load_functions(
                _LAUNCH_PATH,
                ("_llm_provider_api_key",),
            )["_llm_provider_api_key"]
        )

    def test_each_provider_uses_only_its_own_credential(self):
        services = {
            "llm_remote_api_key": "lan-secret",
            "openai_api_key": "openai-secret",
            "anthropic_api_key": "anthropic-secret",
        }
        # Continuum's helper is (provider, services). Remote does not read
        # llm_remote_api_key; that leftover 1.9.0 mapping was never restored.
        self.assertEqual(self.pick_key("remote", services), "")
        self.assertEqual(self.pick_key("openai", services), "openai-secret")
        self.assertEqual(self.pick_key("anthropic", services), "anthropic-secret")
        self.assertEqual(self.pick_key("local", services), "")

    def test_remote_key_ui_exists_but_launch_helper_does_not_read_it(self):
        launch = _read(_LAUNCH_PATH)
        panel = _read(_SERVICES_UI_PATH)
        tree = ast.parse(launch, filename="launch.py")
        helper = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_llm_provider_api_key"
        )
        helper_source = ast.get_source_segment(launch, helper) or ""
        self.assertNotIn("llm_remote_api_key", helper_source)
        self.assertNotIn("_llm_api_key_for_provider", launch)
        self.assertIn('label="Server API Key"', panel)
        self.assertIn("llm_remote_api_key", panel)

    def test_remote_connection_reloads_when_url_or_key_changes(self):
        source = _read(_LLM_SERVICE_PATH)
        self.assertIn("remote_url.rstrip(\"/\")", source)
        self.assertIn("credential_key", source)
        self.assertIn("hashlib.sha256(api_key.encode", source)


class TestCivitAIVariantUpdates(unittest.TestCase):
    def test_continuum_has_no_leftover_compatible_version_selector(self):
        launch = _read(_LAUNCH_PATH)
        tree = ast.parse(launch, filename="launch.py")
        names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("_select_latest_compatible_civitai_version", names)
        self.assertIn("LORA_MANIFEST_VERSION = 1", launch)
        self.assertNotIn("LORA_MANIFEST_VERSION = 2", launch)


class TestStudioInterfaceQuickWins(unittest.TestCase):
    def test_lora_selector_stays_on_continuum_advanced_panel(self):
        source = _read(_ADVANCED_PATH)
        self.assertEqual(source.count("{!isOutpaint && <LoraSelector />}"), 1)
        self.assertGreater(
            source.index("{!isOutpaint && <LoraSelector />}"),
            source.index("<PresetManager />"),
        )
        self.assertNotIn("H3 Text Encoder", source)

    def test_browser_spellcheck_is_enabled_for_text_inputs(self):
        self.assertIn('<body spellcheck="true">', _read(_INDEX_HTML_PATH))

    def test_main_prompt_keeps_continuum_fixed_min_height(self):
        source = _read(_PROMPT_PATH)
        self.assertIn("<textarea", source)
        self.assertIn("style={{ resize: 'none', minHeight: 112 }}", source)
        self.assertIn("grow shrink-0", source)
        self.assertNotIn("useAutoGrowingTextarea", source)
        self.assertNotIn("promptTextareaRef", source)
        self.assertNotIn("resize: 'vertical'", source)
        self.assertNotIn("maxHeight: '70vh'", source)


class TestDisconnectedMediaStreams(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "maestro_win_safe_files_quick_win_test",
            _WIN_SAFE_FILES_PATH,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load win_safe_files.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    async def test_client_disconnect_cancels_file_stream(self):
        payload = b"x" * (1024 * 1024)
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(payload)
            path = handle.name
        try:
            response = self.module.ShareDeleteFileResponse(path)
            response.chunk_size = 1024
            first_chunk = asyncio.Event()
            sent: list[dict] = []

            async def send(message: dict) -> None:
                sent.append(message)
                if message.get("type") == "http.response.body" and message.get("body"):
                    first_chunk.set()

            async def receive() -> dict:
                await first_chunk.wait()
                return {"type": "http.disconnect"}

            await asyncio.wait_for(
                response({"type": "http", "headers": []}, receive, send),
                timeout=2,
            )
            bytes_sent = sum(
                len(message.get("body", b""))
                for message in sent
                if message.get("type") == "http.response.body"
            )
            self.assertGreater(bytes_sent, 0)
            self.assertLess(bytes_sent, len(payload))
        finally:
            os.remove(path)

    async def test_completed_stream_cancels_disconnect_listener(self):
        payload = b"complete response"
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(payload)
            path = handle.name
        try:
            response = self.module.ShareDeleteFileResponse(path)
            sent: list[dict] = []
            never = asyncio.Event()

            async def send(message: dict) -> None:
                sent.append(message)

            async def receive() -> dict:
                await never.wait()
                return {"type": "http.disconnect"}

            await asyncio.wait_for(
                response({"type": "http", "headers": []}, receive, send),
                timeout=2,
            )
            body = b"".join(
                message.get("body", b"")
                for message in sent
                if message.get("type") == "http.response.body"
            )
            self.assertEqual(body, payload)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
