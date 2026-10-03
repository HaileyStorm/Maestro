# H3 cumulative append: private worker and durable recovery

## Current milestone and intent

Latest checkpoint: private worker dispatch, durable AV/media sealing, adoption
of verified pre-promotion staged media, recovery from sealed sidecars and final
publication from the last complete output,
described at the end of this file. Public activation and live generated-media
acceptance remain open. Earlier sections retain their milestone-specific
evidence and limitations.

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
using the private serialization contract below, and verify queue crash recovery
before public exposure. Subsequent live
execution needs a fresh exact GPU grant and coherent validation throughout;
reuse existing accepted Extend/boundary runs instead of repeating them.

## Private durable checkpoint milestone

`services/h3_cumulative_recovery.py` now serializes normalized CPU float32 video
and audio in a single bounded safetensors file, with no pickle or model token.
A JSON-only receipt binds the declared owner, project, chain, job, native runtime
bundle digest, canvas, completed predecessor dependency, generated/published
frame counts, payload size and SHA-256. The caller must verify owner/project
access and the actual loaded bundle; declared identities and a content digest
do not establish authorization or model provenance by themselves. The bundle
identity must cover transformer, conditioner, both VAEs, processor, configuration
and normalization contract. At this checkpoint, no loaded-bundle resolver was
wired; the private runtime-binding milestone below supplies it.

Publication uses a private project recovery staging directory, mode 0600 files,
file/directory synchronization and create-only hard-link publication. A receipt
is returned only after complete publication. Existing different content is never
overwritten. Loading requires a confined relative name, unchanged single-link
regular file, exact size/hash and metadata, bounded header, and reconciled AV
geometry before tensor allocation. Non-finite numeric values are rejected;
creative text and subject matter are not inspected. Unsupported platforms fail
closed before storage creation; Windows runtime acceptance remains open.

Independent review found that the original filename would be removed by generic
queue cleanup even for a live job. Permanent files now use the existing
`unit-{job_id}-` prefix. A focused integration regression calls the unmodified
cleaner: a live job retains its checkpoint, and terminal cleanup removes it.
Review also found name-only temporary cleanup could delete a replaced entry.
The writer now holds its created file open through publication and verifies
its device/inode before linking and unlinking; failure cleanup preserves a
replacement. Controlled cancellation and pre-publication replacement regressions
exercise that boundary. Publication still assumes the owning caller serializes
writers in its private directory; these checks do not make competing directory
mutations atomic. The correction review confirms both deterministic findings
are addressed, with a narrow stat/unlink race remaining against a hostile
same-UID actor; this module does not claim that isolation boundary.

Hard process crashes may leave an unreferenced complete checkpoint, an extra
hard link, or a private partial `.h3-av-*.tmp`. The loader never accepts a partial
file as a receipt. Generic cleanup skips temporary names and multiple-link
files; exact orphan retention/cleanup needs an integration plan. Each step stores
a complete AV snapshot. Serialization and loading hold whole payloads and
tensors, so the 512 MiB tensor limit does not bound process peak memory or total
chain disk usage. Live-job census, checkpoint reference retention and terminal
cleanup must be verified with the queue before activation.

`MiniMaxH3Model.restore_h3_cumulative_handoff` restores a verified record to a
fresh loaded native FL2VA instance under the experimental gate, checks declared
identity and loaded component presence, then mints a fresh instance token. It
does not hash loaded weights. A CPU fake-model restart test captures AV, seals
it, releases the original instance, reloads it, restores a fresh instance and
appends while preserving the retained latent prefix. This is disk/fake-model
restart evidence, not an actual queue, process-crash, model-weight or GPU run.

Seventeen recovery tests and eleven sampler tests passed together after the
review fixes. The recovery seventeen passed again after the test fixture and
lint corrections; sampler evidence was reused. New service/tests pass Ruff
lint/format; syntax and diff checks pass. The model retains its ten prior Ruff
diagnostics with no new lint finding. Direct service health and readiness were rediscovered through Pinokio and
both returned 200; no restart was needed. No full suite, weights, CUDA or
generated media were used. Public UI, WGP and queue routing remain unchanged.

Next integration anchors from the bounded read-only queue map:

- WGP transport is implemented below. Supply its private dispatch from the
  authorized long-form caller; keep tensors and opaque tokens out of queue JSON.
- Seal AV with segment completion in the long-form callback; preserve the
  descriptor through every recovery-unit checkpoint/enrichment path.
- Include the AV payload hash in predecessor dependency evidence; verify/load
  it before treating a recovered unit as skippable or dispatching a successor.
- Supply a verified actual loaded-bundle digest and authorized project/owner/job
  identity before calling restore. Keep bad/missing AV state explicit.
- Exercise queue crash/cancel/cleanup with fakes, then run a separate bounded
  live continuation only under fresh GPU authority.

## Private WGP transport milestone

`H3CumulativeDispatch` is a single-use, server-owned runtime object passed as
`_h3_cumulative_dispatch` directly to `wgp.generate_video`. It carries the exact
sampling frame count and optional prior handoff/planner step. Construction does
not authorize a model workload. The ordinary WGP path does not import this
transport when the argument is absent. JSON objects are rejected, the private
experimental gate remains required, and Python serialization is rejected.
Do not put this object into task params or journal state.

The outer WGP wrapper validates a single native FL2VA video output. The actual
model call forwards private capture/previous/step kwargs; the returned retained
AV state is captured before WGP unwraps dictionary samples to `x`. Output
geometry must match the complete generated/published chain and exact 32 kHz
stereo sample clock. Media settings record complete published frames and seconds,
not the shorter sampling window. The private object is removed before filename
formatting, embedded metadata and saved settings.

The dispatcher exposes a successor handoff only after WGP returns success.
Cancellation, failed encoding/publication, missing/malformed sampler state and
exceptions discard its candidate; previous input references are released at the
terminal boundary. A private cumulative OOM discards the candidate, clears the
failed traceback and performs best-effort resource cleanup, preserving the
original exception. It cannot use ordinary automatic OOM relief to silently
change canvas/steps or retry another window. The caller must restore a sealed
checkpoint and make an explicit retry decision if a model token was invalidated
by completed sampling followed by failed publication.

The path rejects repeat/batch expansion, ordinary source-prefix restoration,
audio offsets, frame-rate changes, postprocessing and incompatible reference
inputs. Multi-clip metadata must defer concatenation and carry no source prefix
or trim: a cumulative output already includes retained frames, so ordinary
concatenation would duplicate them. An unexpected second repeat/window fails
before another sampling invocation. These private restrictions are not a public
capability change. Creative text follows the same path; only prompt invocation
cardinality is checked.

Ten CPU tests execute the AST-extracted real WGP outer wrapper with the real
native sampler and fake components. They cover capture, a 56-frame append window
producing a complete 175-frame output, exact retained AV prefix, final publication
trim, ordinary output parity, failed output/encoder exception, OOM without
implicit retry, single-use/private-object gate, serialization rejection,
concatenation/prefix/repeat/postprocessing rejection and unchanged sensitive
creative prompt routing. The transport-node test also executes the actual model kwargs,
capture, metadata timing and private-field stripping nodes from WGP source.
Independent review found pre-sampling validation/profile/observer failures
could retain prior tensors outside the cleanup boundary. Valid dispatch begin
now discards on failure, profile setup is protected, and observer reset is inside
the protected loop. A regression verifies all three fail before sampling and
release prior state. The correction review confirms that lifecycle finding is
closed. Public/queue injection remained pending. Actual loaded-bundle
verification is supplied by the subsequent private milestone below.
This is CPU sampler/transport evidence, not an end-to-end WGP encode or device
run. The initial nine passed with 26 adjacent OOM-relief/planning-failure checks (35 total),
then ten passed after final lifetime/metadata and review corrections. The
request-guard test passed once more after explicit tail-trim/retake and integer
repeat/batch guards were added; adjacent evidence is reused. New service/tests pass Ruff lint/format and syntax/diff checks. WGP has
its same 445 pre-existing Ruff diagnostics with no new finding.

