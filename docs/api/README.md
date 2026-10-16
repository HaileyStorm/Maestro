# Maestro Continuum API

Continuum's HTTP API uses the same project authorization, admissions, queues,
model execution and output publication as the shipped app. It is distinct from
the **WanGP in-process Python API** in [`app/docs/API.md`](../../app/docs/API.md).
Do not start WanGP or import `wgp.py` to use this HTTP API.

Use the current Maestro URL shown by Pinokio. Ports change after restarts.
Localhost, authorized LAN access and the configured Cloudflare share use the
same `/api/v1` paths. `/health` and `/ready` report service health, not model
readiness or a GPU grant.

The running `/openapi.json` is the request
and response contract. `/docs` offers a filterable interactive viewer with
collapsed feature groups and operation IDs. Filter by a feature such as
`editor`, expand an operation and use **Try it out**. Same-origin browser
requests use your existing signed-in session; never paste session cookies into
the **Authorize** dialog. Owner confirmation, terms and project permissions
still apply. Start with
`GET /api/v1/api/discovery`: it returns actual access, the filtered model
catalog, operation identities, boundaries and capability discovery links.
The [feature coverage map](coverage.md) explains compositions and human
boundaries. The [operation reference](operations.md) contains generated IDs.
The [Drama 3 guide](drama3.md) covers directed speech, reference voices, FP8 CPU
offload and its experimental execution boundaries through the same API.
The [saved voice guide](voice-cloning.md) explains reusable recordings,
paired transcripts and private reference preparation in Python, TypeScript and MCP.
The [exported OpenAPI](openapi.json) is an offline snapshot; current server
discovery remains authoritative.

## Setup and first discovery

Run the shipped app through Pinokio **Start**. Install the Python client from
this checkout using your normal virtual environment:

```bash
uv pip install ./sdk/python
uv pip install './sdk/python[mcp]' # only when using the MCP bridge
```

No package installation starts a model. New model assets follow the configured
model storage location; the API does not select a new cache root.

Python:

```python
import os
from maestro_continuum import MaestroClient

with MaestroClient(os.environ["MAESTRO_URL"],
                   cookie_file=os.environ.get("MAESTRO_COOKIE_FILE")) as maestro:
    discovery = maestro.discover()
    print(discovery["access"])
    print(maestro.request("GET", "/api/v1/workspaces"))
    for operation_id, operation in maestro.operations(feature="editor").items():
        print(operation_id, operation["method"], operation["path"])
```

curl, keeping its signed session in an operator-owned cookie jar:

```bash
umask 077
export MAESTRO_URL='http://127.0.0.1:CURRENT_PORT'
export MAESTRO_ORIGIN="$MAESTRO_URL" # exact scheme/host/port, with no path or trailing slash
curl --fail-with-body -c maestro-discovery.cookies -b maestro-discovery.cookies \
  "$MAESTRO_URL/api/v1/api/discovery"
curl --fail-with-body -b maestro-discovery.cookies \
  "$MAESTRO_URL/api/v1/models"
```

Replace `CURRENT_PORT` with the actual configured port. Keep cookie jars out of
repositories, prompts, MCP tool arguments and logs. Do not copy browser profiles
or cookies to create an AI session.

The TypeScript client is [`sdk/typescript`](../../sdk/typescript). Build it from
the repository root:

```bash
npm --prefix sdk/typescript install
npm --prefix sdk/typescript run build
```

From the repository root, this runnable Node example uses the
compiled TypeScript client to discover the server's actual anonymous access:

```bash
node --input-type=module <<'JS'
import { MaestroClient } from './sdk/typescript/dist/index.js'
const maestro = new MaestroClient(process.env.MAESTRO_URL)
console.log(JSON.stringify(await maestro.discover(), null, 2))
console.log(await maestro.request('GET', '/api/v1/workspaces'))
JS
```

For signed-in Node 22+ usage, use the Node-only adapter with the private
Netscape cookie jar created by the human login helper below:

```bash
node --input-type=module <<'JS'
import { createNodeClient } from './sdk/typescript/dist/node.js'
const maestro = await createNodeClient(process.env.MAESTRO_URL, {
  cookieFile: process.env.MAESTRO_COOKIE_FILE,
})
const account = await maestro.request('GET', '/api/v1/account/context')
if (!account.authenticated) throw new Error('Human sign-in is required')
console.log(await maestro.request('GET', '/api/v1/workspaces'))
console.log(await maestro.request('GET', '/api/v1/projects/mythwright-media/editor/projects'))
JS
```

