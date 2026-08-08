"""Shared utility package without eager accelerator imports.

Most callers import lightweight submodules such as ``prompt_parser`` and
``files_locator``.  Importing Torch-backed schedulers here made those callers
require the full generation environment just to parse a Studio prompt.  Keep
the historical package-level scheduler API, but resolve it only on demand.
"""

_DPM_EXPORTS = {
    "FlowDPMSolverMultistepScheduler",
    "get_sampling_sigmas",
    "retrieve_timesteps",
}


def __getattr__(name):
    if name in _DPM_EXPORTS:
        from . import fm_solvers

        return getattr(fm_solvers, name)
    if name == "FlowUniPCMultistepScheduler":
        from .fm_solvers_unipc import FlowUniPCMultistepScheduler

        return FlowUniPCMultistepScheduler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'HuggingfaceTokenizer', 'get_sampling_sigmas', 'retrieve_timesteps',
    'FlowDPMSolverMultistepScheduler', 'FlowUniPCMultistepScheduler'
]
