"""Thin synchronous HTTP client. No model logic, automatic retries or cancellation."""
from __future__ import annotations

import http.cookiejar
import json
import re
import time
from urllib.parse import quote, unquote, urlsplit

import httpx


class MaestroError(RuntimeError):
    def __init__(self, status: int | None, detail, *, operation: str, uncertain: bool = False):
        self.status, self.detail, self.operation, self.uncertain = status, detail, operation, uncertain
        self.next_action = ("Reconcile the same operation identity; do not blindly resubmit or cancel."
                            if uncertain else "Read access/discovery and the operation's error contract before correcting the request.")
        super().__init__(f"{operation}: {detail}")

    def as_dict(self):
        return {"status": self.status, "detail": self.detail, "operation": self.operation,
                "uncertain": self.uncertain, "next_action": self.next_action}


class UncertainSubmission(MaestroError):
    """A mutation's outcome is unknown. Reconcile it; never auto-retry."""


class MaestroClient:
    def __init__(self, base_url: str, *, origin: str | None = None,
                 cookie_file: str | None = None, timeout: float = 60):
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError("Use the configured Maestro HTTP(S) URL without credentials, query or fragment")
        self.base_url = base_url.rstrip("/")
        self.origin = origin or f"{url.scheme}://{url.netloc}"
        cookies = http.cookiejar.MozillaCookieJar(cookie_file) if cookie_file else http.cookiejar.CookieJar()
        if cookie_file:
            cookies.load(ignore_discard=True)  # Explicit operator-owned session; never exported by MCP.
        self.http = httpx.Client(timeout=timeout, cookies=cookies, follow_redirects=False)
        self._schema = None

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, method: str, path: str, *, query=None, body=None, files=None, headers=None,
                media_reference: bool = False, maximum_response_bytes: int | None = None):
        if (not (path.startswith("/api/v1/") or path in {"/openapi.json", "/health", "/ready"})
                or "?" in path or "#" in path or any(p in {".", ".."} for p in unquote(path).split("/"))):
            raise ValueError("Only Continuum HTTP paths are supported")
        method = method.upper()
        if maximum_response_bytes is not None and maximum_response_bytes <= 0:
            raise ValueError("A response byte limit must be positive")
        mutation = method not in {"GET", "HEAD"}
        request_headers = {"Origin": self.origin, **(headers or {})}
        operation = f"{method} {path}"
        try:
            request = self.http.build_request(method, self.base_url + path, params=query,
                                             json=body if files is None else None, files=files,
                                             data=body if files is not None else None, headers=request_headers)
            response = self.http.send(request, stream=True)
            try:
                if not response.is_success:
                    response.read()
                    try:
                        detail = response.json().get("detail", "HTTP request refused")
                    except (ValueError, AttributeError):
                        detail = "HTTP request refused; inspect the configured access surface"
                    uncertain = mutation and response.status_code >= 500
                    kind = UncertainSubmission if uncertain else MaestroError
                    raise kind(response.status_code, detail, operation=operation,
                               uncertain=uncertain)
                content_type = response.headers.get("content-type", "")
                if media_reference and not ("json" in content_type or content_type.startswith("text/")):
                    return {"media_url": str(response.url), "requires_same_authorized_session": True,
                            "content_type": content_type, "content_length": response.headers.get("content-length")}
                if maximum_response_bytes is None:
                    content = response.read()
                else:
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > maximum_response_bytes:
                        raise MaestroError(413, "Media exceeds the bounded adapter limit; use the explicit SDK download/upload workflow",
                                           operation=operation, uncertain=mutation)
                    content = bytearray()
                    for part in response.iter_bytes(chunk_size=16384):
                        if len(content) + len(part) > maximum_response_bytes:
                            raise MaestroError(413, "Media exceeds the bounded adapter limit; no upload was submitted",
                                               operation=operation, uncertain=mutation)
                        content.extend(part)
                    content = bytes(content)
                if not content:
                    return None
                if "json" in content_type:
                    try:
                        return json.loads(content)
                    except ValueError:
                        kind = UncertainSubmission if mutation else MaestroError
                        raise kind(response.status_code, "Response was not valid JSON; reconcile server state",
                                   operation=operation, uncertain=mutation) from None
                return content.decode(response.encoding or "utf-8") if content_type.startswith("text/") else content
            finally:
                response.close()
        except (httpx.TransportError, httpx.DecodingError):
            kind = UncertainSubmission if mutation else MaestroError
            raise kind(None, "Transport interrupted; server state is unknown", operation=operation, uncertain=mutation) from None

    def contract(self, *, refresh: bool = False):
        if self._schema is None or refresh:
            self._schema = self.request("GET", "/openapi.json")
        return self._schema

    def operations(self, *, feature: str | None = None, mcp_only: bool = False):
        result = {}
        for path, methods in self.contract()["paths"].items():
            if not path.startswith("/api/v1/"):
                continue
            for method, operation in methods.items():
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
                    continue
                meta = operation.get("x-maestro", {})
                declared_feature = str(meta.get("feature", ""))
                if (feature and not (declared_feature == feature or declared_feature.startswith(feature + "."))) or (mcp_only and meta.get("mcp") is not True):
                    continue
                result[operation["operationId"]] = {"path": path, "method": method.upper(), **operation}
        return result

    def call(self, operation_id: str, *, path: dict | None = None, query: dict | None = None,
             body=None, files=None, headers=None, media_reference: bool = False):
        operation = self.operations().get(operation_id)
        if operation is None:
            raise ValueError("Unknown operation; discover the current server contract")
        template = operation["path"]
        path_values = path or {}
        keys = re.findall(r"\{([^}]+)\}", template)
        if set(path_values) != set(keys):
            raise ValueError(f"Path fields must be exactly {keys}")
        for key in keys:
            template = template.replace("{" + key + "}", quote(str(path_values[key]), safe=""))
        return self.request(operation["method"], template, query=query, body=body, files=files,
                            headers=headers, media_reference=media_reference)

    def discover(self):
        return self.request("GET", "/api/v1/api/discovery")

    def wait(self, operation_id: str, *, path=None, query=None, timeout: float = 60,
             interval: float = 2, terminal=("completed", "failed", "cancelled", "blocked", "paused")):
        operation = self.operations().get(operation_id)
        if not operation or operation["method"] != "GET":
            raise ValueError("Waiting requires an existing read-only status operation")
        deadline = time.monotonic() + timeout
        while True:
            state = self.call(operation_id, path=path, query=query)
            if isinstance(state, dict) and (
                state.get("status") in terminal
                or state.get("recovery_actionable") is True
                or state.get("recovery_blocked") is True
            ):
                # Yield an actionable recovery snapshot without retrying or
                # cancelling. Interruption alone can mean active auto-recovery.
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MaestroError(None, "Observation timed out; reconcile server state. No cancellation or retry was requested",
                                   operation=operation_id, uncertain=False)
            time.sleep(min(max(interval, 0.1), remaining))


