# Continuum feature coverage

Use the [API guide](README.md), [operation reference](operations.md), and current
`/openapi.json`. This map follows shipped actions and their production handlers;
route counts and frontend wrappers do not establish parity. A listed operation
retains its existing project, source-revision, consent, locality and runtime
constraints. API availability is separate from native or human acceptance.

| Shipped action | HTTP operation or supported composition | Concrete boundary |
|---|---|---|
| Discover access, capabilities and current contract | `GET /api/v1/api/discovery`, `/access-context`, `/account/context`, `/models`, `/model-options/{model_type}`, `/defaults/{model_type}` | Filtered to actual session/access. Readiness is capability-specific; installation is insufficient. |
| Generate images, video or audio; select settings, LoRAs and references | `POST /generate`; `GET /generate/submissions/{generation_request_id}` | Public model settings are extensible. Start from current defaults/options. No client-authored admission/provenance/GPU authority. |
| Inspect jobs, progress, outputs and logs; cancel | `GET /jobs`, `/status/{job_id}`, `/jobs/{job_id}/log`, `/outputs`; `POST /cancel/{job_id}` | Studio job identity differs from Director/LLM/preparation IDs. Cancellation acknowledgement is not terminal drain. |
| Reorder, hold, resume and change output count in Studio Queue | `GET /queue`; `POST /queue/{job_id}/priority`, `/hold`, `/resume`, `/start-next`, `/output-count`; `/jobs/queue/start`, `/queue/pause-after-output`, `/queue/resume` | Owner/operation permissions apply. Global release may start other work; prefer the exact owned job. |
| Recover retained Studio jobs, H3 delivery or Blender/Editor composition | `/queue/{job_id}/recovery-resume`, `/recovery-retry`, `/blend-reattach`, `/composition-recovery`; `/jobs/{job_id}/delivery-recovery` and its `/accept-native`, `/retry` | Existing frozen receipts and source bindings control recovery. H3 local control preparation/start remains local owner-only and outside general MCP. Never fabricate an authority object. |
| H3 continuation and cumulative scene plans | `POST /generate` with supported `video_source`; `/generate/plan`; `/generate/{job_id}/plan/approve` | Model-specific continuation and plan review use the existing native paths; approval can start work. |
| H3 still, audio/video and control guides | `POST /h3/gallery-still-guide`, `/h3/gallery-av-guide`, `/h3/gallery-control` | Exact Gallery source revisions; availability/capability gates stay explicit. Missing precomputed controls are unavailable, not silently fabricated. |
| H3 bridge between two cuts | `POST /h3/bridge` | Existing clip normalization, minimum source length and native boundary apply; creates a separate output. |
| H3 estimates, style workflow choices, acceleration and qualification | `POST /h3/estimate`; `GET /h3/style-workflows`, `/h3/evaluation/catalog`, `/h3/acceleration`, `/h3/benchmark`; evaluation preflight | Estimates are not authority. Acceleration probing/qualification and sample-campaign controls are explicit owner diagnostic surfaces, excluded from ordinary MCP. |
| Gallery Extend | Read exact authorized `/file/{filename}`; upload retained source through `/upload`; submit `/generate` with continuation settings | Browser Extend composes existing public production operations; there is no independent Extend engine. See the recipe below. |
| Retake a selected range, including an Editor cut | `POST /retake`, optional returned `editor_origin`; Editor return routes below | New diagnostic clips require the separate consent and fresh native authority already applicable to Retake. No shared exactly-once guarantee if its acknowledgement is lost. |
| Segment a frame and edit selected content | `/segment/preview`, `/segment`; `/edit-anything` | Explicit media/mask input and project/source checks. Maestro does not inspect creative subject matter. |
| Repaint, Recast, Outpaint and Blend; inspect previews | Corresponding `POST /repaint`, `/recast`, `/outpaint`, `/blend` and `/blend/preview` | Public request contracts retain model-specific inputs and source constraints. Preview is not native acceptance. |
| Flip, browser-compatible copy, upscale, Revoice and face refinement | `/tools/hflip`, `/tools/browser-copy`, `/tools/upscale`, `/tools/revoice`, H3 face-refine source/frame/submit routes | Output revision required where defined; CPU transforms and native model tools have distinct resource needs. Speaking-voice/media consent is not supplied by the API. |
| Upload and reuse media; remove unused uploads | `/upload`, `/upload-audio`, `/uploads/{filename}`, `/uploads/audio/{filename}`; authorized deletion | Session/project-owned media only. MCP accepts bounded bytes, no host path or URL acquisition. Browser file picker is presentation. |
| Browse/filter/search Gallery, view/download media | `/outputs`, `/file/{filename}` | Browser playback, scrolling and OS Save As have no server state. Binary MCP results are authorized links. |
| Favorite, edit privacy, move and delete Gallery outputs/components | Existing favorite, metadata/privacy, move/delete and bulk routes under `/outputs` | Favorite is a toggle, not idempotent. Destructive bulk results must be checked per item; source revisions and active use remain controlling. |
| Share outputs, view/regroup/rejoin/remove groups | Output share routes and group routes in the operation reference | Share grants never extend project or owner access. Known compatible groups only; arbitrary edits belong in Editor. |
| Load an output's generation settings; choose Gallery inputs | Read output metadata/recipe plus model defaults; upload/select using public source operations | Applying fields to the current UI draft is browser-local. External clients submit those public settings explicitly. |
| Open an Editor cut; list/read saved cuts | `POST /projects/{project}/editor/projects`; new `GET` collection and `GET /{editor_id}` | Non-destructive, source/revision-bound drafts. Reads refuse stale or unreadable assets; list hides those drafts. |
| Trim, reorder/remove cuts, add titles, audio or image layers; save | `PUT /projects/{project}/editor/projects/{editor_id}`; `/clips`, `/audio`, `/image` | Submit retained project and `expected_revision`; CAS conflict requires reread. Up to eight video cuts and bounded layers. No arbitrary source paths. |
| Add/switch takes; return or dismiss a Retake | Editor `/clips/{clip_id}/takes`, `/clips/{clip_id}/take`, `/retakes`, `/retakes/{job_id}/take`, `/dismiss` | Exact retained source/cut and server-authored Editor origin; unsuccessful attachment can remain in Gallery. |
| Export an Editor timeline | Editor `/exports`; then Studio `/status/{job_id}` and `/file/{filename}` | Export operates on saved revision, retained assets and supported encoder/effects. Preview playback/scrubbing is browser-local. |
| Create/animate/inspect/render Blender scenes and references | `/blender/status`, `/scene`, `/animate`, `/inspect`, `/render` | Structured primitives/materials/camera/keyframes only. Arbitrary Python, filesystem and upstream machine-control surfaces remain unsupported. |
| Plan/finalize Blender Director work; queue Blender/Editor compositions | `/blender/director-plan`, `/blender/director-finalize`, existing project composition routes | Public bounded composition package, project/revision/source authorization and native readiness; no unrestricted scene scripts. |
| Chat with a selected local/explicit remote model; prepare/upload images/read progress | `/llm/models`, `/llm/status`, `/llm/prepare`, `/llm/prepare/{operation_id}`, `/llm/chat`, `/llm/chat/{request_id}`, `/llm/chat-upload` | External provider selection retains existing separate privacy/terms gate. API/MCP do not configure credentials. Browser chat history/drafts are local; Stop waiting is not server cancellation. |
| Enhance prompts, describe an image and apply reviewed H3 rewriting | `/llm/enhance-prompt`, `/llm/describe-image`, `/llm/operations/{operation_kind}/{request_id}` plus `/result`, DELETE cancellation and H3 apply | Scoped operation IDs and pending results must be reconciled. Local refusal recovery is opt-in generated-output-only; corpus/owner confirmation is outside general MCP. |
| Write a song, generate music, mix/analyze audio and suggest clips | `/llm/write-song`, `/director/preparation`, `/director/generate-music`, `/audio/mix`, `/audio/analyze`, `/audio/analyze/status`, `/audio/suggest-clips` | Song editor drafts/reordering are browser-local; generation is separate from preparation. Terms and actual runtime authority remain required. |
| YuE2 compose, review ABC, generate/continue/cancel takes and retrieve audio | `/yue2/status`, `/library`, `/compose`, `/generations`, `/takes/{take_id}/plan`, `/continue`, `/cancel`, `/audio.{fmt}` | Versioned symbolic settings and retained take IDs; review can pause. Subjective duration/ending/quality acceptance remains human. |
| YuE2 training library, submission and cancellation | `/yue2/training`, `/training/{job_id}/cancel` | Training identities/inputs/resource requirements differ from generation; no inference lease reuse. |
| Director preview/planning helpers and model/LoRA preflight | `/director/v2/plan`, `/director/capabilities`, `/director/preflight`, public planning/classification/script routes | Preview LLM operation is not a pipeline. Catalogue compatibility is not native qualification. |
| Director start now, save planning for later, review and continue | `/director/pipeline/start` (`hold_planning`), `/pipeline/{pid}`, `/continue`, `/stop`, `/resume` | Hold registers an empty paused pipeline without planning. Continuing may load models. Manual plan/image review and video holds remain real states. |
| Director queues, editing, reordering, start/pause and removing entries | `/director/queue` and `/queue/{entry_id}`, `/start`, `/pause`, `/reorder` | Request ID plus project-instance scope; held queue is separate from a live pipeline and Studio Queue. |
| Director saved history, tagging, repair, image/video reruns and rejoin | `/director/pipelines`, `/pipelines/{pid}`, clip tag/rerun routes, `/repair`, `/repair/cancel`, `/rejoin`, DELETE | Read/permission checks bind retained sources and parent identity. Uncertain reruns without a caller receipt must be reconciled from history before another submission. |
| List/create/select/lock/unlock/delete projects; manage membership | `/workspaces` and account project/member routes | Passwords, credentials, bootstrap, reauthentication, recovery codes and membership administration remain human/owner operations, excluded from MCP; ordinary project use has public operations. |
| Project reference library: create/edit/delete assets and variants, candidate generation, quad sheet | `/projects/{project}/assets`, variant/reference-authoring/media routes, `/assets/generate`, `/character-sheet/quad` | Project/type/privacy/provenance constraints retained. Browser drag/drop and selection are presentation. No arbitrary file read. |
| Saved visual styles: list/read/create/update/delete/resolve references and select | `/projects/{project}/visual-styles` and item/resolve routes; select public style field in generation/Director | Revision/provenance are server-owned. Image-reference capability stays unavailable until its independent native acceptance; text/style support does not imply it. |
| Generation profiles, presets and recipes | Versioned `/presets` CRUD and `/recipes` list/detail/thumbnail/save-from-output/import/delete | Stable IDs/revision conflicts remain visible. Loading into a UI draft is browser-local; external clients retain and submit the public payload. Remote bundled reads differ from local owner mutations. |
| Model visibility, catalogue, installation/update status and curated download | `/models`, `/model-visibility`, `/models/downloads/status`, `/downloads/active`, model download and checkpoint status/update routes | Human terms prerequisite; assets follow configured storage. Download is not load/ready/accepted execution. |
| LoRA lists/directories/multipliers/previews/parameters and custom acquisition | `/loras`, per-model lists/options/preview and local model/import routes | Relative names and public parameter settings supported. Arbitrary HF/Civitai acquisition, local folder scans, checkpoint verification/deletion and host mutations retain local-owner/provider consent boundaries. |
| Performance profiles, resolution/settings options and auto tune | `/resolutions`, `/system-detect`, `/system-detect/apply`, `/system-config`, `/system-stats`, `/system/preflight` plus model options/defaults | Current supported settings only. Host configuration/auto-tune mutations remain owner/local where enforced. A statistical resource estimate is not an execution grant. |
| Resource unloading / Free Resources | `/system/release-model`, `/system/resource-release` preview/confirmation; LLM unload | Preserve active-work token, exact confirmation fields and recent owner reauthentication. General MCP does not bypass confirmation or stop other work. |
| Storage duplicate scan/reclaim/remove-linked and usage | `/storage/duplicates`, `/reclaim`, `/remove-linked`, `/storage/usage` | Local owner maintenance. Preserve referenced/active/frozen assets; destructive operations are not automatic cleanup authority. |
| Service/provider configuration, runtime share registration, host terms, legal/owner policies and support/research | Corresponding settings/account/support/research operations in the contract | Human/owner administration or server-to-server webhook as specified; no provider keys, paid dispatch, remote code, content moderation or account activation through general MCP. |
| Theme, panel sizes, expanded controls, preview playback, drag/drop, clipboard, notifications | Browser/OS presentation and existing local preferences | No meaningful durable backend operation. Clients implement their own presentation; browser privileges are not HTTP permissions. |

