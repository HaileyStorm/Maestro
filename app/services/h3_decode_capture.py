"""Opt-in, host-owned evidence for one H3 decode, outside output metadata.

The selector is an operator file under the checkout's private artifacts tree.
It contains job identity and geometry, never prompts or a public API setting.
Importing this module does not import torch or initialize a device.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess

PLAN_ENV = "MAESTRO_H3_DECODE_CAPTURE_PLAN"
STAGES = ("normalized_latents", "raw_vae", "model_output", "encoder_input")


def begin_decode_capture(observer, context):
    if observer is None:
        return
    try:
        observer.begin(context)
    except Exception:
        try:
            observer.fail("context")
        except Exception:
            pass


def notify_decode_capture(observer, stage, tensor, *, convention="model"):
    """Deliver independent CPU evidence; observation never changes generation."""
    if observer is None:
        return
    try:
        import torch
        import torch.nn.functional as functional

        if stage == "normalized_latents":
            if tensor.numel() > 64_000_000:
                raise ValueError("capture size")
            payload = {"tensor": tensor.detach().to("cpu", copy=True)}
        else:
            chunks = tensor if isinstance(tensor, (tuple, list)) else [tensor]
            frames = []
            thumbnails = []
            shapes = []
            dtypes = []
            total_frames = 0
            for chunk in chunks:
                if chunk.ndim == 5 and chunk.shape[0] == 1:
                    chunk = chunk[0]
                if chunk.ndim != 4 or chunk.shape[0] != 3:
                    raise ValueError("capture geometry")
                total_frames += chunk.shape[1]
                if total_frames > 512:
                    raise ValueError("capture size")
                shapes.append(list(chunk.shape))
                dtypes.append(str(chunk.dtype))
                for index in range(chunk.shape[1]):
                    # The learned decode already occupies substantial VRAM.
                    # Do all observation arithmetic on one independent CPU
                    # frame, so a diagnostic cannot allocate GPU workspaces.
                    raw = chunk[:, index].detach().to("cpu", copy=True).float()
                    if convention == "vae":
                        from models.minimax_h3.packing import (
                            MINIMAX_H3_PIXEL_MEAN, MINIMAX_H3_PIXEL_STD,
                        )
                        mean = raw.new_tensor(MINIMAX_H3_PIXEL_MEAN).view(3, 1, 1)
                        std = raw.new_tensor(MINIMAX_H3_PIXEL_STD).view(3, 1, 1)
                        rgb = raw * std + mean
                    elif convention == "uint8" or (convention == "encoder" and chunk.dtype == torch.uint8):
                        rgb = raw / 255
                    elif convention in {"model", "encoder"}:
                        rgb = raw.add(1).div(2)
                    else:
                        raise ValueError("capture convention")
                    clipped = rgb.clamp(0, 1)
                    small = functional.interpolate(
                        clipped[None], size=(36, 64), mode="area",
                    )[0].to("cpu")
                    frames.append({
                        "raw_min": float(raw.min()), "raw_max": float(raw.max()),
                        "rgb_mean_255": float(clipped.mean() * 255),
                        "sample_mean_255": float(small.mean() * 255),
                        "rgb_min_255": float(clipped.min() * 255),
                        "rgb_max_255": float(clipped.max() * 255),
                        "below_zero_fraction": float((rgb < 0).float().mean()),
                        "above_one_fraction": float((rgb > 1).float().mean()),
                    })
                    thumbnails.append(small.mul(255).to(torch.uint8).permute(1, 2, 0).numpy())
            payload = {
                "shapes": shapes, "dtypes": dtypes, "convention": convention,
                "resampler": "torch-area-64x36", "frames": frames,
                "thumbnails": thumbnails,
            }
        observer(stage, payload)
    except Exception:
        try:
            observer.fail(stage)
        except Exception:
            pass


class H3DecodeCapture:
    def __init__(self, directory: Path, identity: dict):
        self.directory = directory
        self.manifest = {"schema_version": 1, "identity": identity, "stages": {},
                         "errors": [], "complete": False}
        self._write()

    def _write(self):
        temporary = self.directory / "manifest.json.tmp"
        temporary.write_text(json.dumps(self.manifest, indent=2, allow_nan=False))
        temporary.replace(self.directory / "manifest.json")

    def fail(self, stage):
        self.manifest["errors"].append({"stage": stage, "code": "capture_failed"})
        self.manifest["complete"] = False
        self._write()

    def begin(self, context):
        identity = self.manifest["identity"]
        if "execution" in self.manifest or context["repeat_index"] != 0 or context["window_index"] != 1:
            raise ValueError("capture execution")
        if context["seed"] != identity["seed"] or context["frames"] != identity["video_length"]:
            raise ValueError("capture execution")
        if f'{context["width"]}x{context["height"]}' != identity["resolution"]:
            raise ValueError("capture execution")
        self.manifest["execution"] = dict(context)
        self._write()

    def __call__(self, stage, payload):
        if stage not in STAGES or stage in self.manifest["stages"]:
            raise ValueError("capture stage")
        if tuple(self.manifest["stages"]) != STAGES[:STAGES.index(stage)]:
            raise ValueError("capture order")
        if stage == "normalized_latents":
            import torch
            tensor = payload["tensor"]
            torch.save(tensor, self.directory / "normalized_latents.pt")
            evidence = {"shape": list(tensor.shape), "dtype": str(tensor.dtype),
                        "device": str(tensor.device), "file": "normalized_latents.pt"}
        else:
            from PIL import Image
            thumbs = payload["thumbnails"]
            # A bounded contact sheet records every frame, without full media.
            sheet = Image.new("RGB", (64 * 12, 36 * ((len(thumbs) + 11) // 12)))
            for index, frame in enumerate(thumbs):
                sheet.paste(Image.fromarray(frame), ((index % 12) * 64, (index // 12) * 36))
            sheet.save(self.directory / f"{stage}.png")
            evidence = {key: value for key, value in payload.items() if key != "thumbnails"}
            evidence["preview"] = f"{stage}.png"
        self.manifest["stages"][stage] = evidence
        self._write()

    def finalize(self):
        self.manifest["complete"] = (
            "execution" in self.manifest
            and tuple(self.manifest["stages"]) == STAGES and not self.manifest["errors"]
        )
        self._write()


def capture_for_job(*, root: Path, job_id: str, task_index: int, params: dict,
                    environ=None):
    """An exact operator-selected single output; all ordinary jobs return None."""
    raw = (os.environ if environ is None else environ).get(PLAN_ENV)
    if not raw:
        return None
    try:
        artifacts = (root / ".artifacts-temp").resolve(strict=True)
        plan_path = Path(raw).resolve(strict=True)
        if not plan_path.is_relative_to(artifacts) or plan_path.stat().st_size > 8192:
            return None
        plan = json.loads(plan_path.read_text())
        if set(plan) != {"schema_version", "match", "directory", "revision"} or plan["schema_version"] != 1:
            return None
        identity = {"job_id": job_id, "task_index": task_index}
        identity.update({key: params.get(key) for key in (
            "model_type", "seed", "resolution", "video_length", "num_inference_steps",
        )})
        if identity != plan["match"] or identity["model_type"] not in {"minimax_h3", "minimax_h3_ref2va"}:
            return None
        if params.get("repeat_generation", 1) != 1 or params.get("batch_size", 1) != 1:
            return None
        if params.get("video_source") or params.get("_h3_native_boundary") or params.get("_h3_source_audio_premux_recovery"):
            return None
        if params.get("video_length", 0) > params.get("sliding_window_size", 124):
            return None
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if revision != plan["revision"]:
            return None
        directory = (root / plan["directory"]).resolve()
        if not directory.is_relative_to(artifacts) or directory == artifacts:
            return None
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        source_files = (
            "app/models/minimax_h3/minimax_h3_main.py",
            "app/models/minimax_h3/video_vae.py", "app/models/minimax_h3/packing.py",
            "app/services/h3_decode_capture.py", "app/wgp.py", "app/launch.py",
        )
        source_digests = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in source_files if (root / name).is_file()
        }
        return H3DecodeCapture(directory, {
            **identity, "revision": revision, "source_sha256": source_digests,
            "repeat_index": 0, "window_index": 1,
        })
    except Exception:
        # An invalid selector must not divert or fail an ordinary generation.
        return None
