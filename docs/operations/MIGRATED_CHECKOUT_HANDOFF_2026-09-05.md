# Migrated-checkout continuation — 2026-09-05

## Read first

Continue in the saved Maestro Project selected by the owner for the migrated
Pinokio installation. Resolve the repository root from that task's actual
working directory; do not reuse a checkout path from older handoffs. The former
checkout is preserved evidence, not the next writer's workspace. The owner
explicitly authorized reconciling the two checkouts and deleting the stale
copied `.working` sentinels in the migrated checkout.

The immediate objective is reliable, account-scoped failed/cancelled job-card
recovery across reloads, followed by the existing CPU-safe project priorities.
**GPU use and server restarts remain unauthorized.** Do not start Maestro,
load a model, request a lease, run a generation, or restart Pinokio. A separate
task terminated the blocking Maestro process with explicit owner permission;
that incident did not grant this task permission to restart it.

This is a deliberate handoff with an unfinished staged implementation, not a
release or a claim that the larger objective is complete. The owner requested a
new task in the correct Project. The predecessor will relinquish its claims
before dispatching the successor and perform no further project edits.

## Verified repository checkpoint

- `main` was fast-forwarded from `563b0ba` to
  `7495187b4c1990b1a4fc85fb77e8a8b3b4027295`, matching origin before this
  handoff-only commit. No reset, clean, stash, or worktree was used.
- The six incoming commits include the Astra policy, systemd startup notes,
  video-cache identity fix, and denied-browser-storage fix. Local utility/cache
  and continuation-document changes were compared byte-for-byte with incoming
  commits before staging them for the fast-forward; no local content was lost.
- Origin is the owner's Maestro repository; upstream remains the original
  Maestro repository. Preserve both. Recheck live Git state before writing.
- Many existing app/H3/queue/UI changes remain unstaged. Do not stage the whole
  checkout or the entire `useStore.ts` file. The latter intentionally has both
  staged recovery changes and unstaged H3 changes.
- Twenty-one test files were reconciled from the former checkout. All nineteen
  tracked three-way merges were conflict-free. The NVFP4 test gained additive
  native-LoRA coverage, and `test_h3_lora_compat.py` was restored create-only.
  The migrated studio-prompt test's stronger two assertions were retained.
- Ten stale copied `.working` files were backed up and removed under the
  owner's explicit instruction. Originals in the former checkout were not
  removed. Backups and a digest receipt are under the migrated checkout's
  `.working.recovery/migration-copies-20260905T181405Z/`.
- The activation audit reports Beads 1.2.1 / embedded Dolt metadata. Local
  policy still explicitly holds the historical tracker. This discrepancy was
  not treated as authorization to migrate or mutate Beads. Follow the hold
  until its owner reconciles policy and tracker acceptance.

The private local checkpoint directory is
`.artifacts-temp/handoff-migration-20260905/`. It contains staged/unstaged binary
patches, status, pre-merge source inventory, test-merge preimages and manifest,
the UI test log, and hashes. These are evidence and rollback material; keep them
untracked. The staged recovery patch SHA-256 at handoff is
`504705e492dbcb058509d961543f50ba7f1db8c58f1f1f19c6d59a742a8a054a`.
Use fresh diffs as authority if another writer has changed the checkout.

## Staged recovery implementation — needs correction before commit

Exactly three paths are staged for this implementation:

- `ui/src/lib/terminalJobMemory.ts`
- `ui/src/stores/useStore.ts` (only the recovery-related hunks)
- `ui/tests/terminal-job-memory.test.mjs`

The implementation uses a single session-storage v2 envelope `{scope, jobs}`.
Scope is the server-projected account ID or explicit accounts-disabled local
mode. Unknown/signed-out identity does not restore data, and legacy unscoped v1
records are not adopted. Store initialization starts with no jobs; first account
hydration restores matching memory after the account scrub. Logout/account
changes clear memory. The denied `sessionStorage` getter is caught. Prompts and
active-window text are excluded from the compact card. Fresh server job status
wins over a remembered terminal status. The obsolete `mergeTerminalJobs` helper
was removed; there are no remaining callers.

This passes the current tests but **independent review found two real missing
boundaries**:

