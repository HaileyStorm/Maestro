# H3 cumulative append: tensor primitive and private sampler

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

## Private sampler milestone

The native FL2VA model now accepts `_h3_cumulative_capture=True` only with
`MAESTRO_H3_CUMULATIVE_EXPERIMENTAL=1`. A successful first capture returns
`_h3_cumulative_handoff` containing normalized CPU float32 AV state and an opaque
model-instance token. The next private call takes that handoff as
`_h3_cumulative_previous`, the exact planner step as `_h3_cumulative_step`, and
the step's exact target frame count. Separate frame-zero video/audio guide rows
carry the retained tail; video uses the existing 0.999 keyframe augmentation,
audio uses clean guide rows. Ordinary target rows still denoise. The sampled
overlap is discarded before appending the suffix to retained state.

Both VAEs decode the complete assembled generated state. Final publication
trim happens after decoding. Audio uses the absolute frame clock for target
latent ticks and final sample count. A rounded latent tick may provide slightly
less audio than that exact frame endpoint; explicit zero padding completes only
that fractional tick, and `audio_padding_samples` records it in the private
handoff. Ordinary generation output retains its existing duration behavior.
A planned continuation window can be shorter than the ordinary first clip's
five-second minimum; ordinary requests retain their existing limits.

The token is an in-process identity guard, not authentication, checkpoint
provenance, tensor immutability or a durable recovery receipt. Caller-owned
handoffs can be modified or branched. A fresh capture, an intervening ordinary
generation request, or model release invalidates the preceding token. No AV
tensor cache is retained by the model. Cancellation, sampling failure or decode
failure publishes no successor. The 512 MiB retained-state allocation limit is
checked before sampling. Independent review identified the larger full-resolution
decode allocation; a separate private 2 GiB decoded AV output-geometry limit now
also rejects before sampling, using complete generated frames before publication
trim. Neither limit bounds process peak memory or grants VRAM authority.
Longer chains need bounded decoding or streaming publication before public use.
Re-decoding retained latents does not prove decoded-pixel prefix
identity across windows.

This initial private path rejects Ref2VA, semantic references, source audio,
new continuation keyframes, decoded native-boundary inputs, bridge/timeline
guides, LoRAs and alternate sampling profiles. It has no queue, handler, WGP or
public UI routing. Opaque tokens and tensors must not be serialized into public
settings, sidecars or queue responses.

Eleven new CPU tests call the real `generate` method with fake transformer,
conditioner and VAEs plus real packing and schedulers. They verify the
94-versus-93 absolute audio-clock discriminator, fixed guide rows while targets
advance, complete-state AV decode, preserved latent prefix, publication trim,
explicit fractional-tick audio padding, ordinary output parity, exact step and
canvas checks, token invalidation, latent/full-decode allocation rejection before sampling,
cancellation/failure and unchanged creative prompt routing. Thirty-five
adjacent existing CPU tests also passed (source-audio stepping, native packing
and decode, runtime math and timeline still guide). The latest focused eleven
passed after the final token/allocation changes; adjacent evidence is reused.
The new test file passes Ruff lint/format; model lint has the same ten
pre-existing diagnostics as its source baseline. Syntax and diff checks pass.
No full suite, model weights, CUDA or live generated media were used.
The bounded independent sampler review found no remaining serial-path blocker
after its decoded-output allocation finding was fixed. The final focused run
includes that guard and its regression. Local service health and readiness
remained 200 after rediscovery through Pinokio; no restart was required.

Next: forward owned retained state through the handler/WGP/long-form caller,
define atomic durable state publication and checkpoint/canvas/chain identity,
and test cancellation/restart recovery before public exposure. Subsequent live
execution needs a fresh exact GPU grant and coherent validation throughout;
reuse existing accepted Extend/boundary runs instead of repeating them.

## Recovery and ownership

On the Codex app restart, `main` at `b023f4a` and the saved Editor draft were
recovered. Rediscovered direct and stable-share health/readiness returned 200.
Chrome blocked the stable URL locally, so those HTTP probes are not current
Chrome stable-surface acceptance. The service was already running and required
no restart.

The supported claim receipt verifies the current host and physical-workspace
bindings. The completed primitive claim `maestro-h3-cumulative-core-20261002`
was released through the supported helper. The next exact claim,
`maestro-h3-cumulative-sampler-20261002`, covers only this note, the native model
and its new focused test file. Foreign `AGENTS.md`,
storage-janitor work and private
artifacts remain preserved. Historical SQLite Beads remains on its mutation
hold despite the activation audit's Dolt metadata. No Beads lifecycle command,
launcher edit, full-suite rerun, GPU request or provider delegation was used.
