# Maestro sprint rollover — 2026-09-27 UTC

This checkpoint transfers the unfinished Maestro intake and live-acceptance work
from Codex task `01a0a212-ff22-7300-a0d6-06a55262c744`. The owner is restarting
the Codex app. The successor should **acknowledge this handoff, create its Goal,
and pause that Goal without doing further work**. Resume only when the owner asks.
After resumption, work in sprint mode: deliver the smallest verified useful
milestone before expanding coordination or paperwork.

## Source and service checkpoint

- `main` and `origin/main` matched at `1eb2818e48f6c3d26bb22fa766d7c4fbe00bc694`
  before this handoff document. `6e18d10` delivered an opt-in Gallery action that
  creates a browser-compatible H.264/AAC copy of an existing authorized video,
  preserving the source. `1eb2818` streamlined the test suite. Verify the final
  handoff commit and remote equality afresh after resume.
- The browser-copy feature passed the full local CI gate before the test-cleanup
  commit: 5,430 backend tests (17 skipped), five JSON-grammar tests, 747 UI tests,
  type-check and production build. The cleanup agent then passed focused grammar
  discovery (5), Blend (31), and CI-contract (7) checks. **Run the full gate on
  the combined final revision after resume**; no post-cleanup full gate is
  claimed here.
- A signed-in stable-share Gallery action copied a real H3 Guide output. The
  separate output decoded as H.264/yuv420p plus AAC at 1344×768 for 5.166667 s;
  its source remained unchanged. The in-app browser loaded visible video frames
  and played them by keyboard. This is one live browser-video check, not an
  audible-sound, broad compatibility, generated-content quality, or human
  acceptance claim. See `GPU_ACCEPTANCE.md` for the exact dated receipt.
- Maestro was restarted through Pinokio `restart.js` and left running through
  `start.js`; the public restart notice was cleared. The then-current direct and
  stable `/health` and `/ready` probes returned 200, stable root returned 200,
  and the stable share's direct fallback redirected to the current tunnel.
  Rediscover the `ready_url` and verify each surface anew; do not reuse a saved
  port or treat this record as post-app-restart runtime proof. Keep Maestro and
  stable Cloudflare access running whenever practical.
- No GPU work ran for the browser-copy milestone. Its CPU encoding path needed
  no grant. The pending GPU request was still `gpu_busy` with no lease at this
  checkpoint. For later GPU work, use a fresh exact coordinator grant, normally
  sized to the needed 10–30 minutes, validate the current outbox and bindings,
  and withdraw it when done. User permission for GPU work already exists.
- A verified unused runtime installer archive was removed, recovering about
  367 MB. The system disk remains tight; preserve installed Maestro models,
  foreign artifacts and any in-use cache. Further cleanup needs exact ownership
  and active-use checks.

## Work remaining

The goal is **not complete**. `docs/research/intake-reconciliation-2026-09-22.md`
and `docs/research/intake-decision-crosswalk-2026-09-25.json` own the candidate
dispositions and known gaps. `docs/operations/GPU_ACCEPTANCE.md` owns per-run
technical evidence. Do not convert an adopted idea, catalog row, CPU test, lease,
or one sample into model-wide or owner quality acceptance.

On resume, first run the combined-revision CI gate, then choose the next small
user-visible acceptance slice from the crosswalk and current UI. Material open
lanes include a true >15 s generation and final playback/quality review, Music3
checkpoint and license/source gates before its first song, YuE2 duration and
listening checks, broader Guide/Control/continuation execution, H3 and other
model quality comparisons, and the owner resource-unload action's live
reauthentication/queue-warning behavior. The latest user priority for the
resource-unload action remains high; inspect its current implementation and
receipt before changing it. Continue upstream integration selectively, with
no loss of existing Continuum features. Apply the intake procedure for new
source dumps, and preserve negative or deferred decisions.

Prior reported failure across short/long video, image, chat and music motivated
actual generations. The dated acceptance matrix contains several successful
technical runs and distinct failures/recoveries; it does not establish universal
reliability or human approval. Re-test a concrete remaining failure under the
selected model and exact access surface instead of generalizing from a prior
success.

## Safe restart and ownership boundary

At fresh start, read `AGENTS.md`, `FRESH_THREAD_HANDOFF.md`, `CONTINUATION.md`,
`CONTRIBUTING.md`, and this file. Resolve the physical repository root and its
`AGENTS.md`/`.beads`; run the shared read-only activation audit before any Beads
lifecycle command. The historical SQLite tracker remains hold-only: no `bd`
mutation, migration, sync, or hooks. Inspect current dirty files, processes and
`.working` claims, including host/workspace bindings. This handoff transfers no
claim, even if the path and branch match.

The predecessor releases all of its completed-work claims before successor
creation; no predecessor control-root claim is retained. The foreign `AGENTS.md`
edit, storage-janitor files, legacy metadata, `.artifacts-temp`, model/output
symlinks and unrelated claims remain untouched. The successor must acquire only
fresh exact claims after the owner resumes it. Do not reset, clean, stash,
broadly stage, or hand-edit claim/registry files.

The successor's immediate turn is deliberately limited to acknowledging this
document and creating a paused Goal. No CI run, implementation, restart, GPU
request, intake expansion, or mutation should start until the owner resumes it
after the Codex app restart. At that point, use progress before paperwork and
keep evidence levels explicit: source, synthetic, live runtime, browser,
technical media, and human acceptance.
