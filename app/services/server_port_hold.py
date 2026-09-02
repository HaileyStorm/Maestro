"""Bind Maestro's HTTP port before heavy imports.

Pinokio's Caddy reverse proxy can listen on a reserved SERVER_PORT while
launch.py is still importing WanGP. Holding the socket with a plain bind
(no SO_REUSEADDR) keeps that port until uvicorn inherits it.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Mapping, MutableMapping

_TRUE = {"1", "true", "yes", "on"}


class ServerPortHoldError(Exception):
    """Operator-facing bind failure; launch.py exits after printing ``message``."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class HeldServerPort:
    host: str
    display_host: str
    port: int
    sock: socket.socket
    relocated: bool


def resolve_bind_host(environ: Mapping[str, str]) -> str:
    pinokio_share = (environ.get("PINOKIO_SHARE_LOCAL") or "").strip().lower()
    if pinokio_share == "true":
        return "0.0.0.0"
    if pinokio_share == "false":
        return "127.0.0.1"
    return environ.get("SERVER_NAME", "127.0.0.1") or "127.0.0.1"


def _strict_port(environ: Mapping[str, str]) -> bool:
    return str(environ.get("MAESTRO_STRICT_SERVER_PORT") or "").strip().lower() in _TRUE


def _bind_listen(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen(2048)
    except OSError:
        sock.close()
        raise
    return sock


def acquire_configured_server_port(
    environ: MutableMapping[str, str] | None = None,
    *,
    span: int = 20,
) -> HeldServerPort:
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    host = resolve_bind_host(env)
    preferred = int(env.get("SERVER_PORT", "7860"))
    strict = _strict_port(env)
    candidates = [preferred] + [preferred + offset for offset in range(1, span + 1)]
    held: HeldServerPort | None = None
    for candidate in candidates:
        try:
            sock = _bind_listen(host, candidate)
        except OSError:
            continue
        candidate = sock.getsockname()[1]
        relocated = candidate != preferred
        if relocated and strict:
            sock.close()
            raise ServerPortHoldError(
                f"[Maestro] ERROR: required port {preferred} is busy; refusing to "
                "move the stable-share backend to another port.\n"
            )
        if relocated:
            print(
                f"[Maestro] Port {preferred} was busy — using {candidate} instead.",
                flush=True,
            )
        env["SERVER_PORT"] = str(candidate)
        print(
            f"[Maestro] Holding port {candidate} before model import",
            flush=True,
        )
        held = HeldServerPort(
            host=host,
            display_host="127.0.0.1" if host == "0.0.0.0" else host,
            port=candidate,
            sock=sock,
            relocated=relocated,
        )
        break
    if held is None:
        raise ServerPortHoldError(
            f"\n[Maestro] ERROR: could not find a free port in "
            f"{preferred}-{preferred + span}. Another app (or a stale Maestro instance) "
            f"is holding them.\n"
            f"  • Close the other program, or stop the existing Maestro from "
            f"the Pinokio menu, then Start again.\n"
            f"  • On Windows you can see what holds a port with: "
            f"netstat -ano | findstr :{preferred}\n"
        )
    return held
