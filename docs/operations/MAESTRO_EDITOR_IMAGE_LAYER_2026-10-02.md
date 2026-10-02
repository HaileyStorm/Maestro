# Native timed still image in Editor — 2026-10-02

## Delivered behavior

Add up to eight independently timed static PNG, JPEG or WebP images from the current project's loaded Gallery to
an existing video edit. Choose its absolute sequence start/end, size from
10–100%, opacity from 0–100%, and top, center or bottom placement. Removing
it changes the draft only. Original video, audio and image files stay intact.
If no image is listed, return to Gallery, show Images, load the desired image,
then show All or Videos and reopen the edit.

Images sit above video and below titles. Later rows appear on top; select a row
to edit or remove it. Repeated imports have independent server-owned identities. It keeps its aspect ratio, EXIF
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
side. Arbitrary drag/rotation, overlapping video and live mixed audio remain
later Editor work.

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


## Multiple image rows

The ordered image lane now retains up to eight rows. Saving edits/removes each
existing row by its server-owned identity, preserves overlap and order, and
removes only its corresponding asset. Every interval must fit the export;
selecting a valid row cannot hide an invalid neighboring row.

Every imported image participates in privacy, source revision checks, indexed
recovery inputs, dispatch and publication rechecks, including zero-opacity
rows. Preview reveal stays per source identity. CPU composition uses unique
staging files and ordered inputs before titles and the audio bed. Existing
single-image queued jobs retain their scalar input binding through a bounded
adapter; new jobs carry ordered lists under the existing recovery keys.

Focused evidence: 27 project checks, 26 route/recovery/finality checks, 13 real
CPU media checks, 19 UI helper/API checks and four synthetic browser runs across
desktop/mobile layouts in Firefox and Chromium. The media packet covers overlap,
alpha, join boundaries, titles above both stills, an audio bed and unchanged
single-cut decoded audio. Browser checks cover independent private requests,
keyboard row selection, preserved neighboring edits, all-row export guards,
remove focus, overflow and no serious/critical accessibility findings.
TypeScript, scoped ESLint, production build, source compilation and publication
guard passed. One independent review found an audio test selector regression;
it was corrected. The full suite was not rerun under the owner's frequency
preference. Live release and human acceptance are recorded separately below.


### Multiple-row live release

The coordinated Pinokio restart loaded the new backend and production build.
Local and stable-share health/readiness passed using a browser User-Agent; the
stable edge rejected Python's default User-Agent with HTTP 403. The launcher's
exact restart notice was cleared and authenticated status returned empty.

A live four-second source cut accepted two distinct Gallery stills, saved with
intervals 1–3 seconds at top and 2–4 seconds at bottom. An out-of-cut interval
blocked export until corrected. The completed private composite contains both
ordered, redacted image plans and is H.264/AAC, 608 × 352, 24 fps, four seconds
and 96 video frames. Full decoding passed. Audio retains its length through
the existing AAC re-encoding path; decoded bytes differ from the source AAC,
so this live result does not claim byte-identical source audio. The focused
controlled media checks above isolate whether image composition changes audio.
Both image rows reopened with their independent intervals intact. Human
creative approval remains open.
