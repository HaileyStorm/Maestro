# Maestro successor handoff — 2026-09-14

## Read this first: intent and operating mode

The owner explicitly requested a fresh task under the updated harness, this detailed handoff, an active detailed native Goal in that successor, and retirement of the predecessor. This is a transfer of unfinished work, not a completion claim. Start from the saved project directly; do not create another checkout or worktree. Resolve the repository root dynamically and verify `AGENTS.md` plus the historical root `.beads` directory.

**Sprint mode. Progress before paperwork. Work in parallel as much as is effective.** Ship useful, verified milestones. Keep ceremony proportional; do not spend another sprint polishing handoffs, repeating green tests, or expanding dormant infrastructure. Use independent agents when they reduce elapsed time or rework, one writer per file cluster, and central integration. Preserve the latest owner intent across interruptions.

The owner authorizes Nous for private project/code task context, excluding PII and similarly sensitive information. This supersedes the earlier blanket private-project-context restriction. Prefer exact `deepseek/deepseek-v4.1-flash` through the installed Nous roles at Max for eligible bounded implementation, inspection, and verification; read current provider rules first and keep credentials, private media, PII, and unrelated secrets out of packets. Astra owns design, priorities, synthesis, and acceptance. Native Luna Max is the authorized fallback when the route or tools are unsuitable; state the actual reason. Do not inherit stale provider or model assumptions from old handoffs.

GPU work is explicitly authorized **subject to a fresh exact coordinator grant**, using `gpu-coordinator-client`. The old native Goal's CPU-only/no-GPU wording was superseded by the owner's September 6 authorization. Registration `maestro-local` is not a lease. Use current installed skill, exact workspace binding, durable grant and coherent validation before workloads. Never infer device acceptance from CPU tests. The September 14 request separately authorizes launch diagnosis, persistent launch convenience, and necessary recovery; it does not activate accounts, credits, SSO, or new external creative providers.

## Immediate priorities and acceptance

1. Finish and verify the launch experience described below, including fresh local readiness and current served UI. Follow any remaining runtime caveats in the final checkpoint. Do not trust Pinokio's `ready: true` alone.
2. **Observed live priority:** at a normal desktop viewport, the bottom Generate action/model controls are squeezed into very narrow vertical columns and consume most of the sidebar height. This leaves little space for the form. Profiles are correctly near the top, but this layout is not accepted. Fix this visible defect first, then verify the actual rendered Generate, Advanced, and profile interactions, then carry the same coherent interaction rules across Director, output reuse, Inpaint, Chat/settings and other related surfaces. The owner explicitly wants substantially less copy/disclaimer clutter and unsupported combinations handled by controls, available choices, and natural repair actions. Do not replace an awkward flow with paragraphs explaining it.
3. Profiles belong prominently near the top of Generate, with the shared control also available where useful in Advanced/Director. This placement is an evidence-backed decision already implemented, not an unanswered preference question. Saved profiles must capture and restore every applicable generation setting through the canonical schema. Test omissions, false/zero/empty values, model/mode switches, existing-profile updates, output restore, and account/project transitions. Creative prompts/media/authority are currently excluded from reusable technical profiles; do not claim that means literally every job field is saved. Clarify that product boundary only if it materially affects the owner's request; never silently drop technical settings.
4. A new wave of research links is coming. Use `docs/operations/INTAKE.md`: deduplicate, investigate primary material and issues/comments, compare with the shipped baseline, extract invariants, and decide adopt/adapt/experiment/benchmark-lead/watch/defer/reject. A dump is not a build list. Keep intake bounded, then implement only the authorized decided slice. No automatic downloads, model loads, or default changes just because a paper was linked.
5. Continue the remaining planned features and WIP described in `ASTRA_CONTINUATION_HANDOFF_2026-09-06.md`, with the latest checkpoints here taking precedence. Audit actual source before treating an old failure or plan as outstanding. Deliver one useful milestone at a time, with relevant checks and exact Git closure.

### Detailed native Goal to start in the successor

Continue Maestro in sprint mode until the actionable planned backlog is finished or the owner redirects/stops it. First verify the delivered reliable one-command launch and verify that the current UI is actually served. Then finish coherent app-wide generation and settings interactions: prominent complete reusable profiles, reliable save/update/restore across modes and output reuse, intuitive compatibility choices and repair actions, less explanatory clutter, and consistent behavior throughout Generate, Advanced, Director, Inpaint, Chat and related panes. Complete the remaining planned Maestro features and existing WIP from the September 6 continuation handoff, reconcile superseded machinery, and process incoming research waves through the intake contract before implementing decided slices. Work in parallel whenever effective, preserve one writer per scope, and commit/push coherent verified milestones under existing authority. Maintain historical tracker and foreign-work holds, privacy and local-content-neutrality requirements, project/account isolation, and separate CPU/mock/build/browser/live-device/human evidence. GPU acceptance is authorized only through fresh exact coordinator leases; no implicit account/credit/SSO/provider activation. Keep making independent useful progress when a lane is blocked. Acceptance is working user-visible flows plus relevant regression/build/runtime evidence and an accurate remaining-work record, not merely a plan or green mocks. continuation_mode=continuous.

