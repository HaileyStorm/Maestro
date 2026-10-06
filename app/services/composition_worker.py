"""One queue-owned Blender/Editor composition delivery.

The launch adapter supplies verified manifest/owner/project bindings and owns
workspace, generation, native-execution and scene locks. No provider admission
or user path authority is granted here.
"""

from __future__ import annotations
import copy
import math
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from services.atomic_file_publish import publish_file_no_replace
from services.editor_export import render_video_sequence
from services.output_access import stamp_sidecar_policy
from services.queue_recovery_runtime import sha256_file
from services.composition_package import (
    CompositionError,
    CompositionNeedsNative,
    digest,
    identifier,
    atomic_json,
    editor_clips,
    read_receipt,
    sign_receipt,
    render_segments,
    validate_normalized_package,
)


def verify_media(path, *, frames, fps, width, height, audio, probe):
    media = probe(str(path))
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    if (
        result.stdout.strip() != str(frames)
        or media.get("type") != "video"
        or media.get("width") != width
        or media.get("height") != height
        or not math.isclose(float(media.get("fps") or 0), fps, abs_tol=1e-4)
        or abs(float(media.get("duration") or 0) - frames / fps) > 0.002
        or bool(media.get("has_audio")) != audio
        or (audio and media.get("audio_sample_rate") != 48000)
    ):
        raise CompositionError(
            "Composition media does not match its committed frame and audio clock"
        )
    return media


def _file_matches(path, descriptor):
    size, sha = sha256_file(path)
    if size != descriptor["size"] or sha != descriptor["sha256"]:
        raise CompositionError("Composition delivery bytes changed")


def _descriptor(path):
    size, sha = sha256_file(path)
    return {"size": size, "sha256": sha}


def delivery_paths(directory, output_root, binding):
    job_id = identifier(binding["job_id"])
    name = "composition_" + job_id + ".mp4"
    return [
        (Path(directory) / "assembled.mp4", Path(output_root) / name),
        (
            Path(directory) / "assembled.meta.json",
            Path(output_root) / (name[:-4] + ".meta.json"),
        ),
    ]


def load_delivery(package, directory, output_root, *, binding, secret):
    path = Path(directory) / "delivery.receipt.json"
    if not path.exists() and not path.is_symlink():
        return None
    receipt = read_receipt(path, secret)
    if (
        receipt.get("state") != "prepared"
        or receipt.get("package_sha256") != digest(package)
        or receipt.get("binding") != binding
        or set(receipt.get("files", {})) != {"media", "metadata"}
    ):
        raise CompositionError("Composition delivery binding changed")
    for role, (staged, final) in zip(
        ["media", "metadata"], delivery_paths(directory, output_root, binding)
    ):
        # An interrupted exclusive rename may leave either exact file. Both
        # names are permitted only if both independently match the sealed bytes.
        available = False
        for candidate in [staged, final]:
            if candidate.exists() or candidate.is_symlink():
                _file_matches(candidate, receipt["files"][role])
                available = True
        if not available:
            raise CompositionError("A sealed composition delivery file is missing")
    return receipt


