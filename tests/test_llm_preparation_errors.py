"""CPU-only checks for the public LLM preparation failure boundary."""
from __future__ import annotations

import ast
import asyncio
import copy
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
LAUNCH_PATH = APP / "launch.py"
LLM_SERVICE_PATH = APP / "services" / "llm_service.py"
if os.fspath(APP) not in sys.path:
    sys.path.insert(0, os.fspath(APP))


DOWNLOAD = (
    "Chat model download failed. Try loading it again."
)
PROJECTOR = (
    "Image support download failed. Try loading the model again."
)
UNKNOWN = "LLM preparation failed; check the local Maestro logs"


class _HTTPException(Exception):
    def __init__(self, *, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _Request:
    def __init__(self, body: dict):
        self.headers = {"content-type": "application/json; charset=utf-8"}
        self._body = body

    async def json(self) -> dict:
        return dict(self._body)


def _load_llm_endpoint(*, failure: BaseException | None):
    from services.public_failure_copy import (
        public_llm_preparation_failure_message,
    )

    source = LAUNCH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(LAUNCH_PATH))
    endpoint = copy.deepcopy(next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "llm_load"
    ))
    endpoint.decorator_list = []

    authorized_calls = []
    control_calls = []

    def authorized(request, selection):
        authorized_calls.append((request, dict(selection)))
        if failure is not None:
            raise failure

    async def run_blocking_shielded(function, /, *args, **kwargs):
        if function is not authorized:
            raise AssertionError("endpoint bypassed the authorized LLM operation")
        return function(*args, **kwargs)

    llm_service = ModuleType("services.llm_service")
    llm_service.get_status = lambda: {"loaded": True, "model_id": "safe-model"}
    llm_operations = ModuleType("services.llm_operations")
    llm_operations.run_blocking_shielded = run_blocking_shielded
    namespace = {
        "Request": object,
        "HTTPException": _HTTPException,
        "SimpleNamespace": SimpleNamespace,
        "traceback": SimpleNamespace(print_exc=lambda: None),
        "wgp": SimpleNamespace(server_config={"services": {}}),
        "_DEFAULT_LLM_REPO": "default-model",
        "_llm_default_device": lambda: "cpu",
        "_llm_provider_api_key": lambda *_args: "provider-api-secret",
        "_require_local_llm_control": lambda request: control_calls.append(request),
        "_run_authorized_llm_with_selection": authorized,
        "public_llm_preparation_failure_message": (
            public_llm_preparation_failure_message
        ),
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[endpoint], type_ignores=[])),
            str(LAUNCH_PATH),
            "exec",
        ),
        namespace,
    )
    return (
        namespace["llm_load"], llm_service, llm_operations,
        authorized_calls, control_calls,
    )


class LlmPreparationFailureCopyTests(unittest.TestCase):
    def test_model_loader_uses_the_shared_reviewed_copy(self):
        source = LLM_SERVICE_PATH.read_text(encoding="utf-8")
        self.assertIn('LLM_PREPARATION_FAILURE_DETAILS["download"]', source)
        self.assertIn('LLM_PREPARATION_FAILURE_DETAILS["projector"]', source)

    def test_mapping_and_helper_admit_only_exact_plain_runtime_errors(self):
        from services.public_failure_copy import (
            LLM_PREPARATION_FAILURE_DETAILS,
            public_llm_preparation_failure_message,
        )

        self.assertEqual(dict(LLM_PREPARATION_FAILURE_DETAILS), {
            "download": DOWNLOAD,
            "projector": PROJECTOR,
            "unknown": UNKNOWN,
        })
        with self.assertRaises(TypeError):
            LLM_PREPARATION_FAILURE_DETAILS["unknown"] = "private"

        self.assertEqual(
            public_llm_preparation_failure_message(RuntimeError(DOWNLOAD)),
            DOWNLOAD,
        )
        self.assertEqual(
            public_llm_preparation_failure_message(RuntimeError(PROJECTOR)),
            PROJECTOR,
        )

        class RuntimeSubclass(RuntimeError):
            pass

        class Hostile(RuntimeError):
            def __str__(self):
                raise AssertionError("untrusted exceptions must not be stringified")

        private = "provider-private token=/private/model.gguf"
        for error in (
            RuntimeError(f"{DOWNLOAD} {private}"),
            RuntimeError(DOWNLOAD, private),
            RuntimeError({"message": DOWNLOAD, "secret": private}),
            RuntimeSubclass(DOWNLOAD),
            ValueError(DOWNLOAD),
            Hostile(private),
        ):
            with self.subTest(error_type=type(error).__name__, args=error.args):
                self.assertEqual(
                    public_llm_preparation_failure_message(error),
                    UNKNOWN,
                )

    def test_actual_endpoint_returns_only_fixed_failure_copy(self):
        import services

        private_body = {
            "provider": "private-provider",
            "model_id": "private/model-id",
            "remote_url": "https://secret.invalid/api?token=request-secret",
        }
        cases = (
            (RuntimeError(DOWNLOAD), DOWNLOAD),
            (RuntimeError(PROJECTOR), PROJECTOR),
            (
                RuntimeError(f"{DOWNLOAD} provider-private /private/model.gguf"),
                UNKNOWN,
            ),
            (ValueError("provider-private token=request-secret"), UNKNOWN),
        )
        for failure, expected in cases:
            with self.subTest(expected=expected):
                endpoint, llm_service, llm_operations, calls, controls = (
                    _load_llm_endpoint(failure=failure)
                )
                with (
                    mock.patch.object(
                        services, "llm_service", llm_service, create=True,
                    ),
                    mock.patch.dict(sys.modules, {
                        "services.llm_service": llm_service,
                        "services.llm_operations": llm_operations,
                    }),
                ):
                    with self.assertRaises(_HTTPException) as raised:
                        asyncio.run(endpoint(_Request(private_body)))
                self.assertEqual(raised.exception.status_code, 500)
                self.assertEqual(raised.exception.detail, expected)
                self.assertNotIn("private-provider", raised.exception.detail)
                self.assertNotIn("request-secret", raised.exception.detail)
                self.assertNotIn("/private/model.gguf", raised.exception.detail)
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(controls), 1)

    def test_actual_endpoint_success_contract_is_unchanged(self):
        import services

        endpoint, llm_service, llm_operations, calls, controls = (
            _load_llm_endpoint(failure=None)
        )
        with (
            mock.patch.object(services, "llm_service", llm_service, create=True),
            mock.patch.dict(sys.modules, {
                "services.llm_service": llm_service,
                "services.llm_operations": llm_operations,
            }),
        ):
            result = asyncio.run(endpoint(_Request({"provider": "local"})))
        self.assertEqual(result, {
            "status": "ok", "loaded": True, "model_id": "safe-model",
        })
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(controls), 1)


if __name__ == "__main__":
    unittest.main()
