"""Bounded, content-free video facts for completed local tool outputs."""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time


def _positive_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _positive_int(value):
    number = _positive_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _frame_rate(value):
    if not isinstance(value, str):
        return None
    parts = value.split("/", 1)
    numerator = _positive_number(parts[0])
    denominator = _positive_number(parts[1]) if len(parts) == 2 else 1
    if numerator is None or denominator is None:
        return None
    rate = numerator / denominator
    return round(rate, 6) if math.isfinite(rate) and rate > 0 else None


def probe_video_facts(path, *, ffprobe="ffprobe", cancel_check=None):
    """Read only selected container headers; return None when no video is proven.

    Output contains numeric facts only. Invalid, unsupported or stalled media is
    optional history data and must not prevent a finished tool from publishing.
    """
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return None
        if cancel_check and cancel_check():
            return None
        process = subprocess.Popen(
            [ffprobe, "-v", "error", "-show_entries",
             "stream=codec_type,width,height,avg_frame_rate,channels,sample_rate,duration:format=duration",
             "-of", "json", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            output = []
            def read_capped():
                try:
                    output.append(process.stdout.read(65537))
                except OSError:
                    pass
            reader = threading.Thread(target=read_capped, daemon=True)
            reader.start()
            deadline = time.monotonic() + 12
            while reader.is_alive():
                reader.join(timeout=min(0.1, max(0, deadline - time.monotonic())))
                if (cancel_check and cancel_check()) or time.monotonic() >= deadline:
                    return None
            if not output or len(output[0]) > 65536:
                return None
            if process.wait(timeout=max(0.1, deadline - time.monotonic())) != 0:
                return None
            payload = json.loads(output[0])
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            process.stdout.close()
        streams = payload.get("streams")
        if not isinstance(streams, list):
            return None
        video = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
        if video is None:
            return None
        width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
        if width is None or height is None:
            return None
        audio = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"), None)
        facts = {"size_bytes": size, "width": width, "height": height, "has_audio": audio is not None}
        fps = _frame_rate(video.get("avg_frame_rate"))
        if fps is not None:
            facts["fps"] = fps
        container = payload.get("format")
        duration = _positive_number(container.get("duration")) if isinstance(container, dict) else None
        duration = duration or _positive_number(video.get("duration"))
        if duration is not None:
            facts["duration_seconds"] = round(duration, 3)
        if audio is not None:
            channels = _positive_int(audio.get("channels"))
            sample_rate = _positive_int(audio.get("sample_rate"))
            if channels is not None:
                facts["audio_channels"] = channels
            if sample_rate is not None:
                facts["audio_sample_rate"] = sample_rate
        return facts
    except (OSError, TypeError, ValueError, subprocess.TimeoutExpired):
        return None
