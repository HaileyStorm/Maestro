"""Real CPU media checks for a non-destructive, frame-aligned Editor cut."""

from __future__ import annotations

import hashlib
import array
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.editor_export import render_single_source_cut, render_video_sequence  # noqa: E402


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required")
class EditorExportMediaTests(unittest.TestCase):
    def test_fractional_single_and_repeated_clip_encode_exact_frames_and_silence(self):
        source = self.root / "fractional.mp4"
        self.run_media([FFMPEG, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "color=blue:s=128x72:r=3", "-frames:v", "2", "-an", "-c:v", "libx264",
            "-threads", "2", str(source)])
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        clip = {"path": str(source), "source_in": 0, "duration": 2 / 3, "has_audio": False}
        for count in (1, 2):
            with self.subTest(clips=count):
                output = self.root / f"fractional-{count}.mp4"
                render_video_sequence([dict(clip) for _ in range(count)], output,
                    width=128, height=72, fps=3.75, timeout=30)
                probe = json.loads(self.run_media([FFPROBE, "-v", "error", "-show_entries",
                    "stream=codec_type,codec_name,sample_rate,avg_frame_rate,nb_frames:format=duration", "-of", "json", str(output)]))
                video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
                audio = next(stream for stream in probe["streams"] if stream["codec_type"] == "audio")
                self.assertEqual((int(video["nb_frames"]), video["avg_frame_rate"]), (3 * count, "15/4"))
                self.assertEqual((audio["codec_name"], audio["sample_rate"]), ("aac", "48000"))
                self.assertAlmostEqual(float(probe["format"]["duration"]), 0.8 * count, delta=0.025)
                pixels = self.run_media([FFMPEG, "-v", "error", "-i", str(output), "-map", "0:v:0", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
                self.assertEqual(len(pixels), 3 * count * 128 * 72 * 3)
                self.assertGreater(pixels[2], 200)
                samples = array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(output), "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
                self.assertLess(max(abs(value) for value in samples), 1e-6)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
        self.assertFalse(list(self.root.glob(".editor-sequence-*")))

    def test_shared_immutable_source_two_ranges_keep_frame_order_audio_and_rounded_duration(self):
        from services.editor_projects import create_output_video_timeline, append_output_video_clip, apply_output_video_trim, editor_sequence_clips
        source = self.root / "immutable.mp4"
        self.run_media([FFMPEG, "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "color=red:s=128x72:r=24:d=2,drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,1)'",
            "-f", "lavfi", "-i", "aevalsrc=0.1*sin(2*PI*if(lt(t\\,1)\\,440\\,880)*t):s=48000:d=2",
            "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", "-c:a", "aac", str(source)])
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        media = {"type": "video", "duration": 2, "width": 128, "height": 72, "fps": 24, "has_audio": True}
        first = create_output_video_timeline(workspace="scene", output_name=source.name, output_revision=original_hash, media=media)
        current = append_output_video_clip(first, output_name=source.name, output_revision=original_hash, media=media)
        proposed = json.loads(json.dumps(current))
        proposed["tracks"][0]["items"][0].update(source_in=0.25, duration=0.55)
        proposed["tracks"][0]["items"][1].update(source_in=1.25, duration=0.55)
        proposed["tracks"][0]["items"].reverse()
        project = apply_output_video_trim(current, proposed)
        self.assertEqual(len(project["assets"]), 1)
        clips = [{"path": str(source), "source_in": clip["source_in"], "duration": clip["duration"], "has_audio": asset["has_audio"]}
                 for asset, clip in editor_sequence_clips(project)]
        output = self.root / "shared-sequence.mp4"
        render_video_sequence(clips, output, width=128, height=72, fps=24, timeout=30)
        probe = json.loads(self.run_media([FFPROBE, "-v", "error", "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate,nb_frames:format=duration", "-of", "json", str(output)]))
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"], video["avg_frame_rate"], int(video["nb_frames"])), (128, 72, "24/1", 26))
        self.assertAlmostEqual(float(probe["format"]["duration"]), 26 / 24, delta=0.025)
        self.assertEqual(sum(stream["codec_type"] == "audio" for stream in probe["streams"]), 1)
        pixels = self.run_media([FFMPEG, "-v", "error", "-i", str(output), "-map", "0:v:0", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        stride = 128 * 72 * 3
        self.assertEqual(len(pixels), 26 * stride)
        for index in range(26):
            red, _, blue = pixels[index * stride + (36 * 128 + 64) * 3:index * stride + (36 * 128 + 64) * 3 + 3]
            self.assertGreater(blue if index < 13 else red, 200)
            self.assertLess(red if index < 13 else blue, 30)
        samples = array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(output), "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
        for start, frequency in ((0.1, 880), (0.7, 440)):
            chunk = samples[int(start * 48000):int((start + 0.2) * 48000)]
            crossings = sum(left <= 0 < right for left, right in zip(chunk, chunk[1:]))
            self.assertAlmostEqual(crossings / 0.2, frequency, delta=15)
            self.assertGreater(max(abs(value) for value in chunk), 0.05)
        self.run_media([FFMPEG, "-v", "error", "-i", str(output), "-f", "null", "-"])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
        self.assertFalse(list(self.root.glob(".editor-sequence-*")))

    def test_image_alpha_interval_geometry_titles_audio_and_sequence_join(self):
        from PIL import Image
        from services.editor_export import _image_filters
        source = self.make_source()
        image = self.root / "overlay.png"
        raster = Image.new("RGBA", (16, 8), (255, 0, 0, 255))
        raster.putpixel((0, 0), (0, 0, 0, 0)); raster.save(image)
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, image)]
        plan = {"path": str(image), "width": 16, "height": 8, "start": 0.25, "duration": 0.5,
                "size": 0.5, "opacity": 0.5, "position": "center"}
        canvas = {"width": 128, "height": 72, "fps": 24}
        def frame(path, at):
            return self.run_media([FFMPEG, "-v", "error", "-ss", str(at), "-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        def center(raw): return tuple(raw[(36 * 128 + 64) * 3:(36 * 128 + 64) * 3 + 3])
        original = self.root / "original.mp4"
        render_single_source_cut(source, original, source_in=0, duration=1, timeout=30)
        destination = self.root / "overlay.mp4"
        render_single_source_cut(source, destination, source_in=0, duration=1, image_layer=plan, canvas=canvas, timeout=30)
        self.assertLess(max(abs(a-b) for a,b in zip(center(frame(destination, 0.125)), center(frame(original, 0.125)))), 8)
        self.assertLess(max(abs(a-b) for a,b in zip(center(frame(destination, 0.75)), center(frame(original, 0.75)))), 8)
        inside = center(frame(destination, 0.5)); outside = center(frame(original, 0.5))
        self.assertGreater(inside[0], outside[0] + 80)
        self.assertEqual(len(self.probe(destination)["streams"]), len(self.probe(original)["streams"]))
        for stream in range(len(self.probe(original)["streams"]) - 1):
            def sound(path): return self.run_media([FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:a:{stream}", "-f", "s16le", "-"])
            self.assertEqual(sound(destination), sound(original))
        for pos in ("top", "center", "bottom"):
            _image_filters({**plan, "position": pos}, self.root, width=128, height=72, duration=1, fps=24, first_input=1, base="v")
            with Image.open(self.root / "image-layer-0.png") as decoded:
                self.assertEqual(decoded.size, (58, 29)); self.assertEqual(decoded.getpixel((30, 15))[3], 128)
                self.assertLess(decoded.getpixel((0, 0))[3], 128)
        sequence = self.root / "image-sequence.mp4"
        render_video_sequence([{"path": str(source), "source_in": 0, "duration": 0.5, "has_audio": True},
            {"path": str(source), "source_in": 1, "duration": 0.5, "has_audio": True}], sequence, width=128, height=72, fps=24,
            image_layer=plan, text_layers=[{"id": "title", "text": "IMAGE", "start": 0.25, "duration": 0.5, "position": "center"}], timeout=30)
        self.assertEqual(int(self.run_media([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(sequence)]).strip()), 24)
        self.assertNotEqual(center(frame(sequence, 0.5)), inside)
        self.assertEqual(hashes, [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, image)])
        with self.assertRaises(InterruptedError):
            render_single_source_cut(source, self.root / "cancel-image.mp4", source_in=0, duration=1, image_layer=plan, canvas=canvas, abort_check=lambda: True, timeout=30)
        self.assertFalse((self.root / "cancel-image.mp4").exists())
        self.assertFalse(list(self.root.glob('.editor-titles-*')))

    def test_ordered_overlapping_images_keep_alpha_intervals_and_audio(self):
        from PIL import Image
        source = self.make_source()
        plans = []
        for index, color in enumerate(((255, 0, 0, 255), (0, 255, 0, 128))):
            path = self.root / f"still-{index}.png"
            Image.new("RGBA", (16, 8), color).save(path)
            plans.append({"path": str(path), "width": 16, "height": 8, "start": index * 0.25,
                "duration": 0.5, "size": 0.5, "opacity": 1, "position": "center"})
        original = self.root / "original.mp4"
        render_single_source_cut(source, original, source_in=0, duration=1, timeout=30)
        output = self.root / "overlapping.mp4"
        render_single_source_cut(source, output, source_in=0, duration=1, image_layer=plans,
            canvas={"width":128,"height":72,"fps":24}, timeout=30)
        def center(at):
            raw = self.run_media([FFMPEG,"-v","error","-ss",str(at),"-i",str(output),"-frames:v","1","-pix_fmt","rgb24","-f","rawvideo","-"])
            return tuple(raw[(36*128+64)*3:(36*128+64)*3+3])
        red, overlap, green = center(0.125), center(0.375), center(0.625)
        self.assertGreater(red[0], 220); self.assertLess(red[1], 20)
        self.assertGreater(overlap[0], 100); self.assertGreater(overlap[1], 100)
        self.assertLess(overlap[0], 160); self.assertLess(overlap[1], 160)
        self.assertGreater(green[1], green[0] + 90)
        for stream in range(len(self.probe(original)["streams"]) - 1):
            def sound(path): return self.run_media([FFMPEG,"-v","error","-i",str(path),"-map",f"0:a:{stream}","-f","s16le","-"])
            self.assertEqual(sound(output), sound(original))

    def test_multiple_images_titles_and_audio_bed_across_sequence_join(self):
        from PIL import Image
        source = self.make_source()
        plans = []
        for index, color in enumerate(("red", "green")):
            path = self.root / f"joined-{index}.png"
            Image.new("RGBA", (16, 8), color).save(path)
            plans.append({"path": str(path), "width":16, "height":8, "start":0.25, "duration":0.5,
                "size":0.5, "opacity":1, "position":"center"})
        bed = self.root / "join-bed.wav"
        self.run_media([FFMPEG,"-v","error","-f","lavfi","-i","sine=frequency=960:sample_rate=48000:duration=1",str(bed)])
        clips = [{"path":str(source),"source_in":0,"duration":0.5,"has_audio":True},
                 {"path":str(source),"source_in":1,"duration":0.5,"has_audio":True}]
        output = self.root / "all-layers.mp4"
        render_video_sequence(clips, output, width=128,height=72,fps=24,image_layer=plans,
            text_layers=[{"id":"title","text":"TOP","start":0.25,"duration":0.5,"position":"center"}],
            audio_layer={"path":str(bed),"source_in":0,"start":0,"duration":1,"volume":0.5,"muted":False},timeout=30)
        streams = self.probe(output)["streams"]
        self.assertEqual([item["codec_type"] for item in streams], ["video", "audio"])
        self.assertAlmostEqual(float(streams[0]["duration"]), 1, delta=0.001)
        for at in (0.375,0.625):
            raw = self.run_media([FFMPEG,"-v","error","-ss",str(at),"-i",str(output),"-frames:v","1","-pix_fmt","rgb24","-f","rawvideo","-"])
            pixels = [tuple(raw[i:i+3]) for y in range(31,42) for x in range(57,71) for i in [(y*128+x)*3]]
            self.assertTrue(any(min(pixel) > 170 for pixel in pixels), "Title must remain above both overlapping stills")
        import array
        pcm = array.array("h",self.run_media([FFMPEG,"-v","error","-i",str(output),"-map","0:a:0","-ac","1","-ar","48000","-f","s16le","-"]))
        real = sum(v/32768*math.cos(2*math.pi*960*i/48000) for i,v in enumerate(pcm))
        imag = sum(v/32768*math.sin(2*math.pi*960*i/48000) for i,v in enumerate(pcm))
        self.assertGreater(2*math.hypot(real,imag)/len(pcm),0.04)

    def test_audio_layer_timing_trim_gain_mute_silence_sequence_and_all_streams(self):
        source = self.make_source()
        bed = self.root / "bed.wav"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=880:duration=1:sample_rate=48000",
            "-f", "lavfi", "-i", "sine=frequency=960:duration=1:sample_rate=48000", "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]",
            "-map", "[a]", "-c:a", "pcm_s16le", str(bed)])
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, bed)]
        plan = {"path": str(bed), "source_in": 1.25, "duration": 0.5, "start": 0.5, "volume": 1.0, "muted": False}
        def samples(path, index=0):
            return array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:a:{index}", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
        def amplitude(values, frequency, start=0.65, end=0.85):
            part = values[round(start * 48000):round(end * 48000)]
            real = sum(value * math.cos(2 * math.pi * frequency * index / 48000) for index, value in enumerate(part))
            imag = sum(value * math.sin(2 * math.pi * frequency * index / 48000) for index, value in enumerate(part))
            return 2 * math.hypot(real, imag) / len(part)
        amplitudes = []
        baseline = self.root / "baseline.mp4"
        render_single_source_cut(source, baseline, source_in=0.25, duration=1.5, timeout=30)
        for index, (gain, muted) in enumerate(((1, False), (0.5, False), (0, False), (1, True))):
            output = self.root / f"mixed-{index}.mp4"
            render_single_source_cut(source, output, source_in=0.25, duration=1.5, audio_layer={**plan, "volume": gain, "muted": muted}, timeout=30)
            media = self.probe(output)
            self.assertEqual([item["codec_type"] for item in media["streams"]], ["video", "audio", "audio"])
            self.assertAlmostEqual(float(media["streams"][0]["duration"]), 1.5, delta=0.001)
            for stream, source_frequency in enumerate((440, 660)):
                values = samples(output, stream)
                base = samples(baseline, stream)
                self.assertAlmostEqual(amplitude(values, source_frequency) / amplitude(base, source_frequency), 1, delta=0.05)
                self.assertLess(amplitude(values, 960, 0.1, 0.3), 0.002)
                self.assertLess(amplitude(values, 960, 1.15, 1.35), 0.002)
                self.assertLess(amplitude(values, 880), 0.002)  # trim chose the second tone
                if stream == 0: amplitudes.append(amplitude(values, 960))
                if gain == 0 or muted: self.assertEqual(values, base)
        self.assertGreater(amplitudes[0], 0.1)
        self.assertAlmostEqual(amplitudes[1] / amplitudes[0], 0.5, delta=0.05)
        self.assertLess(max(amplitudes[2:]), 0.002)
        for kind in ("silent", "sequence"):
            output = self.root / f"{kind}.mp4"
            if kind == "silent":
                silent = self.root / "silent-source.mp4"
                self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=128x72:r=24:d=1.5", "-c:v", "libx264", "-threads", "2", str(silent)])
                render_single_source_cut(silent, output, source_in=0, duration=1.5, audio_layer=plan, timeout=30)
            else:
                render_video_sequence([{"path": source, "source_in": 0, "duration": 0.75, "has_audio": True}] * 2,
                    output, width=128, height=72, fps=24, audio_layer=plan,
                    text_layers=[{"id": "title", "text": "Across join", "start": 0.5, "duration": 0.5, "position": "top"}], timeout=30)
            self.assertEqual(len(self.probe(output)["streams"]), 2)
            self.assertGreater(amplitude(samples(output), 960), 0.03)
            self.assertAlmostEqual(float(self.probe(output)["streams"][0]["duration"]), 1.5, delta=0.001)
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, bed)], hashes)
        self.assertFalse(list(self.root.glob(".editor-cut-*")))

    def test_audio_fades_follow_trim_before_delay_and_preserve_original_sound(self):
        source = self.make_source()
        bed = self.root / "fades.wav"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=960:duration=3:sample_rate=48000", "-c:a", "pcm_s16le", str(bed)])
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, bed)]
        plan = {"path": str(bed), "source_in": 0.75, "duration": 1, "start": 0.25, "volume": 0.5, "muted": False}
        def samples(path, index=0):
            return array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:a:{index}",
                "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
        def amplitude(values, frequency, start, end):
            part = values[round(start * 48000):round(end * 48000)]
            real = sum(value * math.cos(2 * math.pi * frequency * index / 48000) for index, value in enumerate(part))
            imag = sum(value * math.sin(2 * math.pi * frequency * index / 48000) for index, value in enumerate(part))
            return 2 * math.hypot(real, imag) / len(part)
        baseline = self.root / "no-fades.mp4"
        render_single_source_cut(source, baseline, source_in=0, duration=1.5, audio_layer=plan, timeout=30)
        explicit_zero = self.root / "zero-fades.mp4"
        render_single_source_cut(source, explicit_zero, source_in=0, duration=1.5,
            audio_layer={**plan, "fade_in": 0, "fade_out": 0}, timeout=30)
        self.assertEqual(samples(baseline), samples(explicit_zero))
        for kind in ("cut", "sequence", "overlap"):
            output = self.root / f"fades-{kind}.mp4"
            length = 1 if kind == "overlap" else 0.25
            layer = {**plan, "fade_in": length, "fade_out": length}
            if kind == "sequence":
                render_video_sequence([{"path": source, "source_in": 0, "duration": 0.75, "has_audio": True}] * 2,
                    output, width=128, height=72, fps=24, audio_layer=layer, timeout=30)
            else:
                render_single_source_cut(source, output, source_in=0, duration=1.5, audio_layer=layer, timeout=30)
            # Windows are inside the audio's delayed interval and span whole tone cycles.
            for stream, original in enumerate((440, 660) if kind != "sequence" else (440,)):
                values = samples(output, stream)
                base = samples(baseline, stream)
                for start, end in ((0.27, 0.32), (0.60, 0.65), (1.18, 1.23)):
                    center = (start + end) / 2 - plan["start"]
                    expected = min(1, center / length) * min(1, (plan["duration"] - center) / length)
                    actual = amplitude(values, 960, start, end) / amplitude(base, 960, start, end)
                    self.assertAlmostEqual(actual, expected, delta=0.04)
                    self.assertAlmostEqual(amplitude(values, original, start, end) / amplitude(base, original, start, end), 1, delta=0.05)
                self.assertLess(amplitude(values, 960, 0.05, 0.15), 0.002)
                self.assertLess(amplitude(values, 960, 1.35, 1.45), 0.002)
            self.assertAlmostEqual(float(self.probe(output)["streams"][0]["duration"]), 1.5, delta=0.001)
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, bed)], hashes)

    def test_audio_fades_reject_invalid_export_plan_even_when_muted(self):
        from services.editor_export import _audio_mix_filters
        source = self.make_source()
        layer = {"path": str(source), "start": 0, "source_in": 0, "duration": 1, "volume": 0, "muted": True}
        for key in ("fade_in", "fade_out"):
            for value in (-0.01, 1.01, float("nan"), float("inf"), True, "0.2", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    _audio_mix_filters({**layer, key: value}, duration=1.5, first_input=1, bases=[], layouts=[])
        # Saved values can carry precision beyond the FFmpeg duration string.
        # Validate the raw contract, then quantize the fades on the sample clock.
        precise = {**layer, "duration": 1.0000000004, "fade_in": 1.0000000002,
                   "fade_out": 1.0000000004, "volume": 0.5, "muted": False}
        _, filters, _ = _audio_mix_filters(precise, duration=1.5, first_input=1, bases=[], layouts=[])
        self.assertTrue(any("afade=t=in:ss=0:ns=48000" in part and "afade=t=out:ss=0:ns=48000" in part for part in filters))

    def test_audio_mix_keeps_multichannel_layout(self):
        source = self.root / "surround.mkv"
        bed = self.root / "mono.wav"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=128x72:r=24:d=0.5",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=5.1", "-t", "0.5", "-c:v", "libx264", "-threads", "2", "-c:a", "pcm_s16le", str(source)])
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=960:sample_rate=48000:duration=0.5", str(bed)])
        output = self.root / "surround.mp4"
        render_single_source_cut(source, output, source_in=0, duration=0.5,
            audio_layer={"path": str(bed), "source_in": 0, "duration": 0.5, "start": 0, "volume": 0.5, "muted": False}, timeout=30)
        streams = json.loads(self.run_media([FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries", "stream=channels,channel_layout", "-of", "json", str(output)]))["streams"]
        self.assertEqual(streams, [{"channels": 6, "channel_layout": "5.1"}])

    def test_title_positions_stacking_and_narrow_canvas_fit(self):
        from services.editor_export import _title_filters
        from PIL import Image
        _title_filters([{"id": "long", "text": "W" * 160, "start": 0, "duration": 1, "position": "top"}],
            self.root, width=64, height=64, duration=1, fps=24, first_input=1, base="cut")
        with Image.open(self.root / "title-0.png") as raster:
            self.assertLessEqual(raster.width, 64 * 0.9)
        source = self.root / "positions.mkv"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=128x72:r=24:d=1",
            "-c:v", "libx264", "-threads", "2", str(source)])
        first = {"id": "wide", "text": "MMMMMMMM", "start": 0, "duration": 1, "position": "top"}
        second = {**first, "id": "narrow", "text": "IIIIIIII"}
        pixels = []
        for index, layers in enumerate(([first, second], [second, first], [{**first, "position": "bottom"}])):
            destination = self.root / f"position-{index}.mp4"
            render_single_source_cut(source, destination, source_in=0, duration=1, text_layers=layers,
                canvas={"width": 128, "height": 72, "fps": 24}, timeout=30)
            frame = self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-frames:v", "1",
                "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
            pixels.append(frame)
            rows = [row for row in range(72) if max(frame[row * 384:(row + 1) * 384]) > 80]
            self.assertTrue(rows)
            self.assertLess(max(rows), 20) if index < 2 else self.assertGreater(min(rows), 50)
        self.assertNotEqual(pixels[0], pixels[1])

    def test_blank_title_still_fits_odd_source_to_saved_canvas(self):
        source = self.root / "odd.mkv"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=127x71:rate=24:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-map", "0:v", "-map", "1:a",
            "-c:v", "ffv1", "-threads", "2", "-c:a", "pcm_s16le", str(source)])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.root / "blank.mp4"
        render_single_source_cut(source, destination, source_in=0, duration=1,
            text_layers=[{"id": "blank", "text": "", "start": 0, "duration": 1, "position": "top"}],
            canvas={"width": 128, "height": 72, "fps": 24}, timeout=30)
        streams = json.loads(self.run_media([FFPROBE, "-v", "error", "-show_streams", "-of", "json", str(destination)]))["streams"]
        self.assertEqual((streams[0]["width"], streams[0]["height"]), (128, 72))
        self.assertEqual([stream["codec_type"] for stream in streams], ["video", "audio"])
        self.assertAlmostEqual(float(streams[0]["duration"]), 1, delta=0.001)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob(".editor-titles-*")))

    def test_literal_titles_have_half_open_times_across_a_join_and_keep_all_cut_audio(self):
        source = self.root / "black.mkv"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=black:s=128x72:r=24:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-f", "lavfi", "-i", "sine=frequency=660:duration=3",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(source)])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        layer = {"id": "literal", "text": "[v]; 'hello' %{text}", "start": 0.75, "duration": 0.5, "position": "center"}
        for sequence in (False, True):
            destination = self.root / f"titles-{sequence}.mp4"
            if sequence:
                render_video_sequence([{"path": source, "source_in": 0, "duration": 1, "has_audio": True}] * 2,
                    destination, width=128, height=72, fps=24, text_layers=[layer], timeout=30)
            else:
                render_single_source_cut(source, destination, source_in=0.5, duration=2,
                    text_layers=[layer], canvas={"width": 128, "height": 72}, timeout=30)
            streams = self.probe(destination)["streams"]
            self.assertEqual(len([item for item in streams if item["codec_type"] == "audio"]), 1 if sequence else 2)
            self.assertAlmostEqual(float(streams[0]["duration"]), 2, delta=0.001)
            frames = self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-map", "0:v", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
            stride = 128 * 72 * 3
            self.assertEqual(len(frames), stride * 48)
            for frame in (0, 17, 30, 47):
                self.assertLess(max(frames[frame * stride:(frame + 1) * stride]), 12)
            for frame in (18, 23, 24, 29):
                self.assertGreater(max(frames[frame * stride:(frame + 1) * stride]), 150)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob(".editor-titles-*")))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_media(self, command):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-600:])
        return result.stdout

    def make_source(self, *, audio_tracks=2):
        source = self.root / "original.mkv"
        command = [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=24:duration=3",
        ]
        for frequency in (440, 660)[:audio_tracks]:
            command += ["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=3"]
        command += ["-map", "0:v:0"]
        for index in range(audio_tracks):
            command += ["-map", f"{index + 1}:a:0"]
        command += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source)]
        self.run_media(command)
        return source

    def probe(self, path):
        return json.loads(self.run_media([
            FFPROBE, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,start_time,duration",
            "-of", "json", str(path),
        ]))

    def test_cut_reencodes_video_and_both_audio_tracks_from_zero(self):
        source = self.make_source()
        original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.root / "cut.mp4"
        result = render_single_source_cut(
            source, destination, source_in=0.5, duration=1.3, timeout=30,
        )
        self.assertEqual(result, str(destination))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_digest)
        media = self.probe(destination)
        streams = media["streams"]
        self.assertEqual([(item["codec_type"], item["codec_name"]) for item in streams], [
            ("video", "h264"), ("audio", "aac"), ("audio", "aac"),
        ])
        for stream in streams:
            self.assertAlmostEqual(float(stream["start_time"]), 0.0, delta=0.025)
        self.assertAlmostEqual(float(streams[0]["duration"]), 1.3, delta=1 / 24 + 0.001)
        for stream in streams[1:]:
            self.assertAlmostEqual(float(stream["duration"]), 1.3, delta=0.035)

    def test_video_without_audio_keeps_a_playable_cut(self):
        source = self.make_source(audio_tracks=0)
        destination = self.root / "silent.mp4"
        render_single_source_cut(source, destination, source_in=1.0, duration=0.5, timeout=30)
        self.assertEqual([stream["codec_type"] for stream in self.probe(destination)["streams"]], ["video"])

    def test_single_cut_fits_saved_canvas_without_overlays_and_keeps_audio(self):
        source = self.make_source()
        bed = self.root / "bed.wav"
        self.run_media([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=960:duration=1", str(bed)])
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, bed)]
        canvas = {"width": 128, "height": 128, "fps": 30}
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                options = {"audio_layer": {"path": str(bed), "source_in": 0, "duration": 1,
                            "start": 0, "volume": 0.5, "muted": False}} if mixed else {}
                baseline = self.root / f"baseline-{mixed}.mp4"
                fitted = self.root / f"fit-{mixed}.mp4"
                render_single_source_cut(source, baseline, source_in=0.5, duration=1, timeout=30, **options)
                render_single_source_cut(source, fitted, source_in=0.5, duration=1, canvas=canvas, timeout=30, **options)
                streams = json.loads(self.run_media([FFPROBE, "-v", "error", "-show_entries",
                    "stream=codec_type,width,height,avg_frame_rate,nb_frames", "-of", "json", str(fitted)]))["streams"]
                self.assertEqual((streams[0]["width"], streams[0]["height"], streams[0]["avg_frame_rate"], streams[0]["nb_frames"]), (128, 128, "24/1", "24"))
                self.assertEqual([stream["codec_type"] for stream in streams], ["video", "audio", "audio"])
                for index in (0, 1):
                    def sound(path):
                        return self.run_media([FFMPEG, "-v", "error", "-i", str(path), "-map", f"0:a:{index}", "-f", "s16le", "-"])
                    actual, expected = sound(fitted), sound(baseline)
                    if mixed:
                        # AAC encoder/filter timing need not be byte-identical.
                        # Both the original stream and bed must stay audible.
                        values = array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(fitted),
                            "-map", f"0:a:{index}", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
                        part = values[14400:33600]
                        for frequency in ((440, 660)[index], 960):
                            real = sum(value * math.cos(2 * math.pi * frequency * sample / 48000) for sample, value in enumerate(part))
                            imag = sum(value * math.sin(2 * math.pi * frequency * sample / 48000) for sample, value in enumerate(part))
                            self.assertGreater(2 * math.hypot(real, imag) / len(part), 0.02)
                    else:
                        self.assertEqual(actual, expected)
                    self.assertAlmostEqual(float(self.probe(fitted)["streams"][index + 1]["duration"]), 1, delta=0.035)
                raw = self.run_media([FFMPEG, "-v", "error", "-i", str(fitted), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
                self.assertLess(max(raw[:128 * 20 * 3]), 12)
                self.assertLess(max(raw[-128 * 20 * 3:]), 12)
                self.assertGreater(max(raw[128 * 32 * 3:128 * 96 * 3]), 150)
        self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, bed)])
        for invalid in ({**canvas, "width": "128"}, {**canvas, "width": 127}, {**canvas, "height": 8192}, {**canvas, "fps": float("nan")}):
            with self.assertRaisesRegex(ValueError, "canvas"):
                render_single_source_cut(source, self.root / "invalid.mp4", source_in=0, duration=1, canvas=invalid)
        self.assertFalse((self.root / "invalid.mp4").exists())

    def test_sequence_joins_mixed_canvas_fps_and_audio_at_exact_frames(self):
        sources = []
        for name, color, size, fps, audio in (("red", "red", "128x72", 24, True), ("blue", "blue", "72x128", 30, False)):
            source = self.root / f"{name}.mp4"
            command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                       "-f", "lavfi", "-i", f"color={color}:s={size}:r={fps}:d=2"]
            if audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"]
            command += ["-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", str(source)]
            self.run_media(command)
            sources.append(source)
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
        clips = [{"path": str(path), "source_in": 0.25, "duration": 0.55, "has_audio": index == 0}
                 for index, path in enumerate(sources)]
        destination = self.root / "sequence.mp4"
        render_video_sequence(clips, destination, width=128, height=72, fps=24, timeout=30)
        media = self.probe(destination)
        self.assertEqual([(stream["codec_type"], stream["codec_name"]) for stream in media["streams"]], [("video", "h264"), ("audio", "aac")])
        self.assertAlmostEqual(float(media["streams"][0]["duration"]), 26 / 24, delta=0.001)
        pixels = self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-map", "0:v", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        stride = 128 * 72 * 3
        self.assertEqual(len(pixels), stride * 26)
        center = (36 * 128 + 64) * 3
        for frame in (0, 12):
            red, _, blue = pixels[frame * stride + center:frame * stride + center + 3]
            self.assertGreater(red, 200)
            self.assertLess(blue, 30)
        for frame in (13, 25):
            red, _, blue = pixels[frame * stride + center:frame * stride + center + 3]
            self.assertLess(red, 30)
            self.assertGreater(blue, 200)
        # Portrait source is fitted, preserving black letterbox outside the image.
        self.assertLess(max(pixels[13 * stride:13 * stride + 3]), 10)
        samples = array.array("f", self.run_media([FFMPEG, "-v", "error", "-i", str(destination), "-map", "0:a", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"]))
        self.assertGreater(max(abs(value) for value in samples[4800:19200]), 0.03)
        self.assertLess(max(abs(value) for value in samples[33600:43200]), 0.001)
        self.assertEqual([hashlib.sha256(path.read_bytes()).hexdigest() for path in sources], hashes)
        def cancelled(command, **_options):
            Path(command[-1]).write_bytes(b"partial")
            return 0
        with self.assertRaises(InterruptedError):
            render_video_sequence(clips, self.root / "cancelled.mp4", width=128, height=72, fps=24, runner=cancelled, abort_check=lambda: True)
        self.assertFalse((self.root / "cancelled.mp4").exists())
        with self.assertRaises(FileExistsError):
            render_video_sequence(clips, destination, width=128, height=72, fps=24)
        self.assertFalse(list(self.root.glob(".editor-sequence-*")))

    def test_failed_or_cancelled_render_never_replaces_an_output(self):
        source = self.make_source(audio_tracks=0)
        destination = self.root / "cut.mp4"
        def partial_encode(command, **_kwargs):
            Path(command[-1]).write_bytes(b"partial")
            return 1
        with self.assertRaisesRegex(RuntimeError, "encoding failed"):
            render_single_source_cut(source, destination, source_in=0, duration=0.5, runner=partial_encode)
        self.assertFalse(destination.exists())

        def cancelled_encode(command, **_kwargs):
            Path(command[-1]).write_bytes(b"complete")
            return 0
        with self.assertRaisesRegex(InterruptedError, "cancelled"):
            render_single_source_cut(
                source, destination, source_in=0, duration=0.5,
                runner=cancelled_encode, abort_check=lambda: True,
            )
        self.assertFalse(destination.exists())
        destination.write_bytes(b"owned by another export")
        with self.assertRaises(FileExistsError):
            render_single_source_cut(source, destination, source_in=0, duration=0.5)
        self.assertEqual(destination.read_bytes(), b"owned by another export")


if __name__ == "__main__":
    unittest.main()
