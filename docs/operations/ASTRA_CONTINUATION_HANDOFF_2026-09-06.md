# Astra continuation handoff — 2026-09-06

## Current GPU authorization — owner update, 2026-09-06

The owner explicitly authorized GPU work subject to coordinator grants and
required the installed `gpu-coordinator-client` skill. This supersedes the
GPU prohibition in the copied native Goal and historical CPU-only instructions
below. It permits the planned GPU acceptance work once the client contract is
satisfied; it does not establish that a lease exists or that any GPU acceptance
has passed. Service restarts, credits/SSO activation, new providers, and browser
filesystem permissions retain their separate existing boundaries.

The coordinator owner resolved the initial allowlist rejection (`harness-a97`).
This migrated checkout is now registered as `maestro-local`; former-checkout
identities remain separate. Registration is never GPU authority: validate the
durable grant, current status/epoch/time, and exactly matching raw ledger entry
before every start, stop by its end, and withdraw/confirm after completion.

### W4A8 runtime prerequisite — 2026-09-07 UTC

The first leased synthetic check failed because installed comfy-kitchen 0.2.26
lacked the W4A8 APIs. Its lease was withdrawn. The repaired installer uses UV
with the selected interpreter, builds archived pinned source, preserves a
rollback copy, invalidates the old marker, and restores package/marker on
validation failure. Package fingerprinting happens before import. Schema-2
markers bind the installed bytes as well as revision and runtime identity;
Sage capability remains independent.

The isolated candidate passed 80 CPU tests with 11 explicit skips (91 total),
syntax checks, and independent review. Native Windows execution remains
unverified. A second exact lease ran the small synthetic check on Linux with
RTX 5090 (SM 12.0), Torch 2.10.0+cu130, and Triton 3.6.0. Eager weight
quantization and Triton W4A8 linear dispatch passed with relative MAE
0.07095864661654136. The pinned package and matching schema-2 marker are now
installed in `app/env-rtx50`; the prior package remains in the private rollback
artifact. The GPU child exited and the lease was confirmed cancelled.

Receipts: `.artifacts-temp/astra-w4a8-installer-20260907/`,
`.artifacts-temp/astra-w4a8-kernel-20260907/`, and
`.artifacts-temp/astra-w4a8-repair-20260907/`. This is runtime/kernel evidence;
full checkpoint generation, visual quality, and human acceptance remain open
in `GPU_ACCEPTANCE.md`. No app restart or full-model run occurred.

### Observed offload recovery — 2026-09-07 UTC

Denoise OOM recovery now uses WGP's successfully loaded MMGP profile instead
of reconstructing task intent and applying the standing floor. Observed 4.5
retains its same-setup retry and subsequent escalation to 5 on the same canvas;
unknown or omitted profiles preserve the original failure without fabricating
a retry. Persistent host-limit denial requires an observed profile 5 and the
existing mid-denoise/exhaustion evidence. Existing host-limit history is retained.

The isolated HEAD-based candidate passes all 89 tests across observed-profile,
OOM policy, offload-plan, host-limit, memory-lifecycle, and WGP boundary suites.
Syntax checks pass. Receipts and preserved preimages are in
`.artifacts-temp/astra-observed-offload-20260907/`. Evidence is CPU/synthetic,
not live OOM recovery or a GPU memory/performance claim.

The native-max offload-floor WIP remains unadopted. Before changing its floor,
reconcile sealed integer profile intent with effective fractional profiles;
preserve completed-prefix provenance and calibration semantics. Its unstaged
test expecting observed 4.5 to act like 5 is superseded by the observed-profile
contract and must be corrected when that WIP is integrated. Other native-max
policy/default and resolution-plumbing changes remain separate.

## Authored-shot packing — 2026-09-07 UTC

Short authored shots now pack with their neighbors into legal native windows
without losing their published timing. A shot splits only when its required
generation, including an end-anchor tail, exceeds the active ceiling. Final
window folding discards previous padding, preserves the required final tail,
and realigns the merged frame count instead of summing two grid values.

A continuing authored action gets an opening-only instruction in its first
window; later windows retain continuation and single-owner dialogue behavior.
Cuts inside packed windows retain their authored frame position. Timestamp and
range errors explain decimal seconds versus hours:minutes:seconds through
exact reviewed copy, without echoing authored input.