The adapter reads only the explicitly selected jar, imports Maestro cookies
for the configured host, confines requests to that exact origin, refuses
redirects and keeps response cookie updates in memory. It never exports or
rewrites the file and never logs in automatically. POSIX files must be regular,
owned by the current user and inaccessible to group/others; on Windows, the
operator must protect the file with their account's ACL. Browser bundles should
import `index.js`, which has no Node filesystem dependency. An empty project
list from anonymous discovery does not prove private projects are absent.
In a same-origin browser application, import the TypeScript SDK
through that application's bundler; the browser keeps its existing session:

```typescript
import { MaestroClient } from './sdk/typescript/dist/index.js'

const maestro = new MaestroClient(window.location.origin)
console.log(await maestro.discover())
console.log(await maestro.request('GET', '/api/v1/workspaces'))
const operations = await maestro.operations()
const editorRead = Object.values(operations).find(op =>
  op.method === 'GET' && op.path === '/api/v1/projects/{project}/editor/projects')!
console.log(await maestro.call(editorRead.operationId, { path: { project: 'my-project' } }))
```

## Authentication, project access and locality

Read `/api/v1/access-context`, `/api/v1/account/context` and `/api/v1/workspaces`.
The signed `maestro_session` identifies a session; it does not bypass project
access. When account enforcement is enabled, the existing
`maestro_account_session` and project membership additionally apply. When it is
disabled, the existing local compatibility policy applies. This interface never
activates accounts or creates API keys.

For a separate authorized client session, the human can use the existing
account login flow. The installed human-only helper prompts in an interactive
terminal and saves a new private cookie jar without overwriting an existing one:

```bash
mkdir -m 700 -p "$HOME/.config/maestro"
maestro-continuum-login --url "$MAESTRO_URL" \
  --cookie-file "$HOME/.config/maestro/continuum.cookies"
export MAESTRO_COOKIE_FILE="$HOME/.config/maestro/continuum.cookies"
```

The human types credentials directly into that terminal; the helper never
accepts passwords from command arguments, pipes, environment or MCP. Use a new
filename for a later login and retire sessions through the existing account UI.
For Python, pass `cookie_file=os.environ["MAESTRO_COOKIE_FILE"]` to `MaestroClient`.
curl uses `-b "$MAESTRO_COOKIE_FILE"`; MCP reads the same operator-selected path.
This login helper does not provide recent owner reauthentication or model consent.

For sustained service access, keep the jar in the operator's private store and
provide only its path to the authorized server worker. Account sessions normally
expire after 30 days and can be revoked earlier. They retain that account's
permissions, so a client must explicitly bind every operation to its intended
project; the jar is not a project-restricted API key. Read account/access context
on reconnection. Expiry or revocation requires a new human login and a new jar
path, followed by reloading the client. Before retrying work, reconcile saved
submission/job/pipeline identities. A new login does not authorize replaying an
uncertain mutation or continuing a browser-session-owned Director pipeline.

To use your own operator credential mechanism, obtain `POST /api/v1/account/nonce` with
`{"purpose":"login"}`, then `POST /api/v1/account/login` with `username`,
`password`, the returned `nonce`, and optional `device_label`. Both requests
use the same session and exact app `Origin`; successful login sets the account
cookie. Account bootstrap, registration, password/recovery operations and
reauthentication remain human operations with their existing restrictions.
Supply credentials through the operator's credential mechanism, never an AI
prompt or an MCP argument. A failed or unavailable login is not permission to
enable accounts or weaken access.

Mutation requests send `Origin: <exact configured app origin>`. While remote
sharing is enabled, mutations require an approved Origin or Referer even from
ordinary HTTP clients. Account mutations always require the exact origin.
Browsers set these headers themselves; they cannot be overridden by JavaScript.
The Python client and Node client adapter use the configured origin. CORS is
not authentication. Do not forge forwarded headers or tunnel identity.

Use explicit `workspace` or the `{project}` path parameter. Avoid relying on the
host's mutable active project. Legacy Cloudflare access requires a protected,
unlocked named project and does not expose the default project. Account mode
uses membership and operation permissions such as `project.read`,
`project.mutate`, `project.generate` and `project.membership.manage`.

Local, LAN and Cloudflare are transport distinctions. Actual owner controls
depend on access-context's route allowlist and recent reauthentication, not a
guess based on the hostname. Configuration, filesystem scans/imports, account
administration, recovery authority and confirmed resource release preserve their
specific local/owner boundaries. The SDK can represent their public HTTP
contracts; the MCP bridge excludes credential and internal authority operations.
Use the indicated owner UI when confirmation is required.

## Models, settings, terms and readiness

