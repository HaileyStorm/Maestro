# Maestro sprint successor handoff — 2026-10-05 UTC

## Owner request and intent

Hailey requested a new task using **GPT-6.1 Sol, High**, a detailed handoff,
an updated native Goal, and retirement of predecessor
`01a0e41b-6205-7e53-a7eb-17a472dde3bc`. Continue the entire authorized sprint.
This rollover is not completion of the backlog. The predecessor retires its
unfinished Goal by pausing it; the successor creates an active Goal with the
complete objective below. Do not copy claims, credentials, browser state,
runtime identity, or old GPU authority.

Use the same verified project checkout and local environment. No new worktree
was requested. Resolve the actual root using `git rev-parse --show-toplevel`.
All repository paths below are relative to that resolved root.

## Updated Goal to create in the successor

> Complete the outstanding Maestro Continuum backlog in continuous sprint
> mode, with progress before paperwork: deliver verified user-visible
> milestones, preserve all existing features and foreign work, reuse fresh
> evidence, and avoid coordination churn. Finish the reviewed Extend alignment
> and duration repair and implement and verify truthful H3 continuation;
> continue the remaining selected intake/model/LoRA/upstream integrations,
> generation reliability across video/image/chat/music, owner resource
> unloading, Music3 first song and recovery, YuE2 duration/listening, and
> separately tracked runtime/browser/media/human acceptance. Start this
> successor by verifying and restoring Maestro through its existing Pinokio
> service because the latest observed app state is offline. Preserve the
> completed native H3 continuation, matched final-head repair, and rotary
> repeatability evidence; do not regenerate accepted samples. The next H3
> diagnostic candidate is a single matched private clip with the pinned rotary
> arithmetic, using the retained seed, inputs, guides, canvas and sampling
> controls, to test whether the observed first-step difference affects the
> opening fade. Do not change production arithmetic or claim a fade/quality
> repair from the prediction comparison alone. Use the installed
> gpu-coordinator-client skill for every GPU/model workload: request a fresh
> exact checkout-bound grant, validate coherent authority before use and at
> no more than ten-second intervals during long work, unload/stop the owned
> workload, and withdraw the exact grant. Idle hardware, registration, or old
> receipts are not authority. Keep Maestro and stable access running when
> practical; restart only through Pinokio's coordinated status/restart flow,
> never reboot. Preserve the historical tracker hold and foreign reservations;
> reconcile current tracker routing through the read-only activation audit and
> controlling policy before any lifecycle mutation, using the approved
> Coordination fallback queue while the hold remains. Test proportionally:
> Hailey asked to run full suites less often. Reuse revision-bound accepted
> evidence and broaden checks only for changed behavior, failures, unresolved
> risk or a required acceptance gate. Keep experiments, failures, deferrals,
> action-time Music3 consent and owner whole-clip/listening acceptance explicit.
> Do not mark the broad Goal complete from tests, catalog rows, one sample,
> numerical parity or disposition coverage. Continue until the full requested
> result is verified or the owner explicitly stops the work.

## Immediate stopping state

- `main` HEAD: `adde27d7e4d09ebdfed0e12341d1fd778bf74d69`.
- One physical worktree, on host `hailey-ubu`. No additional checkout created.
- Origin: `https://github.com/HaileyStorm/Maestro.git`; upstream:
  `https://github.com/Blizaine/Maestro.git`.
- No production edits, commit, Git index/ref mutation, or Beads lifecycle
  mutation during the latest rotary diagnostic/recovery turns.
- The four-forward probe is terminal, child exited with return code zero,
  model release ran, old request is withdrawn/denied. No scheduler advance,
  decode, media publication or server launch occurred in that probe.
- Fresh `pterm status Maestro.git --probe --timeout=5000` at rollover found
  **offline**, no running scripts and no `ready_url`. The user systemd unit
  `maestro-continuum` is inactive. Earlier local/stable 200 receipts are
  historical, not current runtime proof. The predecessor has not started a
  replacement service or the next changed-arithmetic clip.
- No live child agents remain in the predecessor team. Older names in app
  context are not ownership or live process evidence.
- Rollover receipts are in `.artifacts-temp/maestro-rollover-20261005/`.
  Read `prior-claims-released.json`, final release/retirement receipts and
  successor metadata there. All predecessor claims must be absent before
  successor mutation; verify independently.

