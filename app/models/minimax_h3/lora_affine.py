"""Strict ordinary-LoRA compatibility for MiniMax H3.

The affine packages and conversion math are derived from the official Wan2GP
changes pinned in ``UPSTREAM.md``.  This adapter deliberately normalizes only
ordinary user LoRAs; Maestro's managed Turbo adapter has its own exact validator
and never passes through this module.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import torch
from safetensors.torch import load_file

FULL_TIME_DIM = 2688
_PRUNED_WIDTHS = (4, 8, 64)
_SUPPORTED_WIDTHS = (*_PRUNED_WIDTHS, FULL_TIME_DIM)
_MAP_DIR = Path(__file__).with_name("lora_affine_maps")

# Keep this list explicit.  A newly named H3 profile must declare which
# conditioning architecture authored its affine table rather than being
# guessed from a substring in a user-controlled model name.
_ARCHITECTURES = {
    "minimax_h3": "fl2va",
    "minimax_h3_fl2va": "fl2va",
    "minimax_h3_fl2va_pruned": "fl2va",
    "minimax_h3_w4a8_fl2va": "fl2va",
    "minimax_h3_pinkcherry_fl2va": "fl2va",
    "minimax_h3_ref2va": "ref2va",
    "minimax_h3_ref2va_pruned": "ref2va",
}

_WRAPPER_PREFIXES = (
    "base_model.model.",
    "diffusion_model.",
    "transformer.",
    "model.",
)

_TOP_LEVEL_ALIASES = (
    ("token_refiner.refiner_blocks.", "token_refiner.blocks."),
    ("transformer_blocks.", "blocks."),
    ("time_embedder.linear_1.", "time_embedder.proj_in."),
    ("time_embedder.linear_2.", "time_embedder.proj_out."),
    ("audio_proj_in.", "audio_patch_proj."),
    ("proj_in.", "video_patch_proj."),
    ("context_embedder.", "condition_proj."),
    ("norm_out.norm.", "final_layer.norm."),
    ("norm_out.linear.", "final_layer.adaln_proj.linear."),
    ("audio_proj_out.", "final_layer.audio_out."),
    ("proj_out.", "final_layer.video_out."),
)

_INNER_ALIASES = (
    (".attn.norm_q", ".attn.q_norm"),
    (".attn.norm_k", ".attn.k_norm"),
    (".attn.to_out.0", ".attn.out_proj"),
    (".attn.to_q", ".attn.q_proj"),
    (".attn.to_k", ".attn.k_proj"),
    (".attn.to_v", ".attn.v_proj"),
    (".ff.net.0.proj", ".mlp.fc1"),
    (".ff.net.2", ".mlp.fc2"),
)

# Ordered longest-first so PEFT ``.default`` keys cannot be consumed by a
# shorter suffix.  All factors leave this module in MMGP's canonical syntax.
_FACTOR_SUFFIXES = (
    (".lora_A.default.weight", "A", "peft_default"),
    (".lora_B.default.weight", "B", "peft_default"),
    (".lora_down.default.weight", "A", "kohya_default"),
    (".lora_up.default.weight", "B", "kohya_default"),
    (".lora.A.default.weight", "A", "peft_dotted_default"),
    (".lora.B.default.weight", "B", "peft_dotted_default"),
    (".lora.down.default.weight", "A", "kohya_dotted_default"),
    (".lora.up.default.weight", "B", "kohya_dotted_default"),
    (".lora_A.weight", "A", "peft"),
    (".lora_B.weight", "B", "peft"),
    (".lora_down.weight", "A", "kohya"),
    (".lora_up.weight", "B", "kohya"),
    (".lora.A.weight", "A", "peft_dotted"),
    (".lora.B.weight", "B", "peft_dotted"),
    (".lora.down.weight", "A", "kohya_dotted"),
    (".lora.up.weight", "B", "kohya_dotted"),
)


@dataclass(frozen=True)
class LoraModuleSpec:
    out_features: int
    in_features: int
    has_bias: bool


def _architecture(model_type: str) -> str:
    try:
        return _ARCHITECTURES[str(model_type)]
    except KeyError as error:
        raise ValueError(
            "Unsupported MiniMax H3 architecture for ordinary LoRA conversion: "
            f"{model_type!r}"
        ) from error


@lru_cache(maxsize=6)
def _load_affine_package(architecture: str, width: int = 8) -> tuple[torch.Tensor, torch.Tensor]:
    package_width = 8 if width == 4 else width
    path = _MAP_DIR / f"{architecture}_rank{package_width}.sft"
    tensors = load_file(str(path), device="cpu")
    if set(tensors) != {"adaln_t_table", "adaln_affine_map"}:
        raise ValueError(f"Invalid MiniMax H3 affine package tensor set: {path.name}")
    table = tensors["adaln_t_table"].float()
    affine = tensors["adaln_affine_map"].float()
    if (
        table.ndim != 2
        or table.shape[1] != package_width
        or affine.shape != (package_width + 1, FULL_TIME_DIM)
        or not bool(torch.isfinite(table).all())
        or not bool(torch.isfinite(affine).all())
    ):
        raise ValueError(
            f"Invalid MiniMax H3 {architecture} rank-{package_width} AdaLN affine package"
        )
    if width == 4:
        table = table[:, :width]
        affine = torch.cat((affine[:width], affine[-1:]))
    return table, affine


def _aligned_affine_map(architecture: str, target_table: torch.Tensor) -> torch.Tensor:
    target_table = target_table.detach().to(device="cpu", dtype=torch.float64)
    if (
        target_table.ndim != 2
        or target_table.shape[1] not in _PRUNED_WIDTHS
        or target_table.shape[0] < 2
        or not bool(torch.isfinite(target_table).all())
    ):
        raise ValueError(
            f"Unsupported MiniMax H3 {architecture} AdaLN target table shape "
            f"{tuple(target_table.shape)}"
        )
    canonical_table, canonical_affine = _load_affine_package(
        architecture, int(target_table.shape[1])
    )
    if target_table.shape[0] != canonical_table.shape[0]:
        position = torch.linspace(
            0,
            canonical_table.shape[0] - 1,
            target_table.shape[0],
            dtype=torch.float64,
        )
        lower = position.floor().long().clamp(max=canonical_table.shape[0] - 2)
        canonical_table = torch.lerp(
            canonical_table[lower].double(),
            canonical_table[lower + 1].double(),
            (position - lower).unsqueeze(1),
        )
    elif torch.equal(target_table.float(), canonical_table):
        return canonical_affine
    ones = target_table.new_ones(target_table.shape[0], 1)
    target_h = torch.cat((target_table, ones), dim=1)
    canonical_h = torch.cat((canonical_table.double(), ones), dim=1)
    fit = torch.linalg.lstsq(target_h, canonical_h, rcond=1e-14)
    if int(fit.rank) != target_h.shape[1]:
        raise ValueError(f"MiniMax H3 {architecture} checkpoint has a rank-deficient AdaLN table")
    relative_error = (
        torch.linalg.vector_norm(target_h @ fit.solution - canonical_h)
        / torch.linalg.vector_norm(canonical_h)
    )
    if not bool(torch.isfinite(relative_error)) or float(relative_error) > 1e-5:
        raise ValueError(
            f"MiniMax H3 {architecture} checkpoint AdaLN table is incompatible with "
            f"the canonical LoRA map (relative error {float(relative_error):.3g})"
        )
    return (fit.solution @ canonical_affine.double()).float()


@lru_cache(maxsize=6)
def _canonical_encoder(architecture: str, width: int) -> torch.Tensor:
    _, affine = _load_affine_package(architecture, width)
    return torch.linalg.pinv(affine[:width].double(), rtol=1e-14).T.float()


def _add_bias_delta(state_dict: dict[str, torch.Tensor], key: str, delta: torch.Tensor) -> None:
    existing = state_dict.get(key)
    if existing is not None:
        if not torch.is_tensor(existing) or existing.shape != delta.shape:
            actual = None if not torch.is_tensor(existing) else tuple(existing.shape)
            raise ValueError(
                f"MiniMax H3 LoRA bias delta shape mismatch for {key}: "
                f"{actual} != {tuple(delta.shape)}"
            )
        if not torch.is_floating_point(existing):
            raise ValueError(
                f"MiniMax H3 LoRA bias delta {key!r} must be a floating-point tensor"
            )
        if not bool(torch.isfinite(existing).all()):
            raise ValueError(
                f"MiniMax H3 LoRA bias delta {key!r} must contain only finite values"
            )
        delta.add_(existing.float())
    state_dict[key] = delta


def convert_adaln_loras(
    model_type: str,
    state_dict: dict[str, torch.Tensor],
    target_table: torch.Tensor | None = None,
) -> tuple[int, str, int, int]:
    """Convert AdaLN input width without changing each adapter's LoRA rank."""

    architecture = _architecture(model_type)
    target_width = FULL_TIME_DIM if target_table is None else int(target_table.shape[1])
    candidates: list[tuple[str, str, str, int]] = []

    for down_key in [key for key in state_dict if key.endswith(".lora_A.weight")]:
        module_name = down_key.removesuffix(".lora_A.weight")
        if ".adaln_proj.linear" not in module_name:
            continue
        up_key = module_name + ".lora_B.weight"
        if up_key not in state_dict:
            raise ValueError(f"MiniMax H3 LoRA is missing {up_key}")
        down, up = state_dict[down_key], state_dict[up_key]
        if down.ndim != 2 or up.ndim != 2 or up.shape[1] != down.shape[0]:
            raise ValueError(
                f"MiniMax H3 LoRA factors are incompatible for {module_name}: "
                f"A={tuple(down.shape)}, B={tuple(up.shape)}"
            )
        candidates.append((module_name, down_key, up_key, int(down.shape[1])))

    if not candidates:
        return 0, architecture, target_width, target_width
    source_widths = {candidate[3] for candidate in candidates}
    if len(source_widths) != 1:
        raise ValueError(f"MiniMax H3 LoRA mixes AdaLN input widths: {sorted(source_widths)}")
    source_width = source_widths.pop()
    if source_width not in _SUPPORTED_WIDTHS or target_width not in _SUPPORTED_WIDTHS:
        raise ValueError(
            f"Unsupported MiniMax H3 AdaLN LoRA conversion {source_width} -> {target_width}; "
            f"supported widths are {_SUPPORTED_WIDTHS}"
        )
    target_affine = None if target_table is None else _aligned_affine_map(architecture, target_table)
    if source_width == target_width:
        return 0, architecture, source_width, target_width
    source_affine = (
        None if source_width == FULL_TIME_DIM else _load_affine_package(architecture, source_width)[1]
    )
    source_encoder = (
        None if source_width == FULL_TIME_DIM else _canonical_encoder(architecture, source_width)
    )

    for module_name, down_key, up_key, _ in candidates:
        down, up = state_dict[down_key], state_dict[up_key]
        mapped = down.float() if source_encoder is None else down.float() @ source_encoder
        inner_bias = (
            mapped.new_zeros(mapped.shape[0])
            if source_affine is None
            else -(mapped @ source_affine[-1])
        )
        if target_affine is not None:
            mapped = mapped @ target_affine.T
            inner_bias.add_(mapped[:, target_width])
            mapped = mapped[:, :target_width]
        state_dict[down_key] = mapped
        _add_bias_delta(state_dict, module_name + ".diff_b", up.float() @ inner_bias)

    return len(candidates), architecture, source_width, target_width


