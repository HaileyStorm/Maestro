"""Bounded ordered Gallery H3 still sources and exact replay validation."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import os
import stat
import warnings
from typing import Any, Callable, Mapping

from services.h3_guide_plan import (
    H3GuidePlanError,
    plan_h3_guide_inputs,
    validate_h3_guide_plan,
)


H3_GALLERY_STILL_GUIDE_SOURCE_KEY = "_h3_timeline_still_guide_source"
H3_GALLERY_STILL_GUIDE_PLAN_KEY = "_h3_timeline_still_guide_plan"
H3_GALLERY_STILL_GUIDE_CUSTOM_KEY = "_h3_timeline_still_guide"
H3_GALLERY_STILL_GUIDE_MAX_BYTES = 64 * 1024 * 1024
H3_GALLERY_STILL_GUIDE_MAX_SOURCES = 8
H3_GALLERY_STILL_GUIDE_MAX_TOTAL_BYTES = H3_GALLERY_STILL_GUIDE_MAX_SOURCES * H3_GALLERY_STILL_GUIDE_MAX_BYTES
H3_GALLERY_STILL_GUIDE_MAX_PIXELS = 100_000_000
H3_GALLERY_STILL_GUIDE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_FORMATS_BY_EXTENSION = {
    ".png": {"PNG"},
    ".jpg": {"JPEG"},
    ".jpeg": {"JPEG"},
    ".webp": {"WEBP"},
}
_SOURCE_FIELDS = frozenset({
    "workspace", "name", "revision", "sha256", "size", "width",
    "height", "frame_index", "target_frames", "plan_sha256",
    "source_private", "source_explicit",
})
_FORBIDDEN_GUIDE_INPUTS = (
    "image_end", "image_refs", "image_guide", "image_mask",
    "video_guide", "video_guide2", "video_guide3", "video_mask",
    "video_source", "video_end", "audio_guide", "audio_guide2",
    "audio_guide3", "audio_guide4", "audio_guide5", "audio_guide6",
    "audio_conditioning_guide", "audio_source", "input_waveform",
    "audio_path", "custom_guide", "voice_reference", "voice_clone_refs",
    "reference_image_path", "character_ref_paths", "location_ref_paths",
)


class H3GalleryStillGuideError(ValueError):
    """A selected Gallery still or its durable guide binding is invalid."""


@dataclass(frozen=True)
class GalleryStillProbe:
    sha256: str
    size: int
    width: int
    height: int


def probe_gallery_still(path: str) -> GalleryStillProbe:
    """Hash and decode one bounded, static PNG/JPEG/WebP file."""
    if not isinstance(path, str) or not path:
        raise H3GalleryStillGuideError("Selected still is unavailable")
    extension = os.path.splitext(path)[1].lower()
    if extension not in H3_GALLERY_STILL_GUIDE_EXTENSIONS:
        raise H3GalleryStillGuideError("Selected Gallery item is not a supported still")

    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size > H3_GALLERY_STILL_GUIDE_MAX_BYTES:
            raise H3GalleryStillGuideError("Selected still exceeds the size limit")
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
            or opened.st_size > H3_GALLERY_STILL_GUIDE_MAX_BYTES
        ):
            raise H3GalleryStillGuideError("Selected still changed while it was read")
        data = bytearray()
        while len(data) <= H3_GALLERY_STILL_GUIDE_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, H3_GALLERY_STILL_GUIDE_MAX_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(data) > H3_GALLERY_STILL_GUIDE_MAX_BYTES
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or after.st_size != opened.st_size
            or len(data) != opened.st_size
        ):
            raise H3GalleryStillGuideError("Selected still changed while it was read")
    except H3GalleryStillGuideError:
        raise
    except OSError as error:
        raise H3GalleryStillGuideError("Selected still is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = str(image.format or "").upper()
                if image_format not in _FORMATS_BY_EXTENSION[extension]:
                    raise H3GalleryStillGuideError(
                        "Selected file format does not match its Gallery type",
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise H3GalleryStillGuideError(
                        "Animated Gallery images cannot be used as a still",
                    )
                width, height = image.size
                if (
                    type(width) is not int
                    or type(height) is not int
                    or width < 1
                    or height < 1
                    or width * height > H3_GALLERY_STILL_GUIDE_MAX_PIXELS
                ):
                    raise H3GalleryStillGuideError(
                        "Selected still exceeds the pixel limit",
                    )
                image.verify()
            with Image.open(BytesIO(data)) as decoded:
                decoded.load()
                if decoded.size != (width, height):
                    raise H3GalleryStillGuideError(
                        "Selected still changed while it was decoded",
                    )
    except H3GalleryStillGuideError:
        raise
    except Exception as error:
        raise H3GalleryStillGuideError("Selected Gallery item is not a readable still") from error

    return GalleryStillProbe(
        sha256="sha256:" + hashlib.sha256(data).hexdigest(),
        size=len(data),
        width=width,
        height=height,
    )


def build_gallery_still_guide_plan(
    *,
    sha256: str,
    frame_index: int,
    target_frames: int,
) -> dict[str, Any]:
    """Build the existing inert one-visual H3 guide commitment."""
    if type(frame_index) is not int:
        raise H3GalleryStillGuideError("Guide frame must be an integer")
    if type(target_frames) is not int or not 0 < frame_index < target_frames - 1:
        raise H3GalleryStillGuideError("Guide frame must be inside the target video")
    try:
        plan = plan_h3_guide_inputs(
            target_frames,
            [{
                "frame_idx": frame_index,
                "visual": {"sha256": sha256, "count": 1},
                "audio": None,
            }],
            conditioning_family="fl2va_timeline",
        )
        validated = validate_h3_guide_plan(plan)
    except (H3GuidePlanError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Guide plan is invalid") from error
    if (
        not isinstance(validated, dict)
        or len(validated.get("guides") or []) != 1
        or validated.get("execution_available") is not False
        or validated.get("automatic_fallback") is not False
        or validated.get("continuation_composition_available") is not False
        or validated["guides"][0].get("resolved_frame_idx") != frame_index
        or validated["guides"][0].get("audio") is not None
    ):
        raise H3GalleryStillGuideError("Guide plan is outside the still-only contract")
    return validated


def make_gallery_still_guide_source(
    *,
    workspace: str,
    name: str,
    revision: str,
    probe: GalleryStillProbe,
    frame_index: int,
    target_frames: int,
    plan: Mapping[str, Any],
    source_private: bool,
    source_explicit: bool,
) -> dict[str, Any]:
    """Create the exact durable source commitment stored in request params."""
    validated_plan = validate_h3_guide_plan(dict(plan))
    if (
        not isinstance(workspace, str)
        or not workspace
        or not isinstance(name, str)
        or not name
        or not isinstance(revision, str)
        or not revision
        or validated_plan.get("target_frames") != target_frames
        or len(validated_plan.get("guides") or []) != 1
        or validated_plan["guides"][0].get("resolved_frame_idx") != frame_index
        or validated_plan["guides"][0].get("visual", {}).get("sha256") != probe.sha256
        or type(source_private) is not bool
        or type(source_explicit) is not bool
    ):
        raise H3GalleryStillGuideError("Gallery still commitment is inconsistent")
    return {
        "workspace": workspace,
        "name": name,
        "revision": revision,
        "sha256": probe.sha256,
        "size": probe.size,
        "width": probe.width,
        "height": probe.height,
        "frame_index": frame_index,
        "target_frames": target_frames,
        "plan_sha256": validated_plan["plan_sha256"],
        "source_private": source_private,
        "source_explicit": source_explicit,
    }


def build_gallery_still_guide_pair_plan(
    *, sha256: str, frame_index: int, second_sha256: str,
    second_frame_index: int, target_frames: int,
) -> dict[str, Any]:
    """Seal two stills in Picture order at distinct interior target frames."""
    for digest, index in ((sha256, frame_index), (second_sha256, second_frame_index)):
        build_gallery_still_guide_plan(
            sha256=digest, frame_index=index, target_frames=target_frames,
        )
    if frame_index == second_frame_index:
        raise H3GalleryStillGuideError("Guide frames must be distinct")
    try:
        return plan_h3_guide_inputs(
            target_frames,
            [{"frame_idx": index, "visual": {"sha256": digest, "count": 1}, "audio": None}
             for digest, index in ((sha256, frame_index), (second_sha256, second_frame_index))],
            conditioning_family="fl2va_timeline",
        )
    except (H3GuidePlanError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Guide plan is invalid") from error


def make_gallery_still_guide_pair_source(
    first: Mapping[str, Any], second: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind both independently authorized source records to their joint plan."""
    if (
        not isinstance(first, Mapping) or set(first) != _SOURCE_FIELDS
        or not isinstance(second, Mapping) or set(second) != _SOURCE_FIELDS
        or first["workspace"] != second["workspace"]
        or first["name"] == second["name"]
        or first["target_frames"] != second["target_frames"]
    ):
        raise H3GalleryStillGuideError("Guide sources are inconsistent")
    expected = build_gallery_still_guide_pair_plan(
        sha256=first["sha256"], frame_index=first["frame_index"],
        second_sha256=second["sha256"], second_frame_index=second["frame_index"],
        target_frames=first["target_frames"],
    )
    if validate_h3_guide_plan(dict(plan)) != expected:
        raise H3GalleryStillGuideError("Guide plan does not match both selected stills")
    return {
        **dict(first), "plan_sha256": expected["plan_sha256"],
        "second_source": {**dict(second), "plan_sha256": expected["plan_sha256"]},
    }



