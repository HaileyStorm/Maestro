"""Native MiniMax H3 Base FL2VA and Ref2VA runtime for Maestro.

The sampling contract follows the official Diffusers implementation pinned in
``UPSTREAM.md``.  Model construction is checkpoint-shaped so MMGP can stream
Comfy-Org's compact consumer weights on machines that cannot hold the full
42.5 GB stack in VRAM at once.
"""

from __future__ import annotations

import math
import os
import time
from contextlib import nullcontext

import numpy as np
import torch
from accelerate import init_empty_weights
from diffusers.models.autoencoders.vae import DiagonalGaussianDistribution
from diffusers.utils.torch_utils import randn_tensor
from PIL import Image
from tqdm import tqdm

from mmgp import offload, quant_router
from shared.utils import files_locator as fl
from services.h3_preview import H3PreviewGeometryError

from .audio_vae import AutoencoderKLMiniMaxH3Audio
from .checkpoint import (
    preprocess_audio_vae_state_dict,
    preprocess_conditioner_state_dict as _preprocess_conditioner_state_dict_base,
    preprocess_video_vae_state_dict,
)
from .conditioner import MiniMaxH3Conditioner, MiniMaxH3Qwen3VL, build_h3_processor, load_h3_qwen_config
from .packing import (
    MINIMAX_H3_AUDIO_CHANNELS,
    MINIMAX_H3_AUDIO_LATENTS_PER_SECOND,
    MINIMAX_H3_FPS,
    MINIMAX_H3_LATENTS_PER_CHUNK,
    MINIMAX_H3_KEYFRAME_ENCODE_SEED,
    MINIMAX_H3_KEYFRAME_NOISE_AUG,
    MINIMAX_H3_MAX_DURATION,
    MINIMAX_H3_MIN_DURATION,
    MINIMAX_H3_PIXEL_MEAN,
    MINIMAX_H3_PIXEL_STD,
    MiniMaxH3PreparedReference,
    align_num_frames,
    audio_latent_num_frames,
    build_packed_sequence,
    build_ref2va_packed_sequence,
    build_row_timesteps,
    keyframe_condition_noise,
    patchify_video_latents,
    prepare_keyframe_image,
    unpack_audio_tokens,
    unpatchify_video_tokens,
    video_latent_num_frames,
)
from .scheduler import MiniMaxH3Scheduler
from .transformer import MiniMaxH3Transformer
from .video_vae import AutoencoderKLMiniMaxH3


VIDEO_LATENTS_MEAN = (
    0.858090341091156,
    -0.9606591463088989,
    1.0661640167236328,
    -0.5090325474739075,
    -0.2727581858634949,
    -1.3675414323806763,
    -0.2553254961967468,
    -0.26907554268836975,
    -0.5376840829849243,
    -0.0464097298681736,
    0.6657370328903198,
    0.19690127670764923,
    -0.5460608005523682,
    -0.4035342037677765,
    -0.23683024942874908,
    0.25928452610969543,
    -0.30133944749832153,
    0.211341992020607,
    -1.1206848621368408,
    0.3581933379173279,
    -0.04225143790245056,
    0.2604829967021942,
    0.22864092886447906,
    0.7056031823158264,
)
VIDEO_LATENTS_STD = (
    1.2223774194717407,
    1.2767263650894165,
    1.6831774711608887,
    1.7549455165863037,
    1.5636216402053833,
    2.194143533706665,
    0.9653137922286987,
    1.0569885969161987,
    0.841948926448822,
    0.7729952931404114,
    1.8955937623977661,
    0.946841835975647,
    0.7996809482574463,
    0.44988900423049927,
    0.7197399735450745,
    0.6936293244361877,
    2.961095094680786,
    2.7694199085235596,
    3.0496184825897217,
    2.1088054180145264,
    3.276226282119751,
    3.1627357006073,
    2.2816812992095947,
    2.6127843856811523,
)


def _advance_paired_h3_latents(
    *,
    video_rows: torch.Tensor,
    audio_rows: torch.Tensor,
    prediction,
    video_timestep,
    audio_timestep,
    video_scheduler: MiniMaxH3Scheduler,
    audio_scheduler: MiniMaxH3Scheduler,
    num_condition_video_rows: int,
    num_condition_audio_rows: int,
    locked_target_audio_rows: torch.Tensor | None = None,
    advance_video: bool = True,
    advance_audio: bool = True,
) -> None:
    """Atomically publish the requested members of one H3 prediction pair."""
    video_velocity, audio_velocity = prediction
    next_video_rows = None
    next_audio_rows = None
    if advance_video:
        next_video_rows = video_scheduler.step(
            video_velocity[0, num_condition_video_rows:].float(),
            video_timestep,
            video_rows[num_condition_video_rows:],
            return_dict=False,
        )[0]
    if advance_audio:
        next_audio_rows = audio_scheduler.step(
            audio_velocity[0, num_condition_audio_rows:].float(),
            audio_timestep,
            audio_rows[num_condition_audio_rows:],
            return_dict=False,
        )[0]
        if (
            locked_target_audio_rows is not None
            and tuple(locked_target_audio_rows.shape) != tuple(next_audio_rows.shape)
        ):
            raise RuntimeError("Locked H3 source audio changed target-row shape")

    # Scheduler calls mutate their own indices. Publish tensors only after
    # every requested call succeeds; the enclosing loop resets both clocks on
    # cancellation or failure before this tick can be retried.
    if next_video_rows is not None:
        video_rows[num_condition_video_rows:] = next_video_rows
    if next_audio_rows is not None:
        audio_rows[num_condition_audio_rows:] = (
            locked_target_audio_rows
            if locked_target_audio_rows is not None
            else next_audio_rows
        )


def _fit_h3_source_waveform(
    waveform: torch.Tensor,
    target_samples: int,
) -> torch.Tensor:
    """Crop or silence-pad stereo source audio to the target latent clock."""

    if waveform.ndim != 2 or waveform.shape[0] != 2:
        raise ValueError("MiniMax H3 source audio must resolve to 32 kHz stereo")
    if target_samples < 1:
        raise ValueError("MiniMax H3 source audio target must contain samples")
    waveform = waveform[:, :target_samples]
    if waveform.shape[-1] < target_samples:
        padding = waveform.new_zeros((2, target_samples - waveform.shape[-1]))
        waveform = torch.cat((waveform, padding), dim=-1)
    return waveform.contiguous()


def _fit_h3_source_audio_latents(
    latents: torch.Tensor,
    target_latents: int,
) -> torch.Tensor:
    """Normalize audio-VAE boundary rounding to H3's exact target clock."""

    if latents.ndim != 3 or latents.shape[0] != 2:
        raise ValueError("MiniMax H3 source audio latents must be stereo")
    if target_latents < 1:
        raise ValueError("MiniMax H3 source audio target must contain latents")
    latents = latents[..., :target_latents]
    if latents.shape[-1] < target_latents:
        padding = latents.new_zeros(
            (*latents.shape[:-1], target_latents - latents.shape[-1])
        )
        latents = torch.cat((latents, padding), dim=-1)
    return latents.contiguous()


def _reset_paired_h3_schedulers(
    video_scheduler: MiniMaxH3Scheduler,
    audio_scheduler: MiniMaxH3Scheduler,
    grid_points: int,
    device: torch.device | str,
    audio_grid_points: int | None = None,
) -> None:
    """Reset both stateful schedulers before replaying the same H3 clocks."""
    video_scheduler.set_timesteps(grid_points, device=device)
    audio_scheduler.set_timesteps(
        grid_points if audio_grid_points is None else audio_grid_points,
        device=device,
    )


def _run_h3_master_schedule(
    *,
    timesteps,
    audio_timesteps,
    row_plan,
    video_advance_ticks,
    interrupt_requested,
    predict,
    advance,
    after_step,
    reset,
) -> bool:
    """Execute one H3 master clock and reset both schedulers on any abort."""

    completed = False
    try:
        for index, (video_timestep, audio_timestep) in enumerate(
            zip(timesteps, audio_timesteps)
        ):
            if interrupt_requested():
                return False
            unique_timesteps, timestep_indices = row_plan[index]
            prediction = predict(index, unique_timesteps, timestep_indices)
            if prediction is None or interrupt_requested():
                return False
            advance(
                prediction,
                video_timestep,
                audio_timestep,
                advance_video=index in video_advance_ticks,
            )
            after_step(index)
            if interrupt_requested():
                return False
        completed = True
        return True
    finally:
        if not completed:
            reset()


AUDIO_LATENTS_MEAN = (
    -0.020211687488382354,
    0.3876466479950502,
    -0.04398279799186767,
    -0.28591514936373,
    0.08179686214561671,
    -0.35782641352446604,
    0.040623809960919084,
    -0.01552534501956604,
    -0.223362481667332,
    0.1821006842509091,
    0.2941778783780663,
    -0.07901167601970885,
    -0.056815072777201,
    -0.3699028221860095,
    -0.31616315591624855,
    0.5905951377425391,
    -0.052139568068853864,
    0.013673160263486295,
    -0.03691647864630577,
    0.09732660653298163,
    -0.3394662328788498,
    -0.30685677538541667,
    -0.24504598907458763,
    -0.034698524462007344,
    0.02868032184767538,
    -0.21217779266454084,
    -0.1678263169941987,
    0.3221287889040614,
    -0.1223055851554907,
    0.4356604928128464,
    -0.0502599202236253,
    0.3979258376211797,
)
AUDIO_LATENTS_STD = (
    1.6895524230479284,
    2.76263727217653,
    1.7945344281264435,
    1.6801681847309828,
    1.6390226546605453,
    2.7788298348882177,
    1.7659090095747236,
    1.6199757612137327,
    2.6336525640336896,
    1.8539356672817833,
    2.5056497896915633,
    1.811019237886178,
    1.9579657790720237,
    1.6685498243529284,
    1.4922469314453364,
    3.298670198067373,
    1.9491804496832168,
    1.8720003270431442,
    1.8334080103291832,
    1.6488070416529093,
    1.6176957696319716,
    1.9131449234774398,
    1.5695245398428617,
    1.6943659940415912,
    1.8318420762504692,
    1.5540637421583379,
    1.9344930328968526,
    1.599198216109855,
    1.718045989838149,
    1.6307219190837705,
    1.8661226051202384,
    1.5613768203168363,
)


