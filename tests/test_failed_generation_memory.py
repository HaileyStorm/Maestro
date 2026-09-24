"""Failed generation cleanup must preserve errors and deliberate H3 retries."""

from __future__ import annotations

import ast
from contextlib import redirect_stdout
import inspect
import io
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_oom_relief import H3OomReliefRetry


SOURCE = APP / "wgp.py"


def _source_function(name: str, namespace: dict):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace[name]


def _wrapper(implementation, cleanup):
    cuda = SimpleNamespace(is_available=Mock(return_value=False), empty_cache=Mock())
    namespace = {
        "_generate_video_impl": implementation,
        "_release_failed_generation_resources": cleanup,
        "_notify_h3_profile_observer": Mock(),
        "get_default_profile": Mock(return_value=4.5),
        "get_output_type_for_model": Mock(return_value="video"),
        "inspect": inspect,
        "traceback": traceback,
        "gc": SimpleNamespace(collect=Mock()),
        "torch": SimpleNamespace(cuda=cuda),
    }
    return _source_function("generate_video", namespace), namespace


class FailedGenerationMemoryTests(unittest.TestCase):
    def test_failed_result_releases_but_other_results_keep_residency(self):
        values = iter((False, []))

        def implementation(task, model_type):
            return next(values)

        cleanup = Mock()
        generate, _ = _wrapper(implementation, cleanup)
        self.assertIs(generate({}, model_type="wan_2_2"), False)
        self.assertEqual(generate({}, model_type="wan_2_2"), [])
        cleanup.assert_called_once_with()

    def test_exception_clears_inner_frame_and_preserves_original_error(self):
        def implementation(task, model_type):
            payload = bytearray(1024)
            raise RuntimeError("original generation error")

        cleanup = Mock(side_effect=ValueError("cleanup also failed"))
        generate, _ = _wrapper(implementation, cleanup)
        with redirect_stdout(io.StringIO()):
            try:
                generate({}, model_type="wan_2_2")
            except RuntimeError as error:
                self.assertIn("original generation error", str(error))
                frames = []
                cursor = error.__traceback__
                while cursor is not None:
                    frames.append(cursor.tb_frame)
                    cursor = cursor.tb_next
                inner = next(frame for frame in frames if frame.f_code.co_name == "implementation")
                self.assertNotIn("payload", inner.f_locals)
            else:
                self.fail("The original generation error was not raised")
        cleanup.assert_called_once_with()

    def test_h3_oom_relief_retries_without_releasing_model(self):
        calls = []

        def implementation(task, model_type, override_profile, resolution, num_inference_steps):
            calls.append((resolution, num_inference_steps, override_profile))
            if len(calls) == 1:
                raise H3OomReliefRetry({
                    "resolution": "608x352",
                    "num_inference_steps": 8,
                    "override_profile": 5,
                })
            return []

        cleanup = Mock()
        generate, namespace = _wrapper(implementation, cleanup)
        task = {"params": {}}
        result = generate(
            task, model_type="minimax_h3", override_profile=4.5,
            resolution="864x480", num_inference_steps=20,
        )
        self.assertEqual(result, [])
        self.assertEqual(calls, [("864x480", 20, 4.5), ("608x352", 8, 5)])
        self.assertEqual(task["params"]["num_inference_steps"], 8)
        cleanup.assert_not_called()
        namespace["gc"].collect.assert_called_once_with()

    def test_auxiliary_residency_released_even_if_main_release_fails(self):
        flash = SimpleNamespace(dit=object())
        flash_module = SimpleNamespace(_RUNTIME=flash, release_models=Mock())
        mmaudio_module = SimpleNamespace(
            persistent_offloadobj=object(), release_persistent_models=Mock(),
        )
        release_model = Mock(side_effect=RuntimeError("offload hook failed"))
        cuda = SimpleNamespace(is_available=Mock(return_value=False), empty_cache=Mock())
        namespace = {
            "release_model": release_model,
            "sys": SimpleNamespace(modules={
                "postprocessing.flashvsr.runtime": flash_module,
                "postprocessing.mmaudio.mmaudio": mmaudio_module,
            }),
            "gc": SimpleNamespace(collect=Mock()),
            "torch": SimpleNamespace(cuda=cuda),
        }
        cleanup = _source_function("_release_failed_generation_resources", namespace)
        with redirect_stdout(io.StringIO()):
            cleanup()
        release_model.assert_called_once_with()
        flash_module.release_models.assert_called_once_with()
        mmaudio_module.release_persistent_models.assert_called_once_with()
        namespace["gc"].collect.assert_called_once_with()
        cuda.empty_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
