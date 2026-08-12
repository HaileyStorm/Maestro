"""Pinokio launcher regressions that do not require the application runtime."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


class TestPinokioGpuCompatibility(unittest.TestCase):
    def _render_launcher_menu(self, *, running, locals_by_script, ready=None):
        loader = r"""
const launcher = require('./pinokio.js');
const state = JSON.parse(process.argv[1]);
const info = {
  exists: (filepath) => filepath === 'app/env',
  running: (filepath) => Boolean(state.running[filepath]),
  ready: (filepath) => Boolean(state.ready[filepath]),
  local: (filepath) => state.locals[filepath] || {},
};
Promise.resolve(launcher.menu({}, info))
  .then((menu) => process.stdout.write(JSON.stringify(menu)))
  .catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            [
                "node", "-e", loader,
                json.dumps({
                    "running": running,
                    "ready": running if ready is None else ready,
                    "locals": locals_by_script,
                }),
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

    def test_restart_action_requires_ready_stable_share_state(self):
        local_url = "http://127.0.0.1:7860"
        cases = (
            (
                "stable_ready",
                True,
                {
                    "url": local_url,
                    "share_kind": "stable",
                    "share_url": "https://maestro.example.workers.dev",
                },
                True,
            ),
            (
                "stable_not_ready",
                False,
                {
                    "url": local_url,
                    "share_kind": "stable",
                    "share_url": "https://maestro.example.workers.dev",
                },
                False,
            ),
            (
                "quick_ready",
                True,
                {
                    "url": local_url,
                    "share_kind": "quick",
                    "share_url": "https://current-session.trycloudflare.com",
                },
                False,
            ),
            (
                "local_ready",
                True,
                {"url": local_url, "share_kind": "local"},
                False,
            ),
            (
                "missing_share_kind_ready",
                True,
                {
                    "url": local_url,
                    "share_url": "https://maestro.example.workers.dev",
                },
                False,
            ),
        )
        for name, ready, local, expected in cases:
            with self.subTest(name=name):
                menu = self._render_launcher_menu(
                    running={"start.js": True},
                    ready={"start.js": ready},
                    locals_by_script={"start.js": local},
                )
                actions = [item for item in menu if item.get("href") == "restart.js"]
                self.assertEqual(len(actions), 1 if expected else 0)
                if actions:
                    self.assertEqual(actions[0]["text"], "Restart Maestro")
                    self.assertNotIn("default", actions[0])
                self.assertIn("start.js", [item.get("href") for item in menu])

    def test_running_restart_status_is_visible_without_auto_restarting(self):
        menu = self._render_launcher_menu(
            running={"restart.js": True},
            locals_by_script={},
        )

        actions = [item for item in menu if item.get("href") == "restart.js"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["text"], "Restarting Maestro")
        self.assertNotIn("default", actions[0])

    def test_restart_script_publishes_before_restart_with_one_opaque_generation(self):
        loader = r"""
const build = require('./restart.js');
Promise.resolve(build())
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
        self.assertEqual(
            [step["method"] for step in definition["run"]],
            ["shell.run", "script.restart"],
        )
        publish, restart = definition["run"]
        command = publish["params"]["message"][0]
        generation_match = re.search(r"--generation ([A-Za-z0-9_-]{16,64})$", command)
        self.assertIsNotNone(generation_match)
        generation = generation_match.group(1)
        self.assertRegex(generation, r"^[0-9a-f]{48}$")
        self.assertIn("--state restarting", command)
        self.assertIn("--reason restart", command)
        self.assertIn("--ttl-seconds 900", command)
        self.assertIn(
            '--message "Maestro is restarting. Please try again shortly."', command,
        )
        self.assertLessEqual(len("Maestro is restarting. Please try again shortly."), 240)
        self.assertEqual(publish["params"]["path"], "app")
        self.assertEqual(publish["params"]["venv"], "env")
        self.assertEqual(publish["params"]["env"]["CLOUDFLARE_API_TOKEN"], "")
        self.assertNotIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET", publish["params"]["env"])
        self.assertEqual(
            publish["params"]["on"],
            [{"event": "/MAESTRO_RESTART_STATUS_SET restarting/", "kill": True}],
        )
        self.assertEqual(restart["params"]["uri"], "start.js")
        self.assertEqual(restart["params"]["params"], {"restart_generation": generation})
        self.assertNotIn("script.stop", [step["method"] for step in definition["run"]])

    def test_restart_startup_health_and_generation_clear_ordering(self):
        definition = self._load_start_with_environment(
            "PINOKIO_SHARE_CLOUDFLARE=true\n"
            "PINOKIO_STABLE_SHARE_URL=https://maestro.example.workers.dev\n",
            {},
        )
        steps = definition["run"]
        capture_index = next(
            index for index, step in enumerate(steps)
            if step.get("method") == "shell.run"
            and step.get("params", {}).get("on", [{}])[0].get("event")
            == "/(http://[0-9.:]+)/"
        )
        local_url_index = next(
            index for index, step in enumerate(steps)
            if step.get("method") == "local.set"
            and step.get("params", {}).get("url") == "{{input.event[1]}}"
        )
        health_indexes = [
            index for index, step in enumerate(steps)
            if step.get("method") == "process.wait"
            and step.get("params", {}).get("url") == "{{local.url}}/health"
        ]
        register_index = next(
            index for index, step in enumerate(steps)
            if "register_share_url.py" in " ".join(
                step.get("params", {}).get("message", []),
            )
        )
        clear_index = next(
            index for index, step in enumerate(steps)
            if "restart_status.py clear" in " ".join(
                step.get("params", {}).get("message", []),
            )
        )

        self.assertEqual(local_url_index, capture_index + 1)
        self.assertEqual(health_indexes[0], local_url_index + 1)
        self.assertLess(health_indexes[0], register_index)
        self.assertLess(register_index, health_indexes[1])
        self.assertEqual(clear_index, health_indexes[1] + 1)
        clear = steps[clear_index]
        self.assertIn("args.restart_generation", clear["when"])
        self.assertIn("[A-Za-z0-9_-]{16,64}", clear["when"])
        self.assertIn("local.share_kind === 'stable'", clear["when"])
        self.assertIn("--generation {{args.restart_generation}}", clear["params"]["message"][0])
        self.assertEqual(clear["params"]["env"]["CLOUDFLARE_API_TOKEN"], "")
        self.assertNotIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET", clear["params"]["env"])

        handler = clear["params"]["on"][0]
        self.assertTrue(handler["kill"])
        event = re.compile(handler["event"][1:-1])
        for output in (
            "MAESTRO_RESTART_STATUS_CLEARED",
            "MAESTRO_RESTART_STATUS_NOT_CLEARED",
            "Maestro restart-status request failed",
        ):
            self.assertIsNotNone(event.fullmatch(output))
        result_step = steps[clear_index + 1]
        status_log = steps[clear_index + 2]
        self.assertEqual(result_step["params"]["restart_status_clear_result"], "{{input.event[1]}}")
        self.assertIn("expire automatically", status_log["params"]["text"])
        self.assertIn("no matching generation", status_log["params"]["text"])
        self.assertIn("MAESTRO_RESTART_STATUS_CLEAR_FAILED", status_log["params"]["text"])

    def test_install_materializes_safe_defaults_without_overriding_choices(self):
        installer = (_ROOT / "install.js").read_text(encoding="utf-8")
        updater = (_ROOT / "update.js").read_text(encoding="utf-8")
        helper_path = _ROOT / "app" / "scripts" / "ensure_environment_defaults.py"
        self.assertIn("ensure_environment_defaults.py --file ENVIRONMENT", installer)
        self.assertEqual(updater.count("ensure_environment_defaults.py --file ENVIRONMENT"), 2)
        self.assertIn('id: "uptodate"', updater)
        self.assertIn('id: "build"', updater)

        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "ENVIRONMENT"
            subprocess.run(
                [sys.executable, str(helper_path), "--file", str(environment)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                {
                    line for line in environment.read_text(encoding="utf-8").splitlines()
                    if line
                },
                {
                    "PINOKIO_SHARE_CLOUDFLARE=true",
                    "MAESTRO_ACCOUNTS_ENABLED=false",
                    "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=false",
                },
            )

            explicit = Path(directory) / "EXPLICIT_ENVIRONMENT"
            explicit.write_text(
                "PINOKIO_SHARE_CLOUDFLARE=false\n"
                "MAESTRO_ACCOUNTS_ENABLED=true\n"
                "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=true\n",
                encoding="utf-8",
            )
            before = explicit.read_text(encoding="utf-8")
            subprocess.run(
                [sys.executable, str(helper_path), "--file", str(explicit)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(explicit.read_text(encoding="utf-8"), before)

            partial = Path(directory) / "PARTIAL_ENVIRONMENT"
            partial.write_text("MAESTRO_ACCOUNTS_ENABLED=true\n", encoding="utf-8")
            subprocess.run(
                [sys.executable, str(helper_path), "--file", str(partial)],
                check=True,
                capture_output=True,
                text=True,
            )
            partial_text = partial.read_text(encoding="utf-8")
            self.assertIn("MAESTRO_ACCOUNTS_ENABLED=true\n", partial_text)
            self.assertIn("MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=false\n", partial_text)

            malformed = Path(directory) / "MALFORMED_ENVIRONMENT"
            malformed.write_text(
                "PINOKIO_SHARE_CLOUDFLARE=false\n"
                "MAESTRO_ACCOUNTS_ENABLED=maybe\n"
                "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=\n",
                encoding="utf-8",
            )
            before = malformed.read_text(encoding="utf-8")
            subprocess.run(
                [sys.executable, str(helper_path), "--file", str(malformed)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(malformed.read_text(encoding="utf-8"), before)

    def test_readme_documents_deliberate_account_bootstrap_reset(self):
        readme = (_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(
            "Deleting only the account store does not reopen owner setup",
            readme,
        )
        self.assertIn("Prefer restoring a known-good backup", readme)
        self.assertIn("stop Maestro first", readme)
        self.assertIn(
            "both the account store and its sibling `.bootstrap-complete` marker",
            readme,
        )
        self.assertIn(
            "Remove the session secret only when you also intend to invalidate all "
            "sealed account state",
            readme,
        )
        self.assertIn("then re-enable bootstrap", readme)
        self.assertIn("direct local loopback Web UI", readme)
        self.assertIn(
            "destructive reset removes account sessions and recovery state",
            readme,
        )
        self.assertIn("no automatic reset or CLI", readme)
        self.assertIn(
            "Never put credentials, recovery codes, or secret values in commands or logs",
            readme,
        )

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

    def test_start_refreshes_explicit_backend_overrides_on_each_invocation(self):
        source = (_ROOT / "start.js").read_text(encoding="utf-8")
        first_environment = "\n".join([
            'MAESTRO_ACCOUNTS_ENABLED="true" # double-quoted flag',
            "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED='true' # single-quoted flag",
            'PINOKIO_SHARE_CLOUDFLARE="true" # cloudflare policy',
            "PINOKIO_SHARE_LOCAL='true' # local policy",
            'PINOKIO_STABLE_SHARE_URL="https://first.example.workers.dev/# release one" # stable policy',
            "MAESTRO_ENVIRONMENT_SENTINEL=app-first",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED=true",
            "CLOUDFLARE_API_TOKEN=app-first-token",
            "PINOKIO_STABLE_SHARE_UPDATE_SECRET=app-first-secret",
            "SERVER_PORT=1111",
            "",
        ])
        second_environment = "\n".join([
            "MAESTRO_ACCOUNTS_ENABLED='false' # explicit local false",
            'MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED="" # explicit local empty',
            'PINOKIO_SHARE_CLOUDFLARE="false" # explicit local false',
            "PINOKIO_SHARE_LOCAL='' # explicit local empty",
            'PINOKIO_STABLE_SHARE_URL="" # explicit local empty',
            "MAESTRO_ENVIRONMENT_SENTINEL=app-second",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED=true",
            "CLOUDFLARE_API_TOKEN=app-second-token",
            "PINOKIO_STABLE_SHARE_UPDATE_SECRET=app-second-secret",
            "SERVER_PORT=2222",
            "",
        ])
        global_environment = {
            "MAESTRO_ACCOUNTS_ENABLED": "true",
            "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED": "true",
            "PINOKIO_SHARE_CLOUDFLARE": "true",
            "PINOKIO_SHARE_LOCAL": "true",
            "PINOKIO_STABLE_SHARE_URL": "https://global.example.workers.dev",
            "MAESTRO_ENVIRONMENT_SENTINEL": "global",
            "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED": "true",
            "CLOUDFLARE_API_TOKEN": "global-token",
            "PINOKIO_STABLE_SHARE_UPDATE_SECRET": "global-secret",
            "SERVER_PORT": "3333",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "start.js").write_text(source, encoding="utf-8")
            (root / "launcher_secret_env.js").write_text(
                (_ROOT / "launcher_secret_env.js").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            loader = r"""
const fs = require('fs');
const build = require('./start.js');
const state = JSON.parse(process.argv[1]);
const kernel = {port: async () => 49152, envs: state.globalEnvironment};
const explicitBackendEnvironment = (definition) => {
  const step = definition.run.find((candidate) =>
    candidate.method === 'shell.run' &&
    (candidate.params.message || []).some((message) => message.includes('python launch.py'))
  );
  if (!step) throw new Error('backend launch step not found');
  return step.params.env;
};
(async () => {
  fs.writeFileSync('ENVIRONMENT', state.firstEnvironment, 'utf8');
  const first = explicitBackendEnvironment(await build(kernel));
  fs.writeFileSync('ENVIRONMENT', state.secondEnvironment, 'utf8');
  const second = explicitBackendEnvironment(await build(kernel));
  process.stdout.write(JSON.stringify({first, second}));
})().catch((error) => { console.error(error); process.exit(1); });
"""
            completed = subprocess.run(
                [
                    "node", "-e", loader,
                    json.dumps({
                        "firstEnvironment": first_environment,
                        "secondEnvironment": second_environment,
                        "globalEnvironment": global_environment,
                    }),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )

        captured = json.loads(completed.stdout)
        expected_params_env_keys = {
            "MAESTRO_ACCOUNTS_ENABLED",
            "MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED",
            "PINOKIO_SHARE_CLOUDFLARE",
            "PINOKIO_SHARE_LOCAL",
            "PINOKIO_STABLE_SHARE_URL",
            "CLOUDFLARE_API_TOKEN",
            "PINOKIO_STABLE_SHARE_UPDATE_SECRET",
            "SERVER_PORT",
        }
        # This is the explicit params.env overlay. Pinokio can still inherit
        # additional ENVIRONMENT values into the eventual child process.
        self.assertEqual(set(captured["first"]), expected_params_env_keys)
        self.assertEqual(set(captured["second"]), expected_params_env_keys)
        self.assertEqual(captured["first"]["MAESTRO_ACCOUNTS_ENABLED"], "true")
        self.assertEqual(captured["first"]["MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED"], "true")
        self.assertEqual(captured["first"]["PINOKIO_SHARE_CLOUDFLARE"], "true")
        self.assertEqual(captured["first"]["PINOKIO_SHARE_LOCAL"], "true")
        self.assertEqual(
            captured["first"]["PINOKIO_STABLE_SHARE_URL"],
            "https://first.example.workers.dev/# release one",
        )
        self.assertEqual(captured["second"]["MAESTRO_ACCOUNTS_ENABLED"], "false")
        self.assertEqual(captured["second"]["MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED"], "")
        self.assertEqual(captured["second"]["PINOKIO_SHARE_CLOUDFLARE"], "false")
        self.assertEqual(captured["second"]["PINOKIO_SHARE_LOCAL"], "")
        self.assertEqual(captured["second"]["PINOKIO_STABLE_SHARE_URL"], "")
        for params_environment in captured.values():
            self.assertNotIn("MAESTRO_ENVIRONMENT_SENTINEL", params_environment)
            self.assertNotIn(
                "MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED",
                params_environment,
            )
            self.assertEqual(params_environment["CLOUDFLARE_API_TOKEN"], "")
            self.assertEqual(
                params_environment["PINOKIO_STABLE_SHARE_UPDATE_SECRET"],
                "",
            )
            self.assertEqual(params_environment["SERVER_PORT"], 49152)

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
            if not any(
                helper in command
                for helper in ("register_share_url.py", "restart_status.py")
            ):
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