def prepare_delivery(
    package,
    service,
    directory,
    output_root,
    *,
    binding,
    secret,
    probe,
    cancelled,
    policy,
    native_context,
    event=lambda _name: None,
    worker_identity=None,
):
    package = validate_normalized_package(package, service)
    directory = Path(directory)
    existing = load_delivery(
        package, directory, output_root, binding=binding, secret=secret
    )
    if existing is not None:
        return existing
    pairs = delivery_paths(directory, output_root, binding)
    if any(final.exists() or final.is_symlink() for _staged, final in pairs):
        raise CompositionError("Unsealed composition delivery already exists")
    for staged, _final in pairs:
        if staged.exists() or staged.is_symlink():
            # CPU assembly may be repeated. Preserve the interrupted evidence;
            # an unsealed public file never receives this exemption.
            sha256_file(staged)
            staged.rename(
                staged.with_name("interrupted-" + uuid.uuid4().hex + "-" + staged.name)
            )
    # The caller already owns the outer generation slot. Never acquire it again.
    try:
        paths, units = render_segments(
            package,
            service,
            directory,
            binding=binding,
            probe=probe,
            cancelled=cancelled,
            secret=secret,
            allow_native=False,
        )
    except CompositionNeedsNative:
        with native_context() as native_service:
            paths, units = render_segments(
                package,
                native_service or service,
                directory,
                binding=binding,
                probe=probe,
                cancelled=cancelled,
                secret=secret,
                worker_identity=worker_identity,
            )
    for segment in package["segments"]:
        animation = segment["animation"]
        verify_media(
            paths[segment["id"]],
            frames=animation["frame_end"] - animation["frame_start"] + 1,
            fps=segment["fps"],
            width=segment["width"],
            height=segment["height"],
            audio=False,
            probe=probe,
        )
    if cancelled():
        raise InterruptedError("Composition cancelled")
    event("segments_completed")
    assembled = pairs[0][0]
    render_video_sequence(
        editor_clips(package, paths),
        assembled,
        **package["canvas"],
        abort_check=cancelled,
        timeout=3600,
    )
    verify_media(
        assembled,
        frames=package["frame_count"],
        audio=True,
        probe=probe,
        **package["canvas"],
    )
    if cancelled():
        raise InterruptedError("Composition cancelled")
    sidecar = {
        "params": None,
        "generation_mode": "video",
        "tool": "editor_export",
        "artifact_class": "final",
        "job_id": binding["job_id"],
        "output_filename": pairs[0][1].name,
        "transform": {
            "kind": "blender_editor_composition",
            "schema": package["schema"],
            "package_sha256": digest(package),
            "canvas": copy.deepcopy(package["canvas"]),
            "frame_count": package["frame_count"],
            "audio": copy.deepcopy(package["audio"]),
            "clips": copy.deepcopy(package["clips"]),
            "segments": [
                {
                    "segment_id": unit["segment_id"],
                    "segment_sha256": unit["segment_sha256"],
                    "sha256": unit["sha256"],
                    "size": unit["size"],
                    "renderer_identity": copy.deepcopy((unit.get("reused_from") or {}).get("producer_binding", unit["binding"])["renderer_identity"]),
                }
                for unit in units
            ],
            "renderer_identity": copy.deepcopy(binding["renderer_identity"]),
        },
    }
    stamp_sidecar_policy(sidecar, policy, workspace=binding["workspace"])
    atomic_json(pairs[1][0], sidecar)
    receipt = {
        "state": "prepared",
        "package_sha256": digest(package),
        "binding": copy.deepcopy(binding),
        "files": {
            "media": _descriptor(assembled),
            "metadata": _descriptor(pairs[1][0]),
        },
    }
    atomic_json(directory / "delivery.receipt.json", sign_receipt(receipt, secret))
    event("delivery_sealed")
    return receipt


def publish_delivery(
    package,
    directory,
    output_root,
    *,
    binding,
    secret,
    cancelled,
    finish,
    event=lambda _name: None,
):
    receipt = load_delivery(
        package, directory, output_root, binding=binding, secret=secret
    )
    if receipt is None:
        raise CompositionError("Composition delivery is not sealed")
    pairs = delivery_paths(directory, output_root, binding)
    # Metadata precedes visible media. Every restart checks both byte seals.
    for role, index in [("metadata", 1), ("media", 0)]:
        if cancelled():
            raise InterruptedError("Composition cancelled")
        staged, final = pairs[index]
        if final.exists() or final.is_symlink():
            _file_matches(final, receipt["files"][role])
        else:
            temporary = staged.with_name(".publish-" + uuid.uuid4().hex)
            try:
                with staged.open("rb") as source, temporary.open("xb") as destination:
                    os.chmod(temporary, 0o600)
                    shutil.copyfileobj(source, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
                _file_matches(temporary, receipt["files"][role])
                publish_file_no_replace(str(temporary), str(final))
            finally:
                temporary.unlink(missing_ok=True)
        event(role + "_published")
    if cancelled():
        raise InterruptedError("Composition cancelled")
    return finish(pairs[0][1].name)


def rollback_delivery(package, directory, output_root, *, binding, secret):
    """Remove only exact authenticated files when cancellation/failure wins."""
    receipt = load_delivery(
        package, directory, output_root, binding=binding, secret=secret
    )
    if receipt is None:
        return
    for role, index in [("media", 0), ("metadata", 1)]:
        _staged, final = delivery_paths(directory, output_root, binding)[index]
        if final.exists() or final.is_symlink():
            _file_matches(final, receipt["files"][role])
            final.unlink()
    if os.name != "nt":
        fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
