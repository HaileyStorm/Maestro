"""Offline contracts for the Workers redirect and launcher registration helper."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "app" / "scripts" / "register_share_url.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("maestro_stable_share_helper", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load stable-share helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode("utf-8")


class StableShareRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.helper = _load_helper()
        self.local = "http://127.0.0.1:7860"
        self.quick = "https://current-tunnel.trycloudflare.com"
        self.stable = "https://maestro.account.workers.dev"
        self.secret = "s" * 64

    def opener(self, *, health_target=None, fail_update=False):
        calls = []
        expected_health_target = health_target or self.quick

        def open_request(request, timeout):
            calls.append(request)
            if request.full_url.endswith("/.well-known/maestro-share/target"):
                if fail_update:
                    raise URLError("unavailable")
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            if request.full_url.endswith("/.well-known/maestro-share/health"):
                return _Response({
                    "ok": True, "configured": True, "target": expected_health_target,
                })
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        return calls, open_request

    def test_healthy_worker_is_updated_verified_and_preferred(self):
        calls, opener = self.opener()
        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].get_method(), "PUT")
        self.assertEqual(json.loads(calls[0].data), {"target": self.quick})
        self.assertEqual(calls[1].get_method(), "GET")
        self.assertEqual(calls[0].get_header("Authorization"), f"Bearer {self.secret}")
        self.assertEqual(calls[1].get_header("Authorization"), f"Bearer {self.secret}")
        registration = calls[2]
        self.assertIsNone(registration.get_header("Authorization"))
        self.assertEqual(json.loads(registration.data), {
            "share_url": self.stable,
            "quick_tunnel_url": self.quick,
            "stable_verified": True,
        })

    def test_all_worker_and_local_requests_use_fixed_json_client_headers(self):
        calls, opener = self.opener()
        self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(len(calls), 3)
        for request in calls:
            with self.subTest(url=request.full_url):
                self.assertEqual(
                    request.get_header("User-agent"),
                    "Maestro-Stable-Share/1.0",
                )
                self.assertEqual(request.get_header("Accept"), "application/json")

        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('_REQUEST_USER_AGENT = "Maestro-Stable-Share/1.0"', source)
        self.assertNotIn("Python-urllib", source)

    def test_unavailable_or_stale_worker_falls_back_to_current_quick_tunnel(self):
        cases = (
            self.opener(fail_update=True),
            self.opener(health_target="https://stale.trycloudflare.com"),
        )
        for calls, opener in cases:
            with self.subTest(case=len(calls)):
                selected = self.helper.register_share_url(
                    self.local,
                    self.quick,
                    stable_url=self.stable,
                    update_secret=self.secret,
                    open_request=opener,
                    sleep=lambda _seconds: None,
                )
                self.assertEqual(selected, (self.quick, "quick"))
                registration = calls[-1]
                self.assertEqual(json.loads(registration.data), {
                    "share_url": self.quick,
                    "quick_tunnel_url": self.quick,
                    "stable_verified": False,
                })

    def test_health_poll_allows_delayed_kv_visibility(self):
        calls = []
        health_reads = 0

        def opener(request, timeout):
            nonlocal health_reads
            calls.append(request)
            if request.full_url.endswith("/.well-known/maestro-share/target"):
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            if request.full_url.endswith("/.well-known/maestro-share/health"):
                health_reads += 1
                target = (
                    "https://stale.trycloudflare.com"
                    if health_reads < 3 else self.quick
                )
                return _Response({"ok": True, "configured": True, "target": target})
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        delays = []
        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=delays.append,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(health_reads, 3)
        self.assertEqual(delays, [5.0, 5.0])

    def test_health_poll_retries_negative_kv_cache_503(self):
        health_reads = 0

        def opener(request, timeout):
            nonlocal health_reads
            if request.full_url.endswith("/.well-known/maestro-share/target"):
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            if request.full_url.endswith("/.well-known/maestro-share/health"):
                health_reads += 1
                if health_reads < 3:
                    raise HTTPError(request.full_url, 503, "not ready", {}, None)
                return _Response({
                    "ok": True, "configured": True, "target": self.quick,
                })
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        delays = []
        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=delays.append,
        )
        self.assertEqual(selected, (self.stable, "stable"))
        self.assertEqual(health_reads, 3)
        self.assertEqual(delays, [5.0, 5.0])

    def test_secret_never_enters_local_api_body_or_error_text(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if request.full_url.startswith(self.stable):
                raise URLError(self.secret)
            body = json.loads(request.data.decode("utf-8"))
            return _Response({"status": "ok", "share_url": body["share_url"]})

        selected = self.helper.register_share_url(
            self.local,
            self.quick,
            stable_url=self.stable,
            update_secret=self.secret,
            open_request=opener,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(selected, (self.quick, "quick"))
        local_request = calls[-1]
        self.assertNotIn(self.secret, local_request.full_url)
        self.assertNotIn(self.secret, local_request.data.decode("utf-8"))
        self.assertIsNone(local_request.get_header("Authorization"))
        source = HELPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("--secret", source)
        self.assertNotIn("print(update_secret", source)
        self.assertIn("_NoRedirect", source)

    def test_stable_origin_validation_is_workers_dev_only(self):
        self.assertEqual(
            self.helper._canonical_workers_dev_url(self.stable + "/"), self.stable,
        )
        for value in (
            "http://maestro.account.workers.dev",
            "https://workers.dev",
            "https://maestro.account.workers.dev.evil.test",
            "https://maestro.account.workers.dev/path",
            "https://maestro.account.workers.dev?token=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper._canonical_workers_dev_url(value)


class StableShareSourceContracts(unittest.TestCase):
    def test_provisioner_keeps_secret_off_argv_and_temporary_wrangler_config(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("if (apiToken) childEnvironment.CLOUDFLARE_API_TOKEN = apiToken", source)
        self.assertIn('{ input: `${updateSecret}\\n`', source)
        self.assertNotIn('"--var", updateSecret', source)
        self.assertNotIn("writeFileSync(configPath, updateSecret", source)
        self.assertIn("observability: { enabled: false }", source)
        self.assertIn("randomBytes(32).toString(\"hex\")", source)
        self.assertIn("CLOUDFLARE_WORKERS_FREE_CONFIRMED=true", source)
        self.assertIn("renameSync(temporaryEnvironment, environmentPath)", source)
        self.assertIn("chmodSync(environmentPath, 0o600)", source)
        self.assertNotIn("process.stdout.write(updateSecret", source)

    def test_oauth_fallback_is_logged_out_and_verified(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('const oauthLane = !apiToken', source)
        self.assertIn('wrangler(["whoami"])', source)
        self.assertIn('wrangler(["logout"])', source)
        self.assertIn("oauthLogoutVerified = true", source)
        self.assertIn("if (oauthAuthenticated && !oauthLogoutVerified)", source)

    def test_application_process_does_not_inherit_cloudflare_provisioning_token(self):
        mask = (ROOT / "launcher_secret_env.js").read_text(encoding="utf-8")
        self.assertIn('CLOUDFLARE_API_TOKEN: ""', mask)
        self.assertIn('PINOKIO_STABLE_SHARE_UPDATE_SECRET: ""', mask)

    def test_example_has_only_blank_secret_placeholders(self):
        environment = (ROOT / "ENVIRONMENT.example").read_text(encoding="utf-8")
        self.assertIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET=\n", environment)
        self.assertIn("CLOUDFLARE_API_TOKEN=\n", environment)
        self.assertIn("CLOUDFLARE_WORKERS_FREE_CONFIRMED=\n", environment)
        self.assertNotIn("PINOKIO_STABLE_SHARE_UPDATE_SECRET=s", environment)


if __name__ == "__main__":
    unittest.main()