def _strip_wrappers(module_name: str) -> str:
    previous = None
    while module_name != previous:
        previous = module_name
        for prefix in _WRAPPER_PREFIXES:
            if module_name.startswith(prefix):
                module_name = module_name[len(prefix) :]
                break
    return module_name


def _flattened_aliases(module_specs: Mapping[str, LoraModuleSpec]) -> dict[str, str | None]:
    aliases: dict[str, str | None] = {}

    def add(alias: str, target: str) -> None:
        flattened = alias.replace(".", "_")
        previous = aliases.get(flattened, target)
        aliases[flattened] = target if previous == target else None

    def diffusers_alias(target: str) -> str:
        diffusers = target
        for source, replacement in reversed(_TOP_LEVEL_ALIASES):
            if diffusers == replacement[:-1]:
                diffusers = source[:-1]
                break
            if diffusers.startswith(replacement):
                diffusers = source + diffusers[len(replacement) :]
                break
        for source, replacement in reversed(_INNER_ALIASES):
            diffusers = diffusers.replace(replacement, source)
        return diffusers

    for target in module_specs:
        add(target, target)
        inner_diffusers = target
        for source, replacement in reversed(_INNER_ALIASES):
            inner_diffusers = inner_diffusers.replace(replacement, source)
        add(inner_diffusers, target)
        add(diffusers_alias(target), target)
        if target.endswith(".attn.qkv_proj"):
            base = target.removesuffix("qkv_proj")
            for projection in ("q", "k", "v"):
                virtual = base + projection + "_proj"
                add(virtual, virtual)
                add(base + "to_" + projection, virtual)
                add(diffusers_alias(virtual), virtual)
    return aliases