For the selected model, read `/api/v1/models`,
`/api/v1/model-options/{model_type}` and `/api/v1/defaults/{model_type}`.
Use returned IDs and settings; do not hard-code a model from an example.
`supported_operations` lists operations explicitly declared by a model recipe;
an empty list means none are declared, not that the generic generation endpoint
is disabled. Read the model's options and defaults before using that endpoint.
Input capabilities and Director compatibility describe separate support.
`generates_audio` includes audio-only speech/music and audiovisual generation;
`audio_only` distinguishes the former. `supports_audio_input` describes accepted
audio conditioning, independently of output. These flags describe the handler's
output/input contract, not successful native qualification.
`is_downloaded` describes installed assets. `verified`, experimental
capability flags, `availability_status`, manual-checkpoint verification,
`required_host_terms` and capability-specific status/preparation describe
different prerequisites. An `available` legal/install policy is not a promise
that the runtime can execute a model now. Unknown readiness stays unknown.

Read `/api/v1/host-terms?workspace=...` for applicable acceptance state. The human
must review/accept the current terms through the existing flow. The API and MCP
do not accept terms on the human's behalf. Curated download uses
`POST /api/v1/models/{model_type}/download` with the required project and terms;
observe `/api/v1/models/downloads/status`. Downloading is distinct from loading,
preparing and generating. Manual checkpoints and custom acquisition retain their
local owner workflow.

Before actual model work, obtain the installed GPU coordinator's fresh,
exact-workload authority. Catalog entries, idle hardware, a held request and
`/ready` are not grants. Do not submit internal grant objects through this API.
Local creative prompts/media follow the existing content-neutral execution
path; this interface adds no prompt scanning or subject-matter policy layer.
An explicitly chosen remote provider retains its existing privacy/terms gate.

## Generation, held queues and uncertain submission

Use `POST /api/v1/generate` with an immutable `generation_request_id`, explicit
workspace, a catalog model ID, prompt and model-specific settings. The public
`_queue_mode` is `now` or `held`; `held` saves a genuine job without releasing it
for model work. Preserve the exact submitted JSON before sending it.

New generation admissions default to the current `settings_version` from the
model defaults. Preserve an explicit old version when importing legacy saved
settings: historical migrations can change audio-processing flags. A normal
clean voice reference uses `audio_prompt_type: "A"`; vocal extraction is an
optional processing choice, rather than a requirement for cloning. Existing
admitted jobs and repeated submission IDs retain their original settings.

```python
import os, uuid
from maestro_continuum import MaestroClient, UncertainSubmission

with MaestroClient(os.environ["MAESTRO_URL"],
                   cookie_file=os.environ.get("MAESTRO_COOKIE_FILE")) as maestro:
    workspace = "my-project"
    model = os.environ["MAESTRO_MODEL"]  # selected from this server's catalog
    request_id = str(uuid.uuid4())
    defaults = maestro.request("GET", f"/api/v1/defaults/{model}")
    body = {**defaults, "workspace": workspace, "model_type": model,
            "prompt": "A ceramic cup beside an open window in afternoon light",
            "generation_request_id": request_id, "_queue_mode": "held",
            "enhance_before_generate": False}
    try:
        admission = maestro.request("POST", "/api/v1/generate", body=body)
    except UncertainSubmission:
        admission = maestro.request("GET", f"/api/v1/generate/submissions/{request_id}",
                                    query={"workspace": workspace})
    print(admission)
```

`admission_state` is `unknown`, `pending` or `accepted`. Accepted responses bind
the `generation_request_id` to a server `job_id`. Pending/unknown is not proof of
cancellation or permission to send a new ID. Continue polling the same lookup;
an ambiguous submission never gets an automatic retry. Exact same-body replay
is supported by the Studio admission contract; different input under the same
ID conflicts. Reconcile before replaying, including after a 503 or reconnect.

curl for a **previously prepared** JSON body containing the chosen request ID:

```bash
curl --fail-with-body -c "$MAESTRO_COOKIE_FILE" -b "$MAESTRO_COOKIE_FILE" \
  -H "Origin: $MAESTRO_ORIGIN" -H 'Content-Type: application/json' \
  --data-binary @generation.json "$MAESTRO_URL/api/v1/generate"
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" \
  "$MAESTRO_URL/api/v1/generate/submissions/$REQUEST_ID?workspace=$WORKSPACE"
```

Read `/api/v1/status/{job_id}` and `/api/v1/queue` for progress, holds, recovery
and outputs. A held job is released individually by
`POST /api/v1/queue/{job_id}/resume` only after its execution prerequisites are
satisfied. `/jobs/queue/start` releases all authorized held jobs: do not use it
to release an unrelated backlog. `POST /api/v1/cancel/{job_id}` requests
cancellation; observe terminal status and resource release separately.