## Startup and ownership

Read current `AGENTS.md`, `~/.codex/AGENTS.md`, applicable
`~/.codex/harness-operations.md`, `CONTRIBUTING.md`, and the relevant current
sections of `docs/operations/CONTINUATION.md` and `FRESH_THREAD_HANDOFF.md`.
The owner replaced the AGENTS instructions during the interruption. Current
files and explicit owner choices control; old snapshots are historical.

The shared sentinel is
`~/Documents/ChatGPT/Coordination/universal-harness/shared-tools/working-sentinel/working_sentinel.py`.
Use its supported exact claims and release flow. Claims bind human, task,
session, host and physical workspace. The predecessor's workspace instance is
`maestro-main-5272a93`; its exact bindings are retained in private rollover
receipts. Matching path, root, commit or human does not transfer ownership.
Take fresh successor claims with the successor's real task/session identity.
Never alter `CODEX_THREAD_ID`, hand-edit markers/registry state, or recreate
retired `.working.registry.lock` files.

The final census found two predecessor claims before handoff preparation:
`maestro-h3-rotary-repeatability-20261003` and
`maestro-h3-live-execution-20261003`. Both were released using the supported
tool, with receipts. Handoff and rollover-receipt claims are separately
released before successor creation. There is no deliberately retained
unfinished predecessor claim and no takeover is needed.

Preserve this foreign dirt exactly:

- `AGENTS.md` (15 additions/8 removals), storage-janitor source/test files;
- existing `.artifacts-temp`, `.beads-cutover-*`, `.beads.gate.lock`,
  `.gpu-coord`, legacy `.working` state, delegations/recovery/takeovers;
- model/output/upload/LoRA links, `ckpts`, temporary WGP config file;
- foreign `docs/operations/MAESTRO_SOL_HANDOFF_2026-09-27.md`;
- existing registry locks under docs, cloudflare, tests and UI.

Do not reset, clean, stash, broadly stage, delete artifacts by age, or kill
foreign processes. The new handoff file itself is owner-requested and not
committed; preserve it and include it in later authorized serial closure.

### Tracker distinction

Latest read-only activation audit reports installed `bd 1.3.1`, an embedded
local Dolt route, database `Maestro_git`, route status `ok`, and no mutation.
Its receipt is `activation-audit.json` in the rollover directory. That static
route differs from the historical SQLite language retained in CONTRIBUTING,
earlier handoffs and the active objective. The startup hook also reported a
deterministic migration. **Neither is a reviewed handover of the existing
historical hold.** Do not migrate, initialize, sync, install hooks, push Dolt,
or mutate issues merely to tidy rollover. Before future lifecycle work,
reconcile native history/route, current project policy and migration receipts
under the supported shared contract. Preserve historical records throughout.
Existing fallback item: `UH-20260927-MAESTRO-EXTEND-FLAGS` in Coordination.

## Runtime restoration — first successor action

The existing user service launches through Pinokio `start.js`, bound to
`pinokio.service`. Inspect actual control-plane state and unit before use.
If the app is offline and no explicit owner stop supersedes this Goal:

```bash
systemctl --user start pinokio
systemctl --user start maestro-continuum
pterm status Maestro.git --probe --timeout=5000
```

Use the installed Pinokio `pterm` command/environment. The verified installed
unit runs `pterm start start.js` through loopback Pinokio. Do not run `wgp.py`,
`launch.py`, an AppImage, or another server as an agent-owned shell.
Discover the new `ready_url`; do not reuse 42003/42004 from old records.
Probe `/health`, then `/ready`, then the configured stable share:
`https://maestro-stable-share.stable-share-worker.workers.dev`.
For stable HTTP checks prefer curl; an earlier Python urllib 403 was a
transport-specific negative, not a proven outage.

For an actual coordinated bounce use existing `restart.js`, preceded by the
`app/scripts/restart_status.py` protocol and followed by exact-generation clear
only after intended local and stable readiness. A Start of an offline app is
distinct from Restart of a ready app. Never restart the computer.

`logs/api/start.js/latest` and bounded shell log tails are useful. A historical
start.js event log is several GB: never broad-scan it or single-line records.

## H3: accepted facts and preserved failures

