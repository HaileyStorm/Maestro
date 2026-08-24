"""Text-to-speech model handlers.

Handler modules are intentionally loaded on demand. This keeps lightweight
prompt and catalog helpers usable without importing Torch or initializing a
model runtime.
"""

from importlib import import_module

_HANDLERS = {
    "ace_step_handler",
    "chatterbox_handler",
    "heartmula_handler",
    "index_tts2_handler",
    "kugelaudio_handler",
    "minimax_music3_handler",
    "qwen3_handler",
    "voxcpm_handler",
    "yue_handler",
}

__all__ = sorted(_HANDLERS)


def __getattr__(name):
    if name not in _HANDLERS:
        raise AttributeError(name)
    module = import_module(f".{name}", __name__)
    globals()[name] = module
    return module
