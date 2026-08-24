"""Native and accelerated MiniMax-Music3 components used by Maestro.

The neural-network definitions are adapted from the Apache-2.0 Diffusers
integration contributed by the MiniMax and Hugging Face teams. Model weights
remain governed by MiniMax's Music3 Community License. Maestro's default
pipeline uses the ConvRot checkpoints and semantic decoder adapted from
WanGP; the original Diffusers-style pipeline remains available as a fallback.
"""

from importlib import import_module

_LAZY_EXPORTS = {
    "MiniMaxMusic3ConditionEncoder": (".condition_encoder", "MiniMaxMusic3ConditionEncoder"),
    "LegacyMiniMaxMusic3Pipeline": (".pipeline", "MiniMaxMusic3Pipeline"),
    "MiniMaxMusic3Pipeline": (".optimized_pipeline", "MiniMaxMusic3Pipeline"),
    "MiniMaxMusic3RVQDepthDecoder": (".rvq_depth_decoder", "MiniMaxMusic3RVQDepthDecoder"),
    "MiniMaxMusic3Transformer1DModel": (".transformer", "MiniMaxMusic3Transformer1DModel"),
    "MiniMaxMusic3Vocoder": (".vocoder", "MiniMaxMusic3Vocoder"),
}


def __getattr__(name):
    """Load neural-network components only when a caller actually requests one."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    "MiniMaxMusic3ConditionEncoder",
    "LegacyMiniMaxMusic3Pipeline",
    "MiniMaxMusic3Pipeline",
    "MiniMaxMusic3RVQDepthDecoder",
    "MiniMaxMusic3Transformer1DModel",
    "MiniMaxMusic3Vocoder",
]