### Extend and continuation

The source-prefix/finishing work is already pushed. Trailing alignment `T`
and internal Temporal Depth `T` are separate concepts. Preserve ordinary
Extend behavior, copy-on-write parent protection, schema-2 publication and
visible `Finishing output` while post-generation delivery remains active.
An old cancelled Extend downloaded unintended Video Depth Anything; the flag
interpretation was corrected. Do not repeat that mistake or use the old
failure as current success evidence.

The canonical cumulative implementation/evidence history is
`docs/operations/MAESTRO_H3_CUMULATIVE_APPEND_2026-10-02.md`.
It includes CPU geometry/latent invariants, private native capture and restore,
durable checkpoint/binding/finality, streaming decode/publication, experimental
HTTP/queue admission and native live recovery. Retained records include:

- standalone full-canvas fresh-process restore, 124→141 frames at 1344×768;
  second sampler window 39 frames, delivered suffix 17; exact retained AV
  latent prefixes and equal binding payloads;
- owner queue job `65ce82f1499e4220b1aa115f15b3f513`, actual retry recovery
  and two-window completion under source `1372044` (doc `fe0932d`);
- final HEVC 141 frames/24 FPS/5.875 s with 32 kHz stereo AAC 5.856 s;
  Chrome H.264 copy reached its end without video error; original HEVC remained
  unchanged when making that browser copy;
- the owner-run sample's audio was nearly silent (about −72.2 dBFS true peak),
  and listening/quality acceptance remains open. A separate standalone sample
  had about −22.6 dBTP; do not merge these different samples' measurements.

No claim of arbitrary multi-window quality, general reliability, decoded-pixel
prefix identity, broad native playback or human approval follows. Re-decoding
the exact retained latents can change decoded prefix pixels. Keep latent,
compressed-stream, decoded-pixel and perceptual continuity claims distinct.

Private historical execution details:
`.artifacts-temp/maestro-h3-live-execution-20261003/intent.json` and
`RECOVERY_CHECKPOINT.md`. Its final request05 is withdrawn; no live authority
passes to the successor. The owner password confirmation and actual
Free Resources UI unload/queue-warning acceptance were pending. Do not enter
owner credentials or recovery codes for the user.

### Matched Dense final-head precision repair

`dd1346b` repaired demonstrated compact-head precision: normalize the backbone,
then perform modulation/projection in FP32 with bounded selected rows/chunks
of 8192. Preserve full-width behavior, hooks and fresh Spectrum rows.
Accepted checks: 27 focused CPU precision/regression checks plus 36 retained
reference/chunk comparisons. Reuse them for unchanged bytes.

The repaired matched native job `e6a2bc37046842a782692414f8f79126` completed in
724 s. Full CPU decode passed 124 HEVC frames at 1344×768/24 FPS and stereo
32 kHz AAC. Video 5.166667 s, audio 5.152 s; output SHA-256:
`7f35800f6ffb10d74bd9ee446a7bc1eb420d31b16225c5cdb9e0c69595188e69`.
Frame 0 mean RGB was zero; frames 0–2 were ≥99% near-black. The opening fade
persisted. The repair is numerical correctness work, not a demonstrated fade
or quality fix. The prior matched Dense output SHA is
`666dde22846503af3acf930a704e989297b119b503db0b0c3d84195582e9b7dd`.

Canonical public-safe summary:
`docs/operations/MAESTRO_H3_DECODE_EVIDENCE_2026-10-02.md`, section
“Matched native result after compact final-head repair.” Private manifests,
seals, media metrics and cleanup are in
`.artifacts-temp/maestro-h3-final-head-native-20261003/`.

### Rotary diagnosis — latest coherent unit

Pinned WanGP reference commit:
`fa79896eadbcb048dc13e76233b3b72486b522a8`.
Private source `reference_wangp.py` in
`.artifacts-temp/maestro-h3-transformer-parity-20261003/` has SHA-256
`d95180065a55ec861c3f5881d7be98d93b1988ab55eca88843d8fa8f6a2f33a8`.

18 synthetic CPU configurations (three dtypes, three seeds, two chunk sizes)
isolated rotary low-precision rounding. BF16/FP16 nonrotary attention and
refiner are exact against common pinned weights; substituting pinned rotary
operation makes synthetic attention/blocks exact. FP32 differences <1e−6.
See `.artifacts-temp/maestro-h3-block-parity-20261003/`.

