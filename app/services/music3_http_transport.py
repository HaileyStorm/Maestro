"""Single-attempt, bounded HTTP transport for the Music 3 loopback client.

Cancelling this coroutine closes its HTTP stream and connection. It does not
cancel or otherwise control any GPU work performed by the remote runtime.
"""

from __future__ import annotations

import asyncio

import httpx

from services.minimax_music3_sglang_client import (
    Music3TransportRequest,
    Music3TransportResponse,
)
from services.music_model_contract import MusicModelContractError

_RAW_CHUNK_BYTES = 64 * 1024


class Music3HTTPTransportError(RuntimeError):
    """Content-free failure from the local HTTP transport."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Music 3 HTTP transport failed ({code})")


def _revalidate_request(value: object) -> Music3TransportRequest:
    if type(value) is not Music3TransportRequest:
        raise MusicModelContractError("HTTP request must be an exact Music 3 request")
    try:
        method = value.method
        url = value.url
        headers = value.headers
        body = value.body
        timeout_seconds = value.timeout_seconds
        max_response_bytes = value.max_response_bytes
        redirect_policy = value.redirect_policy
    except AttributeError:
        raise MusicModelContractError("HTTP request fields are invalid") from None
    if type(method) is not str or method not in {"GET", "POST"}:
        raise MusicModelContractError("HTTP method is invalid")
    return Music3TransportRequest(
        method=method,
        url=url,
        headers=headers,
        body=body,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        redirect_policy=redirect_policy,
    )


def _declared_length(response: httpx.Response, *, maximum: int) -> int | None:
    values = response.headers.get_list("content-length", split_commas=False)
    if not values:
        return None
    if len(values) != 1:
        raise Music3HTTPTransportError("invalid_content_length")
    raw = values[0]
    if (
        not raw
        or not raw.isascii()
        or not raw.isdecimal()
        or (len(raw) > 1 and raw.startswith("0"))
    ):
        raise Music3HTTPTransportError("invalid_content_length")
    maximum_text = str(maximum)
    if len(raw) > len(maximum_text) or (
        len(raw) == len(maximum_text) and raw > maximum_text
    ):
        raise Music3HTTPTransportError("response_too_large")
    return int(raw)


def _require_identity_encoding(response: httpx.Response) -> None:
    values = response.headers.get_list("content-encoding", split_commas=False)
    if len(values) > 1 or (values and values[0].strip().casefold() != "identity"):
        raise Music3HTTPTransportError("unsupported_content_encoding")


async def music3_http_transport(
    request: Music3TransportRequest,
) -> Music3TransportResponse:
    """Send one exact loopback request and collect bounded raw response bytes."""

    request = _revalidate_request(request)
    request_headers = dict(request.headers)
    request_headers["accept-encoding"] = "identity"

    transport = httpx.AsyncHTTPTransport(trust_env=False, retries=0)
    try:
        async with (
            httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(request.timeout_seconds),
                trust_env=False,
                follow_redirects=False,
            ) as client,
            client.stream(
                request.method,
                request.url,
                headers=request_headers,
                content=request.body,
                follow_redirects=False,
            ) as response,
        ):
            _require_identity_encoding(response)
            declared_length = _declared_length(
                response,
                maximum=request.max_response_bytes,
            )
            body = bytearray()
            chunk_size = min(
                _RAW_CHUNK_BYTES,
                request.max_response_bytes + 1,
            )
            async for chunk in response.aiter_raw(chunk_size=chunk_size):
                if len(body) + len(chunk) > request.max_response_bytes:
                    raise Music3HTTPTransportError("response_too_large")
                body.extend(chunk)
            if declared_length is not None and declared_length != len(body):
                raise Music3HTTPTransportError("invalid_content_length")
            response_headers = tuple(
                (name.casefold(), value) for name, value in response.headers.items()
            )
            return Music3TransportResponse(
                status_code=response.status_code,
                url=str(response.url),
                headers=response_headers,
                body=bytes(body),
                redirect_count=0,
            )
    except asyncio.CancelledError:
        raise
    except Music3HTTPTransportError:
        raise
    except Exception:  # noqa: BLE001 - redact HTTP implementation error details
        raise Music3HTTPTransportError("transport_error") from None