The predecessor's native Goal was verified non-active (`blocked`) during transfer. Its old objective and enormous cumulative counters are historical; do not mark its unfinished objective complete or copy stale prohibitions as current authority. The fresh task should call `create_goal` with the detailed objective, without inventing a token budget, and report that it is active. Predecessor final delivery/archive is the ownership transition; do not race its remaining launch/helper/doc publication.

## Where we have been

The long September 6 handoff contains the design history, research intake, GPU receipts and earlier account/project decisions. Read its current pointer, authorization updates and the sections relevant to the next work slice; do not reread its entire history as a ritual.

The source baseline entering this launch/handoff wave is `68744f1` on main, matching origin/main when checked. Recent pushed milestones:

- `610156e`: canonical complete generation profiles, 76 parameter fields (55 UI plus 21 custom), schema v2 complete versus v1 partial semantics. Preserves false, zero, null and empty values. Shared prominent Generate/Advanced controls.
- `5c55ab9`: 53 mode-local settings, attachment constraints, atomic reference packs, stale async media-result fences.
- `96dcf16`: output-sidecar restore uses canonical projection; exact Custom H3 restoration and confirmed reroll; delayed results cannot overwrite another context.
- `afdd9a1`: H3 Sage2 selection fidelity and explicit Dense repair, pre-upload/pre-benchmark guards, all-segment Base validation.
- `4cf2d2d`: Inpaint target/invert/prompt-strength restoration, mask invalidation and preview identity fences.
- `f161365` and `c1231b2`: deterministic LLM lease tests and closed public preparation errors; arbitrary exception text stays out of UI.
- `7892f2d`: reject malformed saved H3 executable plans before model setup, including prompts, mode, presence, frame geometry, totals/counts; structured mismatch recovery with useful Open Generate action instead of ineffective Retry.
- `5d908ca`: Use Automatic attachment repair; adaptive FL routing changes metadata without silently resetting generation settings, media, LoRAs, custom values or delivery choices.
- `b1a715c`: Improve before Generate shares eligibility between control and submission; unsupported Blend/avatar Edit/audio paths no longer get blocked by an invisible saved toggle.
- `7077e65`, `a9c4f24`: reconciled unused Reference/test residue while preserving executable regressions.
- `7ce61de`, `963a08f`: transient profile-fetch failures retain records and offer Retry; access-loss statuses clear scoped records; consistent refresh/loading states across Generate, Advanced and Director.
- `4402792`: Start forwards configured account store, migration and membership paths with correct per-app empty/global precedence.
- `42b2637`: profile drafts/notices are keyed to account epoch, account, project and mode; delayed deletion cannot erase a new selection.
- `50a80cb`: shorter offline status copy and same-page Try Again link. Worker tests pass; **not deployed**.
- `68744f1`: Update selected profile plus Save as new. Complete capture is shared. Backend exact-scope revision compare-and-swap preserves identity/creation time, increments revision, handles identical replay idempotently, authorizes before body work. UI retries the exact serialized payload after 503; 409 refreshes with a distinct notice while preserving the form; late account/project/list responses are fenced.

Earlier substantial work includes H3 authored-shot/physical execution contracts, role-aware continuity and references, crash recovery, native Ref/FL paths, NVFP4 LoRA work and observed offload profiles. Those are not blanket GPU/product acceptance. Consult `GPU_ACCEPTANCE.md` and the exact receipts before promoting a capability or changing defaults.

## Evidence: what is proven and what remains

- Full committed-tree backend suite at `4402792`: **5,037 tests, 19 skips, zero failures**, with Python compilation, JSON grammar and tracked-publication guard passing. Receipt directory `.artifacts-temp/astra-integrated-cpu-20260912/`. This predates the final profile Update endpoint.
- Profile Update endpoint/source at `68744f1`: **125 relevant backend/account tests** pass; UI **601 tests**, production build and targeted lint pass; independent review resolved. Do not describe the earlier full backend run as covering this later endpoint.
- Worker offline change: **35 tests**, source-only and undeployed.
- Earlier launcher checkpoint: **34 tests, one skip**. Native Windows remains unverified.
- On September 14 the root served `ui/dist` was stale (September 7), despite later candidate builds passing. Parent rebuilt current UI successfully with `--emptyOutDir false`, preserving the dist reservation and old assets; current index names the new bundle. Previous dist is saved in `.artifacts-temp/maestro-handoff-20260914/previous-ui-dist` for local rollback.
- Browser-rendered acceptance, LAN/Cloudflare parity, human acceptance and complete GPU generation/quality/performance/cancellation matrices remain separate. Do not use tests or a listening server as substitutes.
- The E2E runner requires browser cache/results/npm/Vite scratch outside the repo on a different mounted filesystem. The prior specific scratch-location question remains unanswered; do not silently bypass that contract. Read current runner before acting.

