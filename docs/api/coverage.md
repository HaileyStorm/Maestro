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
| Release an image job's generation model after execution | `PUT /jobs/{job_id}/completion-policy`; observe `/status/{job_id}` | Ordinary held, unstarted Studio image jobs only. Exact session/project job ownership and generate permission; policy is separate from frozen request. Cleanup uses the invocation's still-owned native slot; no language-model or machine-wide release. A real Flux2 Klein image job completed and returned a released cleanup receipt; this is generation-model release, not proof that all machine VRAM is free. |
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

Actual local acceptance used the running app through Python, the built Node
client, curl, same-origin Swagger and official MCP stdio. Discovery, authorized
project/Editor reads, served documentation and typed response schemas passed.
TypeScript builds, offline Python/Node packages and the existing publication
boundary passed. No routine unit or integration suites or accepted-sample
replays were run.

The human created a separate owner-private API session through the login helper.
Authenticated Python, Node and MCP project reads passed, and the same session
remained usable after coordinated restart. No browser credentials were extracted.
One revision-bound MCP Gallery handoff transferred 350,168 bytes to a private
session upload without generation. Its original intent and receipt were retained;
no transfer was repeated. Listing revisions, content revisions and convenience
URLs now have explicit contracts.

A held generation was reconciled by its original submission ID, cancelled and
read independently after restart, with no model execution. A separate genuine
Mythwright Flux2 Klein image submission stayed held across restart. Its owner
configured invocation cleanup through the new API, then released that same job
once under fresh development GPU authority. It completed with one PNG and
`completion_cleanup.status: "released"`. Mythwright adopted that output into a
private map copy, and its connected DM edited the copy. This establishes the
ordinary image submission/status/output/cleanup handoff; it does not establish
all-model execution, every downstream feature or subjective image quality.
After another coordinated restart, the same authenticated client reconciled
the original submission as completed with `retained_job: false` and found the
same private PNG in project Gallery. The live job status had expired; no
submission was repeated and no output was regenerated.

Director's real held story admission, recovered Continue, explicit Stop and
terminal reconnection paths were exercised. Frozen inputs and unrelated retained
queue payloads survived coordinated restarts. Later bounded attempts reached
local model preparation and CPU inference but did not complete planning. Runtime
and observation failures, explicit cancellation and interrupted admission remain
distinct; a timeout never cancels or authorizes replay. Current recovery errors
identify the required next action. A later held admission used the Services
default CUDA device and completed actual local Director planning after one
Continue under fresh GPU authority. It paused at prompt review with one clip
plan and no error. After natural LLM release, runtime drain, lease withdrawal
and restoration, the authenticated API returned the same plan and Continue
action. All 22 unrelated retained queue payloads were preserved. This establishes
completed planning and review reconnection; subjective plan quality and its
image/video execution remain unverified. Inspection then found that this
attempt's `story_description` had not reached the planner, which read only
`scene_description`: the resulting car scene did not match the submitted cup
story. The story alias binding is corrected in both planning paths and saved/
queue projections. The retained mismatched plan is failure evidence, not an
accepted story result. A fresh held admission after the fix completed native
CUDA planning under a separate exact GPU lease. The resulting cup, window,
curtain and morning-light plan followed the submitted story and paused for
prompt review without error. After LLM release, bounded runtime drain and
lease withdrawal, normal service was restored. Authenticated reads returned
the same prompts and structured timing, with only empty/default planning
fields added by recovery, and an explicit Continue action. All 23 unrelated
retained queue payloads were unchanged. This establishes the corrected story
input binding and retained review state, not subjective plan quality or
image/video execution. The structured plan is 124 frames at 24 fps; the raw
LLM prompt says eight seconds. Review timing before execution; no encoded
video duration has been verified for this plan.

Two subsequent saved-image reruns used the production authenticated clip
rerun operation under one fresh GPU lease: a creator anchor, followed by a
distinct editor start image referencing that exact anchor. Both returned
successful receipts and committed PNGs with sealed producer metadata. The
cancelled parent stayed cancelled; these were saved-image repairs, not a
completed pipeline or video. After normal-service restoration, authenticated
reads and exact saved-state, media and sidecar hashes confirmed both outputs.
Startup had omitted their state-derived retention metadata and retired their
completed queue rows. The saved producer records and files survived; retired
rows were not recreated and neither image was replayed. The startup retention
correction is deployed: a subsequent real restart rebuilt both retention
entries from the unchanged sealed state, and authenticated history remained
unchanged. All 23 unrelated job identities and saved states survived; eight
Director cursors gained only their saved pause reason. Because the original
child rows were already retired, this establishes metadata reconstruction,
not future native child-row survival. Subjective image quality and video
execution remain distinct acceptance requirements.
The subsequent saved-video recovery update is deployed. Authenticated saved
pipeline reads exposed three bounded repair observations without private request
or custody data, and the two changed live OpenAPI operations matched their
authored contracts. The maintained client references were regenerated from that
fresh live export; the TypeScript client build passed. All 24 retained job
identities, the saved parent and the environment remained unchanged across the
coordinated restart. This establishes the public reconciliation contract and
deployment. The subsequent native journey below establishes the ordinary video
rerun response and execution separately.
Actual model-free preparation then found that an independent H3 clip rerun was
applying its saved parent's whole-pipeline Seamless compatibility check. The
correction clears that flag only in the temporary assessment for an ordinary
independent rerun. Saved settings and H3 scene assessments remain unchanged.
The deployed correction preserved the same 24 retained identities and parent
bytes. The original builder now prepares the sealed-image request as 124 frames
at 24 fps, eight steps and the saved rerun's 1280x720 request. Authorized live
discovery matches its scalar model metadata; nested diagnostic values are
abbreviated, so this is not full registry equivalence or native execution proof.

One ordinary saved-video rerun subsequently completed through the authenticated
production HTTP operation under a fresh exact GPU lease. Its HTTP 200 response
returned the actual child job identifier and output filename. The native H3
sampler completed eight steps and published one sealed video; the saved clip
adopted that output while its parent remained cancelled. After the coordinated
restart, the completed child row retired normally and its saved repair still
retained the completed identity and output. The maintained Python client read
that saved clip, found the output in Gallery, and fetched bytes matching its
producer seal. Media inspection found 124 frames at 24 fps, 5.166667 seconds,
704x704 HEVC video and stereo AAC audio. The saved request was 1280x720: current
native image fitting adopted the square reference canvas. This proves ordinary
rerun execution, adoption and reconnection, not fixed requested dimensions,
H3 scene/image-reference style qualification or subjective quality. The earlier
failed attempt stopped before model admission because the private observer
incorrectly required an ephemeral field in the persisted manifest; its failure
and terminal request remain historical evidence. No failed POST was replayed.
The successful runtime drained, its lease became terminal, normal service and
public restart status were restored, and no routine test suite was run.
Historical attempt receipts remain
retained separately rather than becoming routine regression scenarios.

Local and stable-share health/readiness passed after the coordinated deployment,
and public Cloudflare API discovery worked in the browser. That remote origin was
unauthenticated: private LAN/Cloudflare workflow acceptance remains pending and
requires the existing human session on that origin. Do not copy a localhost
session to another origin. Git publication and human/model-specific acceptance
remain separate from local deployment.

Every mapped action has a production operation, documented composition or
explicit boundary. Complete deployed feature parity remains unclaimed while
these acceptance limitations remain.
