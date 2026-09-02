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
ignored configuration, but no tracked document should claim a particular
deployment's live state. Before project migration becomes active, project
passwords and browser project sessions remain the bounded legacy access path.

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

Once the migration state is `active`, sealed account membership is the only
project authorization path. The active owner/member experience must not ask for,
set, unlock, relock, or depend on a project password. Keep the legacy password
manager reachable only while accounts are disabled or migration is incomplete,
so an operator can roll back without exposing or stranding pre-migration data.

`GET /api/v1/account/context` is the server-authored source for account
activation state. Relevant values are `disabled`, `setup_available`,
`setup_requires_loopback`, `disable_bootstrap`, and `ready`; do not infer them
from client state.

### Owner-authorized CPU-safe activation checkpoint

On 2026-08-24 the owner explicitly authorized making accounts, existing-project
membership, implemented supporter-benefit projections, sharing, and hosted
credit scheduling live to the fullest extent that does not require a GPU. This
authorization supersedes the earlier hold on those deployment features. It does
not turn static or test evidence into live acceptance, permit an agent to choose
owner credentials, or authorize a generation while the GPU is unavailable.

Use this serial activation sequence:

1. Read the live account context and exact pre-restart project census. If an
   owner already exists, keep `MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED=false`; do not
   reopen bootstrap or ask the owner to repeat setup.
2. In ignored operator configuration, keep `MAESTRO_ACCOUNTS_ENABLED=true`, set
   `MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED=true`, and set
   `MAESTRO_COMPUTE_EXECUTION_REALM=hosted`. Configure only the intended public
   support destinations. These flags activate the runtime policy; they are not
   proof that the restarted process received it.
3. Use the coordinated restart-status protocol in this guide, restart Maestro
   through the existing Pinokio path, and dynamically resolve the new ready URL.
4. Confirm `GET /api/v1/account/context` reports `ready` and
   `GET /api/v1/account/projects/migration` reports `active`, with the complete
   pre-restart project census still present. Stop rather than publishing a
   partial or quarantined migration.
5. Confirm `GET /api/v1/support/catalog` and the signed-in
   `GET /api/v1/support/self` projection show the configured support choices,
   supporter recognition, bounded queue-priority state, and promotional Maestro
   credit allowance. A direct-compute record must not change recovery, tiers,
   benefits, queue priority, or allowance. A public Vast.ai destination must
   remain locked until eligible non-Vast support reaches the recovery target.
6. Read the existing H3 legal-access projection. Japan (`JP`) is the owner's
   current declaration; if the live signed record matches the current document,
   do not ask for another country attestation. This step does not run H3.
7. Exercise sign-in, project membership, private output/project sharing, and the
   account/support presentation on each configured direct, LAN, and Cloudflare
   surface. Keep first-owner setup loopback-only.
8. Verify the hosted scheduler and durable accounting journal initialize
   cleanly. Model-free tests may prove the three-band ordering and restart
   contract; real queue behavior under GPU demand remains the separate
   acceptance in [GPU_ACCEPTANCE.md](GPU_ACCEPTANCE.md).
9. Clear only the matching restart-status generation after local health and
   every claimed access surface pass. Record live browser and human acceptance
   separately.

Evidence labels are not interchangeable:

| Label | What it proves |
| --- | --- |
| Source/static | The reviewed configuration and code contain the intended contract. |
| Synthetic/model-free | Tests exercise the contract without model load, generation, or live deployment. |
| Local runtime | The restarted local process reports health, readiness, account state, support state, and hosted-credit policy from its actual configuration. |
| Live access surface | An authenticated client completes the intended flow on the specific direct, LAN, or Cloudflare surface named in the record. |
| Human acceptance | The owner personally confirms sign-in/recovery handling and the visible account, supporter recognition, queue-priority, credit, sharing, and generated-output experience. |

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
- Keep legacy project-password/browser grants isolated from queue/recovery
  ownership and account sessions. They remain a pre-cutover rollback layer, not
  an additional authorization requirement after account migration is active.
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

## Optional hosted credit scheduling

Maestro has an operator-controlled hosted-credit scheduler. It activates only
when accounts are enabled, `MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED=true`, and
`MAESTRO_COMPUTE_EXECUTION_REALM=hosted`. The safe tracked defaults are `false`
and `local`; account activation or a recorded contribution alone never changes
the runtime policy. The owner-authorized activation above deliberately changes
both ignored runtime values for this deployment. Local and authenticated-LAN
execution remain unmetered.

The activation contract is:

- A freshly resolved owner role from the sealed account store bypasses new-job
  reservation, allowance consumption, and journaling. Client
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