def _keyframe_latent_stats_cpu() -> tuple[torch.Tensor, torch.Tensor]:
    """Return the official FL2VA keyframe normalization tensors on CPU.

    H3 rounds encoded keyframes to float16, promotes them back to float32,
    and normalizes them on CPU before returning the packed rows to the GPU.
    Maestro sets a CUDA default device globally, so an omitted ``device``
    here would silently put these constants on CUDA and break that contract.
    """
    means = torch.tensor(
        VIDEO_LATENTS_MEAN,
        dtype=torch.float32,
        device=torch.device("cpu"),
    ).view(1, -1, 1, 1, 1)
    stds = torch.tensor(
        VIDEO_LATENTS_STD,
        dtype=torch.float32,
        device=torch.device("cpu"),
    ).view(1, -1, 1, 1, 1)
    return means, stds


def _decode_h3_video_rows(
    *,
    vae: AutoencoderKLMiniMaxH3,
    device: torch.device,
    packed_rows: torch.Tensor,
    latent_frames: int,
    latent_height: int,
    latent_width: int,
    pixel_frames: int,
    pixel_height: int,
    pixel_width: int,
    channels: int,
    patch_size: tuple[int, int, int],
) -> tuple[torch.Tensor, torch.Tensor]:
    """Run the exact native H3 video decode recipe for final or preview use."""

    if channels != len(VIDEO_LATENTS_MEAN) or channels != len(VIDEO_LATENTS_STD):
        raise H3PreviewGeometryError(
            "MiniMax H3 video latent channel geometry is invalid"
        )
    if any(type(value) is not int or value < 1 for value in (
        latent_frames,
        latent_height,
        latent_width,
        pixel_frames,
        pixel_height,
        pixel_width,
    )):
        raise H3PreviewGeometryError(
            "MiniMax H3 video geometry must contain positive integers"
        )
    if len(patch_size) != 3 or any(type(value) is not int or value < 1 for value in patch_size):
        raise H3PreviewGeometryError("MiniMax H3 video patch geometry is invalid")
    patch_t, patch_h, patch_w = patch_size
    if latent_frames % patch_t or latent_height % patch_h or latent_width % patch_w:
        raise H3PreviewGeometryError(
            "MiniMax H3 video latent geometry is not patch aligned"
        )
    expected_rows = (
        (latent_frames // patch_t)
        * (latent_height // patch_h)
        * (latent_width // patch_w)
    )
    expected_channels = channels * math.prod(patch_size)
    if packed_rows.ndim != 2 or tuple(packed_rows.shape) != (
        expected_rows,
        expected_channels,
    ):
        raise H3PreviewGeometryError(
            "MiniMax H3 packed video row geometry is invalid"
        )

    video_latents = unpatchify_video_tokens(
        packed_rows,
        latent_frames,
        latent_height,
        latent_width,
        channels,
        patch_size,
    )
    expected_latent_shape = (
        1,
        channels,
        latent_frames,
        latent_height,
        latent_width,
    )
    if tuple(video_latents.shape) != expected_latent_shape:
        raise H3PreviewGeometryError(
            "MiniMax H3 unpacked video latent geometry is invalid"
        )
    video_mean = torch.tensor(VIDEO_LATENTS_MEAN, device=device).view(1, -1, 1, 1, 1)
    video_std = torch.tensor(VIDEO_LATENTS_STD, device=device).view(1, -1, 1, 1, 1)
    denormalized_latents = video_latents * video_std + video_mean
    autocast = (
        torch.autocast(device_type="cuda", dtype=torch.float16)
        if device.type == "cuda"
        else nullcontext()
    )
    with autocast:
        video = vae.decode(denormalized_latents, return_dict=False)[0]
    expected_pixel_shape = (1, 3, pixel_frames, pixel_height, pixel_width)
    if tuple(video.shape) != expected_pixel_shape:
        raise H3PreviewGeometryError(
            "MiniMax H3 decoded video geometry is invalid"
        )
    pixel_mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN, device=device).view(1, -1, 1, 1, 1)
    pixel_std = torch.tensor(MINIMAX_H3_PIXEL_STD, device=device).view(1, -1, 1, 1, 1)
    video = (video.float() * pixel_std + pixel_mean).clamp(0, 1).mul(2).sub(1)
    return video, video_latents


def _first_path(value):
    if isinstance(value, (list, tuple)):
        return value[0]
    return value


def _tensor_to_pil(image) -> Image.Image | None:
    if image is None:
        return None
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if not isinstance(image, torch.Tensor):
        return Image.fromarray(np.asarray(image).astype(np.uint8)).convert("RGB")

    tensor = image.detach().to("cpu")
    if tensor.ndim == 4:
        tensor = tensor[:, 0]
    if tensor.ndim != 3:
        raise ValueError(f"MiniMax H3 keyframes must be CHW tensors, got {tuple(tensor.shape)}.")
    if tensor.dtype == torch.uint8:
        pixels = tensor.permute(1, 2, 0).numpy()
    else:
        pixels = tensor.float().clamp(-1, 1).add(1).mul(127.5).round().to(torch.uint8)
        pixels = pixels.permute(1, 2, 0).numpy()
    return Image.fromarray(pixels).convert("RGB")


def _as_video_tensor(video) -> torch.Tensor | None:
    if video is None:
        return None
    if not isinstance(video, torch.Tensor):
        raise ValueError("MiniMax H3 reference videos must be preprocessed CTHW tensors.")
    tensor = video.detach()
    if tensor.ndim == 5 and tensor.shape[0] == 1:
        tensor = tensor[0]
    if tensor.ndim == 3:
        tensor = tensor[:, None]
    if tensor.ndim != 4 or tensor.shape[0] != 3:
        raise ValueError(
            f"MiniMax H3 reference videos must be CTHW RGB tensors, got {tuple(tensor.shape)}."
        )
    if tensor.dtype == torch.uint8:
        tensor = tensor.float().div(127.5).sub(1.0)
    else:
        tensor = tensor.float()
        if tensor.numel() and float(tensor.min()) >= 0.0 and float(tensor.max()) <= 1.0:
            tensor = tensor.mul(2.0).sub(1.0)
    return tensor


def _pil_to_video_tensor(image: Image.Image) -> torch.Tensor:
    pixels = torch.from_numpy(np.asarray(image.convert("RGB")).copy())
    return pixels.permute(2, 0, 1).float().div(127.5).sub(1.0).unsqueeze(1)


def _qwen_video_frames(video: torch.Tensor) -> torch.Tensor:
    return video.permute(1, 2, 3, 0).add(1.0).mul_(0.5).clamp_(0.0, 1.0)


def _reference_canvas_size(width: int, height: int, pixel_budget: float) -> tuple[int, int]:
    ratio = width / height
    if not 0.25 <= ratio <= 4.0:
        raise ValueError(
            f"MiniMax H3 references require an aspect ratio from 1:4 to 4:1, got {width}x{height}."
        )
    short_edge = math.sqrt(pixel_budget / max(ratio, 1.0 / ratio))
    if ratio >= 1.0:
        target_width, target_height = short_edge * ratio, short_edge
    else:
        target_width, target_height = short_edge, short_edge / ratio
    return (
        max(32, round(target_height / 32) * 32),
        max(32, round(target_width / 32) * 32),
    )


def _audio_rows(latents: torch.Tensor) -> torch.Tensor:
    """Pack normalized stereo audio latents into channel-major rows."""

    if latents.ndim != 3 or latents.shape[0] != MINIMAX_H3_AUDIO_CHANNELS:
        raise ValueError(f"Expected normalized stereo audio latents, got {tuple(latents.shape)}.")
    return latents.permute(0, 2, 1).reshape(-1, latents.shape[1]).contiguous()


def _probe_transformer_checkpoint(filename: str) -> dict:
    """Read only checkpoint headers to choose compact versus full AdaLN."""

    checkpoint_path = filename[0] if isinstance(filename, (list, tuple)) else filename
    state_dict, _metadata = quant_router.load_metadata_state_dict(checkpoint_path)
    normalized = {}
    for key, value in state_dict.items():
        for prefix in ("model.diffusion_model.", "diffusion_model."):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break
        normalized[key] = value
    table = normalized.get("adaln_t_table")
    if table is not None:
        if len(table.shape) != 2 or int(table.shape[0]) < 2:
            raise ValueError(f"Invalid H3 AdaLN curve table shape: {tuple(table.shape)}")
        return {
            "architecture": "compact_curve",
            "curve_grid": int(table.shape[0]),
            "curve_dim": int(table.shape[1]),
        }
    time_out = normalized.get("time_embedder.proj_out.weight")
    if time_out is None or len(time_out.shape) != 2:
        raise ValueError(
            "H3 checkpoint has neither a compact AdaLN curve table nor the full timestep embedder"
        )
    return {
        "architecture": "full_timestep",
        "curve_grid": None,
        "curve_dim": int(time_out.shape[0]),
    }


def _restore_interleaved_transformer_qkv(
    state_dict,
    *,
    qkv_layout="interleaved",
):
    """Convert WanGP's head-interleaved fused QKV rows for this runtime."""

    if qkv_layout != "interleaved":
        return state_dict
    heads = 56
    head_dim = 128
    expected_rows = heads * 3 * head_dim
    for key, tensor in list(state_dict.items()):
        if not key.endswith((".qkv_proj.weight", ".qkv_proj.weight_scale")):
            continue
        if tensor.ndim == 0 or int(tensor.shape[0]) != expected_rows:
            continue
        trailing_shape = tuple(tensor.shape[1:])
        grouped = tensor.reshape(heads, 3, head_dim, *trailing_shape)
        permutation = (1, 0, 2, *range(3, grouped.ndim))
        state_dict[key] = (
            grouped.permute(permutation)
            .reshape(expected_rows, *trailing_shape)
            .contiguous()
        )
    return state_dict


def _load_transformer(
    filename: str,
    dtype: torch.dtype,
    *,
    qkv_layout: str = "contiguous",
) -> MiniMaxH3Transformer:
    from .convrot import adapt_int8_convrot_state_dict

    checkpoint = _probe_transformer_checkpoint(filename)
    with init_empty_weights(include_buffers=True):
        transformer = MiniMaxH3Transformer(
            dtype=dtype,
            curve_grid=checkpoint["curve_grid"],
            curve_dim=checkpoint["curve_dim"],
        )

    def preprocess_transformer_state_dict(state_dict):
        state_dict = _restore_interleaved_transformer_qkv(
            state_dict,
            qkv_layout=qkv_layout,
        )
        return adapt_int8_convrot_state_dict(
            transformer, state_dict, output_dtype=dtype,
        )

    offload.load_model_data(
        transformer,
        filename,
        writable_tensors=False,
        preprocess_sd=preprocess_transformer_state_dict,
        default_dtype=dtype,
    )
    transformer._model_dtype = dtype
    transformer.h3_checkpoint_info = checkpoint
    transformer.h3_qkv_layout = qkv_layout
    return transformer.eval().requires_grad_(False)


def _load_conditioner(
    filename: str,
    config_relative_path: str,
    processor_relative_paths: list[str],
    dtype: torch.dtype,
) -> MiniMaxH3Conditioner:
    config_path = fl.locate_file(config_relative_path)
    processor_folder = os.path.dirname(processor_relative_paths[0])
    processor_files = [os.path.basename(path) for path in processor_relative_paths]
    processor_path = fl.locate_folder(
        processor_folder,
        required_files=processor_files,
    )
    missing_processor_files = [
        filename
        for filename in processor_files
        if not os.path.isfile(os.path.join(processor_path, filename))
    ]
    if missing_processor_files:
        raise FileNotFoundError(
            "MiniMax H3 processor folder is incomplete: "
            + ", ".join(missing_processor_files)
        )
    config = load_h3_qwen_config(config_path)
    tokenizer, processor = build_h3_processor(processor_path)
    # Qwen keeps rotary-frequency tables as computed, non-persistent buffers,
    # so they are intentionally absent from the checkpoint.  Keep those small
    # buffers materialized while Accelerate places the 32B parameters on meta.
    with init_empty_weights(include_buffers=False):
        qwen = MiniMaxH3Qwen3VL(config, dtype=dtype)
    def preprocess_conditioner_state_dict(state_dict):
        from .convrot import adapt_int8_convrot_state_dict
        state_dict = adapt_int8_convrot_state_dict(
            qwen, state_dict, output_dtype=dtype,
        )
        return _preprocess_conditioner_state_dict_base(state_dict)

    offload.load_model_data(
        qwen,
        filename,
        writable_tensors=False,
        preprocess_sd=preprocess_conditioner_state_dict,
        default_dtype=dtype,
    )
    qwen._model_dtype = dtype
    qwen.eval().requires_grad_(False)
    conditioner = MiniMaxH3Conditioner(qwen, tokenizer, processor).eval().requires_grad_(False)
    conditioner._model_dtype = dtype
    return conditioner


def _load_video_vae(filename: str) -> AutoencoderKLMiniMaxH3:
    # Rotary tables are computed, non-persistent buffers and therefore are
    # not present in the compact checkpoint.
    with init_empty_weights(include_buffers=False):
        vae = AutoencoderKLMiniMaxH3(
            latents_mean=VIDEO_LATENTS_MEAN,
            latents_std=VIDEO_LATENTS_STD,
        )
    offload.load_model_data(
        vae,
        filename,
        writable_tensors=False,
        preprocess_sd=preprocess_video_vae_state_dict,
        default_dtype=torch.float16,
    )
    vae._model_dtype = torch.float16
    return vae.eval().requires_grad_(False)


def _load_audio_vae(filename: str) -> AutoencoderKLMiniMaxH3Audio:
    # Preserve any computed codec buffers while keeping all learned
    # parameters empty until MMGP streams the checkpoint.
    with init_empty_weights(include_buffers=False):
        vae = AutoencoderKLMiniMaxH3Audio(
            latents_mean=AUDIO_LATENTS_MEAN,
            latents_std=AUDIO_LATENTS_STD,
        )
    offload.load_model_data(
        vae,
        filename,
        writable_tensors=False,
        preprocess_sd=preprocess_audio_vae_state_dict,
        default_dtype=torch.float32,
    )
    vae._model_dtype = torch.float32
    return vae.eval().requires_grad_(False)


class MiniMaxH3Model:
    """Maestro generation wrapper for the separate H3 FL2VA/Ref2VA checkpoints."""

    def __init__(
        self,
        model_filename,
        model_def,
        text_encoder_filename,
        dtype: torch.dtype = torch.bfloat16,
        load_status_callback=None,
        **_kwargs,
    ):
        self.device = torch.device("cuda")
        self.dtype = dtype
        self.model_def = model_def
        self.selected_model_type = str(_kwargs.get("selected_model_type") or "")
        self.reference_mode = bool(model_def.get("minimax_h3_reference_mode", False))
        self.assets_root = model_def.get("minimax_h3_assets_root", "minimax_h3")
        self.transformer = None
        self.conditioner = None
        self.vae = None
        self.audio_vae = None
        self.scheduler = None
        self.audio_scheduler = None
        self._ref2va_handoff_cache = None
        self._last_spectrum_stats = None
        self.__interrupt = False

        transformer_path = _first_path(model_filename)
        if not transformer_path:
            raise FileNotFoundError("MiniMax H3 transformer checkpoint is missing.")
        if not text_encoder_filename:
            raise FileNotFoundError("MiniMax H3 Qwen3-VL conditioner checkpoint is missing.")

        required_assets = model_def.get("required_runtime_assets", {})
        if not isinstance(required_assets, dict):
            raise ValueError("MiniMax H3 required-runtime asset manifest is missing.")
        video_vae_relative = required_assets.get("video_vae")
        audio_vae_relative = required_assets.get("audio_vae")
        text_config_relative = required_assets.get("text_encoder_config")
        processor_relative = required_assets.get("processor")
        if not all(
            isinstance(path, str) and path
            for path in (
                video_vae_relative,
                audio_vae_relative,
                text_config_relative,
            )
        ) or not (
            isinstance(processor_relative, (list, tuple))
            and len(processor_relative) == 7
            and all(isinstance(path, str) and path for path in processor_relative)
            and len({os.path.dirname(path) for path in processor_relative}) == 1
        ):
            raise ValueError("MiniMax H3 required-runtime asset manifest is invalid.")

        video_vae_path = fl.locate_file(video_vae_relative)
        audio_vae_path = fl.locate_file(audio_vae_relative)
        qkv_layout = str(model_def.get("minimax_h3_qkv_layout") or "contiguous")
        qkv_layout = str(
            model_def.get("compatible_model_qkv_layouts", {}).get(
                os.path.basename(transformer_path),
                qkv_layout,
            )
        )

        def report_load_stage(label: str) -> None:
            if callable(load_status_callback):
                load_status_callback(label)

        try:
            report_load_stage("Loading H3 transformer checkpoint")
            self.transformer = _load_transformer(
                transformer_path,
                dtype,
                qkv_layout=qkv_layout,
            )
            report_load_stage("Loading H3 conditioner checkpoint")
            self.conditioner = _load_conditioner(
                text_encoder_filename,
                text_config_relative,
                list(processor_relative),
                dtype,
            )
            report_load_stage("Loading H3 video VAE checkpoint")
            self.vae = _load_video_vae(video_vae_path)
            report_load_stage("Loading H3 audio VAE checkpoint")
            self.audio_vae = _load_audio_vae(audio_vae_path)
            self.scheduler = MiniMaxH3Scheduler(shift=12.0)
            self.audio_scheduler = MiniMaxH3Scheduler(shift=3.0)
        except Exception:
            # A checkpoint OOM can occur before the wrapper is returned to
            # WGP. Sever every component already constructed so the allocator
            # can reclaim the partial stack and a later job can retry cleanly.
            self.release()
            try:
                offload.flush_torch_caches()
            except Exception:
                pass
            raise

    def release(self) -> None:
        """Sever every heavyweight H3 component during model replacement."""
        self.__interrupt = True
        for component_name in ("transformer", "conditioner"):
            component = getattr(self, component_name, None)
            if component is not None:
                try:
                    component._interrupt = True
                except Exception:
                    pass
        self._ref2va_handoff_cache = None
        self._last_spectrum_stats = None
        self.transformer = None
        self.conditioner = None
        self.vae = None
        self.audio_vae = None
        self.scheduler = None
        self.audio_scheduler = None

    @property
    def _interrupt(self) -> bool:
        return self.__interrupt

    @_interrupt.setter
    def _interrupt(self, value: bool) -> None:
        self.__interrupt = bool(value)
        if getattr(self, "transformer", None) is not None:
            self.transformer._interrupt = self.__interrupt
        if getattr(self, "conditioner", None) is not None:
            self.conditioner._interrupt = self.__interrupt

    @property
    def patch_size(self) -> tuple[int, int, int]:
        return tuple(self.transformer.config.patch_size)

    @torch.inference_mode()
    def decode_h3_preview_rows(
        self,
        *,
        packed_rows: torch.Tensor,
        latent_frames: int,
        latent_height: int,
        latent_width: int,
        pixel_frames: int,
        pixel_height: int,
        pixel_width: int,
        channels: int,
        patch_size: tuple[int, int, int],
    ) -> torch.Tensor:
        """Decode detached preview rows through the loaded native video VAE."""

        if self._interrupt:
            raise InterruptedError("MiniMax H3 preview decode was cancelled")
        if self.vae is None:
            raise RuntimeError("MiniMax H3 native video VAE is unavailable")
        if patch_size != self.patch_size:
            raise H3PreviewGeometryError(
                "MiniMax H3 preview patch geometry does not match the model"
            )
        video, _normalized_latents = _decode_h3_video_rows(
            vae=self.vae,
            device=self.device,
            packed_rows=packed_rows,
            latent_frames=latent_frames,
            latent_height=latent_height,
            latent_width=latent_width,
            pixel_frames=pixel_frames,
            pixel_height=pixel_height,
            pixel_width=pixel_width,
            channels=channels,
            patch_size=patch_size,
        )
        if self._interrupt:
            raise InterruptedError("MiniMax H3 preview decode was cancelled")
        return video

    def _encode_keyframes(
        self,
        images: list[Image.Image],
        latent_height: int,
        latent_width: int,
        generator: torch.Generator,
    ) -> torch.Tensor | None:
        if not images:
            return None

        means, stds = _keyframe_latent_stats_cpu()
        pixel_mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN, device=self.device).view(1, -1, 1, 1, 1)
        pixel_std = torch.tensor(MINIMAX_H3_PIXEL_STD, device=self.device).view(1, -1, 1, 1, 1)

        rows = []
        for image in images:
            if self._interrupt:
                return None
            pixels = torch.from_numpy(np.array(image, dtype=np.uint8)).to(self.device)
            pixels = pixels.permute(2, 0, 1)[None, :, None]
            pixels = (pixels.float().div(255.0) - pixel_mean) / pixel_std
            moments = self.vae._encode_clip(pixels)
            posterior = DiagonalGaussianDistribution(moments)
            encoded = posterior.sample(generator=torch.Generator().manual_seed(MINIMAX_H3_KEYFRAME_ENCODE_SEED))
            encoded = encoded.to(torch.float16).float().cpu()
            rows.append(patchify_video_latents((encoded - means) / stds, self.patch_size))

        clean_rows = torch.cat(rows).to(self.device)
        noise = keyframe_condition_noise(
            ((1, latent_height, latent_width),) * len(images),
            self.patch_size,
            24,
            generator=generator,
            device=self.device,
        )
        return self.scheduler.scale_noise(clean_rows, MINIMAX_H3_KEYFRAME_NOISE_AUG, noise)

    def _encode_reference_video(
        self, video: torch.Tensor, *, keep_all_latents: bool = False,
    ) -> torch.Tensor:
        video = video.to(self.device)
        pixel_mean = torch.tensor(MINIMAX_H3_PIXEL_MEAN, device=self.device).view(1, -1, 1, 1, 1)
        pixel_std = torch.tensor(MINIMAX_H3_PIXEL_STD, device=self.device).view(1, -1, 1, 1, 1)
        pixels = video[None].add(1.0).mul_(0.5)
        pixels = (pixels - pixel_mean) / pixel_std
        moments = (
            self.vae._encode_clip(pixels)
            if video.shape[1] == 1 or keep_all_latents
            else self.vae._encode(pixels)
        )
        posterior = DiagonalGaussianDistribution(moments)
        encoded = posterior.sample(
            generator=torch.Generator().manual_seed(MINIMAX_H3_KEYFRAME_ENCODE_SEED)
        )
        means, stds = _keyframe_latent_stats_cpu()
        return (encoded.to(torch.float16).float().cpu() - means) / stds

    def _coerce_waveform(self, waveform, sample_rate: int | None = None) -> torch.Tensor | None:
        if waveform is None:
            return None
        audio = torch.as_tensor(waveform, dtype=torch.float32)
        if audio.ndim == 1:
            audio = audio.unsqueeze(0)
        elif audio.ndim == 2:
            if audio.shape[0] in (1, 2):
                pass
            elif audio.shape[1] in (1, 2):
                audio = audio.transpose(0, 1)
            else:
                raise ValueError(
                    "MiniMax H3 reference audio must be mono or stereo; "
                    f"multichannel input has shape {tuple(audio.shape)}."
                )
        else:
            raise ValueError(f"MiniMax H3 reference audio must be mono or stereo, got {tuple(audio.shape)}.")
        if audio.shape[-1] < 1:
            raise ValueError("MiniMax H3 reference audio cannot be empty")
        if audio.shape[0] == 1:
            audio = audio.repeat(2, 1)
        sample_rate = int(sample_rate or 32000)
        if sample_rate != 32000:
            from torchaudio.functional import resample

            audio = resample(audio, sample_rate, 32000)
        return audio

    def _load_waveform(self, path) -> torch.Tensor | None:
        if path is None:
            return None
        import soundfile as sf

        waveform, sample_rate = sf.read(os.fspath(path), dtype="float32", always_2d=True)
        return self._coerce_waveform(waveform, sample_rate)

    def _encode_reference_audio(self, waveform: torch.Tensor) -> torch.Tensor:
        posterior = self.audio_vae.encode(waveform[:, None].to(self.device)).latent_dist
        encoded = posterior.mode().float().cpu()
        mean = torch.tensor(AUDIO_LATENTS_MEAN, dtype=torch.float32).view(1, -1, 1)
        std = torch.tensor(AUDIO_LATENTS_STD, dtype=torch.float32).view(1, -1, 1)
        return (encoded - mean) / std

    def _prepare_references(
        self,
        image_refs,
        video_refs,
        audio_refs,
        video_soundtracks,
        height: int,
        width: int,
        fps: float,
        image_refs_relative_size: float,
        generator: torch.Generator,
        override_last_video_latent: torch.Tensor | None = None,
        override_last_audio_latent: torch.Tensor | None = None,
    ):
        presentation: list[dict] = []
        prepared: list[MiniMaxH3PreparedReference] = []
        visual_latents: list[torch.Tensor] = []
        audio_latents: list[torch.Tensor] = []

        for source in image_refs:
            image = _tensor_to_pil(source)
            if image is None:
                continue
            target_height, target_width = _reference_canvas_size(
                image.width,
                image.height,
                width * height * float(image_refs_relative_size) / 100.0,
            )
            if image.size != (target_width, target_height):
                image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            video = _pil_to_video_tensor(image)
            latent = self._encode_reference_video(video)
            presentation.append({"type": "image", "frames": _qwen_video_frames(video)})
            visual_latents.append(latent)
            prepared.append(
                MiniMaxH3PreparedReference(
                    kind="image",
                    latent_height=latent.shape[-2],
                    latent_width=latent.shape[-1],
                )
            )

        for video_index, (source, soundtrack) in enumerate(zip(video_refs, video_soundtracks)):
            video = _as_video_tensor(source)
            if video is None:
                continue
            if video.shape[1] < 5 or (video.shape[1] - 5) % 17:
                raise ValueError(
                    "MiniMax H3 reference videos must contain 17n+5 preprocessed frames; "
                    f"got {video.shape[1]}."
                )
            is_last_video = video_index == len(video_refs) - 1
            latent = (
                override_last_video_latent.detach().float().cpu()
                if is_last_video and override_last_video_latent is not None
                else self._encode_reference_video(video)
            )
            soundtrack_latent = (
                override_last_audio_latent.detach().float().cpu()
                if is_last_video and override_last_audio_latent is not None
                else None
            )
            if soundtrack is not None and soundtrack_latent is None:
                soundtrack_latent = self._encode_reference_audio(soundtrack)
            if soundtrack_latent is not None:
                presentation.append({"type": "audio"})
                audio_latents.append(soundtrack_latent)
            sample_indices: list[int] = []
            cursor = 0.0
            while round(cursor) < video.shape[1]:
                if not sample_indices or round(cursor) > sample_indices[-1]:
                    sample_indices.append(round(cursor))
                cursor += fps / 2.0
            presentation.append(
                {
                    "type": "video",
                    "frames": _qwen_video_frames(video[:, sample_indices]),
                    "timestamps": [index / fps for index in sample_indices],
                }
            )
            visual_latents.append(latent)
            prepared.append(
                MiniMaxH3PreparedReference(
                    kind="video",
                    num_latent_frames=latent.shape[2],
                    latent_height=latent.shape[-2],
                    latent_width=latent.shape[-1],
                    num_audio_latents=(
                        0 if soundtrack_latent is None else soundtrack_latent.shape[-1]
                    ),
                )
            )

        for waveform in audio_refs:
            latent = self._encode_reference_audio(waveform)
            presentation.append({"type": "audio"})
            audio_latents.append(latent)
            prepared.append(
                MiniMaxH3PreparedReference(kind="audio", num_audio_latents=latent.shape[-1])
            )

        condition_video_rows = None
        if visual_latents:
            clean_rows = torch.cat(
                [patchify_video_latents(latent, self.patch_size) for latent in visual_latents]
            ).to(self.device)
            condition_shapes = tuple(
                (latent.shape[2], latent.shape[-2], latent.shape[-1]) for latent in visual_latents
            )
            noise = keyframe_condition_noise(
                condition_shapes,
                self.patch_size,
                24,
                generator=generator,
                device=self.device,
            )
            condition_video_rows = self.scheduler.scale_noise(
                clean_rows, MINIMAX_H3_KEYFRAME_NOISE_AUG, noise
            )
        condition_audio_rows = (
            torch.cat([_audio_rows(latent) for latent in audio_latents]).to(self.device)
            if audio_latents
            else None
        )
        return presentation, prepared, condition_video_rows, condition_audio_rows

    @torch.inference_mode()
    def generate(
        self,
        input_prompt: str,
        image_start=None,
        image_end=None,
        input_frames=None,
        input_frames2=None,
        input_frames3=None,
        input_ref_images=None,
        input_waveform=None,
        input_waveform_sample_rate=None,
        audio_guide=None,
        audio_guide2=None,
        audio_guide3=None,
        audio_prompt_type: str = "",
        video_prompt_type: str = "",
        image_refs_relative_size: float = 100,
        frame_num: int = 124,
        height: int = 480,
        width: int = 864,
        sampling_steps: int = 20,
        seed: int | None = None,
        callback=None,
        fps: float = MINIMAX_H3_FPS,
        **_kwargs,
    ):
        self._interrupt = False
        self._last_spectrum_stats = None
        custom_settings = _kwargs.get("custom_settings")
        if not isinstance(custom_settings, dict):
            custom_settings = {}
        native_boundary_enabled = (
            custom_settings.get("h3_native_boundary_conditioning") is True
        )
        progress_status = _kwargs.get("set_progress_status")

        def report_phase(label: str) -> None:
            if callable(progress_status):
                progress_status(label)
        from services.h3_turbo import (
            resolve_h3_turbo_schedule,
            turbo_requested,
            validate_turbo_request,
        )

        turbo_enabled = turbo_requested(custom_settings)
        attention_engine = str(
            custom_settings.get("h3_attention_engine")
            or ("sdpa" if turbo_enabled else "sol_attn")
        )
        if attention_engine not in {"sdpa", "sol_attn", "sage2"}:
            raise ValueError(f"Unknown MiniMax H3 attention engine: {attention_engine}")
        if attention_engine == "sage2":
            from services.h3_acceleration import get_h3_acceleration_status

            sage2 = get_h3_acceleration_status(probe_kernel=True)["sage2"]
            if not sage2["available"]:
                reason = sage2.get("reason") or "official SageAttention2++ kernel unavailable"
                raise RuntimeError(
                    f"MiniMax H3 SageAttention2++ cannot start: {reason}. "
                    "Select Dense SDPA explicitly to continue."
                )
        from .spectrum import validate_spectrum_request

        def spectrum_input_present(value) -> bool:
            if value is None:
                return False
            if isinstance(value, torch.Tensor):
                return value.numel() > 0
            if isinstance(value, (str, bytes, list, tuple, dict, set)):
                return len(value) > 0
            return True

        spectrum_semantic_inputs = spectrum_input_present(
            input_ref_images
        ) or any(
            spectrum_input_present(item)
            for item in (
                input_frames, input_frames2, input_frames3,
                input_waveform, audio_guide, audio_guide2, audio_guide3,
            )
        )
        spectrum_config = validate_spectrum_request(
            selected_model_type=str(
                getattr(self, "selected_model_type", None)
                or getattr(self, "transformer_type", None)
                or "minimax_h3"
            ),
            model_def=getattr(self, "model_def", None),
            reference_mode=bool(
                getattr(self, "reference_mode", False) or spectrum_semantic_inputs
            ),
            sampling_steps=sampling_steps,
            attention_engine=attention_engine,
            custom_settings=custom_settings,
            activated_loras=_kwargs.get("activated_loras"),
            loras_multipliers=_kwargs.get("loras_multipliers"),
            skip_steps_cache_type=(
                _kwargs.get("skip_steps_cache_type") or _kwargs.get("tea_cache")
            ),
            native_boundary=native_boundary_enabled,
        )
        from services.h3_lightx2v import (
            lightx2v_scheduler_grid_points, validate_lightx2v_request,
        )
        lightx2v_enabled = validate_lightx2v_request(
            selected_model_type=str(
                getattr(self, "selected_model_type", None)
                or getattr(self, "transformer_type", None)
                or "minimax_h3"
            ),
            model_def=getattr(self, "model_def", None) or {},
            custom_settings=custom_settings,
            authored_steps=sampling_steps,
            semantic_references=spectrum_semantic_inputs,
            multisegment=(
                isinstance(_kwargs.get("multi_clip_info"), dict)
                and int(_kwargs["multi_clip_info"].get("total", 1) or 1) > 1
            ),
            activated_loras=_kwargs.get("activated_loras"),
            loras_multipliers=_kwargs.get("loras_multipliers"),
            skip_steps_cache_type=_kwargs.get("skip_steps_cache_type") or _kwargs.get("tea_cache"),
            native_boundary=native_boundary_enabled,
        )
        from services.h3_audio import resolve_h3_audio_roles, source_audio_requested

        experimental_source_audio = source_audio_requested(custom_settings)
        declared_semantic_references = (
            bool(input_ref_images)
            or "V" in (video_prompt_type or "")
            or (
                not experimental_source_audio
                and any(letter in (audio_prompt_type or "") for letter in "ABCK")
            )
        )
        source_audio_roles = resolve_h3_audio_roles(
            selected_model_type=str(
                getattr(self, "selected_model_type", None)
                or getattr(self, "transformer_type", None)
                or "minimax_h3"
            ),
            model_def=getattr(self, "model_def", None) or {},
            custom_settings=custom_settings,
            sampling_steps=sampling_steps,
            attention_engine=attention_engine,
            audio_prompt_type=audio_prompt_type,
            audio_guides=(audio_guide, audio_guide2, audio_guide3),
            final_audio=_kwargs.get("audio_source"),
            semantic_references=declared_semantic_references,
            multisegment=(
                isinstance(_kwargs.get("multi_clip_info"), dict)
                and int(_kwargs["multi_clip_info"].get("total", 1) or 1) > 1
            ),
            activated_loras=_kwargs.get("activated_loras"),
            loras_multipliers=_kwargs.get("loras_multipliers"),
            skip_steps_cache_type=(
                _kwargs.get("skip_steps_cache_type") or _kwargs.get("tea_cache")
            ),
            native_boundary=native_boundary_enabled,
        )
        if turbo_enabled:
            validate_turbo_request(
                base_model_type="minimax_h3_ref2va" if self.reference_mode else "minimax_h3",
                model_def=self.model_def,
                custom_settings=custom_settings,
                authored_steps=sampling_steps,
                activated_loras=_kwargs.get("activated_loras"),
                loras_multipliers=_kwargs.get("loras_multipliers"),
                skip_steps_cache_type=_kwargs.get("skip_steps_cache_type"),
                _h3_turbo_validation_authorized=(
                    _kwargs.get("_h3_turbo_validation_authorized") is True
                ),
            )
            runtime_state = self.transformer.h3_turbo_runtime_state()
            expected_adaln = 51 if runtime_state["curve_mode"] else 0
            expected_residual = 0
            if runtime_state["backbone_mode"] == "residual_output":
                expected_residual = 208 if runtime_state["curve_mode"] else 259
            if (
                not runtime_state["active"]
                or runtime_state["adaln_modules"] != expected_adaln
                or runtime_state["residual_modules"] != expected_residual
            ):
                raise RuntimeError(
                    "H3 Turbo managed LoRA was not fully activated before generation"
                )
        handoff_chain_id = str(custom_settings.get("h3_ref2va_chain_id") or "")
        handoff_mode = str(custom_settings.get("h3_ref2va_handoff") or "")
        cached_handoff = getattr(self, "_ref2va_handoff_cache", None)
        use_cached_handoff = bool(
            self.reference_mode
            and not native_boundary_enabled
            and handoff_mode == "temporal_tail"
            and handoff_chain_id
            and isinstance(cached_handoff, dict)
            and cached_handoff.get("chain_id") == handoff_chain_id
        )
        # A Ref2VA handoff is single-consumer state. Keep the candidate local
        # for this request and clear the instance slot before any cancellable
        # work; only a fully decoded successor may publish the next handoff.
        if self.reference_mode:
            self._ref2va_handoff_cache = None
        if not isinstance(input_prompt, str):
            raise ValueError("MiniMax H3 accepts one text prompt per generation.")
        if height % 32 or width % 32:
            raise ValueError(f"MiniMax H3 dimensions must be multiples of 32, got {width}x{height}.")

        fps = float(fps)
        if fps != MINIMAX_H3_FPS:
            raise ValueError(f"MiniMax H3 runs at its native {MINIMAX_H3_FPS} fps.")
        frame_num = align_num_frames(int(frame_num))
        continuation = _as_video_tensor(_kwargs.get("input_video"))
        prefix_frames_count = int(_kwargs.get("prefix_frames_count") or 0)
        native_continuation = bool(
            native_boundary_enabled
            and continuation is not None
            and prefix_frames_count
        )
        if native_continuation:
            from services.h3_boundary_policy import (
                H3_NATIVE_HISTORY_FRAMES,
                H3_NATIVE_OVERLAP_FRAMES,
            )

            if fps != MINIMAX_H3_FPS:
                raise ValueError("Native H3 boundary conditioning requires 24 fps")
            if prefix_frames_count != H3_NATIVE_OVERLAP_FRAMES:
                raise ValueError(
                    "Native H3 boundary conditioning requires exactly "
                    f"{H3_NATIVE_OVERLAP_FRAMES} input frames"
                )
            if continuation.shape[1] != H3_NATIVE_OVERLAP_FRAMES:
                raise ValueError(
                    "Native H3 boundary media did not contain exactly "
                    f"{H3_NATIVE_OVERLAP_FRAMES} frames"
                )
            target_frame_num = frame_num - H3_NATIVE_HISTORY_FRAMES
            if target_frame_num < 1 or align_num_frames(target_frame_num) != target_frame_num:
                raise ValueError(
                    "Native H3 boundary target must remain on the 17n+5 frame grid"
                )
        else:
            target_frame_num = frame_num
        duration = target_frame_num / fps
        minimum_duration = 4.0 if self.reference_mode else MINIMAX_H3_MIN_DURATION
        if not minimum_duration <= duration <= MINIMAX_H3_MAX_DURATION:
            raise ValueError(
                f"MiniMax H3 supports {minimum_duration:g}-{MINIMAX_H3_MAX_DURATION:g}s at 24 fps; "
                f"the aligned request is {frame_num} frames ({duration:.3f}s)."
            )
        if int(sampling_steps) < 2:
            raise ValueError("MiniMax H3 needs at least two scheduler grid points.")

        if input_ref_images is None:
            image_refs = []
        elif isinstance(input_ref_images, (list, tuple)):
            image_refs = list(input_ref_images)
        else:
            image_refs = [input_ref_images]
        selected_video_refs = []
        if "V" in (video_prompt_type or ""):
            selected_video_refs.append(input_frames)
            if "+" in (video_prompt_type or ""):
                selected_video_refs.append(input_frames2)
            if "++" in (video_prompt_type or "") or input_frames3 is not None:
                selected_video_refs.append(input_frames3)
        video_refs = [source for source in selected_video_refs if source is not None]

        loaded_audio_guides = [
            self._load_waveform(path)
            for path in (audio_guide, audio_guide2, audio_guide3)
        ]
        source_audio_waveforms: list[torch.Tensor] = []
        if source_audio_roles.experimental:
            source_audio_waveforms = [
                self._load_waveform(path)
                for path in (
                    source_audio_roles.reference_audios
                    if source_audio_roles.mode == "reference_only"
                    else (source_audio_roles.drive_audio,)
                )
            ]
            if any(waveform is None for waveform in source_audio_waveforms):
                raise ValueError(
                    "MiniMax H3 source-audio slots must resolve to readable audio"
                )
        soundtrack_mode = "K" in (audio_prompt_type or "")
        video_soundtracks = (
            loaded_audio_guides[: len(video_refs)]
            if soundtrack_mode
            else [None] * len(video_refs)
        )
        if soundtrack_mode and any(item is None for item in video_soundtracks):
            raise ValueError(
                "MiniMax H3 Ref2VA soundtrack references require one extracted audio track per reference video."
            )
        audio_refs = []
        if not soundtrack_mode and not source_audio_roles.experimental:
            if "A" in (audio_prompt_type or ""):
                first_audio = loaded_audio_guides[0]
                if first_audio is None:
                    first_audio = self._coerce_waveform(
                        input_waveform, input_waveform_sample_rate
                    )
                audio_refs.append(first_audio)
            if "B" in (audio_prompt_type or ""):
                audio_refs.append(loaded_audio_guides[1])
            if "C" in (audio_prompt_type or ""):
                audio_refs.append(loaded_audio_guides[2])
            audio_refs = [source for source in audio_refs if source is not None]

        if self.reference_mode:
            if (
                (image_start is not None or image_end is not None)
                and not native_boundary_enabled
            ):
                raise ValueError(
                    "MiniMax H3 first/last-frame conditioning requires the FL2VA checkpoint; "
                    "Ref2VA references are arbitrary context."
                )
            if len(image_refs) > 9:
                raise ValueError("MiniMax H3 Ref2VA accepts at most 9 reference images.")
            if len(video_refs) > 3:
                raise ValueError("MiniMax H3 Ref2VA accepts at most 3 reference videos.")
            audio_count = len(audio_refs) + sum(item is not None for item in video_soundtracks)
            if audio_count > 3:
                raise ValueError("MiniMax H3 Ref2VA accepts at most 3 reference audio clips.")
            visual_count = len(image_refs) + len(video_refs)
            if audio_count > visual_count:
                raise ValueError(
                    "MiniMax H3 Ref2VA requires at least as many visual references as audio references."
                )
            mixed_count = visual_count + (0 if soundtrack_mode else len(audio_refs))
            if mixed_count > 12:
                raise ValueError("MiniMax H3 Ref2VA accepts at most 12 mixed reference files.")
            video_durations = [
                _as_video_tensor(item).shape[1] / fps for item in video_refs
            ]
            for index, reference_duration in enumerate(video_durations, 1):
                if not 2.0 <= reference_duration <= 15.0:
                    raise ValueError(
                        f"MiniMax H3 reference video {index} must be 2-15 seconds; "
                        f"found {reference_duration:.2f}s."
                    )
            total_video_duration = sum(video_durations)
            if total_video_duration > 15.0:
                raise ValueError(
                    "MiniMax H3 reference videos must total at most 15 seconds; "
                    f"found {total_video_duration:.2f}s."
                )
            reference_audio = audio_refs + [
                item for item in video_soundtracks if item is not None
            ]
            audio_durations = [item.shape[-1] / 32000.0 for item in reference_audio]
            for index, reference_duration in enumerate(audio_durations, 1):
                if not 2.0 <= reference_duration <= 15.0:
                    raise ValueError(
                        f"MiniMax H3 reference audio {index} must be 2-15 seconds; "
                        f"found {reference_duration:.2f}s."
                    )
            total_audio_duration = sum(audio_durations)
            if total_audio_duration > 15.0:
                raise ValueError(
                    "MiniMax H3 reference audio must total at most 15 seconds; "
                    f"found {total_audio_duration:.2f}s."
                )
        elif image_refs or video_refs or audio_refs or soundtrack_mode:
            raise ValueError(
                "Arbitrary image, video, and audio references require the MiniMax H3 Ref2VA checkpoint."
            )

        user_keyframes = [
            item for item in (_tensor_to_pil(image_start), _tensor_to_pil(image_end))
            if item is not None
        ]
        user_anchors = tuple(
            anchor
            for anchor, item in (("first", image_start), ("last", image_end))
            if item is not None
        )
        user_keyframes = [
            prepare_keyframe_image(image, height, width, stretch=index == 0)
            for index, image in enumerate(user_keyframes)
        ]

        boundary_history = None
        boundary_waveform = None
        boundary_keyframe = None
        boundary_audio_rows = None
        boundary_audio_anchors = ()
        if native_continuation:
            boundary_history = continuation[:, :H3_NATIVE_HISTORY_FRAMES]
            boundary_keyframe = prepare_keyframe_image(
                _tensor_to_pil(continuation[:, -1:]),
                height,
                width,
                stretch=True,
            )
            boundary_waveform = self._coerce_waveform(
                input_waveform,
                input_waveform_sample_rate,
            )
            if boundary_waveform is None:
                raise ValueError(
                    "Native H3 boundary conditioning requires 32 kHz stereo audio"
                )
            expected_samples = round(
                H3_NATIVE_OVERLAP_FRAMES / MINIMAX_H3_FPS * 32000
            )
            if boundary_waveform.shape[-1] != expected_samples:
                raise ValueError(
                    "Native H3 boundary audio must cover exactly 18 frames at 32 kHz"
                )
        keyframes = ([boundary_keyframe] if boundary_keyframe is not None else []) + user_keyframes
        anchors = (
            (("history", MINIMAX_H3_LATENTS_PER_CHUNK), "first")
            if native_continuation else ()
        ) + user_anchors

        request_seed = int(torch.seed() if seed is None else seed)
        generator = torch.Generator(device=self.device).manual_seed(request_seed)
        num_latent_frames = video_latent_num_frames(target_frame_num)
        latent_height = height // self.vae.spatial_compression_ratio
        latent_width = width // self.vae.spatial_compression_ratio
        num_audio_latents = audio_latent_num_frames(target_frame_num)

        source_audio_target_rows = None
        source_audio_condition_rows = None
        source_audio_condition_anchors = ()
        if source_audio_roles.experimental:
            # H3's audio VAE advances at 40 latents/s with an 800-sample hop.
            # Resolve the target clock structurally; no waveform content is
            # inspected to choose a mode, role, or ordinal.
            target_samples = num_audio_latents * (32000 // MINIMAX_H3_AUDIO_LATENTS_PER_SECOND)
            encoded_sources = [
                _fit_h3_source_audio_latents(
                    self._encode_reference_audio(
                        _fit_h3_source_waveform(waveform, target_samples)
                    ),
                    num_audio_latents,
                )
                for waveform in source_audio_waveforms
            ]
            if source_audio_roles.mode == "reference_only":
                source_audio_condition_rows = torch.cat(
                    [_audio_rows(latent) for latent in encoded_sources]
                ).to(self.device)
                source_audio_condition_anchors = tuple(
                    ("first", int(latent.shape[-1])) for latent in encoded_sources
                )
            else:
                source_audio_target_rows = _audio_rows(encoded_sources[0]).to(
                    self.device
                )

        boundary_video_rows = None
        if native_continuation:
            report_phase("Encoding H3 native boundary history")
            history_latent = self._encode_reference_video(
                boundary_history, keep_all_latents=True,
            )
            if history_latent.shape[2] != MINIMAX_H3_LATENTS_PER_CHUNK:
                raise RuntimeError("H3 boundary video history encoded to an unexpected size")
            history_rows = patchify_video_latents(history_latent, self.patch_size).to(
                self.device
            )
            history_noise = keyframe_condition_noise(
                ((history_latent.shape[2], latent_height, latent_width),),
                self.patch_size,
                24,
                generator=generator,
                device=self.device,
            )
            boundary_video_rows = self.scheduler.scale_noise(
                history_rows, MINIMAX_H3_KEYFRAME_NOISE_AUG, history_noise,
            )
            encoded_boundary_audio = self._encode_reference_audio(boundary_waveform)
            boundary_latents = min(
                encoded_boundary_audio.shape[-1],
                max(1, round(MINIMAX_H3_AUDIO_LATENTS_PER_SECOND / MINIMAX_H3_FPS)),
            )
            history_audio_latents = encoded_boundary_audio.shape[-1] - boundary_latents
            boundary_audio_parts = []
            audio_anchor_parts = []
            if history_audio_latents:
                boundary_audio_parts.append(
                    _audio_rows(encoded_boundary_audio[..., :history_audio_latents])
                )
                audio_anchor_parts.append(("history", history_audio_latents))
            boundary_audio_parts.append(
                _audio_rows(encoded_boundary_audio[..., history_audio_latents:])
            )
            audio_anchor_parts.append(("first", boundary_latents))
            boundary_audio_rows = torch.cat(boundary_audio_parts).to(self.device)
            boundary_audio_anchors = tuple(audio_anchor_parts)

        if keyframes:
            report_phase("Encoding H3 keyframes")
        condition_rows = self._encode_keyframes(
            keyframes,
            latent_height,
            latent_width,
            generator,
        )
        if self._interrupt:
            return None
        if boundary_video_rows is not None:
            condition_rows = (
                boundary_video_rows
                if condition_rows is None
                else torch.cat([boundary_video_rows, condition_rows])
            )

        reference_presentation = []
        references: list[MiniMaxH3PreparedReference] = []
        reference_video_rows = reference_audio_rows = None
        if self.reference_mode:
            report_phase("Encoding H3 references")
            (
                reference_presentation,
                references,
                reference_video_rows,
                reference_audio_rows,
            ) = self._prepare_references(
                image_refs,
                video_refs,
                audio_refs,
                video_soundtracks,
                height,
                width,
                fps,
                image_refs_relative_size,
                generator,
                override_last_video_latent=(
                    cached_handoff.get("video") if use_cached_handoff else None
                ),
                override_last_audio_latent=(
                    cached_handoff.get("audio")
                    if use_cached_handoff
                    and bool(custom_settings.get("h3_ref2va_handoff_audio"))
                    else None
                ),
            )

        prompt_presentation = list(reference_presentation)
        prompt_keyframes = keyframes or None
        if source_audio_roles.experimental:
            prompt_presentation.extend(
                {"type": "audio"} for _ in source_audio_waveforms
            )
        if (native_boundary_enabled or prompt_presentation) and keyframes:
            prompt_presentation = [
                {
                    "type": "image",
                    "frames": _qwen_video_frames(
                        _pil_to_video_tensor(image)
                    ),
                }
                for image in keyframes
            ] + prompt_presentation
            prompt_keyframes = None

        from services.h3_audio import (
            remap_prompt_audio_ordinals,
            validate_prompt_media_ordinals,
        )

        prompt_for_conditioner = input_prompt
        if source_audio_roles.audio_ordinal_remap:
            prompt_for_conditioner = remap_prompt_audio_ordinals(
                prompt_for_conditioner,
                dict(source_audio_roles.audio_ordinal_remap),
            )
        validate_prompt_media_ordinals(
            prompt_for_conditioner,
            picture_count=(
                sum(item.get("type") == "image" for item in prompt_presentation)
                if prompt_presentation else len(keyframes)
            ),
            video_count=sum(
                item.get("type") == "video" for item in prompt_presentation
            ),
            audio_count=(
                sum(item.get("type") == "audio" for item in prompt_presentation)
            ),
        )

        report_phase("Encoding H3 prompt")
        prompt_embeds, text_tags = self.conditioner(
            prompt_for_conditioner,
            self.device,
            prompt_keyframes,
            presentation=prompt_presentation or None,
        )
        if prompt_embeds is None or self._interrupt:
            return None
        audio_condition_anchors = (
            tuple(boundary_audio_anchors) + tuple(source_audio_condition_anchors)
        )
        if self.reference_mode:
            layout = build_ref2va_packed_sequence(
                text_tags,
                references,
                num_latent_frames,
                latent_height,
                latent_width,
                num_audio_latents,
                self.patch_size,
                keyframe_anchors=anchors,
                audio_condition_anchors=audio_condition_anchors,
            )
        else:
            layout = build_packed_sequence(
                text_tags,
                num_latent_frames,
                latent_height,
                latent_width,
                num_audio_latents,
                self.patch_size,
                anchors,
                audio_condition_anchors=audio_condition_anchors,
                target_condition_audio_latents=(
                    num_audio_latents
                    if source_audio_roles.mode == "lock_source" else 0
                ),
            )

        video_noise = randn_tensor(
            (1, 24, num_latent_frames, latent_height, latent_width),
            generator=generator,
            device=self.device,
            dtype=torch.float32,
        )
        video_rows = patchify_video_latents(video_noise, self.patch_size)
        audio_rows = randn_tensor(
            (num_audio_latents * MINIMAX_H3_AUDIO_CHANNELS, 32),
            generator=generator,
            device=self.device,
            dtype=torch.float32,
        )
        locked_target_audio_rows = None
        if source_audio_target_rows is not None:
            if source_audio_roles.mode == "lock_source":
                audio_rows = source_audio_target_rows.clone()
                locked_target_audio_rows = source_audio_target_rows.clone()
            elif source_audio_roles.mode == "remix_source":
                remix_noise = audio_rows
                audio_rows = self.audio_scheduler.scale_noise(
                    source_audio_target_rows,
                    1.0 - source_audio_roles.remix_strength,
                    remix_noise,
                )
        condition_video_parts = [
            rows for rows in (condition_rows, reference_video_rows)
            if rows is not None
        ]
        if condition_video_parts:
            video_rows = torch.cat([*condition_video_parts, video_rows])
        if reference_audio_rows is not None:
            audio_rows = torch.cat([reference_audio_rows, audio_rows])
        if boundary_audio_rows is not None:
            audio_rows = torch.cat([boundary_audio_rows, audio_rows])
        if source_audio_condition_rows is not None:
            audio_rows = torch.cat([source_audio_condition_rows, audio_rows])

        turbo_schedule = None
        if lightx2v_enabled:
            video_scheduler_points = lightx2v_scheduler_grid_points(sampling_steps)
            audio_scheduler_points = video_scheduler_points
        elif spectrum_config is not None:
            from .spectrum import spectrum_scheduler_grid_points

            video_scheduler_points = spectrum_scheduler_grid_points(sampling_steps)
            audio_scheduler_points = video_scheduler_points
        elif turbo_enabled:
            turbo_schedule = resolve_h3_turbo_schedule(sampling_steps)
            video_scheduler_points = turbo_schedule.video_grid_points
            audio_scheduler_points = turbo_schedule.audio_grid_points
        else:
            # MiniMaxH3Scheduler includes terminal zero in its grid. Authored
            # native steps are model evaluations, so N/N needs N+1 points.
            video_scheduler_points = int(sampling_steps) + 1
            audio_scheduler_points = video_scheduler_points
        self.scheduler.set_timesteps(video_scheduler_points, device=self.device)
        self.audio_scheduler.set_timesteps(
            audio_scheduler_points, device=self.device,
        )
        if source_audio_roles.mode == "remix_source":
            # Preserve the paired call count while beginning only the audio
            # clock at the requested source-denoise strength.  The two row
            # modalities already carry independent timestep values.
            remix_sigmas = (
                self.audio_scheduler.sigmas.detach().float().cpu()
                * source_audio_roles.remix_strength
            )
            self.audio_scheduler.set_timesteps(
                sigmas=remix_sigmas,
                device=self.device,
            )
        video_timesteps = self.scheduler.timesteps
        base_audio_timesteps = self.audio_scheduler.timesteps
        if turbo_schedule is not None:
            timesteps = tuple(
                video_timesteps[index]
                for index in turbo_schedule.video_timestep_indices
            )
            audio_timesteps = tuple(
                base_audio_timesteps[index]
                for index in turbo_schedule.audio_timestep_indices
            )
            video_advance_ticks = turbo_schedule.video_advance_ticks
        else:
            timesteps = video_timesteps
            audio_timesteps = base_audio_timesteps
            video_advance_ticks = tuple(range(len(timesteps)))
        if len(timesteps) != len(audio_timesteps):
            raise RuntimeError(
                "MiniMax H3 effective video/audio master schedules must have equal lengths"
            )
        report_phase("Preparing H3 denoising schedule")
        row_plan = [
            tuple(
                tensor.to(self.device)
                for tensor in build_row_timesteps(
                    layout,
                    float(video_timestep),
                    float(audio_timestep),
                    max(float(video_timestep), MINIMAX_H3_KEYFRAME_NOISE_AUG),
                    1.0,
                )
            )
            for video_timestep, audio_timestep in zip(timesteps, audio_timesteps)
        ]
        token_tags = layout.token_tags.to(self.device)
        position_ids = layout.position_ids.to(self.device)
        video_indices = layout.video_indices.to(self.device)
        audio_indices = layout.audio_indices.to(self.device)
        text_indices = layout.text_indices.to(self.device)

        if callback is not None:
            callback(-1, None, True, override_num_inference_steps=len(timesteps))
        def run_transformer_step(
            index, unique_timesteps, timestep_indices, spectrum_phase=None,
        ):
            transformer_kwargs = dict(
                hidden_states=video_rows[None],
                audio_hidden_states=audio_rows[None],
                encoder_hidden_states=prompt_embeds,
                timestep=unique_timesteps,
                timestep_indices=timestep_indices,
                token_tags=token_tags,
                position_ids=position_ids,
                video_indices=video_indices,
                audio_indices=audio_indices,
                text_indices=text_indices,
                h3_attention_engine=attention_engine,
                h3_step_index=index,
                h3_sol_tau=custom_settings.get("h3_sol_tau", 1.0),
                h3_sol_dense_steps=custom_settings.get("h3_sol_dense_steps", 10),
                h3_sol_dense_blocks=custom_settings.get("h3_sol_dense_blocks", 2),
                h3_sol_min_tokens=custom_settings.get("h3_sol_min_tokens", 4096),
                h3_sol_sink_tokens=int(
                    layout.video_indices[layout.num_condition_video_rows].item()
                ),
                return_dict=False,
            )
            if spectrum_phase is not None:
                transformer_kwargs.update({
                    "h3_spectrum_controller": controller,
                    "h3_spectrum_phase": spectrum_phase,
                    "h3_spectrum_context_signature": context_signature,
                    "h3_spectrum_step_signature": step_signatures[index],
                    "h3_spectrum_num_condition_video_rows": (
                        layout.num_condition_video_rows
                    ),
                    "h3_spectrum_num_condition_audio_rows": (
                        layout.num_condition_audio_rows
                    ),
                })
            return self.transformer(**transformer_kwargs)

        def advance_latents(
            prediction,
            video_timestep,
            audio_timestep,
            *,
            advance_video=True,
        ):
            _advance_paired_h3_latents(
                video_rows=video_rows,
                audio_rows=audio_rows,
                prediction=prediction,
                video_timestep=video_timestep,
                audio_timestep=audio_timestep,
                video_scheduler=self.scheduler,
                audio_scheduler=self.audio_scheduler,
                num_condition_video_rows=layout.num_condition_video_rows,
                num_condition_audio_rows=layout.num_condition_audio_rows,
                locked_target_audio_rows=locked_target_audio_rows,
                advance_video=advance_video,
            )

        def reset_denoising_schedulers():
            _reset_paired_h3_schedulers(
                self.scheduler,
                self.audio_scheduler,
                video_scheduler_points,
                self.device,
                audio_scheduler_points,
            )

        def run_native_denoising(*, progress_label="MiniMax H3 denoising"):
            report_phase("Running first H3 denoising step (runtime warmup)")
            with tqdm(total=len(timesteps), desc=progress_label) as progress:
                def after_step(index):
                    if index == 0:
                        report_phase("H3 denoising")
                    if callback is not None:
                        callback(index, None)
                    progress.update()

                return _run_h3_master_schedule(
                    timesteps=timesteps,
                    audio_timesteps=audio_timesteps,
                    row_plan=row_plan,
                    video_advance_ticks=video_advance_ticks,
                    interrupt_requested=lambda: self._interrupt,
                    predict=run_transformer_step,
                    advance=advance_latents,
                    after_step=after_step,
                    reset=reset_denoising_schedulers,
                )

        if spectrum_config is None:
            if not run_native_denoising():
                return None
        else:
            from .spectrum import (
                SpectrumGenerationController,
                SpectrumStateError,
                run_length_tensor_signature,
                small_tensor_signature,
                tensor_identity,
            )

            initial_video_rows = video_rows.detach().clone()
            initial_audio_rows = audio_rows.detach().clone()
            context_signature = (
                tensor_identity(prompt_embeds),
                tensor_identity(token_tags),
                tensor_identity(position_ids),
                tensor_identity(video_indices),
                tensor_identity(audio_indices),
                tensor_identity(text_indices),
                tensor_identity(video_rows),
                tensor_identity(audio_rows),
                int(layout.num_condition_video_rows),
                int(layout.num_condition_audio_rows),
                int(num_latent_frames),
                int(latent_height),
                int(latent_width),
                int(num_audio_latents),
            )
            step_signatures = tuple(
                (
                    float(video_timestep),
                    float(audio_timestep),
                    small_tensor_signature(unique_timesteps),
                    run_length_tensor_signature(timestep_indices),
                )
                for (video_timestep, audio_timestep), (
                    unique_timesteps,
                    timestep_indices,
                ) in zip(zip(timesteps, audio_timesteps), row_plan)
            )
            controller = SpectrumGenerationController(
                spectrum_config,
                total_steps=len(timesteps),
                context_signature=context_signature,
                step_signatures=step_signatures,
                audio_row_count=(
                    audio_rows.shape[0] - layout.num_condition_audio_rows
                ),
                video_row_count=(
                    video_rows.shape[0] - layout.num_condition_video_rows
                ),
            )
            spectrum_reset_reason = "completed"
            spectrum_capture_seconds = None
            spectrum_replay_seconds = None
            try:
                report_phase("Spectrum H3 anchor capture")
                capture_started = time.perf_counter()
                with tqdm(total=len(timesteps), desc="Spectrum H3 capture") as progress:
                    for index, (video_timestep, audio_timestep) in enumerate(
                        zip(timesteps, audio_timesteps)
                    ):
                        if self._interrupt:
                            spectrum_reset_reason = "cancelled"
                            return None
                        unique_timesteps, timestep_indices = row_plan[index]
                        prediction = run_transformer_step(
                            index,
                            unique_timesteps,
                            timestep_indices,
                            spectrum_phase="capture",
                        )
                        if prediction is None or self._interrupt:
                            spectrum_reset_reason = "cancelled"
                            return None
                        advance_latents(prediction, video_timestep, audio_timestep)
                        progress.update()
                spectrum_capture_seconds = time.perf_counter() - capture_started
                controller.seal_capture()
                video_rows.copy_(initial_video_rows)
                audio_rows.copy_(initial_audio_rows)
                reset_denoising_schedulers()
                report_phase("Spectrum H3 offline smoothing replay")
                replay_started = time.perf_counter()
                for index, (video_timestep, audio_timestep) in enumerate(
                    zip(timesteps, audio_timesteps)
                ):
                    if self._interrupt:
                        spectrum_reset_reason = "cancelled"
                        return None
                    unique_timesteps, timestep_indices = row_plan[index]
                    prediction = run_transformer_step(
                        index,
                        unique_timesteps,
                        timestep_indices,
                        spectrum_phase="replay",
                    )
                    if prediction is None or self._interrupt:
                        spectrum_reset_reason = "cancelled"
                        return None
                    advance_latents(prediction, video_timestep, audio_timestep)
                    if callback is not None:
                        callback(index, None)
                spectrum_replay_seconds = time.perf_counter() - replay_started
            except SpectrumStateError:
                spectrum_reset_reason = "native_fallback"
                video_rows.copy_(initial_video_rows)
                audio_rows.copy_(initial_audio_rows)
                reset_denoising_schedulers()
                report_phase("Spectrum H3 fallback to native denoising")
                if not run_native_denoising(progress_label="MiniMax H3 native fallback"):
                    spectrum_reset_reason = "cancelled"
                    return None
            finally:
                stats = controller.stats()
                stats["reset_reason"] = spectrum_reset_reason
                if spectrum_capture_seconds is not None:
                    stats["anchor_capture_seconds"] = spectrum_capture_seconds
                if spectrum_replay_seconds is not None:
                    stats["offline_replay_seconds"] = spectrum_replay_seconds
                self._last_spectrum_stats = stats
                controller.reset(spectrum_reset_reason)
                del controller
                del initial_video_rows
                del initial_audio_rows

        if self._interrupt:
            return None
        report_phase("Decoding H3 video")
        video, normalized_video_latents = _decode_h3_video_rows(
            vae=self.vae,
            device=self.device,
            packed_rows=video_rows[layout.num_condition_video_rows :],
            latent_frames=num_latent_frames,
            latent_height=latent_height,
            latent_width=latent_width,
            pixel_frames=target_frame_num,
            pixel_height=height,
            pixel_width=width,
            channels=24,
            patch_size=self.patch_size,
        )

        report_phase("Decoding H3 audio")
        audio_latents = unpack_audio_tokens(
            audio_rows[layout.num_condition_audio_rows :],
            num_audio_latents,
        )
        normalized_audio_latents = audio_latents
        audio_mean = torch.tensor(AUDIO_LATENTS_MEAN, device=self.device).view(1, -1, 1)
        audio_std = torch.tensor(AUDIO_LATENTS_STD, device=self.device).view(1, -1, 1)
        audio_latents = audio_latents * audio_std + audio_mean
        audio = self.audio_vae.decode(audio_latents, return_dict=False)[0]
        audio = audio.float().permute(1, 0, 2)[0].transpose(0, 1).cpu().numpy()
        if self.reference_mode and handoff_chain_id and not native_boundary_enabled:
            # Keep only the minimum legal Ref2VA tail in CPU RAM. The next
            # segment still carries a decoded tail file as a reload-safe
            # fallback; when this exact model instance survives, replacing the
            # final prepared reference with these generated latents avoids a
            # lossy decode/re-encode round trip and carries audio context too.
            handoff_video_latents = video_latent_num_frames(56)
            handoff_audio_latents = audio_latent_num_frames(56)
            self._ref2va_handoff_cache = {
                "chain_id": handoff_chain_id,
                "video": normalized_video_latents[
                    :, :, -handoff_video_latents:
                ].detach().float().cpu(),
                "audio": normalized_audio_latents[
                    :, :, -handoff_audio_latents:
                ].detach().float().cpu(),
            }
        output_video = video[0]
        if native_continuation:
            output_video = torch.cat(
                [boundary_history.to(output_video), output_video], dim=1,
            )
            history_samples = round(
                H3_NATIVE_HISTORY_FRAMES / MINIMAX_H3_FPS * 32000
            )
            prefix_audio = boundary_waveform[:, :history_samples].transpose(0, 1)
            audio = np.concatenate([prefix_audio.cpu().numpy(), audio], axis=0)
        return {
            "x": output_video,
            "audio": audio,
            "audio_sampling_rate": 32000,
        }
