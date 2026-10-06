"""Private precomputed Control video admission; model and VAE execution stay upstream.

Asset receipts bind selections and regular-file metadata. The native loader still
proves every checkpoint byte; this boundary never rehashes the 73 GB export.
"""
from __future__ import annotations

import ast
import copy
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from services import h3_gallery_av_guide as av
from services.h3_control_plan import validate_h3_control_plan


class H3GalleryControlError(ValueError):
    """Invalid private Control input, with no filesystem details in messages."""


@dataclass(frozen=True)
class H3GalleryControlDispatch:
    video: object
    source_binding: dict
    plan: dict
    base_checkpoint: str
    control_checkpoint: str


_ASSET_SCHEMA = "maestro.h3.gallery-control-assets"
_SOURCE_FIELDS = av.GALLERY_AV_SOURCE_FIELDS - {"frame_index"}
_FILE_FIELDS = {"role", "name", "size", "sha256", "identity", "selection_sha256"}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_CONTROL_NAME = "MiniMax-H3-Fun-Controlnet-Union.safetensors"
_INDEX_NAME = "model.safetensors.index.json"


def _digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _literal_constants(path, names):
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 128 * 1024:
            raise H3GalleryControlError("Control asset contract is unavailable")
        data = os.read(descriptor, info.st_size + 1)
        if len(data) != info.st_size or av._identity(os.fstat(descriptor)) != av._identity(info):
            raise H3GalleryControlError("Control asset contract changed")
        values = {}
        for node in ast.parse(data).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        if target.id in values:
                            raise ValueError()
                        values[target.id] = ast.literal_eval(node.value)
        if set(values) != set(names):
            raise ValueError()
        return values
    except (OSError, ValueError, SyntaxError, TypeError):
        raise H3GalleryControlError("Control asset contract is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _asset_contract():
    root = Path(__file__).resolve().parents[1] / "models" / "minimax_h3"
    base = _literal_constants(root / "original_base.py", {
        "ORIGINAL_BASE_SHARDS", "ORIGINAL_BASE_INDEX_SHA256",
        "ORIGINAL_BASE_REPOSITORY", "ORIGINAL_BASE_REVISION"})
    control = _literal_constants(root / "control.py", {"ORIGINAL_CONTROL_SHA256", "ORIGINAL_CONTROL_BYTES"})
    shards = base["ORIGINAL_BASE_SHARDS"]
    if (type(shards) is not tuple or len(shards) != 13
            or base["ORIGINAL_BASE_REPOSITORY"] != "MiniMaxAI/MiniMax-H3"
            or base["ORIGINAL_BASE_REVISION"] != "5d9b308a59ab12e67147f191e184baf704185bd1"
            or base["ORIGINAL_BASE_INDEX_SHA256"] != "fb457a26ffa6294660e249b0ddd03a337f2e5393f770b5c34c8b8f90a29a7efb"
            or control["ORIGINAL_CONTROL_SHA256"] != "919a48acb525dc8fc70287fcd94ec1f5e5e289a77f1df14d01099c6ce204eb02"
            or type(control["ORIGINAL_CONTROL_BYTES"]) is not int
            or control["ORIGINAL_CONTROL_BYTES"] != 6_806_843_904):
        raise H3GalleryControlError("Control asset contract differs from the original export")
    for index, row in enumerate(shards, 1):
        if (type(row) is not tuple or len(row) != 3
                or row[0] != f"model-{index:05d}-of-00013.safetensors"
                or type(row[1]) is not int or not 0 < row[1] < 8 * 1024**3
                or type(row[2]) is not str or re.fullmatch(r"[0-9a-f]{64}", row[2]) is None):
            raise H3GalleryControlError("Control Base shard contract is invalid")
    if hashlib.sha256(json.dumps(shards, separators=(",", ":")).encode("ascii")).hexdigest() != "33d7eca04dacd7b6274fcf1e5c2b9aa26811f7318a670dfc7cbcb613dcfb5567":
        raise H3GalleryControlError("Control Base shard pins differ from the original export")
    return shards, base["ORIGINAL_BASE_INDEX_SHA256"], control["ORIGINAL_CONTROL_BYTES"], control["ORIGINAL_CONTROL_SHA256"]


def _capture(path, role, expected_size, sha256, *, index=False):
    descriptor = -1
    try:
        requested = str(path)
        resolved = str(path.resolve(strict=True))
        descriptor = os.open(requested, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or av._identity(info) != av._identity(os.lstat(requested))
                or (info.st_size != expected_size if not index else not 0 < info.st_size <= 1024**2)):
            raise H3GalleryControlError("Control assets are incomplete or changed")
        if index and _digest(os.read(descriptor, info.st_size + 1)) != "sha256:" + sha256:
            raise H3GalleryControlError("Control Base index differs from the original export")
        if (av._identity(os.fstat(descriptor)) != av._identity(info)
                or av._identity(os.lstat(requested)) != av._identity(info)
                or str(path.resolve(strict=True)) != resolved):
            raise H3GalleryControlError("Control assets changed while captured")
        return {"role": role, "name": path.name, "size": info.st_size,
                "sha256": "sha256:" + sha256, "identity": list(av._identity(info)),
                "selection_sha256": _digest((requested + "\0" + resolved).encode("utf-8"))}
    except (OSError, ValueError, UnicodeError):
        raise H3GalleryControlError("Control assets are unavailable or changed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def control_assets_from_environment():
    """Capture already acquired original assets under both private route flags."""
    if any(os.environ.get(key) != "1" for key in (
            "MAESTRO_H3_CONTROL_EXPERIMENTAL", "MAESTRO_H3_CONTROL_GALLERY_EXPERIMENTAL")):
        raise H3GalleryControlError("Private Gallery Control is disabled")
    selected = [os.environ.get(key) for key in (
        "MAESTRO_H3_CONTROL_BASE_CHECKPOINT", "MAESTRO_H3_CONTROL_CHECKPOINT")]
    if any(type(path) is not str or not path or "\0" in path or not os.path.isabs(path) for path in selected):
        raise H3GalleryControlError("Private Gallery Control requires acquired original assets")
    base, control = map(Path, selected)
    shards, index_sha, control_size, control_sha = _asset_contract()
    if base.name != shards[0][0] or control.name != _CONTROL_NAME:
        raise H3GalleryControlError("Private Gallery Control requires exact original filenames")
    paths = [(base.parent / _INDEX_NAME, "index", None, index_sha)]
    paths += [(base.parent / name, "base", size, digest) for name, size, digest in shards]
    paths += [(control, "control", control_size, control_sha)]
    for path, role, _size, _sha in paths:
        if role != "index" and os.path.lexists(path.with_name(path.stem + "_map.json")):
            raise H3GalleryControlError("Private Gallery Control does not accept quantization sidecars")
    files = [_capture(path, role, size, digest, index=role == "index") for path, role, size, digest in paths]
    return {"base_checkpoint": str(base), "control_checkpoint": str(control),
            "binding": {"schema": _ASSET_SCHEMA, "version": 1, "files": files}}


def _asset_binding(value):
    if (type(value) is not dict or set(value) != {"schema", "version", "files"}
            or value["schema"] != _ASSET_SCHEMA or type(value["version"]) is not int or value["version"] != 1
            or type(value["files"]) is not list or len(value["files"]) != 15):
        raise H3GalleryControlError("Control asset binding is invalid")
    shards, index_sha, control_size, control_sha = _asset_contract()
    expected = [("index", _INDEX_NAME, None, index_sha)]
    expected += [("base", name, size, digest) for name, size, digest in shards]
    expected += [("control", _CONTROL_NAME, control_size, control_sha)]
    for item, (role, name, size, digest) in zip(value["files"], expected):
        if (type(item) is not dict or set(item) != _FILE_FIELDS
                or any(type(item[key]) is not str for key in ("role", "name", "sha256"))
                or item["role"] != role or item["name"] != name or item["sha256"] != "sha256:" + digest
                or type(item["size"]) is not int
                or (item["size"] != size if size is not None else not 0 < item["size"] <= 1024**2)
                or type(item["identity"]) is not list or len(item["identity"]) != 6
                or any(type(number) is not int or number < 0 for number in item["identity"])
                or item["identity"][2] != item["size"] or item["identity"][5] != 1
                or type(item["selection_sha256"]) is not str or _DIGEST.fullmatch(item["selection_sha256"]) is None):
            raise H3GalleryControlError("Control asset binding differs from the original export")
    return copy.deepcopy(value)


def make_gallery_control_source(record, plan, asset_binding):
    """Seal one explicitly chosen, precomputed non-inpaint Gallery video."""
    if type(record) is not dict or set(record) != _SOURCE_FIELDS:
        raise H3GalleryControlError("Control source fields are invalid")
    try:
        source = av._records([{**record, "frame_index": 0}])[0]
        validated = validate_h3_control_plan(plan)
    except (ValueError, TypeError):
        raise H3GalleryControlError("Control source or plan is invalid") from None
    source.pop("frame_index")
    geometry, control = validated["geometry"], validated["control"]
    if (source["kind"] != "video"
            or control["kind"] == "inpaint" or control["mask_sha256"] is not None
            or control["source_sha256"] != source["sha256"]
            or (geometry["source_width"], geometry["source_height"], geometry["source_frame_count"])
            != (source["width"], source["height"], source["frame_count"])):
        raise H3GalleryControlError("Control plan differs from the selected precomputed video")
    return {"source": source, "plan_sha256": validated["plan_sha256"], "assets": _asset_binding(asset_binding)}


def _envelope(binding, plan):
    if type(binding) is not dict or set(binding) != {"source", "plan_sha256", "assets"}:
        raise H3GalleryControlError("Control source binding is invalid")
    expected = make_gallery_control_source(binding["source"], plan, binding["assets"])
    if binding != expected:
        raise H3GalleryControlError("Control source binding differs from the plan")
    return expected


def decode_gallery_control_source(path, binding, plan, *, cancel_check=None):
    """Decode the sealed legal prefix as CPU float32 unit-range BCTHW pixels."""
    expected = _envelope(binding, plan)
    source, geometry = expected["source"], plan["geometry"]
    frames, height, width = (geometry[key] for key in ("frame_count", "height", "width"))
    count = frames * height * width * 3
    if max(height, width) > 4096 or count * 4 + height * width * 36 + 8 * 1024**2 > av.MAX_DECODED_BYTES:
        raise H3GalleryControlError("Control decoded media exceeds its limit")
    try:
        av._check(cancel_check)
        with av._snapshot(path, "video", cancel_check) as (snapshot, sha256, size):
            if (sha256, size) != (source["sha256"], source["size"]):
                raise H3GalleryControlError("Control source bytes changed")
            facts = asdict(av._probe_snapshot(snapshot, sha256, size, "video", cancel_check))
            if any(source[key] != value for key, value in facts.items()):
                raise H3GalleryControlError("Control source geometry or full frame count changed")
            import torch
            tensor = torch.empty(count, dtype=torch.float32, device="cpu")
            offset = 0
            def consume(chunk):
                nonlocal offset
                values = torch.frombuffer(bytearray(chunk), dtype=torch.uint8)
                tensor[offset:offset + values.numel()].copy_(values)
                offset += values.numel()
            # Control geometry already fits the original aspect to a 32-pixel
            # canvas. Preserve the full image instead of applying Guide's crop.
            command = av._conversion(snapshot, "video", height, width)
            command[command.index("-vf") + 1] = f"fps=fps=24:round=near,scale={width}:{height},setsar=1"
            command[command.index("-pix_fmt"):command.index("-pix_fmt")] = ["-frames:v", str(frames)]
            av._stream(command, count, consume, cancel_check)
            if offset != count:
                raise H3GalleryControlError("Control normalized prefix count changed")
            tensor.div_(255)
            av._check(cancel_check)
            return tensor.reshape(frames, height, width, 3).permute(3, 0, 1, 2).unsqueeze(0)
    except av.H3GalleryAVGuideCancelled:
        raise InterruptedError("Control media operation cancelled") from None
    except av.H3GalleryAVGuideError:
        raise H3GalleryControlError("Control media is unavailable or changed") from None


def make_gallery_control_dispatch(path, binding, plan, *, cancel_check=None):
    assets = control_assets_from_environment()
    expected = _envelope(binding, plan)
    if expected["assets"] != assets["binding"]:
        raise H3GalleryControlError("Control asset selection changed")
    video = decode_gallery_control_source(path, expected, plan, cancel_check=cancel_check)
    dispatch = H3GalleryControlDispatch(video, expected, copy.deepcopy(plan), assets["base_checkpoint"], assets["control_checkpoint"])
    geometry = plan["geometry"]
    validate_gallery_control_dispatch(dispatch, frame_num=geometry["frame_count"], height=geometry["height"], width=geometry["width"])
    return dispatch


def validate_gallery_control_dispatch(value, *, frame_num, height, width):
    if type(value) is not H3GalleryControlDispatch:
        raise H3GalleryControlError("Private Control dispatch is invalid")
    expected = _envelope(value.source_binding, value.plan)
    assets = control_assets_from_environment()
    geometry = value.plan["geometry"]
    if (any(type(number) is not int for number in (frame_num, height, width))
            or (frame_num, height, width) != tuple(geometry[key] for key in ("frame_count", "height", "width"))
            or expected["assets"] != assets["binding"]
            or value.base_checkpoint != assets["base_checkpoint"] or value.control_checkpoint != assets["control_checkpoint"]):
        raise H3GalleryControlError("Private Control dispatch geometry or assets changed")
    if max(height, width) > 4096 or (frame_num * 12 + 36) * height * width + 8 * 1024**2 > av.MAX_DECODED_BYTES:
        raise H3GalleryControlError("Control decoded media exceeds its limit")
    import torch
    video = value.video
    if (type(video) is not torch.Tensor or video.device.type != "cpu" or not video.is_floating_point()
            or tuple(video.shape) != (1, 3, frame_num, height, width) or video.requires_grad
            or video.dtype not in (torch.float16, torch.bfloat16, torch.float32, torch.float64)
            or video.layout != torch.strided):
        raise H3GalleryControlError("Private Control requires unit-range CPU pixels")
    if (frame_num * 3 + 9) * height * width * video.element_size() + 8 * 1024**2 > av.MAX_DECODED_BYTES:
        raise H3GalleryControlError("Control decoded media exceeds its limit")
    # Bound finite-check scratch to one frame, even for the longest prefix.
    for index in range(frame_num):
        frame = video[0, :, index]
        if not torch.isfinite(frame).all().item() or frame.min().item() < 0 or frame.max().item() > 1:
            raise H3GalleryControlError("Private Control requires unit-range CPU pixels")
    return value
