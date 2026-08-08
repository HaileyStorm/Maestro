"""Comfy-Kitchen INT8 ConvRot checkpoint adapter for MiniMax H3."""

from __future__ import annotations

import json

import torch
import torch.nn as nn


class ConvRotInt8Linear(nn.Module):
    def __init__(
        self,
        in_features: int,
        out_features: int,
        *,
        bias: bool,
        output_dtype: torch.dtype,
        convrot: bool = True,
        convrot_groupsize: int = 256,
        device: torch.device | str | None = None,
    ):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.output_dtype = output_dtype
        self.convrot = convrot
        self.convrot_groupsize = convrot_groupsize
        self._lock_dtype = output_dtype
        self.weight = nn.Parameter(
            torch.empty((out_features, in_features), dtype=torch.int8, device=device),
            requires_grad=False,
        )
        self.weight_scale = nn.Parameter(
            torch.empty((out_features, 1), dtype=torch.float32, device=device),
            requires_grad=False,
        )
        self.bias = nn.Parameter(
            torch.empty(out_features, dtype=output_dtype, device=device),
            requires_grad=False,
        ) if bias else None

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        try:
            from comfy_kitchen import int8_linear
        except Exception as error:
            raise RuntimeError(
                "This H3 INT8 ConvRot checkpoint requires comfy-kitchen 0.2.26 or newer"
            ) from error
        return int8_linear(
            value,
            self.weight,
            self.weight_scale,
            self.bias,
            out_dtype=self.output_dtype,
            convrot=self.convrot,
            convrot_groupsize=self.convrot_groupsize,
        )


class W4A8ConvRotLinear(nn.Module):
    """Kijai/Comfy-Kitchen grouped W4A8 linear with a Triton fallback."""

    def __init__(
        self,
        original: nn.Linear,
        state_dict: dict[str, torch.Tensor],
        module_path: str,
        *,
        output_dtype: torch.dtype,
        group_size: int = 16,
        convrot_groupsize: int = 256,
    ):
        super().__init__()
        self.in_features = original.in_features
        self.out_features = original.out_features
        self.output_dtype = output_dtype
        self.group_size = group_size
        self.convrot_groupsize = convrot_groupsize
        self._lock_dtype = output_dtype
        device = original.weight.device

        def parameter(suffix: str, *, required: bool = True):
            key = f"{module_path}.{suffix}"
            tensor = state_dict.get(key)
            if tensor is None:
                if required:
                    raise ValueError(f"Missing W4A8 tensor: {key}")
                return None
            return nn.Parameter(
                torch.empty(tensor.shape, dtype=tensor.dtype, device=device),
                requires_grad=False,
            )

        self.weight = parameter("weight")
        self.weight_s_rel = parameter("weight_s_rel")
        self.weight_s_channel = parameter("weight_s_channel")
        self.weight_codebook = parameter("weight_codebook", required=False)
        self.weight_correction = parameter("weight_correction", required=False)
        self.bias = parameter("bias", required=False)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        try:
            from comfy_kitchen import w4a8_int8_linear
        except Exception as error:
            raise RuntimeError(
                "This H3 W4A8 checkpoint requires Maestro's pinned comfy-kitchen W4A8 runtime"
            ) from error
        return w4a8_int8_linear(
            value,
            self.weight,
            self.weight_s_rel,
            self.weight_s_channel,
            codebook=self.weight_codebook,
            correction=self.weight_correction,
            bias=self.bias,
            group_size=self.group_size,
            convrot_groupsize=self.convrot_groupsize,
            out_dtype=self.output_dtype,
        )


def _module_and_parent(root: nn.Module, path: str) -> tuple[nn.Module, str, nn.Module]:
    parent_path, _, name = path.rpartition(".")
    parent = root.get_submodule(parent_path) if parent_path else root
    return parent, name, getattr(parent, name)


