# Maestro Fresh-Thread Handoff

Use this file to start a new Maestro development thread. It is a source and
operations checkpoint, not evidence that the post-reboot runtime is live.

## Checkpoint

- Prior thread reference: `019fd895-21e8-7f03-86ea-a1296103337e`.
- Verified source checkpoint before this handoff: `main` at `b528ab7fd467be21c0567f6a619ef1d33208df2b`, matching `origin/main` when the
  handoff was prepared.
- The repository already has an `origin` owned by `HaileyStorm` and an
  `upstream` pointing to the original Maestro repository. Verify both before
  Git mutation; do not create, replace, or reinterpret a remote from a hook's
  generic message.
- No post-reboot runtime, port, account, project, LAN, or Cloudflare acceptance
  has been performed. Resolve and verify all live state afresh.
- Preserve the existing legacy `.working` sentinel, `.artifacts-temp/`,
  `app/services/storage_janitor.py`, and `tests/test_storage_janitor.py`. They
  are not part of the handoff and must not be overwritten, removed, staged, or
  claimed without explicit ownership recovery.
- The legacy sentinel covers the same accounts/projects scope as the next
  milestone. This handoff does not clear or transfer it. The fresh thread must
  stop after its audit until the human owner explicitly transfers/releases that
  scope; only then may account/project work begin.
- The repo-root tracker is preserved historical SQLite. Run only the read-only
  activation audit described in `AGENTS.md`; do not invoke Beads lifecycle,
  migration, sync, or hooks.

## Bird-in-the-hand next milestone

Make the pushed accounts/project flow usable before expanding dormant credit or
security policy:

1. Re-read `AGENTS.md`, this handoff, `docs/operations/CONTINUATION.md`, and
   `CONTRIBUTING.md`; perform the read-only activation audit and inventory all
   dirty/reserved paths. Obtain an explicit transfer/release of the legacy
   `.working` account/project scope before continuing this list.
2. Verify the source/launcher state, then start Maestro through its existing
   Pinokio flow and dynamically discover the current URL/port. Do not reuse old
   runtime evidence.
3. Confirm the pre-migration project inventory and account activation state.
   Enabling accounts alone must not hide projects.
4. Enable first-owner bootstrap only for the setup window. The user personally
   chooses the owner name/password and saves the one-time recovery codes; this
   is intentionally a human step, not missing agent work.
5. Disable bootstrap, restart, sign in, and recently reauthenticate. Record the
   exact pre-migration workspace census, then use the guarded migration action
   to connect every valid existing project to that owner. The action performs
   its own artifact-free inventory check and refuses any quarantine before
   publication. Never silently orphan, hide, rename, or delete a project.
6. Verify the same intended project inventory on each configured surface. Keep
   account login, project-password sessions, and transport authorization as
   distinct layers.

Credits are not part of this milestone. Runtime accounting remains hard-off.
The recovered later intent is soft prioritization, not pay-to-generate: every
otherwise-valid zero-credit job remains durable and eligible at the bottom FIFO
band with starvation-bound capacity; funded work may receive bounded priority;
owner and local/authenticated-LAN work are exempt. Do not reintroduce flat 402
credit rejection. Duration shaping and other entitlements require a separate,
explicit server-authored design after accounts/projects are live.

Future optional SSO is also deferred, but current account work must preserve its
migration path. Keep Maestro's opaque internal `account_id` as the permanent
owner of projects, roles, credits, and history. Later provider-neutral OIDC may
link a canonical `(issuer, subject)` to that existing account; it must not
auto-link by email or rewrite project/account IDs. Authentik is a promising
self-hosted candidate, including for brokering Google sign-in.
It is not a hard dependency or final product choice. Preserve local password and recovery
access as break-glass until the future link/unlink and provider-outage contract
is implemented and proven. The full deferred contract and test matrix live in
`docs/operations/CONTINUATION.md` under **Preserve a future SSO migration path**.

## Parallelization contract

- For any multi-file or cross-layer change, use three bounded read-only scouts:
  ownership, invariants, and tests. Main thread synthesizes their evidence.
- Stop scouting when findings converge. Use one writer per file or symbol
  cluster, then an independent reviewer and verifier on frozen bytes.
- Keep credentials, recovery codes, restarts, migration publication, and Git
  index/ref operations serial. Do not delegate user-secret handling.
- Prefer the smallest demonstrably usable result. Do not turn dormant policy,
  speculative security hardening, or exhaustive cleanup into the critical path.
- Preserve unrelated dirty files and the legacy sentinel. No reset, checkout,
  clean, implicit stash, Beads repair, or tracker migration.

## Concise prompt references

### Primary fresh-thread prompt

> Work from the current Maestro Git root. Read `AGENTS.md`,
> `docs/operations/FRESH_THREAD_HANDOFF.md`,
> `docs/operations/CONTINUATION.md`, and `CONTRIBUTING.md`. Perform the required
> read-only activation audit; preserve the historical SQLite tracker, legacy
> `.working`, and all dirty paths. The existing legacy reservation overlaps the
> next milestone: stop after the audit and ask the human owner to explicitly
> transfer/release its account/project scope. Do not resume credit work. After
> that transfer, use bird-in-the-hand priority to get accounts and the complete
> existing-project inventory live, with the user performing owner
> credential/recovery-code steps. Use three focused scouts only where the
> multi-file trigger applies, one writer per cluster, then independent review
> and verification. Reference prior thread
> `019fd895-21e8-7f03-86ea-a1296103337e` only as provenance; verify current state.

### Post-owner prompt

> The owner setup is complete. Verify bootstrap is disabled, restart once,
> sign in and reauthenticate on direct loopback, record the exact current
> workspace census, and invoke the guarded migration action. It must refuse any
> quarantine before publication; proceed only with zero quarantine and no
> orphaned/hidden projects. Then verify the intended inventory on every
> configured access surface. Keep credits hard-off.

### Deferred credit prompt

> Accounts and project migration are live and accepted. Recover the soft-credit
> contract from this handoff before planning: zero credit never rejects an
> otherwise-valid job; bottom-FIFO work must have starvation-bound capacity;
> funded work gets bounded priority; owner and local/authenticated-LAN work are
> exempt. Re-scout current code and tests before editing and keep activation off
> until the full contract is verified.

### Deferred SSO prompt

> Accounts and project migration are live and accepted. Read **Preserve a future
> SSO migration path** in `docs/operations/CONTINUATION.md`, then re-scout the
> current account schema before planning optional provider-neutral OIDC.
> Authentik is a candidate, not a commitment. Link canonical issuer+subject to
> the existing Maestro account ID; never auto-link by email or re-key projects,
> credits, jobs, or history. Retain and test local break-glass recovery.

## Completion evidence for the fresh thread

Report source/static, mocked, local-runtime, live-access, and human-acceptance
evidence separately. Dynamically resolve the current service URL and do not put
credentials, recovery codes, cookies, private URLs, ports, process identifiers,
or machine-specific paths in tracked files or handoff comments.