Native `_apply_rope` uses separate products/add; the reference clones the
tensor and uses split-half `mul_` followed by `addcmul_` on the first and second
halves. Same real mathematical rotation, different low-precision rounding.
No production rotary change has been made.

The successful two-forward learned probe is
`.artifacts-temp/maestro-h3-rotary-prediction-20261003-03/`.
The subsequent repeatability control is
`.artifacts-temp/maestro-h3-rotary-repeatability-20261003/`:

1. current arithmetic;
2. current arithmetic again;
3. pinned rotary operation;
4. current arithmetic after the pinned operation.

All eight sealed input tensor digests remain unchanged after every prediction;
CPU/CUDA RNG is restored for each evaluation; native Dense SDPA, loaded model
binding and actual effective offload profile are verified. Repeated current
video/audio predictions are exactly equal to the first, including after the
pinned variant. The pinned variant reproduces the earlier delta:

| First-step target velocity difference | RMS | Maximum absolute |
| --- | ---: | ---: |
| Video | 0.1303591132 | 1.0559635162 |
| Audio | 0.0317601971 | 0.1752889752 |

These are prediction-tensor units, not pixel brightness or audio quality.
Each forward took about 20–21 s in this sample. Per-forward Torch allocation
observations do not prove total VRAM, admission limits or repeatable throughput.
No scheduler advanced and no clip was encoded. The worker restored hooks,
released its model and exited zero. Exact requests were withdrawn and fresh
coherent validation denies them.

Interpretation: rotary arithmetic explains this reproducible first-step delta
in this controlled run. It does **not** establish the cause of the opening
fade, whole-path reference parity, a trained-checkpoint defect or a quality fix.
A changed-arithmetic matched clip is the selected next experiment, with
production behavior preserved until its evidence is reviewed.

#### Probe failures already resolved

- Attempt01 expected requested profile 1 at the load observer and stopped
  before prediction. Native 1344-long-edge standing floor is profile 5 in
  `app/services/h3_oom_relief.py`; do not conflate requested and effective
  profiles. Attempt02/03 directly observed effective 5. The old public recipe
  label “profile 1” describes request metadata, not a fresh effective-profile
  observation for every historical clip.
- WGP deliberately swallows advisory observer exceptions. Required load proof
  is asserted before predict. Reset/implicit OOM retry clears the witness;
  retries are rejected by this sealed comparison probe.
- Attempt02 encountered empty closure cells belonging to unused Spectrum
  state. `named_closure_values` reads only required Dense inputs; a CPU fixture
  extracted from the actual native prediction function reproduces that error
  and verifies the fix. Attempt03 passed six focused CPU guard/closure checks.
- Child cleanup uses exact pidfds, not `Popen.poll` from a fork that is not the
  child's parent. Independent watchdog heartbeat timeout is eight seconds;
  coherent lease cadence is five seconds. Linux parent-death SIGKILL is armed
  before model/CUDA import. Do not weaken these guards when adapting the probe.

### Other accepted negatives — avoid redundant generations

- 916 CPU denoise/RNG/scheduler/paired-advance comparisons were exact; synthetic
  evidence only (`maestro-h3-denoise-parity-20261003`).
- Learned streamed/buffered VAE decoding of the retained 124-frame state was
  pixel-identical. Raw VAE output already had the dark opening, before pixel
  conversion, overlap or encoding. Bright robot round-trip starts bright.
- Repeating latent0 is black; repeating latent10 is bright. Another seed
  starting bright does not remedy the original sealed seed.
- AV boundary ON worsened the matched seam; keep the accepted OFF control.
- Pinned WanGP first/last guide routing is not an oracle for arbitrary interior
  guides at 31/90. Do not silently map every nonzero guide to last frame.

## Exact controls for the next changed-arithmetic clip

Read `request-manifest.json` in the final-head native artifact tree rather than
retyping settings. Retain Base FL2VA `minimax_h3`, the installed scaled-FP8
pruned checkpoint, seed `935314058`, 1344×768, 124 frames, 28 evaluations,
guides at frames 31/90 in original order, explicit Dense SDPA, AV boundary OFF,
no LoRA, requested profile 1 with observed native floor 5. No prompt enhancer.
The retained prompt describes the red ceramic robot/yellow triangle transitioning
to a green glass marble/red paper triangle; the exact string is in the manifest.

