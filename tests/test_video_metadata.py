"""Embedded video settings keep finished outputs usable with image boundaries."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from shared.utils.video_metadata import save_metadata_to_mp4


class VideoMetadataTests(unittest.TestCase):
    def test_in_memory_boundary_image_does_not_break_final_mp4_metadata(self):
        fake_mp4 = mock.Mock()
        fake_mp4.tags = {}
        settings = {
            "prompt": "synthetic test",
            "image_start": [Image.new("RGB", (2, 2)), "reference.png"],
        }
        with mock.patch("mutagen.mp4.MP4", return_value=fake_mp4):
            self.assertTrue(save_metadata_to_mp4("final.mp4", settings))

        embedded = json.loads(fake_mp4.tags["©cmt"][0])
        self.assertEqual(embedded["image_start"], [None, "reference.png"])
        self.assertEqual(embedded["prompt"], "synthetic test")
        fake_mp4.save.assert_called_once_with()
        self.assertIsInstance(settings["image_start"][0], Image.Image)

    def test_unrelated_non_json_values_still_fail_closed(self):
        fake_mp4 = mock.Mock()
        fake_mp4.tags = {}
        with mock.patch("mutagen.mp4.MP4", return_value=fake_mp4):
            self.assertFalse(save_metadata_to_mp4("final.mp4", {"bad": object()}))
        fake_mp4.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