Eligible support contribution records may produce promotional Maestro credits
only through the server-owned allowance policy. These credits are a bounded
hosted queue allowance, never purchased compute, cash value, or guaranteed
service. Fully funded hosted work receives bounded priority, while zero,
partial, refunded, or expired allowance never causes a flat `402` or rejects an
otherwise-valid submission. The durable credit journal must conserve units
across reserve, consume, release, restart, and recovery.

The shipped default benefit policy implements only supporter recognition and
bounded queue priority, plus the separately projected promotional Maestro
credit allowance. Compatibility parsing may retain the historical/custom
benefit IDs `early_access_updates` and `supporter_convenience`, but the default
policy does not grant or advertise them. Treat either ID as inactive unless a
separate implementation, projection, UI, and acceptance wave explicitly makes
it real.

Owner-attested Vast.ai direct-compute records are accepted before or after the
$1,000 recovery goal solely to keep the ledger truthful. They are excluded from
development-cost recovery, supporter tiers and implemented benefits, queue
priority, and promotional Maestro credits. Refund and chargeback entries are
manual adjustments to an existing owner-attested Vast.ai record. Maestro has no
Vast.ai payment detector and performs no automatic refund; the public Vast.ai
destination remains locked until the recovery goal is met by eligible other
sources.

For a release, verify the configured realm, all three queue bands, the
cross-band starvation bound, owner exemption from the sealed account store,
local/LAN exemptions, and restart/recovery behavior. Duration shaping and SSO
remain separate later waves.

GPU-dependent acceptance for H3, Music3, Scene Kit/Director, Krea, attention
runtimes, FlashVSR delivery, and real queue demand is tracked in
[GPU_ACCEPTANCE.md](GPU_ACCEPTANCE.md). Do not relist already-passing CPU-safe
tests there as blocked work.

## GPU-idle comparative sample campaign

Historical issue `Maestro.git-134` is the campaign queue and
`Maestro.git-28` is its GPU-yield dependency. Preserve those IDs through the
controlled Beads importer repair; do not mutate the historical SQLite tracker
or duplicate the issues while the 160-record round-trip is not lossless.

This campaign exists to demonstrate Maestro-specific improvements, not to fill
a gallery with unrelated attractive generations. Every high-priority candidate
needs a credible control: use the same normalized prompt/input, model revision,
steps, resolution, frame rate, seed, and output index where technically
possible. The `maestro` arm enables the named recipe, reference, planning,
continuity, recovery, or creator workflow; the `control` arm uses the direct
path without that intervention. Record exactly which intervention differs.

The authoritative priority, intervention matrix, historical alias map, and
release states live in `docs/operations/SAMPLE_CAMPAIGN_QUEUE.md`. Wave 1 is
**Reference Lock**, **One Idea, Four Roles**, **Recovery Is a Feature**, then
**Pocket to Picture Lock**. Continuity and correction proofs follow in Wave 2;
90-second and 3–12-minute stories are assembled only after their shorter
component evidence is accepted. Recovery tests use safe durable boundaries and
must not manufacture an unsafe failure. Real external contention or urgent
owner/agent work may still cancel a running sample through Maestro, accept the
in-flight loss, and requeue it as specified below.

Queue each arm as an ordinary durable generation job with
`queue_class=background_sample`, at the lowest manual priority, and hold it
until the GPU-idle gate passes. Release one arm at a time.
The owner's active work, agent-required verification, and meaningful external
GPU work take precedence. Require five qualifying snapshots over at least 8
seconds, with positive gaps no greater than 3 seconds. The significant-work
rule is telemetry-only and never uses process names: foreign compute blocks on
fresh process activity above the 1 percent incidental floor or aggregate memory
at least `min(1 GiB, 10 percent of total GPU memory)`. Graphics-only contexts
may pass only at device utilization no greater than 25 percent and aggregate
foreign graphics memory no greater than
`min(4 GiB, 15 percent of total GPU memory)`. WDDM unknown graphics bytes need
valid total-used residual evidence. Compute/graphics overlap counts as compute;
missing or ambiguous process APIs, compute activity or bytes, utilization PIDs,
timestamps, totals, or residuals fail closed and reset the window. The exact
classification and public PID/name-redaction contract is canonical in
`docs/operations/SAMPLE_CAMPAIGN_QUEUE.md`. Every deferral or interruption
records a durable `not_before` time and uses bounded exponential backoff with jitter; a
low-frequency watcher rechecks telemetry and ordinary work without busy
polling. The exact 30-second base, 1800-second cap, zero-to-25-percent
deterministic jitter, durable attempt progression, 15-second minimum poll
interval, and reset-after-durable-arm-commit rule are canonical in
`docs/operations/SAMPLE_CAMPAIGN_QUEUE.md`. If meaningful external contention
appears, preserve the newest
durable checkpoint, request cancellation only through Maestro, accept loss of
the in-flight arm, and requeue the same job without duplicating the pair; never
signal or control the foreign process. For owner- or agent-required GPU work,
prefer the next completed generation boundary when urgency permits, otherwise
use the same durable cancel/requeue path.

