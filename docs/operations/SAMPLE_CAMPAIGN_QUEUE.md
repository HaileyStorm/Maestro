# Comparative Sample Campaign Queue

This is the durable, reviewable queue for Maestro capability demonstrations.
It replaces no historical tracker record. It turns the recovered idea slate
into matched comparisons that can be released only when ordinary Maestro work
and meaningful external GPU work are idle.

## Evidence rule

A campaign item is valuable only when it isolates a Maestro intervention.
Every visual-quality item therefore has a Maestro arm and a control arm with
the same source brief, normalized inputs, model revision, settings, seed, and
output index. Before a pair can enter `held`, executor validation must also lock
duration or frame count and every model-specific geometry control in addition
to the manifest-required steps, resolution, and FPS. The intervention delta
names exactly which Maestro workflow or tweak is enabled.
The control disables that intervention; it is not a random generation and is
never selected merely because it looks worse.

Each video arm is reviewed from two to five identical normalized positions.
Frames are sequential, non-adjacent, and nearby enough to show one motion
window rather than unrelated stills. The private receipt binds output and frame
digests, the VLM report and verdict, and the later human decision. VLM review is
provisional. Only the owner can keep, reject, or request a rerun.

Frame selection is deterministic from the shorter arm. Let `last_index` be its
frame count minus one and set `stride = max(2, (last_index + 10) // 20)` and
`span = stride * (sample_count - 1)`. Use floor-left centering with
`start = (last_index - span) // 2`; the shared rational positions are
`(start + index * stride) / last_index`. Reject clips whose requested window
spans more than one quarter of normalized duration. Map each rational position
onto an arm with half-up integer rounding: divide numerator by denominator and
increment the quotient exactly when twice the remainder is at least the
denominator. Every mapped index must remain at least two frames after the
preceding index in both arms.

Visual-quality claims use the paired media and VLM path above. Workflow-only
claims such as queue recovery use matched operational scenarios, content-free
transition receipts, and human acceptance. They do not invent a VLM verdict or
counterexample image when an interrupted control has no complete media.

## Release states

- `design_ready`: paired hypothesis, intervention, and review dimensions are
  specified.
- `held`: both ordinary generation jobs are durably registered with
  `queue_class=background_sample`, at background priority, and cannot run yet.
- `running_arm`: exactly one arm owns the GPU slot.
- `awaiting_review`: both arms and their evidence receipt are available.
- `accepted`, `rejected`, or `rerun_requested`: human decision.
- `blocked`: the exact missing prerequisite is recorded; this is not evidence
  that a generation ran.

No item may enter `held` until launch integration can atomically publish both
arms already held. A sample may leave `held` only after the process-attributed
GPU gate and allocator/headroom gate pass over a sustained window. Recheck both
immediately before release and after slot acquisition. Meaningful external GPU
contention may preempt the sample immediately: preserve the newest durable
checkpoint, request cancellation only through Maestro, accept loss of the
in-flight arm, and never signal or control the foreign process. Owner- or
agent-required GPU work has priority too; prefer the next completed generation
boundary when its urgency permits, otherwise use the same durable cancel and
requeue path.

If an external action nevertheless interrupts or terminates a sample, record
the newest durable checkpoint, leave foreign work untouched, and return the
same job to `held` without duplicating the pair. Retry uses a durable
`not_before` time with bounded exponential backoff and jitter. A low-frequency
watcher rechecks ordinary Maestro work, process attribution, allocator
headroom, and the not-before time; missing evidence defers again instead of
busy-polling or assuming idle. Agent-required or owner-requested GPU work has
priority. When urgency permits, allow the current sample arm to reach its next
safe completion boundary first.

The durable retry attempt is the zero-based index of the delay being scheduled.
On a failed release gate or interrupted arm, compute the delay with the current
attempt, then atomically persist `attempt + 1` with `not_before` before returning
to `held`; the first failure therefore waits 30 seconds before jitter. The base
delay is `min(1800, 30 * 2 ** min(attempt, 6))` seconds; add deterministic,
content-free jitter from zero through 25 percent of that base and cap the total
at 1800 seconds. Reset the attempt only after an arm output is durably
committed. The watcher must not poll a held campaign more often than once every
15 seconds and must never release it before `not_before`.

## Wave 1 — highest-value paired proofs

| Rank | Campaign | Format | Maestro intervention | Control | Review focus | State |
|---:|---|---|---|---|---|---|
| 1 | Reference Lock | 15 s | Reference-sheet selection plus locked subject, wardrobe, prop, and palette constraints | Same source references passed directly without the reference-lock workflow | Identity, wardrobe, prop, palette, camera continuity, temporal stability | `design_ready`; blocked on launch coordinator |
| 2 | One Idea, Four Roles | 6 s | Director role planning converts one brief into explicit visual, motion, camera, and sound responsibilities | Same brief sent through the direct generation path without role planning | Whether the requested beat reads clearly; motion/camera agreement; prompt drift | `design_ready`; blocked on launch coordinator |
| 3 | Recovery Is a Feature | 6 s plus recovery evidence | Durable held job, safe output-boundary yield, restart recovery, and same-job continuation | Direct generation without the recovery workflow, using the same creative inputs | Output continuity plus queue/restart provenance; no VLM claim if the control has no complete output | `design_ready`; blocked on launch coordinator and recovery dispatch |
| 4 | Pocket to Picture Lock | 30–45 s | Mobile brief capture, Director plan, reference lock, queued generation, and review handoff | Same brief and assets submitted through the direct desktop generation path without the coordinated workflow | End-to-end intent survival, continuity, and reviewability rather than UI speed alone | `design_ready`; blocked on launch coordinator and capture script |

