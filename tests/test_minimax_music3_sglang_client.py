"""Focused tests for the fail-closed Music 3 SGLang HTTP boundary."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import wave


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services import minimax_music3_sglang_client as client_module  # noqa: E402
from services.minimax_music3_sglang_client import (  # noqa: E402
    AUTHORIZATION_AUDIENCE,
    AUTHORIZATION_SCHEMA,
    AUTHORIZATION_SCOPE,
    GENERATE_PATH,
    HEALTH_PATH,
    MAX_AUTHORIZATION_BYTES,
    MAX_JSON_RESPONSE_BYTES,
    MODELS_PATH,
    Music3ExternalAuthorizationEvidence,
    Music3RequestDisconnected,
    Music3SglangClient,
    Music3SglangClientError,
    Music3TransportRequest,
    Music3TransportResponse,
    validate_music3_external_authorization_response,
)
from services.minimax_music3_sglang_contract import (  # noqa: E402
    MUSIC3_HF_EXACT_REVISION,
    MUSIC3_MODEL_ID,
    REQUIRED_EXTERNAL_GATES,
    MusicModelContractError,
    bind_music3_sglang_source,
)


BASE_URL = "http://127.0.0.1:31000"
AUTH_URL = "https://licenses.example.test/v1/music3/authorization"
RUNTIME_REVISION = "sha256:" + ("a" * 64)
PEER_CERTIFICATE = "sha256:" + ("b" * 64)


def _request(**updates):
    value = {
        "model": MUSIC3_MODEL_ID,
        "input": "Locally authored lyrics",
        "instructions": "An energetic electronic arrangement",
        "response_format": "wav",
        "seed": 42,
        "max_new_tokens": 9_000,
        "stream": False,
    }
    value.update(updates)
    return value


def _health(**updates):
    value = {
        "status": "healthy",
        "running": True,
        "stages": ["music-ar", "music-acoustic"],
        "entry_stage": "music-ar",
        "total_requests": 1,
        "pending_completions": 0,
        "request_states": {"completed": 1},
    }
    value.update(updates)
    return value


def _models(**updates):
    entry = {
        "id": MUSIC3_MODEL_ID,
        "object": "model",
        "created": 0,
        "owned_by": "sglang-omni",
        "permission": [{
            "id": "modelperm-default",
            "object": "model_permission",
            "allow_create_engine": False,
            "allow_sampling": True,
            "allow_logprobs": True,
        }],
        "root": MUSIC3_MODEL_ID,
    }
    entry.update(updates)
    return {"object": "list", "data": [entry]}


def _wav_bytes(frames=8):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(32_000)
        writer.writeframes(b"\x00" * (frames * 2 * 2))
    return output.getvalue()


def _json_response(request, value, *, status=200, url=None, headers=None):
    body = json.dumps(value, separators=(",", ":")).encode()
    return Music3TransportResponse(
        status_code=status,
        url=url or request.url,
        headers=headers or (("content-type", "application/json"),),
        body=body,
    )


def _wav_response(request, body=None, *, status=200, url=None, headers=None):
    return Music3TransportResponse(
        status_code=status,
        url=url or request.url,
        headers=headers or (("content-type", "audio/wav"),),
        body=_wav_bytes() if body is None else body,
    )


def _authorization_document(**updates):
    value = {
        "schema": AUTHORIZATION_SCHEMA,
        "issuer": "https://licenses.example.test",
        "evidence_id": "music3-authorization-1",
        "subject_id": "owner-1",
        "audience": AUTHORIZATION_AUDIENCE,
        "model_id": MUSIC3_MODEL_ID,
        "model_revision": MUSIC3_HF_EXACT_REVISION,
        "runtime_source_revision": RUNTIME_REVISION,
        "base_url": BASE_URL,
        "scope": AUTHORIZATION_SCOPE,
        "approved_gates": list(REQUIRED_EXTERNAL_GATES),
        "issued_at_unix": 100,
        "expires_at_unix": 200,
    }
    value.update(updates)
    return value


def _authorization(**document_updates):
    body = json.dumps(
        _authorization_document(**document_updates),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return validate_music3_external_authorization_response(
        request_url=AUTH_URL,
        final_url=AUTH_URL,
        status_code=200,
        headers=(("content-type", "application/json"),),
        body=body,
        peer_certificate_sha256=PEER_CERTIFICATE,
        trusted_peer_certificate_sha256=PEER_CERTIFICATE,
        source_binding=bind_music3_sglang_source(RUNTIME_REVISION),
        base_url=BASE_URL,
        now_unix=150,
    )


def _authorization_for_runtime(runtime_revision):
    body = json.dumps(
        _authorization_document(runtime_source_revision=runtime_revision),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return validate_music3_external_authorization_response(
        request_url=AUTH_URL,
        final_url=AUTH_URL,
        status_code=200,
        headers=(("content-type", "application/json"),),
        body=body,
        peer_certificate_sha256=PEER_CERTIFICATE,
        trusted_peer_certificate_sha256=PEER_CERTIFICATE,
        source_binding=bind_music3_sglang_source(runtime_revision),
        base_url=BASE_URL,
        now_unix=150,
    )


class _ScriptedTransport:
    def __init__(self, handlers):
        self.handlers = list(handlers)
        self.calls = []

    async def __call__(self, request):
        self.calls.append(request)
        handler = self.handlers.pop(0)
        if isinstance(handler, BaseException):
            raise handler
        if callable(handler):
            value = handler(request)
            if asyncio.iscoroutine(value):
                return await value
            return value
        return handler


def _healthy_transport(*, wav=None):
    return _ScriptedTransport([
        lambda request: _json_response(request, _health()),
        lambda request: _json_response(request, _models()),
        lambda request: _wav_response(request, wav),
    ])


class ConfigurationTests(unittest.TestCase):
    def test_only_exact_canonical_ipv4_loopback_base_is_accepted(self):
        binding = bind_music3_sglang_source(RUNTIME_REVISION)
        transport = _ScriptedTransport([])
        client = Music3SglangClient(
            base_url=BASE_URL,
            source_binding=binding,
            transport=transport,
        )
        self.assertEqual(client.base_url, BASE_URL)
        invalid = (
            None,
            "",
            "http://localhost:31000",
            "http://[::1]:31000",
            "http://0.0.0.0:31000",
            "https://127.0.0.1:31000",
            "http://127.0.0.1",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://127.0.0.1:31000/",
            "http://127.0.0.1:31000/path",
            "http://127.0.0.1:31000?query=1",
            "http://user@127.0.0.1:31000",
            "HTTP://127.0.0.1:31000",
            "http://127.0.0.1:031000",
        )
        for base_url in invalid:
            with self.subTest(base_url=base_url):
                with self.assertRaises(MusicModelContractError):
                    Music3SglangClient(
                        base_url=base_url,
                        source_binding=binding,
                        transport=transport,
                    )

    def test_timeout_bounds_source_binding_and_transport_are_validated(self):
        binding = bind_music3_sglang_source(RUNTIME_REVISION)
        for value in (0, -1, True, float("nan"), 3601, "5"):
            with self.subTest(value=value):
                with self.assertRaises(MusicModelContractError):
                    Music3SglangClient(
                        base_url=BASE_URL,
                        source_binding=binding,
                        transport=_ScriptedTransport([]),
                        probe_timeout_seconds=value,
                    )
        with self.assertRaises(MusicModelContractError):
            Music3SglangClient(
                base_url=BASE_URL,
                source_binding=object(),
                transport=_ScriptedTransport([]),
            )
        with self.assertRaises(MusicModelContractError):
            Music3SglangClient(
                base_url=BASE_URL,
                source_binding=binding,
                transport=object(),
            )

    def test_transport_values_are_exact_immutable_and_bounded(self):
        request = Music3TransportRequest(
            method="GET",
            url=BASE_URL + HEALTH_PATH,
            headers=(("accept", "application/json"),),
            body=None,
            timeout_seconds=1,
            max_response_bytes=10,
        )
        with self.assertRaises(FrozenInstanceError):
            request.method = "POST"
        invalid_headers = (
            (("Accept", "application/json"),),
            (("accept", "one"), ("accept", "two")),
            (("accept", "line\nbreak"),),
            (["accept", "application/json"],),
        )
        for headers in invalid_headers:
            with self.subTest(headers=headers):
                with self.assertRaises(MusicModelContractError):
                    Music3TransportRequest(
                        "GET", BASE_URL + HEALTH_PATH, headers, None, 1, 10
                    )
        for url in (
            BASE_URL + "/unknown",
            BASE_URL + HEALTH_PATH + "?redirect=1",
            "http://127.0.0.1:31000.evil.test" + HEALTH_PATH,
            "http://user@127.0.0.1:31000" + HEALTH_PATH,
        ):
            with self.subTest(url=url):
                with self.assertRaises(MusicModelContractError):
                    Music3TransportRequest(
                        "GET", url, (("accept", "application/json"),), None, 1, 10
                    )


class AuthorizationTests(unittest.TestCase):
    def test_exact_server_authored_document_mints_immutable_evidence(self):
        evidence = _authorization()
        self.assertEqual(evidence.issuer, "https://licenses.example.test")
        self.assertEqual(evidence.base_url, BASE_URL)
        self.assertEqual(evidence.runtime_source_revision, RUNTIME_REVISION)
        self.assertEqual(evidence.peer_certificate_sha256, PEER_CERTIFICATE)
        self.assertRegex(evidence.document_sha256, r"^sha256:[0-9a-f]{64}$")
        with self.assertRaises(FrozenInstanceError):
            evidence.expires_at_unix = 300

    def test_client_boolean_or_direct_evidence_construction_cannot_open_gate(self):
        with self.assertRaises(MusicModelContractError):
            Music3ExternalAuthorizationEvidence(
                "https://licenses.example.test",
                "id",
                "subject",
                BASE_URL,
                RUNTIME_REVISION,
                100,
                200,
                "sha256:" + ("1" * 64),
                PEER_CERTIFICATE,
            )

    def test_authorization_rejects_redirect_origin_status_type_and_peer_failures(self):
        binding = bind_music3_sglang_source(RUNTIME_REVISION)
        body = json.dumps(_authorization_document(), separators=(",", ":")).encode()
        base = {
            "request_url": AUTH_URL,
            "final_url": AUTH_URL,
            "status_code": 200,
            "headers": (("content-type", "application/json"),),
            "body": body,
            "peer_certificate_sha256": PEER_CERTIFICATE,
            "trusted_peer_certificate_sha256": PEER_CERTIFICATE,
            "source_binding": binding,
            "base_url": BASE_URL,
            "now_unix": 150,
        }
        cases = (
            {"final_url": "https://other.example.test/v1/music3/authorization"},
            {"request_url": "http://licenses.example.test/v1/music3/authorization"},
            {"status_code": 204},
            {"headers": (("content-type", "application/json; charset=utf-8"),)},
            {"headers": (("content-type", "application/json"), ("location", "/other"))},
            {"headers": (("content-type", "application/json"), ("content-length", "1"))},
            {"headers": (("content-type", "application/json"), ("content-length", "9" * 8_192))},
            {"peer_certificate_sha256": True},
            {"peer_certificate_sha256": "sha256:" + ("A" * 64)},
            {"trusted_peer_certificate_sha256": "sha256:" + ("c" * 64)},
            {"body": b"{" + (b" " * MAX_AUTHORIZATION_BYTES) + b"}"},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                with self.assertRaises(MusicModelContractError):
                    validate_music3_external_authorization_response(**(base | updates))

    def test_authorization_document_is_exact_and_bound_to_every_external_gate(self):
        binding = bind_music3_sglang_source(RUNTIME_REVISION)
        base = {
            "request_url": AUTH_URL,
            "final_url": AUTH_URL,
            "status_code": 200,
            "headers": (("content-type", "application/json"),),
            "peer_certificate_sha256": PEER_CERTIFICATE,
            "trusted_peer_certificate_sha256": PEER_CERTIFICATE,
            "source_binding": binding,
            "base_url": BASE_URL,
            "now_unix": 150,
        }
        documents = (
            _authorization_document(approved_gates=["license_approval"]),
            _authorization_document(approved_gates=True),
            _authorization_document(model_id="replacement"),
            _authorization_document(runtime_source_revision="sha256:" + ("c" * 64)),
            _authorization_document(base_url="http://127.0.0.1:32000"),
            _authorization_document(issuer="https://other.example.test"),
            _authorization_document(scope="hosted_service"),
            _authorization_document(issued_at_unix=200, expires_at_unix=300),
            _authorization_document(issued_at_unix=0, expires_at_unix=4000),
            _authorization_document(extra="unexpected"),
        )
        for document in documents:
            with self.subTest(document=document):
                body = json.dumps(document, separators=(",", ":")).encode()
                with self.assertRaises(MusicModelContractError):
                    validate_music3_external_authorization_response(**base, body=body)


class ClientFlowTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, transport, **updates):
        values = {
            "base_url": BASE_URL,
            "source_binding": bind_music3_sglang_source(RUNTIME_REVISION),
            "transport": transport,
            "probe_timeout_seconds": 0.25,
            "generation_timeout_seconds": 0.25,
            "clock": lambda: 150.0,
        }
        values.update(updates)
        return Music3SglangClient(**values)

    async def test_generation_uses_exact_order_wire_contract_and_content_free_provenance(self):
        wav = _wav_bytes(frames=16)
        transport = _healthy_transport(wav=wav)
        result = await self._client(transport).generate(
            _request(),
            authorization=_authorization(),
            disconnect_event=asyncio.Event(),
        )

        self.assertEqual([call.method for call in transport.calls], ["GET", "GET", "POST"])
        self.assertEqual(
            [call.url for call in transport.calls],
            [BASE_URL + HEALTH_PATH, BASE_URL + MODELS_PATH, BASE_URL + GENERATE_PATH],
        )
        self.assertEqual(transport.calls[0].headers, (("accept", "application/json"),))
        self.assertEqual(transport.calls[1].headers, (("accept", "application/json"),))
        self.assertEqual(transport.calls[2].headers, (
            ("accept", "audio/wav"),
            ("content-type", "application/json"),
        ))
        self.assertTrue(all(call.redirect_policy == "error" for call in transport.calls))
        self.assertIsNone(transport.calls[0].body)
        wire = json.loads(transport.calls[2].body)
        self.assertEqual(wire, _request())
        self.assertEqual(result.audio_bytes, wav)
        rendered = result.provenance.to_mapping()
        self.assertEqual(set(rendered), {
            "model_id",
            "model_revision",
            "runtime_source_revision",
            "request",
            "authorization_document_sha256",
            "response_sha256",
            "response_bytes",
            "frame_count",
            "duration_seconds",
        })
        self.assertNotIn("Locally authored lyrics", repr(rendered))
        self.assertNotIn("electronic arrangement", repr(rendered))
        request_fingerprint = client_module.hashlib.sha256(
            transport.calls[2].body
        ).hexdigest()
        self.assertNotIn(request_fingerprint, repr(rendered))
        self.assertEqual(rendered["request"], {
            "lyrics_supplied": True,
            "instructions_supplied": True,
            "response_format": "wav",
            "seed": 42,
            "max_new_tokens": 9_000,
            "stream": False,
        })
        self.assertEqual(rendered["response_sha256"], result.provenance.response.sha256)

    async def test_invalid_request_or_authorization_makes_no_transport_call(self):
        transport = _healthy_transport()
        client = self._client(transport)
        cases = (
            (_request(model="replacement"), _authorization()),
            (_request(), True),
            (_request(), object()),
        )
        for request, authorization in cases:
            with self.subTest(authorization=type(authorization).__name__):
                with self.assertRaises(MusicModelContractError):
                    await client.generate(
                        request,
                        authorization=authorization,
                        disconnect_event=asyncio.Event(),
                    )
        self.assertEqual(transport.calls, [])

    async def test_expired_or_wrong_binding_authorization_makes_no_call(self):
        transport = _healthy_transport()
        expired_client = self._client(transport, clock=lambda: 200.0)
        with self.assertRaises(MusicModelContractError):
            await expired_client.generate(
                _request(),
                authorization=_authorization(),
                disconnect_event=asyncio.Event(),
            )
        wrong_runtime = "sha256:" + ("d" * 64)
        with self.assertRaises(MusicModelContractError):
            await self._client(transport).generate(
                _request(),
                authorization=_authorization_for_runtime(wrong_runtime),
                disconnect_event=asyncio.Event(),
            )
        other_transport = _healthy_transport()
        other_client = Music3SglangClient(
            base_url="http://127.0.0.1:32000",
            source_binding=bind_music3_sglang_source(RUNTIME_REVISION),
            transport=other_transport,
            clock=lambda: 150,
        )
        with self.assertRaises(MusicModelContractError):
            await other_client.generate(
                _request(),
                authorization=_authorization(),
                disconnect_event=asyncio.Event(),
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(other_transport.calls, [])

    async def test_authorization_is_rechecked_between_stages(self):
        values = iter((150.0, 200.0))
        transport = _healthy_transport()
        client = self._client(transport, clock=lambda: next(values))
        with self.assertRaisesRegex(MusicModelContractError, "expired"):
            await client.generate(
                _request(),
                authorization=_authorization(),
                disconnect_event=asyncio.Event(),
            )
        self.assertEqual(len(transport.calls), 1)

    async def test_health_must_validate_before_model_or_generation_calls(self):
        transport = _ScriptedTransport([
            lambda request: _json_response(request, _health(running=False)),
            lambda request: self.fail("model probe must not run"),
        ])
        with self.assertRaises(Music3SglangClientError) as caught:
            await self._client(transport).generate(
                _request(),
                authorization=_authorization(),
                disconnect_event=asyncio.Event(),
            )
        self.assertEqual((caught.exception.code, caught.exception.stage), (
            "invalid_response", "health"
        ))
        self.assertEqual(len(transport.calls), 1)

    async def test_model_identity_must_validate_before_generation(self):
        transport = _ScriptedTransport([
            lambda request: _json_response(request, _health()),
            lambda request: _json_response(request, _models(id="replacement")),
            lambda request: self.fail("generation must not run"),
        ])
        with self.assertRaises(Music3SglangClientError) as caught:
            await self._client(transport).generate(
                _request(),
                authorization=_authorization(),
                disconnect_event=asyncio.Event(),
            )
        self.assertEqual((caught.exception.code, caught.exception.stage), (
            "invalid_response", "models"
        ))
        self.assertEqual(len(transport.calls), 2)

    async def test_redirect_or_changed_origin_is_rejected_at_each_stage(self):
        scenarios = (
            [lambda request: _json_response(request, _health(), url=BASE_URL + "/other")],
            [
                lambda request: _json_response(request, _health()),
                lambda request: _json_response(
                    request, _models(), url="http://127.0.0.1:32000" + MODELS_PATH
                ),
            ],
            [
                lambda request: _json_response(request, _health()),
                lambda request: _json_response(request, _models()),
                lambda request: _wav_response(
                    request,
                    url="http://127.0.0.1:32000" + GENERATE_PATH,
                ),
            ],
        )
        for handlers in scenarios:
            with self.subTest(stage_count=len(handlers)):
                transport = _ScriptedTransport(handlers)
                with self.assertRaises(Music3SglangClientError) as caught:
                    await self._client(transport).generate(
                        _request(),
                        authorization=_authorization(),
                        disconnect_event=asyncio.Event(),
                    )
                self.assertEqual(caught.exception.code, "redirect_or_origin")
                self.assertEqual(len(transport.calls), len(handlers))

    async def test_transport_cannot_report_followed_redirect_even_if_final_url_returns(self):
        def followed(request):
            response = _json_response(request, _health())
            return Music3TransportResponse(
                response.status_code,
                response.url,
                response.headers,
                response.body,
                redirect_count=2,
            )

        transport = _ScriptedTransport([followed])
        with self.assertRaises(Music3SglangClientError) as caught:
            await self._client(transport).generate(
                _request(), authorization=_authorization(),
                disconnect_event=asyncio.Event()
            )
        self.assertEqual(caught.exception.code, "redirect_or_origin")
        self.assertEqual(transport.calls[0].redirect_policy, "error")

    async def test_location_header_is_rejected_even_on_200(self):
        transport = _ScriptedTransport([
            lambda request: _json_response(
                request,
                _health(),
                headers=(
                    ("content-type", "application/json"),
                    ("location", BASE_URL + "/other"),
                ),
            ),
        ])
        with self.assertRaises(Music3SglangClientError) as caught:
            await self._client(transport).generate(
                _request(), authorization=_authorization(), disconnect_event=asyncio.Event()
            )
        self.assertEqual(caught.exception.code, "redirect_or_origin")

    async def test_http_status_content_type_and_length_are_exact(self):
        health_body = json.dumps(_health(), separators=(",", ":")).encode()
        scenarios = (
            Music3TransportResponse(
                503, BASE_URL + HEALTH_PATH,
                (("content-type", "application/json"),), b"private failure detail"
            ),
            Music3TransportResponse(
                200, BASE_URL + HEALTH_PATH,
                (("content-type", "application/json; charset=utf-8"),), health_body
            ),
            Music3TransportResponse(
                200, BASE_URL + HEALTH_PATH,
                (("content-type", "application/json"), ("content-length", "1")),
                health_body,
            ),
            Music3TransportResponse(
                200, BASE_URL + HEALTH_PATH,
                (("content-type", "application/json"), ("content-length", "9" * 8_192)),
                health_body,
            ),
        )
        for response in scenarios:
            with self.subTest(response=response):
                transport = _ScriptedTransport([response])
                with self.assertRaises(Music3SglangClientError) as caught:
                    await self._client(transport).generate(
                        _request(), authorization=_authorization(),
                        disconnect_event=asyncio.Event()
                    )
                rendered = str(caught.exception)
                self.assertNotIn("private failure detail", rendered)

    async def test_malformed_and_oversized_json_responses_are_rejected(self):
        bodies = (
            b'{"status":"healthy","status":"healthy"}',
            b'{"status":NaN}',
            b"\xef\xbb\xbf{}",
            b"[]",
            b"{",
            b"x" * (MAX_JSON_RESPONSE_BYTES + 1),
            b'{"huge":' + (b"9" * 10_000) + b"}",
        )
        for body in bodies:
            with self.subTest(prefix=body[:20], size=len(body)):
                transport = _ScriptedTransport([
                    Music3TransportResponse(
                        200,
                        BASE_URL + HEALTH_PATH,
                        (("content-type", "application/json"),),
                        body,
                    )
                ])
                with self.assertRaises(Music3SglangClientError):
                    await self._client(transport).generate(
                        _request(), authorization=_authorization(),
                        disconnect_event=asyncio.Event()
                    )

    async def test_malformed_and_oversized_wav_responses_are_rejected(self):
        malformed = _healthy_transport(wav=b"not a wav")
        with self.assertRaises(Music3SglangClientError) as malformed_error:
            await self._client(malformed).generate(
                _request(), authorization=_authorization(),
                disconnect_event=asyncio.Event()
            )
        self.assertEqual(malformed_error.exception.code, "invalid_response")

        wav = _wav_bytes()
        oversized = _healthy_transport(wav=wav)
        with mock.patch.object(client_module, "MAX_WAV_BYTES", len(wav) - 1):
            with self.assertRaises(Music3SglangClientError) as size_error:
                await self._client(oversized).generate(
                    _request(), authorization=_authorization(),
                    disconnect_event=asyncio.Event()
                )
        self.assertEqual(size_error.exception.code, "response_too_large")

    async def test_hostile_transport_return_type_and_subclass_are_rejected(self):
        class ResponseSubclass(Music3TransportResponse):
            pass

        responses = (
            {"status_code": 200},
            ResponseSubclass(
                200,
                BASE_URL + HEALTH_PATH,
                (("content-type", "application/json"),),
                json.dumps(_health()).encode(),
            ),
        )
        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                transport = _ScriptedTransport([response])
                with self.assertRaises(Music3SglangClientError) as caught:
                    await self._client(transport).generate(
                        _request(), authorization=_authorization(),
                        disconnect_event=asyncio.Event()
                    )
                self.assertEqual(caught.exception.code, "transport_contract")

    async def test_transport_errors_are_redacted(self):
        transport = _ScriptedTransport([
            RuntimeError("secret upstream body and private path /tmp/private")
        ])
        with self.assertRaises(Music3SglangClientError) as caught:
            await self._client(transport).generate(
                _request(), authorization=_authorization(),
                disconnect_event=asyncio.Event()
            )
        rendered = str(caught.exception)
        self.assertEqual(caught.exception.code, "transport_error")
        self.assertNotIn("secret", rendered)
        self.assertNotIn("/tmp/private", rendered)

    async def test_synchronous_hostile_transport_is_rejected_or_redacted(self):
        for transport, expected in (
            (lambda _request: {"status_code": 200}, "transport_contract"),
            (
                lambda _request: (_ for _ in ()).throw(
                    RuntimeError("secret synchronous transport detail")
                ),
                "transport_error",
            ),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(Music3SglangClientError) as caught:
                    await self._client(transport).generate(
                        _request(), authorization=_authorization(),
                        disconnect_event=asyncio.Event()
                    )
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn("secret", str(caught.exception))


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, transport, timeout=0.05, grace=0.01):
        return Music3SglangClient(
            base_url=BASE_URL,
            source_binding=bind_music3_sglang_source(RUNTIME_REVISION),
            transport=transport,
            probe_timeout_seconds=timeout,
            generation_timeout_seconds=timeout,
            cancellation_grace_seconds=grace,
            clock=lambda: 150,
        )

    async def test_preexisting_disconnect_makes_no_transport_call(self):
        event = asyncio.Event()
        event.set()
        transport = _healthy_transport()
        with self.assertRaises(Music3RequestDisconnected):
            await self._client(transport).generate(
                _request(), authorization=_authorization(), disconnect_event=event
            )
        self.assertEqual(transport.calls, [])

    async def test_disconnect_cancels_only_exact_inflight_transport_task(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(_request):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        transport = _ScriptedTransport([blocked])
        disconnect = asyncio.Event()
        operation = asyncio.create_task(self._client(transport, 1).generate(
            _request(), authorization=_authorization(), disconnect_event=disconnect
        ))
        await started.wait()
        disconnect.set()
        with self.assertRaises(Music3RequestDisconnected):
            await operation
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(transport.calls), 1)

    async def test_timeout_cancels_transport_task_and_returns_redacted_error(self):
        cancelled = asyncio.Event()

        async def blocked(_request):
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        transport = _ScriptedTransport([blocked])
        with self.assertRaises(Music3SglangClientError) as caught:
            await self._client(transport).generate(
                _request(), authorization=_authorization(), disconnect_event=asyncio.Event()
            )
        self.assertEqual((caught.exception.code, caught.exception.stage), ("timeout", "health"))
        self.assertTrue(cancelled.is_set())

    async def test_caller_task_cancellation_propagates_to_transport(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(_request):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        transport = _ScriptedTransport([blocked])
        operation = asyncio.create_task(self._client(transport, 1).generate(
            _request(), authorization=_authorization(), disconnect_event=asyncio.Event()
        ))
        await started.wait()
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        self.assertTrue(cancelled.is_set())

    async def test_cancellation_resistant_transport_never_hangs_timeout(self):
        resisted = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def resistant(_request):
            try:
                await release.wait()
            except asyncio.CancelledError:
                resisted.set()
                await release.wait()
            finally:
                finished.set()
            raise RuntimeError("released after cancellation test")

        transport = _ScriptedTransport([resistant])
        try:
            with self.assertRaises(Music3SglangClientError) as caught:
                await asyncio.wait_for(
                    self._client(transport, timeout=0.05, grace=0.005).generate(
                        _request(), authorization=_authorization(),
                        disconnect_event=asyncio.Event()
                    ),
                    timeout=0.3,
                )
            self.assertEqual(caught.exception.code, "abort_unconfirmed")
            self.assertTrue(resisted.is_set())
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.2)
            await asyncio.sleep(0)

    async def test_cancellation_resistant_transport_never_hangs_disconnect(self):
        started = asyncio.Event()
        resisted = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def resistant(_request):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                resisted.set()
                await release.wait()
            finally:
                finished.set()
            raise RuntimeError("released after cancellation test")

        transport = _ScriptedTransport([resistant])
        disconnect = asyncio.Event()
        operation = asyncio.create_task(
            self._client(transport, timeout=1, grace=0.005).generate(
                _request(), authorization=_authorization(), disconnect_event=disconnect
            )
        )
        try:
            await started.wait()
            disconnect.set()
            with self.assertRaises(Music3SglangClientError) as caught:
                await asyncio.wait_for(operation, timeout=0.2)
            self.assertEqual(caught.exception.code, "abort_unconfirmed")
            self.assertTrue(resisted.is_set())
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.2)
            await asyncio.sleep(0)

    async def test_cancellation_resistant_transport_does_not_delay_caller_cancel(self):
        started = asyncio.Event()
        resisted = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def resistant(_request):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                resisted.set()
                await release.wait()
            finally:
                finished.set()
            raise RuntimeError("released after cancellation test")

        transport = _ScriptedTransport([resistant])
        operation = asyncio.create_task(
            self._client(transport, timeout=1, grace=0.005).generate(
                _request(), authorization=_authorization(),
                disconnect_event=asyncio.Event()
            )
        )
        try:
            await started.wait()
            operation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=0.2)
            self.assertTrue(resisted.is_set())
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=0.2)
            await asyncio.sleep(0)

    async def test_disconnect_cancels_model_and_generation_stages(self):
        for completed_probes in (1, 2):
            with self.subTest(completed_probes=completed_probes):
                started = asyncio.Event()
                cancelled = asyncio.Event()

                async def blocked(_request):
                    started.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise

                handlers = [lambda request: _json_response(request, _health())]
                if completed_probes == 2:
                    handlers.append(lambda request: _json_response(request, _models()))
                handlers.append(blocked)
                transport = _ScriptedTransport(handlers)
                disconnect = asyncio.Event()
                operation = asyncio.create_task(
                    self._client(transport, timeout=1).generate(
                        _request(), authorization=_authorization(),
                        disconnect_event=disconnect
                    )
                )
                await started.wait()
                disconnect.set()
                with self.assertRaises(Music3RequestDisconnected):
                    await operation
                self.assertTrue(cancelled.is_set())
                self.assertEqual(len(transport.calls), completed_probes + 1)

    async def test_no_public_cancel_or_unload_claim_exists(self):
        public = {name for name in dir(Music3SglangClient) if not name.startswith("_")}
        self.assertEqual(public, {"base_url", "generate"})

    def test_authorization_json_rejects_duplicate_keys_nan_bom_and_nonobject(self):
        binding = bind_music3_sglang_source(RUNTIME_REVISION)
        base = {
            "request_url": AUTH_URL,
            "final_url": AUTH_URL,
            "status_code": 200,
            "headers": (("content-type", "application/json"),),
            "peer_certificate_sha256": PEER_CERTIFICATE,
            "trusted_peer_certificate_sha256": PEER_CERTIFICATE,
            "source_binding": binding,
            "base_url": BASE_URL,
            "now_unix": 150,
        }
        valid = json.dumps(_authorization_document(), separators=(",", ":"))
        hostile = (
            (valid[:-1] + ',"schema":"duplicate"}').encode(),
            valid.replace('"issued_at_unix":100', '"issued_at_unix":NaN').encode(),
            b"\xef\xbb\xbf" + valid.encode(),
            b"[]",
            b"{",
            b"\xff",
        )
        for body in hostile:
            with self.subTest(body=body[:20]):
                with self.assertRaises(MusicModelContractError):
                    validate_music3_external_authorization_response(**base, body=body)
