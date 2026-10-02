# Sequential Editor delivery — 2026-10-01

## Delivered behavior

Open a Gallery video in Editor, then add up to seven more videos from the same
project. Select each clip to trim it; use Move earlier / Move later to change
playback order. Drafts save in the project with revision conflict protection.
Export MP4 submits the saved revision to the existing queue. Originals stay
intact. The preview plays the selected clip; joined playback is available on
the exported video in Gallery. [Clip removal](MAESTRO_EDITOR_CLIP_REMOVAL_2026-10-02.md)
now permits removal of any clip while keeping at least one; originals remain
in Gallery and the draft retains its opening identity.

Exports use CPU H.264/AAC. The sequence keeps the first imported video's canvas
and frame rate, rounding odd dimensions up to even pixels. Other shapes receive
black bars. Each clip uses its first audio stream; absent audio becomes silence.
Clip lengths round individually to output frames. Single-source cuts retain
their existing behavior. Later milestones add
[titles](MAESTRO_EDITOR_TEXT_LAYERS_2026-10-02.md),
[audio](MAESTRO_EDITOR_AUDIO_LAYER_2026-10-02.md) and
[a still image](MAESTRO_EDITOR_IMAGE_LAYER_2026-10-02.md).
Transitions, overlapping video and an interactive preview of the full sequence
remain later work.

## Source and publication boundaries

Only current same-project Gallery video revisions can be appended. Server-owned
assets, transforms and canvas cannot be replaced through a trim/reorder save.
Every source's bytes, sidecar revision, authorization and privacy state are
checked at dispatch and again before publication. Recovery records include
every source. Private/explicit flags combine across all sources. Cancellation,
no-replace staging and publication rollback use the existing queue boundaries.
Rendered sequence dimensions, frame rate, duration, audio and media type are
checked before publication.

The sidecar records each source name, immutable revision and trim plus the saved
Editor revision and output canvas. A joined sequence has no single generation
recipe: Gallery must not show the first clip's prompt, seed or regeneration
action as though it could recreate the joined result. Source recipes remain
on the original outputs.

## Verification observed

- 33 focused Editor backend tests passed, including real FFmpeg joins, mixed
  aspect/frame rates, audio/silence, frame boundaries, cancellation, immutable
  source hashes, conflict handling and publication checks.
- A subsequent provenance correction passed 14 focused route tests.
- 11 focused UI/API tests and scoped ESLint passed.
- Existing desktop and mobile save-race browser tests passed (2 checks).
- Production UI build passed; the existing large-bundle warning remains.
- Live browser: appended a private source without revealing its preview,
  reordered two clips, trimmed each to one second, saved and reopened the draft,
  exported through the real queue, and played the final Gallery MP4 to its end
  without a media error. The file measures 2 seconds / 48 frames, 1344 × 768
  at 24 fps, H.264 video and AAC audio. Its combined privacy flag remained set.
- Both local and stable access health/readiness probes passed after the
  coordinated restart, with public restart status cleared.
- Desktop rendering inspected. At 390 pixels the page reflows without horizontal
  overflow. Full mobile visual QA remains incomplete: browser capture returned
  a malformed image or timed out, including the documented capture alternative.

The first diagnostic sequence export inherited the first source's generation
metadata. That negative artifact was retained. The correction uses null recipe
metadata while keeping source provenance; the final live Gallery export omits
misleading recipe/regeneration actions. Private receipts retain exact identities.

One bounded independent review identified missing canvas postconditions, a
misleading multi-clip preview label and odd-dimension handling. Each was fixed
and verified with focused checks. No full suite was repeated for this milestone,
following the owner's request to run it less often. This is not a current full
CI, human creative-quality or broad sprint completion claim.

## Continuation

The broader sprint remains active. The H3 opening-quality comparison is still
waiting for its exact GPU coordinator grant; no GPU work was started for this
CPU Editor slice. Music3 terms/assets and other pending acceptance remain under
their existing gates. The historical SQLite Beads tracker stays preserved and
unmodified. No Pinokio launcher was changed; launcher destination, example and
URL-capture checks do not apply to these application/UI changes.

Recover the repository dynamically, inspect current Git/claims and the private
Editor evidence checkpoint, then acquire fresh exact claims before further
mutation. The ongoing source of sprint scope remains CONTINUATION.md and the
native active Goal; this record is evidence for one delivered unit.