The bounded map identified actual loaded-bundle inputs for the next resolver:
transformer/conditioner aliases resolved by WGP, constructor-selected transformer
and both VAE paths, conditioner config and seven processor files. Configurable
checkpoint roots may be linked, so bind exact resolved files rather than just
relative names or pinned upstream repositories. Include code defaults, selected
dtype/QKV layout, scheduler shifts and normalization constants. The standard
asset manifest does not provide hashes for the complete standard bundle. No
weight files were read or hashed during this map.

Remaining: the authorized long-form caller must inject this object without
serializing it, choose cumulative output replacement rather than ordinary
component concatenation, seal AV receipts with completed media dependencies,
and restore against the verified actually loaded bundle. No public UI or queue
activation, model weights, CUDA, generated media, restart or full-suite run was
used for this milestone.

## Private loaded-runtime binding milestone

`services/h3_runtime_binding.py` now captures content identity for the exact
files consumed by a private native FL2VA load. Ordinary loading and Ref2VA do
not hash assets. Only `MAESTRO_H3_CUMULATIVE_EXPERIMENTAL=1` enables capture.
Transformer, every conditioner shard, both VAEs, text config and every direct
processor file are hashed once in bounded streaming reads before loading. The
constructor passes the same canonical checkpoint/config/processor paths to its
loaders. Processor loading has local-only fallback, requires the seven expected
files and includes optional direct files in the digest. A file census catches
additions/removals. Nested processor asset directories are currently rejected;
only the unused Hugging Face `.cache` directory is excluded. Linked checkpoint
roots remain supported and their original resolutions must remain unchanged.

File size/device/inode/mtime/ctime evidence is checked after hashing, after
loading and at identity lookup. Empty/nonregular/oversized files, changed
assets, missing versions and nonfinite/non-JSON contracts fail closed. Inventory
is capped at 80 files and each file at 1 TiB; this is a streamed-read bound, not
a model resource-admission or process-memory guarantee. Hashing a real large
bundle adds one complete disk read on a private load. Cancellation is checked
for each read block, using WGP's load reporter when present.

The digest includes actual loaded H3/shared/MMGP Python implementations and
serializable defaults/constants, installed required and optional runtime
versions, selected native model/type/dtype, effective QKV/checkpoint geometry,
conditioner/VAE configuration, normalization, paired scheduler shifts and
post-offload tensor names/shapes/dtypes/module classes. Paths, process addresses
and scheduler progress are excluded from the digest. Same-byte asset relocation
does not change it. Python build/code changes may conservatively invalidate it.
This is load provenance and an effective configuration check, **not a hash of
live parameter values or an attestation against hostile in-process/filesystem
mutation**. Existing serialized trusted-owner execution and file immutability
assumptions remain necessary.

The independent review identified that MMGP setup happens after the model
constructor. Capture therefore stays pending: `verified_h3_runtime_sha256()`
fails until WGP finalizes immediately after successful offload setup and its
cancellation check. A second cancellation check follows the metadata walk,
before runtime-ready. Finalization rejects compilation, fresh transformer
quantization, loaded LoRA adapters and Turbo overlays for this experiment.
Failures reach existing partial-load cleanup. Finalization consumes its pending
snapshot once; it cannot renew provenance after an intervening ordinary request.
The binding uses weak component references and owns no AV state.

Restore now requires the saved runtime SHA to match
`verified_h3_runtime_sha256()` before validating state and minting a new token.
Release and ordinary generation clear the binding, pending snapshot and token.
Missing proof, replaced components, changed files, scheduler/configuration
changes and mismatched identities are rejected. No queue or public caller is
activated by this addition.

Fifty-four focused checks passed together: 16 runtime-binding, 17 disk recovery,
11 sampler and 10 WGP transport checks. A subsequent cancellation regression
and the affected constructor check passed, then the WGP setup boundary passed
again after the review correction. The final runtime-binding file has 17 tests;
the adjacent evidence above is reused. These tests use temporary files and CPU
model/offloader fakes. The WGP setup regression executes its actual protected
boundary with fake success/setup failure/compile/quantization/LoRA/cancellation
paths. The separate disk restart test explicitly mocks the loaded proof;
runtime-binding tests independently exercise the actual temp-file digest and
fresh-instance restore.

The final runs hid CUDA devices. An initial run had CUDA visible and observed
the library context initialized; it did not load model weights or run device
sampling. No real weight file was read/hashed, no real MMGP/device run or media
was produced, and no GPU lease or live acceptance is claimed. New service/tests
pass Ruff lint/format, syntax and diff checks. The changed existing files retain
their baseline Ruff findings (model 10, conditioner 2, WGP 445), with no new
diagnostic. No full suite, provider request or service restart was performed.

Next safe unit: authorize the long-form caller to obtain this actual digest,
inject transient dispatch outside task JSON, replace the complete cumulative
media chain, seal AV receipts with completed media dependencies, verify queue
resume/skip/reference retention and terminal/crash cleanup. Only then perform
bounded device and browser/media acceptance under fresh authority. Existing
accepted Extend/boundary evidence remains retained; the broader sprint is open.

## Recovery and ownership

On the Codex app restart, `main` at `b023f4a` and the saved Editor draft were
recovered. Rediscovered direct and stable-share health/readiness returned 200.
Chrome blocked the stable URL locally, so those HTTP probes are not current
Chrome stable-surface acceptance. The service was already running and required
no restart.

The supported claim receipt verifies the current host and physical-workspace
bindings. The completed primitive claim `maestro-h3-cumulative-core-20261002`
was released through the supported helper. The completed sampler claim
`maestro-h3-cumulative-sampler-20261002` was also released after commit `d9261e1`
and verified origin parity. The recovery claim
`maestro-h3-cumulative-recovery-20261002` covers only this note, the native model,
the new serialization service and its focused test file. The controller owns
implementation; the existing native read-only agent supplied the queue map and
independent review. The exact recovery claim was released after commit `0ece65e` and verified
origin parity. The dispatch claim `maestro-h3-cumulative-dispatch-20261002`
was released after commit `6b9e497` and verified origin parity. On the subsequent
Codex restart, that revision, untracked binding service and exact narrow claim
were recovered. Rediscovered direct health/readiness both returned 200; the
service was already running. The binding claim
`maestro-h3-runtime-binding-20261002` was released/reacquired through the
supported helper to add offline conditioner loading and the WGP finalization
hook. It covered only those three existing source files, the new binding service,
new tests, recovery test correction and this note. That exact binding claim was
released after commit `64be4a0` and verified origin parity. Foreign `AGENTS.md`,
storage-janitor work and private
artifacts remain preserved. Historical SQLite Beads remains on its mutation
hold despite the activation audit's Dolt metadata. No Beads lifecycle command,
launcher edit, full-suite rerun, GPU request or provider delegation was used.