def _map_module_name(
    raw_name: str,
    module_specs: Mapping[str, LoraModuleSpec],
    flattened_aliases: Mapping[str, str | None],
    *,
    flattened: bool,
) -> tuple[str, bool]:
    raw_name = _strip_wrappers(raw_name)
    diffusers_fc1 = ".ff.net.0.proj" in raw_name or "_ff_net_0_proj" in raw_name
    if flattened:
        target = flattened_aliases.get(raw_name)
        if target is None:
            raise ValueError(f"MiniMax H3 LoRA targets unknown flattened module {raw_name!r}")
        return target, diffusers_fc1

    mapped = raw_name
    for source, target in _TOP_LEVEL_ALIASES:
        if mapped == source[:-1]:
            mapped = target[:-1]
            break
        if mapped.startswith(source):
            mapped = target + mapped[len(source) :]
            break
    for source, target in _INNER_ALIASES:
        mapped = mapped.replace(source, target)
    if mapped in module_specs:
        return mapped, diffusers_fc1
    if mapped.endswith((".attn.q_proj", ".attn.k_proj", ".attn.v_proj")):
        fused = mapped.rsplit(".", 1)[0] + ".qkv_proj"
        if fused in module_specs:
            return mapped, diffusers_fc1
    raise ValueError(f"MiniMax H3 LoRA targets unknown module {raw_name!r}")