def adapt_int8_convrot_state_dict(
    model: nn.Module,
    state_dict: dict[str, torch.Tensor],
    *,
    output_dtype: torch.dtype,
) -> dict[str, torch.Tensor]:
    """Replace marked linear modules before MMGP places their tensors.

    Comfy checkpoints serialize one tiny JSON descriptor per quantized
    module. The marker is configuration, not a model parameter, so it is
    consumed here and never reaches ``load_state_dict``.
    """
    w4a8_paths = sorted({
        key.removesuffix(".weight_s_rel")
        for key in state_dict
        if key.endswith(".weight_s_rel")
    })
    # Parse and validate the complete checkpoint before replacing any module.
    # ``comfy_quant`` is a shared metadata namespace used by scaled FP8,
    # NVFP4, and ConvRot—not a ConvRot discriminator by itself.
    marker_specs = []
    for marker_key in [key for key in state_dict if key.endswith(".comfy_quant")]:
        raw = state_dict[marker_key]
        try:
            descriptor = json.loads(
                bytes(raw.detach().to("cpu").to(torch.uint8).reshape(-1).tolist())
                .rstrip(b"\0")
                .decode("utf-8")
            )
        except Exception as error:
            raise ValueError(f"Invalid H3 quantization descriptor at {marker_key}") from error
        if not isinstance(descriptor, dict):
            raise ValueError(f"Invalid H3 quantization descriptor at {marker_key}")
        module_path = marker_key.removesuffix(".comfy_quant")
        format_name = str(descriptor.get("format") or "")
        if format_name not in {
            "int8_tensorwise", "float8_e4m3fn", "float8_e5m2", "nvfp4",
        }:
            raise ValueError(
                f"Unsupported H3 quantization format {format_name!r} at {module_path}"
            )
        if format_name == "int8_tensorwise":
            _parent, _name, original = _module_and_parent(model, module_path)
            if not (hasattr(original, "num_embeddings") and hasattr(original, "embedding_dim")):
                if not isinstance(original, nn.Linear):
                    raise ValueError(f"ConvRot marker targets a non-linear module: {module_path}")
                weight = state_dict.get(f"{module_path}.weight")
                scale = state_dict.get(f"{module_path}.weight_scale")
                expected = (original.out_features, original.in_features)
                if weight is None or weight.dtype != torch.int8 or tuple(weight.shape) != expected:
                    actual = None if weight is None else tuple(weight.shape)
                    raise ValueError(
                        f"H3 ConvRot weight at {module_path} has shape {actual}; expected {expected}"
                    )
                if scale is None or tuple(scale.shape) not in {
                    (original.out_features,), (original.out_features, 1), (),
                }:
                    actual = None if scale is None else tuple(scale.shape)
                    raise ValueError(
                        f"H3 ConvRot scale at {module_path} has shape {actual}; "
                        f"expected {(original.out_features, 1)}"
                    )
        marker_specs.append((marker_key, module_path, descriptor))

    for module_path in w4a8_paths:
        parent, name, original = _module_and_parent(model, module_path)
        if not isinstance(original, nn.Linear):
            raise ValueError(f"W4A8 tensors target a non-linear module: {module_path}")
        setattr(parent, name, W4A8ConvRotLinear(
            original,
            state_dict,
            module_path,
            output_dtype=output_dtype,
        ))

    for marker_key, module_path, descriptor in marker_specs:
        _parent, _name, original = _module_and_parent(model, module_path)
        # Conditioner embedding validation runs after this adapter. Preserve
        # its marker so that tensorwise scales can be expanded to the
        # row-addressable shape required by MiniMaxH3Int8Embedding and wrong
        # embedding formats fail closed there.
        if hasattr(original, "num_embeddings") and hasattr(original, "embedding_dim"):
            continue
        state_dict.pop(marker_key)
        if descriptor.get("format") != "int8_tensorwise":
            # Maestro's registered MMGP handlers identify scaled FP8 and
            # NVFP4 from the actual weight/scale tensors. The generic marker
            # is source metadata and would otherwise become an unexpected
            # model parameter.
            continue
        parent, name = _parent, _name
        replacement = ConvRotInt8Linear(
            original.in_features,
            original.out_features,
            bias=original.bias is not None,
            output_dtype=output_dtype,
            convrot=bool(descriptor.get("convrot", False)),
            convrot_groupsize=int(descriptor.get("convrot_groupsize") or 256),
            device=original.weight.device,
        )
        setattr(parent, name, replacement)
    return state_dict


__all__ = ["ConvRotInt8Linear", "W4A8ConvRotLinear", "adapt_int8_convrot_state_dict"]
