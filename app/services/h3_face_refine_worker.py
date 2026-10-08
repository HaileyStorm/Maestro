"""Sealed CPU crop/source admission for the private H3 generation worker.

This reads owner-selected local files. It creates no job or model, grants no
project/GPU authority and publishes no media. Public Gallery admission remains
separate; its future worker must revalidate project revision and privacy.
"""
from dataclasses import dataclass
import copy
import math
import os
import shutil

from services import h3_face_refine as face
from services import h3_gallery_av_guide as av


@dataclass(frozen=True)
class H3FaceRefineDispatch:
    plan: dict
    binding: dict
    payload: object


def _contract(plan, expected_plan_sha256, audio_stream):
    facts, track = face._validate_plan(plan)
    frames = facts["frame_count"]
    if (expected_plan_sha256 != plan["plan_sha256"] or not 124 <= frames <= 345
            or frames % 17 != 5):
        raise ValueError("FaceRefine worker requires the pinned exact Base-H3 crop clock")
    if audio_stream is not None and (type(audio_stream) is not int or not 0 <= audio_stream < 16):
        raise ValueError("FaceRefine requires an explicit source audio ordinal or None")
    from models.minimax_h3.packing import audio_latent_num_frames
    samples = 0 if audio_stream is None else audio_latent_num_frames(frames) * 800
    width, height = track["canvas"]
    # Decode, WGP capture and native capture can coexist. Include finite-check
    # scratch and pipe buffers rather than counting only the final tensor.
    if (frames * height * width * 12 + samples * 8 > 512 * 1024**2
            or frames * height * width * 3 * 13 + samples * 2 * 13 + 8 * 1024**2 > av.MAX_DECODED_BYTES):
        raise ValueError("FaceRefine worker exceeds combined decoded/capture memory limit")
    binding = dict(schema="maestro.h3.face-worker", version=1,
                   plan_sha256=expected_plan_sha256, source_sha256=facts["sha256"],
                   crops_sha256=plan["crops_sha256"], audio_stream=audio_stream,
                   audio_sample_count=samples)
    return facts, track, binding


def validate_face_refine_dispatch(value, *, frame_num, height, width, sampling_steps):
    """Recheck and capture the exact private handoff before model preparation."""
    if type(value) is not H3FaceRefineDispatch or type(value.binding) is not dict:
        raise ValueError("FaceRefine requires the typed private worker handoff")
    facts, track, binding = _contract(value.plan, value.binding.get("plan_sha256"),
                                      value.binding.get("audio_stream"))
    if (value.binding != binding
            or (frame_num, height, width) != (facts["frame_count"], track["canvas"][1], track["canvas"][0])):
        raise ValueError("FaceRefine worker source binding or target changed")
    from models.minimax_h3.face_refine import H3FaceRefinePayload, validate_face_refine_request
    if type(value.payload) is not H3FaceRefinePayload:
        raise ValueError("FaceRefine worker payload is invalid")
    if (value.payload.waveform is None) != (binding["audio_stream"] is None):
        raise ValueError("FaceRefine worker selected audio differs from its decoded handoff")
    payload = validate_face_refine_request(
        value.payload, frame_num=frame_num, height=height, width=width, fps=24,
        sampling_steps=sampling_steps, reference_mode=False, model_type="minimax_h3",
        custom_settings={"h3_attention_engine": "sdpa"}, conditioning=(), kwargs={},
    )
    if any(multiplier != 0 for rectangle, multiplier in
           zip(track["rectangles"], payload.frame_multipliers) if rectangle is None):
        raise ValueError("FaceRefine unresolved frames must hold the source")
    return H3FaceRefineDispatch(copy.deepcopy(value.plan), binding, payload)


def _decode_video(snapshot, *, frames, height, width, cancel_check):
    import torch
    count = frames * height * width * 3
    tensor = torch.empty(count, dtype=torch.float32, device="cpu")
    offset = 0
    def consume(chunk):
        nonlocal offset
        values = torch.frombuffer(bytearray(chunk), dtype=torch.uint8)
        tensor[offset:offset + values.numel()].copy_(values)
        offset += values.numel()
    command = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-nostdin",
               "-threads", "2", *av._input(snapshot), "-map", "0:v:0",
               "-an", "-sn", "-dn", "-vsync", "0", "-pix_fmt", "rgb24",
               "-f", "rawvideo", "pipe:1"]
    av._stream(command, count, consume, cancel_check)
    if offset != count:
        raise ValueError("FaceRefine decoded crop frame count changed")
    tensor.div_(255)
    return tensor.reshape(frames, height, width, 3).permute(3, 0, 1, 2).unsqueeze(0)


