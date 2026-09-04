"""Probe caches must follow file identity, not just the dest path.

FlashVSR delivery copies native bytes onto a work path, probes 1344x768,
then unlinks and renames a 2688x1536 mux onto the same name. Path-only
lru_cache made exact delivery refuse the upscaled file.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from shared.utils.utils import get_video_info  # noqa: E402
from shared.utils.video_decode import probe_video_stream_metadata  # noqa: E402


def _clear_video_probe_caches() -> None:
    decode = sys.modules.get("shared.utils.video_decode")
    utils = sys.modules.get("shared.utils.utils")
    for module in (decode, utils):
        if module is None:
            continue
        for name in dir(module):
            fn = getattr(module, name, None)
            if callable(fn) and hasattr(fn, "cache_clear") and "video" in name.lower():
                fn.cache_clear()
            elif callable(fn) and hasattr(fn, "cache_clear") and name.startswith("_probe"):
                fn.cache_clear()


def _ffprobe_from_payload(cmd, **_kwargs):
    path = cmd[-1]
    payload = Path(path).read_bytes()
    if payload.startswith(b"native"):
        width, height = 1344, 768
    else:
        width, height = 2688, 1536
    stdout = json.dumps(
        {
            "streams": [
                {
                    "width": width,
                    "height": height,
                    "avg_frame_rate": "24/1",
                    "nb_frames": "24",
                    "sample_aspect_ratio": "1:1",
                    "duration": "1.0",
                }
            ],
            "format": {"duration": "1.0"},
        }
    )
    return SimpleNamespace(returncode=0, stdout=stdout)


class VideoProbeCacheTests(unittest.TestCase):
    def setUp(self):
        _clear_video_probe_caches()

    def tearDown(self):
        _clear_video_probe_caches()

    def test_probe_follows_replaced_bytes_on_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "unit.mp4")
            Path(dest).write_bytes(b"native-1344")
            with patch(
                "shared.utils.video_decode._resolve_media_binary",
                return_value="ffprobe",
            ), patch(
                "shared.utils.video_decode.subprocess.run",
                side_effect=_ffprobe_from_payload,
            ):
                first = probe_video_stream_metadata(dest)
                self.assertEqual(
                    (first["width"], first["height"]),
                    (1344, 768),
                )
                os.remove(dest)
                Path(dest).write_bytes(b"upscaled-2688-payload-larger-than-native")
                second = probe_video_stream_metadata(dest)
                self.assertEqual(
                    (second["width"], second["height"]),
                    (2688, 1536),
                )

    def test_get_video_info_follows_replaced_bytes_on_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "unit.mp4")
            Path(dest).write_bytes(b"native-1344")
            with patch(
                "shared.utils.video_decode._resolve_media_binary",
                return_value="ffprobe",
            ), patch(
                "shared.utils.video_decode.subprocess.run",
                side_effect=_ffprobe_from_payload,
            ):
                first = get_video_info(dest)
                self.assertEqual(first[1:3], (1344, 768))
                os.remove(dest)
                Path(dest).write_bytes(b"upscaled-2688-payload-larger-than-native")
                second = get_video_info(dest)
                self.assertEqual(second[1:3], (2688, 1536))


if __name__ == "__main__":
    unittest.main()