Robot PNG SHA:
`e70c767867b83405fb1bc3ec8f34cdd3e1ba1a96b9dfc72acbd2cab672d4f59c`;
350168 bytes, 848×480. Marble PNG SHA:
`65872c808bbc34fda31f06af967795181b42dfd44aecaca1707dd8a645a903a7`;
270238 bytes, 672×672. Sidecars are same-stem `.meta.json`, not `.png.meta.json`.
Physical paths and sidecar seals are in the retained private checkpoint.

Build on the inspected installed-only setup in
`maestro-h3-full-canvas-20261003/probe.py` and reviewed supervisor in
`maestro-h3-final-head-native-20261003/lease_guard.py`, as the latest probes do.
Standalone library execution is not launching a replacement server and is not
authenticated queue/browser acceptance. Preserve model terms, twelve installed
asset checks, no-download policy, offline HF flags and private output roots.
The copied private config is mode 0600 and must stay untracked.

Do not mutate or copy old `.working`, leases or generated attempt state.
Use a fresh private tree and request. The old prediction guard uses 600 s work
plus 120 s stop; a full 28-step clip needs an honestly larger budget and fresh
grant. The last full matched clip took 724 s excluding unrelated overhead.
Restore private hooks/unload/exit and withdraw before claiming completion.

GPU project ID is exact `maestro-local`, registered to this physical checkout.
Use installed `gpu-coordinator-client` skill and its current instructions.
Request/poll ACK, stale grant, callback and registration are not authority.
Validate the coherent bundle for full runtime plus stop margin before start,
at ≤10-second intervals throughout, and before each restart. Do not print raw
`gpu-coord status`: historical ledger output is enormous. Use compact `queue`
or bounded JSON projection. No remote compute/provider route is implied.

Next diagnostic acceptance: exact recipe/input/source seals, directly observed
loaded/effective-profile witness, one completed changed-arithmetic clip, full
CPU decode and actual media dimensions/durations, comparable first-25-frame
brightness/darkness metrics and sampled frames, old input/output bytes intact,
owned child stopped and grant withdrawn, local/stable runtime restored if
affected. Report positive or negative fade result; keep human listening and
whole-clip quality separate.

## Remaining whole-sprint lanes

Do not let the H3 experiment replace the objective. Source-of-truth intake
records are `docs/research/intake-reconciliation-2026-09-22.md` and
`intake-decision-crosswalk-2026-09-25.json`; runtime matrix is
`docs/operations/GPU_ACCEPTANCE.md`. Refresh concrete remaining items against
current source and retained receipts before choosing work. Candidate dumps are
not install/build lists; follow `docs/operations/INTAKE.md`.

1. Restore usable app/local/stable access; owner resource-unload action still
   needs actual recent-auth/queue-warning/load-release acceptance. The owner
   personally enters credentials; no browser session transfer on rollover.
2. Complete truthful H3 continuation qualification and remaining quality/audio
   work. Preserve accepted live recovery and successful outputs; target actual
   unresolved failures and meaningful parameter changes.
3. YuE2: structural score timing and produced audio duration are separate.
   Keep `nominal_score_seconds` and actual media duration truthful; listening
   acceptance remains separate. Do not promise model output length from score.
4. Music3: first song/recovery remains gated on explicit action-time owner
   consent for license/source acceptance and approximately 37 GB native assets.
   A pending question, prior intent or another interface is not consent. Do
   not accept terms, download or initialize native Music3 without that gate.
5. Finish selected model/LoRA/upstream integrations and concrete video/image/
   chat/music reliability negatives. Catalog registration, mocked tests or one
   sample do not establish execution or universal reliability.
6. Retain owner whole-video/listening decisions, true >15-second generation and
   broad playback/quality requirements where still unverified. Do not close
   merely from earlier shorter samples or source coverage.

Preserve local content neutrality: no Maestro creative-subject scanning,
moderation, heuristic blocking or third-party classification of local content.
Keep authorization, filesystem, resource, privacy, provider/locality and input
shape safeguards. LAN-origin authorized users receive local-equivalent
capabilities except explicit concrete security/browser boundaries.