def _split_key(key: str) -> tuple[str, str, str, bool]:
    flattened = key.startswith("lora_unet_")
    if flattened:
        key = key[len("lora_unet_") :]
    for suffix, component, dialect in _FACTOR_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)], component, dialect, flattened
    for suffix, component in ((".diff_b", "bias"), (".alpha", "alpha")):
        if key.endswith(suffix):
            return key[: -len(suffix)], component, "metadata", flattened
    raise ValueError(f"MiniMax H3 LoRA contains unsupported tensor key {key!r}")


def _path_dialect(module_name: str, *, flattened: bool) -> str:
    if flattened:
        return "kohya_flattened"
    module_name = _strip_wrappers(module_name)
    if any(
        module_name == source[:-1] or module_name.startswith(source)
        for source, _target in _TOP_LEVEL_ALIASES
    ) or any(source in module_name for source, _target in _INNER_ALIASES):
        return "diffusers"
    return "native"


def _scalar_alpha(value: torch.Tensor, module_name: str) -> float:
    if not torch.is_tensor(value) or value.numel() != 1:
        raise ValueError(f"MiniMax H3 LoRA alpha for {module_name} must be a scalar tensor")
    alpha = float(value.detach().to(device="cpu", dtype=torch.float64).item())
    if not math.isfinite(alpha):
        raise ValueError(f"MiniMax H3 LoRA alpha for {module_name} must be finite")
    return alpha


def _validate_factor_tensor(value: torch.Tensor, key: str) -> None:
    if not torch.is_tensor(value) or not torch.is_floating_point(value):
        raise ValueError(f"MiniMax H3 LoRA factor {key!r} must be a floating-point tensor")
    if not bool(torch.isfinite(value).all()):
        raise ValueError(f"MiniMax H3 LoRA factor {key!r} must contain only finite values")