## Gallery Extend recipe

Read the selected authorized output and current model options/defaults. Fetch its
media via `/file/{filename}?workspace=...&listing_revision=...` using the Gallery
revision (or the returned `content_revision` where available), preserve those
exact retained bytes locally,
and upload those bytes through `/upload` using the same authorized session, then
generate in the same project. Use the returned
upload identity as `video_source` in `/generate`, retain the original settings,
and set the public continuation controls accepted by that model:
`image_prompt_type: "V"`, `image_mode: 0`, and a `video_prompt_type` ending in
`"T"`. These are the production UI's continuation inputs, not a new engine.
For a model using sliding windows, requested new-content `video_length` subtracts the model option
`sliding_window_defaults.overlap_default` minus one frame (fallback overlap
default 9). The backend retains the source separately. H3 does not use that sliding-model subtraction. Use the
model's options and H3 plan/estimate to choose its actual supported length.
Pin the source before admission and use one `generation_request_id`; never
silently refetch a different revision after an uncertain submission.

MCP clients can perform the byte transfer with `maestro_gallery_to_upload` for
direct-child Gallery media up to 16 MiB. Supply the exact listed revision; the
tool uses the authorized revision-bound file read and ordinary private upload,
returning the upload identity without model execution. For larger media or nested
filenames, use the explicit SDK download/upload handoff. Binary links alone do
not transfer source bytes. Upload acknowledgement loss has no durable caller-ID
receipt, so preserve uncertainty and do not automatically repeat the transfer.

