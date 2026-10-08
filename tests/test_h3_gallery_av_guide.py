"""Real CPU media and immutable replay proof; no native model qualification."""
from dataclasses import asdict
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services import h3_gallery_av_guide as av


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "CPU FFmpeg required")
class GalleryAVGuideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = tempfile.TemporaryDirectory(prefix="h3-av-fixtures-")
        cls.root = Path(cls.storage.name)
        cls.red, cls.blue, cls.audio = (cls.root / name for name in ("red.mp4", "blue.mp4", "tone.wav"))
        for path, color in ((cls.red, "red"), (cls.blue, "blue")):
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                f"color=c={color}:s=96x64:r=12:d=0.5", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(path)], check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=1000:sample_rate=44100:duration=0.126", "-c:a", "pcm_s16le", str(cls.audio)],
            check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.bands = cls.root / "bands.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=black:s=96x64:r=24:d=0.25,drawbox=x=0:y=0:w=32:h=64:color=red:t=fill,"
            "drawbox=x=32:y=0:w=32:h=64:color=lime:t=fill,drawbox=x=64:y=0:w=32:h=64:color=blue:t=fill",
            "-c:v", "libx264", "-crf", "0", "-threads", "1", "-pix_fmt", "yuv420p", str(cls.bands)],
            check=True, timeout=20, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.probes = {path: av.probe_gallery_av(str(path), "audio" if path == cls.audio else "video")
                      for path in (cls.red, cls.blue, cls.audio, cls.bands)}

    @classmethod
    def tearDownClass(cls):
        cls.storage.cleanup()

    def record(self, path, frame_index=0):
        return dict(asdict(self.probes[path]), workspace="project", name=path.name, revision="r1",
                    frame_index=frame_index, source_private=True, source_explicit=True)

    def binding(self, records, target=22):
        plan = av.build_gallery_av_guide_plan(records, target)
        return av.make_gallery_av_guide_sources(records, plan), plan

    def decode(self, paths, records, **kwargs):
        binding, plan = self.binding(records)
        return av.decode_gallery_av_guide_sources([str(path) for path in paths], binding, plan,
                                                  height=32, width=64, **kwargs)

    def test_moov_at_end_normalization_and_actual_payload_consumption(self):
        import torch
        from models.minimax_h3.timeline_guides import encode_timeline_guides
        data = self.red.read_bytes()
        self.assertGreater(data.index(b"moov"), data.index(b"mdat"))
        self.assertEqual((self.probes[self.red].width, self.probes[self.red].height,
                          self.probes[self.red].frame_count, self.probes[self.red].sample_count), (96, 64, 12, 0))
        # Independent full FFmpeg conversion measures the 32 kHz sample count.
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(self.audio),
            "-ac", "2", "-ar", "32000", "-f", "f32le", "pipe:1"], timeout=20)
        self.assertEqual(self.probes[self.audio].sample_count, len(raw) // 8)
        records = [self.record(self.audio, 20), self.record(self.red, 2), self.record(self.blue, 0)]
        payload = self.decode([self.audio, self.red, self.blue], records)
        self.assertEqual([guide["resolved_frame_idx"] for guide in payload.plan["guides"]], [20, 2, 0])
        self.assertFalse(payload.plan["execution_available"])
        # 22 target frames round to 37 ticks; floor(37 - 20 * 5/3) is 3.
        self.assertEqual(payload.plan["guides"][0]["audio"]["capacity_tick_count"], 3)
        self.assertEqual(payload.media[0].waveform.shape, (2, len(raw) // 8))
        self.assertTrue(torch.equal(payload.media[0].waveform[0], payload.media[0].waveform[1]))
        red, blue = payload.media[1].visual, payload.media[2].visual
        self.assertEqual(tuple(red.shape), (3, 12, 32, 64))
        self.assertGreater(float(red[0].mean()), 0.9)
        self.assertLess(float(red[2].mean()), -0.9)
        self.assertGreater(float(blue[2].mean()), 0.9)
        calls = []
        def video_encoder(pixels, keep_all_latents):
            calls.append(tuple(pixels.shape))
            return torch.zeros(1, 24, 2, 2, 4)
        def audio_encoder(waveform):
            ticks = (waveform.shape[-1] + 799) // 800
            return torch.stack((torch.ones(32, ticks), torch.full((32, ticks), 2.0)))
        rows = encode_timeline_guides(payload, frame_num=22, height=32, width=64,
            patch_size=(1, 2, 2), seed=42, device=torch.device("cpu"), encode_video=video_encoder,
            encode_audio=audio_encoder, interrupted=lambda: False)
        self.assertEqual(calls, [(3, 5, 32, 64), (3, 5, 32, 64)])
        self.assertEqual(rows.condition_order, ((0, 3), (2, 0), (2, 0)))
        self.assertTrue(torch.equal(rows.audio[:3], torch.ones(3, 32)))
        self.assertTrue(torch.equal(rows.audio[3:], torch.full((3, 32), 2.0)))

    def test_cover_crop_keeps_square_pixels_and_centered_content(self):
        import torch
        binding, plan = self.binding([self.record(self.bands)])
        payload = av.decode_gallery_av_guide_sources([str(self.bands)], binding, plan, height=32, width=32)
        # A 96x64 three-band frame cropped to a square covers x=16..80:
        # the middle band occupies half the output rather than one third.
        labels = payload.media[0].visual[:, 0, 16].argmax(dim=0)
        self.assertEqual([int((labels == channel).sum()) for channel in range(3)], [8, 16, 8])
        self.assertTrue(torch.isfinite(payload.media[0].visual).all())

    def test_one_through_eight_preserve_unsorted_overlap_and_private_binding(self):
        for count in (1, 4, 8):
            paths = [self.red if index % 2 == 0 else self.blue for index in range(count)]
            records = [self.record(path, [2, 0, 1][index % 3]) for index, path in enumerate(paths)]
            binding, plan = self.binding(records)
            self.assertEqual([g["resolved_frame_idx"] for g in plan["guides"]], [r["frame_index"] for r in records])
            self.assertEqual(binding["sources"], records)
            self.assertNotIn(str(self.root), str(binding))
            payload = self.decode(paths, records)
            self.assertEqual(len(payload.media), count)
            for index, media in enumerate(payload.media):
                self.assertGreater(float(media.visual[0 if index % 2 == 0 else 2].mean()), 0.9)
        records = [self.record(self.red, 0), self.record(self.blue, 0)]
        payload = self.decode([self.red, self.blue], records)
        self.assertEqual(len(payload.media), 2)
        records[0]["revision"] = "changed"
        self.assertEqual(payload.plan["guides"][0]["visual"]["sha256"], self.probes[self.red].sha256)

    def test_invalid_bindings_and_aggregate_budgets_reject_before_open(self):
        binding, plan = self.binding([self.record(self.red)])
        malformed = [None, {**binding, "sources": None}, {**binding, "extra": "private"},
                     {**binding, "target_frames": True}, {**binding, "plan_sha256": "sha256:" + "0" * 64}]
        changed_interval = copy.deepcopy(binding)
        changed_interval["sources"][0]["frame_index"] = 1
        malformed.append(changed_interval)
        for value in malformed:
            with self.subTest(value=value), patch.object(av.os, "open", side_effect=AssertionError("opened")):
                with self.assertRaises(av.H3GalleryAVGuideError):
                    av.decode_gallery_av_guide_sources([str(self.red)], value, plan, height=32, width=64)
        for key, value in (("kind", []), ("frame_index", True), ("source_private", 1), ("name", "../red.mp4")):
            record = self.record(self.red)
            record[key] = value
            with self.subTest(key=key), self.assertRaises(av.H3GalleryAVGuideError):
                self.binding([record])
        with patch.object(av, "MAX_TOTAL_ENCODED_BYTES", self.probes[self.red].size), self.assertRaises(av.H3GalleryAVGuideError):
            self.binding([self.record(self.red), self.record(self.red)])
        with patch.object(av, "MAX_DECODED_BYTES", 9 * 1024**2), patch.object(av.os, "open", side_effect=AssertionError("opened")):
            with self.assertRaises(av.H3GalleryAVGuideError):
                av.decode_gallery_av_guide_sources([str(self.red)], binding, plan, height=256, width=256)

    def test_changed_bytes_and_committed_normalized_counts_block_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "red.mp4"
            shutil.copyfile(self.red, path)
            record = self.record(self.red)
            path.write_bytes(self.blue.read_bytes())
            with self.assertRaisesRegex(av.H3GalleryAVGuideError, "bytes changed"):
                self.decode([path], [record])
        for key in ("frame_count", "width"):
            record = self.record(self.red)
            record[key] += 1
            with self.subTest(key=key), self.assertRaisesRegex(av.H3GalleryAVGuideError, "normalized counts changed"):
                self.decode([self.red], [record])

    def test_original_replaced_after_snapshot_decoder_reads_only_sealed_copy(self):
        import torch
        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "red.mp4"
            shutil.copyfile(self.red, path)
            seen = []
            def spawn(command, **kwargs):
                if Path(command[0]).name == "ffmpeg":
                    snapshot = Path(command[command.index("-i") + 1])
                    self.assertNotEqual(snapshot, path)
                    seen.append(snapshot)
                    path.write_bytes(self.blue.read_bytes())
                return real_popen(command, **kwargs)
            with patch.object(av.subprocess, "Popen", side_effect=spawn):
                payload = self.decode([path], [self.record(self.red)])
            self.assertGreater(float(payload.media[0].visual[0].mean()), 0.9)
            self.assertLess(float(payload.media[0].visual[2].mean()), -0.9)
            self.assertTrue(seen)
            self.assertTrue(all(not snapshot.exists() for snapshot in seen))
            self.assertEqual(path.read_bytes(), self.blue.read_bytes())
            with self.assertRaises(av.H3GalleryAVGuideError):
                self.decode([path], [self.record(self.red)])

    def test_same_inode_mutation_restoring_mtime_cannot_pass_snapshot(self):
        real_read = os.read
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "red.mp4"
            shutil.copyfile(self.red, path)
            before = path.stat()
            changed = False
            def read(descriptor, count):
                nonlocal changed
                result = real_read(descriptor, count)
                if result and not changed:
                    changed = True
                    with path.open("r+b") as stream:
                        stream.write(b"X")
                    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                return result
            with patch.object(av.os, "read", side_effect=read), self.assertRaisesRegex(av.H3GalleryAVGuideError, "changed while copying"):
                av.probe_gallery_av(str(path), "video")
            self.assertEqual(path.stat().st_size, before.st_size)
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(path.read_bytes()[:1], b"X")

    def test_cancel_and_timeout_reap_owned_children_before_snapshot_cleanup(self):
        real_popen = subprocess.Popen
        for cancel in (True, False):
            children, snapshots = [], []
            def spawn(command, **kwargs):
                snapshots.append(Path(command[command.index("-i") + 1]))
                child = real_popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], **kwargs)
                children.append(child)
                return child
            with self.subTest(cancel=cancel), patch.object(av.subprocess, "Popen", side_effect=spawn), patch.object(av, "CHILD_SECONDS", 0.1):
                error = av.H3GalleryAVGuideCancelled if cancel else av.H3GalleryAVGuideError
                with self.assertRaises(error):
                    av.probe_gallery_av(str(self.red), "video", cancel_check=lambda: cancel and bool(children))
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode)
            self.assertTrue(all(not path.exists() for path in snapshots))
            self.assertFalse(any(thread.name == "h3-av-pipe" for thread in threading.enumerate()))

    def test_real_ffmpeg_cancellation_and_full_conversion_limit(self):
        real_popen = subprocess.Popen
        children = []
        def spawn(command, **kwargs):
            child = real_popen(command, **kwargs)
            if Path(command[0]).name == "ffmpeg":
                children.append(child)
            return child
        with patch.object(av.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(av.H3GalleryAVGuideCancelled):
                av.probe_gallery_av(str(self.red), "video", cancel_check=lambda: bool(children))
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        with patch.object(av, "MAX_FRAMES", 5), self.assertRaisesRegex(av.H3GalleryAVGuideError, "exceeds its limit"):
            av.probe_gallery_av(str(self.red), "video")
        with patch.object(av, "MAX_SAMPLES", 100), self.assertRaisesRegex(av.H3GalleryAVGuideError, "exceeds its limit"):
            av.probe_gallery_av(str(self.audio), "audio")

    def test_cancellation_after_pipe_eof_reaps_decoder_instead_of_succeeding(self):
        real_popen = subprocess.Popen
        cancelled = threading.Event()
        children = []

        def spawn(command, **kwargs):
            child = real_popen(command, **kwargs)
            children.append(child)
            real_wait = child.wait

            def cancel_during_exit_wait(*args, **wait_kwargs):
                cancelled.set()
                return real_wait(*args, **wait_kwargs)

            child.wait = cancel_during_exit_wait
            return child

        with patch.object(av.subprocess, "Popen", side_effect=spawn):
            with self.assertRaises(av.H3GalleryAVGuideCancelled):
                av._stream(
                    [sys.executable, "-B", "-c", "import os,time; os.close(1); time.sleep(0.7)"],
                    64, lambda chunk: None, cancelled.is_set,
                )
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        self.assertFalse(any(thread.name == "h3-av-pipe" for thread in threading.enumerate()))

    def test_links_fifo_and_wrong_encoded_type_fail_closed_without_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "source.mp4"
            shutil.copyfile(self.red, regular)
            linked = root / "linked.mp4"
            linked.symlink_to(regular)
            hard = root / "hard.mp4"
            os.link(regular, hard)
            fifo = root / "pipe.mp4"
            os.mkfifo(fifo)
            for path in (linked, hard):
                with self.subTest(path=path.name), self.assertRaises(av.H3GalleryAVGuideError):
                    av.probe_gallery_av(str(path), "video")
            # A regression to blocking open must fail this test promptly rather
            # than hanging the test runner on a FIFO with no writer.
            code = ("import sys; from services.h3_gallery_av_guide import probe_gallery_av, H3GalleryAVGuideError\n"
                    "try: probe_gallery_av(sys.argv[1], 'video')\n"
                    "except H3GalleryAVGuideError: pass\n"
                    "else: raise AssertionError('FIFO accepted')\n")
            subprocess.run([sys.executable, "-B", "-c", code, str(fifo)], check=True, timeout=3,
                           env={**os.environ, "PYTHONPATH": str(ROOT / "app")},
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            wrong = root / "not-video.mp4"
            wrong.write_bytes(self.audio.read_bytes())
            with self.assertRaises(av.H3GalleryAVGuideError):
                av.probe_gallery_av(str(wrong), "video")
            self.assertEqual(regular.read_bytes(), self.red.read_bytes())
            self.assertTrue(linked.is_symlink())
            self.assertTrue(fifo.exists())


if __name__ == "__main__":
    unittest.main()