def _validate_pairs(state_dict: Mapping[str, torch.Tensor]) -> None:
    pairs: dict[str, set[str]] = {}
    for key, tensor in state_dict.items():
        if not torch.is_tensor(tensor):
            raise ValueError(f"MiniMax H3 LoRA tensor {key!r} is not a tensor")
        if key.endswith(".lora_A.weight"):
            _validate_factor_tensor(tensor, key)
            pairs.setdefault(key.removesuffix(".lora_A.weight"), set()).add("A")
        elif key.endswith(".lora_B.weight"):
            _validate_factor_tensor(tensor, key)
            pairs.setdefault(key.removesuffix(".lora_B.weight"), set()).add("B")
    for module_name, components in pairs.items():
        if components != {"A", "B"}:
            missing = "B" if components == {"A"} else "A"
            raise ValueError(f"MiniMax H3 LoRA is missing factor {missing} for {module_name}")
        down = state_dict[module_name + ".lora_A.weight"]
        up = state_dict[module_name + ".lora_B.weight"]
        if down.ndim != 2 or up.ndim != 2 or down.shape[0] <= 0 or up.shape[1] != down.shape[0]:
            raise ValueError(
                f"MiniMax H3 LoRA factors are incompatible for {module_name}: "
                f"A={tuple(down.shape)}, B={tuple(up.shape)}"
            )
        if module_name + ".alpha" in state_dict:
            _scalar_alpha(state_dict[module_name + ".alpha"], module_name)
    for key in state_dict:
        if key.endswith(".alpha") and key.removesuffix(".alpha") not in pairs:
            raise ValueError(
                f"MiniMax H3 LoRA alpha has no factor pair for {key.removesuffix('.alpha')}"
            )