After a service interruption, a job can retain its historical `status: "running"`
while `recovery_interrupted: true` records the interruption. That flag can also
remain set while automatic recovery is starting; it does not prove that no
worker is active. Check Queue and the returned recovery metadata.
Inspect `recovery_interrupted`, `recovery_blocked`, `recovery_actionable` and
`recovery_actions` along with status. Python and TypeScript `wait` return blocked
or actionable recovery snapshots without cancelling or retrying. The
interruption flag alone does not end a wait during automatic recovery. A wait
timeout leaves execution unchanged;
reconcile the same identifiers and Queue rather than resubmitting. If recovery
stays unavailable, reconcile with the owner; do not create another generation
request to bypass recovery.

An interrupted job admitted under an operator's supervised GPU reservation
stays held after startup. The operator must renew its exact reservation before
the usual release or recovery operation can resume it. An individual release
request otherwise returns `409` with a next-step instruction; bulk release
leaves it held. Clients cannot renew that authority through this API. Ordinary
jobs retain their existing automatic recovery.

While that original supervised job is queued and held, status and Queue return
`recovery_requires_gpu_reservation: true` and `recovery_blocked: true`.
If no other recovery failure takes precedence, `recovery_reason` is
`gpu_reservation_required`. SDK waits return this blocked snapshot. These fields
describe the release prerequisite; they do not grant authority or certify that
a reservation is ready. After the operator installs fresh matching authority,
release the same job individually through `/queue/{job_id}/resume` or
`/queue/{job_id}/start-next`. An empty `recovery_actions` list does not prevent
that ordinary queue release. Preserve the original submission and job IDs.

If an accepted, never-started Studio job encounters resource-admission failure,
keep its original request ID and job ID. Read `recovery_actions`; when it offers
`retry`, call `POST /api/v1/queue/{job_id}/recovery-retry` once. This narrow
recovery revalidates the original request and registers the same job while
held. A successful result has `recovery_state: "restored"`, `held: true` and
`reruns_denoise: false`; it starts no worker or model work. Release that job
separately after its execution prerequisites are satisfied. If registration
remains blocked, the server preserves the request and returns an actionable
conflict. After an uncertain retry response, inspect the same job and submission
receipt before doing anything else; do not create another generation request.
Other advertised retries can start a fresh worker and rerun generation, so
inspect the returned recovery state and `reruns_denoise` rather than assuming
every retry is held-only.

The live job list and `/status/{job_id}` are not permanent history. A completed
job can disappear from them after restart or dismissal. Reconcile its original
`generation_request_id` through `/generate/submissions/{generation_request_id}`
with the same session and workspace. An accepted, completed receipt with
`retained_job: false` still binds the original job; its minimal `job` projection
can have empty `output_files` and zero `produced_outputs`. Those fields do not
mean the output was lost. Preserve the output names, revisions and terminal
cleanup receipt when first observed, and locate retained media through
`GET /api/v1/outputs` with `workspace` before requesting its authorized file.
A live status 404 alone is neither cancellation nor permission to regenerate.

For an ordinary **held, unstarted Studio image job**, opt into releasing its
generation model when its invocation exits. This leaves the immutable generation
request and its submission ID intact:

```python
policy = maestro.request("PUT", f"/api/v1/jobs/{job_id}/completion-policy",
                         body={"workspace": workspace,
                               "release_generation_model": True})
```

The same operation is discoverable through the TypeScript client and MCP under
`queue.completion_cleanup`. Setting the same value on the same job is idempotent;
after an uncertain policy response, read that job's `completion_policy` through
`GET /api/v1/status/{job_id}`. The policy does not release the queue or execute a
model. Start the existing job separately after its execution prerequisites are
satisfied. Wait for `completion_cleanup.status` as well as terminal generation
status: `released` confirms the generation-model owners were released while the
invocation still held its native slot; `not_loaded` means no generation model
remained. `not_used` means no renderer entry, and `pending` or `unconfirmed`
requires reconciliation before claiming resource cleanup. A timeout or process
crash does not prove cleanup and must not cause a new generation submission.

This policy currently supports ordinary Studio image jobs. It does not unload
the language model, other tools' models, or a peer's yielded execution slot.
General Free Resources still uses the existing owner confirmation and recent
reauthentication flow. Other job types retain their documented resource controls.

H3 plan preview and approval use `/generate/plan` and
`/generate/{job_id}/plan/approve`. Guides and Bridge use their dedicated authored
operations with current Gallery revisions. Continue/Extend can be composed from
authorized uploaded source media and the ordinary generation operation; use the
model options and the documented continuation fields in the coverage map.
Specialized Retake/Bridge/tool submissions currently lack Studio's common
caller-ID receipt. After a lost response, inspect the same session's jobs,
Queue and outputs and stop for owner reconciliation if acceptance cannot be
identified. Never infer exactly-once behavior from a generated wrapper.