## Verification and retirement rules

Hailey asked to **run full suites less often**. No full suite was repeated in
the latest rotary turns. Reuse unchanged precision/guard evidence; run focused
checks for a real change, failure, new input or unresolved risk. A full gate
belongs to a justified release/completion boundary, not every exploratory unit.
Do not claim old cancelled full gates passed.

No completion claim is made for the full sprint. The successor must retain the
active full Goal, a compact current worklog and clear per-lane evidence levels.
The predecessor releases all owned claims, pauses its Goal for the requested
retirement, then creates the explicit Sol/High successor. Successor identity,
native Goal observation and retirement receipts are private rollover evidence.
The predecessor is archived after dispatch; it does not continue experiments
or send routine coordination messages.

The Shared Memory packet was cached/stale and the native memory tool schema
was unavailable at rollover. Do not treat that as empty memory or a fresh
recall. Existing project artifacts carry the exact work evidence. Preserve
legacy memory originals and current cutover controls; do not change memory
stores, credentials or provider configuration to make startup look complete.

## October 5 Stop and Revoice delivery checkpoint

Commit `1d671554` preserves the full video tail when replacement audio is
shorter and reports actual Revoice chunk/step activity. The focused backend
gate passed 221 tests. A retained native HTTP run produced all 350 source
video packets with identical ordered timestamps, sizes and data hashes; full
CPU decoding passed. This is output-preservation evidence, not listening
acceptance. The original inputs remain unchanged.

That run completed before the UI Stop click. The cancellation endpoint returned
HTTP 200, but its response body was unavailable; this does not prove that
cancellation won. During collection, the guardian's controller heartbeat
expired and stopped the exact owned workload before withdrawing its request.
Normal Pinokio service and stable access were restored and verified. Preserve
the failed guardian receipt and terminal request; do not replay this attempt.
Native HTTP Stop qualification remains open.

Commit `3574ff02` keeps the job card, terminal waiter and canonical status
observation after Stop, including while the cancellation POST is pending or
its acknowledgement is lost. Repeated pending clicks coalesce without an
automatic POST retry. Account, project and job-incarnation fences reject stale
results. H3 cancellation also follows the actual terminal winner instead of
manufacturing cancellation from HTTP 200. The applicable UI gate passed 111
tests, TypeScript, scoped lint/build and six Firefox/Chromium browser checks;
independent final review found no remaining issues.

Commit `81fd38c1` shows completed Revoice source and timing records in Gallery's
existing Finishing details section. The section requires a completed sidecar
record and private reveal, exposes only the source basename, and omits unrelated
upscale settings. Fourteen private-preview tests, TypeScript, scoped lint/build
and the publication guard passed. The signed-in local Gallery showed the
retained output's result, source, recorded timestamp and seven-second job time;
re-hiding the preview hid the section. Media bytes remained unchanged.

Both UI releases were promoted atomically while retaining earlier hashed assets
for open clients. Fresh Pinokio discovery, local and stable health/readiness,
exact served assets and an empty public restart notice were verified. No backend
restart was needed for either UI release. Owned source and promotion claims were
released; foreign work and the historical tracker hold remain preserved.
These milestones do not complete the full sprint, human quality/listening,
remote signed-in viewing, Windows acceptance or native HTTP cancellation.

## October 5 YuE2 submission recovery checkpoint

Commit `f381008ad3b3c648ed6f2df5cba4d86c76ba6a4d` retains an exact frozen
YuE2 submission before dispatch. A lost, invalid or uncertain acknowledgement
keeps that request pending. Project-library reconciliation can adopt its exact
matching take; an explicit retry reuses the original request ID and settings,
even after edits or reopening the composer. Absent library evidence does not
prove rejection. Unresolved requests are bounded without eviction, and account
changes clear their memory and fence stale callbacks.

Canonical library acceptance now releases the exact submission's busy state
while its POST is pending. Late responses cannot overwrite that accepted take
or a newer submission. Independent review is clean; 42 focused UI checks and
six synthetic browser cases across desktop Firefox and Android-like Chromium
pass, along with TypeScript, scoped lint, private build and publication guard.
Before-fix controls reproduced the lost-acknowledgement and GET-adoption races.