def _decode_audio(snapshot, ordinal, samples, cancel_check):
    import torch
    data = face._json_command([shutil.which("ffprobe") or "ffprobe", "-v", "error",
        *av._input(snapshot), "-select_streams", "a", "-show_entries", "stream=index",
        "-of", "json"], cancel_check)
    if ordinal >= len(data.get("streams", [])):
        raise ValueError("FaceRefine selected source audio stream is unavailable")
    tensor = torch.empty(samples * 2, dtype=torch.float32, device="cpu")
    offset = 0
    def consume(chunk):
        nonlocal offset
        if len(chunk) % 4:
            raise ValueError("FaceRefine decoded audio sample bytes changed")
        values = torch.frombuffer(bytearray(chunk), dtype=torch.float32)
        tensor[offset:offset + values.numel()].copy_(values)
        offset += values.numel()
    # Preserve source timestamp gaps, then explicitly silence-pad/trim only the
    # conditioning copy to round(frames/24*40)*800. Never change source packets.
    command = [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-nostdin",
        "-copyts", "-threads", "2", *av._input(snapshot), "-map", f"0:a:{ordinal}",
        "-vn", "-sn", "-dn", "-af",
        f"aresample=32000:async=1:first_pts=0,apad,atrim=end_sample={samples}",
        "-ac", "2", "-ar", "32000", "-f", "f32le", "pipe:1"]
    av._stream(command, samples * 8, consume, cancel_check)
    if offset != samples * 2:
        raise ValueError("FaceRefine decoded source audio clock changed")
    return tensor.reshape(samples, 2).T.contiguous()


def make_face_refine_dispatch(source, crops, plan, *, expected_plan_sha256,
                              strength, frame_multipliers, audio_stream,
                              sampling_steps, cancel_check=None):
    """Decode a sealed bundle once on its worker; no paths enter the dispatch."""
    if os.environ.get("MAESTRO_H3_FACE_REFINE_EXPERIMENTAL") != "1":
        raise ValueError("FaceRefine worker requires the private experimental gate")
    plan = copy.deepcopy(plan)
    facts, track, binding = _contract(plan, expected_plan_sha256, audio_stream)
    if (type(sampling_steps) is not int or not 2 <= sampling_steps <= 100
            or type(strength) not in (int, float) or not math.isfinite(strength)
            or not sampling_steps / 4096 <= strength <= 1):
        raise ValueError("FaceRefine worker denoise schedule is invalid")
    width, height = track["canvas"]
    expected = {k: facts[k] for k in ("width", "height", "frame_count", "fps")}
    with av._snapshot(str(source), "video", cancel_check) as (original, digest, size):
        if ((digest, size) != (facts["sha256"], facts["size"])
                or face._probe(original, cancel_check) != expected):
            raise ValueError("FaceRefine worker source bytes or clock changed")
        with av._snapshot(str(crops), "video", cancel_check) as (crop_snapshot, crop_digest, _):
            if (crop_digest != plan["crops_sha256"]
                    or face._probe(crop_snapshot, cancel_check) != {**expected, "width": width, "height": height}):
                raise ValueError("FaceRefine worker crop bytes or clock changed")
            video = _decode_video(crop_snapshot, frames=facts["frame_count"],
                                  height=height, width=width, cancel_check=cancel_check)
            waveform = (None if audio_stream is None else _decode_audio(
                original, audio_stream, binding["audio_sample_count"], cancel_check))
            av._check(cancel_check)
    from models.minimax_h3.face_refine import H3FaceRefinePayload
    return validate_face_refine_dispatch(
        H3FaceRefineDispatch(plan, binding, H3FaceRefinePayload(
            video, strength, frame_multipliers, waveform)),
        frame_num=facts["frame_count"], height=height, width=width,
        sampling_steps=sampling_steps,
    )