## Editor save example

Read the saved project, retain its server-authored fields, change only existing
cut decisions, and submit with the revision read. The source must still be
available and unchanged. For example, a Python client can shorten an existing
first cut without forging asset identity:

```python
project_path = f"/api/v1/projects/{workspace}/editor/projects/{editor_id}"
project = maestro.request("GET", project_path)["project"]
revision = project["revision"]
track = next(t for t in project["tracks"] if t["id"] == "video-main")
clip = track["items"][0]
clip["duration"] = max(1 / project["canvas"]["fps"], clip["duration"] / 2)
# The server recalculates sequence starts and retained take state.
result = maestro.request("PUT", project_path,
    body={"expected_revision": revision, "project": project})
```

Retain at least one existing clip. The current production Editor supports one
bounded audio layer, up to eight image layers and up to eight timed titles
(top/center/bottom, at most three lines and 160 characters), with exact source
assets. Unsupported effects/transitions/speed changes fail explicitly rather
than disappearing during export. Adding media uses the dedicated Gallery
source routes; do not patch the assets map to introduce new paths.

## Evidence and remaining limits

Authored contracts describe production behavior; they do not establish that
every model has executed successfully. Current source/host/native/human
acceptance records remain distinct. The API addition does not repeat accepted
samples or activate experimental capabilities.

