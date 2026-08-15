"""Deterministic, final-container true-peak protection for MiniMax H3 audio.

The policy deliberately applies only a constant negative gain.  It does not
compress, normalize, clip, or otherwise reshape the soundtrack envelope.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import threading


POLICY_VERSION = "h3-final-true-peak-v1"
DEFAULT_TARGET_DBTP = -1.0
OVERSAMPLE_FACTOR = 4
_VERIFY_HEADROOM_DB = 0.10
_LINEAR_METER_ROUNDING = 0.0005
_TRUE_PEAK_PATTERN = re.compile(
    r"lavfi\.r128\.true_peak=([^\s]+)"
)


class H3AudioSafetyError(RuntimeError):
    """Raised when final H3 audio cannot be measured or made compliant."""


def _tool_paths():
    ffmpeg = os.environ.get("FFMPEG_BINARY", "ffmpeg")
    ffprobe = os.environ.get("FFPROBE_BINARY")
    if not ffprobe:
        directory, filename = os.path.split(ffmpeg)
        if "ffmpeg" in filename:
            ffprobe = os.path.join(directory, filename.replace("ffmpeg", "ffprobe", 1))
        else:
            ffprobe = "ffprobe"
    return ffmpeg, ffprobe


def _probe_media(media_path):
    _, ffprobe = _tool_paths()
    completed = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            os.fspath(media_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise H3AudioSafetyError("Final H3 media could not be probed")
    try:
        probe = json.loads(completed.stdout)
        audio_streams = [
            stream for stream in probe.get("streams", [])
            if stream.get("codec_type") == "audio"
        ]
        video_streams = [
            stream for stream in probe.get("streams", [])
            if stream.get("codec_type") == "video"
        ]
        if len(audio_streams) != 1 or not video_streams:
            raise ValueError
        audio = audio_streams[0]
        sample_rate = int(audio["sample_rate"])
        channels = int(audio["channels"])
        if not 8000 <= sample_rate <= 192000 or not 1 <= channels <= 8:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise H3AudioSafetyError(
            "Final H3 media must contain one valid audio stream and video"
        ) from error
    return probe, audio, video_streams[0]


def _dbfs(peak):
    if peak <= 0.0:
        return None
    return 20.0 * math.log10(peak)


def _peak_from_ebur128_lines(lines):
    peak = 0.0
    observations = 0
    for line in lines:
        match = _TRUE_PEAK_PATTERN.search(str(line))
        if match is None:
            continue
        try:
            value = float(match.group(1))
        except (TypeError, ValueError) as error:
            raise H3AudioSafetyError(
                "Decoded H3 true-peak metadata was invalid"
            ) from error
        if not math.isfinite(value) or value < 0.0:
            raise H3AudioSafetyError(
                "Decoded H3 audio contained a non-finite true peak"
            )
        peak = max(peak, value)
        observations += 1
    return peak, observations


def measure_true_peak(media_path, *, oversample_factor=OVERSAMPLE_FACTOR):
    """Measure decoded intersample peak with bounded-memory reconstruction."""

    if (
        isinstance(oversample_factor, bool)
        or oversample_factor != OVERSAMPLE_FACTOR
    ):
        raise ValueError("FFmpeg ebur128 true-peak measurement is fixed at 4x")
    factor = OVERSAMPLE_FACTOR
    _, audio, _ = _probe_media(media_path)
    sample_rate = int(audio["sample_rate"])
    ffmpeg, _ = _tool_paths()
    # FFmpeg's EBU R128 meter performs its standardized reconstructed true-peak
    # measurement while decoding. Per-frame metadata keeps the parse bounded
    # and avoids treating resampler edge ringing as program material.
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-v", "info",
        "-nostdin",
        "-i", os.fspath(media_path),
        "-map", "0:a:0",
        "-vn",
        "-filter:a", (
            "ebur128=metadata=1:peak=true,"
            "ametadata=print:key=lavfi.r128.true_peak"
        ),
        "-f", "null",
        "-",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    meter_result = {}

    def drain_meter():
        try:
            assert process.stderr is not None
            meter_result["measurement"] = _peak_from_ebur128_lines(
                process.stderr
            )
        except Exception as error:
            meter_result["error"] = error

    reader = threading.Thread(
        target=drain_meter,
        daemon=True,
        name="h3-true-peak-meter",
    )
    reader.start()
    try:
        returncode = process.wait(timeout=300)
        reader.join(timeout=5)
        if reader.is_alive():
            raise H3AudioSafetyError("Final H3 true-peak meter did not finish")
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=5)
        raise
    finally:
        if process.stderr is not None:
            process.stderr.close()
    if "error" in meter_result:
        raise meter_result["error"]
    peak, observations = meter_result.get("measurement", (0.0, 0))
    if returncode != 0 or observations == 0 or not math.isfinite(peak):
        raise H3AudioSafetyError("Final H3 audio could not be decoded for verification")
    return {
        "peak_linear": peak,
        "peak_dbtp": _dbfs(peak),
        "sample_rate": sample_rate,
        "channels": int(audio["channels"]),
        "oversample_factor": factor,
    }


def _codec_arguments(audio_stream):
    codec = str(audio_stream.get("codec_name") or "").lower()
    encoders = {
        "aac": "aac",
        "alac": "alac",
        "flac": "flac",
        "opus": "libopus",
        "mp3": "libmp3lame",
        "pcm_s16le": "pcm_s16le",
        "pcm_s24le": "pcm_s24le",
        "pcm_s32le": "pcm_s32le",
        "pcm_f32le": "pcm_f32le",
        "pcm_f64le": "pcm_f64le",
    }
    encoder = encoders.get(codec)
    if encoder is None:
        raise H3AudioSafetyError("Final H3 audio codec is not supported by the safety pass")
    arguments = ["-c:a:0", encoder]
    if codec in {"aac", "opus", "mp3"}:
        try:
            bitrate = int(audio_stream.get("bit_rate") or 0)
        except (TypeError, ValueError):
            bitrate = 0
        if bitrate <= 0:
            bitrate = 192000
        bitrate = min(1000000, max(32000, bitrate))
        arguments.extend(["-b:a:0", str(bitrate)])
    return arguments, codec


def _duration_seconds(stream):
    try:
        duration = float(stream.get("duration"))
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration >= 0 else None


def _finite_seconds(stream, key):
    try:
        value = float(stream.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _verify_optional_time(before, after, key, tolerance, label):
    before_value = _finite_seconds(before, key)
    if before_value is None:
        return
    after_value = _finite_seconds(after, key)
    if after_value is None or abs(after_value - before_value) > tolerance:
        raise H3AudioSafetyError(f"H3 audio safety changed the {label}")


def _verify_stream_contract(before_audio, before_video, after_audio, after_video):
    if (
        int(after_audio.get("sample_rate") or 0)
        != int(before_audio.get("sample_rate") or 0)
        or int(after_audio.get("channels") or 0)
        != int(before_audio.get("channels") or 0)
        or (
            before_audio.get("channel_layout") is not None
            and after_audio.get("channel_layout")
            != before_audio.get("channel_layout")
        )
        or str(after_video.get("codec_name") or "")
        != str(before_video.get("codec_name") or "")
    ):
        raise H3AudioSafetyError("H3 audio safety changed the media stream contract")
    _verify_optional_time(
        before_video, after_video, "duration", 0.001, "video duration",
    )
    _verify_optional_time(
        before_video, after_video, "start_time", 0.001, "video start time",
    )
    before_audio_duration = _duration_seconds(before_audio)
    after_audio_duration = _duration_seconds(after_audio)
    duration_tolerance = 1.0 / int(before_audio["sample_rate"])
    if (
        before_audio_duration is not None
        and (
            after_audio_duration is None
            or abs(after_audio_duration - before_audio_duration)
            > duration_tolerance
        )
    ):
        raise H3AudioSafetyError("H3 audio safety changed the audio duration")
    _verify_optional_time(
        before_audio,
        after_audio,
        "start_time",
        duration_tolerance,
        "audio start time",
    )


def _render_attenuated_candidate(media_path, output_path, gain_db, audio_stream):
    ffmpeg, _ = _tool_paths()
    codec_args, codec = _codec_arguments(audio_stream)
    audio_duration = _duration_seconds(audio_stream)
    if audio_duration is None:
        raise H3AudioSafetyError("H3 audio duration could not be preserved")
    command = [
        ffmpeg,
        "-y",
        "-v", "error",
        "-nostdin",
        "-i", os.fspath(media_path),
        "-map", "0:v?",
        "-map", "0:a:0",
        "-map", "0:s?",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-c", "copy",
        "-filter:a:0", (
            f"volume={float(gain_db):.8f}dB,"
            f"atrim=end={audio_duration:.9f}"
        ),
        *codec_args,
        "-ar:a:0", str(int(audio_stream["sample_rate"])),
        "-ac:a:0", str(int(audio_stream["channels"])),
    ]
    if os.path.splitext(os.fspath(output_path))[1].lower() in {".mp4", ".m4v", ".mov"}:
        command.extend(["-movflags", "+faststart"])
    command.append(os.fspath(output_path))
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if (
        completed.returncode != 0
        or not os.path.isfile(output_path)
        or os.path.getsize(output_path) < 1
    ):
        raise H3AudioSafetyError("H3 audio attenuation remux failed")
    return codec


def _public_stats(pre, post, target_dbtp, gain_db, verified):
    def measured(value):
        return None if value is None else round(float(value), 1)

    return {
        "policy_version": POLICY_VERSION,
        "target_dbtp": round(float(target_dbtp), 1),
        "measured_pre_dbtp": measured(pre.get("peak_dbtp")),
        "measured_post_dbtp": measured(post.get("peak_dbtp")),
        "applied_gain_db": round(min(0.0, float(gain_db)), 3),
        "oversample_factor": int(pre["oversample_factor"]),
        "verified": bool(verified),
    }


def enforce_true_peak_safety(
    media_path,
    *,
    target_dbtp=DEFAULT_TARGET_DBTP,
    max_attempts=3,
):
    """Atomically enforce the H3 final-container ceiling and return safe stats."""

    target = float(target_dbtp)
    if not math.isfinite(target) or target > 0.0 or target < -12.0:
        raise ValueError("target_dbtp must be finite and between -12 and 0")
    attempts = int(max_attempts)
    if attempts < 1 or attempts > 4:
        raise ValueError("max_attempts must be between 1 and 4")

    before_probe, before_audio, before_video = _probe_media(media_path)
    del before_probe
    original_mode = os.stat(media_path, follow_symlinks=False).st_mode & 0o7777
    pre = measure_true_peak(media_path)
    pre_db = pre["peak_dbtp"]
    internal_target_dbtp = target - _VERIFY_HEADROOM_DB
    verified_linear_ceiling = (
        10.0 ** (internal_target_dbtp / 20.0)
        - _LINEAR_METER_ROUNDING
    )
    if pre_db is None or pre["peak_linear"] <= verified_linear_ceiling:
        return _public_stats(pre, pre, target, 0.0, True)

    _, codec = _codec_arguments(before_audio)
    margin_db = 0.75 if codec in {"aac", "opus", "mp3"} else 0.10
    cumulative_gain_db = min(
        0.0, internal_target_dbtp - pre_db - margin_db,
    )
    directory = os.path.dirname(os.path.abspath(os.fspath(media_path)))
    suffix = os.path.splitext(os.fspath(media_path))[1]
    last_post = pre

    for _ in range(attempts):
        descriptor, candidate = tempfile.mkstemp(
            prefix=".h3-true-peak-", suffix=suffix, dir=directory,
        )
        os.close(descriptor)
        try:
            os.remove(candidate)
            _render_attenuated_candidate(
                media_path, candidate, cumulative_gain_db, before_audio,
            )
            _, after_audio, after_video = _probe_media(candidate)
            _verify_stream_contract(
                before_audio, before_video, after_audio, after_video,
            )
            last_post = measure_true_peak(candidate)
            post_db = last_post["peak_dbtp"]
            if (
                post_db is None
                or last_post["peak_linear"] <= verified_linear_ceiling
            ):
                os.chmod(candidate, original_mode)
                os.replace(candidate, media_path)
                return _public_stats(
                    pre, last_post, target, cumulative_gain_db, True,
                )
            cumulative_gain_db += min(
                0.0, internal_target_dbtp - post_db - margin_db,
            )
        finally:
            try:
                os.remove(candidate)
            except FileNotFoundError:
                pass

    raise H3AudioSafetyError("Final H3 audio remained above the true-peak ceiling")