Wave 1 releases one pair at a time in rank order. Reference Lock is the first
GPU candidate because it has a strong visual hypothesis, an honest matched
control, and frame-level VLM criteria. Recovery Is a Feature may be exercised
earlier without GPU if its queue/restart protocol can be proven synthetically.
This deliberately supersedes the recovered tracker suggestion of One Idea,
Four Roles → Recovery Is a Feature → Pocket to Picture Lock: Reference Lock now
has the most specific intervention delta, strongest matched control, and most
objective first-wave VLM criteria.

## Wave 2 — workflow stress and correction

| Rank | Campaign | Format | Maestro intervention | Control | Review focus | State |
|---:|---|---|---|---|---|---|
| 5 | Continuity Rescue | 30 s | Detect and repair a bounded continuity break while preserving approved canon | Same initial result continued without the repair workflow | Subject/prop geometry across the repaired boundary; new artifacts | `design_ready`; waits for Wave 1 evidence |
| 6 | Break It on Purpose | 30–45 s | Deliberate cancellation or resource interruption followed by durable recovery | Equivalent direct run restarted manually | Duplicate work, lost outputs, changed seed/settings, final continuity | `design_ready`; waits for recovery dispatch |
| 7 | One Note, Three Consequences | 30–45 s | One creator note propagates through plan, references, and generation constraints | Note appended only to the direct generation prompt | Whether the requested change lands without collateral drift | `design_ready`; waits for Director revision proof |
| 8 | Reference Enters, Direction Emerges | 90 s | Reference analysis produces explicit direction and a traceable generation plan | Reference used only as a raw generation input | Faithfulness versus useful direction; provenance; creator control | `design_ready`; waits for Wave 1 evidence |

## Wave 3 — longer product stories

These are assembled only from already accepted shorter evidence. They do not
trigger fresh random generations merely to fill a reel.

1. **Brief Survived Reality** (90 s): one brief through planning, resource
   contention, recovery, comparison, and human selection.
2. **No Tab Escape** (3–5 min): plan, queue, monitor, compare, and review in one
   Maestro session.
3. **Commercial in One Sitting** (3–5 min): a compact multi-shot deliverable
   built from accepted Reference Lock and continuity evidence.
4. **Brief to Campaign** (8–12 min): the complete concept-to-selection proof,
   including rejected alternatives and recovery provenance.

## Deferred idea bank

Red Umbrella Rewrite; Continuity Court; Three Directors, One Canon; Blank Page
to Premiere; Change One Fact; Fork, Compare, Recombine; Cold Restart Recovery;
and Evidence Board remain useful, but each must first name a distinct Maestro
intervention and a matched control. They are not GPU work merely because an
idea exists.

High-priority historical slate names remain mapped as provenance: **One Prompt, One Perfect
Beat** and **Seven-Second Note** feed One Idea, Four Roles; **Reference-Locked
Reveal** feeds Reference Lock; **Mobile Idea to Queue** feeds Pocket to Picture
Lock; **Session Handoff**, **Approval Pause**, **Timeout Without Drama**, and
**Cold Restart Recovery** feed Recovery Is a Feature and Break It on Purpose.
The **60 s Director cut**, **Music Carries the Scene**, **Brief to Mini-Episode**,
and prior ten-minute end-to-end proof feed Wave 3 only after their component
evidence is accepted. These aliases are deprioritized, not erased.

Additional recovered names remain explicitly deprioritized pending a distinct
intervention hypothesis: **Brief to Beat Sheet**, **Phone Idea to Finished
Beat**, **Recovery Button**, **Human in the Loop**, **Imperfect First Take**,
**Living World Bible**, **Run X-Ray**, **Fork/Compare/Recombine**,
**Late-Joining Specialist**, **Status Means What It Says**, **Revision Ripple**,
**Budget-Aware Director**, **Artifact Archaeology**, **One Brief Many
Workflows**, and **Safe Cross-Project Borrowing**.

## Current implementation evidence

- Pair manifests, exact generation settings, intervention deltas, motion-frame
  selection, and provenance-bound evaluation receipts are implemented and
  covered by focused unit tests.
- Process-aware NVML compute/graphics attribution and a sustained fail-closed
  idle window are implemented and covered by model-free tests.
- `background_sample` is a durable queue class that sorts behind every user job
  across live admission, recovery ordering, credit-starvation guards, and queue
  position simulation.
- The recovery coordinator can atomically register multiple held jobs or none.
- Launch-side pair submission, allocator-gated release, post-slot recheck,
  sample-specific recovery dispatch, VLM execution, and the human review UI are
  not yet live. Consequently no Wave 1 item is claimed as queued or generated.