Beta3 sampler qualification currently needs a field absent from the public H3
evaluation allowlist; this diagnostic is unsupported through the HTTP API.
Storage maintenance has no model-tier move operation; model relocation remains
local owner filesystem maintenance, with referenced assets preserved. Neither
is exposed as an unrestricted MCP fallback.

Some specialized derivative, planning and rerun submissions lack a durable
caller-ID admission receipt. The contract says so. After an uncertain response,
inspect the matching project queue/history; if the original cannot be uniquely
identified, preserve that unresolved attempt for owner resolution. Clients and
MCP never automatically resubmit it. Browser-only presentation and human
credential/terms/confirmation actions are documented above.

The October 11 deployment exercised the running app through the Python client,
the built TypeScript client, curl and the official MCP stdio protocol. Discovery,
access-filtered workspace reads and the served documentation worked. The
TypeScript declarations build, and the existing publication-boundary check
passed. The coordinated restart preserved all 16 retained queue payloads;
local and stable-share health/readiness answered successfully. No routine unit
or integration suites, model execution or accepted-sample replay were performed.

Account enforcement is active on the observed host. Fresh clients correctly
see no private projects until the human establishes an authorized client session.
The signed-in same-origin Swagger browser exercised the scoped Editor collection
and saved timeline reads. One fresh private generation was admitted as held,
reconciled by its original request ID, read through job status, cancelled, and
separately observed terminal with no outputs or model execution. No cookies were
extracted. After a coordinated restart, the same submission ID returned its
retained accepted admission and cancelled job. The terminal job left active
recovery while all 16 pre-existing queue payloads remained unchanged; it was
never resubmitted. That actual Editor response also established numeric Unix timestamps;
the corrected collection and saved-timeline schemas validate those retained
HTTP bodies. The filterable explorer exposes operation IDs and collapsed groups.
The human-only login helper established a separate private session. Python and
Node clients authenticated and read the authorized project and Editor collection;
the official MCP stdio client read that same collection. One authenticated
`maestro_gallery_to_upload` call then read an exact authorized Gallery image
revision and created a private session upload (350,168 bytes), returning its
filename without a path or generation submission. Its exclusive transfer intent
and receipt were retained; it was not repeated. These observations establish the
local client/session and bounded upload handoff, without browser credential
extraction. The Gallery response contract now names the production listing fields
and explains the difference between listing revisions, content revisions and
unpinned convenience URLs. Authenticated LAN/Cloudflare feature acceptance,
native model execution, subjective quality
and Git publication are not established by these discovery observations.
Every mapped action has a production operation, documented composition or
explicit boundary; complete deployed feature parity remains unclaimed while
those acceptance limitations remain.