## Launch procedure and current diagnosis

Continuum starts through root `start.js`, never direct WanGP CLI. Classic UI is separate `start_classic.js`. The user-installed Linux systemd service `maestro-continuum.service` owns the persistent start, requiring `pinokio.service`; agents must not make Pinokio itself a tool-shell child whose lifetime ends with a timeout. `systemctl --user start maestro-continuum` is the persistent start operation. Starting an already healthy instance is not a restart. Use existing `restart.js` and its bounded public-status protocol for a coordinated bounce.

The new `scripts/maestro_open.py` is the user/agent convenience entrypoint. Local installation provides `maestro`, `maestro --status`, and `maestro --no-open`, as the single installed entrypoint. Desktop/menu installation was not required for this command-based delivery and was left untouched after its shared reservation discovery was incomplete. It discovers Pinokio and the exact app, starts the persistent service only if needed, waits with progress, validates a loopback ready URL, probes `/health` then `/ready`, and opens the browser only after success. Status is read-only. Consult `--help` for the bounded timeout and optional explicit pterm path. The helper is Linux-specific; it does not replace cross-platform Pinokio launchers or install dependencies.

The September 14 initial launch exposed a **relocated virtual environment**: `app/env-rtx50/bin/activate` still pointed to the old drive's installation. As a result it ran that old interpreter with stock `mmgp 3.7.12`, while the current local environment already had required `3.7.12+maestro1`. Parent corrected only the local activation file under reservation, retaining its preimage in the local evidence directory. Direct activation now resolves this checkout's interpreter and the correct package. No package reinstall was needed. Audit other relocated executable/shebang paths only if relevant; do not mutate the old checkout or broadly rewrite environments.

A failed backend also allowed Pinokio to advance to a malformed literal `{{input.event[1]}}` ready URL and start sharing without a backend. This is false readiness, not a live app. Preserve the mandatory capture pattern in `start.js`; investigate failure gating separately if still reproducible. The helper validates actual URL and health to avoid treating it as success. Cold Conda/import startup took several minutes, so the helper must show progress with an adequate timeout rather than appearing inert. Follow the final runtime checkpoint below; never reuse a recorded port.

## Ownership, tracking and closure

- Root tracker is preserved historical SQLite. Current repo policy explicitly prohibits lifecycle/migration/sync/hook mutations. Perform the shared read-only activation audit and use the approved Coordination fallback as needed; do not initialize a second tracker or run obsolete sync recipes. Generic Beads hook text does not make a historical store healthy.
- Dirty `AGENTS.md` is owner/harness work and must remain untouched and unstaged by this milestone. Preserve all unrelated untracked files, model/output/upload links, old cutover directories, existing locks and private artifacts.
- Protected `app/services/storage_janitor.py` and `tests/test_storage_janitor.py` remain untracked historical WIP requiring explicit ownership recovery under `FRESH_THREAD_HANDOFF.md`. Do not adopt, delete, claim or wire them into mutation flows simply because they are visible.
- Use current `working_sentinel.py`; release only exact owned claims. Do not recreate retired locks, edit claims by hand, reset, clean, stash, or broadly stage. New task identity must obtain its own reservations after predecessor releases.
- Account/project usability has priority over dormant credit enforcement. Keep census/recoverable quarantine invariants; user handles credentials and recovery codes. Historical account activation notes are not current runtime proof. Local/LAN/Cloudflare remain one owner unless explicitly changed.
- Preserve local content neutrality: no Maestro prompt/media moderation, classification, rewriting or refusal layer. Retain non-content security/privacy/resource constraints.
- Run relevant full modules and CI-equivalent gates for the changed scope. Reuse fresh evidence for unchanged bytes; no redundant 23-minute suite purely for a documentation/helper milestone. Serial exact-path Git publication, fetch/inspect upstream first, no forced merge/stash of foreign work.

## Final runtime and transfer checkpoint

The following checkpoint supersedes the earlier failed-start observations.

September 14 launch recovery succeeded. `maestro --status` and `maestro --no-open` report the currently discovered local ready URL; `/health` and `/ready` return 200. The served root HTML is byte-identical to current `ui/dist/index.html`. Chrome rendered the actual app with profiles prominent near the top, while exposing the squeezed bottom-control layout described in priority 2. No project was selected, no profile/user content was modified, no terms were accepted, and no generation/model load was requested. This is local launch/render evidence, not whole-app or GPU acceptance.

