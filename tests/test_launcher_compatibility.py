"""Pinokio launcher regressions that do not require the application runtime."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


class TestPinokioGpuCompatibility(unittest.TestCase):
    def _render_launcher_menu(self, *, running, locals_by_script):
        loader = r"""
const launcher = require('./pinokio.js');
const state = JSON.parse(process.argv[1]);
const info = {
  exists: (filepath) => filepath === 'app/env',
  running: (filepath) => Boolean(state.running[filepath]),
  local: (filepath) => state.locals[filepath] || {},
};
Promise.resolve(launcher.menu({}, info))
  .then((menu) => process.stdout.write(JSON.stringify(menu)))
  .catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            [
                "node", "-e", loader,
                json.dumps({"running": running, "locals": locals_by_script}),
            ],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return json.loads(completed.stdout)

    def test_installed_app_menu_is_not_hidden_by_early_gpu_detection(self):
        launcher = (_ROOT / "pinokio.js").read_text(encoding="utf-8")

        self.assertNotIn("if (kernel.gpu", launcher)
        self.assertIn("<strong>Start</strong>", launcher)
        self.assertIn('href: "start.js"', launcher)

    def test_fresh_install_still_uses_pinokios_documented_gpu_variable(self):
        installer = (_ROOT / "install.js").read_text(encoding="utf-8")

        self.assertIn("{{gpu !== 'nvidia'}}", installer)
        self.assertIn("This app requires an NVIDIA GPU", installer)

    def test_start_url_uses_the_required_capture_object(self):
        for filename in ("start.js", "start_classic.js"):
            with self.subTest(filename=filename):
                start = (_ROOT / filename).read_text(encoding="utf-8")

                self.assertIn('bundle: "ai"', start)
                self.assertIn('SERVER_PORT: port', start)
                self.assertIn('"event": "/(http:\\/\\/[0-9.:]+)/"', start)
                self.assertIn('url: "{{input.event[1]}}"', start)

    def test_cloudflare_sharing_defaults_on_without_a_committed_passcode(self):
        environment = (_ROOT / "ENVIRONMENT.example").read_text(encoding="utf-8")
        launcher = (_ROOT / "pinokio.js").read_text(encoding="utf-8")

        self.assertIn("PINOKIO_SHARE_CLOUDFLARE=true", environment)
        self.assertIn("PINOKIO_SHARE_LOCAL=false", environment)
        self.assertIn("PINOKIO_SHARE_PASSCODE=\n", environment)
        self.assertNotIn("PINOKIO_SHARE_PASSCODE=maestro", environment)
        self.assertIn("Cloudflare app sharing is enabled by default", launcher)
        self.assertNotIn("env.PINOKIO_SHARE_PASSCODE", launcher)
        self.assertIn("local.$share.cloudflare", launcher)
        self.assertIn("Open / copy Cloudflare stable URL", launcher)
        self.assertIn("Open / copy direct Quick Tunnel URL", launcher)

    def test_verified_stable_share_never_erases_direct_quick_tunnel(self):
        local_url = "http://127.0.0.1:7860"
        stable = "https://maestro.example.workers.dev"
        quick = "https://current-session.trycloudflare.com"
        menu = self._render_launcher_menu(
            running={"start.js": True},
            locals_by_script={"start.js": {
                "url": local_url,
                "share_url": stable,
                "share_kind": "stable",
                "$share": {"cloudflare": {local_url: quick}},
                "sharing": f"Cloudflare stable: {stable}",
            }},
        )

        hrefs = [item.get("href") for item in menu]
        self.assertIn(stable, hrefs)
        self.assertIn(quick, hrefs)
        self.assertLess(hrefs.index(stable), hrefs.index(quick))
        quick_item = next(item for item in menu if item.get("href") == quick)
        self.assertIn("direct Quick Tunnel", quick_item["text"])
        self.assertIn("Worker proxy hop", quick_item["text"])
        self.assertIn("Worker quota", quick_item["text"])
        self.assertIn("stable route is unavailable", quick_item["text"])
        self.assertNotIn("100 MB", quick_item["text"])
        self.assertNotIn("upload", quick_item["text"].lower())

    def test_quick_tunnel_is_shown_independently_without_stable_share(self):
        local_url = "http://127.0.0.1:7860"
        quick = "https://current-session.trycloudflare.com"
        menu = self._render_launcher_menu(
            running={"start.js": True},
            locals_by_script={"start.js": {
                "url": local_url,
                "share_url": quick,
                "share_kind": "quick",
                "$share": {"cloudflare": {local_url: quick}},
            }},
        )

        quick_items = [item for item in menu if item.get("href") == quick]
        self.assertEqual(len(quick_items), 1)
        self.assertIn("direct Quick Tunnel", quick_items[0]["text"])

    def test_identical_stable_and_quick_urls_are_not_duplicated(self):
        local_url = "http://127.0.0.1:7860"
        shared = "https://maestro.example.workers.dev"
        menu = self._render_launcher_menu(
            running={"start.js": True},
            locals_by_script={"start.js": {
                "url": local_url,
                "share_url": shared,
                "share_kind": "stable",
                "$share": {"cloudflare": {local_url: shared}},
            }},
        )

        self.assertEqual(sum(item.get("href") == shared for item in menu), 1)

    def test_share_links_leave_local_and_classic_entries_unchanged(self):
        local_url = "http://127.0.0.1:7860"
        menu = self._render_launcher_menu(
            running={"start.js": True},
            locals_by_script={"start.js": {
                "url": local_url,
                "share_url": "https://maestro.example.workers.dev",
                "share_kind": "stable",
                "$share": {"cloudflare": {
                    local_url: "https://current-session.trycloudflare.com",
                }},
            }},
        )

        local_item = next(item for item in menu if item.get("href") == local_url)
        classic_item = next(
            item for item in menu if item.get("href") == local_url + "/classic"
        )
        self.assertTrue(local_item["default"])
        self.assertEqual(local_item["text"], "Open Web UI")
        self.assertEqual(classic_item["text"], "Open Classic UI")

        classic_menu = self._render_launcher_menu(
            running={"start_classic.js": True},
            locals_by_script={"start_classic.js": {"url": local_url}},
        )
        self.assertEqual(classic_menu[0]["href"], local_url)
        self.assertEqual(classic_menu[0]["text"], "Open Classic UI")
        self.assertTrue(classic_menu[0]["default"])

    def test_install_materializes_cloudflare_default_without_overriding_choice(self):
        installer = (_ROOT / "install.js").read_text(encoding="utf-8")
        updater = (_ROOT / "update.js").read_text(encoding="utf-8")
        helper_path = _ROOT / "app" / "scripts" / "ensure_environment_defaults.py"
        self.assertIn("ensure_environment_defaults.py --file ENVIRONMENT", installer)
        self.assertEqual(updater.count("ensure_environment_defaults.py --file ENVIRONMENT"), 2)
        self.assertIn('id: "uptodate"', updater)
        self.assertIn('id: "build"', updater)

        spec = importlib.util.spec_from_file_location("ensure_environment_defaults", helper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "ENVIRONMENT"
            self.assertTrue(module.ensure_default(environment, "PINOKIO_SHARE_CLOUDFLARE", "true"))
            self.assertIn("PINOKIO_SHARE_CLOUDFLARE=true", environment.read_text(encoding="utf-8"))
            environment.write_text("PINOKIO_SHARE_CLOUDFLARE=false\n", encoding="utf-8")
            self.assertFalse(module.ensure_default(environment, "PINOKIO_SHARE_CLOUDFLARE", "true"))
            self.assertEqual(environment.read_text(encoding="utf-8"), "PINOKIO_SHARE_CLOUDFLARE=false\n")

    def test_start_registers_pinokios_live_tunnel_url_with_local_ui(self):
        start = (_ROOT / "start.js").read_text(encoding="utf-8")
        helper = (_ROOT / "app" / "scripts" / "register_share_url.py").read_text(encoding="utf-8")

        self.assertIn("local.$share.cloudflare[local.url]", start)
        self.assertIn("scripts/register_share_url.py", start)
        self.assertIn("/api/v1/access-context/share-url", helper)
        self.assertIn('"Origin": local_origin', helper)

    def test_live_tunnel_registration_does_not_depend_on_global_environment(self):
        start = (_ROOT / "start.js").read_text(encoding="utf-8")

        # The app ENVIRONMENT must override kernel.envs, which is Pinokio's
        # global environment at launcher-construction time.
        self.assertIn("readAppEnvironment", start)
        self.assertIn("Object.prototype.hasOwnProperty.call(appEnvironment, key)", start)
        self.assertIn("local.$share.cloudflare[local.url]", start)
        self.assertNotIn("envs.PINOKIO_SHARE_CLOUDFLARE", start)

    def _load_start_with_environment(self, app_environment, global_environment):
        source = (_ROOT / "start.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "start.js").write_text(source, encoding="utf-8")
            (root / "launcher_secret_env.js").write_text(
                (_ROOT / "launcher_secret_env.js").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "ENVIRONMENT").write_text(app_environment, encoding="utf-8")
            loader = """
const build = require('./start.js');
const envs = JSON.parse(process.argv[1]);
Promise.resolve(build({port: async () => 7860, envs}))
  .then((definition) => process.stdout.write(JSON.stringify(definition)))
  .catch((error) => { console.error(error); process.exit(1); });
"""
            completed = subprocess.run(
                ["node", "-e", loader, json.dumps(global_environment)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        return json.loads(completed.stdout)

    def test_app_cloudflare_true_overrides_global_false_for_delayed_registration(self):
        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=true\nPINOKIO_STABLE_SHARE_URL=\n",
            {"PINOKIO_SHARE_CLOUDFLARE": "false"},
        )
        steps = definition["run"]
        poll = next(step for step in steps if step.get("id") == "wait-for-cloudflare")
        register = next(
            step
            for step in steps
            if "register_share_url.py" in " ".join(step.get("params", {}).get("message", []))
        )

        self.assertIsInstance(poll["when"], str)
        self.assertNotIn("share_poll_limit", poll["when"])
        self.assertIsInstance(register["when"], str)
        self.assertIn("local.$share.cloudflare[local.url]", register["when"])

    def test_effective_local_only_state_skips_cloudflare_poll(self):
        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=false\nPINOKIO_STABLE_SHARE_URL=\n",
            {"PINOKIO_SHARE_CLOUDFLARE": "true"},
        )
        steps = definition["run"]
        poll = next(step for step in steps if step.get("id") == "wait-for-cloudflare")
        register = next(
            step
            for step in steps
            if "register_share_url.py" in " ".join(step.get("params", {}).get("message", []))
        )

        self.assertFalse(poll["when"])
        self.assertFalse(register["when"])

    def test_configured_stable_share_never_suppresses_quick_tunnel_registration(self):
        stable_url = "https://maestro.example.workers.dev"
        definition = self._load_start_with_environment(
            f'PINOKIO_SHARE_CLOUDFLARE=true\nPINOKIO_STABLE_SHARE_URL="{stable_url}"\n',
            {
                "PINOKIO_SHARE_CLOUDFLARE": "false",
                "PINOKIO_STABLE_SHARE_URL": "https://global.example.workers.dev",
            },
        )
        steps = definition["run"]
        captured = next(step for step in steps if step.get("method") == "local.set")
        poll = next(step for step in steps if step.get("id") == "wait-for-cloudflare")
        register = next(
            step
            for step in steps
            if "register_share_url.py" in " ".join(step.get("params", {}).get("message", []))
        )

        self.assertEqual(captured["params"]["share_url"], "")
        self.assertTrue(captured["params"]["stable_share_configured"])
        self.assertIsInstance(poll["when"], str)
        self.assertIsInstance(register["when"], str)

    def test_delayed_cloudflare_url_poll_is_indefinite_and_preserves_quick_path(self):
        source = (_ROOT / "start.js").read_text(encoding="utf-8")
        loader = """
const build = require('./start.js');
Promise.resolve(build({port: async () => 7860}))
  .then((definition) => process.stdout.write(JSON.stringify(definition)))
  .catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", loader],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        definition = json.loads(completed.stdout)
        steps = definition["run"]
        poll_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("id") == "wait-for-cloudflare"
        )
        jump_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("method") == "jump"
        )
        register_index = next(
            index
            for index, step in enumerate(steps)
            if "register_share_url.py" in " ".join(step.get("params", {}).get("message", []))
        )

        self.assertLess(poll_index, jump_index)
        self.assertLess(jump_index, register_index)
        self.assertEqual(steps[jump_index]["params"]["id"], "wait-for-cloudflare")
        self.assertIn("time.sleep(1)", " ".join(steps[poll_index]["params"]["message"]))
        self.assertNotIn("share_poll_limit", source)
        self.assertNotIn("was not ready after", source)
        self.assertIn('effectiveEnvironmentValue("PINOKIO_STABLE_SHARE_URL")', source)
        self.assertNotIn("configuredShareUrl", source)

        # The captured local URL is published before the cancellable sleep/jump
        # loop, so Maestro is locally ready even if Cloudflare is delayed.
        local_url_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("method") == "local.set" and "url" in step.get("params", {})
        )
        self.assertLess(local_url_index, poll_index)
        self.assertTrue(definition["daemon"])

    def test_cloudflare_url_appearing_after_poll_31_is_registered(self):
        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=true\nPINOKIO_STABLE_SHARE_URL=\n",
            {},
        )
        steps = definition["run"]
        poll = next(step for step in steps if step.get("id") == "wait-for-cloudflare")
        increment = next(
            step
            for step in steps
            if step.get("method") == "local.set"
            and "share_poll_attempt" in step.get("params", {})
            and "url" not in step.get("params", {})
        )
        register = next(
            step
            for step in steps
            if "register_share_url.py" in " ".join(step.get("params", {}).get("message", []))
        )

        def evaluate(template, local):
            expression = template.removeprefix("{{").removesuffix("}}")
            script = (
                "const local = JSON.parse(process.argv[1]);"
                f"process.stdout.write(JSON.stringify(Boolean({expression})));"
            )
            completed = subprocess.run(
                ["node", "-e", script, json.dumps(local)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return json.loads(completed.stdout)

        local = {"url": "http://127.0.0.1:7860", "share_poll_attempt": 0, "$share": {}}
        for _ in range(31):
            self.assertTrue(evaluate(poll["when"], local))
            local["share_poll_attempt"] += 1
        self.assertEqual(local["share_poll_attempt"], 31)
        self.assertIn("local.share_poll_attempt + 1", increment["params"]["share_poll_attempt"])

        tunnel = "https://eventual-test.trycloudflare.com"
        local["$share"] = {"cloudflare": {local["url"]: tunnel}}
        self.assertFalse(evaluate(poll["when"], local))
        self.assertTrue(evaluate(register["when"], local))

    def test_cloudflare_poll_is_cancellable_and_not_a_busy_loop(self):
        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=true\nPINOKIO_STABLE_SHARE_URL=\n",
            {},
        )
        steps = definition["run"]
        poll_index = next(
            index for index, step in enumerate(steps) if step.get("id") == "wait-for-cloudflare"
        )
        jump_index = next(
            index for index, step in enumerate(steps) if step.get("method") == "jump"
        )
        poll = steps[poll_index]
        command = " ".join(poll["params"]["message"])

        self.assertLess(poll_index, jump_index)
        self.assertEqual(poll["method"], "shell.run")
        self.assertEqual(poll["params"]["path"], "app")
        self.assertIn("time.sleep(1)", command)
        self.assertNotIn("&", command)
        self.assertNotIn("nohup", command)
        self.assertNotIn("detached", command)

    def test_blender_bridge_readiness_advances_main_start_without_daemon_subroutine(self):
        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=false\nPINOKIO_STABLE_SHARE_URL=\n",
            {},
        )
        steps = definition["run"]
        bridge_index = next(
            index
            for index, step in enumerate(steps)
            if "start_blender_bridge.py" in " ".join(
                step.get("params", {}).get("message", [])
            )
        )
        backend_index = next(
            index
            for index, step in enumerate(steps)
            if "launch.py" in " ".join(step.get("params", {}).get("message", []))
        )
        bridge = steps[bridge_index]

        self.assertLess(bridge_index, backend_index)
        self.assertEqual(bridge["method"], "shell.run")
        self.assertEqual(bridge["when"], "{{exists('app/tools/blender/runtime.json')}}")
        self.assertEqual(bridge["params"]["path"], "app")
        self.assertEqual(
            bridge["params"]["message"],
            [
                "python -m services.blender_mcp_service attest-runtime --marker tools/blender/runtime.json",
                "python scripts/start_blender_bridge.py",
            ],
        )
        self.assertTrue(bridge["params"]["on"][0]["done"])
        event = bridge["params"]["on"][0]["event"]
        self.assertTrue(event.startswith("/") and event.endswith("/"))
        readiness = re.compile(event[1:-1])
        self.assertIsNotNone(readiness.fullmatch("MCP server started on 127.0.0.1:9876"))
        self.assertIsNotNone(
            readiness.fullmatch("Blender bridge already ready at 127.0.0.1:9876")
        )
        self.assertIsNone(readiness.fullmatch("Blender bridge starting"))
        self.assertIsNone(readiness.fullmatch("http://127.0.0.1:9876"))
        self.assertNotIn(
            "blender_runtime_start.js",
            [
                step.get("params", {}).get("uri")
                for step in steps
                if step.get("method") == "script.start"
            ],
        )

    def test_cloudflare_secrets_are_masked_from_every_non_share_child_shell(self):
        files = [
            "install.js", "update.js", "blender_mcp_install.js",
            "blender_runtime_install.js", "blender_runtime_start.js",
            "sam_install.js", "h3_acceleration_install.js",
            "h3_w4a8_runtime_install.js", "torch.js", "start_classic.js",
        ]
        loader = r"""
const files = JSON.parse(process.argv[1]);
(async () => {
  const failures = [];
  for (const file of files) {
    let definition = require('./' + file);
    if (typeof definition === 'function') definition = await definition({port: async () => 7860});
    for (const [index, step] of (definition.run || []).entries()) {
      if (step.method !== 'shell.run') continue;
      const env = (step.params || {}).env || {};
      if (env.CLOUDFLARE_API_TOKEN !== '' || env.PINOKIO_STABLE_SHARE_UPDATE_SECRET !== '') {
        failures.push(`${file}:${index}`);
      }
    }
  }
  process.stdout.write(JSON.stringify(failures));
})().catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", loader, json.dumps(files)],
            cwd=_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(json.loads(completed.stdout), [])

        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=true\nPINOKIO_STABLE_SHARE_URL=\n",
            {},
        )
        for step in definition["run"]:
            if step.get("method") != "shell.run":
                continue
            command = " ".join(step.get("params", {}).get("message", []))
            environment = step.get("params", {}).get("env", {})
            self.assertEqual(environment.get("CLOUDFLARE_API_TOKEN"), "")
            if "register_share_url.py" not in command:
                self.assertEqual(
                    environment.get("PINOKIO_STABLE_SHARE_UPDATE_SECRET"), "",
                )

    def test_share_registration_sends_loopback_origin_proof(self):
        helper_path = _ROOT / "app" / "scripts" / "register_share_url.py"
        spec = importlib.util.spec_from_file_location("register_share_url", helper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        captured = {}

        class Response:
            status = 200

            def __init__(self):
                self.headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return json.dumps({
                    "status": "ok",
                    "share_url": "https://example.trycloudflare.com",
                }).encode("utf-8")

        def open_request(request, timeout):
            captured["origin"] = request.get_header("Origin")
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return Response()

        selected = module.register_share_url(
            "http://127.0.0.1:7860",
            "https://example.trycloudflare.com",
            open_request=open_request,
        )
        self.assertEqual(selected, ("https://example.trycloudflare.com", "quick"))
        self.assertEqual(captured["origin"], "http://127.0.0.1:7860")
        self.assertEqual(captured["url"], "http://127.0.0.1:7860/api/v1/access-context/share-url")
        self.assertEqual(captured["body"], {
            "share_url": "https://example.trycloudflare.com",
            "quick_tunnel_url": "https://example.trycloudflare.com",
            "stable_verified": False,
        })

    def test_share_registration_rejects_noncanonical_tunnel_urls_and_nonloopback_origins(self):
        helper_path = _ROOT / "app" / "scripts" / "register_share_url.py"
        spec = importlib.util.spec_from_file_location("register_share_url_validation", helper_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        valid = "https://eventual-test.trycloudflare.com/"
        self.assertEqual(module._canonical_quick_tunnel_url(valid), valid.rstrip("/"))
        for value in (
            "http://eventual-test.trycloudflare.com",
            "https://trycloudflare.com",
            "https://eventual-test.trycloudflare.com.evil.test",
            "https://eventual-test.trycloudflare.com/path",
            "https://eventual-test.trycloudflare.com?token=x",
            "https://eventual-test.trycloudflare.com:443",
        ):
            with self.subTest(share_url=value), self.assertRaises(ValueError):
                module._canonical_quick_tunnel_url(value)

        for value in (
            "http://0.0.0.0:7860",
            "http://192.168.1.5:7860",
            "https://127.0.0.1:7860",
            "http://127.0.0.1:7860/path",
        ):
            with self.subTest(origin=value), self.assertRaises(ValueError):
                module._canonical_loopback_origin(value)

    def test_classic_lan_binding_has_localhost_safe_default(self):
        classic = (_ROOT / "start_classic.js").read_text(encoding="utf-8")

        self.assertIn("PINOKIO_SHARE_LOCAL === 'true'", classic)
        self.assertIn("'0.0.0.0' : '127.0.0.1'", classic)


if __name__ == "__main__":
    unittest.main()