## Private queue receipt and loaded-model restore milestone

The actual queue safe-unit matcher now recognizes an explicit
`settings.cumulative_append` contract containing the chain ID and native canvas.
It checks owner/job identity, re-reads the physical project-instance marker,
recomputes the media unit ID and verifies generated/published/trim geometry.
The matcher, checkpoint and enrichment require an independently supplied
`H3QueueAuthority` from the trusted sealed plan. Receipt/sidecar/journal values
cannot authorize their own chain or canvas. Until the new caller supplies that
authority, cumulative units cannot be committed or treated as skippable.
The AV receipt must match the exact unit dependency and the receipt sealed into
each verified media sidecar. Missing, corrupt, non-finite, replaced or
mismatched AV bytes make the unit unskippable, even when media is intact or the
generic consumed-continuation set would otherwise permit a retired file.

Queue receipt encoding renames only the proven bundle digest key to
`bundle_sha256`. Exact schema validation maps it back to the checkpoint's
`runtime_sha256` field before loading. The generic journal runtime/tensor/token
denylist is unchanged. A real temporary journal restart preserves this receipt;
no host path, tensor or opaque instance token enters the journal. Verification
reads the bounded whole payload and scans float32 data in 1 MiB NumPy views
without constructing full tensor arrays. This reduces allocation during skip
verification; it does not remove the bounded whole-file byte allocation or
establish a peak-memory limit.

Re-sealing the same media unit preserves its cumulative receipt when omitted
from the call and rejects a different receipt. Enrichment verifies AV and the
already sealed media first, rejects a conflicting sidecar handoff, updates the
sidecar and then commits the rebuilt media-plus-AV descriptor. An interrupted
sidecar/journal update fails closed under the existing orphan recovery flow;
this is not a new atomic transaction spanning those files.

`H3CumulativeQueueDispatch` binds server-supplied owner/project/job/chain/canvas
authority to the actual loaded native FL2VA model. WGP forwards that handler at
its sampler-kwargs boundary, after load finalization and canvas normalization.
The dispatch compares the loaded bundle with the prior receipt, verifies and
loads the AV checkpoint, validates the planned step and restores a fresh model
token before sampling. After successful output completion it can seal the next
full AV state against the caller's media unit ID; it rechecks loaded identity
and releases its handoff after returning the durable receipt. It holds the model
weakly, is single-use and cannot be serialized. Failed generation discards AV.
Ordinary calls retain their prior transport behavior.

The real WGP alignment helper applies the ordinary first-clip minimum even
when `for_generation=True`. A legal 56-frame continuation window would otherwise
be expanded to 124 frames before dispatch. The private transport now preserves
its exact legal window at request alignment, observer timing, sampler frame
count and loaded-model dispatch. It rejects a changed window and caps native
windows at 345 frames. Ordinary calls still use their existing alignment.
The regression executes all three actual WGP alignment expressions with the
real alignment helper and native minimum/maximum, plus the actual dispatch node.

Public queue admission is still pending. Existing long-form plans encode local
segment timelines and cannot be reinterpreted as cumulative windows. The next
coherent unit must seal explicit cumulative context/extension/absolute-audio
steps against the authored semantic slices, inject the private dispatch from
the authorized worker, and checkpoint/publish the last complete output through
the final audio policy without concatenating cumulative snapshots. Recovery
must restore from durable AV before dispatch and preserve exact predecessor
receipt hashes. A resident model whose proof was invalidated by an ordinary
request needs a fresh private load; it must not mint a replacement proof from
configuration alone. Private staging crash-orphan retention/cleanup and actual
live GPU/encode/media/browser/human acceptance remain open.

CPU evidence: 44 distinct focused checks passed across queue integration,
disk recovery and WGP transport. The first combined 40-check run had one old
AST fixture missing the newly forwarded loaded model; that fixture was corrected
and the failing check passed. The additional journal restart and conflicting
sidecar checks passed, with affected enrichment/re-seal checks rerun. After the
independent review's authority correction and the private short-window fix,
27 queue/transport checks passed together; the unchanged 17 disk checks are
reused. The queue
tests execute actual AST-extracted launch matcher/checkpoint/enrichment/project
identity functions, real temporary files/journal, the real WGP wrapper and
sampler kwargs node, and the native sampler with CPU components. Bundle getter
substitution is explicit; prior actual temp-asset binding evidence is reused.
Scoped lint/format, syntax and diff checks pass. Launch and WGP retain exactly
817 and 445 pre-existing Ruff diagnostics respectively, with no new finding.
No full suite, real assets, CUDA sampling, generated media, service restart,
provider call or public activation is claimed.
The bounded independent review found the trusted-plan authority gap, corrected
above with explicit authority and a focused negative regression. Its noted
sidecar/journal crash residual remains explicit pending the existing orphan
recovery and upcoming final-replacement integration. There is no live queue,
encode or hostile same-UID isolation claim.

Recovery record: the existing physical checkout remains on `main`, same host
and workspace instance, active fallback item
`UH-20260927-MAESTRO-EXTEND-FLAGS` and continuous Goal. The exact claim
`maestro-h3-queue-recovery-20261002` covers the queue bridge, dispatch/recovery,
WGP/launch, two focused test files and this note. It was released/reacquired
through the supported helper to include the old AST fixture correction. Release
that exact claim after serial Git closure. Foreign dirty files and historical
SQLite tracker remain preserved. The rediscovered Maestro ready URL passed
health/readiness with 200; HTTP health is not browser acceptance. Launcher
destination/example/URL-capture checks are inapplicable because no launcher was
edited. The broad sprint remains active.

## Explicit cumulative window compiler

`h3_cumulative_plan.py` compiles one full authored narrative for the private
native FL2VA path. It reuses the existing v2 semantic compiler against disjoint
publication pieces, retaining its exact source, canonicalization descriptor,
event/dialogue ownership, execution slices and seal. A separately versioned
`cumulative_append` wrapper records each sampler window, retained history,
absolute native AV step and cumulative generated/published output clock. It
never upgrades or changes the meaning of an existing saved v2 plan.

For 500 requested frames, the default generated pieces are 345/119/51 and
published pieces are 345/119/36. Actual sampler windows are 345/141/73: later
windows include 22 retained context frames. Complete generated outputs are
345/464/515, with published outputs 345/464/500. The final trim is 15 frames;
hidden context is never counted as publication trim or a newly published tail.
The first sampler ceiling is caller-selected on the legal grid through 345;
extensions use a caller-selected multiple of 17 through 119. The compiler
requires at least 22 requested frames and bounds planning to 256 windows,
256 KiB source text and half the private manifest's byte ceiling. The manifest
writer must still check the complete enclosing request before queue admission.
These limits do not establish runtime RAM/VRAM or decode admission.

Sampler prompts preserve the full payload bytes of each publication record and
shift only generated canonical headers. Hidden history receives a neutral
record requesting preservation of already generated AV; it contains no authored
action or dialogue. Final alignment padding receives a separate neutral record
after the last published frame, so final blocking remains in its published
tail. Dialogue is protected during structural parsing and restored exactly;
literal shot labels, timecodes and field labels inside speech remain payload.
The existing source canonicalizer still requires dialogue blocks on one line;
that error is surfaced without changing the authored wording. This compiler
does not admit reference guides, alternate checkpoints or LoRAs. Their existing
contracts remain separate, and the eventual worker must enforce native runtime
eligibility before sampling.