def _fuse_qkv(
    state_dict: dict[str, torch.Tensor],
    module_specs: Mapping[str, LoraModuleSpec],
) -> None:
    groups: set[str] = set()
    for key in state_dict:
        for projection in ("q_proj", "k_proj", "v_proj"):
            marker = f".{projection}."
            if marker in key:
                groups.add(key.split(marker, 1)[0])
                break

    for attention_name in sorted(groups):
        fused_name = attention_name + ".qkv_proj"
        if fused_name not in module_specs:
            raise ValueError(f"MiniMax H3 LoRA has no fused QKV target for {attention_name}")
        if any(key.startswith(fused_name + ".") for key in state_dict):
            raise ValueError(f"MiniMax H3 LoRA collides at fused QKV target {fused_name}")
        factors = []
        for projection in ("q_proj", "k_proj", "v_proj"):
            module_name = attention_name + "." + projection
            down_key = module_name + ".lora_A.weight"
            up_key = module_name + ".lora_B.weight"
            if down_key not in state_dict or up_key not in state_dict:
                raise ValueError(
                    f"MiniMax H3 LoRA must provide complete Q/K/V factors for {attention_name}"
                )
            down = state_dict.pop(down_key)
            up = state_dict.pop(up_key)
            alpha_value = state_dict.pop(module_name + ".alpha", None)
            scale = 1.0 if alpha_value is None else _scalar_alpha(alpha_value, module_name) / down.shape[0]
            factors.append((down, up, scale))
        if len({int(down.shape[1]) for down, _, _ in factors}) != 1:
            raise ValueError(f"MiniMax H3 LoRA Q/K/V input widths differ for {attention_name}")
        spec = module_specs[fused_name]
        if spec.out_features % 3 or any(up.shape[0] != spec.out_features // 3 for _, up, _ in factors):
            raise ValueError(f"MiniMax H3 LoRA Q/K/V output widths are invalid for {attention_name}")
        state_dict[fused_name + ".lora_A.weight"] = torch.cat(
            [down for down, _, _ in factors], dim=0
        ).contiguous()
        state_dict[fused_name + ".lora_B.weight"] = torch.block_diag(
            *(up * scale for _, up, scale in factors)
        ).contiguous()


def _validate_targets(
    state_dict: Mapping[str, torch.Tensor],
    module_specs: Mapping[str, LoraModuleSpec],
) -> None:
    for key, tensor in state_dict.items():
        if key.endswith(".lora_A.weight"):
            module_name = key.removesuffix(".lora_A.weight")
            spec = module_specs.get(module_name)
            if spec is None or tensor.shape[1] != spec.in_features:
                expected = None if spec is None else spec.in_features
                raise ValueError(
                    f"MiniMax H3 LoRA A shape for {module_name} is {tuple(tensor.shape)}; "
                    f"expected input width {expected}"
                )
        elif key.endswith(".lora_B.weight"):
            module_name = key.removesuffix(".lora_B.weight")
            spec = module_specs.get(module_name)
            if spec is None or tensor.shape[0] != spec.out_features:
                expected = None if spec is None else spec.out_features
                raise ValueError(
                    f"MiniMax H3 LoRA B shape for {module_name} is {tuple(tensor.shape)}; "
                    f"expected output width {expected}"
                )
        elif key.endswith(".diff_b"):
            module_name = key.removesuffix(".diff_b")
            spec = module_specs.get(module_name)
            _validate_factor_tensor(tensor, key)
            if spec is None or not spec.has_bias or tensor.ndim != 1 or tensor.shape[0] != spec.out_features:
                raise ValueError(f"MiniMax H3 LoRA bias delta is invalid for {module_name}")
        elif key.endswith(".alpha"):
            module_name = key.removesuffix(".alpha")
            if module_name not in module_specs:
                raise ValueError(f"MiniMax H3 LoRA alpha targets unknown module {module_name}")
            _scalar_alpha(tensor, module_name)
        else:
            raise ValueError(f"MiniMax H3 LoRA contains unsupported normalized key {key!r}")


def normalize_h3_lora_state_dict(
    model_type: str,
    state_dict: Mapping[str, torch.Tensor],
    *,
    target_table: torch.Tensor | None,
    module_specs: Mapping[str, LoraModuleSpec],
) -> dict[str, torch.Tensor]:
    """Normalize one ordinary H3 adapter and reject any ambiguous state."""

    _architecture(model_type)
    if not state_dict:
        raise ValueError("MiniMax H3 ordinary LoRA is empty")
    flattened_aliases = _flattened_aliases(module_specs)
    converted: dict[str, torch.Tensor] = {}
    factor_dialects: set[str] = set()
    path_dialects: set[str] = set()

    for original_key, original_value in state_dict.items():
        module_name, component, dialect, flattened = _split_key(str(original_key))
        path_dialects.add(_path_dialect(module_name, flattened=flattened))
        mapped_name, reverse_fc1 = _map_module_name(
            module_name,
            module_specs,
            flattened_aliases,
            flattened=flattened,
        )
        value = original_value
        if component in {"A", "B"}:
            factor_dialects.add(dialect)
            suffix = ".lora_A.weight" if component == "A" else ".lora_B.weight"
            if reverse_fc1 and component == "B":
                if not torch.is_tensor(value) or value.ndim != 2 or value.shape[0] % 2:
                    raise ValueError(
                        f"MiniMax H3 Diffusers FC1 LoRA B has invalid shape at {original_key!r}"
                    )
                value = torch.cat(value.chunk(2, dim=0)[::-1], dim=0).contiguous()
        elif component == "bias":
            suffix = ".diff_b"
        else:
            suffix = ".alpha"
        target_key = mapped_name + suffix
        if target_key in converted:
            raise ValueError(f"MiniMax H3 LoRA keys collide after mapping to {target_key!r}")
        converted[target_key] = value

    if len(factor_dialects) > 1:
        raise ValueError(
            f"MiniMax H3 LoRA mixes factor naming dialects: {sorted(factor_dialects)}"
        )
    if len(path_dialects) > 1:
        raise ValueError(f"MiniMax H3 LoRA mixes path naming dialects: {sorted(path_dialects)}")
    _validate_pairs(converted)
    _fuse_qkv(converted, module_specs)
    _validate_pairs(converted)
    convert_adaln_loras(model_type, converted, target_table)
    _validate_pairs(converted)
    _validate_targets(converted, module_specs)
    return converted


__all__ = [
    "FULL_TIME_DIM",
    "LoraModuleSpec",
    "convert_adaln_loras",
    "normalize_h3_lora_state_dict",
]
