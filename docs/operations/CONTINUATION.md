# Maestro Continuation Guide

This is the durable starting point for a fresh development session. It records
contracts and procedures, not a claim that any particular deployment is live.

## Start at the repository root

Resolve the checkout instead of relying on a machine-specific path:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
test -f "$REPO_ROOT/AGENTS.md" && test -d "$REPO_ROOT/.beads" || exit 1
cd "$REPO_ROOT"
```

Then read `AGENTS.md`, `docs/operations/FRESH_THREAD_HANDOFF.md`, this guide,
and `CONTRIBUTING.md`. Before any Beads lifecycle action, perform the shared
read-only activation audit: record the exact root, composed policy hashes,
installed `bd` version, static tracker/redirect metadata, workspace status, and
reservation state. Inventory the shared workspace without invoking Beads:

```bash
bd --version
git status --short --branch
find .beads -maxdepth 2 -type f -printf '%s %P\n' | sort
```

Inspect `.working` without rewriting it. Use the shared `working_sentinel.py`
tool for new structured claims; a legacy or malformed sentinel fails closed
until explicit owner/recovery review. Preserve every pre-existing dirty path,
reserve one writer per file or symbol cluster, and stage only owned files.

This checkout currently contains a preserved historical SQLite tracker. Do not
run `bd where`, `bd init`, migration, sync, hooks, or any other Beads lifecycle
mutation. Record/queue the condition, preserve the canonical repo-root tracker,
and continue independent work. Normal Beads commands may resume only after a
separately controlled tracker migration is verified.

## Accounts and existing projects

Accounts are an optional host feature. They may be enabled in an operator's
ignored configuration, but no tracked document should claim their current live
state. Project passwords and browser project sessions remain separate.

The first-owner procedure is:

1. Set `MAESTRO_ACCOUNTS_ENABLED=true` and
   `MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=true` in Pinokio's per-app **Configure**
   tab, then use **Restart Maestro**.
2. Open the direct loopback Web UI on the computer running Maestro. In
   **Support** > **Account**, create the first owner and save the one-time
   recovery codes offline. Owner setup is never offered over LAN or Cloudflare.
3. Set `MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=false` and restart again. Keep
   `MAESTRO_ACCOUNTS_ENABLED=true` so accounts and sign-in remain available.

Never invent an owner password, store recovery codes in the repository, or put
account-store or signing-secret values in documentation, commands, or logs.

Enabling accounts alone does not hide or reassign existing projects. Until the
owner explicitly completes a zero-quarantine migration, account-based project
filtering remains off and the legacy browser/project-password view continues to
show existing projects.

After owner creation, use the direct loopback UI to sign in and confirm the
owner password. In **Support** > **Account**, choose **Connect existing projects
to this owner**. The corresponding owner-only, recent-reauth, loopback-only API
is:

- `GET /api/v1/account/projects/migration` — inspect current migration state.
- `POST /api/v1/account/projects/migration` — explicitly census and bind the
  existing projects to the owner.

Migration states are:

| State | Meaning |
| --- | --- |
| `disabled` | Accounts are off; account project filtering is off. |
| `not_started` | No migration was committed; existing projects remain visible. |
| `needs_attention` | At least one project could not be safely bound; filtering remains off. |
| `active` | The complete zero-quarantine inventory is bound and account project access is enforced. |

Do not bypass `needs_attention`, silently orphan a folder, or treat a partial
census as success. Repair an invalid project or resolve an explicitly approved,
recoverable removal, then rerun the normal flow.

`GET /api/v1/account/context` is the server-authored source for account
activation state. Relevant values are `disabled`, `setup_available`,
`setup_requires_loopback`, `disable_bootstrap`, and `ready`; do not infer them
from client state.

## Preserve a future SSO migration path

Current account work must keep Maestro's opaque internal `account_id` as the
permanent identity for project membership, roles, credit lineage, contribution
history, and audit provenance. A future SSO provider must authenticate an
existing Maestro account; it must not replace that ID or trigger another
project migration.

Queue optional OpenID Connect support as a later design and implementation
wave, after the local owner/project flow is live. Authentik is a promising
self-hosted candidate and can broker providers such as Google.
It is not a selected dependency or release commitment. Implement
provider-neutral OIDC rather than provider-specific sign-in code.

The future contract is:

- Add a versioned, sealed external-identity mapping from the canonical pair
  `(issuer, subject)` to exactly one existing internal `account_id`.
- Never create, merge, or link accounts solely by email, username, provider
  groups, or display claims. Email remains optional profile data, not identity
  authority.
- Require an authenticated, recently reauthenticated local account for initial
  linking. Global roles/capabilities continue to come from Maestro's sealed
  local account store and project permissions from the sealed membership store,
  never from provider claims.
- Issue the normal Maestro browser session after successful OIDC authentication
  so project permissions and credit ownership keep using the same account ID.
- Preserve the local password and recovery codes as a break-glass path until
  unlink, provider-outage, sole-owner, backup/restore, and recovery behavior are
  proven safe. Linking or unlinking must not strand the only owner.
- Do not initially treat an OIDC login as recent privileged reauthentication.
  The later design must record enough authentication-source provenance to
  revoke affected sessions after unlink/deprovision, or conservatively revoke
  every session for that account.
- Keep authentication links separate from existing contribution/support
  provider links and their opaque keys. Contribution events never grant login
  authority or create an OIDC identity mapping.
- Keep project-password/browser grants, queue/recovery ownership, and account
  sessions as distinct compatibility layers unless a separate migration proves
  otherwise.
- Use OIDC state, nonce, PKCE, exact redirect origins, and conservative proxy
  handling. Treat issuer and subject as exact validated strings without local
  case-folding or normalization. Verify signed ID tokens against fail-closed
  discovery/JWKS and a fixed algorithm policy, with exact issuer, audience,
  authorized-party, nonce, and lifetime checks. Verify direct, LAN, and
  Cloudflare behavior separately; first-owner bootstrap and project cutover
  remain direct-loopback-only.
- Preserve immutable project migration records and existing project bindings.
  Provider changes must not re-key projects, credits, jobs, or historical data.

Before implementation, re-scout the then-current account-store schema and add
regressions for duplicate issuer/subject links, same subject under different
issuers, email collisions, link races and rollback, disabled/deprovisioned
accounts, provider outage, safe unlink, session revocation, origin-specific
callbacks, forged signatures/algorithm confusion, wrong or unavailable token
authority, issuer/subject normalization collisions, contribution-link
non-authority, unchanged project/credit IDs, and local recovery. Keep this
deferred wave out of the current bird-in-the-hand account activation milestone.

## Credits remain a separate activation gate

Runtime credit accounting is currently compiled hard-off. Do not describe an
environment flag, support contribution, or account activation as credit
enforcement. Credit work is deferred until the account/project milestone is
live and verified.

Before credits can be enabled, a separate release must prove both sides of the
contract:

- A freshly resolved owner role from the sealed account store bypasses new-job
  reservation, allowance consumption, journaling, and credit denial. Client
  fields and persisted job parameters are not authority. Historical owner holds
  still need their normal release path.
- Otherwise-valid zero/partial/refunded/expired-credit hosted work must still
  create a durable job. It receives the lowest ordinary FIFO queue band with a
  starvation-bound capacity path; it is not flatly rejected or made a paid-only
  feature. Fully funded work may receive bounded priority. Local/authenticated
  LAN and owner work remain exempt, and exact capability exclusions remain
  baseline.
- Under demand, later entitlement work may impose model-valid duration shaping
  (the recovered intent was approximately 15/10/5-second bands), but that is a
  separate server-authored contract, not permission to deny submission.

Keep credit activation off until the implementation, regression matrix, and
live acceptance for that contract are complete.

## Coordinated restart and status

Use Pinokio's **Restart Maestro** launcher action. It invokes `restart.js`,
publishes a bounded public restart notice when stable sharing is configured,
and restarts `start.js`. Do not assume that launching an already-ready app with
a default selector has performed a restart; confirm an actual lifecycle change.

For a manually coordinated maintenance window, use one untracked generation
value and the existing helper:

```bash
RESTART_GENERATION="$(python -c 'from app.scripts.restart_status import new_generation; print(new_generation())')"
python app/scripts/restart_status.py set --state planned --reason restart \
  --message "Planned maintenance" --generation "$RESTART_GENERATION"
