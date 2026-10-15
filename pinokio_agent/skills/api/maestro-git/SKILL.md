---
name: api-maestro-git
description: Use Maestro Continuum's production HTTP API and MCP with discovery, scoped sessions and reliable operation reconciliation.
---

# Maestro Continuum API

Start with the [API guide](../../../../docs/api/README.md) and
[feature coverage map](../../../../docs/api/coverage.md). The running app also
serves the guide at `/api/v1/api/documentation/guide`, coverage at
`/api/v1/api/documentation/coverage`, OpenAPI at `/openapi.json`, and discovery
at `/api/v1/api/discovery`. Resolve the current URL from Pinokio; do not assume
an old port. This HTTP API is separate from WanGP's in-process Python API.

## Discover before acting

Read discovery, access-context, account/context and workspaces with the same
operator-authorized session. Use explicit project/workspace scope. Read the
selected model's catalog entry, options, defaults and applicable host terms.
Supported, installed, experimental, legally available and runtime-ready are
separate facts. Health, catalog visibility and a queued ACK do not grant GPU
execution. Apply the installed gpu-coordinator-client skill before model work.

Use the maintained `sdk/python` MaestroClient, `sdk/typescript` MaestroClient,
or the `maestro-continuum-mcp` stdio bridge. Operation IDs, inputs, result
shapes, permissions and replay policies come from the current OpenAPI contract.
MCP exposes approved public operations and bounded base64 uploads, never a
shell, arbitrary host paths, credentials or internal runtime authority.
For retained Gallery input reuse, `maestro_gallery_to_upload` pins an exact
authorized media revision into a private session-owned upload, up to 16 MiB.
Generate in the original project using its returned upload filename. Larger
media uses the explicit SDK download/upload workflow. Upload acknowledgement
loss is uncertain; never automatically repeat this composite operation.
Reconnect to refresh tools after a release. The `--read-only` mode narrows
available tools; annotations do not override server permissions.

## Authentication and locality

Preserve signed session/account cookies in the operator's owning credential
mechanism; never print them, copy browser profiles, or put credentials in AI
prompts/tool arguments. The existing human login, nonce, model-term acceptance
and recent reauthentication flows remain controlling. Do not activate accounts,
alter policy or impersonate an owner to make an operation succeed.

Send the exact configured app Origin for HTTP mutations. Browsers set their own
Origin and cookies. Local, LAN and Cloudflare use the same scoped production
API; discover concrete owner/local-only restrictions from access-context and
each operation. A 401/403 is an access problem, not a retry or bypass request.
Do not forge forwarded headers, paths or project-instance state.

## Long-running operations

Generation jobs, Director pipelines, queue entries, LLM operations, preparation
requests, YuE2 takes and training jobs have distinct IDs and status routes.
Persist the exact body, caller identity when supported, returned ID and project.
A submission response is admission, not completed output. Observe progress,
outputs, holds and manual review with the matching route.

For Studio use one UUID4 `generation_request_id` and reconcile
`GET /api/v1/generate/submissions/{generation_request_id}` with the same project
and session after uncertainty. Other operations have their own request-ID and
replay contracts. If an operation lacks durable replay support, inspect its
existing queue/history and preserve the unresolved submission; do not guess a
new ID or blindly resubmit. Client timeout never cancels server work.

Use held submission when deliberately staging work. Releasing a queue or
continuing a review can start model execution and still requires actual GPU
and project authority. Cancel only the specifically authorized owned operation;
a cancel ACK is not proof of terminal drain. Recovery must use the public,
project-scoped production path and preserve frozen inputs and failed attempts.
Never replay accepted samples as routine API verification.

For an interrupted ordinary saved-video rerun, read its retained
`recovery.video_repairs` identity. The explicit
`POST /director/pipelines/{pid}/clips/{clip_index}/rerun-video/reconcile`
under `/api/v1` accepts the same `workspace`, `job_id` and `revision` and only
checkpoints a durably terminal child. It cannot submit, resume or cancel work.
A blocked/nonterminal response requires the stated owner action; drain or a
client timeout never implies failure or cancellation. An explicitly authorized
exact-child cancellation is separate. Repeat only this reconciliation with the
same identity after uncertainty; never repeat the generation rerun to reconcile.

## Repository maintenance

For implementation, read the current AGENTS/CONTRIBUTING and durable worklog,
verify the exact checkout, dirty paths and structured ownership reservations,
and use the shared activation audit before any Beads lifecycle mutation.
Do not run `bd where` to discover a historical tracker, initialize an absent
tracker, hand-edit claims, erase foreign changes or broadly stage files.

Update authored contracts in `app/continuum_api` when handlers change. Export
actual running OpenAPI and generate maintained references with
`scripts/generate_continuum_clients.py --schema docs/api/openapi.json`.
Source inventory extraction is not complete semantic or execution evidence.
Apply test-value: zero routine unit/integration suites; use only the narrow
build or smallest real shipped-app journey justified by changed behavior.
Preserve formal obligations and runtime guards. Record actual evidence and
remaining limitations; source/build/API health do not prove native quality.

Serialize restarts and Git/tracker writes through existing ownership and
publication authority. Use Pinokio's existing coordinated restart-status flow,
verify the actual local/share health, then clear only its exact generation.
Do not blindly retry an ambiguous restart or push through foreign dirty work.
