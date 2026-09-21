"""Server-side bridge to the local Sound/Vision YuE2 service.

The browser never receives the Sound/Vision bearer token.  Configuration is
resolved locally so Maestro can use an existing Sound/Vision installation
without duplicating its model runtime, queue, or artifact store.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class Yue2BridgeError(RuntimeError):
    """A public-safe Sound/Vision bridge failure."""

    def __init__(self, message: str, *, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _candidate_env_files() -> list[Path]:
    configured = os.environ.get("MAESTRO_YUE2_ENV_FILE", "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend(
        [
            Path.home() / "AI" / "sound-and-vision" / "service.env",
            Path("/mnt/fast-storage/music/sound-and-vision/service.env"),
        ]
    )
    return candidates


def _resolve_token_file() -> Path | None:
    direct = (
        os.environ.get("MAESTRO_YUE2_TOKEN_FILE")
        or os.environ.get("SOUND_VISION_TOKEN_FILE")
        or ""
    ).strip()
    if direct:
        return Path(direct).expanduser()
    for env_file in _candidate_env_files():
        value = _read_env_file(env_file).get("SOUND_VISION_TOKEN_FILE", "").strip()
        if value:
            return Path(value).expanduser()
    return None


class Yue2Bridge:
    JSON_LIMIT = 4 * 1024 * 1024

    def __init__(self, base_url: str | None = None, token_file: Path | None = None):
        self.base_url = (
            base_url or os.environ.get("MAESTRO_YUE2_URL") or "http://127.0.0.1:5191"
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise Yue2BridgeError("YuE2 service URL is invalid.", status_code=500)
        self.token_file = token_file or _resolve_token_file()

    def configured(self) -> bool:
        return bool(self.token_file and self.token_file.is_file())

    def _token(self) -> str:
        if not self.token_file:
            raise Yue2BridgeError(
                "YuE2 is installed, but Maestro cannot find its local service token."
            )
        try:
            token = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise Yue2BridgeError(
                "Maestro cannot read the local YuE2 service token."
            ) from exc
        if not token:
            raise Yue2BridgeError("The local YuE2 service token is empty.")
        return token

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: float = 20,
    ) -> dict[str, Any] | list[Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._token()}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(self.JSON_LIMIT + 1)
        except HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read(self.JSON_LIMIT).decode("utf-8", "replace"))
                detail = str(body.get("detail") or "")
            except Exception:
                pass
            public = detail or "The YuE2 service rejected this request."
            raise Yue2BridgeError(public, status_code=exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Yue2BridgeError(
                "The local YuE2 service is not reachable. Start Sound/Vision and try again."
            ) from exc
        if len(raw) > self.JSON_LIMIT:
            raise Yue2BridgeError("The YuE2 service returned an oversized response.")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Yue2BridgeError("The YuE2 service returned an invalid response.") from exc

    def health(self):
        return self._request("/api/health", timeout=5)

    def loras(self):
        return self._request("/api/loras", timeout=8)

    def library(self):
        return self._request("/api/library", timeout=8)

    def project_library(self, workspace: str) -> dict[str, Any]:
        library = self.library()
        if not isinstance(library, dict) or not isinstance(library.get("tracks"), list):
            raise Yue2BridgeError("The YuE2 service returned an invalid library.")
        return {
            "tracks": [
                track for track in library["tracks"]
                if isinstance(track, dict) and track.get("project") == workspace
            ]
        }

    def require_take(self, take_id: str, workspace: str) -> dict[str, Any]:
        for track in self.project_library(workspace)["tracks"]:
            if track.get("id") == take_id:
                return track
        raise Yue2BridgeError("YuE2 take not found in this project.", status_code=404)

    def submit(self, payload: dict[str, Any]):
        return self._request("/api/generations", method="POST", payload=payload, timeout=20)

    def plan(self, take_id: str):
        return self._request(f"/api/takes/{quote(take_id, safe='')}/plan", timeout=8)

    def continue_plan(self, take_id: str, abc: str | None = None):
        payload = {} if abc is None else {"abc": abc}
        return self._request(
            f"/api/takes/{quote(take_id, safe='')}/continue",
            method="POST",
            payload=payload,
            timeout=8,
        )

    def cancel(self, take_id: str):
        return self._request(
            f"/api/takes/{quote(take_id, safe='')}/cancel",
            method="POST",
            payload={},
            timeout=8,
        )

    def audio_request(self, take_id: str, fmt: str = "mp3") -> Request:
        if fmt not in {"mp3", "wav", "flac"}:
            raise Yue2BridgeError("Unsupported YuE2 audio format.", status_code=404)
        return Request(
            self.base_url
            + f"/api/takes/{quote(take_id, safe='')}/files/audio.{fmt}",
            headers={"Authorization": f"Bearer {self._token()}"},
        )

    def audio(self, take_id: str, fmt: str = "mp3", *, limit: int = 256 * 1024 * 1024):
        try:
            with urlopen(self.audio_request(take_id, fmt), timeout=60) as response:
                content_type = response.headers.get_content_type()
                raw = response.read(limit + 1)
        except HTTPError as exc:
            raise Yue2BridgeError(
                "YuE2 audio is not ready.", status_code=exc.code
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Yue2BridgeError(
                "The local YuE2 service is not reachable. Start Sound/Vision and try again."
            ) from exc
        if len(raw) > limit:
            raise Yue2BridgeError("The YuE2 audio file is too large to preview.", status_code=413)
        return raw, content_type


def public_status(bridge: Yue2Bridge) -> dict[str, Any]:
    """Return a browser-safe status envelope without local paths or tokens."""
    try:
        health = bridge.health()
        loras = bridge.loras()
    except Yue2BridgeError as exc:
        return {"available": False, "message": str(exc), "loras": []}
    groups = []
    for group in loras.get("groups", []) if isinstance(loras, dict) else []:
        groups.append(
            {
                key: group.get(key)
                for key in ("id", "name", "kind", "trigger", "preferredStep", "generation", "checkpoints")
            }
        )
    return {
        "available": bool(isinstance(health, dict) and health.get("generation")),
        "model": health.get("model") if isinstance(health, dict) else None,
        "decoder": health.get("decoder") if isinstance(health, dict) else None,
        "sampleRate": health.get("sample_rate") if isinstance(health, dict) else None,
        "formats": health.get("formats", []) if isinstance(health, dict) else [],
        "license": health.get("weight_license") if isinstance(health, dict) else None,
        "queue": health.get("queue") if isinstance(health, dict) else None,
        "loraEngine": health.get("lora_engine") if isinstance(health, dict) else None,
        "loras": groups,
    }