For **VoxCPM text-only narration**, use the ordinary generation workflow with
`model_type: "voxcpm2"`, `generation_mode: "audio"`, a narration `prompt`,
`audio_prompt_type: ""` and no `audio_guide`. The empty conditioning value means
**Text only**; it is intentional. `alt_prompt` can carry voice delivery
instructions. To use optional voice cloning, select `audio_prompt_type: "A"`
and supply an authorized uploaded audio reference as `audio_guide`. Choosing
`"A"` without that reference is an invalid input, not a text-only request.
Inspect the current model options and readiness before submission; installed
assets alone do not establish working native inference. A configured duration
is not a guarantee of exact speech length or voice quality.

## Director, Chat, music and recovery

Director preview is a separate LLM operation: submit `/director/v2/plan` with
its caller request ID, then observe `/llm/operations/director_preview/{id}` and
`/result`. Its cancellation operation is distinct from canceling a generation
job. An LLM preparation ID and a Director pipeline ID are also distinct.

For a real story pipeline, `/director/preflight` discovers admissibility;
`/director/pipeline/start` with `hold_planning:true` saves a genuine parent before
planning. Prepared clip plans cannot be substituted into this hold. Observe
`/director/pipeline/{pipeline_id}`. At `pause_reason:"before_planning"`, continue
with an empty body to release the frozen request after fresh model authority.
Review pauses permit their documented clip-plan edits; image review can set
`hold_video:true`. Stop, continue and recovered resume are separate actions.
Do not assume a restart resumes an unreleased hold.
For `short_film_story` or `short_film_audio`, supply the authored text as
`story_description` or the compatible `scene_description`. A nonempty
`scene_description` takes precedence if both are supplied. Both planning
backends and saved/queue descriptions use that same text; the original frozen
request fields remain intact. Review the resulting plan for story fidelity
before continuing to image or video execution.
An interrupted attempt tied to a private execution reservation reports
`blocked_planning_admission` after restart. Its owner must resolve the retained
attempt and obtain fresh authority through the existing recovery path;
ordinary Continue cannot release it. Clients cannot supply private admission
markers or reconstruct an expired grant.

Unexpected runtime stops end the pipeline as `failed`; an explicit Stop remains
`cancelled`. Read `failure_details.code` before taking recovery action. The
planning guard retains these fixed reasons without exposing execution authority:

| Code | Meaning and next action |
|---|---|
| `director_planning_parent_busy` | The parent could not be observed within the guard's lock budget. Check the retained pipeline status and local logs before an explicit recovery attempt. |
| `director_planning_parent_inactive` | The parent disappeared or became terminal. Read its current status; do not start another attempt for a cancelled or completed parent. |
| `director_planning_authority_rejected` | The current execution reservation observation was rejected. The owner must resolve that reservation and obtain fresh authority before recovery. |
| `director_planning_observation_expired` | No sufficiently fresh observation was available. Inspect the retained attempt; a timeout is neither permission to resubmit nor cancellation of other work. |

These codes identify a guard stop, not a model timeout or crash. Other failures
retain the existing structured failure envelope. Cleanup does not replace the
first recorded guard reason; historical attempts without it remain unknown.

For example, save this body as `director-story.json`, using a project you can
access and model IDs returned by discovery. `short_film_story` is the story
workflow; `short_film` is not an accepted pipeline type.

```json
{
  "workspace": "example-project",
  "pipeline_type": "short_film_story",
  "story_description": "A ceramic cup rests beside an open window. A breeze lifts the curtain and morning light moves across the cup. One continuous five-second shot with a quiet ending.",
  "video_model": "minimax_h3",
  "image_creator_model": "flux2_klein_9b",
  "image_editor_model": "flux2_klein_9b",
  "target_duration": 5,
  "target_scenes": 1,
  "fps": 24,
  "private_output": true,
  "auto_mode": false,
  "hold_planning": true,
  "hold_video": true
}
```

```bash
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" \
  -H "Origin: $MAESTRO_ORIGIN" -H 'Content-Type: application/json' \
  --data-binary @director-story.json "$MAESTRO_URL/api/v1/director/pipeline/start"
# Save the returned pipeline_id; substitute it below.
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" \
  "$MAESTRO_URL/api/v1/director/pipeline/$PIPELINE_ID"
```

Director freezes the Services provider, model and device at admission. With no
saved device override, a new admission uses the displayed host default: CUDA
when available, otherwise CPU. An explicitly saved CPU selection stays on CPU.
Saved parents retain their frozen device; changing Services is not permission to
rewrite or replay them. CPU planning can take much longer than video duration.
Read the returned planning progress and retain the pipeline identity.

