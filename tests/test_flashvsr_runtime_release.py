"""A failed FlashVSR load must not retain a partial model graph."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "app/postprocessing/flashvsr/runtime.py"


def _upscale_with_runtime(runtime):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upscale_video"
    )
    function.decorator_list = []
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[],
    )
    namespace = {
        "_RUNTIME": runtime,
        "_report_progress": Mock(),
        "FLASHVSR_VARIANT_TINY_LONG": "tiny-long",
        "FLASHVSR_TOPK_RATIO": 0.0,
        "FLASHVSR_STILL_IMAGE_SHIFT_CORRECTION": False,
    }
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace["upscale_video"]


class FlashVSRReleaseTests(unittest.TestCase):
    def test_failed_initial_load_releases_partial_graph(self):
        runtime = Mock()
        runtime.load.side_effect = RuntimeError("partial load failed")
        upscale = _upscale_with_runtime(runtime)

        with self.assertRaisesRegex(RuntimeError, "partial load failed"):
            upscale(object(), 2.0, object(), init_pipe=object(), profile=object(), persistent_models=True)

        runtime.upscale.assert_not_called()
        runtime.release.assert_called_once_with()

    def test_failed_pass_releases_persistent_graph_but_success_retains_it(self):
        runtime = Mock()
        upscale = _upscale_with_runtime(runtime)
        runtime.upscale.side_effect = RuntimeError("pass failed")

        with self.assertRaisesRegex(RuntimeError, "pass failed"):
            upscale(object(), 2.0, object(), init_pipe=object(), profile=object(), persistent_models=True)
        runtime.release.assert_called_once_with()

        runtime.reset_mock()
        runtime.upscale.side_effect = None
        runtime.upscale.return_value = (object(), None)
        result = upscale(object(), 2.0, object(), init_pipe=object(), profile=object(), persistent_models=True)
        self.assertIsNotNone(result[0])
        runtime.release.assert_not_called()


if __name__ == "__main__":
    unittest.main()