Replay requires independently supplied expected source and plan hashes,
recompiles the full source, and compares the complete JSON contract. Changes to
geometry, prompt bytes, ownership, source descriptors, unknown fields or even a
re-sealed malformed journal are rejected. The validator returns independent
data rather than retaining mutable journal objects. Hashes provide drift
detection, not authorization; taking the expected hashes from that same journal
would not bind a job to trusted source authority.

CPU evidence: 15 focused compiler checks pass, including grid and tail rounding,
selected ceilings, absolute audio counts, exact unchanged publication contracts,
complete payload-byte preservation, one dialogue owner, completed-history
non-replay, published final blocking, sensitive subject matter on the same
compiler path, invalid bounds and trusted/re-sealed replay negatives. An initial
test incorrectly treated the legal 124-frame count as invalid and expected the
existing multiline-dialogue restriction to be relaxed; both expectations were
corrected and the current focused set passes. The bounded independent review
found one size-boundary defect: the builder checked bytes before adding its
hash, while replay checked the complete object. The builder now gates its final
serialized size, and the regression verifies rejection one byte below the
sealed size and successful build/replay at the exact boundary. No other concrete
compiler defect was reported. Scoped lint/format, syntax and diff checks pass.
No full suite, CUDA sampling, model loading,
service restart or public activation is involved.

Next coherent integration: create this plan only in the authorized private
request preparation path, bind its digest and normalized canvas to the owning
job/variant chain, inject `H3CumulativeQueueDispatch` outside serialized sampler
parameters, and seal media plus AV after successful WGP completion. Cumulative
recovery settings must distinguish full-output frame counts from the disjoint
semantic execution slice. Resume from the previous verified durable AV receipt
before dispatch. Final publication must use the last complete output through the
existing audio policy without concatenating cumulative snapshots; verified final
recovery must avoid denoising. Sidecar/journal crash recovery, private staging
crash orphans and fresh GPU/encode/media/browser/human acceptance remain open.

Continuation record: fallback item `UH-20260927-MAESTRO-EXTEND-FLAGS` and the
continuous Goal remain active in the same physical checkout on `main`. The exact
claim `maestro-h3-cumulative-plan-20261002` covers this note and the new compiler
and focused test file; release it after serial Git closure. Foreign dirty work
and the historical SQLite tracker hold remain preserved. Applicable policy
snapshot: Non-Negotiable Execution Workflow, Durable continuation, Bird-in-hand
delivery, Local Content Neutrality and proportional verification under the
owner's explicit request to run full suites less often. Launcher destination,
example, menu and URL-capture checks are inapplicable because no launcher was
edited. Pinokio reports the existing Maestro process running/ready, and fresh
probes against its rediscovered URL returned 200 for health and readiness.
HTTP health is not browser acceptance. The broad sprint remains unfinished.

## Private cumulative worker integration

The worker now prepares the sealed plan from the authorized job's private source
manifest before ordinary parameter processing. It derives each variant's chain
identity from that source, the job and the plan digest, then dispatches windows
in variant order. The transport object travels directly through the real handler
to WGP outside serialized task parameters. Exact sampler prompts and short legal
frame counts survive ordinary validation. WGP retains the private window size
instead of applying the generic sliding-window quantization; the focused case
uses 39 frames rather than the ordinary 37-frame result. Public HTTP selection
remains rejected and missing-window execution still requires the experimental
environment gate.

Eligibility is shared by admission and recovery. Recovery can verify an already
completed chain with the execution gate disabled, but cannot start a missing
window. Source/guide inputs, LoRAs, alternate continuation modes, prompt
enhancement, film grain, upsampling and auxiliary audio processing remain
excluded. Structural validation does not inspect creative subject matter.

After successful native generation and encoding, the worker seals the normalized
AV state, writes the private component sidecar, promotes the media and records
the completed recovery unit under the existing transition lock. Its dependency
evidence includes both predecessor media hashes and the predecessor AV receipt
hash. Verification derives authority from the original job rather than trusting
the journal's declared chain. Missing, changed or malformed source, canvas,
dependency and continuation metadata prevent reuse. A recovered terminal
window reaches final publication without validation/model dispatch or denoising.

Final publication copies only the last complete cumulative output. It never
concatenates overlapping full-output snapshots. The copy is verified before the
existing final-container audio policy runs, and its pending sealed sidecar is
prepared before atomic promotion. Components remain private and unchanged;
the selected final represents the complete published frame count at native FPS.
Cancellation and audio-policy failure publish no final. A local cancellation
bridge preserves interruption through the existing atomic publication helper,
which otherwise wraps an interrupted copy as a generic I/O failure. An already
verified final is reused without copying or reapplying audio processing.

Startup graph verification supplies the full dependency cursor. When the journal
is lost after media and sidecar publication, reconciliation reconstructs the
window chain and final from sealed sidecars plus existing AV receipts. Final
sidecars preserve variant/window positions and verified audio-policy attestation.
This does not establish adoption or cleanup of AV/sidecar/staging files created
before media promotion; that crash interval remains a separate open item.

Evidence after the final metadata guards: 40 focused CPU/mock checks passed in
`test_h3_cumulative_execution`, `test_h3_cumulative_queue` and
`test_h3_cumulative_dispatch`. Thirteen new checks exercise admission and
gate-disabled recovery, variant-major tasks, trusted-source replay, predecessor
evidence, malformed metadata, actual handler forwarding, actual WGP short-window
selection, actual worker sealing, recovered terminal bypass, final copy/reuse,
sidecar-only reconciliation, cancellation and audio failure. Five ordinary H3
recovery/publication regressions also passed earlier in this integration; those
unchanged checks are reused. New/changed service and test files pass Ruff lint
and format checks. Syntax compilation and diff checks pass. The large launch
and WGP modules retain their baseline 817 and 445 Ruff diagnostics respectively,
with no added diagnostic. Full suites were not repeated under the owner's
explicit request to run them less often; UI code was unchanged.

One bounded independent read-only review found no remaining concrete integration
blocker after correcting its initial film-grain/upsampling concern: the pure
admission validator already rejects those settings before parameter mutation.
This is source and CPU/mock evidence, including real worker seams and synthetic
AV state. No GPU/model-weight generation, real encode/audio measurements,
process-kill crash, browser or human acceptance was performed. The running
service remained healthy/ready after Pinokio URL rediscovery; it was not restarted
or upgraded to this private code during verification.

Next coherent work: resolve checkpoint references and bounded cleanup across the
pre-promotion crash interval, total chain storage and true peak decode memory,
then perform separately authorized live GPU/encode/recovery and media acceptance.
The 512 MiB retained-state limit and 2 GiB decoded-geometry limit remain
allocation bounds, not total RAM/VRAM admission. Keep public activation closed
until those acceptance boundaries are satisfied. Existing accepted Extend and
decoded-boundary evidence should be reused.

Continuation: fallback item `UH-20260927-MAESTRO-EXTEND-FLAGS` and the continuous
Goal remain active in the same physical checkout on `main`. Owned changes are
`app/launch.py`, `app/wgp.py`, `app/services/h3_cumulative_dispatch.py`,
`app/services/h3_cumulative_execution.py`, `tests/test_h3_cumulative_execution.py`
and this note. Release the exact claims
`maestro-h3-cumulative-worker-20261003` and
`maestro-h3-cumulative-admission-20261003` after serial Git closure, through the
supported helper. Preserve foreign dirty files and the historical tracker hold.
Policy exit checklist: ownership and dirty-state checks, focused verification,
durable evidence and serial Git closure apply; launcher destination, examples,
menu and URL-capture checks are inapplicable because no launcher was edited.