The initial status is `paused` with `pause_reason:"before_planning"`; no model
work has begun. After satisfying the selected model's terms/readiness and the
host's compute authority, POST `{}` to
`/api/v1/director/pipeline/$PIPELINE_ID/continue` using the same cookie and Origin
headers. `auto_mode:false` pauses for prompt and image review. `hold_video:true`
holds video children separately and does not hold planning. Do not resubmit the
start body on reconnect: inspect the retained pipeline by its ID.

Director queue entry IDs, enqueue request IDs and later pipeline IDs differ.
Use the queue's exact request/project-incarnation contract and GET operations to
reconcile. Saved pipeline tags, repairs, reruns and rejoin use `/director/pipelines`
routes. Repair state is not a generation status. Direct start/rerun operations
without an admission receipt must not be blindly retried after a timeout.

For the currently supported one-clip H3 story shapes, inspect
`GET /api/v1/director/pipelines/{pid}/clips/0/rerun-video/prepare?workspace=...`
first. This read-only operation invokes the production builder and validates
its sealed starting image or supported retained style inputs; it creates no
job, intent, output or model load.
`generated_request` shows the derived public controls. Its hashes are current
observations, not execution authority or idempotency keys. Preparation currently
supports a cancelled pipeline with a retained editor image, or a failed
supervised LoRA original whose retries are exhausted and custody is released.
The experimental Ref2VA shape is one cancelled story clip using retained
style images only, with no starting image, ordinary references, audio source,
long-form plan or active LoRAs. It validates the current owner/project and
the retained image bytes; it does not convert a style into a subject reference.
Other shapes return a state or unsupported error. Preparation does not promise
that the request will still be admissible when submitted.

Read the derived controls with the authenticated Python client from setup:

```python
from urllib.parse import quote

prepared = maestro.request(
    "GET",
    f"/api/v1/director/pipelines/{quote(pipeline_id, safe='')}/clips/0/rerun-video/prepare",
    query={"workspace": project_id},
)
print(prepared["generated_request"], prepared["unit"])
# Review these controls and current model readiness before the separate POST.
```

The same read is available through the generated TypeScript operation reference
and MCP's discovered operation tools. Neither returns private execution seals.

For an exhausted original, a supported rerun creates a distinct child and
atomically retains the original failure alongside the new intent. It does not
reset the original attempts or regenerate the accepted starting image. The
rerun builder derives frames and window size from the saved clip plan, so its
request can differ from the original generation; inspect those values first.

For an ordinary saved clip video rerun, POST the explicit `workspace` and an
optional `prompt` to `/api/v1/director/pipelines/{pid}/clips/{clip_index}/rerun-video`.
The successful response includes the actual child `job_id` and output
`filename`. This awaited call can exceed a client's timeout. Read
`GET /api/v1/director/pipelines/{pid}?workspace=...` and inspect
`recovery.video_repairs["0"]` for clip zero: `state`, `revision`, `job_id` and
`output_files` describe its retained attempt. Use `/status/{job_id}` for the
generation child when it remains available; the saved repair and clip output
remain the reconciliation record after completed queue rows retire. A parent
can remain `cancelled` while its separately requested clip repair completes.
If an interrupted ordinary clip rerun retains a submitted repair, use
`POST /api/v1/director/pipelines/{pid}/clips/{clip_index}/rerun-video/reconcile`
with the explicit `workspace`, retained `job_id`, and integer `revision` from
`recovery.video_repairs`. This operation only checkpoints an already terminal
child; it cannot start or resume generation. A still held or running child
returns `409` with its next recovery action. A lost client response or a drained
worker alone is never terminal evidence. Where the owner explicitly chooses to
cancel an unwanted attempt, cancel that exact child through `/cancel/{job_id}`
and observe its durable cancellation before reconciliation. Reconciliation of
the same terminal child and revision is idempotent; a different identity or
revision conflicts. It preserves accepted images and the original attempts.

If generation completed but producer metadata publication failed, reconciliation
returns `409` while successful native evidence remains. For a single ordinary
saved H3 task/repeat with one retained MP4 and no requested effects, use
`POST /api/v1/queue/{job_id}/recovery-retry?publication_only=true`. This explicit
CPU recovery verifies the sealed request, original owner/project, native receipts
and exact quarantined bytes. It restores the producer and runs the existing
Director delivery integrity/publication path without loading a model. Unsupported
shapes or changed/ambiguous evidence return `409`; there is no generation fallback.
The original native attempt and quarantined file remain preserved. The response
returns the original `job_id`, `operation="director_publication_recovery"`,
`reruns_denoise=false` and `next_action="observe_job"`. Poll that job after an
uncertain response; do not submit another generation. After a server restart, a
held CPU publication can be explicitly resumed with the same publication-only
request; its sealed intent is revalidated and no native retry is created. Once completed, reconcile
the same saved repair job and revision as described above.