The UI build was promoted without a backend restart. Fresh local and stable
health/readiness and identical served asset bytes were verified. The signed-in
local music composer opened its existing project library without submitting
another generation. This is browser/readiness evidence, not native recovery or
listening acceptance. Pending submissions survive SPA navigation only;
reloading the page clears them. Native YuE2 duration/listening, Music3 consent,
owner resource unloading, H3 quality, HTTP Stop, Windows and other remaining
whole-sprint lanes remain open. The full continuous Goal stays active.

## October 5 YuE2 training recovery checkpoint

Commit `28c930119dccda381cbdeb81715cc31eccb8cd30` confines pending training
requests to their account and project. Account changes prune private frozen
inputs even while the panel is unmounted; old callbacks cannot start status
requests or alter a newer account's attempts. Same-account explicit retries
retain the original request ID and inputs, including a still-pending POST
after leaving and reopening the panel. A later validation rejection cannot
discard an already uncertain request. Exact canonical job confirmation clears
the stale uncertainty alert and busy state, and fences the late POST.

Independent review is clean. Fifty-nine focused checks, TypeScript, scoped
lint, private build, publication guard and eight synthetic browser cases
across desktop Firefox and Android-like Chromium pass. Before-fix controls
reproduced the account/late-response gaps and stale alert. Browser fixture
failures and corrections remain in private evidence; the final run is green.
The change covers SPA memory, not reload recovery. Synthetic account switching
is distinct from live owner authentication and native training acceptance.

Fresh local and stable health/readiness and exact served assets were verified
after atomic UI promotion. The signed-in local training panel shows the
existing private project checkpoints, empty fields and disabled empty-submit
control. No new training/generation or backend restart was performed.
Foreign changes and the historical tracker hold remain preserved. Ordinary
Image/Video submission recovery was the next source-proven reliability gap;
its delivery is recorded below. Full sprint acceptance and the other previously
recorded runtime/human gates remain open.

## October 5 Studio submission recovery checkpoint

Commit `ad7eb0b7ff94d4aefda30be574e1f5e8c552dc78` adds an optional canonical
UUID4 `generation_request_id` to ordinary Image/Video generation. The server
authorizes the project, binds account/session and immutable project identity,
and durably reserves the exact original settings before staging, charging or
worker admission. Exact replay adopts the same job; a pending reservation does
not rerun those effects. Raw settings remain in the existing private manifest.
Terminal receipts survive journal compaction, job removal and restart. Legacy
callers without the new field retain their existing behavior.

Read-only lookup uses
`GET /api/v1/generate/submissions/{generation_request_id}?workspace=...`.
Accepted results include canonical job status; pending or unknown results do
not prove rejection. Only an exact initial pre-reservation rejection releases
the UI's request. The visible pending notice survives closing the composer;
Check reads status, and explicit Retry sends the original frozen wire body.
Form edits are reserved for a later submission. Account changes and project
round trips fence stale responses, and canonical acceptance preserves newer
terminal, review and held states. Completed recovery refreshes Gallery with
the same account/project fence. Frontend persistence covers SPA navigation;
reloading the page clears its in-memory request.

The durable content-free ledger has a hard limit of 4096 records and 2 MiB;
it never evicts identities. Existing requests remain readable/replayable at
capacity, while new admission is rejected before reservation. Unresolved
reservations protect their project recovery inputs and remain non-executable.
History removal or manual reconciliation needs a separate controlled contract.

The final affected gates passed 204 launch/recovery tests, 22 adapter tests,
39 UI handler tests, 41 existing UI fixture tests, TypeScript, scoped lint,
private build, publication guard and eight synthetic desktop Firefox/mobile
Chromium cases. Independent review of the final source bindings is clear.
Real journal and manifest checks are distinct from substituted registry,
credit and worker boundaries. Before-fix failures and corrected fixture
attempts remain in private evidence.

The backend restarted through Pinokio's coordinated public-status flow before
atomic UI promotion. Fresh local and stable health/readiness, the installed
protected route, exact served asset bytes and an empty restart notice were
verified. The real signed-in Chrome Studio shows the existing Gallery, idle
queue and available Generate/Hold controls; no new generation was submitted.
This is rollout/browser evidence, not native lost-acknowledgement, Windows or
human acceptance. The full continuous Goal and its remaining gates stay open.