## Verified staged-window startup adoption

Authorized startup materialization now explicitly enables one bounded adoption
pass after ordinary cursor reconciliation, before recovered workers start. The
default reconciliation path preserves staged files without adopting them, so
local recovery discovery remains read-only. This pass applies only to private
cumulative jobs and exact job-prefixed video names in private recovery staging.
At most 256 components promote per pass; an oversized directory census is
uncertainty and produces no adoption.

A candidate requires its unchanged direct-child sidecar, exact media size/hash,
private component role, original source-derived plan, physical project identity,
AV receipt and completed predecessor. The predecessor must already verify
against promoted media. Candidates with the same variant/window position are
ambiguous and remain untouched. An existing destination is preserved. After
the ordinary staging promotion, recovery rebuilds and verifies the public-root
descriptor before updating the cursor, then reconciles finality again. No
staging descriptor is persisted as a completed media unit. The media stays a
private component until the existing last-output final publication succeeds.

This closes the sidecar-plus-complete-staged-media-before-promotion interval for
an authorized restored job: a focused restart fixture reconstructs and promotes
both windows with execution disabled and then verifies their normal receipts.
Incomplete AV-only files, partial temporary writes and missing-sidecar media are
not accepted. Their reference retention/cleanup, full storage admission and true
peak decode memory remain open. The existing transition lock serializes app
writers; it does not claim isolation from a hostile same-UID filesystem actor.
The default path performs no orphan deletion.

Focused evidence: staged source/media/AV/privacy/predecessor negatives,
symlink/private-directory rejection, ambiguous candidates, preservation of an
existing destination and read-only discovery all pass. The existing ordinary H3
publication/recovery regressions and startup identity/project recreation,
staged native adoption, final-adoption ordering and failed-retry retention checks
also pass: 17 execution checks and ten distinct adjacent recovery/startup checks.
The bounded independent read-only review found no remaining concrete blocker
in the current source or startup-only authority boundary. Service/test Ruff
lint and format, syntax compilation and diff checks pass; launch retains its
817 baseline Ruff diagnostics with none added. Full-suite evidence was not
repeated under the owner's explicit testing preference. Source/static and
synthetic filesystem recovery are the evidence
level; no process-kill crash, GPU generation, encode/audio measurement or browser
acceptance occurred, and the running service was not restarted for this change.

Continuation remains on fallback item `UH-20260927-MAESTRO-EXTEND-FLAGS` and the
active continuous Goal. The exact claim `maestro-h3-staged-recovery-20261003`
owns launch integration, the cumulative execution service, its focused tests
and this note; release it through the supported helper after serial Git closure.
Foreign changes and the historical tracker hold remain preserved. The applicable
policy checklist is unchanged: ownership, focused checks and durable/Git closure
apply; launcher-specific destination/examples/menu/URL checks do not apply.

## Native loading probes and two runtime-binding repairs

Recovery resumed in the same physical checkout and continuous Goal. Local and
stable-share HTTP health/readiness probes pass; the stable surface requires a
browser-style client header for these probes. This is endpoint evidence, not an
authenticated browser workflow. The running app was not restarted for this work.

A private standalone native/WGP probe was prepared for two short windows on a
320×192 canvas: 56 initial frames and a 39-frame sampler append, producing a
planned complete 73-frame output at 24 FPS. It uses installed scaled-FP8 H3 and
NVFP4 Qwen assets, 28 steps, eager SDPA and MMGP profile 4. CPU preflight verifies
the installed assets, existing loader terms, sealed settings and source hashes.
No downloads or terms changes occur. The probe declares its own private chain;
it does not impersonate an authenticated server job or enable public generation.

Two freshly leased native attempts stopped before sampling and produced no
media or AV checkpoint. Their owned processes exited, resources were released,
and both exact leases were withdrawn and confirmed closed. The first exposed a
false implementation-identity mismatch during checkpoint loading: MMGP changes
its two lazy quantizer discovery caches from `None` to populated runtime state.
The fingerprint now excludes only `_QTYPE_QMODULE_CACHE` and
`_QMODULE_BASE_ATTRS` in the exact `mmgp.quant_router` module. Functions, defaults,
routing priorities, other constants, package versions and selected loaded module
layout remain bound. This avoids making code identity depend on discovery order.

The second attempt passed that repaired guard and completed native MMGP setup,
then exposed the LoRA guard treating support hooks as loaded adapters. MMGP
installs `{owned_submodule: {}}` support metadata before any adapter is loaded;
empty hooks delegate to the original forward. The layout verifier now permits
only exact empty dictionaries keyed by the component's own named modules. It
rejects actual adapter entries, malformed metadata and foreign keys, and checks
module-level adapter data independently so an absent root registry cannot hide
an adapter. Public request and native LoRA exclusions remain enforced.

Evidence: 19 focused CPU runtime-binding checks pass. New regressions exercise
real MMGP cache discovery and real tiny CPU LoRA hook forwarding, preserve output
and layout identity, and reject changed routing priorities, loaded adapters,
stale registries, malformed shapes and cache-name collisions in other modules.
Ruff lint/format and diff checks pass. A bounded read-only review of each changed
boundary found no remaining concrete blocker. The probe supervisor also closes
parent-crash termination and worker lease-identity races; its Linux parent-death
stop passed a separate CPU process check. No full suites were repeated under the
owner's testing preference; no UI or launcher changed.

The LoRA repair is CPU-verified; a complete native run remains pending. The next
fresh exact GPU request is queued behind another project's active reservation.
Keep that same request and validate coherent authority before starting; queue
timing and a notification do not authorize work. The retained private probe must
check unchanged source bytes and installed assets, validate before each process
and every five seconds during work, stop owned work on failure or deadline,
unload and withdraw the exact lease. Its planned fresh-process AV restore,
prefix identity, real encode/audio and peak-memory observations have not passed.
Public activation, actual authorized queue/process-crash recovery, cleanup and
storage admission, full-resolution memory, decoded-prefix quality, browser and
human acceptance remain open. The continuous Goal and fallback item
`UH-20260927-MAESTRO-EXTEND-FLAGS` remain active; the historical tracker is unchanged.

## AV crash-write cleanup and retry/completion retention

New private AV temporary files carry the same `unit-{job_id}-` prefix as their
sealed checkpoints. A partial write remains inside the existing private staging
directory with mode 0600. This lets the bounded startup cleanup retain bytes for
live or failed/retryable jobs and retire a crashed partial after its job retires.
Anonymous legacy `.h3-av-*.tmp` files remain untouched: their names do not establish
which job owns them. No prompt or media-content inspection is introduced.

Completion cleanup now shares startup's automatic-retirement policy, which keeps
failed jobs' executable Retry data. It also retains completed cumulative jobs'
staging through the next startup. Their completed graph still requires the
stable AV checkpoint bytes to verify each sealed producer dependency. Removing
those bytes immediately after success could turn a completed snapshot into a
held incomplete recovery on restart. Later jobs' completion cleanup now preserves
earlier completed cumulative jobs as well.