```python
saved = maestro.request(
    "GET", f"/api/v1/director/pipelines/{quote(pipeline_id, safe='')}",
    query={"workspace": project_id},
)
repair = saved["recovery"]["video_repairs"]["0"]
reconciled = maestro.request(
    "POST",
    f"/api/v1/director/pipelines/{quote(pipeline_id, safe='')}/clips/0/rerun-video/reconcile",
    body={"workspace": project_id, "job_id": repair["job_id"], "revision": repair["revision"]},
)
print(reconciled)
```

The same terminal operation through TypeScript (using the client from setup):

```typescript
const saved = await maestro.request('GET',
  `/api/v1/director/pipelines/${encodeURIComponent(pipelineId)}`,
  { query: { workspace: projectId } }) as {
    recovery: { video_repairs: Record<string, { job_id: string; revision: number }> };
  };
const repair = saved.recovery.video_repairs['0'];
const reconciled = await maestro.request('POST',
  `/api/v1/director/pipelines/${encodeURIComponent(pipelineId)}/clips/0/rerun-video/reconcile`,
  { body: { workspace: projectId, job_id: repair.job_id, revision: repair.revision } });
console.log(reconciled);
```

For curl, save those same three fields as `repair.json`, then submit once:

```bash
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" -H "Origin: $MAESTRO_ORIGIN" \
  -H 'Content-Type: application/json' --data-binary @repair.json \
  "$MAESTRO_URL/api/v1/director/pipelines/$PIPELINE_ID/clips/0/rerun-video/reconcile"
```

An uncertain response to this terminal-only operation can be reconciled by
reading the saved repair and repeating the same identity. This guarantee does
not apply to the separate generation rerun endpoint.

H3 scene reruns instead retain `recovery.scene_repairs` and may omit the response
`job_id`. There is no caller idempotency key for either operation. Matching
unresolved ordinary work can attach to its saved intent; repeating a completed
rerun can create another revision. Read first and never automatically retry.
Private frozen inputs, process identities and execution authority are not part
of the public repair record.

The requested resolution is not a guarantee of the encoded canvas: the current
native image-fitting policy can adopt the starting image's aspect ratio. A real
H3 rerun requested 1280x720 and produced 704x704 from its square starting image,
with 124 frames at 24 fps and stereo audio. Inspect the returned media's actual
dimensions and duration before composing or exporting it; do not infer them
from the saved request alone.

```bash
# Observe a retained clip attempt after a lost rerun response; this starts no work.
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" \
  --get --data-urlencode "workspace=$MAESTRO_PROJECT" \
  "$MAESTRO_URL/api/v1/director/pipelines/$PIPELINE_ID"
```

Director music preparation uses `/director/preparation` and its saved
`director_request_id`; `/director/generate-music` consumes that request. YuE2
uses take IDs and its own compose, plan review/continue/cancel, library and audio
operations. Training has a separate job/request identity. Preserve those IDs
and the model terms; do not reinterpret a prepared song as a generation job.

Chat uses `/llm/prepare`, `/llm/chat` and `/llm/chat/{request_id}` with frozen
input and exact session/project identity. Chat attachment uploads are scoped to
that identity. Browser **Stop** stops waiting; there is no public Chat cancel
operation. Conversation history and lyric/draft transfers remain client state,
which an external client may maintain itself. A preparation can need human
terms or model selection before it becomes ready.

On reconnection, use the **same authorized session**, project and saved IDs.
Read current state; do not assume the active project or process is unchanged.
Recovery routes retain their existing revision, owner and capability boundaries.
403/423 indicates missing authority/access; 409 indicates drift/conflict;
422 indicates invalid input/settings; 503 indicates temporary unavailability.
The response's `detail` supplies the operation-specific next step. A timeout is
an observation failure, never cancellation, completion or a fresh-submit grant.

Director runtime preparation can report `error_code: "llm_runtime_unavailable"`
with `failure_details.stage: "model_load"`. Its public message tells the owner
to repair the local chat runtime before starting a new Director run. Detailed
loader diagnostics remain in private server logs. A failed parent is terminal;
do not blindly Continue or resubmit it. A reserved native attempt cannot
silently repair, build or download a replacement runtime.

## Editor, assets and output workflows

