# Astra continuation handoff — 2026-09-06

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

The review's non-blocking cleanup remainder is `H3-RECOVERY-ALTERNATE-SIDECAR`:
this Goal owns a future exact-sidecar cleanup check. Reconciliation conservatively
retains an unmatched alternate sidecar when its media basename was adopted.
Before cleanup, prove that the alternate metadata is obsolete and is not another
accepted artifact's sidecar; never quarantine the adopted media to remove it.

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