def login_main():
    """Human-only interactive login using the existing account/session routes."""
    import argparse
    import getpass
    import os
    from pathlib import Path
    import sys
    import tempfile

    parser = argparse.ArgumentParser(description=login_main.__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--origin")
    parser.add_argument("--cookie-file", required=True, help="New private Netscape cookie file; never overwritten")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Run interactively as the human operator; credentials are never read from arguments or pipes")
    destination = Path(args.cookie_file).expanduser().absolute()
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        parser.error("Choose a new file in an existing operator-owned private directory")
    username = input("Maestro username: ")
    password = getpass.getpass("Maestro password: ")
    temporary = None
    try:
        with MaestroClient(args.url, origin=args.origin) as client:
            nonce = client.request("POST", "/api/v1/account/nonce", body={"purpose": "login"})
            client.request("POST", "/api/v1/account/login", body={
                "username": username, "password": password, "nonce": nonce["nonce"],
                "device_label": "Continuum API client",
            })
            context = client.request("GET", "/api/v1/account/context")
            if not context.get("authenticated"):
                raise RuntimeError("Login did not establish an authenticated account session")
            # A private temporary file plus exclusive link prevents replacement
            # of an existing destination, including one introduced during login.
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".maestro-session-", delete=False) as handle:
                temporary = Path(handle.name)
            jar = http.cookiejar.MozillaCookieJar(str(temporary))
            for cookie in client.http.cookies.jar:
                jar.set_cookie(cookie)
            jar.save(ignore_discard=True)
            os.link(temporary, destination)
        print("Signed in. Private session saved; provide only its file path to your authorized client.")
    except MaestroError as error:
        parser.exit(1, f"Login observation failed (HTTP {error.status or 'unknown'}). {error.next_action}\nNo automatic retry was made.\n")
    except (OSError, RuntimeError, KeyError):
        parser.exit(1, "Session setup or export failed. Preserve any accepted login; resolve it through the owner account UI before retrying.\n")
    finally:
        del password
        if temporary is not None:
            temporary.unlink(missing_ok=True)
