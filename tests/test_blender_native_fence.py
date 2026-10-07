"""CPU safety contracts: durable native ambiguity must prohibit GPU overlap."""
from __future__ import annotations

import ast
import asyncio
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from app.services.blender_native_fence import NativeFence, BlenderNativeUnresolved, process_identity
from app.services.blender_mcp_service import (
    EXECUTE_BLENDER_CODE, RENDER_THUMBNAIL_TO_PATH, UPSTREAM_TOOL_ALLOWLIST,
    BlenderMCPCancelled, BlenderMCPToolError, BlenderMCPSecurityError,
)
from app.services.blender_mcp_transport import StdioBlenderMCPClient


class NativeFenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "fence"
        self.identity = process_identity(os.getpid())
        self.fence = NativeFence(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def begin(self, fence=None):
        return (fence or self.fence).begin(self.identity, "a" * 32,
            EXECUTE_BLENDER_CODE, {"code": "owned CPU operation"})

    def test_pending_survives_process_exit_and_fresh_import(self):
        # The renderer identity is this live parent; the backend is disposable.
        code = ("import json,sys; from app.services.blender_native_fence import NativeFence; "
                "NativeFence(sys.argv[1]).begin(json.loads(sys.argv[2]),'a'*32,"
                "'execute_blender_code',{'code':'CPU only'}); "
                "__import__('os')._exit(17)")
        result = subprocess.run([sys.executable, "-c", code, str(self.root),
            json.dumps(self.identity)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        recovered = NativeFence(self.root)
        with self.assertRaises(BlenderNativeUnresolved):
            recovered.wrap_lock(threading.Lock()).acquire(False)
        self.assertIsNotNone(json.loads((self.root / "state.json").read_text())["pending"])

    def test_only_exact_completion_settles_and_reused_pid_holds(self):
        token = self.begin()
        for wrong_token, wrong_incarnation in (("b" * 32, "a" * 32), (token, "b" * 32)):
            with self.assertRaises(BlenderNativeUnresolved):
                self.fence.completed(wrong_token, self.identity, wrong_incarnation)
        reused = dict(self.identity, birth=self.identity["birth"] + "0")
        different = NativeFence(self.root, process_reader=lambda _pid: reused)
        with self.assertRaises(BlenderNativeUnresolved):
            different.require_idle()
        with self.assertRaises(BlenderNativeUnresolved):
            different.completed(token, self.identity, "a" * 32)
        self.fence.completed(token, self.identity, "a" * 32)
        self.fence.require_idle()
        newer = self.begin()
        with self.assertRaises(BlenderNativeUnresolved):
            self.fence.completed(token, self.identity, "a" * 32)
        self.fence.completed(newer, self.identity, "a" * 32)

    def test_independent_exact_process_death_clears_without_replay(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            identity = process_identity(child.pid)
            self.fence.begin(identity, "a" * 32, "execute_blender_code", {})
            with self.assertRaises(BlenderNativeUnresolved):
                self.fence.require_idle()
            child.terminate()
            child.wait(timeout=5)
            self.fence.require_idle()
            self.assertIsNone(json.loads((self.root / "state.json").read_text())["pending"])
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)

    def test_waiting_acquirer_rechecks_after_previous_owner_releases(self):
        underlying = threading.Lock()
        underlying.acquire()
        entered = threading.Event()
        checked = threading.Event()
        original = self.fence.require_idle
        def check():
            original()
            checked.set()
        self.fence.require_idle = check
        result = []
        def waiter():
            try:
                self.fence.wrap_lock(underlying).acquire()
                entered.set()
            except BlenderNativeUnresolved:
                result.append("held")
        worker = threading.Thread(target=waiter)
        worker.start()
        self.assertTrue(checked.wait(2))
        token = self.begin()
        underlying.release()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(entered.is_set())
        self.assertEqual(result, ["held"])
        self.assertFalse(underlying.locked())
        self.fence.completed(token, self.identity, "a" * 32)

    def test_lock_semantics_and_cross_thread_release(self):
        lock = self.fence.wrap_lock(threading.Lock())
        self.assertTrue(lock.acquire(False))
        self.assertTrue(lock.locked())
        self.assertFalse(lock.acquire(False))
        self.assertFalse(lock.acquire(timeout=0.01))
        worker = threading.Thread(target=lock.release)
        worker.start()
        worker.join(2)
        self.assertFalse(lock.locked())
        with lock as acquired:
            self.assertIs(acquired, True)
            self.assertTrue(lock.locked())
        self.assertFalse(lock.locked())
        with self.assertRaises(ValueError):
            lock.acquire(False, 0.01)

    def test_unreadable_missing_malformed_or_linked_state_never_admits(self):
        self.fence.require_idle()
        state = self.root / "state.json"
        original = state.read_bytes()
        for raw in (b"{}", b"not-json", b'{"schema":1,"pending":{}}'):
            state.write_bytes(raw)
            with self.assertRaises(BlenderNativeUnresolved):
                self.fence.require_idle()
        state.unlink()
        with self.assertRaises(BlenderNativeUnresolved):
            self.fence.require_idle()
        state.write_bytes(original)
        state.chmod(0o600)
        foreign = self.root.parent / "foreign"
        foreign.write_bytes(original)
        state.unlink()
        try:
            state.symlink_to(foreign)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(BlenderNativeUnresolved):
            self.fence.require_idle()
        self.assertEqual(foreign.read_bytes(), original)

    def test_failed_persistence_prevents_dispatch_reservation(self):
        self.fence.require_idle()
        with mock.patch.object(self.fence, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(BlenderNativeUnresolved):
                self.begin()
        self.fence.require_idle()

    @unittest.skipUnless(os.name == "posix", "POSIX uid contract")
    def test_posix_psutil_owner_uses_numeric_uid(self):
        process = SimpleNamespace(oneshot=lambda: nullcontext(), uids=lambda: SimpleNamespace(real=os.getuid()),
            create_time=lambda: 123.5, exe=lambda: "/usr/bin/blender", username=lambda: "owner")
        psutil = SimpleNamespace(Process=lambda _pid: process)
        with mock.patch("app.services.blender_native_fence.sys.platform", "darwin"), mock.patch.dict(
                sys.modules, {"psutil": psutil}):
            identity = process_identity(12)
        self.assertEqual(identity["owner"], str(os.getuid()))
        self.assertEqual(identity["platform"], "darwin")

    def test_reparse_metadata_is_rejected_even_without_symlink_bit(self):
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(st_mode=0o40700, st_file_attributes=0x400)
        with self.assertRaises(BlenderNativeUnresolved):
            NativeFence._reject_redirect(path)


class TransportFenceTests(unittest.TestCase):
    def test_native_cancellation_waits_for_completion_and_suppresses_result(self):
        self._exercise("cancel")

    def test_timeout_keeps_durable_hold_after_sdk_child_exit(self):
        self._exercise("timeout")

    def test_replacement_incarnation_cannot_settle(self):
        self._exercise("replacement")

    def test_backend_sdk_shutdown_retains_hold_while_native_work_continues(self):
        self._exercise("shutdown")

    def test_success_settles_exact_guarded_mutation(self):
        self._exercise("success")

    def test_replaced_socket_recipient_cannot_execute_before_old_pid_death_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            client = StdioBlenderMCPClient(checkout_root=temporary, blender_version="5.1.0")
            identity = process_identity(os.getpid())
            client._native_identity, client._native_incarnation = identity, "a" * 32
            name, arguments = client._guarded_native_command(EXECUTE_BLENDER_CODE,
                {"code": "from __future__ import annotations\neffects.append('native effect')\nresult = {}"})
            self.assertEqual(name, EXECUTE_BLENDER_CODE)
            effects = []
            with mock.patch.dict(sys.modules, {"_maestro_native_instance": SimpleNamespace(incarnation="a" * 32)}):
                with mock.patch("os.getpid", return_value=identity["pid"] + 1):
                    with self.assertRaises(RuntimeError):
                        exec(arguments["code"], {"effects": effects})
                with mock.patch.dict(sys.modules, {"_maestro_native_instance": SimpleNamespace(incarnation="b" * 32)}):
                    with self.assertRaises(RuntimeError):
                        exec(arguments["code"], {"effects": effects})
                self.assertEqual(effects, [])
                exec(arguments["code"], {"effects": effects})
                self.assertEqual(effects, ["native effect"])

    def test_thumbnail_guard_preserves_pinned_calling_convention_and_deferred_result(self):
        # Test the transport adaptation independently of an installed Blender.
        template = ("from typing import NamedTuple\n"
                    "class Params(NamedTuple):\n    output_path: str\n"
                    "class Result(NamedTuple):\n    status: str\n    filepath: str\n"
                    "def main(params):\n"
                    "    effects.append(params.output_path)\n"
                    "    def completed(): return {'status': 'ok', 'filepath': params.output_path}\n"
                    "    return completed\n")
        helper = SimpleNamespace(
            toolcode_load_from_filepath=lambda _path: template,
            toolcode_wrap_with_calling_convention=lambda code: code + (
                "\n_rv = main(__BLMCP_PARAMS__)\n"
                "if callable(_rv):\n    check_is_finished = _rv\n    result = {}\n"
                "else:\n    result = _rv._asdict()\n"),
            toolcode_format_call=lambda code, params: code.replace("__BLMCP_PARAMS__", repr(params)),
        )
        client = StdioBlenderMCPClient(checkout_root="unused", blender_version="5.1.0")
        client._native_identity = process_identity(os.getpid())
        client._native_incarnation = "a" * 32
        loader = SimpleNamespace(exec_module=lambda _module: None)
        with mock.patch("app.services.blender_mcp_transport.importlib.util.spec_from_file_location",
                return_value=SimpleNamespace(loader=loader)), mock.patch(
                "app.services.blender_mcp_transport.importlib.util.module_from_spec", return_value=helper):
            name, arguments = client._guarded_native_command(RENDER_THUMBNAIL_TO_PATH,
                {"output_path": "quoted' private.png"})
        self.assertEqual(name, EXECUTE_BLENDER_CODE)
        namespace = {"effects": []}
        with mock.patch.dict(sys.modules, {"_maestro_native_instance": SimpleNamespace(incarnation="b" * 32)}):
            with self.assertRaises(RuntimeError):
                exec(arguments["code"], namespace)
        self.assertEqual(namespace["effects"], [])
        with mock.patch.dict(sys.modules, {"_maestro_native_instance": SimpleNamespace(incarnation="a" * 32)}):
            exec(arguments["code"], namespace)
            self.assertEqual(namespace["effects"], ["quoted' private.png"])
            self.assertEqual(namespace["check_is_finished"](),
                {"status": "ok", "filepath": "quoted' private.png"})

    def _exercise(self, behavior):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fence = NativeFence(root / "fence")
            started, finish, native_done = threading.Event(), threading.Event(), threading.Event()
            calls = []
            native_threads = []
            identity = process_identity(os.getpid())
            def native_work():
                started.set()
                finish.wait(5)
                native_done.set()
            class Session:
                def __init__(self, *_args, **_kwargs):
                    pass
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *_args):
                    pass
                async def initialize(self):
                    return SimpleNamespace(serverInfo=SimpleNamespace(name="blender-mcp"))
                async def list_tools(self):
                    return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in UPSTREAM_TOOL_ALLOWLIST])
                async def call_tool(self, name, arguments):
                    calls.append((name, arguments))
                    code = arguments.get("code", "")
                    if "# Maestro native process/idle proof v1" in code:
                        token = ast.literal_eval(code.split("'token': ", 1)[1].split("}", 1)[0])
                        incarnation = "b" * 32 if token and behavior == "replacement" else "a" * 32
                        result = {"status": "ok", "pid": os.getpid(), "incarnation": incarnation,
                                  "render_running": False, "token": token}
                    elif "# Maestro authenticated native dispatch v1" in code:
                        # The record must already be durable at the native send boundary.
                        self_record = json.loads((fence.root / "state.json").read_text())["pending"]
                        if not self_record:
                            raise AssertionError("dispatch preceded persistence")
                        worker = threading.Thread(target=native_work)
                        native_threads.append(worker)
                        worker.start()
                        if behavior == "timeout":
                            await asyncio.sleep(0.1)
                            raise TimeoutError("native still running")
                        while not native_done.is_set():
                            await asyncio.sleep(0.01)
                        result = {"status": "ok", "filepath": "private scratch only"}
                    else:
                        result = {"status": "ok"}
                    return SimpleNamespace(isError=False, structuredContent={"status": "ok", "result": result})
            class Context:
                async def __aenter__(self):
                    return (object(), object())
                async def __aexit__(self, *_args):
                    pass
            sdk = SimpleNamespace(ClientSession=Session, StdioServerParameters=lambda **kw: SimpleNamespace(**kw))
            stdio = SimpleNamespace(get_default_environment=lambda: {}, stdio_client=lambda _params: Context())
            client = StdioBlenderMCPClient(checkout_root=root, blender_version="5.1.0",
                scratch_root=root, request_timeout_seconds=1, native_fence=fence)
            client._expected_binary = Path(identity["exe"]).resolve()
            observed = []
            def caller():
                try:
                    observed.append(client.call_tool(EXECUTE_BLENDER_CODE,
                        {"code": "# owned CPU work\nresult = {'status': 'ok'}"},
                        cancelled=(lambda: started.is_set()) if behavior == "cancel" else None))
                except BaseException as error:
                    observed.append(error)
            with mock.patch.object(client, "_validate_launcher_facts"), mock.patch.object(client, "_verify_checkout"), mock.patch(
                "app.services.blender_mcp_transport.importlib.import_module",
                side_effect=lambda n: sdk if n == "mcp" else stdio):
                try:
                    worker = threading.Thread(target=caller)
                    worker.start()
                    self.assertTrue(started.wait(2))
                    with self.assertRaises(BlenderNativeUnresolved):
                        fence.wrap_lock(threading.Lock()).acquire(False)
                    if behavior == "cancel":
                        time.sleep(0.15)
                        self.assertTrue(worker.is_alive())
                        self.assertFalse(native_done.is_set())
                    if behavior == "timeout":
                        worker.join(2)
                        self.assertIsInstance(observed[0], BlenderNativeUnresolved)
                        client.close()
                        with self.assertRaises(BlenderNativeUnresolved):
                            NativeFence(fence.root).require_idle()
                        self.assertFalse(native_done.is_set())
                    if behavior == "shutdown":
                        client._cancel_worker()
                        worker.join(2)
                        self.assertFalse(worker.is_alive())
                        self.assertIsInstance(observed[0], BlenderMCPToolError)
                        self.assertFalse(native_done.is_set())
                        with self.assertRaises(BlenderNativeUnresolved):
                            NativeFence(fence.root).require_idle()
                    finish.set()
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    if behavior == "cancel":
                        self.assertIsInstance(observed[0], BlenderMCPCancelled)
                        fence.require_idle()
                    elif behavior == "replacement":
                        self.assertIsInstance(observed[0], BlenderNativeUnresolved)
                        with self.assertRaises(BlenderNativeUnresolved):
                            fence.require_idle()
                    elif behavior == "success":
                        self.assertIsInstance(observed[0], dict)
                        fence.require_idle()
                    self.assertEqual(sum("# Maestro authenticated native dispatch v1" in args.get("code", "")
                                         for _name, args in calls), 1)
                finally:
                    finish.set()
                    for thread in native_threads:
                        thread.join(2)
                    client.close()


if __name__ == "__main__":
    unittest.main()
