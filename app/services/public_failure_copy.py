"""Reviewed public failure copy; never admit arbitrary exception text by prefix.

Stage fallbacks have one owner here. The exact-message set is a publication
compatibility boundary for existing producer strings, not a runtime source scan.
Producer copy changes require a matching review of this allowlist. Dynamic
filenames, paths and authored text are deliberately excluded.
"""
from __future__ import annotations

import re
from typing import Final, Mapping
from types import MappingProxyType

FAILURE_STAGE_DETAILS: Final[Mapping[str, str]] = MappingProxyType({
    "model_load": "The generation model could not be loaded with the available host memory.",
    "denoise": "Generation failed during denoising.",
    "vae_decode": "Generation failed while decoding the rendered segment.",
    "segment_checkpoint": "The rendered segment could not be sealed for recovery.",
    "concat": "Rendered segments could not be joined into the final output.",
    "audio_mux": "The rendered output could not be combined with audio.",
    "postprocess": "The rendered output failed during post-processing.",
    "flashvsr": "The rendered output failed during FlashVSR processing.",
    "delivery": "The requested delivery output could not be produced.",
    "publication": "The completed output could not be published safely.",
    "generation": "Generation failed."
})

PUBLIC_CONTRACT_MESSAGES: Final[frozenset[str]] = frozenset({
    'Dasiwa artifact verification failed',
    'Dasiwa cannot be stacked with another LoRA or accelerator',
    'Dasiwa cannot be stacked with another accelerator',
    'Dasiwa cannot use a step cache',
    'Dasiwa metadata does not match the pinned Ref2VA release',
    'Dasiwa metadata is unavailable',
    'Dasiwa requires LoRA strength 1.0',
    'Dasiwa requires MiniMax H3 Ref2VA',
    'Dasiwa requires MiniMax H3 Ref2VA for every planned shot',
    'Dasiwa requires exactly four sampling steps',
    'Dasiwa tensor count does not match the pinned release',
    'Generation LoRA selection is invalid.',
    'Generation custom settings are invalid.',
    'Generation parameters are invalid.',
    'Generation planning mode is invalid.',
    'H3 Turbo LoRA revision is not pinned',
    'H3 Turbo Ref2VA is structurally compatible but unavailable by default until its 4/8 reference-adherence, motion, coherence, and collapse visual gates pass',
    'H3 Turbo Sol-Attn dense step count is invalid',
    'H3 Turbo cache combination is unsupported: MiniMaxH3Transformer has no Tea/Mag cache execution hook',
    'H3 Turbo checkpoint and FL2VA/Ref2VA conditioning mode do not match',
    'H3 Turbo is incompatible with PinkCherry; choose a native profile such as Quality or select a different H3 checkpoint',
    'H3 Turbo managed assets are not installed',
    'H3 Turbo managed manifest has the wrong profile',
    'H3 Turbo managed manifest is invalid',
    'H3 Turbo managed release ID is invalid',
    'H3 Turbo node companion commit is not pinned',
    'H3 Turbo requires a MiniMax H3 FL2VA or Ref2VA transformer',
    'H3 Turbo requires one structurally validated H3 transformer checkpoint',
    'H3 Turbo schedule identity does not match the current runtime',
    'H3 Turbo schedule identity must be an object',
    'H3 Turbo steps must be an integer from 4 through 8',
    'H3 Turbo supports exactly 4 through 8 model evaluations',
    'H3 Turbo user-LoRA stacking is unsupported on W4A8/PinkCherry: their packed/INT8 generic adapter path is not dtype-safe',
    'MiniMax H3 LoRA multipliers exceed the selected asset count.',
    'MiniMax H3 LoRA multipliers must be text.',
    'MiniMax H3 LoRA selection is incompatible with its architecture.',
    'MiniMax H3 LoRA selection is incompatible with the planned models.',
    'MiniMax H3 LoRA selections must be a list of asset names.',
    'MiniMax H3 LoRA selections must not contain duplicates.',
    'MiniMax H3 LoRA selections require non-empty asset names.',
    'MiniMax H3 LoRA: Dasiwa cannot be stacked with another LoRA or accelerator',
    'MiniMax H3 LoRA: Turbo SLA cannot be stacked with another LoRA or accelerator',
    'MiniMax H3 Ref2VA accepts at most 9 combined semantic references and per-clip keyframes',
    'MiniMax H3 architecture LoRA selections must be lists.',
    'MiniMax H3 dialogue blocks cannot be truncated by compaction',
    'MiniMax H3 dialogue tags must be balanced before planning',
    'MiniMax H3 dialogue tags must use canonical <d>[language] text</d> syntax before planning',
    'MiniMax H3 executable records cannot contain multiple OPENING BLOCKING fields',
    'MiniMax H3 inference steps must be an integer from 2 to 50.',
    'MiniMax H3 inference steps must be between 2 and 50 scheduler grid points.',
    'MiniMax H3 shot ranges must end inside the selected duration. Fractional seconds use a decimal on the seconds field (00:09.500 or 9.5s). Two-colon clocks are hours:minutes:seconds, so 00:09:30 is 9 minutes 30 seconds.',
    'MiniMax H3 timestamps must land inside the selected duration. Fractional seconds use a decimal on the seconds field (00:09.500 or 9.5s). Two-colon clocks are hours:minutes:seconds, so 00:09:30 is 9 minutes 30 seconds.',
    'Native MiniMax H3 boundary conditioning is unavailable; use the supported automatic segmented path',
    'Pinned FL2VA cannot use semantic references; enable adaptive conditioning or remove the semantic references',
    'Pinned Ref2VA cannot use first/last-frame anchors; enable adaptive conditioning or remove the edge anchors',
    'Spectrum Experimental cannot be combined with Turbo',
    'Spectrum Experimental cannot be combined with another step cache',
    'Spectrum Experimental currently requires 20 authored evaluations',
    'Spectrum Experimental currently requires a 20-step native schedule',
    'Spectrum Experimental currently supports only MiniMax H3 Base FL2VA',
    'Spectrum Experimental does not support H3 W4A8',
    'Spectrum Experimental does not support LoRA multipliers',
    'Spectrum Experimental does not support PinkCherry or ConvRot checkpoints',
    'Spectrum Experimental does not support Ref2VA',
    'Spectrum Experimental does not support native boundary conditioning',
    'Spectrum Experimental does not support user or managed LoRAs',
    'Spectrum Experimental requires a valid segment plan',
    'Spectrum Experimental requires exactly 20 native evaluations',
    'Spectrum Experimental requires its pinned degree-one warmup profile',
    'Spectrum Experimental supports only Dense SDPA or Sol-Attn',
})

_CLIP_LIMIT = re.compile(
    r"MiniMax H3 clips are limited to ([1-9][0-9]{0,3}) frames each\. "
    r"Split oversized Director scenes into consecutive clips; a "
    r"single long Studio prompt is segmented automatically\."
)


def reviewed_contract_message(text: object) -> str | None:
    """Accept complete reviewed copy or the bounded native-frame-limit template."""
    if type(text) is not str or not text or len(text) > 240:
        return None
    if text in PUBLIC_CONTRACT_MESSAGES:
        return text
    if _CLIP_LIMIT.fullmatch(text):
        return text
    return None
