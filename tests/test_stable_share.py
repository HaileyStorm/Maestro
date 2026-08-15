"""Offline contracts for the Workers redirect and launcher registration helper."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "app" / "scripts" / "register_share_url.py"
STATUS_HELPER_PATH = ROOT / "app" / "scripts" / "restart_status.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("maestro_stable_share_helper", HELPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load stable-share helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_status_helper():
    spec = importlib.util.spec_from_file_location(
        "maestro_restart_status_helper", STATUS_HELPER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load restart-status helper")
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


class _RawResponse(_Response):
    def __init__(self, content, *, status=200, headers=None):
        self.content = content
        self.status = status
        self.headers = headers or {}

    def read(self, limit):
        return self.content[:limit]


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


class RestartStatusClientTests(unittest.TestCase):
    def setUp(self):
        self.helper = _load_status_helper()
        self.stable = "https://maestro.account.workers.dev"
        self.secret = "operator-secret-" + ("s" * 48)
        self.environ = {
            "PINOKIO_STABLE_SHARE_URL": self.stable,
            "PINOKIO_STABLE_SHARE_UPDATE_SECRET": self.secret,
        }
        self.now = datetime(2026, 8, 12, 18, 30, tzinfo=timezone.utc)

    def payload(self, **overrides):
        values = {
            "state": "planned",
            "reason": "restart",
            "message": "Host restart is planned.",
            "ttl_seconds": 1800,
            "generation": "generation_0123456789",
            "now": self.now,
        }
        values.update(overrides)
        return self.helper.build_status_payload(**values)

    def test_stable_status_url_is_canonical_workers_dev_only(self):
        self.assertEqual(
            self.helper.canonical_stable_url(
                " HTTPS://Maestro.Account.Workers.Dev/ ",
            ),
            self.stable,
        )
        for value in (
            "http://maestro.account.workers.dev",
            "https://workers.dev",
            "https://maestro.account.workers.dev.evil.test",
            "https://maestro.account.workers.dev:443",
            "https://user@maestro.account.workers.dev",
            "https://maestro.account.workers.dev/status",
            "https://maestro.account.workers.dev?secret=x",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper.canonical_stable_url(value)

    def test_status_requests_use_env_only_secret_and_never_redirect(self):
        calls = []
        expected = self.payload()

        def opener(request, timeout):
            calls.append((request, timeout))
            return _Response({"ok": True, "status": expected})

        status = self.helper.show_status(
            environ=self.environ,
            open_request=opener,
        )
        self.assertEqual(status, expected)
        request, timeout = calls[0]
        self.assertEqual(
            request.full_url,
            self.stable + "/.well-known/maestro-share/status",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            request.get_header("Authorization"),
            f"Bearer {self.secret}",
        )
        self.assertEqual(timeout, 10)
        self.assertIsNone(
            self.helper._NoRedirect().redirect_request(
                request, None, 307, "redirect", {}, "https://evil.test/",
            ),
        )

        source = STATUS_HELPER_PATH.read_text(encoding="utf-8")
        self.assertNotIn('add_argument("--secret"', source)
        self.assertNotIn("print(update_secret", source)
        self.assertNotIn("print(self.secret", source)

    def test_put_and_delete_use_exact_request_shapes(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if request.get_method() == "PUT":
                return _Response({
                    "ok": True,
                    "status": json.loads(request.data),
                })
            return _Response({"ok": True, "cleared": True})

        eta = self.helper.build_eta(at="2026-08-12T18:40:00Z")
        payload = self.helper.build_status_payload(
            state="waiting_for_boundary",
            reason="maintenance",
            message="Host restart is queued.",
            ttl_seconds=1800,
            generation="generation_0123456789",
            eta=eta,
            now=self.now,
        )
        self.helper.set_status(
            payload,
            environ=self.environ,
            open_request=opener,
        )
        self.helper.clear_status(
            "generation_0123456789",
            environ=self.environ,
            open_request=opener,
        )

        self.assertEqual(calls[0].get_method(), "PUT")
        self.assertEqual(json.loads(calls[0].data), {
            "schema_version": 1,
            "generation": "generation_0123456789",
            "state": "waiting_for_boundary",
            "reason": "maintenance",
            "message": "Host restart is queued.",
            "issued_at": "2026-08-12T18:30:00Z",
            "expires_at": "2026-08-12T19:00:00Z",
            "eta": {"kind": "at", "at": "2026-08-12T18:40:00Z"},
        })
        self.assertEqual(calls[0].get_header("Content-type"), "application/json")
        self.assertEqual(calls[1].get_method(), "DELETE")
        self.assertEqual(
            json.loads(calls[1].data),
            {"generation": "generation_0123456789"},
        )

    def test_generation_utc_ttl_and_eta_are_strict_and_bounded(self):
        generated = self.helper.new_generation()
        self.assertRegex(generated, r"^[A-Za-z0-9_-]{16,64}$")
        with self.assertRaises(ValueError):
            self.helper.build_status_payload(
                state="planned",
                reason="restart",
                message="Restart planned.",
                ttl_seconds=60,
                generation="",
                now=self.now,
            )
        self.assertEqual(
            self.helper.canonical_utc("2026-08-12T18:30:00Z"),
            "2026-08-12T18:30:00Z",
        )
        self.assertEqual(
            self.helper.build_eta(
                earliest="2026-08-12T18:40:00Z",
                latest="2026-08-12T18:50:00Z",
            ),
            {
                "kind": "range",
                "earliest": "2026-08-12T18:40:00Z",
                "latest": "2026-08-12T18:50:00Z",
            },
        )
        for value in (
            "2026-08-12T18:30:00+00:00",
            "2026-08-12T18:30:00.000Z",
            "2026-08-12T18:30:00",
            "not-a-time",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.helper.canonical_utc(value)
        for ttl in (0, 86401):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.helper.build_status_payload(
                    state="planned",
                    reason="restart",
                    message="Restart planned.",
                    ttl_seconds=ttl,
                    now=self.now,
                )

        invalid_schema = self.payload()
        invalid_schema["schema_version"] = True
        with self.assertRaises(ValueError):
            self.helper.validate_status_payload(invalid_schema)
        with self.assertRaises(ValueError):
            self.payload(message="line one\nline two")
        for eta in (
            {"at": "2026-08-12T18:40:00Z"},
            {"kind": "at", "at": "2026-08-12T18:40:00Z", "extra": "x"},
            {
                "kind": "range",
                "earliest": "2026-08-12T18:50:00Z",
                "latest": "2026-08-12T18:40:00Z",
            },
            {"kind": "at", "at": "2026-08-12T18:29:59Z"},
            {"kind": "at", "at": "2026-08-12T19:00:01Z"},
        ):
            with self.subTest(eta=eta), self.assertRaises(ValueError):
                self.helper.build_status_payload(
                    state="planned",
                    reason="restart",
                    message="Restart planned.",
                    ttl_seconds=60,
                    eta=eta,
                    now=self.now,
                )

    def test_response_body_and_errors_are_bounded_and_redacted(self):
        oversized = b"{" + (b"x" * self.helper.MAX_RESPONSE_BYTES) + b"}"
        cases = (
            _RawResponse(b"{}", headers={"Content-Length": "999999"}),
            _RawResponse(oversized),
            _RawResponse(b"[]"),
            _RawResponse(b"{}", status=503),
        )
        for response in cases:
            with self.subTest(status=response.status), self.assertRaises(
                (TypeError, ValueError),
            ):
                self.helper.show_status(
                    environ=self.environ,
                    open_request=lambda _request, _timeout, value=response: value,
                )

        def failing_opener(_request, _timeout):
            raise URLError(self.secret)

        output = []
        with self.assertRaises(SystemExit) as raised:
            self.helper.main(
                ["show"],
                environ=self.environ,
                open_request=failing_opener,
                output=output.append,
            )
        self.assertEqual(str(raised.exception), "Maestro restart-status request failed")
        self.assertNotIn(self.secret, str(raised.exception))
        self.assertEqual(output, [])

    def test_cli_prints_only_content_free_summaries(self):
        calls = []
        current = self.payload(
            state="restarting",
            message="Private maintenance detail",
        )

        def opener(request, timeout):
            calls.append(request)
            if request.get_method() == "GET":
                return _Response({"ok": True, "status": current})
            if request.get_method() == "PUT":
                return _Response({
                    "ok": True,
                    "status": json.loads(request.data),
                })
            return _Response({"ok": True, "cleared": True})

        output = []
        self.helper.main(
            ["show"],
            environ=self.environ,
            open_request=opener,
            output=output.append,
        )
        self.helper.main(
            [
                "set",
                "--state", "verifying",
                "--reason", "restart",
                "--message", "Private maintenance detail",
            ],
            environ=self.environ,
            open_request=opener,
            output=output.append,
        )
        self.helper.main(
            ["clear", "--generation", "generation_0123456789"],
            environ=self.environ,
            open_request=opener,
            output=output.append,
        )
        self.assertEqual(output, [
            "MAESTRO_RESTART_STATUS restarting",
            "MAESTRO_RESTART_STATUS_SET verifying",
            "MAESTRO_RESTART_STATUS_CLEARED",
        ])
        self.assertNotIn("Private maintenance detail", " ".join(output))
        for request in calls:
            self.assertNotIn(self.secret, request.full_url)
            if request.data:
                self.assertNotIn(self.secret, request.data.decode("utf-8"))

    def test_clear_reports_generation_mismatch_truthfully(self):
        output = []
        result = self.helper.main(
            ["clear", "--generation", "generation_0123456789"],
            environ=self.environ,
            open_request=lambda _request, timeout: _Response({
                "ok": True,
                "cleared": False,
            }),
            output=output.append,
        )
        self.assertEqual(result, 1)
        self.assertEqual(output, ["MAESTRO_RESTART_STATUS_NOT_CLEARED"])

        with self.assertRaises(SystemExit) as raised:
            self.helper.main(
                ["clear", "--generation", ""],
                environ=self.environ,
                open_request=lambda _request, timeout: self.fail(
                    "invalid generations must fail before a request",
                ),
                output=output.append,
            )
        self.assertEqual(str(raised.exception), "Maestro restart-status request failed")


class StableShareSourceContracts(unittest.TestCase):
    def test_provision_helpers_parse_exact_stage_and_promotion_contract(self):
        helper_url = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision_helpers.mjs"
        ).as_uri()
        script = f"""
            import {{
              candidateMatches,
              canonicalizeManagedEnvironment,
              encodeCandidateMetadata,
              extractVersionUpload,
              parseCandidateMetadata,
              parseDeploymentReadback,
              parseProvisionArgs,
            }} from {json.dumps(helper_url)};
            const versionId = "12345678-1234-4abc-8def-123456789abc";
            const upload = extractVersionUpload(
              `Uploaded maestro-stable-share (1 sec)\nWorker Version ID: ${{versionId}}\n` +
              "Version Preview URL: https://12345678-maestro-stable-share.owner.workers.dev\\n",
              "maestro-stable-share",
            );
            const candidate = {{
              schemaVersion: 1,
              accountId: "a".repeat(32),
              workerName: "maestro-stable-share",
              namespaceId: "b".repeat(32),
              versionId,
              previewUrl: upload.previewUrl,
              stableUrl: upload.stableUrl,
              sourceSha256: "c".repeat(64),
              configSha256: "d".repeat(64),
              updateTokenSha256: "e".repeat(64),
            }};
            const encoded = encodeCandidateMetadata(candidate);
            const parsed = parseCandidateMetadata(encoded);
            const invalidArgs = [];
            for (const args of [["--promote"], ["--promote", "latest"], ["--stage", "extra"]]) {{
              try {{ parseProvisionArgs(args); invalidArgs.push(false); }}
              catch {{ invalidArgs.push(true); }}
            }}
            console.log(JSON.stringify({{
              defaultAction: parseProvisionArgs([]),
              stageAction: parseProvisionArgs(["--stage"]),
              promoteAction: parseProvisionArgs(["--promote", versionId.toUpperCase()]),
              upload,
              parsed,
              matches: candidateMatches(parsed, candidate),
              rejectsDigestDrift: !candidateMatches(parsed, {{ ...candidate, sourceSha256: "f".repeat(64) }}),
              rejectsChangedSecret: !candidateMatches(
                parsed,
                {{ ...candidate, updateTokenSha256: "a".repeat(64) }},
              ),
              activeReadback: parseDeploymentReadback(
                JSON.stringify({{ versions: [{{ version_id: versionId, percentage: 100 }}] }}),
                versionId,
              ),
              inactiveReadback: parseDeploymentReadback(
                JSON.stringify({{ versions: [
                  {{ version_id: versionId, percentage: 25 }},
                  {{ version_id: "87654321-4321-4abc-8def-abcdef123456", percentage: 75 }},
                ] }}),
                versionId,
              ),
              invalidReadback: parseDeploymentReadback(
                `{{"versions":[{{"version_id":"${{versionId}}","percentage":100}}],"versions":[]}}`,
                versionId,
              ),
              canonicalEnvironment: canonicalizeManagedEnvironment(
                "CLOUDFLARE_API_TOKEN=old\\nKEEP=value\\nCLOUDFLARE_API_TOKEN=leaked\\nPINOKIO_STABLE_SHARE_CANDIDATE=old\\nPINOKIO_STABLE_SHARE_CANDIDATE=stale\\n",
                {{ CLOUDFLARE_API_TOKEN: "", PINOKIO_STABLE_SHARE_CANDIDATE: "new" }},
                ["CLOUDFLARE_API_TOKEN", "PINOKIO_STABLE_SHARE_CANDIDATE"],
              ),
              stagedDefaultMode: canonicalizeManagedEnvironment(
                "KEEP=value\\n",
                {{ SHARE_MODE: "proxy", PINOKIO_STABLE_SHARE_CANDIDATE: "new" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              promotedDefaultMode: canonicalizeManagedEnvironment(
                "KEEP=value\\nPINOKIO_STABLE_SHARE_CANDIDATE=new\\nSHARE_MODE=proxy\\n",
                {{ SHARE_MODE: "proxy", PINOKIO_STABLE_SHARE_CANDIDATE: "" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              stagedRedirectMode: canonicalizeManagedEnvironment(
                "KEEP=value\\nSHARE_MODE=redirect\\n",
                {{ SHARE_MODE: "redirect", PINOKIO_STABLE_SHARE_CANDIDATE: "new" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              promotedRedirectMode: canonicalizeManagedEnvironment(
                "KEEP=value\\nPINOKIO_STABLE_SHARE_CANDIDATE=new\\nSHARE_MODE=redirect\\n",
                {{ SHARE_MODE: "redirect", PINOKIO_STABLE_SHARE_CANDIDATE: "" }},
                ["PINOKIO_STABLE_SHARE_CANDIDATE", "SHARE_MODE"],
              ),
              rejectsBadPreview: extractVersionUpload(
                `Worker Version ID: ${{versionId}}\nVersion Preview URL: https://other.workers.dev`,
                "maestro-stable-share",
              ) === null,
              invalidArgs,
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        version_id = "12345678-1234-4abc-8def-123456789abc"
        self.assertEqual(payload["defaultAction"], {"phase": "stage", "versionId": ""})
        self.assertEqual(payload["stageAction"], {"phase": "stage", "versionId": ""})
        self.assertEqual(
            payload["promoteAction"],
            {"phase": "promote", "versionId": version_id},
        )
        self.assertEqual(payload["upload"], {
            "versionId": version_id,
            "previewUrl": "https://12345678-maestro-stable-share.owner.workers.dev",
            "stableUrl": "https://maestro-stable-share.owner.workers.dev",
        })
        self.assertEqual(payload["parsed"]["versionId"], version_id)
        self.assertTrue(payload["matches"])
        self.assertTrue(payload["rejectsDigestDrift"])
        self.assertTrue(payload["rejectsChangedSecret"])
        self.assertTrue(payload["rejectsBadPreview"])
        self.assertTrue(payload["activeReadback"]["active"])
        self.assertFalse(payload["inactiveReadback"]["active"])
        self.assertIsNone(payload["invalidReadback"])
        self.assertEqual(
            payload["canonicalEnvironment"],
            "KEEP=value\nCLOUDFLARE_API_TOKEN=\nPINOKIO_STABLE_SHARE_CANDIDATE=new\n",
        )
        self.assertEqual(
            payload["stagedDefaultMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=new\nSHARE_MODE=proxy\n",
        )
        self.assertEqual(
            payload["promotedDefaultMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=\nSHARE_MODE=proxy\n",
        )
        self.assertEqual(
            payload["stagedRedirectMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=new\nSHARE_MODE=redirect\n",
        )
        self.assertEqual(
            payload["promotedRedirectMode"],
            "KEEP=value\nPINOKIO_STABLE_SHARE_CANDIDATE=\nSHARE_MODE=redirect\n",
        )
        self.assertEqual(payload["invalidArgs"], [True, True, True])

    def test_provisioner_keeps_secret_off_argv_and_temporary_wrangler_config(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("if (apiToken) childEnvironment.CLOUDFLARE_API_TOKEN = apiToken", source)
        self.assertIn("JSON.stringify({ UPDATE_TOKEN: updateSecret })", source)
        self.assertIn('"--secrets-file", secretsPath', source)
        self.assertNotIn('"--var", updateSecret', source)
        self.assertNotIn("writeFileSync(configPath, updateSecret", source)
        self.assertNotIn("updateSecret]", source)
        self.assertIn("observability: { enabled: false }", source)
        self.assertIn('vars: { SHARE_MODE: shareMode }', source)
        self.assertIn(
            'const shareMode = (setting("SHARE_MODE") || "proxy").trim().toLowerCase()',
            source,
        )
        self.assertIn("randomBytes(32).toString(\"hex\")", source)
        self.assertIn("CLOUDFLARE_WORKERS_FREE_CONFIRMED=true", source)
        self.assertIn("renameSync(temporaryEnvironment, environmentPath)", source)
        self.assertIn("chmodSync(environmentPath, 0o600)", source)
        self.assertIn(
            "const effectiveUpdates = { ...updates, SHARE_MODE: shareMode }",
            source,
        )
        self.assertIn(
            "currentEnvironment,\n    effectiveUpdates,\n    managedEnvironmentKeys",
            source,
        )
        self.assertNotIn("process.stdout.write(updateSecret", source)
        self.assertIn("delete childEnvironment.PINOKIO_STABLE_SHARE_UPDATE_SECRET", source)
        self.assertIn("delete childEnvironment.PINOKIO_STABLE_SHARE_CANDIDATE", source)
        self.assertIn("delete childEnvironment.UPDATE_TOKEN", source)

    def test_provisioner_stages_without_traffic_then_promotes_exact_candidate(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        helpers = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision_helpers.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('"versions", "upload", "--config", configPath', source)
        self.assertIn('"versions", "deploy", `${candidate.versionId}@100%`', source)
        self.assertIn('"--config", configPath, "-y"', source)
        self.assertNotIn('wrangler(["deploy"', source)
        self.assertNotIn('wrangler(["secret", "put"', source)
        self.assertIn("extractVersionUpload(commandOutput(uploaded), workerName)", source)
        self.assertIn("PINOKIO_STABLE_SHARE_CANDIDATE: encodeCandidateMetadata(candidate)", source)
        self.assertIn("candidateMatches(candidate, expected)", source)
        self.assertIn("sourceSha256", source)
        self.assertIn("configSha256", source)
        self.assertIn("updateTokenSha256", source)
        self.assertIn("PINOKIO_STABLE_SHARE_URL: candidate.stableUrl", source)
        self.assertLess(
            source.index('"versions", "deploy", `${candidate.versionId}@100%`'),
            source.index("PINOKIO_STABLE_SHARE_URL: candidate.stableUrl"),
        )
        stage_start = source.index('if (action.phase === "stage")')
        promote_start = source.index("} else {", stage_start)
        self.assertNotIn("PINOKIO_STABLE_SHARE_URL", source[stage_start:promote_start])
        self.assertIn("CANDIDATE_KEYS", helpers)
        self.assertNotIn("updateSecret", helpers)
        self.assertIn("canonicalizeManagedEnvironment", source)
        self.assertIn('"deployments", "status", "--json"', source)
        self.assertNotIn('"versions", "deployments", "status"', source)
        self.assertIn("parseDeploymentReadback(readbackResult.stdout", source)
        self.assertIn("promotion outcome is ambiguous", source)

        namespace_assignment = source.index(
            "namespaceId = extractNamespaceId(commandOutput(created))",
        )
        namespace_persist = source.index("replaceEnvironmentValues({", namespace_assignment)
        version_upload = source.index('"versions", "upload"')
        self.assertLess(namespace_assignment, namespace_persist)
        self.assertLess(namespace_persist, version_upload)

    def test_oauth_fallback_is_logged_out_and_verified(self):
        source = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision.mjs"
        ).read_text(encoding="utf-8")
        helpers = (
            ROOT / "cloudflare" / "stable-share-worker" / "provision_helpers.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn('const oauthLane = !apiToken', source)
        self.assertIn('wrangler(["whoami", "--json"])', source)
        self.assertNotIn('wrangler(["whoami"])', source)
        self.assertIn('wrangler(["logout"])', source)
        self.assertIn("isWhoamiLoggedOut(commandOutput(verification))", source)
        self.assertIn("oauthLogoutVerified = true", source)
        self.assertIn("if (oauthAuthenticated && !oauthLogoutVerified)", source)
        self.assertIn("parsed.loggedIn !== true", helpers)
        self.assertIn("Array.isArray(parsed.accounts)", helpers)
        self.assertNotIn("parsed.memberships", helpers)

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