List drafts with `GET /projects/{project}/editor/projects` and retrieve by
`/{editor_id}`. Opening from Gallery remains a POST with `output_name` and
`output_revision`. Save the validated timeline using PUT with
`expected_revision`; do not silently overwrite a newer draft. Clip/take/audio/
image operations also carry source and revision identities. Export queues a
saved revision and returns a job ID; originals remain intact.

Gallery reads, metadata and authorized media downloads use `/outputs`,
`/outputs/{name}/metadata` and `/file/{name}?workspace=...`. Preserve the current
revision for operations that require it. Favorites toggle; repeating them is
not idempotent. Bulk moves/privacy/deletion return per-item outcomes. Component
cleanup can be deferred; a response is not proof that every linked file vanished.

References and saved visual styles use project-scoped operations. A saved style
update requires its current revision and selector-based references; do not
submit internal SHA/authority snapshots as a replacement for resolution.
Director's experimental Ref2VA video stage can plan retained image references
for palette, materials and lighting. Image creation/editing stages, other video modes and
camera-motion image borrowing remain text-only. A planned reference is not
proof of native use or visual adherence: inspect the output's saved-style
delivery facts, and review its appearance separately. Previously saved
text-only requests remain text-only after an adapter becomes available.
For an already authored sequence, submit `prepared_clip_plans` to
`POST /api/v1/director/pipeline/start`; those exact plans skip LLM planning and
prompt polishing. Use `auto_mode: false` for prompt and image review, and
`hold_video: true` to keep video children held until explicitly released.
These holds do not authorize model execution or replace resource coordination.
Profiles use their stable create IDs and revision contracts. Recipe application
is a client-side projection into generation settings; remote recipe reads and
local owner mutations keep their existing boundaries. Blender accepts structured
scene/animation/inspection/render and composition packages, never arbitrary
Python, shell commands or unrestricted filesystem paths.

## MCP setup

The general-purpose bridge reads the current HTTP contract and publishes only
explicitly eligible operations, using the official MCP stdio protocol. It has
no direct model, shell or filesystem execution route. It does not create a new
network listener or a new account system.

```json
{
  "mcpServers": {
    "maestro-continuum": {
      "command": "maestro-continuum-mcp",
      "args": ["--url", "http://127.0.0.1:CURRENT_PORT"],
      "env": {"MAESTRO_COOKIE_FILE": "/operator/private/maestro-session.cookies"}
    }
  }
}
```

The operator supplies a session established through the normal flow. Optional
`--origin` sets the exact configured origin, and `--read-only` limits tools to
eligible GET operations. Repeat `--feature` to publish only selected feature
families, such as `--feature editor --feature gallery`; read the current
operation metadata for family names. For LAN/Cloudflare use the configured URL and an
authorized session for that surface; a local cookie is not a cross-host grant.
Never place passwords, provider keys or GPU authority in the MCP config.

Tools list their real path/query/body schemas, boundaries and replay policy.
Outputs are structured `{result:...}` or `{error:...}` with `isError`. Binary
media produces a same-session authorized link. MCP multipart uploads accept up to
16 MiB of base64 media with a direct-child filename; larger files use the SDK's
explicit upload API; MCP does not accept arbitrary host filenames. Authentication,
terms acceptance, owner confirmation and internal recovery authority remain
outside AI tool arguments. Destructive/idempotent hints are conservative and
never override runtime permissions. Reconnect to refresh tools after a release.

For Gallery input reuse, `maestro_gallery_to_upload` downloads one exact authorized
media revision and uploads those retained bytes privately through the existing
HTTP routes. It accepts `workspace`, `filename`, `revision`, and optional
`revision_kind` (`listing_revision` by default, or `content_revision`). It returns
`upload.filename` and does not start generation. Use that filename as the selected
model's input in the same project. The transfer is limited to 16 MiB and is absent
in read-only mode. Larger files use the SDK download/upload recipe in the coverage
map. A lost upload acknowledgement remains uncertain; never repeat the composite
tool automatically. This helper is a client composition, not a separate HTTP
engine or access grant.

## Maintenance and evidence

Authored contracts live beside the source-extracted inventory in
`app/continuum_api`. Update them when a production handler changes. Export the
running `/openapi.json` to `docs/api/openapi.json`, then run
`python scripts/generate_continuum_clients.py --schema docs/api/openapi.json`
to regenerate Python/TypeScript operation references. `--inventory` updates
source-derived fields without replacing authored contracts. No model loads or
test suite are part of generation.

`contract_status:authored`, `source_extracted` and `framework_only` distinguish
semantic review from generated inventory. Coverage and validation gaps are
listed in the feature map. Full feature parity and execution acceptance are
separate claims; this API does not claim native model or human quality acceptance
from an OpenAPI export, a client build or a route count.
