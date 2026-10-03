"""Exercise the real queue/WGP callback seam without loading a model."""

from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path

from test_job_lifecycle_wiring import _function, _load_isolated_function, _parse


def execute(node, namespace):
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    # Execute reviewed repository AST only, never request input.
    exec(compile(module, "repository-callback-seam", "exec"), namespace)  # noqa: S102


class H3CumulativeCheckpointRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launch = _parse("app/launch.py")
        generation = _function(launch, "_run_generation")
        cls.install = next(
            node for node in ast.walk(generation)
            if isinstance(node, ast.If)
            and any(
                isinstance(item, ast.Assign)
                and any(ast.unparse(target) == "params['after_segment_output']"
                        for target in item.targets)
                for item in node.body
            )
        )
        cls.staged = _load_isolated_function(
            "app/launch.py", "_queue_recovery_staged_media_paths",
            {"os": os, "GENERATED_MEDIA_EXTENSIONS": {".mp4"}},
        )
        wgp = _function(_parse("app/wgp.py"), "_generate_video_impl")
        cls.invoke = next(
            node for node in ast.walk(wgp)
            if isinstance(node, ast.If)
            and any(
                isinstance(item, ast.Assign)
                and isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Name)
                and item.value.func.id == "seal_multi_clip_segment_before_concat"
                for item in node.body
            )
        )
        cls.seal = _load_isolated_function(
            "app/wgp.py", "seal_multi_clip_segment_before_concat",
            {"PostDecodeStageError": RuntimeError},
        )

    def run_seam(self, cumulative, *, recovery=True):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging = root / "staging"
            staging.mkdir()
            source = staging / "window.mp4"
            source.write_bytes(b"rendered-native-AV")
            promoted = root / source.name
            calls = []

            def ordinary_callback(path, clip):
                calls.append(clip["index"])
                os.replace(path, promoted)
                return str(promoted)

            ns = {
                "params": {}, "recovery_h3_segment": recovery,
                "h3_cumulative_plan": cumulative,
                "_seal_h3_segment_before_concat": ordinary_callback,
            }
            execute(self.install, ns)
            callback = ns["params"].get("after_segment_output")
            wgp = {
                "multi_clip_info": {"index": 0, "total": 2, "defer_concat": True},
                "is_image": False, "audio_only": False, "is_last_window": True,
                "after_segment_output": callback, "video_path": str(source),
                "seal_multi_clip_segment_before_concat": type(self).seal,
            }
            execute(self.invoke, wgp)
            staged = type(self).staged([wgp["video_path"]], str(staging))
            return calls, staged, source.exists(), promoted.exists()

    def test_cumulative_window_stays_staged_for_its_retained_av_checkpoint(self):
        calls, staged, source, promoted = self.run_seam({"windows": [{}, {}]})
        self.assertEqual(calls, [])
        self.assertEqual(list(staged), ["window.mp4"])
        self.assertTrue(source)
        self.assertFalse(promoted)

    def test_ordinary_h3_segment_keeps_preconcat_promotion(self):
        calls, staged, source, promoted = self.run_seam(None)
        self.assertEqual(calls, [0])
        self.assertEqual(staged, {})
        self.assertFalse(source)
        self.assertTrue(promoted)

    def test_nonrecovery_segment_has_no_recovery_promotion(self):
        calls, staged, source, promoted = self.run_seam(None, recovery=False)
        self.assertEqual(calls, [])
        self.assertEqual(list(staged), ["window.mp4"])
        self.assertTrue(source)
        self.assertFalse(promoted)


if __name__ == "__main__":
    unittest.main()
