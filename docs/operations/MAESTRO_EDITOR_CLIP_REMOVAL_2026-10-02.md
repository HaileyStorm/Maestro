# Remove clips from an Editor sequence — 2026-10-02

Select a timeline clip and choose **Remove clip**. The selected cut leaves the
edit; its Gallery video stays intact. At least one clip must remain. Later
clips move earlier to close the gap, retaining their source trims. Audio,
image and title layers keep their absolute sequence times. If a shortened
sequence leaves a layer outside the cut, adjust its interval before exporting.
A removed video can be added again from the current Gallery listing.

The draft still opens from its original Gallery video, including when that
video's clip has been removed. A server-owned opening identity retains the
original output/revision separately from active assets. Legacy drafts acquire
that identity on save. Save accepts only a nonempty subset of existing clip
IDs, trims and order; it cannot add or replace source identities. Removed
assets leave the active graph. The opening identity is navigation metadata,
not an export input or a source of output privacy flags.

The canvas metadata stays unchanged. Multiple remaining clips use the existing
sequence export clock and canvas. A single remaining clip uses the existing
single-cut renderer and its source frame rate/audio behavior. It fits and
letterboxes a differently shaped remaining source to the saved canvas, also
without image/title layers. Publication checks the fitted dimensions.
Original video, audio and image files remain unchanged.
The existing source, CAS, publication and recovery boundaries remain in use.
The provenance canvas records the saved editing settings; a single cut's
measured frame rate remains its source frame rate. Canvas-fit exports do not
inherit a regeneration recipe that omits the edit.

## Focused evidence

- 48 of 49 affected backend checks initially passed. The new fixture's missing
  import was repaired and its focused check then passed. Coverage includes
  original/appended removal, legacy opening identity, fixed overlay times,
  stale saves, invalid IDs/duplicates/empty tracks, reopen through the original
  Gallery video, and remaining-source export/privacy provenance.
- 18 UI helper checks passed, including source trims, asset pruning, unchanged
  overlay clocks, re-add visibility and protection of the final clip.
- Desktop and mobile synthetic browser cases passed: selected removal,
  keyboard focus on the next clip, save/reopen, disabled final removal,
  no horizontal overflow and no serious/critical axe findings. Their initial
  cleanup failure was a missing fixture font route; it was repaired.
- TypeScript, e2e TypeScript, scoped ESLint, Vite build and publication guard
  passed. The existing large-bundle warning remains. No full suite was repeated.
- Independent review found the sole remaining source could bypass canvas fit.
  The correction seals the canvas and verifies output dimensions. All 24 route
  checks and 11 CPU media checks passed. Media coverage includes letterboxing
  without overlays, source FPS, both source audio streams, mixed-bed tones and
  duration, unchanged source hashes and invalid canvas rejection. The mixed
  graph test exposed early audio completion; bounding the source input to the
  same cut range as sequence export fixed it.

## Native acceptance

The coordinated Pinokio restart completed. Local and stable-share `/health`
and `/ready` returned 200; Chrome reached the stable sign-in boundary. The
launcher cleared its exact restart generation and authenticated status showed
no active notice. Cloudflare rejects the default Python user agent with 1010;
the browser and a browser user agent reached the intended surface.

In the retained native draft, removal of the original clip saved and reopened
through that original Gallery entry with only the second clip remaining. Final
clip removal was disabled, and audio/image/title intervals past the shortened
cut prevented export. Re-adding the original through the current Gallery pin,
trimming it to two seconds and restoring order produced a saved four-second
edit. All four original source hashes and the saved canvas and layer clocks
remained unchanged. Its CPU export was H.264/AAC, 608 × 352, 24 fps, 96 frames
and four seconds. Chrome decoded it and played to the end without a media error.
The draft and exported output retain the source privacy controls.

The local acceptance receipt and screenshot are retained under
`.artifacts-temp/maestro-editor-remove-20261002/`. Full sequence preview,
overlapping video and transitions remain deferred. The broader sprint stays
active; accepted H3 generation evidence was reused.

No GPU/model work is needed. Human creative approval and Windows acceptance
remain separate from these checks.