For each video arm, VLM review uses 2–5 sequential, non-adjacent, nearby frames.
The frames must be spread enough to show motion and temporal coherence, but not
so far apart that they become unrelated stills. Both arms use identical
normalized sampling positions. Preserve private per-arm manifests, frame and
output digests, VLM evidence, and an evidence-class label. The human review
queue must show the pair together and allow keep, reject, or request-rerun; VLM
output is provisional and never substitutes for creator acceptance. The exact
nearby-window geometry and receipt requirements are canonical in
`docs/operations/SAMPLE_CAMPAIGN_QUEUE.md`.

Current model-free implementation evidence now covers atomic two-arm held
submission, guarded one-arm release with allocator and post-slot rechecks,
durable sample-specific preemption/retry, and an
owner/project-authorized read-only paired queue projection visible only to a
local, recently reauthenticated owner. The projection is content-free and
deliberately has no hold, resume, priority, release, rerun, or review-decision
controls. Completed arm outputs remain `outputs_unbound` until review evidence
can be durably bound.

Do not start GPU work merely because that substrate exists. The remaining gaps
are:

- no live authenticated browser/NVML/model acceptance;
- no VLM execution;
- no durable receipt/CAS store; and
- no human review decision mutations or human-review UI.

The model-free evidence is not authority to claim a Wave 1 pair as queued,
generated, VLM-reviewed, or human-reviewed. Before releasing Reference Lock,
exercise the authenticated owner flow and fail-closed NVML/model path live at
an observed idle boundary, then implement and verify durable receipt binding,
VLM execution, and explicit human keep/reject/rerun decisions. None of this
changes the separate, existing recent-password-reauthentication gate for the
account/project migration action or permits a historical SQLite tracker
mutation.

## Start Maestro Continuum

Start Continuum from Pinokio's **Start** action (`start.js`). Do not launch
with `python wgp.py`. **Start (Classic UI)** is a separate local-only path.

If Pinokio itself is not running, start the installed desktop/AppImage first.
On Linux, an Electron SUID sandbox abort is fixed with `--no-sandbox` at
launch time; do not rewrite Pinokio or Maestro files for that.

Resolve the live app; do not hardcode a port:

```bash
pterm search "Maestro Continuum" --mode balanced --min-match 1 --limit 8
pterm status "$MAESTRO_REF" --probe --timeout=5000
pterm run "$MAESTRO_REF" --default start.js
```

RTX 50 with NVIDIA driver 580 or newer prefers `app/env-rtx50` when its
install marker exists. Otherwise Start uses the preserved `app/env`
compatibility runtime and logs that Update still needs to finish the CUDA 13
migration. A driver older than 580 still stops until the operator updates it.

`start.js` assigns `SERVER_PORT` with Pinokio's `{{port}}` on the `launch.py`
step. `launch.py` binds that port before importing torch/WanGP so Pinokio
Caddy cannot steal it while models load. Caddy is Pinokio's HTTPS reverse
proxy, not Blender. Keep `MAESTRO_STRICT_SERVER_PORT=true` in the ignored
operator ENVIRONMENT so a busy requested port fails instead of silently
moving the stable-share backend; set it false only as a temporary recovery.

After Start, use the current `ready_url` and probe `/health` then `/ready`.
Never reuse a previous session's port.

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

After the intended surfaces are healthy, require both direct-loopback `/health`
liveness and `/ready` recovery completion before clearing that exact generation,
then show the result:

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
curl -fsS "${MAESTRO_URL%/}/ready"
```

Set `MAESTRO_URL` from the current `ready_url` or the specific external surface
being tested; never copy an old port from a handoff. After the direct-loopback
`/health` probe succeeds and `/ready` reports that startup recovery and reindexing
are complete, `start.js` records its backend-ready marker even if Cloudflare is
still starting. The dynamic menu requires that marker plus a fresh, bounded
direct-loopback `/health` probe before advertising the local Web UI. `/health`
remains the minimal process-liveness check for stable and other access-surface
verification.

## Verification matrix

Verify only configured surfaces and label each evidence level accurately.

| Surface | Required checks |
| --- | --- |
| Direct local | `pterm status --probe`, then `/health`, `/ready`, `/api/v1/account/context`, and `/api/v1/workspaces` against the current `ready_url`. |
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
