"""Persistent stdio transport for the pinned Blender Lab MCP checkout."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import ipaddress
import json
import os
import queue
import importlib.util
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

try:  # package import in tests / repository-root execution
    from app.services.blender_mcp_service import (
        EXECUTE_BLENDER_CODE,
        GET_OBJECTS_SUMMARY,
        PINNED_INSTALL,
        RENDER_THUMBNAIL_TO_PATH,
        UPSTREAM_TOOL_ALLOWLIST,
        BlenderMCPCancelled,
        BlenderMCPError,
        BlenderMCPSecurityError,
        BlenderMCPToolError,
        _version_tuple,
    )
except ModuleNotFoundError:  # launch.py runs with app/ as cwd + sys.path root
    from services.blender_mcp_service import (
        EXECUTE_BLENDER_CODE,
        GET_OBJECTS_SUMMARY,
        PINNED_INSTALL,
        RENDER_THUMBNAIL_TO_PATH,
        UPSTREAM_TOOL_ALLOWLIST,
        BlenderMCPCancelled,
        BlenderMCPError,
        BlenderMCPSecurityError,
        BlenderMCPToolError,
        _version_tuple,
    )

try:
    from app.services.blender_native_fence import fence as _native_fence, BlenderNativeUnresolved
except ModuleNotFoundError:
    from services.blender_native_fence import fence as _native_fence, BlenderNativeUnresolved


def _native_identity_probe_code(token=""):
    # sys.modules survives loading another .blend; driver_namespace does not.
    return (
        "# Maestro native process/idle proof v1\n"
        "import bpy, os, sys, types, uuid\n"
        "_instance = sys.modules.get('_maestro_native_instance')\n"
        "if _instance is None:\n"
        "    _instance = types.ModuleType('_maestro_native_instance')\n"
        "    _instance.incarnation = uuid.uuid4().hex\n"
        "    sys.modules['_maestro_native_instance'] = _instance\n"
        "result = {'status': 'ok', 'pid': os.getpid(), "
        "'incarnation': _instance.incarnation, "
        "'render_running': bpy.app.is_job_running('RENDER'), "
        f"'token': {token!r}}}\n"
    )


class _ThumbnailParams(NamedTuple):
    output_path: str

    def __repr__(self):
        # The pinned template defines Params with this exact field.
        return f"Params(output_path={self.output_path!r})"


@dataclass
class _ToolRequest:
    name: str
    arguments: dict[str, Any]
    result: concurrent.futures.Future[dict[str, Any]] = field(
        default_factory=concurrent.futures.Future
    )
    cancel: threading.Event = field(default_factory=threading.Event)


class StdioBlenderMCPClient:
    """Lazy stdio adapter whose one lifecycle task owns all SDK contexts."""

    def __init__(
        self,
        *,
        checkout_root: str | os.PathLike[str],
        blender_version: str,
        bridge_host: str = PINNED_INSTALL.bridge_host,
        bridge_port: int = PINNED_INSTALL.bridge_port,
        scratch_root: str | os.PathLike[str] | None = None,
        request_timeout_seconds: float = 300.0,
        native_fence=None,
    ) -> None:
        self.checkout_root = Path(checkout_root).expanduser().resolve()
        # Keep the lexical venv entry point. Resolving it can collapse a
        # venv's python symlink to the base interpreter and lose dependencies.
        self.command = Path(os.path.abspath(sys.executable))
        source_path = str(self.checkout_root / "mcp")
        bootstrap = (
            "import sys;"
            f"sys.path.insert(0,{source_path!r});"
            "from blmcp import main;"
            "raise SystemExit(main())"
        )
        self.args = ("-c", bootstrap, "--transport", "stdio")
        self.blender_version = blender_version
        self.bridge_host = bridge_host
        self.bridge_port = bridge_port
        self._scratch_root_explicit = scratch_root is not None
        default_scratch = Path(tempfile.gettempdir()) / "blender_mcp"
        self.scratch_root = Path(scratch_root or default_scratch).expanduser().resolve()
        if not isinstance(request_timeout_seconds, (int, float)) or isinstance(
            request_timeout_seconds, bool
        ):
            raise TypeError("request_timeout_seconds must be numeric")
        if not 1 <= float(request_timeout_seconds) <= 3600:
            raise ValueError("request_timeout_seconds must be between 1 and 3600")
        self.request_timeout_seconds = float(request_timeout_seconds)
        self._native_fence = native_fence if native_fence is not None else _native_fence
        self._native_identity = None
        self._native_incarnation = None
        self._expected_binary = None

        self._requests: queue.Queue[_ToolRequest | None] = queue.Queue()
        self._ready = threading.Event()
        self._connect_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._root_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._startup_error: BaseException | None = None
        self._attestation: dict[str, Any] | None = None

    def connect(
        self,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any]:
        if self._attestation is not None:
            return dict(self._attestation)
        with self._connect_lock:
            if self._attestation is not None:
                return dict(self._attestation)
            self._validate_launcher_facts()
            self._verify_checkout()
            self._native_fence.require_idle()
            self._requests = queue.Queue()
            self._ready.clear()
            self._startup_error = None
            self._stopping = False
            self._thread = threading.Thread(
                target=self._thread_main,
                name="maestro-blender-mcp",
                daemon=True,
            )
            self._thread.start()
            deadline = time.monotonic() + self.request_timeout_seconds
            while not self._ready.wait(timeout=0.05):
                if cancelled is not None and cancelled():
                    self._cancel_worker()
                    raise BlenderMCPCancelled("Blender MCP initialization was cancelled")
                if time.monotonic() >= deadline:
                    self._cancel_worker()
                    raise BlenderMCPToolError("timed out initializing Blender MCP")
            if self._startup_error is not None:
                error = self._startup_error
                self._thread.join(timeout=5)
                self._thread = None
                if isinstance(error, BlenderMCPError):
                    raise error
                raise BlenderMCPToolError(f"failed to initialize Blender MCP: {error}") from error
            if self._attestation is None:
                raise BlenderMCPToolError("Blender MCP initialized without attestation")
            return dict(self._attestation)

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> Mapping[str, Any] | str:
        self.connect(cancelled=cancelled)
        thread = self._thread
        if thread is None or not thread.is_alive():
            raise BlenderMCPToolError("Blender MCP transport is not running")
        request = _ToolRequest(name=name, arguments=dict(arguments))
        self._requests.put(request)
        deadline = time.monotonic() + self.request_timeout_seconds + 1
        while True:
            try:
                return request.result.result(timeout=0.05)
            except concurrent.futures.TimeoutError:
                if not thread.is_alive():
                    raise BlenderMCPToolError("Blender MCP transport stopped unexpectedly")
                if cancelled is not None and cancelled():
                    request.cancel.set()
                if time.monotonic() >= deadline:
                    request.cancel.set()
                    raise BlenderMCPToolError(f"upstream MCP tool {name} timed out")
            except concurrent.futures.CancelledError as exc:
                raise BlenderMCPCancelled("Blender MCP operation was cancelled") from exc

    def close(self) -> None:
        thread = self._thread
        if thread is None:
            self._attestation = None
            return
        self._requests.put(None)
        thread.join(timeout=self.request_timeout_seconds + 5)
        if thread.is_alive():
            self._cancel_worker()
            if thread.is_alive():
                raise BlenderMCPToolError("timed out closing Blender MCP stdio session")
        self._thread = None
        self._attestation = None
        if self._startup_error is not None:
            error = self._startup_error
            self._startup_error = None
            if isinstance(error, BlenderMCPError):
                raise error
            raise BlenderMCPToolError(f"Blender MCP shutdown failed: {error}") from error

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        task = loop.create_task(self._lifecycle())
        self._root_task = task
        try:
            loop.run_until_complete(task)
        except BaseException as exc:  # noqa: BLE001 - crosses the thread boundary
            if not self._stopping:
                self._startup_error = exc
        finally:
            self._attestation = None
            self._ready.set()
            self._fail_pending_requests()
            self._root_task = None
            self._loop = None
            loop.close()

    def _cancel_worker(self) -> None:
        self._stopping = True
        loop = self._loop
        task = self._root_task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is None or not thread.is_alive():
            self._thread = None

    def _fail_pending_requests(self) -> None:
        error = BlenderMCPToolError("Blender MCP transport stopped")
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return
            if request is not None and not request.result.done():
                request.result.set_exception(error)

    async def _lifecycle(self) -> None:
        try:
            mcp_module = importlib.import_module("mcp")
            stdio_module = importlib.import_module("mcp.client.stdio")
            client_session = mcp_module.ClientSession
            server_parameters = mcp_module.StdioServerParameters
            stdio_client = stdio_module.stdio_client
            default_environment = stdio_module.get_default_environment
        except (ImportError, AttributeError) as exc:
            raise BlenderMCPToolError(
                "the standard MCP Python SDK is required for Blender MCP"
            ) from exc

        env = default_environment()
        env.update(
            {
                "BLENDER_MCP_HOST": self.bridge_host,
                "BLENDER_MCP_PORT": str(self.bridge_port),
                # Bind the launched entry point to the verified source tree,
                # overriding any caller/global PYTHONPATH.
                "PYTHONPATH": str(self.checkout_root / "mcp"),
            }
        )
        parameters = server_parameters(
            command=str(self.command),
            args=list(self.args),
            env=env,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):  # noqa: SIM117
            async with client_session(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.request_timeout_seconds),
            ) as session:
                initialized = await session.initialize()
                server_info = getattr(initialized, "serverInfo", None)
                server_name = getattr(server_info, "name", None)
                if server_name != "blender-mcp":
                    raise BlenderMCPSecurityError("initialized MCP server is not blender-mcp")

                listed = await session.list_tools()
                tool_names = [
                    getattr(tool, "name", None)
                    for tool in getattr(listed, "tools", ())
                ]
                if (
                    any(not isinstance(name, str) for name in tool_names)
                    or len(tool_names) != len(UPSTREAM_TOOL_ALLOWLIST)
                    or frozenset(tool_names) != UPSTREAM_TOOL_ALLOWLIST
                ):
                    raise BlenderMCPSecurityError("pinned Blender MCP tool listing did not match")

                probe = self._decode_call_result(
                    GET_OBJECTS_SUMMARY,
                    await session.call_tool(GET_OBJECTS_SUMMARY, {}),
                )
                self._assert_probe_ok(probe)
                identity_probe = self._decode_call_result(
                    EXECUTE_BLENDER_CODE,
                    await session.call_tool(EXECUTE_BLENDER_CODE,
                        {"code": _native_identity_probe_code()}),
                )
                native = self._native_probe_result(identity_probe, "")
                identity = self._native_fence._process_reader(native["pid"])
                owner = self._native_fence._process_reader(os.getpid())
                if (identity is None or self._expected_binary is None
                        or Path(identity["exe"]).resolve() != self._expected_binary
                        or owner is None or identity["owner"] != owner["owner"]):
                    raise BlenderMCPSecurityError("Blender native process identity did not match")
                self._native_identity = identity
                self._native_incarnation = native["incarnation"]
                if not self._scratch_root_explicit:
                    scratch_probe = self._decode_call_result(
                        EXECUTE_BLENDER_CODE,
                        await session.call_tool(
                            EXECUTE_BLENDER_CODE,
                            {
                                "code": (
                                    "# Maestro deterministic scratch_root_probe v1\n"
                                    "import bpy\n"
                                    "import os\n"
                                    "scratch_root = os.path.join(bpy.app.tempdir, 'blender_mcp')\n"
                                    "os.makedirs(scratch_root, mode=0o700, exist_ok=True)\n"
                                    "result = {'status': 'ok', 'scratch_root': scratch_root}\n"
                                )
                            },
                        ),
                    )
                    self.scratch_root = self._attest_scratch_root(scratch_probe)
                self._attestation = {
                    "repository": PINNED_INSTALL.repository,
                    "tag": PINNED_INSTALL.tag,
                    "revision": PINNED_INSTALL.revision,
                    "package_version": PINNED_INSTALL.package_version,
                    "license": PINNED_INSTALL.license,
                    "transport": PINNED_INSTALL.transport,
                    "bridge_host": PINNED_INSTALL.bridge_host,
                    "bridge_port": PINNED_INSTALL.bridge_port,
                    "blender_version": self.blender_version,
                    "server_name": server_name,
                    "tools": sorted(tool_names),
                    "probe_tool": GET_OBJECTS_SUMMARY,
                    "probe_ok": True,
                    "scratch_root": str(self.scratch_root),
                }
                self._ready.set()

                while True:
                    request = await asyncio.to_thread(self._requests.get)
                    if request is None:
                        return
                    if request.cancel.is_set():
                        request.result.cancel()
                        continue
                    mutating = request.name in {EXECUTE_BLENDER_CODE, RENDER_THUMBNAIL_TO_PATH}
                    token = None
                    try:
                        if mutating:
                            dispatch_name, dispatch_arguments = self._guarded_native_command(
                                request.name, request.arguments)
                            token = self._native_fence.begin(self._native_identity,
                                self._native_incarnation, request.name, request.arguments)
                        else:
                            dispatch_name, dispatch_arguments = request.name, request.arguments
                    except Exception as exc:
                        request.result.set_exception(exc)
                        continue
                    task = asyncio.create_task(
                        session.call_tool(dispatch_name, dispatch_arguments)
                    )
                    try:
                        while not task.done():
                            if request.cancel.is_set() and not mutating:
                                task.cancel()
                                break
                            await asyncio.sleep(0.05)
                    except asyncio.CancelledError:
                        # Reap the SDK task, retaining the native-operation fence.
                        task.cancel()
                        try:
                            await task
                        except BaseException:
                            pass
                        raise
                    if request.cancel.is_set() and not mutating:
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        request.result.cancel()
                        continue
                    try:
                        response = self._decode_call_result(request.name, await task)
                        if token is not None:
                            # A real terminal response must precede this private barrier.
                            # Never run it to reconcile a timeout or lost response.
                            self._assert_native_terminal(response)
                            settled = self._decode_call_result(EXECUTE_BLENDER_CODE,
                                await session.call_tool(EXECUTE_BLENDER_CODE,
                                    {"code": _native_identity_probe_code(token)}))
                            native = self._native_probe_result(settled, token)
                            if (native["pid"] != self._native_identity["pid"]
                                    or native["incarnation"] != self._native_incarnation):
                                raise BlenderMCPSecurityError("Blender native incarnation changed")
                            self._native_fence.completed(token, self._native_identity,
                                self._native_incarnation)
                        if request.cancel.is_set():
                            request.result.cancel()
                        else:
                            request.result.set_result(response)
                    except asyncio.CancelledError:
                        # SDK/root-task shutdown is not native completion.
                        raise
                    except BaseException as exc:  # noqa: BLE001 - return via Future
                        if token is not None:
                            state = self._native_fence.status()
                            exc = (BlenderNativeUnresolved(state["recovery_action"])
                                   if state["native_operation_unresolved"] else
                                   BlenderMCPToolError("Blender native operation did not complete"))
                        request.result.set_exception(exc)

    def _validate_launcher_facts(self) -> None:
        if not self.checkout_root.is_dir():
            raise BlenderMCPSecurityError("Blender MCP checkout does not exist")
        if self.command != Path(os.path.abspath(sys.executable)) or not self.command.is_file():
            raise BlenderMCPSecurityError("Blender MCP must use Maestro's trusted Python")
        if not (self.checkout_root / "mcp" / "blmcp" / "__init__.py").is_file():
            raise BlenderMCPSecurityError("verified Blender MCP server source is missing")
        if _version_tuple(self.blender_version) < (5, 1, 0):
            raise BlenderMCPSecurityError("Blender 5.1 or newer is required")
        if self.bridge_port != PINNED_INSTALL.bridge_port:
            raise BlenderMCPSecurityError("Blender MCP bridge port must be 9876")
        try:
            address = ipaddress.ip_address(self.bridge_host)
        except ValueError:
            if self.bridge_host.lower() != "localhost":
                raise BlenderMCPSecurityError("Blender MCP bridge must be localhost")
        else:
            if not address.is_loopback:
                raise BlenderMCPSecurityError("Blender MCP bridge must be loopback-only")
        if self.bridge_host != PINNED_INSTALL.bridge_host:
            raise BlenderMCPSecurityError("Blender MCP bridge attestation requires localhost")
        if self.args[-2:] != ("--transport", "stdio"):
            raise BlenderMCPSecurityError("Blender MCP must use stdio transport")
        if not self.scratch_root.is_absolute():
            raise BlenderMCPSecurityError("Blender MCP scratch root must be absolute")
        marker = Path(__file__).resolve().parents[1] / "tools" / "blender" / "runtime.json"
        try:
            binary = json.loads(marker.read_text())["binary"]
            self._expected_binary = Path(binary).resolve(strict=True)
            if not self._expected_binary.is_file():
                raise ValueError("not a file")
        except (OSError, ValueError, KeyError, TypeError):
            raise BlenderMCPSecurityError("Blender runtime identity is unavailable") from None

    @staticmethod
    def _assert_native_terminal(value):
        current = value.get("structuredContent", value)
        for _depth in range(3):
            if not isinstance(current, Mapping):
                break
            if current.get("status") in {"ok", "error"}:
                return
            current = current.get("result")
        raise BlenderMCPToolError("Blender returned no terminal native response")

    def _guarded_native_command(self, name, arguments):
        """Authenticate the actual socket recipient before any native effects.

        A post-response probe alone cannot bind a newly opened TCP connection.
        Thumbnail code is loaded through the verified upstream's pure template
        helper, preserving its settings, restoration and deferred completion.
        No public tool or caller-controlled exemption is introduced.
        """
        if name == EXECUTE_BLENDER_CODE:
            if set(arguments) != {"code"} or not isinstance(arguments["code"], str):
                raise BlenderMCPSecurityError("Invalid native command")
            code = arguments["code"]
        elif name == RENDER_THUMBNAIL_TO_PATH:
            if set(arguments) != {"output_path"} or not isinstance(arguments["output_path"], str):
                raise BlenderMCPSecurityError("Invalid thumbnail command")
            # This helper imports only os; do not import/register another MCP server.
            helper_path = self.checkout_root / "mcp/blmcp/tools_helpers/__init__.py"
            spec = importlib.util.spec_from_file_location("_maestro_pinned_blender_templates", helper_path)
            if spec is None or spec.loader is None:
                raise BlenderMCPSecurityError("Pinned thumbnail template is unavailable")
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            tool = self.checkout_root / "mcp/blmcp/tools/render_thumbnail_to_path.py"
            expanded = helper.toolcode_load_from_filepath(str(tool))
            # Use the exact upstream footer; Params is defined by the expanded code.
            template = helper.toolcode_wrap_with_calling_convention(expanded)
            code = helper.toolcode_format_call(template,
                _ThumbnailParams(arguments["output_path"]))
            # EXEC's socket envelope is non-strict. Preserve the thumbnail's
            # strict JSON validation for both immediate and deferred results.
            code += (
                "\nimport json as _maestro_json\n"
                "if callable(globals().get('check_is_finished')):\n"
                "    _maestro_original_check = check_is_finished\n"
                "    def check_is_finished():\n"
                "        value = _maestro_original_check()\n"
                "        if value is not None: _maestro_json.dumps(value)\n"
                "        return value\n"
                "else:\n"
                "    _maestro_json.dumps(result)\n"
            )
        else:
            raise BlenderMCPSecurityError("Unrecognized native mutation")
        identity = self._native_identity
        guard = (
            "# Maestro authenticated native dispatch v1\n"
            "import os as _maestro_os, sys as _maestro_sys\n"
            f"if (_maestro_os.getpid() != {identity['pid']!r} or "
            "getattr(_maestro_sys.modules.get('_maestro_native_instance'), 'incarnation', None) "
            f"!= {self._native_incarnation!r}):\n"
            "    raise RuntimeError('Native Blender incarnation changed before dispatch')\n"
            f"exec(compile({code!r}, '<maestro-native>', 'exec'), globals(), globals())\n"
        )
        return EXECUTE_BLENDER_CODE, {"code": guard}

    @staticmethod
    def _native_probe_result(value, token):
        current = value.get("structuredContent", value)
        for _depth in range(3):
            if not isinstance(current, Mapping) or "result" not in current:
                break
            current = current["result"]
        if (not isinstance(current, Mapping) or current.get("status") != "ok"
                or type(current.get("pid")) is not int or current["pid"] < 1
                or current.get("token") != token or current.get("render_running") is not False
                or not isinstance(current.get("incarnation"), str)
                or len(current["incarnation"]) != 32
                or any(c not in "0123456789abcdef" for c in current["incarnation"])):
            raise BlenderMCPSecurityError("Blender native completion could not be verified")
        return current

    def _verify_checkout(self) -> None:
        revision = self._git("rev-parse", "HEAD")
        tag = self._git("describe", "--tags", "--exact-match", "HEAD")
        repository = self._git("remote", "get-url", "origin").rstrip("/")
        dirty = self._git("status", "--porcelain", "--untracked-files=no")
        if revision != PINNED_INSTALL.revision:
            raise BlenderMCPSecurityError("Blender MCP checkout revision does not match")
        if tag != PINNED_INSTALL.tag:
            raise BlenderMCPSecurityError("Blender MCP checkout tag does not match")
        if repository + ".git" != PINNED_INSTALL.repository and repository != PINNED_INSTALL.repository:
            raise BlenderMCPSecurityError("Blender MCP checkout origin does not match")
        if dirty:
            raise BlenderMCPSecurityError("Blender MCP checkout has modified tracked files")

    def _git(self, *args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.checkout_root), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BlenderMCPSecurityError("could not attest Blender MCP checkout") from exc
        return completed.stdout.strip()

    @staticmethod
    def _decode_call_result(name: str, result: Any) -> dict[str, Any]:
        if getattr(result, "isError", False):
            raise BlenderMCPToolError(f"upstream MCP tool {name} returned an error")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, Mapping):
            return {"structuredContent": dict(structured)}
        texts = [
            text
            for item in getattr(result, "content", ())
            if isinstance((text := getattr(item, "text", None)), str)
        ]
        if len(texts) != 1:
            raise BlenderMCPToolError(f"upstream MCP tool {name} returned no JSON result")
        try:
            decoded = json.loads(texts[0])
        except json.JSONDecodeError as exc:
            raise BlenderMCPToolError(f"upstream MCP tool {name} returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise BlenderMCPToolError(f"upstream MCP tool {name} returned a non-object result")
        return dict(decoded)

    @staticmethod
    def _assert_probe_ok(value: Mapping[str, Any]) -> None:
        current: Any = value.get("structuredContent", value)
        for _depth in range(3):
            if not isinstance(current, Mapping):
                break
            if str(current.get("status", "")).lower() == "error":
                raise BlenderMCPToolError("get_objects_summary probe returned an error")
            if "result" not in current:
                break
            current = current["result"]

    @staticmethod
    def _attest_scratch_root(value: Mapping[str, Any]) -> Path:
        current: Any = value.get("structuredContent", value)
        for _depth in range(3):
            if not isinstance(current, Mapping) or "result" not in current:
                break
            current = current["result"]
        if not isinstance(current, Mapping) or current.get("status") != "ok":
            raise BlenderMCPSecurityError("Blender MCP scratch probe returned an error")
        raw_path = current.get("scratch_root")
        if not isinstance(raw_path, str) or not raw_path:
            raise BlenderMCPSecurityError("Blender MCP scratch probe returned no path")
        candidate = Path(raw_path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
            metadata = resolved.stat()
        except (OSError, RuntimeError) as exc:
            raise BlenderMCPSecurityError("Blender MCP scratch root is unavailable") from exc
        if (
            not candidate.is_absolute()
            or candidate != resolved
            or resolved.name != "blender_mcp"
            or not resolved.is_dir()
            or resolved.is_symlink()
            or metadata.st_mode & 0o002
        ):
            raise BlenderMCPSecurityError("Blender MCP scratch root failed attestation")
        return resolved