Existing `restart.js` published its bounded restart notice, restarted the corrected environment, registered sharing, and cleared its exact notice. Authenticated `restart_status.py show` returned `MAESTRO_RESTART_STATUS none`. Full remote UI/account/LAN parity was not verified. `health-receipt.json` and the build log remain in the local handoff evidence directory. Pinokio owns the backend after its native restart; the initiating helper terminal is not its lifetime owner.

The single installed command is in the user's normal local-bin directory and invokes this checkout's `scripts/maestro_open.py` with system Python. It requires the existing installed Pinokio/Maestro user services. It starts Pinokio idempotently, waits for its control plane, verifies the app's exact checkout before starting Maestro, and keeps `--status` read-only. Cold-control-plane readiness is tested with mocks; the live successful check reused the running Pinokio/app and did not deliberately stop them again. Thirteen helper-module tests pass, including cold-control recovery, invalid/foreign targets, non-loopback URL rejection and nonfinite timeout rejection. Source compilation, diff whitespace, production UI build, and the tracked-publication guard pass. The previous full backend/UI evidence remains as scoped above.

A bounded Nous code-review request failed terminally with HTTP 400 at the provider forwarder. It was not replayed; native Luna fallback review found that command text could misclassify fatal status errors as transient. The helper worker corrected that path and added a regression before publication. The timeout option covers readiness waiting; service/control-plane startup has separate bounded waits. This does not revoke the owner's new permission and must not become another standing preflight gate or a shared-harness repair sidetrack.

The fresh successor task has been created in this same saved project with Astra medium and has confirmed its detailed continuous native Goal is active. It is waiting for the predecessor's explicit publication/ownership release before writes. The predecessor retains no roadmap execution after transfer. The final task message supplies the source commit and ownership release; preserve that message as the last transition evidence.

Latest cross-task updates: the successor reproduced the footer defect independently and reported another terminal Nous HTTP 400. At the owner's explicit request, the predecessor sent the failure and permission update to provider/harness task `01a0a059-2491-7d60-abf6-66f944ddfaf4`, requesting recovery findings go to the successor. Keep Maestro delivery moving via native fallback; the provider owner handles that incident. The successor's fresh activation audit reports Dolt metadata despite historical hold documentation. Treat this as current tracker-policy drift requiring reconciliation evidence, not authority to perform a migration or blindly apply historical SQLite claims; the successor is preserving the no-mutation hold pending verified current policy.

## Successor milestone — Generate footer and browser coverage

The predecessor published `861bf35` and explicitly released ownership before
successor edits. The successor's detailed continuous native Goal is active.

Owner operating instruction: whenever possible, leave Maestro Continuum
running, including its stable Cloudflare access. Restart when necessary, but
avoid stopping it without bringing it back up. Preserve this availability
priority during GPU coordination and subsequent implementation milestones.
The owner allows a reasonable delay when immediate restart would create churn
or a pointless wait.

Generate now gives its model choices a full-width row, with paired desktop
cards and stacked mobile cards. The action row wraps instead of starving the
selectors. Model names have their own line in the popup, capability badges
wrap, and completed manual-file verification uses a short neutral status.
Group descriptions remain available to assistive technology and hover.
Generate, queue, and LoRA actions use the existing mobile touch-target rule.

Synthetic browser startup was blocked by the shared profile-schema import.
The harness now exposes that exact checked-in JSON as a virtual module while
retaining its backend, external-network, and general filesystem guards. Its
adaptive held-job fixture also answers polling for the exact synthetic job ID.
The desktop/mobile opening helper, responsive remount checks, profile fixture,
and footer geometry/name/reachability regressions are repaired.

Evidence: 601 UI tests, production build, targeted lint, E2E typecheck and
suite discovery pass. All 16 adaptive-controls/profile browser tests pass in
Firefox and Android-like Chromium, with a final two-browser layout recheck and
four network-boundary checks. The runner now invokes installed Vite directly;
its final startup/teardown completes cleanly without an npm lifecycle wrapper.
The real local app was also inspected; health/readiness and served-build
identity were checked. This is not GPU, Windows, LAN, or human acceptance.
The stable Cloudflare URL reaches Maestro's normal sign-in screen in Chrome.
Python probes were rejected by Cloudflare error 1010; no service restart was
needed. Authenticated remote account/project parity was not exercised.

The broader 96-test browser sweep is **not green**: it was stopped after 18
passes, 10 failures and one interruption, with 67 tests not run. Its footer
failure was subsequently fixed. Remaining smoke findings include render-fault
injection that React now recovers before the boundary, stale Support/What's New
navigation expectations, LoRA-dialog touch targets, and overlay accessibility.
Continue those as the next bounded browser/interaction work; retain the full
results rather than weakening assertions. The historical tracker hold and
protected storage-janitor work remain unchanged.