Startup already verifies/materializes completed graphs before successfully
compacting their terminal journal snapshots and then retiring their staging and
request manifests. Unsettled terminal accounting retains those snapshots and
bytes. This existing retirement boundary is preferable to retaining checkpoints
for every surviving sidecar indefinitely. The interim retention is per job:
all its unit-prefixed staging survives until that boundary, including partials.
It is not a total chain storage bound or garbage collection of live-job orphans.

Focused evidence includes an actual CPU subprocess exiting during its first
partial checkpoint write, job-prefixed retention and terminal cleanup, unchanged
legacy-file preservation, and the actual worker success-cleanup seam preserving
failed jobs' verified two-window AV receipts. A completed cumulative graph test
uses real synthetic AV receipts and final sidecars, runs completion cleanup for
both that job and a later job, then executes the startup seam and requires graph
verification before compaction before cleanup. These three final regressions
pass, alongside four adjacent cleanup/startup checks; unchanged AV/adoption checks
from the 36-check affected run are reused. The independent review identified the
completed-graph retention gap and confirmed the existing startup retirement
authority. Service/test lint and formatting, syntax compilation and diff checks
pass; launch retains its 817 baseline Ruff diagnostics. No full suite was repeated.

The queued private native probe's source preflight must be refreshed for these
changed bytes before execution; it still requires the same request's fresh exact
grant. This milestone is source, CPU process and synthetic filesystem/queue
evidence. No CUDA generation, real media encode, production restart, browser or
human acceptance occurred. Total chain storage, live-job orphan pruning and true
peak-memory admission remain open, as do the native qualification requirements
above. The public cumulative gate remains closed and the Goal stays active.

Policy checklist: exact ownership and foreign dirty preservation, focused
verification, durable evidence and serial Git closure apply; launcher-specific
destination, example, menu and URL-capture checks are inapplicable. Release the
exact `maestro-h3-crash-staging-retention-20261003` claim after Git closure; retain
only the narrow unfinished private native-probe tree through the supported helper.

## Early cumulative output admission

Request preparation now mirrors the existing native retained-state (512 MiB)
and full-decode (2 GiB) allocation limits for every planned cumulative window,
before task creation or model loading. It counts the complete generated frame
and audio geometry, including terminal grid padding that publication later
trims. Accepted plans and the native limits are unchanged. Recovery preparation
uses the same deterministic geometry checks. These are output-size screens;
they do not establish total CPU/GPU peak memory or checkpoint disk capacity.

Two new focused admission regressions pass: oversized retained/decode geometry
is rejected without tensor allocation or request mutation, and the terminal
padding boundary is counted before publication trim. The existing gate/recovery
exclusion and task-serialization checks pass, as does the focused native
pre-sampling full-decode guard regression. Ruff lint/format, compilation and diff
checks pass. One test invocation named a nonexistent native test class; the
corrected individual test passed. No full suite was repeated.

A bounded independent read-only review confirmed parity with the native byte
formulas and identified the remaining aggregate checkpoint-storage preflight:
each repeat retains a full cumulative checkpoint for every window. A planned
byte estimate must be compared with staging-filesystem capacity before loading,
while preserving write-time error handling because free space can change. That
capacity work and process peak measurements remain open.

The refreshed installed-asset/source preflight passed, and a fresh exact lease
started the private two-window native probe. Its supervisor validates authority
every five seconds and stops its owned process on failure or deadline. Native
generation, real media, fresh-process restore and public acceptance remain
pending until the retained probe results are inspected. The public gate stays
closed; the running app and historical tracker were not changed.

Exit checklist: exact source/test/doc ownership and foreign dirty preservation,
focused evidence, durable records and serial Git closure apply. No launcher was
changed, so destination/example/menu/URL-capture checks are inapplicable. Release
only the early-admission claim after Git closure; retain the unfinished private
probe claim until its owned execution and evidence are complete.

## Short private first-window duration parity

The next leased native attempt passed the repaired loading guards and native
MMGP setup, then stopped before denoising: the planned 56-frame first capture
still used the ordinary five-second minimum. No media or AV checkpoint was
produced. The owned worker exited, and its exact lease was withdrawn and
confirmed denied. Its logs and closure receipt remain in the private evidence
tree. This is a real loading-path observation, not generation acceptance.

The native duration guard now applies the existing 22-frame minimum to private
cumulative first captures as well as append tails. The private experimental
gate and FL2VA/reference/LoRA/cache exclusions run before this condition.
Ordinary requests retain their five-second minimum. Private capture now
requires an integer frame count of at least 22 before native grid alignment;
raw 5, 6 and 21, floating-point counts and booleans are rejected. No public capability or model-quality promise changes.

Two focused CPU checks pass through the real native generate method with fake
weights: first captures of 22 and 56 frames each accept a 17-frame append with
unchanged retained video/audio prefixes, reject an ordinary 56-frame request and
a private five-frame request, and preserve the existing absolute audio-span
regression. Two adjacent gate/exclusion and exact handoff checks also pass.
Test lint/format, syntax and diff checks pass. Native source keeps its
ten baseline Ruff diagnostics with unchanged code/message counts. The refreshed
installed-source/asset preflight passes. No full suite was repeated.

A fresh exact lease has started a retry of the same standalone two-window
probe. It remains pending; its supervisor validates authority every five seconds.
Independent review caught raw below-22 counts rounding upward before the
duration guard; the pre-alignment integer/minimum check and focused cases above
resolve that finding. Native results remain separately required.
Public activation, authenticated queue crash/restart, full-resolution
memory, aggregate storage admission, browser and human acceptance remain open.

The next native attempt completed all 28 first-window denoising steps and
reached AV decoding, then failed in standalone WGP metadata preparation because
its optional application global was absent. No completed output/checkpoint was
verified. The owned process exited, and that exact lease was withdrawn and
confirmed denied. The private probe now supplies an absent optional UI/plugin
application context; production WGP is unchanged. Its CPU preflight was refreshed
for the final duration guard and probe bytes before another fresh lease started.

The source milestone's exit checklist remains exact ownership, focused tests,
review finding resolution, durable evidence and serial Git closure. Launcher
destination/example/menu/URL checks are inapplicable. Release the short-first
claim after closure; retain the narrow unfinished probe claim.

## Fresh-process default-config identity repair

Two native attempts now encode the first 56-frame window and seal a verified
AV checkpoint; fresh second processes still rejected their loaded-bundle
identity before sampling. All owned workers exited and both exact leases were
withdrawn and confirmed denied. The first file's independent FFprobe check found
56 frames, 24 FPS, 320×192 HEVC and stereo 32 kHz AAC; measured true peak was
−21.72 dBTP. This remains a partial first-window result, not a complete chain.

A private diagnostic compared both verified binding payloads before the unchanged
restore guard. Only the video/audio Diffusers configs' `_use_default_values`
list order differed. Field membership, config values, code, package versions,
asset hashes, effective dtypes and tensor layout matched. Sorting only those
two lists made the complete recorded binding inputs equal. Installed Diffusers
0.36.0 constructs this metadata list from a set and uses it as field membership.

The native loaded contract now copies each VAE config and sorts only that exact
metadata field. It requires a list of unique nonempty strings. Actual values,
all other sequence order and the default-field membership remain bound; the
producer's config is not mutated. Changed implementation code intentionally
invalidates older private runtime receipts; a fresh native run is required.

