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
Queue renders **Pause queue** with zero running/queued jobs. The action has not
been clicked against the old backend, and no new work has been submitted.

The backend requires a coordinated Pinokio restart before the new immediate
flag takes effect. Source checks and a rendered button do not establish live
admission or restart-recovery acceptance. Record the paused queue, held job,
resume, and durable restart evidence after deployment separately.