python app/scripts/restart_status.py show
```

After the intended surfaces are healthy, clear only that exact generation and
show the result:

```bash
python app/scripts/restart_status.py clear --generation "$RESTART_GENERATION"
python app/scripts/restart_status.py show
unset RESTART_GENERATION
```

Never clear on process visibility alone. A `NOT_CLEARED` result is a safety
stop, not permission to try a different generation.

Maestro's port is dynamic. Resolve the app with `pterm search`, keep its returned
canonical reference, and use:

```bash
pterm status "$MAESTRO_REF" --probe --timeout=5000
curl -fsS "${MAESTRO_URL%/}/health"
```

Set `MAESTRO_URL` from the current `ready_url` or the specific external surface
being tested; never copy an old port from a handoff.

## Verification matrix

Verify only configured surfaces and label each evidence level accurately.

| Surface | Required checks |
| --- | --- |
| Direct local | `pterm status --probe`, then `/health`, `/api/v1/account/context`, and `/api/v1/workspaces` against the current `ready_url`. |
| LAN | Use the currently advertised LAN URL; repeat the relevant health, account-context, and workspace checks from a LAN client. |
| Stable/Cloudflare | Use the currently advertised stable URL, confirm `/health`, then exercise account context and workspaces with a cookie-aware browser/client. Keep the direct Quick Tunnel as a separately tested fallback when claimed. |

For account activation, also confirm that direct loopback offers the expected
setup state while LAN and Cloudflare never offer first-owner setup. Before
migration, confirm the existing project inventory remains visible. After an
`active` migration, confirm the owner sees the same intended inventory through
account authorization.

Do not record project names, credentials, cookies, private URLs, ports, process
IDs, or secrets in the handoff. Static checks and tests are not live acceptance;
automated browser evidence is not human acceptance.

Finish with the quality and serial Git closure procedure in `CONTRIBUTING.md`.
Do not run Beads sync while the historical tracker is preserved. Remove only a
structured `.working` claim you own; never clear the current legacy sentinel
without explicit recovery review.
