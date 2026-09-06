# Astra continuation handoff — 2026-09-06

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
