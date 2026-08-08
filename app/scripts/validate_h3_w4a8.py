"""Run a bounded finite-output check before exposing H3 W4A8."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import torch


RUNTIME_REVISION = "b812819a97ac11d01f4a3a16ba47dd38de3b2519"
MARKER = Path(sys.prefix) / ".maestro_h3_w4a8_validated.json"


def main() -> None:
    if not torch.cuda.is_available() or torch.cuda.get_device_capability(0)[0] < 8:
        raise RuntimeError("H3 W4A8 requires an NVIDIA SM80+ GPU")
    import comfy_kitchen as kitchen
    import triton

    torch.manual_seed(42)
    weight = torch.randn((256, 256), device="cuda", dtype=torch.bfloat16) * 0.02
    value = torch.randn((4, 256), device="cuda", dtype=torch.bfloat16)
    qdata, s_rel, s_channel, correction, codebook = kitchen.quantize_w4a8_int8_weight(
        weight, group_size=16, convrot_groupsize=256, codebook=True,
    )
    output = kitchen.w4a8_int8_linear(
        value, qdata, s_rel, s_channel, codebook=codebook,
        correction=correction, group_size=16, convrot_groupsize=256,
        out_dtype=torch.bfloat16,
    )
    reference = torch.nn.functional.linear(value, weight)
    if tuple(output.shape) != (4, 256) or not bool(torch.isfinite(output).all()):
        raise RuntimeError("W4A8 validation produced invalid output")
    mae = float((output - reference).abs().mean())
    reference_mean = max(float(reference.abs().mean()), 1e-8)
    relative_mae = mae / reference_mean
    if relative_mae > 0.25:
        raise RuntimeError(f"W4A8 validation error is too high ({relative_mae:.3f})")
    marker = {
        "schema_version": 1,
        "runtime_revision": RUNTIME_REVISION,
        "gpu": torch.cuda.get_device_name(0),
        "compute_capability": list(torch.cuda.get_device_capability(0)),
        "torch": str(torch.__version__),
        "triton": str(triton.__version__),
        "relative_mae": relative_mae,
        "validated_at": time.time(),
    }
    temporary = MARKER.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(marker, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, MARKER)
    print(f"H3 W4A8 validated on {marker['gpu']} (relative MAE {relative_mae:.4f})")


if __name__ == "__main__":
    main()