Three focused CPU checks pass: actual Diffusers ConfigMixin configurations agree
across three fresh hash-seed processes; the real loaded getter accepts only
default-name reordering while rejecting value/semantic-order/membership changes
and malformed metadata; existing replacement/release/ordinary-request/scheduler
invalidation remains enforced. An initial subprocess fixture lacked the required
`config_name`; the corrected fixture and final focused run pass. Lint/format,
syntax and diff checks pass; native source retains its ten baseline diagnostics.
The installed-assets/source preflight is refreshed. No full suite was repeated.

Bounded independent review found no remaining concrete issue with the narrow
normalization; it verified installed Diffusers field-membership semantics.
Fresh native two-window restore qualification remains pending for these bytes. Aggregate checkpoint capacity, true
full-resolution peak admission, authenticated queue crash/restart, final media,
browser and human acceptance remain separate requirements. Public cumulative
generation stays disabled and the continuous Goal remains active.

## Remaining checkpoint storage admission

The worker now screens free bytes on the authorized project's private staging
filesystem before dispatch/model loading for each pending cumulative window.
The bound sums every remaining full FP32 AV state, including native terminal
padding and the writer's maximum header, plus all windows for later variants.
Verified earlier windows and completed variants are excluded. The staging
directory identity is checked after the capacity query; unavailable or changed
storage fails closed with a useful retry message. No output or request is removed.

Three focused CPU checks pass: real serialized AV checkpoints fit the bound;
repeat and resumed-tail accounting accepts exact capacity and rejects one byte
less; the actual extracted worker block stops before dispatch without modifying
the request; unavailable storage errors are redacted and directory substitution
is rejected. Lint/format and syntax checks pass. The large launch module retains
its 817 baseline lint diagnostics with no additions. No full suite was repeated.

This is an early capacity screen, not a storage reservation or total-output
quota. Encoded media, final copies and future crash/retry orphans are not
estimated. Concurrent consumption can still exhaust space, so checkpoint-write
failure and retry handling remain necessary. Full-resolution GPU peak admission,
authenticated queue crash/restart, browser and human acceptance remain separate.

Independent review found no estimate or worker-placement defect. It identified
that repeated insufficient-space retries can consume the existing recovery
budget. The refusal now gives the rounded-up minimum additional MiB and states
that encoded media needs more space. The one-byte-deficit regression verifies
that actionable amount and passes. Recovery attempt semantics remain unchanged;
users must prepare the indicated storage before retrying.

The saved job failure now carries that guidance through the existing reviewed
public-copy boundary. Insufficient capacity raises the already permitted exact
`ValueError` type; only the complete numeric template with a positive bounded
decimal MiB amount is admitted. Arbitrary recovery exceptions remain hidden.
Two changed focused regressions pass, including the actual extracted
`_safe_failure_updates` path's saved message/error/detail and rejection of
suffix/path/leading-zero injections. Public-copy lint retains its two baseline
diagnostics; execution/tests lint and format pass. No broad suite was rerun.

## Native fresh-process continuation observed

This observation supersedes the earlier pending standalone native continuation
and final-media qualification statements. The checkpoint capacity screen above
also supersedes the earlier missing aggregate checkpoint estimate; it does not
establish a total storage reservation.

The next exact checkout-bound lease completed the standalone two-window WGP
probe with installed assets. The first process generated 56 frames and sealed
its AV checkpoint. A separate second process restored it and sampled the legal
39-frame window to produce 73 cumulative frames. Both complete binding payloads
were identical. The retained normalized video and audio latent prefixes compared
exactly equal; this does not imply decoded pixel-prefix identity.

The native output passed the existing cumulative final-copy helper and actual
audio policy in the private synthetic project. Independent FFprobe found HEVC
320×192, 73 frames at 24 FPS (3.041667 seconds), with stereo 32 kHz AAC
(3.040000 seconds). Final true peak was −13.0 dBTP, below the −1.0 dBTP ceiling;
no attenuation was applied and the final-copy hash matched the source. No
concatenation occurred. This is real media and final-copy evidence, not an
authenticated queue publication or human quality acceptance.

The maximum observed CUDA allocation across the two windows was 21,356,353,024
bytes; maximum CUDA reservation was 21,600,665,600 bytes. Process peak RSS was
56,179,724 KiB. These observations apply only to this tiny-canvas probe, not to
full-resolution admission or a general memory/performance guarantee. Both
workers exited after model release; the exact lease withdrawal was confirmed
denied. The local and stable-share health/readiness endpoints returned 200 while
the app remained running.

Private receipts retain the exact preflight source/probe hashes, asset identities,
window/binding/AV receipts, capacity observations, final-container inspection and
lease closure. The later actionable storage-error wording changes no native
algorithm; native evidence remains tied to its recorded preflight bytes. Full
resolution resource qualification, authenticated queue crash/restart, rendered
browser behavior and listening/visual acceptance remain open. Public cumulative
generation remains disabled; the continuous Goal remains active.

## Full-canvas checkpoint and desktop-restart recovery

The next private probe completed its first 124-frame window at the shipped
1344×768 canvas, with 28 steps, eager SDPA and requested offload profile 4.
The actual sealed AV checkpoint and encoded media hashes were independently
verified after the desktop restart. FFprobe found 124 frames at 24 FPS
(5.166667 seconds), HEVC video and stereo 32 kHz AAC (5.152000 seconds).
Its complete binding payload matched the interrupted second process's payload.

The first window took 661.3 seconds. Its observed maximum CUDA allocation was
5,184,347,648 bytes, CUDA reservation 8,042,577,920 bytes, and process peak RSS
48,088,672 KiB. This run selected a conservative asynchronous MMGP offload plan
after reporting an unsupported cached residency region. These are first-window
observations for that runtime, not complete-chain peak admission or a general
performance guarantee; they cannot be compared directly to the tiny-canvas
probe's different offload plan.

The desktop restart terminated the owned supervisor and second worker before a
completed 141-frame receipt was saved. The old exact lease was withdrawn and
confirmed denied. The accepted first window is retained; it will not be
regenerated. A CPU preflight verified that a resume-only supervisor uses the
same job, plan, production source hashes and installed asset identities. Its
fresh exact request is queued, with a bounded wait and coherent validation
before starting only the second window. The existing supervision retains its
five-second validation cadence, operation deadline and owned-child stop guard.
Completion must still prove the retained AV prefixes and final container.
Two initial waiters stopped before GPU startup: the CLI can emit multiple
queued JSON snapshots, and the first outbox receipt is asynchronous. Both exact
requests were withdrawn and confirmed denied. The corrected waiter reads the
exact durable outbox, treats an absent receipt or acknowledgement as no authority,
and passed disposable checks for missing, queued, granted, denied, wrong-request
and malformed responses. Its observed current state is queued.

Local health/readiness and stable-share readiness returned 200 after recovery.
No Maestro restart or public cumulative activation occurred. Authenticated
queue crash/restart, full-chain resource qualification, browser rendering and
human listening/visual acceptance remain open.

## Cumulative offload-plan prerequisite

Source inspection found that the existing sealed offload contract represented
a cumulative request as one ordinary clip. It could therefore reject the
server-generated continuation tasks before dispatch. The contract now derives
the actual sampler windows and newly published tails from the existing
cumulative compiler, including during recovery with the experimental gate off.
For the retained full-canvas plan those pairs are 124/124 and 39/17; the final
cumulative publication remains 141 frames. Sampled frames include retained
context and must not be presented as additional delivered frames.

The generation worker supplies its independently derived cumulative plan to
the existing child offload check. It binds the plan digest, window index,
sampler geometry and cumulative output geometry before applying any profiles.
The entire manifest must pass before profile mutation; ordinary dispatch keeps
its existing path. This preserves the sealed manual/default profile contract
and does not establish live queue or physical offload acceptance.