## October 5 Chat response recovery checkpoint

Commit `f5489a5e73976fbfaa7a9a92884d4eeef88820b9` validates successful Chat
submission and status responses against the original normalized request ID
and operation schema before accepting text or acknowledgement callbacks.
Truncated JSON, a foreign request ID, or malformed status remains uncertain.
Recovery and Resume read the original scoped operation; a missing status never
automatically sends another turn or creates a new request ID. The submitted
message remains visible and Send stays locked while Resume is available.

Resume no longer deletes uploads to infer whether admission occurred. An
uncertain attempted submission retains its image references; a definitive
initial rejection still permits cleanup and branch rollback. Validated failed
operations retain their terminal behavior. The explicit upload-cleanup API
remains compatible, while its obsolete reload-recovery test and unused special
polling branch have been retired.

The affected checks passed nine actual-client/cleanup tests, 42 existing
source/runtime tests, 39 adjacent Studio client tests, TypeScript, scoped lint,
private build and publication guard. Six synthetic desktop Firefox/mobile
Chromium checks cover truncated acceptance, foreign/missing status, explicit
Resume and browser reload with one original POST. Independent final source
review is clear, and the before-fix failure is retained in private evidence.

The UI was promoted atomically without a backend restart. Fresh local/stable
health and exact asset bytes passed. Browser reload evidence uses a synthetic
persistent operation table: the real backend table remains bounded process
memory and can expire or disappear after restart. This change cannot recover
an absent result or prove native lost-response recovery, model quality,
Windows or human acceptance. The full continuous Goal remains open.

## October 5 native Chat response recovery acceptance

The released Chat recovery now has one native local acceptance run. The
already-installed Gemma 4 31B Heretic ARA Q4_K_M loaded on CUDA under a fresh,
coherently validated lease. In the actual Chrome Chat view, one ordinary Send
was admitted with HTTP 202. The controlled test tab then received truncated
acknowledgement JSON. Chat retrieved the original normalized request ID with
GET and displayed one assistant response, `Ready.`, without another POST.
The original acknowledgement, canonical completed result, request counts,
runtime log, visible transcript and screenshot are retained privately.

The independent systemd guardian checked coherent authority every three
seconds, with an eight-second watchdog and independent cleanup. Source review
and eleven CPU cleanup boundaries preceded execution; a disposable CPU browser
probe also verified response replacement and removal of interception. The
native model unloaded through its existing idle timeout. The guardian then
confirmed explicit unloaded/not-loading status, no download and no live owned
llama process before withdrawing the exact lease. Maestro stayed healthy and
ready without a restart; the completed runtime claim is released.

This verifies the selected native lost-acknowledgement response path. It does
not establish backend-restart recovery, native pending-request reload,
Windows, general model quality, or owner Free Resources acceptance. Source
code did not change, so the revision-bound checks above remain applicable.
The full continuous Goal remains open.

## October 5 native Chat page-reload recovery acceptance

The released Chat recovery also passed one native page-reload case with the
existing installed Gemma 4 31B Heretic ARA Q4_K_M model. One new short turn
received a real HTTP 202 acknowledgement. The controlled browser exposed
truncated JSON for that acknowledgement and the first original-request GET,
leaving the visible turn pending with **Resume wait** available and Send
disabled. The original backend operation had completed.

After clearing response interception, the actual page was reloaded. Opening
Chat restored the pending turn and automatically read that original request.
Its completed result, `Reloaded.`, appeared once. The complete event capture
contains one Chat POST and two GETs for the same request ID; no second POST
or new request was sent during reload or recovery.

The native model loaded on CUDA and then unloaded through its idle timeout.
The independent guardian performed 55 coherent lease checks, confirmed cold
model state and process cleanup, and withdrew the exact grant. The guardian
finished successfully; Maestro remained healthy and ready without a restart.

This verifies persistence and recovery across one Chrome page reload with a
completed native text turn. It does not establish recovery after a backend
restart, an interrupted generation, image-upload recovery, Windows, owner
Free Resources acceptance or human model-quality approval. Unchanged source
checks are reused from the released recovery revision; no source was changed.
The full continuous Goal and other recorded acceptance gates remain open.
