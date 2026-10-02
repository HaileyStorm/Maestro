# H3 cumulative append: tensor primitive

## Current milestone and intent

Continue fallback item `UH-20260927-MAESTRO-EXTEND-FLAGS` in the existing
physical checkout. Preserve completed Extend, decoded-boundary and Editor
evidence. The next selected H3 lane is cumulative latent append, using the
reviewed direct synchronized tail-guide family. Masked continuation remains a
separate experiment.

The geometry module already plans legal context/window lengths, absolute audio
clock spans and final publication trim. `h3_cumulative_latents.py` now implements
the tensor operation independently: copy the retained normalized AV context,
discard the sampled window's hidden overlap and append only its new suffix.
Video is `[1,24,T,H,W]`; stereo audio is `[2,32,T]`. Prefix tensors and caller
inputs remain unchanged. Shape/device/dtype mismatches and stale steps fail
before concatenation. An explicit output byte budget bounds both copied context
and appended state before allocation.
It is not a total process peak-memory limit.

The algorithm follows the reviewed contract from the pinned
[continuation source](https://github.com/ttulttul/ComfyUI-Minimax-H3-Continuation/blob/e1768d5fdfc6f9519d2090dcf78458c2d9625f80/continuation_nodes.py),
independently implemented with Maestro's native tensor layouts and existing
absolute clock planner. No node pack or upstream framework is installed.

Full generated latent state survives final publication trim. A trimmed chain
cannot append another window; exact final video/audio trimming belongs to the
publication layer after decoding. Dataclass records borrow tensors; frozen
metadata does not make their contents immutable. Context and append outputs
have independent storage.

## Evidence and remaining work

Acceptance for this slice is CPU tensor correctness only. No model weights,
sampler, CUDA, media decode or live generation are involved. This helper is not
connected to a public generation mode and does not establish runtime cumulative
conditioning, decoded-prefix identity, perceptual quality or human acceptance.

Ten focused CPU tensor tests passed, including twenty successive appends,
prefix/input storage independence, final trim, malformed/stale state, and byte
limits checked before copy/concatenation. Scoped Ruff lint, syntax compilation
and diff checks passed. The independent review found no clock/suffix correctness
blocker. Its context-copy budget observation was addressed with the same
pre-allocation byte limit and a focused regression. Borrowed tensor contents
remain caller-owned; this primitive validates structure, not provenance or
subject matter. A sampler integration must establish chain identity and its
required common device/dtype before accepting any handoff.

The independent source review identified the critical next boundary:
`_advance_paired_h3_latents` advances all target rows. Retained context must be
packed as conditioning or explicitly pinned during every denoising step; simply
prepending it to target noise would drift. Decode and publication must remove
hidden context exactly once. The current 18-frame decoded adapter and video-only
Ref2VA handoff are separate paths.

Next implementation: accept retained normalized AV state at the model boundary,
compose a synchronized frame-zero guide, return complete next AV state, and
forward it through the handler/WGP/long-form caller. Bound cancellation and
ownership, and define durable recovery before exposing the mode. An in-memory
cache cannot prove restart recovery. Subsequent live execution needs a fresh
exact GPU grant and coherent validation throughout; reuse existing accepted
Extend/boundary runs instead of repeating them.

## Recovery and ownership

On the Codex app restart, `main` at `b023f4a` and the saved Editor draft were
recovered. Rediscovered direct and stable-share health/readiness returned 200.
Chrome blocked the stable URL locally, so those HTTP probes are not current
Chrome stable-surface acceptance. The service was already running and required
no restart.

The supported claim receipt verifies the current host and physical-workspace
bindings. Fresh exact claim: `maestro-h3-cumulative-core-20261002`, covering only
this note, the tensor helper and its focused test file. Foreign `AGENTS.md`,
storage-janitor work and private
artifacts remain preserved. Historical SQLite Beads remains on its mutation
hold despite the activation audit's Dolt metadata. No Beads lifecycle command,
launcher edit, full-suite rerun, GPU request or provider delegation was used.