A fresh private story also exercised Director's genuine `hold_planning` start
and pipeline status through the signed-in owner browser. Its retained parent
survived a coordinated restart, with the frozen request and source bindings
unchanged, `pause_reason:"before_planning"`, no clips or outputs, and an explicit
recovered `continue` action. The other 16 queue payloads remained unchanged.
At that checkpoint, the story had not been released or resubmitted. This journey corrected the
documented workflow to `short_film_story` and established the structured
progress, numeric timestamp and nullable recovery/failure response fields.
It proves held admission and reconnection, not native planning or cancellation
of an executing model.

A later exact-reservation attempt exercised the production Continue operation:
the owner session submitted the retained story once and received HTTP 200 with
`status:"resumed"`. The private supervisor subsequently rejected an incoherent
journal/state observation and drained its owned runtime. Normal Maestro was
restored; the exact pipeline was explicitly stopped through the public API,
which persisted cancellation. A separate status read confirmed `cancelled`,
with no clips or outputs; model status and queue counters were inactive. All
16 unrelated retained queue payloads were preserved. This establishes accepted
continuation, explicit cancellation and recovery observations; it does not
establish completed planning, model execution or output quality. The observer
coherence correction is retained for a future separately authorized attempt.
The durable custody correction is now deployed: interrupted reserved planning
stays blocked without fresh authority, and private custody markers are omitted
from public responses. After that deployment, a fresh status read confirmed
the same cancelled parent remained terminal with no outputs, and all 16
unrelated queue payloads stayed unchanged. This terminal reconnection does not
establish execution or interrupted-marker recovery acceptance.

The successor attempt exercised a fresh held story, recovered Continue and
explicit Stop through the same owner HTTP session. Its corrected supervisor
maintained coherent state observations and fresh authority, but planning waited
before model load because a legacy GPU wait counted retained held/blocked jobs.
Only that owned attempt was cancelled; normal local and stable-share health
returned, cancellation survived reconnection, and all 17 unrelated queue
payloads remained unchanged. No clips, model result or quality acceptance were
produced. The narrow CPU-planning correction resolves the frozen selection
before applying that wait only to native GPU selections; native planning
acceptance remains pending. Public queue summaries are access-filtered and do
not establish global resource idleness.

After deploying that CPU-wait correction, a fresh private story was continued
once through the owner API under a separately validated lease and corrected
supervisor. It reached model preparation, then failed before model load because
llama-server runtime validation rejected both the installed binary and its staged
replacement. The pipeline reports terminal `failed`, no clips or outputs, and
survives reconnection. Original runtime configuration was restored; local and
stable-share health/readiness answered 200 and all 18 unrelated retained queue
payloads were unchanged. The exact lease was withdrawn after the owned runtime
drained. This establishes the continuation/failure/reconnection path, not
completed planning. The next safe action is to repair the owner-managed LLM
runtime, recheck readiness, and use a fresh authorized attempt; do not Continue
or resubmit this failed parent.

A source correction now supplies sibling-library paths to CPU and CUDA runtime
probes and launch, retains bounded private loader diagnostics, and fails closed
before runtime repair/download during a reserved native attempt. Director
exposes the typed `llm_runtime_unavailable` failure with an actionable public
message. After deployment, a fresh separately authorized owner-API Continue
returned 200 and reached CPU llama-server launch without replacing the frozen
runtime assets. The private supervisor then timed out while observing model
loading and drained its owned process. No model-ready response, generated text,
clips or outputs were established. On normal restart, the production API
returned `blocked_planning_admission` with an explicit requirement for fresh
authority, demonstrating interrupted planning custody. Explicit Stop returned
persisted cancellation; a separate status read confirmed `cancelled`. Original
configuration, inactive model state, local/stable-share health/readiness and
all 19 unrelated retained queue payloads were verified after withdrawal.
Completed planning remains unverified; a timeout is never cancellation or
permission to replay the attempt.

The stable-share API viewer and discovery also answered successfully from the
browser. That origin was unauthenticated, so this proves public discovery rather
than authenticated Cloudflare feature acceptance. Establish the existing owner
session on that origin before private workflows; do not copy a localhost session.
