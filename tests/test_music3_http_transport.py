"""Loopback-only tests for the bounded Music 3 HTTP transport."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from services.minimax_music3_sglang_client import (
    GENERATE_PATH,
    HEALTH_PATH,
    MODELS_PATH,
    Music3TransportRequest,
)
from services.music3_http_transport import (
    Music3HTTPTransportError,
    music3_http_transport,
)
from services.music_model_contract import MusicModelContractError


async def _read_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes]:
    request_line = await reader.readline()
    method, target, _version = request_line.decode("ascii").rstrip("\r\n").split(" ")
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b""):
            break
        name, value = line.decode("latin-1").rstrip("\r\n").split(":", 1)
        headers[name.casefold()] = value.strip()

    body = bytearray()
    if headers.get("transfer-encoding", "").casefold() == "chunked":
        while True:
            size_line = await reader.readline()
            size = int(size_line.split(b";", 1)[0].strip(), 16)
            if size == 0:
                await reader.readline()
                break
            body.extend(await reader.readexactly(size))
            await reader.readexactly(2)
    elif "content-length" in headers:
        body.extend(await reader.readexactly(int(headers["content-length"])))
    return method, target, headers, bytes(body)


async def _send_response(
    writer: asyncio.StreamWriter,
    *,
    status: int = 200,
    headers: tuple[tuple[str, str], ...] = (),
    body: bytes = b"",
) -> None:
    reason = {200: "OK", 302: "Found"}.get(status, "Response")
    writer.write(f"HTTP/1.1 {status} {reason}\r\n".encode("ascii"))
    for name, value in headers:
        writer.write(f"{name}: {value}\r\n".encode("latin-1"))
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _send_chunked(
    writer: asyncio.StreamWriter,
    chunks: tuple[bytes, ...],
) -> None:
    writer.write(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    )
    for chunk in chunks:
        writer.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
        await writer.drain()
    writer.write(b"0\r\n\r\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


class Music3HTTPTransportTests(unittest.IsolatedAsyncioTestCase):
    async def _listen(self, handler):
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close_server, server)
        return server

    @staticmethod
    async def _close_server(server: asyncio.AbstractServer) -> None:
        server.close()
        await server.wait_closed()

    @staticmethod
    def _request(
        server: asyncio.AbstractServer,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        maximum: int = 1024,
        timeout_seconds: float = 3.0,
    ) -> Music3TransportRequest:
        address = server.sockets[0].getsockname()
        return Music3TransportRequest(
            method=method,
            url=f"http://127.0.0.1:{address[1]}{path}",
            headers=(("accept", "application/octet-stream"),),
            body=body,
            timeout_seconds=timeout_seconds,
            max_response_bytes=maximum,
        )

    async def test_get_and_post_preserve_request_and_response_bytes(self):
        captured: list[tuple[str, str, dict[str, str], bytes]] = []

        async def handler(reader, writer):
            captured.append(await _read_request(reader))
            if captured[-1][0] == "GET":
                await _send_response(
                    writer,
                    status=201,
                    headers=(("Content-Length", "3"), ("X-Result", "get")),
                    body=b"get",
                )
            else:
                await _send_response(
                    writer,
                    status=202,
                    headers=(("Content-Length", "4"), ("X-Result", "post")),
                    body=b"post",
                )

        server = await self._listen(handler)
        get_request = self._request(server, HEALTH_PATH)
        post_body = b"\x00music3\xff\r\n"
        post_request = self._request(
            server,
            GENERATE_PATH,
            method="POST",
            body=post_body,
        )

        get_response = await music3_http_transport(get_request)
        post_response = await music3_http_transport(post_request)

        self.assertEqual(captured[0][0], "GET")
        self.assertEqual(captured[0][1], HEALTH_PATH)
        self.assertEqual(captured[0][3], b"")
        self.assertEqual(captured[1][0], "POST")
        self.assertEqual(captured[1][1], GENERATE_PATH)
        self.assertEqual(captured[1][3], post_body)
        self.assertTrue(
            all(item[2]["accept-encoding"] == "identity" for item in captured)
        )
        self.assertEqual(get_response.status_code, 201)
        self.assertEqual(get_response.url, get_request.url)
        self.assertEqual(get_response.body, b"get")
        self.assertEqual(post_response.status_code, 202)
        self.assertEqual(post_response.url, post_request.url)
        self.assertEqual(post_response.body, b"post")
        for response in (get_response, post_response):
            names = [name for name, _value in response.headers]
            self.assertEqual(names, [name.casefold() for name in names])
            self.assertEqual(len(names), len(set(names)))

    async def test_chunked_response_stays_within_configured_bound(self):
        async def handler(reader, writer):
            await _read_request(reader)
            await _send_chunked(writer, (b"raw", b"-bytes"))

        server = await self._listen(handler)
        response = await music3_http_transport(
            self._request(server, HEALTH_PATH, maximum=9)
        )
        self.assertEqual(response.body, b"raw-bytes")

    async def test_chunked_response_over_limit_is_closed_and_rejected(self):
        closed = asyncio.Event()

        async def handler(reader, writer):
            await _read_request(reader)
            writer.write(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                b"Connection: close\r\n\r\n"
            )
            writer.write(b"3\r\nabc\r\n")
            await writer.drain()
            writer.write(b"4\r\ndefg\r\n")
            await writer.drain()
            try:
                await asyncio.wait_for(reader.read(), timeout=2.0)
            finally:
                closed.set()
                writer.close()
                await writer.wait_closed()

        server = await self._listen(handler)
        with self.assertRaises(Music3HTTPTransportError) as raised:
            await music3_http_transport(self._request(server, HEALTH_PATH, maximum=5))
        self.assertEqual(raised.exception.code, "response_too_large")
        await asyncio.wait_for(closed.wait(), timeout=2.0)

    async def test_declared_oversize_and_invalid_content_length_fail_early(self):
        reached_body = asyncio.Event()

        async def handler(reader, writer):
            _method, target, _headers, _body = await _read_request(reader)
            if target == HEALTH_PATH:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n"
                    b"Connection: close\r\n\r\n"
                )
            else:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: invalid\r\n"
                    b"Connection: close\r\n\r\n"
                )
            await writer.drain()
            try:
                await asyncio.wait_for(reader.read(), timeout=2.0)
            finally:
                reached_body.set()
                writer.close()
                await writer.wait_closed()

        server = await self._listen(handler)
        for path, expected_code in (
            (HEALTH_PATH, "response_too_large"),
            (MODELS_PATH, None),
        ):
            with self.subTest(path=path):
                with self.assertRaises(Music3HTTPTransportError) as raised:
                    await music3_http_transport(self._request(server, path, maximum=8))
                if expected_code is not None:
                    self.assertEqual(raised.exception.code, expected_code)
                else:
                    self.assertIn(
                        raised.exception.code,
                        {"invalid_content_length", "transport_error"},
                    )
        await asyncio.wait_for(reached_body.wait(), timeout=2.0)

    async def test_redirect_is_returned_without_contacting_target(self):
        second_server_hits = 0

        async def second_handler(reader, writer):
            nonlocal second_server_hits
            second_server_hits += 1
            await _read_request(reader)
            await _send_response(writer, headers=(("Content-Length", "0"),))

        second = await self._listen(second_handler)
        second_port = second.sockets[0].getsockname()[1]

        async def first_handler(reader, writer):
            await _read_request(reader)
            await _send_response(
                writer,
                status=302,
                headers=(
                    ("Content-Length", "0"),
                    ("Location", f"http://127.0.0.1:{second_port}{MODELS_PATH}"),
                ),
            )

        first = await self._listen(first_handler)
        response = await music3_http_transport(self._request(first, HEALTH_PATH))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            dict(response.headers)["location"],
            f"http://127.0.0.1:{second_port}{MODELS_PATH}",
        )
        self.assertEqual(second_server_hits, 0)

    async def test_proxy_environment_is_ignored(self):
        proxy_hits = 0

        async def proxy_handler(reader, writer):
            nonlocal proxy_hits
            proxy_hits += 1
            await _read_request(reader)
            await _send_response(
                writer,
                headers=(("Content-Length", "5"),),
                body=b"proxy",
            )

        proxy = await self._listen(proxy_handler)
        proxy_port = proxy.sockets[0].getsockname()[1]
        origin_bodies: list[bytes] = []

        async def origin_handler(reader, writer):
            _method, _target, _headers, body = await _read_request(reader)
            origin_bodies.append(body)
            await _send_response(
                writer,
                headers=(("Content-Length", "6"),),
                body=b"origin",
            )

        origin = await self._listen(origin_handler)
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        environment = {
            "HTTP_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "ALL_PROXY": proxy_url,
            "all_proxy": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": "",
            "no_proxy": "",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            response = await music3_http_transport(self._request(origin, HEALTH_PATH))
        self.assertEqual(response.body, b"origin")
        self.assertEqual(origin_bodies, [b""])
        self.assertEqual(proxy_hits, 0)

    async def test_non_identity_content_encoding_is_rejected(self):
        requested_encoding: list[str] = []

        async def handler(reader, writer):
            _method, _target, headers, _body = await _read_request(reader)
            requested_encoding.append(headers.get("accept-encoding", ""))
            await _send_response(
                writer,
                headers=(
                    ("Content-Length", "4"),
                    ("Content-Encoding", "gzip"),
                ),
                body=b"gzip",
            )

        server = await self._listen(handler)
        with self.assertRaises(Music3HTTPTransportError) as raised:
            await music3_http_transport(self._request(server, HEALTH_PATH))
        self.assertEqual(raised.exception.code, "unsupported_content_encoding")
        self.assertEqual(requested_encoding, ["identity"])

    async def test_cancellation_closes_only_its_peer_and_other_request_survives(self):
        first_started = asyncio.Event()
        first_peer_closed = asyncio.Event()
        second_started = asyncio.Event()
        release_second = asyncio.Event()

        async def handler(reader, writer):
            _method, target, _headers, _body = await _read_request(reader)
            if target == HEALTH_PATH:
                first_started.set()
                try:
                    await reader.read()
                finally:
                    first_peer_closed.set()
                    writer.close()
                    await writer.wait_closed()
                return
            second_started.set()
            await release_second.wait()
            await _send_response(
                writer,
                headers=(("Content-Length", "8"),),
                body=b"survives",
            )

        server = await self._listen(handler)
        first_task = asyncio.create_task(
            music3_http_transport(self._request(server, HEALTH_PATH, timeout_seconds=10.0))
        )
        await asyncio.wait_for(first_started.wait(), timeout=10.0)
        second_task = asyncio.create_task(
            music3_http_transport(self._request(server, MODELS_PATH, timeout_seconds=10.0))
        )
        await asyncio.wait_for(second_started.wait(), timeout=10.0)
        self.assertFalse(second_task.done())

        first_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_task
        await asyncio.wait_for(first_peer_closed.wait(), timeout=10.0)
        self.assertFalse(second_task.done())

        release_second.set()
        second_response = await asyncio.wait_for(second_task, timeout=10.0)
        self.assertEqual(second_response.body, b"survives")

    async def test_exact_request_type_and_mutated_fields_are_revalidated(self):
        with self.assertRaises(MusicModelContractError):
            await music3_http_transport(object())

        request = Music3TransportRequest(
            method="GET",
            url="http://127.0.0.1:43210" + HEALTH_PATH,
            headers=(),
            body=None,
            timeout_seconds=1.0,
            max_response_bytes=32,
        )
        object.__setattr__(request, "url", "http://example.com" + HEALTH_PATH)
        with self.assertRaises(MusicModelContractError):
            await music3_http_transport(request)

    async def test_transport_errors_do_not_include_request_data(self):
        request = Music3TransportRequest(
            method="POST",
            url="http://127.0.0.1:1" + GENERATE_PATH,
            headers=(),
            body=b"private request bytes",
            timeout_seconds=0.5,
            max_response_bytes=32,
        )
        with self.assertRaises(Music3HTTPTransportError) as raised:
            await music3_http_transport(request)
        rendered = str(raised.exception)
        self.assertNotIn(request.url, rendered)
        self.assertNotIn("private request bytes", rendered)


if __name__ == "__main__":
    unittest.main()