1. **Current project authorization is not checked on restoration.**
   `_terminalJobsForAccount` restores before authoritative workspace hydration.
   `loadWorkspaces` currently removes only certain previously active/relocked
   project rows, not every recovered row whose project is now absent, locked,
   or lacks `project.read`. MainContent can render retained project names and
   failure details. An unchanged account ID is not sufficient authority after
   project membership or legacy unlock expiry.
2. **Authenticated remote users with no selected project can lose recovery.**
   Committed MainContent clears `jobs` when `queuePollingReady` is false. The
   no-selected-project shell is supported; clearing jobs also removes persisted
   memory through the new subscriber. A pre-existing unstaged MainContent hunk
   preserves terminal rows, but must not be committed indiscriminately because
   it can amplify finding 1. Another MainContent hunk adds a retry button and
   belongs to the broader unfinished recovery work.

The tests bundle the real store but stub `loadWorkspaces`, so green tests do not
cover either boundary. No corrective code for these two findings has been
written yet.

### Best next implementation step

Hold remembered cards privately until a fresh server workspace list establishes
read access. Restore only authorized project rows; erase rejected rows from
memory. Retain account-identity/request-generation fences and local/LAN parity.
Reuse `isAccountProjectAccessActive`, workspace `project_permissions`, and the
legacy unlock contract rather than inventing another authorization policy.
Keep recovery data through the no-selection state without displaying unverified
cards. This is a proposed approach, not a decided implementation.

Add real lifecycle regressions for same-account membership revocation, an
accounts-disabled remote project's unlock expiry, no-project-selected recovery,
and identity changes while workspace requests are in flight. Keep the existing
same-account reload/logout/scope tests. Then re-review the corrected boundary
and commit only the intended integration.

## Verification and limits

Canonical migrated checkout evidence:

- `cd ui && npm test`: **469 passed, zero failed** after test reconciliation.
  Before restoring missing test edits, three model-terms/reference-sheet tests
  failed against already-migrated source; the merged tests resolved those
  mismatches. Full log is in the private checkpoint.
- `ui/node_modules/.bin/tsc -p ui/tsconfig.app.json --noEmit --incremental false`:
  passed on the migrated checkout.
- `app/env/bin/python scripts/verify_clean_repo.py`: passed for 2445 staged /
  tracked paths before this handoff document.
- `git diff --check` and staged whitespace checking passed.
- The same isolated staged UI patch passed 465 tests in a temporary snapshot
  before migration, but that is supplemental evidence, not new-checkout browser
  acceptance.
- The earlier denied-storage fix had a successful isolated production build.
  There is no new canonical production-build or browser acceptance claim for
  the staged account recovery. Do not overwrite the installed UI dist merely
  to build a test artifact.
- The restored Python/H3/NVFP4 tests have **not** been rerun here. Review their
  execution paths and use CPU-only checks; imports or static tests are not GPU
  generation acceptance.
- No GPU, live-account, LAN, Cloudflare, human, or restarted-service acceptance
  was performed by this task.

## Resumption checklist

1. Read this handoff, `AGENTS.md`, `CONTRIBUTING.md`, and the existing
   `CONTINUATION.md` / `GPU_ACCEPTANCE.md`. Resolve the selected Project's root
   and run the shared read-only activation audit before tracker work.
2. Inspect `git status --short --branch`, `git diff --cached`, and the unstaged
   diffs. Verify reservations afresh; acquire scoped claims using the shared
   tool. Do not recreate migrated legacy claims or copy host-bound metadata.
3. Fix the two reviewed recovery boundaries and tests above. Separate the new
   patch from pre-existing H3 hunks when staging. An isolated index snapshot is
   useful for checking that the commit works without unrelated dirty changes.
4. Finish CPU verification, then commit/push the complete recovery slice.
   Keep the restored test changes paired with the implementation they exercise;
   do not publish tests that depend on uncommitted source as an isolated release.
5. Continue existing CPU-safe accounts/project usability and recovery work.
   Credits, SSO, rendering/model experiments, and GPU-dependent acceptance must
   not displace the current usable milestone. Keep the broader goal open.

Next-milestone completion requires secure same-account recovery with current
project authorization, no-selection retention, logout/switch isolation, focused
independent review, applicable full CPU checks, and a verified intended commit
on origin. Test success alone does not satisfy the missing authorization/UI
boundaries, and handing off does not complete the predecessor's broad goal.
