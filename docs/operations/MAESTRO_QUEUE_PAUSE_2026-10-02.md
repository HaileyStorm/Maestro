# Pause an idle generation queue

The Queue shows **Pause queue** when the server reports no running jobs.
It immediately closes admission through the existing durable queue pause
primitive. New work remains queued until **Resume queue** or **Start next**.
If a job starts between the status refresh and the click, that already-started
job may finish; the action does not cancel work or unload a model.

With a running job, the control remains **Pause after output**. Its cooperative
output-boundary latch and **Cancel pause** behavior are unchanged. This keeps
the choice visible without an additional menu or queue-wide cancellation.

The existing `POST /api/v1/queue/pause-after-output` accepts optional boolean
`immediate: true` together with `enabled: true`. Existing callers omit the
flag and retain their behavior. Immediate requests must receive `paused: true`;
an older backend that only sets the output latch is reported as a failed pause
with an actionable restart instruction. The existing local/reauthenticated
owner route gates and machine-control visibility continue to apply.

## Evidence and deployment

Two focused endpoint checks exercise idle admission/resume and validate pause
modes before mutation. Five existing lifecycle pause checks and 31 affected UI
checks pass. The UI checks cover idle, running, cancellation and resume copy,
machine-control visibility, request serialization, and the old-server response.
TypeScript/production build, scoped lint, source compilation and diff checks
pass. No full-suite rerun or GPU work is needed for these checks.

Independent review found boolean coercion could admit a string value. The
route now rejects non-object requests and non-boolean controls before mutation;
the focused route regressions pass after that correction. The rebuilt local
Queue renders **Pause queue** with zero running/queued jobs.

After the coordinated Pinokio restart, live Queue acceptance observed the
explicit paused message and **Resume queue**. Submitting one matched H3 Guide
job left zero running and one waiting job. **Start next** then admitted that
job. The runtime loaded the current source, and direct and stable-share health
probes passed. This proves idle pause and explicit admission for the observed
run. A restart while paused, concurrent submissions, and human acceptance
remain separate checks.