def build_gallery_still_guide_multiple_plan(sources: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Seal one through eight source records in Picture order, never time-sort them."""
    if (
        type(sources) is not list or not 1 <= len(sources) <= H3_GALLERY_STILL_GUIDE_MAX_SOURCES
        or any(not isinstance(item, Mapping) or set(item) != _SOURCE_FIELDS for item in sources)
        or any(
            not isinstance(item["name"], str) or not item["name"]
            or not isinstance(item["workspace"], str) or not item["workspace"]
            or type(item["frame_index"]) is not int
            or type(item["target_frames"]) is not int
            for item in sources
        )
        or len({item["name"] for item in sources}) != len(sources)
        or len({item["frame_index"] for item in sources}) != len(sources)
        or any(item["workspace"] != sources[0]["workspace"] or item["target_frames"] != sources[0]["target_frames"] for item in sources)
    ):
        raise H3GalleryStillGuideError("Guide sources must be distinct and bound to one target")
    for item in sources:
        build_gallery_still_guide_plan(sha256=item["sha256"], frame_index=item["frame_index"], target_frames=item["target_frames"])
    try:
        return plan_h3_guide_inputs(
            sources[0]["target_frames"],
            [{"frame_idx": item["frame_index"], "visual": {"sha256": item["sha256"], "count": 1}, "audio": None} for item in sources],
            conditioning_family="fl2va_timeline",
        )
    except (H3GuidePlanError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Guide plan is invalid") from error


def _validate_plain_still_sources(sources: Any) -> None:
    if (
        type(sources) is not list
        or not 1 <= len(sources) <= H3_GALLERY_STILL_GUIDE_MAX_SOURCES
        or any(type(item) is not dict or set(item) != _SOURCE_FIELDS for item in sources)
    ):
        raise H3GalleryStillGuideError("Guide sources must contain one through eight exact records")
    for item in sources:
        name = item["name"]
        if (
            any(type(item[field]) is not str or not item[field]
                for field in ("workspace", "name", "revision"))
            or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name
            or os.path.splitext(name)[1].lower() not in H3_GALLERY_STILL_GUIDE_EXTENSIONS
            or any(type(item[field]) is not str or len(item[field]) != 71
                   or not item[field].startswith("sha256:")
                   or any(ch not in "0123456789abcdef" for ch in item[field][7:])
                   for field in ("sha256", "plan_sha256"))
            or type(item["size"]) is not int
            or not 1 <= item["size"] <= H3_GALLERY_STILL_GUIDE_MAX_BYTES
            or type(item["width"]) is not int or item["width"] < 1
            or type(item["height"]) is not int or item["height"] < 1
            or item["width"] * item["height"] > H3_GALLERY_STILL_GUIDE_MAX_PIXELS
            or type(item["source_private"]) is not bool
            or type(item["source_explicit"]) is not bool
        ):
            raise H3GalleryStillGuideError("Guide source commitment is invalid")
    if sum(item["size"] for item in sources) > H3_GALLERY_STILL_GUIDE_MAX_TOTAL_BYTES:
        raise H3GalleryStillGuideError("Selected stills exceed the total size limit")


def make_gallery_still_guide_sources(
    sources: list[dict[str, Any]], plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind an ordered bounded source list to one shared guide commitment."""
    _validate_plain_still_sources(sources)
    expected = build_gallery_still_guide_multiple_plan(sources)
    try:
        validated = validate_h3_guide_plan(dict(plan))
    except (H3GuidePlanError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Guide plan is invalid") from error
    if validated != expected:
        raise H3GalleryStillGuideError("Guide plan does not match all selected stills")
    return {
        "sources": [{**item, "plan_sha256": expected["plan_sha256"]} for item in sources],
        "plan_sha256": expected["plan_sha256"],
    }


def make_gallery_still_guide_triple_source(
    first: Mapping[str, Any], second: Mapping[str, Any], third: Mapping[str, Any], plan: Mapping[str, Any],
) -> dict[str, Any]:
    expected = build_gallery_still_guide_multiple_plan([first, second, third])
    if validate_h3_guide_plan(dict(plan)) != expected:
        raise H3GalleryStillGuideError("Guide plan does not match the three selected stills")
    records = [{**dict(item), "plan_sha256": expected["plan_sha256"]} for item in (first, second, third)]
    return {**records[0], "second_source": records[1], "third_source": records[2]}


def validate_gallery_still_guide_job(
    params: Mapping[str, Any],
    *,
    workspace: str,
    out_dir: str,
    safe_direct_file_under: Callable[[str, str], str | None],
    output_revision: Callable[[str, str, str], str],
    load_sidecars: Callable[[str, set[str]], Mapping[str, Any]],
    classify_artifacts: Callable[[list[dict[str, Any]]], Mapping[str, str]],
    integrity_pending: Callable[[str, str], bool],
    job_private: bool,
    job_explicit: bool,
) -> dict[str, Any]:
    """Revalidate the exact Gallery revision and plan before execution/recovery."""
    if not isinstance(params, Mapping):
        raise H3GalleryStillGuideError("Guide request is invalid")
    source = params.get(H3_GALLERY_STILL_GUIDE_SOURCE_KEY)
    if isinstance(source, Mapping) and "sources" in source:
        return _validate_gallery_still_guide_sources_job(
            params, workspace=workspace, out_dir=out_dir,
            safe_direct_file_under=safe_direct_file_under,
            output_revision=output_revision, load_sidecars=load_sidecars,
            classify_artifacts=classify_artifacts, integrity_pending=integrity_pending,
            job_private=job_private, job_explicit=job_explicit,
        )
    if isinstance(source, Mapping) and ("second_source" in source or "third_source" in source):
        return _validate_gallery_still_guide_pair_job(
            params, workspace=workspace, out_dir=out_dir,
            safe_direct_file_under=safe_direct_file_under,
            output_revision=output_revision, load_sidecars=load_sidecars,
            classify_artifacts=classify_artifacts, integrity_pending=integrity_pending,
            job_private=job_private, job_explicit=job_explicit,
        )
    plan = params.get(H3_GALLERY_STILL_GUIDE_PLAN_KEY)
    custom = params.get("custom_settings")
    private_setting = (
        custom.get(H3_GALLERY_STILL_GUIDE_CUSTOM_KEY)
        if isinstance(custom, Mapping) else None
    )
    if (
        not isinstance(source, Mapping)
        or set(source) != _SOURCE_FIELDS
        or not isinstance(plan, Mapping)
        or type(private_setting) is not dict
        or set(private_setting) != {"frame_index"}
    ):
        raise H3GalleryStillGuideError("Guide request binding is missing")
    source = dict(source)
    if (
        type(source["workspace"]) is not str
        or source["workspace"] != workspace
        or type(source["name"]) is not str
        or not source["name"]
        or type(source["revision"]) is not str
        or not source["revision"]
        or type(source["sha256"]) is not str
        or len(source["sha256"]) != 71
        or not source["sha256"].startswith("sha256:")
        or any(ch not in "0123456789abcdef" for ch in source["sha256"][7:])
        or type(source["size"]) is not int
        or not 1 <= source["size"] <= H3_GALLERY_STILL_GUIDE_MAX_BYTES
        or type(source["width"]) is not int
        or type(source["height"]) is not int
        or source["width"] < 1
        or source["height"] < 1
        or source["width"] * source["height"] > H3_GALLERY_STILL_GUIDE_MAX_PIXELS
        or type(source["frame_index"]) is not int
        or type(source["target_frames"]) is not int
        or type(source["plan_sha256"]) is not str
        or type(source["source_private"]) is not bool
        or type(source["source_explicit"]) is not bool
        or (source["source_private"] and job_private is not True)
        or (source["source_explicit"] and job_explicit is not True)
        or type(private_setting["frame_index"]) is not int
        or private_setting["frame_index"] != source["frame_index"]
        or source["target_frames"] != params.get("video_length")
        or source["target_frames"] < 124
        or source["target_frames"] > 345
        or (source["target_frames"] - 5) % 17 != 0
        or not 0 < source["frame_index"] < source["target_frames"] - 1
        or str(params.get("model_type") or "") != "minimax_h3"
        or str(params.get("generation_mode") or "video") != "video"
        or any(params.get(key) for key in _FORBIDDEN_GUIDE_INPUTS)
        or str(params.get("video_prompt_type") or "")
        or str(params.get("audio_prompt_type") or "")
        or str((custom.get("h3_source_audio_mode") if isinstance(custom, Mapping) else "native") or "native").strip().lower() != "native"
        or params.get("h3_native_boundary_conditioning") is True
        or (isinstance(custom, Mapping) and custom.get("h3_native_boundary_conditioning") is True)
        or params.get("trim_tail_frames", 0) != 0
        or params.get("multi_prompts_gen_type") == 3
        or params.get("multi_clip_info") not in (None, "", [])
        or params.get("_h3_longform") not in (None, "", {})
    ):
        raise H3GalleryStillGuideError("Guide request is outside the still-only contract")
    if (
        os.path.splitext(source["name"])[1].lower()
        not in H3_GALLERY_STILL_GUIDE_EXTENSIONS
        or params.get("image_start") is None
    ):
        raise H3GalleryStillGuideError("Guide source is not a Gallery still")

    try:
        validated_plan = validate_h3_guide_plan(dict(plan))
    except (H3GuidePlanError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Guide plan is invalid") from error
    if (
        validated_plan.get("plan_sha256") != source["plan_sha256"]
        or validated_plan.get("target_frames") != source["target_frames"]
        or len(validated_plan.get("guides") or []) != 1
        or validated_plan["guides"][0].get("resolved_frame_idx") != source["frame_index"]
        or validated_plan["guides"][0].get("visual", {}).get("sha256") != source["sha256"]
        or validated_plan["guides"][0].get("audio") is not None
        or validated_plan.get("execution_available") is not False
        or validated_plan.get("automatic_fallback") is not False
        or validated_plan.get("continuation_composition_available") is not False
    ):
        raise H3GalleryStillGuideError("Guide plan does not match the selected still")

    try:
        path = safe_direct_file_under(out_dir, source["name"])
        if not path or os.path.realpath(path) != os.path.realpath(str(params["image_start"])):
            raise H3GalleryStillGuideError("Guide source path changed")
        if integrity_pending(out_dir, source["name"]):
            raise H3GalleryStillGuideError("Gallery still is not ready for use")
        if output_revision(path, out_dir, source["name"]) != source["revision"]:
            raise H3GalleryStillGuideError("Gallery still revision changed")
        sidecar = load_sidecars(out_dir, {source["name"]}).get(source["name"])
        if (
            not isinstance(sidecar, Mapping)
            or sidecar.get("workspace") != workspace
            or type(sidecar.get("private", False)) is not bool
            or type(sidecar.get("explicit", False)) is not bool
            or sidecar.get("private", False) is not source["source_private"]
            or sidecar.get("explicit", False) is not source["source_explicit"]
        ):
            raise H3GalleryStillGuideError("Gallery still project binding changed")
        st = os.stat(path, follow_symlinks=False)
        artifact = {
            "name": source["name"],
            "meta": dict(sidecar),
            "size": st.st_size,
            "created_at": st.st_mtime,
        }
        if classify_artifacts([artifact]).get(source["name"]) != "final":
            raise H3GalleryStillGuideError("Selected Gallery item is not a final still")
        probe = probe_gallery_still(path)
    except H3GalleryStillGuideError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Gallery still could not be revalidated") from error
    if (
        probe.sha256 != source["sha256"]
        or probe.size != source["size"]
        or probe.width != source["width"]
        or probe.height != source["height"]
        or output_revision(path, out_dir, source["name"]) != source["revision"]
    ):
        raise H3GalleryStillGuideError("Gallery still changed after it was selected")
    return {
        "workspace": workspace,
        "name": source["name"],
        "revision": source["revision"],
        "sha256": source["sha256"],
        "frame_index": source["frame_index"],
        "target_frames": source["target_frames"],
        "plan_sha256": source["plan_sha256"],
    }


def _validate_gallery_still_guide_pair_job(
    params: Mapping[str, Any], **validation: Any,
) -> dict[str, Any]:
    """Revalidate every source through the exact one-still replay gate."""
    source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
    tripled = "third_source" in source
    extra_fields = {"second_source"} | ({"third_source"} if tripled else set())
    if set(source) != _SOURCE_FIELDS | extra_fields:
        raise H3GalleryStillGuideError("Guide request binding is invalid")
    first = {key: value for key, value in source.items() if key not in extra_fields}
    records = [first, source["second_source"]] + ([source["third_source"]] if tripled else [])
    custom = params.get("custom_settings")
    setting = custom.get(H3_GALLERY_STILL_GUIDE_CUSTOM_KEY) if isinstance(custom, Mapping) else None
    fields = {"frame_index", "end_frame_index"} | ({"third_frame_index", "third_still_path"} if tripled else set())
    if (
        any(not isinstance(item, Mapping) or set(item) != _SOURCE_FIELDS for item in records)
        or type(setting) is not dict or set(setting) != fields
        or not isinstance(params.get("image_end"), (str, os.PathLike))
        or (tripled and (type(setting["third_still_path"]) is not str or not setting["third_still_path"]))
    ):
        raise H3GalleryStillGuideError("Guide request does not bind all selected stills")
    keys = ["frame_index", "end_frame_index"] + (["third_frame_index"] if tripled else [])
    if any(type(setting[key]) is not int or setting[key] != item["frame_index"] for key, item in zip(keys, records)):
        raise H3GalleryStillGuideError("Guide frame positions changed")
    try:
        expected = build_gallery_still_guide_multiple_plan(records)
        plan = validate_h3_guide_plan(params.get(H3_GALLERY_STILL_GUIDE_PLAN_KEY))
    except (H3GuidePlanError, TypeError, ValueError) as error:
        raise H3GalleryStillGuideError("Guide plan is invalid") from error
    if plan != expected or any(item["plan_sha256"] != plan["plan_sha256"] for item in records):
        raise H3GalleryStillGuideError("Guide plan does not match all selected stills")
    paths = [params.get("image_start"), params["image_end"]] + ([setting["third_still_path"]] if tripled else [])
    results = []
    for item, path in zip(records, paths):
        single_plan = build_gallery_still_guide_plan(
            sha256=item["sha256"], frame_index=item["frame_index"], target_frames=item["target_frames"],
        )
        single_params = {
            **dict(params), "image_start": path, "image_end": None,
            H3_GALLERY_STILL_GUIDE_SOURCE_KEY: {**dict(item), "plan_sha256": single_plan["plan_sha256"]},
            H3_GALLERY_STILL_GUIDE_PLAN_KEY: single_plan,
            "custom_settings": {**dict(custom), H3_GALLERY_STILL_GUIDE_CUSTOM_KEY: {"frame_index": item["frame_index"]}},
        }
        results.append(validate_gallery_still_guide_job(single_params, **validation))
    return {
        **results[0], "plan_sha256": plan["plan_sha256"], "guide_count": len(results),
        "frame_indices": [item["frame_index"] for item in results], "sources": results,
    }


def _validate_gallery_still_guide_sources_job(
    params: Mapping[str, Any], **validation: Any,
) -> dict[str, Any]:
    source = params[H3_GALLERY_STILL_GUIDE_SOURCE_KEY]
    if type(source) is not dict or set(source) != {"sources", "plan_sha256"}:
        raise H3GalleryStillGuideError("Guide request binding is invalid")
    records = source["sources"]
    _validate_plain_still_sources(records)
    custom = params.get("custom_settings")
    setting = custom.get(H3_GALLERY_STILL_GUIDE_CUSTOM_KEY) if isinstance(custom, Mapping) else None
    if (
        type(setting) is not dict or set(setting) != {"frame_indices", "additional_still_paths"}
        or type(setting["frame_indices"]) is not list
        or len(setting["frame_indices"]) != len(records)
        or any(type(index) is not int or index != record["frame_index"]
               for index, record in zip(setting["frame_indices"], records))
        or type(setting["additional_still_paths"]) is not list
        or len(setting["additional_still_paths"]) != len(records) - 1
        or any(type(path) is not str or not path for path in setting["additional_still_paths"])
        or type(params.get("image_start")) is not str or not params["image_start"]
        or params.get("image_end") is not None
    ):
        raise H3GalleryStillGuideError("Guide request does not bind every selected still")
    expected = make_gallery_still_guide_sources(records, params.get(H3_GALLERY_STILL_GUIDE_PLAN_KEY))
    if source != expected:
        raise H3GalleryStillGuideError("Guide plan does not match all source commitments")
    results = []
    paths = [params["image_start"], *setting["additional_still_paths"]]
    for record, path in zip(records, paths):
        single_plan = build_gallery_still_guide_plan(
            sha256=record["sha256"], frame_index=record["frame_index"], target_frames=record["target_frames"],
        )
        single_params = {
            **dict(params), "image_start": path, "image_end": None,
            H3_GALLERY_STILL_GUIDE_SOURCE_KEY: {**record, "plan_sha256": single_plan["plan_sha256"]},
            H3_GALLERY_STILL_GUIDE_PLAN_KEY: single_plan,
            "custom_settings": {**dict(custom), H3_GALLERY_STILL_GUIDE_CUSTOM_KEY: {"frame_index": record["frame_index"]}},
        }
        results.append(validate_gallery_still_guide_job(single_params, **validation))
    return {
        **results[0], "plan_sha256": source["plan_sha256"], "guide_count": len(results),
        "frame_indices": [result["frame_index"] for result in results], "sources": results,
    }


__all__ = [
    "GalleryStillProbe",
    "H3_GALLERY_STILL_GUIDE_CUSTOM_KEY",
    "H3_GALLERY_STILL_GUIDE_EXTENSIONS",
    "H3_GALLERY_STILL_GUIDE_MAX_SOURCES",
    "H3_GALLERY_STILL_GUIDE_MAX_TOTAL_BYTES",
    "H3_GALLERY_STILL_GUIDE_PLAN_KEY",
    "H3_GALLERY_STILL_GUIDE_SOURCE_KEY",
    "H3GalleryStillGuideError",
    "build_gallery_still_guide_plan",
    "build_gallery_still_guide_pair_plan",
    "build_gallery_still_guide_multiple_plan",
    "make_gallery_still_guide_triple_source",
    "make_gallery_still_guide_pair_source",
    "make_gallery_still_guide_source",
    "make_gallery_still_guide_sources",
    "probe_gallery_still",
    "validate_gallery_still_guide_job",
]
