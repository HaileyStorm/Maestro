# Face repair HTTP reads

`app/services/h3_face_refine_reads.py` prepares the two read handlers required
by the [reviewed Gallery editor](H3_FACE_REFINE_UI.md). They use the
[exact CPU source reader](H3_FACE_REFINE_PREVIEW.md), and remain unmounted until
the launch adapter can be changed without invalidating its pending native test.
The module neither enables experimental flags nor advertises model capability.

`register_face_preview_reads(api, authorize_source=...)` registers once:

- `GET /api/v1/tools/h3-face-refine/source` accepts exactly `workspace`, `name`
  and `revision`. It returns that binding and measured source facts, including
  zero-based audio ordinals, without paths, hashes or arbitrary source tags.
- `GET /api/v1/tools/h3-face-refine/frame` additionally requires an integer
  `frame_index`. Optional `preview_attempt` is only a bounded read retry counter.
  The result is the exact lossless source PNG at measured dimensions.

The synchronous authorizer receives the current request and exact query binding.
It must enforce both FaceRefine flags, generation permission, current model
visibility and legal/terms admission, final Gallery classification, listing
revision and project/session/private-media access. Under the existing workspace
and lineage guards, it resolves the direct source and returns `FacePreviewAccess`
with the same workspace/name/revision, resolved path and boolean privacy flags.
It must reject aliases or changed bindings rather than selecting another source.
No public input supplies a path or an access record.

The handler invokes this authorizer before CPU work and again after it. Revoked
access or changes to the binding, path or source flags prevent returning facts
or pixels. The caller must derive authority from current account/project state
on both invocations, rather than a cached permission snapshot. Source-byte
changes during inspection are also rejected by the CPU reader. Read results,
admission failures, malformed queries and handled decoder errors carry
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

At most two CPU reads run through one mounted handler pool. Additional authorized
reads receive 429 with a short retry hint. There is no background admission queue.
A release-once lifecycle keeps each slot until its actual worker exits; failed
or cancelled submissions retire before start, so a late queued invocation cannot
decode. Request disconnect and repeated waiter cancellation set the worker's
stop flag. Shutdown waits for the worker to settle even if its offload coroutine
has already been cancelled. The existing upload-usage context follows off-loop
work through actual drain. No GPU lease, model, generation request, Gallery
publication or Browser Copy is involved.

The ASGI checks mount these real handlers on a synthetic test app. They exercise
actual FFV1/PNG pixels, current-authorizer rechecks, privacy/revision changes,
query validation, error redaction, HTTP disconnect, repeated cancellation,
capacity during drain, pre-start failures and late invocation retirement.
The authorizer in those checks is a fixture. Passing them does not prove the
production authentication adapter, catalog hook, live browser/Queue workflow,
native face quality, LAN, platform or human acceptance. Production mounting and
an accepted activation wave remain necessary before exposing the editor.