The final isolated candidate passes 452 planner/Studio/Director/duration/recovery
tests plus 64 frame-lattice/preflight/optimization/audio tests. Its deterministic
686-case packing matrix covers frame-grid legality, exact published totals,
end-anchor tails, and preservation of fitting shots; a separate timed case
covers the manual 192-frame ceiling. Independent candidate review is clean.
A synthetic plan compiled by the prior committed planner still passes saved-seal
validation byte-for-byte; changed new-plan instructions receive a different
seal. This is CPU/synthetic and local codec evidence, not live model quality or
GPU recovery acceptance. Receipts and source hashes are in
`.artifacts-temp/astra-shot-packing-20260907/`.

## NVFP4 native LoRA forwarding — 2026-09-07 UTC

NVFP4 modules now keep the native-forward marker as an instance attribute,
which the pinned MMGP router copies. Ordinary LoRA execution retains the
module's input scaling and real dequantization. The numerical CPU regression
uses the installed router load, hook, wrapper, and native forward; it checks
scaled base output plus factor delta, floating output, and unchanged packed
weight bytes across three ranks and both bias modes.

LightX2V inputs now pad rows to 128 and trim the result back to the original
shape. Empty inputs retain their dtype and do not report or dispatch a kernel.
Kernel RuntimeError/OOM propagation remains intact. The uncommitted global
LoRA monkeypatch and catch-all kernel fallback were removed instead of adopted;
the raw-integer transformer activation casts remain unadopted pending their
actual producer/dtype diagnosis.

The isolated candidate passes 121 of 131 tests (10 explicit runtime skips),
plus the five NVFP4 CPU tests in the CUDA-13 environment. Both local MMGP 3.7.12
installations' source bytes match their installed RECORD entries. Independent
review is clean. Receipts/preimages are in
`.artifacts-temp/astra-nvfp4-native-20260907/`. These are CPU numerical and
mock-kernel dispatch checks, not live GPU kernel, full-model, or Windows proof.

Remaining task-owned compatibility work:

- `NVFP4-K32`: CPU scale-layout repair is complete. Full physical 128-by-4
  scale tiles are deswizzled before logical columns are cropped; extra complete
  tiles remain supported and incomplete tiles fail explicitly. Tests match the
  eager reference exactly for widths 32/64/96, both nibble layouts, and
  FP32/BF16/FP16. The full candidate passes 124 of 134 tests (10 runtime skips),
  plus eight focused CPU tests in the CUDA-13 environment. Source/reference
  hashes and independent clean review are retained under
  `.artifacts-temp/astra-nvfp4-scale-20260907/`.
