"""CPU-only evidence for H3 final-media verification."""

import ast
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services.h3_output_integrity import expected_h3_final_frames, probe_h3_output


def _production_final_checker():
    source = Path(__file__).resolve().parents[1] / "app" / "launch.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), str(source))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_h3_final_output_integrity"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"os": os}
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["_h3_final_output_integrity"]


def _probe_payload(*, audio=True, width=608, frames=124):
    streams = [{
        "codec_type": "video", "width": width, "height": 352,
        "avg_frame_rate": "24/1", "nb_read_frames": str(frames),
        "duration": str(frames / 24),
    }]
    if audio:
        streams.append({"codec_type": "audio", "sample_rate": "32000", "channels": 2})
    return json.dumps({"streams": streams, "format": {"duration": str(frames / 24)}})


class H3OutputIntegrityTests(unittest.TestCase):
    def test_extend_final_counts_retained_source_and_generated_frames(self):
        prefix = {
            "version": 1,
            "input_field": "video_source:0",
            "source_native_frames": 124,
            "retained_frames": 124,
            "output_fps": 24.0,
            "fit": "contain",
            "conditioning": "last_frame",
            "audio_policy": "preserve_source_then_generated",
        }
        cursor = {"completed_units": [{
            "kind": "h3_segment", "index": 0,
            "settings": {"source_prefix": {**prefix, "sha256": "a" * 64, "size": 100}},
        }]}
        final_unit = {
            "kind": "h3_concat",
            "settings": {"source_prefix": {**prefix, "sha256": "a" * 64, "size": 100}},
        }
        public_prefix = {
            "source_frames": 124, "added_frames": 124,
            "final_output_frames": 248,
        }
        self.assertEqual(expected_h3_final_frames(124), 124)
        self.assertEqual(
            expected_h3_final_frames(
                124, planned_source_prefix=prefix,
                public_source_prefix=public_prefix,
                recovery_cursor=cursor, recovery_final_unit=final_unit,
                require_recovery_evidence=True,
            ),
            248,
        )
        self.assertEqual(
            expected_h3_final_frames(124, recovery_cursor=cursor), 248,
        )
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "extended.mp4"
            artifact.write_bytes(b"synthetic container bytes")
            report = probe_h3_output(
                artifact, expected_frames=248, expected_fps=24,
                require_audio=True,
                run=lambda *_a, **_k: SimpleNamespace(
                    stdout=_probe_payload(frames=248),
                ),
                which=lambda command: command,
            )
            self.assertEqual(report["validation"], "valid")
        with self.assertRaises(ValueError):
            expected_h3_final_frames(
                124, planned_source_prefix={**prefix, "retained_frames": 123},
                recovery_cursor=cursor,
            )
        with self.assertRaises(ValueError):
            expected_h3_final_frames(
                124, planned_source_prefix={**prefix, "retained_frames": True},
            )
        with self.assertRaises(ValueError):
            expected_h3_final_frames(
                124, recovery_cursor={"completed_units": []},
                recovery_final_unit=final_unit,
                require_recovery_evidence=True,
            )
        with self.assertRaises(ValueError):
            expected_h3_final_frames(
                124, recovery_cursor=cursor,
                recovery_final_unit={"kind": "h3_concat", "settings": {}},
                require_recovery_evidence=True,
            )
        with self.assertRaises(ValueError):
            expected_h3_final_frames(
                124, public_source_prefix=public_prefix,
                recovery_cursor={"completed_units": []},
                require_recovery_evidence=True,
            )

    def test_verified_geometry_frame_grid_audio_and_hash_have_no_path(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "private-output.mp4"
            artifact.write_bytes(b"synthetic container bytes")
            result = probe_h3_output(
                artifact, expected_resolution=(608, 352), expected_fps=24,
                expected_frames=124, require_audio=True,
                audio_sample_rate=32000, audio_channels=2,
                run=lambda *_a, **_k: SimpleNamespace(stdout=_probe_payload()),
                which=lambda command: command,
            )
            self.assertEqual(result["validation"], "valid")
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(result["frame_count"], 124)
            self.assertTrue(result["artifact_sha256"].startswith("sha256:"))
            self.assertNotIn(str(artifact), json.dumps(result))

    def test_dark_static_and_silent_samples_are_advisory(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "intentional-still.mp4"
            artifact.write_bytes(b"synthetic container bytes")

            def execute(command, **_kwargs):
                if command[0] == "ffprobe":
                    return SimpleNamespace(stdout=_probe_payload())
                if "-vf" in command:
                    return SimpleNamespace(stdout=bytes(32 * 32 * 3))
                return SimpleNamespace(stdout=struct.pack("<100f", *([0.0] * 100)))

            result = probe_h3_output(
                artifact, require_audio=True, sample_signal=True,
                run=execute, which=lambda command: command,
            )
            self.assertEqual(result["validation"], "valid")
            self.assertEqual(result["sampled_max_mean_luma"], 0)
            self.assertEqual(result["sampled_max_motion_delta"], 0)
            self.assertEqual(result["sampled_audio_ac_rms"], 0)

    def test_missing_promised_audio_and_wrong_geometry_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "bad-output.mp4"
            artifact.write_bytes(b"synthetic container bytes")
            result = probe_h3_output(
                artifact, expected_resolution=(608, 352), require_audio=True,
                run=lambda *_a, **_k: SimpleNamespace(stdout=_probe_payload(audio=False, width=600)),
                which=lambda command: command,
            )
            self.assertEqual(result["validation"], "invalid")
            self.assertFalse(result["checks"]["one_audio_stream"])
            self.assertFalse(result["checks"]["expected_dimensions"])

    def test_missing_probe_is_unverified_and_symlink_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "output.mp4"
            artifact.write_bytes(b"synthetic container bytes")
            self.assertEqual(
                probe_h3_output(artifact, which=lambda _command: None)["validation"],
                "unverified",
            )
            link = Path(directory) / "alias.mp4"
            link.symlink_to(artifact)
            self.assertEqual(probe_h3_output(link)["validation"], "invalid")

    def test_production_final_selection_fails_closed_without_paths(self):
        check = _production_final_checker()
        seen = []

        def valid(path, **options):
            seen.append((path, options))
            return {"validation": "valid", "checks": {"video": True}}

        self.assertEqual(check("/private", [], probe=valid)["validation"], "invalid")
        result = check("/private", ["scene.mp4"], probe=valid)
        self.assertEqual(result["validation"], "valid")
        self.assertEqual(seen, [("/private/scene.mp4", {
            "require_audio": True, "sample_signal": True,
        })])
        self.assertNotIn("/private", json.dumps(result))
        self.assertEqual(
            check("/private", ["../elsewhere.mp4"], probe=valid)["validation"],
            "invalid",
        )
        self.assertEqual(len(seen), 1)

        expected = check(
            "/private", ["scene.mp4"], expected_frames=124,
            expected_fps=24.0, probe=valid,
        )
        self.assertEqual(expected["validation"], "valid")
        self.assertEqual(seen[-1][1]["expected_frames"], 124)
        self.assertEqual(seen[-1][1]["expected_fps"], 24.0)

        def wrong_length(_path, **_options):
            return {"validation": "invalid", "checks": {"expected_frames": False}}

        self.assertEqual(
            check("/private", ["scene.mp4"], expected_frames=124,
                  probe=wrong_length)["validation"],
            "invalid",
        )


if __name__ == "__main__":
    unittest.main()