Three new focused CPU regressions pass: gate-off sealing/recovery parity and
changed-source rejection; real repeated cumulative task construction and
profile assignment; changed/incomplete child evidence rejected before mutation.
Three existing focused offload/child-dispatch checks also pass. An initial
test invocation named the wrong existing class; the corrected check passes.
The new test file passes lint/format; syntax and diff checks pass. Launch and
offload modules retain their respective 817 and one baseline lint diagnostics
with no additions. No full suite was repeated. Bounded independent review found
no concrete correctness or ordinary-job regression. Multi-window native
completion and runtime peak evidence remain unverified.

## Full-canvas resume and final-container evidence

The resume-only worker completed the retained 124→141-frame chain at 1344×768.
It sampled only the second window: 39 sampler frames, including retained
context, delivered 17 new frames. The first window was not regenerated. The
second process's complete runtime binding payload equalled the first process's
payload; retained video and audio latent prefixes were byte-identical. The
terminal latent shapes were video `[1, 24, 42, 48, 84]` and audio `[2, 32, 235]`.
This is real standalone native/WGP fresh-process restore evidence using private
synthetic project identities, not authenticated HTTP queue recovery.

The existing final-copy helper copied the whole terminal container without
concatenation, and its final hash matched the source. Independent FFprobe found
HEVC 1344×768, 141 frames at 24 FPS (5.875000 seconds), with stereo 32 kHz AAC
(5.856000 seconds). The actual final audio policy measured −22.6 dBTP, below the
−1.0 dBTP ceiling; no attenuation was applied. A six-frame contact sheet,
including frames 123 and 124 at the append boundary, was inspected. This does
not establish motion, listening or human quality acceptance.

The second window took 409.4 seconds, including loading, sampling and encoding.
Across both completed windows, maximum observed CUDA allocation was
5,184,347,648 bytes, reservation was 8,042,577,920 bytes, and process peak RSS
was 48,362,036 KiB. These are observations for this exact conservative offload
run, not general admission limits or speed/memory promises. The resumed owned
supervisor and child exited, and withdrawal of the fresh exact lease was
confirmed denied. Private receipts retain both binding payloads, source/asset
pins, checkpoint/media hashes, resources, final-media inspection and closure.

## Gated HTTP admission source milestone

The public request selector `h3_cumulative_append` now has a source admission
path through the existing project-authorized generation route. It requires an
exact boolean, the operator's experimental gate, Base H3 video, one timeline
prompt and no LoRAs or prompt enhancement. Existing project, model, legal,
media-input and client-private-field checks remain in place. Only the server
authors the private cumulative marker; final normalized source validation runs
before a job identifier or recovery manifest is allocated.

Immediate and held cumulative submissions use the existing durable queue and
generation worker. They skip decoded clip planning, ordinary preparation and
the ordinary sliding-window length adjustment. The sealed source retains the
requested 141 frames and 124-frame first window. Ordinary H3 submissions keep
their preparation/planning path. Preview returns separate cumulative geometry:
sampler windows 124 and 39, delivered tails 124 and 17, final 141 frames. It
exposes no authored prompt or recovery identity and supplies no ordinary time
or memory estimate. Final metadata preserves only the public mode selection.

Six focused CPU admission tests pass using the complete source routes and real
cumulative compiler with mocked authorization/queue collaborators. They cover
invalid/gate-off/private-field inputs, unsupported settings and denial before
queue mutation; immediate and held admission; bounded truthful preview;
ordinary H3 compatibility; and identical admission paths for authorized
sensitive creative prompts; and rejection of otherwise-valid accelerator
profiles using real Turbo, Spectrum and LightX2V compatibility validators.
Three adjacent focused queue/planning checks pass.
New tests pass lint/format; source compilation and diff checks pass, with the
existing launch lint baseline unchanged. No full suite was repeated, following
the owner's request to run it less often. Bounded independent review identified
that accelerator profiles could pass ordinary admission before the cumulative
sampler rejected them. Admission now rejects those profiles after style/default
normalization and again before job allocation, with an actionable error. The
new regression proves the otherwise-valid candidates create no queue state.
The test's first attempt lacked the Turbo checkpoint URL in its model stub;
the corrected structural fixture passes. No second review was needed for this
focused correction.

The live operator gate remains off. Authenticated live queue publication and
crash/restart, rendered browser behavior, and human listening/visual acceptance
remain open. Native restore, mocked route checks and final-copy media evidence
do not satisfy those gates. The continuous sprint Goal remains active.

## Gated cumulative browser controls

Advanced Settings now contains an experimental cumulative timeline selection
and a legal first-window frame selector. The host advertises availability only
for exact Base H3 with the cumulative operator gate enabled. An unavailable
saved selection can still be turned off. The existing Generate and held Queue
paths submit the public boolean; the server remains authoritative for capacity
and final normalized admission. No new public activation is implied.

Cumulative total duration edits and Load Settings retain exact total and
first-window frame counts rather than using the ordinary clip grid. Model
metadata refresh preserves those counts. The duration and prompt controls omit
ordinary shot/section counts, and H3 performance presets and time/memory
estimates are unavailable. Selecting this mode invalidates outstanding ordinary
estimate requests. Incompatible references, LoRAs, acceleration profiles,
enhancement and postprocessing produce actionable errors before uploads or job
creation. The prompt is retained without subject-matter inspection.

Both public cumulative and decoded clip-continuity booleans are classified in
the canonical saved-profile catalog. The existing clip-continuity flag was
missing from that catalog and prevented complete profile capture even when
false. Profile capture and restore now retain these public choices and clear
omitted legacy selections; private worker state remains excluded.

Twelve focused UI/profile checks and eight source admission/profile checks pass.
They include actual store immediate/held submission, rejection before mutation,
estimate suppression, model-options hydration, Load Settings, and saved-profile
round trips, plus stale estimate response rejection and ordinary estimation
after turning the mode off. Backend profile normalization accepts the boolean
and rejects malformed values. Two adjacent backend profile storage/catalog
checks also pass. The frontend production build and scoped lint pass; launch
lint retains its existing 817-diagnostic baseline. No full suite or native/GPU
workload was repeated.

The built UI was inspected in a temporary local browser tab. The cumulative
checkbox was visibly disabled and explained host unavailability while the gate
remained off. The existing owner editor draft was preserved. Local health and
readiness returned 200, and the stable-share readiness endpoint returned 200
through curl. The Python default user agent received Cloudflare 1010, and this
browser blocked the stable-share navigation; neither observation proves remote
browser acceptance. Enabled-mode rendered interaction, authenticated live queue
publication/recovery and human quality acceptance remain open.

The bounded independent review found two source issues: a nullable profile
selector could reach an HTTP route requiring an exact boolean, and Generate
still showed a calibrating badge despite unavailable cumulative estimates.
The cumulative profile field now rejects null, with a focused real profile
normalization/HTTP-selector regression. Both Generate button branches omit the
badge in cumulative mode. A focused component markup check uses the arranged
store snapshot, covers ready and disabled controls, and preserves the ordinary
calibrating badge after turning cumulative mode off. This markup check is not
enabled-mode live browser acceptance. The first test-harness attempts needed
Node require/JSX setup and the arranged Zustand server snapshot; the corrected
fixture passes. The final production build also passes after these corrections.
