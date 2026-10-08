"""Real CPU crop/composition geometry, source seals and audio-clock regression."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services import h3_face_refine as face
from services import h3_gallery_av_guide as av


def run(command):
    return subprocess.run(command, check=True, timeout=30, capture_output=True).stdout


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class FaceMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="face-cpu-")
        cls.root = Path(cls.storage.name)
        cls.source = cls.root / "source.mov"
        run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=s=96x64:r=24:d=1",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=1",
             "-filter_complex", "[2:a]adelay=125[a2]", "-map", "0:v", "-map", "1:a", "-map", "[a2]",
             "-c:v", "libx264", "-threads", "1", "-c:a", "pcm_s16le", str(cls.source)])
        cls.replacement = cls.root / "replacement.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=cyan:s=64x64:r=24:d=1",
             "-c:v", "ffv1", "-threads", "1", "-pix_fmt", "bgr0", str(cls.replacement)])

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.observations = dict(shots=[0, 12], boxes=[[16, 16, 48, 48] for _ in range(24)],
                                 canvas=[64, 64], padding=1, smoothing=3)
        self.observations["boxes"][5:8] = [None] * 3

    def prepare(self, **kwargs):
        return face.prepare_crops(self.source, self.observations, self.directory / "prepared", **kwargs)

    def rgb(self, path):
        facts = face._probe(path, None)
        return face._read_rgb(path, facts, None)

    def test_inverse_paste_preserves_outside_pixels_gaps_all_audio_and_clock(self):
        import numpy as np
        source_digest = face._file_digest(self.source)
        plan = self.prepare()
        receipt = face.compose_crops(self.source, self.replacement, plan, face._file_digest(self.replacement),
                                     self.directory / "composed")
        output = self.directory / "composed" / "composite.mkv"
        before, after, patch = self.rgb(self.source), self.rgb(output), self.rgb(self.replacement)
        self.assertEqual(before.shape, after.shape)
        for index, rectangle in enumerate(plan["track"]["rectangles"]):
            with self.subTest(frame=index):
                if rectangle is None:
                    np.testing.assert_array_equal(before[index], after[index])
                else:
                    x, y, width, height = rectangle
                    outside = np.ones(before.shape[1:3], dtype=bool)
                    outside[y:y+height, x:x+width] = False
                    np.testing.assert_array_equal(before[index][outside], after[index][outside])
                    np.testing.assert_array_equal(after[index, y:y+height, x:x+width],
                                                  np.broadcast_to(patch[0, 0, 0], (height, width, 3)))
        audio_before, audio_after = face._audio(self.source, None), face._audio(output, None)
        self.assertEqual(len(audio_before["streams"]), 2)
        face._check_audio(audio_before, audio_after)
        self.assertTrue(receipt["audio_packets_preserved"])
        self.assertFalse(receipt["native_h3_generation"])
        self.assertEqual(face._file_digest(self.source), source_digest)
        self.assertEqual(json.loads((self.directory / "composed" / "receipt.json").read_text()), receipt)

    def test_full_canvas_round_trip_is_lossless(self):
        import numpy as np
        self.observations.update(boxes=[[0, 0, 96, 64]] * 24, canvas=[96, 64])
        plan = self.prepare()
        crop = self.directory / "prepared" / "crops.mkv"
        np.testing.assert_array_equal(self.rgb(self.source), self.rgb(crop))
        face.compose_crops(self.source, crop, plan, plan["crops_sha256"], self.directory / "composed")
        np.testing.assert_array_equal(self.rgb(self.source), self.rgb(self.directory / "composed" / "composite.mkv"))

    def test_smoothing_stops_at_shot_and_dropout_boundaries(self):
        facts = {"width": 96, "height": 64, "frame_count": 6}
        observations = dict(shots=[0, 3], boxes=[[0, 0, 16, 16], [16, 0, 32, 16], None,
                            [64, 0, 80, 16], [48, 0, 64, 16], [32, 0, 48, 16]],
                            canvas=[64, 64], padding=1, smoothing=3)
        self.assertEqual(face.plan_crops(facts, observations)["rectangles"],
                         [[0, 0, 16, 16], [8, 0, 16, 16], None, [64, 0, 16, 16], [56, 0, 16, 16], [48, 0, 16, 16]])
        observations["shots"] = [0]
        observations["boxes"][3] = [64, 0, 80, 16]
        self.assertEqual(face.plan_crops(facts, observations)["rectangles"][3], [64, 0, 16, 16])

    def test_changed_source_plan_replacement_and_existing_destinations_fail_before_publication(self):
        plan = self.prepare()
        changed = self.directory / "changed.mov"
        changed.write_bytes(self.source.read_bytes() + b"changed tail")
        damaged = copy.deepcopy(plan)
        damaged["track"]["rectangles"][0][0] += 1
        for source, manifest, digest in ((changed, plan, face._file_digest(self.replacement)),
                                         (self.source, damaged, face._file_digest(self.replacement)),
                                         (self.source, plan, "sha256:" + "0" * 64)):
            with self.subTest(source=source.name), self.assertRaises(ValueError):
                face.compose_crops(source, self.replacement, manifest, digest, self.directory / "failed")
            self.assertFalse((self.directory / "failed").exists())
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(face._file_digest(self.directory / "prepared" / "crops.mkv"), plan["crops_sha256"])

    def test_non_24fps_replacement_clock_refused(self):
        wrong = self.directory / "wrong.mkv"
        run(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=red:s=64x64:r=25:d=1",
             "-c:v", "ffv1", "-threads", "1", str(wrong)])
        plan = self.prepare()
        with self.assertRaisesRegex(ValueError, "24-fps"):
            face.compose_crops(self.source, wrong, plan, face._file_digest(wrong), self.directory / "failed")
        self.assertFalse((self.directory / "failed").exists())

    def test_invalid_observations_and_pre_cancel_create_no_bundle(self):
        for changes in ({"shots": [0, 0]}, {"boxes": [[0, 0, 96, 64]]}, {"smoothing": True},
                        {"padding": float("nan")}, {"canvas": [63, 64]}, {"boxes": [None] * 24}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                face.prepare_crops(self.source, {**self.observations, **changes}, self.directory / "invalid")
            self.assertFalse((self.directory / "invalid").exists())
        with self.assertRaises(av.H3GalleryAVGuideCancelled):
            self.prepare(cancel_check=lambda: True)
        self.assertFalse((self.directory / "prepared").exists())

    def test_audio_receipt_rejects_packet_or_timing_drift(self):
        original = face._audio(self.source, None)
        for key, value in (("data_hash", "SHA256:changed"), ("pts_time", "2.0")):
            changed = copy.deepcopy(original)
            changed["packets"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                face._check_audio(original, changed)

    def test_prepare_rejects_combined_memory_overflow_before_ready_bundle(self):
        # Both images fit separately, but their round-trip does not.
        with patch.object(face, "MAX_RGB_BYTES", 600000):
            with self.assertRaisesRegex(ValueError, "composition memory"):
                self.prepare()
        self.assertFalse((self.directory / "prepared").exists())


if __name__ == "__main__":
    unittest.main()
