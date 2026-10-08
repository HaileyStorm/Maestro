# Reviewed Gallery face repair editor

The editable Gallery editor is prepared behind the absent-by-default
`h3_face_refine` Base-model capability. It is not yet an activated feature.
The backend source reader, exact frame-preview routes and catalog hook below
must be implemented and verified before advertising that capability. The
native installed-weight qualification and human face-quality review remain
separate gates. See [the worker contract](H3_FACE_REFINE.md).

## Browser workflow

Select one finished video in the active project with generation permission.
The editor requires visible, enabled, legally available MiniMax H3 Base and
explicit server capability. Old servers omit the capability and show no
repair entry.

Read authoritative source facts before editing. Private previews require an
explicit reveal; no image pixels are mounted while hidden. Browse exact
integer frames and draw a square, or enter integer Left, Top and Size.
Every frame starts with no observation. Explicit review marks the current
frame or a specified range within one declared shot. No face detector,
identity guess, interpolation, feathering or smoothing is performed here.
Square observations with padding 1 and smoothing 1 make the visible crop
match the current compositor's hard rectangular replacement.

Describe the repair and set strength. Source audio selection is a zero-based
audio ordinal used for conditioning only; the composed copy retains every
original audio stream. Unreviewed frames have null observations and zero
multipliers, preserving original pixels. Reviewed frames have multiplier 1.
The source's privacy and explicit-preview flags accompany the submission.
Technical controls expose square crop resolution, steps and an exactly
representable JavaScript integer seed. Backend validation remains authoritative
for decoded clock, geometry, memory, model access and execution.

Submission is once per attempt. A session receipt scoped to account, project,
source name and revision is written before POST. Pending and uncertain receipts
survive closing, selection remounts and reload. Transport failure, server 5xx
or unreadable success acknowledgements offer Queue inspection, without a
resend button. Queue refresh never submits another repair. A confirmed
accepted attempt can explicitly start a new repair; a definitive admission
rejection releases its receipt. Receipts contain state only, with no prompt,
observations or extracted pixels. Durable server request-ID reconciliation is
still needed before offering safe retry of uncertain attempts.

## Required backend read contracts

Both reads must use the same FaceRefine feature flags, current project access,
final Gallery source, listing revision and remote visibility/privacy admission
as `POST /api/v1/tools/h3-face-refine`. Read the authorized source through the
existing bounded snapshot/probe path, and recheck source revision and access
before returning. Do not return host paths or generate a Browser Copy.

`GET /api/v1/tools/h3-face-refine/source` accepts query fields
`workspace`, `name`, `revision`. Its response binds those three fields and
returns measured integer `width`, `height`, `frame_count`, canonical
`fps: "24/1"`, and ordered `audio_streams: [{ordinal, label}]` with audio
ordinals starting at zero. The browser rejects a foreign or malformed binding,
unsupported clock, frame counts outside 124–345 or not `17n+5`, and decoded
source sizes beyond the worker's bound. Saved generation parameters and browser
duration cannot supply these facts.

`GET /api/v1/tools/h3-face-refine/frame` accepts the same binding plus integer
`frame_index`, with zero the first decoded frame. It returns a bounded PNG of
that exact source frame at measured source dimensions. `preview_attempt` is an
optional cache-busting read retry counter; it cannot select another source.
No-store responses must preserve project/private-media access behavior.
Missing, changed or unauthorized sources return a bounded error rather than
another revision or frame. Preview extraction needs owned-child cancellation,
decode limits and a source-revision recheck; browser-incompatible FFV1 sources
still need exact frame access.

Only advertise `h3_face_refine: true` after both read routes and the existing
POST are available under both FaceRefine experimental flags, current Base-model
access and an accepted activation wave. Implementing these hooks while a
native qualification pins the launch adapter would invalidate that frozen run;
finish its exact request first.

## Verification boundaries

Review helpers exercise capability/permission selection, exact clock and source
binding, explicit shot-bounded region review, unresolved frames, source flags,
audio ordinal and request limits. Browser fixtures render the real React
editor using synthetic media and mocked reads/admission, checking pixel access,
frame loading, drawing coordinates, duplicate prevention, keyboard focus,
responsive layout, contrast and controls. These checks do not establish native
FaceRefine weights, live authenticated Queue admission, face quality, LAN or
physical mobile/platform acceptance.