- `NVFP4-LIGHTX-SHAPES`: a fresh validated lease attempted nine small native
  LightX2V cases on RTX 5090 / Torch 2.10.0+cu130. The first case (logical M=1,
  N=128, K=64; padded activation rows=128) reached quantization and GEMM but
  failed with `cuBLAS error: 7`. Zero numerical cases completed. The process
  exited and the lease was withdrawn/confirmed cancelled. No retry, fallback,
  package mutation, model load, or service restart occurred.
  A subsequent source-informed, separately leased process-only
  `LIGHTX2V_NVFP4_GEMM=cutlass` comparison passed all nine identical shapes
  (relative MAE 0.00694–0.05556; 8,785,920 peak allocated bytes). Its lease is
  confirmed cancelled and its selector expired with the child process.
  Maestro's default remains unchanged; this is small synthetic kernel evidence,
  not full-model quality/performance acceptance.

  ELF/loader inspection found the extension requires `libcublasLt.so.12`,
  resolving to CUDA 12.0's `libcublasLt.so.12.0.2.224`; that header lacks the
  FP4 A/B scale-mode attributes used by the inspected source. The default
  cuBLAS path is therefore strongly implicated, but the exact failing API call
  is not instrumented. Source snapshot and installed binary selector strings
  agree; there is no build attestation linking that snapshot to the wheel.
  [Inspected source](https://github.com/deepbeepmeep/kernels/blob/2808bfb073bd91e4fe3ef83712f600b8d642579b/lightx2v_kernel/csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu).

  A second, separately leased diagnostic exposed an existing RECORD-verified
  cuBLASLt 12.8.3.14 library through one task-private symlink and a child-only
  loader path. Actual process maps confirmed the intended library. The default
  cuBLAS route moved past the prior error 7 but failed on the same first case
  with `Unable to find suitable cuBLAS GEMM algorithm`; zero numerical cases
  completed. The process exited, its lease is confirmed cancelled, and the exact
  temporary link was removed. No loader path, kernel default, or installation
  was changed permanently.

  Therefore library resolution contributes to the original failure, but a
  newer CUDA-12 library alone is not sufficient acceptance. The instrumented
  CUDA-13 diagnostic below now passes the same case. Next: build the complete
  pinned extension for the selected runtime, verify package/ELF dependencies,
  and stage rollback before a separate installed-runtime acceptance lease.
  Preserve rollback and the working CUTLASS diagnostic, but do not promote a
  permanent route from these small tensors. Source and acceptance must be
  checked independently on Windows. Receipts are in
  `.artifacts-temp/astra-lightx-cublas128-20260907/`; the completed CUTLASS and
  ABI records are in `.artifacts-temp/astra-lightx-route-20260907/`.
- `NVFP4-DORA`: MMGP's DoRA branch bypasses the native-forward marker and
  needs separate scaled-base/dequantization analysis and numerical acceptance.
  Ordinary low-rank LoRA evidence cannot close this item. Do not restore broad
  monkeypatches or integer casts as a workaround.

## CUDA-13 diagnostic result — 2026-09-07 UTC

The local CUDA 13.0 compiler, selected environment's cuBLASLt 13 headers/library,
and Ninja/G++ are available. A task-private diagnostic extracts the unchanged
cuBLAS math/descriptor path from the recorded upstream snapshot, removes only
unused CUTLASS includes, adds failing-call/line diagnostics, and registers an
independent Torch operator. Review confirmed parity; the runner supplies the
upstream tensor dtype/device/contiguity/shape checks before calling it.

Artifacts: `.artifacts-temp/astra-lightx-cu13-build-20260907/` contains
`cu13_diagnostic.cu`, `build-manifest.json`, `gpu-plan.json`, and `run_once.py`.
The coordinator granted the queued request after the other project released
its reservation. The source-bound runner validated grant/status/epoch/raw
ledger before compilation and again before GEMM. Compilation used CUDA 13.0,
one Ninja worker, and the selected CUDA-13 Python environment. ELF inspection
confirmed `libcublasLt.so.13` and `libcudart.so.13` dependencies, with no CUDA-12
dependency in the diagnostic extension. Process maps confirmed the selected
CUDA-13 libraries (the unchanged original quantizer also loaded its own older
libraries).

The single M=128/N=128/K=64 diagnostic GEMM passed with finite output and
relative MAE 0.0069444444961845875. Binary SHA-256:
`dc0d700b1f712928414e4eef5d86bb2e1167f86a28fa17e2d9c332a08cf4e577`.
The process exited and the lease is confirmed cancelled. No installed package,
launcher, default selector, or service was changed. This is native diagnostic
GEMM evidence, not a complete extension rebuild, installed-runtime rollout,
full-model generation, performance, or Windows acceptance.

Next: produce a reproducible complete package build against the selected
runtime, assert resolved ABI dependencies and source/package provenance,
preserve the installed baseline for rollback, and perform installed-runtime
acceptance under a new exact lease. Do not promote the task-private operator
or process-only CUTLASS selector into the default path.

While waiting, a direct CPU audit of the uncommitted `h3_prompt_adapt.py` found
two reproducible blockers to integration: an embedded `summary:` label inside
canonical dialogue causes the exact dialogue span to disappear during family
mapping, and freeform dialogue appears twice after Ref2VA wrapping (summary
plus detailed description). The recorded source hash and synthetic results are
in `prompt-adapter-audit.json` under the diagnostic artifact. Do not adopt that
adapter unchanged or treat its eight simple tests as preservation evidence.
Keep single-owner dialogue, literal authored text, and sealed execution digests
as the acceptance conditions when replacing its field parser/summary behavior.
The clip/model-count probe retained both clips and is not a failure finding.

## Successor progress

The successor's native Goal is active and continuous. The original handoff
below remains provenance; these completed slices supersede its first failure
list and part of its account UI work:

- `945ab68`: all seven named recovery failures are resolved. Five were stale
  source probes; two exposed continuity text whose execution digests were not
  updated. Plan construction now seals the final bytes, and Director restores
  matching digests when normalizing carry. Projection leaves sealed source
  plans unchanged. Duplicate WGP probes were removed in favor of the existing
  implementation tests. The isolated candidate passed 322 tests across the
  complete recovery, planner, visual-continuity, WGP implementation, and
  Director H3 suites, plus syntax/publication checks and independent review.
- `55153e1`: required account sign-in precedes Welcome and has matching focus
  and visual priority above optional dialogs. Optional account/support behavior
  is preserved. The isolated candidate passed 472 UI tests, 68 remote-access
  contracts, TypeScript, production build, and independent review.
- `e3bcb62`: reference-pack quality checks are optional and default to Off in
  the UI. Retry/Edit honor the current checker choice in both directions;
  disabled review ignores stale reviewer fields. New Off results have an
  explicit unreviewed public state without failure reasons or pending-grade
  badges. Recovery binds that state to the exact Off metadata, retaining the
  existing single-candidate selection marker and historical metadata reads.
  The API's omitted-review default is preserved for compatibility. The final
  isolated candidate passed 215 backend tests, 473 UI tests, application/E2E
  TypeScript, production build, syntax/publication checks, and independent
  review. Private evidence is in `.artifacts-temp/astra-optional-review-20260906/`.

- `dd23be5`: the H3 resolution-report parser uses the existing declared
  `tomli` fallback under Python 3.10. Its 60-test suite passes on Python 3.10
  and 3.11, with the exact pinned-uv offline probe intentionally skipped once
  on each runtime. No dependency installation or resolution run was performed.
- `5173276`: model and projector download failures have bounded messages and
  suppressed private causal tracebacks. A registered vision model cannot
  silently become text-only; failed replacement preserves the incumbent
  runtime, retry clears loading state, and unregistered text fallback remains
  available without raw error logging. All 80 isolated LLM tests pass. The
  hardware-identity test uses explicit visibility stubs under the CPU mask.

The current Krea declaration update uses schema 3 and binds GET-displayed text
and revision into PUT. Valid historical v1/v2 declarations require explicit
owner reconfirmation; reads do not rewrite them. The separate host-term v2
notice contains clarification only, so it does not silently gain the new
deletion acknowledgment. UI consent resets on identity changes, and stale
responses cannot announce success. The isolated candidate passes 165 backend
tests, 474 UI tests, TypeScript, production build, and independent review.
This prepares a future owner-facing confirmation; no live account or policy
record was changed.

The hash-named slices are pushed. Their staged bytes matched isolated candidates;
unrelated changes remain unstaged. Private receipts and preimages are under
`.artifacts-temp/astra-recovery-contracts-20260906/` and
`.artifacts-temp/astra-required-signin-20260906/`. The generic visual-carry
helper's pre-existing digest mutation was superseded by the construction and
normalization fixes; its original preimage is retained privately.
Additional private receipts are under `.artifacts-temp/astra-toml-compat-20260906/`,
`.artifacts-temp/astra-llm-artifacts-20260906/`, and
`.artifacts-temp/astra-krea-declaration-20260906/`.

`0a9d6fc` retains thread-local model-release authority and passes
an explicit captured slot to Listener output callbacks. Ordinary repeats keep
that slot; an explicit hold or queue pause yields native exclusion before the
generation lock, and a cancelled resume does not reacquire it. The proposed
process-global ownership state is superseded because it could let an unrelated
thread bypass model-release exclusion. Its private preimage is retained.
The isolated candidate passes 166 LLM/lifecycle tests and all 52 lifecycle wiring
tests, including synthetic cross-thread exclusion and execution of both parking
branches. A per-slot guard fences duplicate release and post-context
reacquisition; the worker-thread round trip and persistence-failure paths are
covered. Independent review, syntax, and publication checks pass. This is CPU
synchronization evidence, not live model residency or GPU acceptance. Receipts are in `.artifacts-temp/astra-native-slot-20260906/`.
H3 segment checkpoint and native-conditioning WIP remain separate and must
be integrated with their own dependencies and acceptance.

The delivery-native slice keeps intermediate media in artifact lineage while
excluding it from public finals until the protected delivery pass. Metadata
refresh preserves its temporary role, and resealing follows the current
project-scoped sidecar format without requiring an obsolete browser-session
owner field. Nested orphan-recovery owner checks remain intact. Executable
callback/metadata tests replace superseded source-string probes; the reseal
fixture uses the real policy stamp and rejects invalid project/job/private/role
state, forged producer units, and changed media. All three new regressions fail
against the prior source. The broader recovery fixture now supplies the captured
slot state used by the already-shipped native-slot fix. All 293 applicable
delivery, wiring, output-privacy, and queue-recovery tests pass, along with
syntax/publication checks and independent review. Private evidence is in
`.artifacts-temp/astra-delivery-native-20260906/`. This remains synthetic CPU
and temporary-filesystem evidence, not live delivery or generation acceptance.

Checkpoint error attribution now distinguishes H3 audio/video decode, audio
mux checkpoints, and rendered-segment sealing. Model-checkpoint loading and
sealed-reference planning remain generic generation phases. Missing predecessor
units or artifact hashes carry an explicit segment-checkpoint stage/code through
sanitization, independent of a stale decode message. This slice does not change
completed-unit validation or callback timing. All 246 applicable wiring,
delivery, and queue-recovery tests pass, as do syntax/publication checks.
Independent review found no issues; private candidate/test evidence is in
`.artifacts-temp/astra-checkpoint-errors-20260906/`.

The checkpoint-ordering slice seals every completed video component before
concat, including single-component/deferred-concat hooks, and binds the callback
to its current segment/group. It removes the ambiguous reversed-file fallback.
Missing producer evidence fails before another task can run. Normal completion
and replay share the CPU handoff helper; replay reconstructs a handoff only when
the descriptor is absent and the next same-group task explicitly requires one.
Present null, malformed, missing-file, hash-changed, or invalid native-boundary
descriptors remain rejected by both journal and orphan-sidecar reconciliation.

Continuation enrichment binds the current unit ID, dependencies, settings,
private staging location, size, and hash. It fsyncs the file and (on Linux) its
directory, then atomically updates only existing producer metadata before the
journal. An update failure does not quarantine the sealed video. Reconciliation
defers quarantine long enough to adopt a valid same-media sidecar update over
an old journal descriptor; replaced media cannot be adopted under that old
identity, and all producer sidecars must agree on the continuation. Private
handoffs are retained rather than treated as consumed-file exceptions.

`H3-RECOVERY-ALTERNATE-SIDECAR` is resolved for the supported Linux path.
Reconciliation protects every accepted media and sidecar name, then retires only
an alternate header matching the old size/hash and job/producer/output identity.
The metadata-only primitive rechecks content and file identity through directory
handles under a process-local guard before moving it into private quarantine.
Changed, malformed, linked, protected, or unsupported-platform metadata remains
in place. Its snapshot/move guarantee relies on the repository's existing
writer-coordination contract. The isolated candidate passes 294 tests, with one
Windows-only test skipped; syntax/publication checks and independent review pass.
Evidence is in `.artifacts-temp/astra-alternate-sidecar-20260906/` and includes
real temporary-filesystem moves plus replacement-at-primitive-entry tests.

The isolated candidate passes all 464 applicable lifecycle, queue recovery,
Studio, native-boundary, audio-safety, delivery, and LLM tests. Its six code/test
files stayed byte-identical throughout the final run. Syntax and publication
checks pass; independent-review blockers were addressed with regression coverage.
Private preimages, isolated candidates, regression output, and closure receipts
are in `.artifacts-temp/astra-segment-checkpoint-20260906/`. Tests include CPU
handoff construction from synthetic frames, old-journal/updated-sidecar recovery,
metadata-write failure preservation, replaced-media rejection, strict malformed
continuation cases, and callback/replay ordering. This does not establish live
GPU, encoded native-AV handoff, generation, or human acceptance. Native
conditioning, offload/quality changes, and the remaining WIP are still open.

The CPU LoRA contract slice now uses one per-checkpoint resolver for admission,
direct/restored workers, asset preparation, task manifests, and estimates.
Missing/null architecture lists inherit shared choices; an explicit empty list
clears that side. Paths and positional weights retain their selected identity;
only compatibility classification normalizes basenames across path separators.
Empty/duplicate assets, incompatible explicit lists, exclusive stacks, invalid
Dasiwa step/strength settings, and surplus weights reject before model work.
Ordinary missing weights retain the existing 1.00 default.

Estimate routing uses the existing adaptive resolver on a private presence-only
request. Segment Turbo validation and profile previews preserve architecture
selection precedence. Single-task aliases resolve before mutation, and manifest
projection runs before parsing and recovery-sidecar snapshots. The existing
Dasiwa runtime artifact/receipt checks remain intact. Superseded clip-only
projection and duplicate validation code have been removed. This slice does not
adopt checkpoint-default, prompt-adaptation, native-boundary, or offload WIP.

The final frozen candidate passes 359 of 368 tests; nine optional runtime tests
are skipped. Its seven code/test files stayed byte-identical through the run.
Python syntax and tracked publication checks pass. Private
preimages, review fixes, CPU logs, and candidate digests are under
`.artifacts-temp/astra-h3-lora-contract-20260906/`. Independent static review's
four findings were resolved and have executable AST/spy regressions. This is
CPU/synthetic evidence, not model, GPU, provider, rendered-UI, or human acceptance.

The stale fixtures exposed by the broader run are corrected: CPU-text and
credit admission fixtures now supply captured native-slot state; residency
assertions inspect `_generate_video_impl` and verify that the retry wrapper
delegates there; UI source assertions track held-job responses, normalized
resolution, and the dynamic inference-step ceiling while retaining its 50-step
cap. All 195 tests in the four affected suites pass on an isolated HEAD-based
candidate. The four test files stayed byte-identical during verification;
production source and unrelated WIP were not changed. Evidence is under
`.artifacts-temp/astra-stale-fixtures-20260906/`. This closes those recorded
fixture failures, not the entire historical backend inventory.

Public failure copy now has a shared reviewed-message publication boundary.
Exact approved contract messages and the bounded numeric frame-limit template
survive live production and restored envelopes. Arbitrary prefixed text, unknown
fallbacks, string subclasses, and malformed values fall back to content-free
stage/planning copy. Stage messages have one immutable owner in
`services/public_failure_copy.py`; the prefix-only rules and duplicate stage
mapping are removed. Exact TypeError preparation failures preserve actionable
parameter/custom-settings/LoRA copy. OOM detection and stage/code classification
are unchanged.

All 401 tests in the affected planning, delivery, lifecycle, recovery, and LLM
suites pass on the isolated candidate. A subsequent contract-comment correction
was proved AST-equivalent outside that docstring and passed all 12 final copy
boundary tests. Independent review findings were resolved, and final candidate
bytes were checked against the staged index. Evidence is under
`.artifacts-temp/astra-public-failure-copy-20260906/`. These checks are
CPU/static/synthetic; no GPU lease or workload was started.

The native-max offload candidate remains open. Its resolution-aware policy must
be carried consistently through requested residency identity, load, wrapper,
implementation, and retry. Verify supported canvas/model boundaries and retain
source evidence separately from any GPU memory or performance claim. Keep UI,
model routing, prompt, native-boundary, and offload WIP separate.

Adaptive estimate requests now carry explicit FL2VA/Ref2VA selections and
independent LoRA lists/weights. Missing or null lists retain the shared-list
fallback; an explicit empty list remains a clear selection. Compatibility
queries for another FL2VA flavor use that queried model rather than a stale
picker. The shared request type owns these fields for estimates and generation.
The server honors explicit FL2VA selection, then preserves the selected flavor,
then falls back to Base; output metadata never selects PinkCherry. Invalid
adaptive IDs fail validation, and remote visibility runs before model/settings
validation so hidden and unknown IDs share the same response. Effective-model
access checks remain in place after routing.

The final isolated candidate passes 136 backend tests and 478 UI tests, Python
syntax, TypeScript, and the production build. Independent review is clean. One
stale submission-source assertion now matches the held-job argument while
preserving its ordering checks. Receipts and exact source hashes are under
`.artifacts-temp/astra-adaptive-request-20260907/`. This is CPU, synthetic
endpoint, and build evidence; no browser/server/GPU acceptance is claimed.

Remaining adaptive UI work includes picker integration, persistence/seeding,
TypeScript basename normalization against the Python contract, and rendered
acceptance. Do not adopt the dirty helpers that make `explicit_output` choose
PinkCherry: that behavior is superseded by the existing metadata-only contract.
The broad picker/helper/default WIP remains uncommitted and separate.

Browser acceptance is still deferred pending permission for task-specific
external cache/result directories on a different filesystem. Existing browser
binaries are available, so no download is needed for the prepared attempt.
Keep the cross-filesystem and synthetic-network gates intact. This is not
authorization to start Maestro or perform live account, LAN, GPU, model, or
provider work. All original task boundaries and the historical tracker hold
remain in force.

Next, inspect the remaining account/support, project-access, H3/native
conditioning, lifecycle, and LLM/runtime WIP against its paired tests. The
historical full-backend failure inventory below is a triage reference, not a
current failure count: the completed bounded suites above supersede their
covered entries. Keep storage-janitor and other untracked source/test pairs
intact. Do not stage mixed files wholesale or claim that the entire backend,
live deployment, or broader Goal is complete.

## Authority and Goal

The owner explicitly requested a new task using GPT-6 Astra at medium to
enable task features, with a native Goal. The predecessor is paused by the
owner and must not be resumed, woken, or treated as a parallel project writer.
This is an authorized successor, not completion of the broader objective.

Create the successor's native Goal first, without an invented token budget:

> Complete all actionable work in ASTRA_CONTINUATION_HANDOFF_2026-09-06.md,
> beginning with current-state verification, recovery test failures, and
> account/project usability; then continue integrating the already planned
> Maestro features, testing and finishing existing WIP, removing superseded
> machinery, and committing/pushing coherent verified milestones. Keep making
> useful authorized progress beyond this handoff until the planned backlog is
> complete or the owner stops or redirects the work. Preserve unrelated work
> and all authorization, privacy, ownership, and evidence boundaries.

This workflow is explicitly continuous. A milestone, empty/unavailable Beads
ready result, or one blocked acceptance lane is not broad completion. Use the
explicit next actions here and in versioned project plans; retain unresolved
gates and keep independent safe work moving. Do not mark the Goal complete
merely because the handoff has been read or the first slice shipped.

Use the saved local Maestro project directly, never a worktree or the former
checkout. Resolve its root from the task environment. Preserve Astra medium.
The phrase "enable features" refers to this new-task setup; it does not
override the existing prohibition on GPU/model execution, generation, lease
requests, Maestro/Pinokio restarts, credits/SSO activation, or new providers.
Those remain deferred until the owner explicitly changes the boundary.

## Read first and inspect current state

1. Read `AGENTS.md`, `CONTRIBUTING.md`, this file,
   `MIGRATED_CHECKOUT_HANDOFF_2026-09-05.md`, `CONTINUATION.md`, and
   `GPU_ACCEPTANCE.md`. Newer user constraints above override historical
   activation/restart instructions in older handoffs.
2. Run the shared read-only activation audit and inventory Git and reservations.
   The prior audit reports Beads 1.2.1 embedded Dolt, while repo policy holds
   the historical tracker. Preserve that hold: no Beads lifecycle, migration,
   init, or sync on inference alone. Use the approved Coordination fallback
   for deferred issue tracking.
3. The source baseline at preparation is `164d834` on `main`. The index is
   empty; the working tree remains heavily dirty. Fresh source and status are
   authoritative. Private preimages/status/digests are retained under
   `.artifacts-temp/astra-handoff-20260906/`; never commit these artifacts.
4. Acquire exact reservations before edits using the installed shared tool.
   Bind both task-id and session-id to the native task ID, not a descriptive
   session label. All predecessor implementation claims were released; its
   handoff-only claim will be released before successor creation.

## Shipped and verified

- `aea3e7d`: account-scoped terminal memory stays private until fresh project
  authorization; handles revoked membership, expired unlock, no-selection,
  account/workspace races, and existing unscoped live tool placeholders.
- `b4e4fbd`: output-privacy test extracts only its 15 asserted routes; all 50
  tests passed in 8.6 seconds without changing assertion semantics.
- `9fae1e6`: polling assertion includes queue readiness; 15 tests pass.
- `164d834`: failed-generation retry projection, endpoint, and UI; unknown
  reasons stay opaque, attempts and safe worker are checked, current owner,
  project.generate, sealed-input, and durable-checkpoint gates remain intact.

Latest slice: 475 working UI tests, 471 isolated candidate UI tests, application
and config TypeScript, isolated Vite build, 21 queue UI contract tests, and
publication guard passed. The staged files matched the candidate byte-for-byte.
No live account, mounted browser, LAN, GPU, generation, or human acceptance was
claimed. See `.artifacts-temp/failed-retry-current/` for exact receipts.

## Next work, in order

1. Resolve the seven remaining queue-recovery test failures/errors in bounded
   slices. The isolated 159-test file had three failures and four errors; all
   seven also reproduce against HEAD without the retry patch. Exact tests:
   `test_all_recovery_preprocessing_audio_uses_private_unit_prefix`,
   `test_recovered_v2_h3_worker_preserves_replan_contract_to_parser`,
   `test_sample_arms_are_excluded_from_generic_public_job_projections`,
   `test_semantic_execution_slices_dispatch_exact_children`,
   `test_enhance_before_generate_admits_then_prepares_without_gpu_slot`,
   `test_native_recovery_uses_private_stable_target_before_promotion`, and
   `test_wgp_completed_repeat_offset_skips_only_outer_dispatch`.
   Distinguish stale source extraction from real behavioral regressions;
   replace obsolete assertions with meaningful executable coverage, not weaker
   expectations. Candidate/baseline logs are in `failed-retry-current`.
2. Finish the existing account/project UI WIP coherently: App bootstrap,
   account/support panels, access projection, project-reference UI, and paired
   tests. Scout the actual diffs before deciding a slice. Restore synthetic
   browser acceptance through compliant cache/result paths; do not weaken the
   E2E runner's cross-filesystem requirement. Its default cache now shares the
   migrated checkout filesystem and is rejected. No real server start is
   authorized as a shortcut.
3. Reconcile the remaining CPU backend failures and their matching WIP source:
   Director H3/schema/semantic execution, native conditioning, lifecycle,
   LTX/runtime/launcher assumptions, project references, and storage janitor.
   Preserve all unpublished source and restored tests, including untracked
   `h3_lora_compat.py`, `h3_prompt_adapt.py`, `storage_janitor.py`, and their
   tests. Do not bulk-stage or publish tests against uncommitted dependencies.
4. Continue existing planned feature integration from `CONTINUATION.md`,
   project plans, and the approved intake decisions. Prefer user-visible,
   complete CPU-safe milestones; research intake is not a build list. Keep
   deferred GPU/activation/provider work out of the critical path. Obtain new
   authority only where it is actually required, with concrete prepared work.

The earlier complete CPU backend run was not green: 4,583 tests, 35 failures,
48 errors, 14 skips. Its private `terminal-recovery-current/backend.log` is
about 8.7 MB and contains enormous assertion lines: inspect sizes, then stream
projected failure headers/short excerpts, never broad regex output. One import
error is Python 3.10 missing tomllib. Reuse unchanged evidence; run focused
checks during development and full applicable checks for each final slice.
Do not repeatedly rerun the entire long suite just to rediscover the baseline.

## Shared tooling and closure

Universal Harness task `01a007ff-393c-71d1-8156-9992c3679753` repaired the
reservation scanner. Installed Linux helper SHA-256 at acceptance:
`421770b03125295ce9f5c1bd8ae48cc6e92e44f9196d5939b4df64c29c6bf4cd`.
Recheck current tooling rather than restoring older bytes. Supported full
read-only status uses `--include-pruned --max-directories 65536
--max-entries 1048576 --max-seconds 60`; reconciliation accepts the same budget
flags without include-pruned. Exact app-root scanning returned complete,
zero skipped scopes, followed by successful reconcile/acquire/release. Do not
manually edit registries, bypass unknown links, or create local scanner shims.
Windows acceptance is the shared owner's separate gate.

Use minimal useful parallel scouting/review, one writer per file, and serial
Git operations. Preserve the dirty tree; no reset, clean, implicit stash,
worktree, or broad staging. Verify isolated staged bytes when mixed files carry
unrelated hunks. Commit/push each coherent slice and report evidence honestly.
Rebase pulls are blocked by unrelated dirt; previously a fresh fetch proved no
incoming commits, permitting ordinary fast-forward pushes without disturbing
that dirt. If incoming changes now exist, reconcile safely rather than force.

The predecessor performs no further feature work after writing this handoff.
The successor owns the new Goal and continuation, not the paused predecessor's
native state. Retain the unfinished broad objective until genuinely satisfied.
