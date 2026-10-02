# Native timed still image in Editor — 2026-10-02

## Delivered behavior

Add one static PNG, JPEG or WebP from the current project's loaded Gallery to
an existing video edit. Choose its absolute sequence start/end, size from
10–100%, opacity from 0–100%, and top, center or bottom placement. Removing
it changes the draft only. Original video, audio and image files stay intact.
If no image is listed, return to Gallery, show Images, load the desired image,
then show All or Videos and reopen the edit.

The image sits above video and below titles. It keeps its aspect ratio, EXIF
orientation and alpha. Size is fitted within a box of 90% of canvas width and
88% of canvas height, scaled by the chosen percentage; top/bottom use a 6%
vertical margin. The interval is half open: start is included, end excluded.
It stays at its absolute sequence times when video clips move or trim. A draft
can retain an interval outside a shortened cut; export requires at least one
frame and an end within the rendered cut. Unknown source FPS uses canvas FPS.

Private images require an explicit browser-local reveal before the Editor
requests their preview. Preview failures explain how to refresh/reopen.
Preview shows the selected clip and its current image/title overlays; audio
preview behavior remains [documented separately](MAESTRO_EDITOR_AUDIO_LAYER_2026-10-02.md).
Animated WebP/APNG and other containers are rejected. Export an animation as a
still first. Supported images are bounded to 32 megapixels and 16384 pixels per
side. Multiple images, arbitrary drag/rotation, overlapping video and live
mixed audio remain later Editor work.

## Source and export contract

The image import route accepts only the saved draft revision, Gallery name and
listing revision. The server authorizes the same-project output, decodes the
still, pins media plus sidecar, and rechecks identity/privacy under the existing
lineage guard. Saving accepts bounded appearance/timing and removal; source
identity stays server-owned. Existing drafts acquire the image lane when first
used, without migrating unrelated timeline data.

Export seals the image plan and an explicit recovery input. Sidecarless images
remain live-only, following the existing recovery contract. Dispatch and
publication recheck every source. Output privacy/explicit flags include the
image even at zero opacity. The sidecar records image identity and appearance,
never a host path; composite output has no inherited generation recipe.

CPU rendering decodes an oriented RGBA still into temporary staging and applies
opacity to its alpha. It composes the still before titles, preserving existing
single-cut audio streams or sequence audio plus the optional bed. It uses the
existing queue cancellation, staging and atomic publication machinery.

## Evidence

- Affected schema, route, recovery, finality and real CPU media checks; no full
  suite. Schema includes pinned identity, save/reopen/removal and FPS fallback.
- CPU fixture checks alpha, placement geometry, half-open timing, a sequence
  join, titles above the image, source hashes, retained decoded audio and
  cancelled-render staging cleanup.
- 17 UI transformation/API checks; two synthetic desktop/mobile workflows
  cover private request gating, save/reopen, export guards, remove focus,
  overflow and no serious/critical axe findings.
- TypeScript, e2e TypeScript, scoped ESLint and Vite build passed. Existing
  bundle-size warning retained.
- One independent read-only review found the unknown-FPS fallback mismatch;
  it was fixed with a focused regression check.

The coordinated Pinokio release passed local and stable-share health/readiness;
the exact restart notice was cleared. A retained two-clip draft reopened with
its audio and two titles intact. A Gallery still was imported, saved and
reopened at revision 17 with sequence interval 1–3 seconds, 50% size, 75%
opacity and center placement. Its pinned preview loaded at 848 × 480.

The live composite is H.264/AAC, 608 × 352 at 24 fps, exactly four seconds and
96 video frames. Full decoding passed. Decoded audio equals the prior audio
export byte for byte; hashes of both source videos, audio and image are
unchanged. Sampled frames differ within the image interval, with only small
encoding differences before and after it. Gallery playback reached its end
without a media error.

Native desktop rendering and visible keyboard focus were inspected. At 390px,
the native controls remain at least 44px and the page has no horizontal
overflow. The retained mobile render is from the synthetic browser fixture:
live mobile screenshot capture returned a partial viewport. Browser zoom,
human creative approval, Windows acceptance and broad VFR behavior remain
unverified. No GPU/model inference is required for this feature.
