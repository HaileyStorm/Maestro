# Editor mixed audio preview — 2026-10-03

## Delivered behavior

The existing selected-clip preview can play the saved audio layer alongside the
video's original sound. **Include audio layer in preview** starts enabled;
turning it off stops only the layer. This is a browser playback choice and does
not change the saved draft or export.

Audio follows the selected video's media clock. Sequence offsets use the same
frame rounding as the existing title/image preview, and source trimming maps
the overlap back to the audio file. The layer plays only inside the selected
cut and its own half-open timeline interval. Saved gain, mute and linear fades
use the same gain function as the existing audio audition. Fades overlap by
multiplication.

Pause, seek, video underflow, clip changes, draft edits, pending save/import/export,
manual audition and leaving Editor stop mixed playback. Resumption seeks the
layer back to the video's current clock. A failed audio load or play request
shows an explicit retry; retry pauses the video and requires Play to resume.

Private audio is fetched only after the existing explicit browser-local reveal.
Both audition and mixed playback use the same output identity/revision reveal
state and the existing revision-pinned, authorized preview URL. No source,
export plan, saved schema, content inspection or provider behavior changed.

## Focused verification

- 23 Editor helper/API/clock checks passed, including absolute placement on a
  later trimmed clip, source clock mapping, half-open endpoints, mute/zero gain,
  reordering and the existing audition/clip/image/title transformations.
- Eight synthetic browser cases passed across Chromium and Firefox, using
  desktop and mobile viewport sizes and real CPU-encoded H.264/AAC video plus
  PCM audio. Checks cover clocks, fade gain, seeks, pause, disable/re-enable,
  audition, leaving Editor, private no-fetch/reveal, mute and failed-load retry.
  Video underflow/resumption uses deterministic ready-state/event injection;
  this does not establish recovery from a real network outage.
- The private/muted case passed the serious/critical accessibility check.
- TypeScript, e2e TypeScript, scoped ESLint and production build passed. The
  existing bundle-size warning remains. One bounded independent read-only
  review found no actionable correctness defect; its missing stall/retry
  coverage was added to the focused cases above.
- The owner asked to run full suites less often. No full suite or backend/GPU
  workload was run for this UI-only change. Existing accepted backend/export
  evidence was reused; this milestone does not refresh those gates.

## Running app acceptance

The rebuilt UI was exercised in the signed-in local app against the retained
four-second, two-clip layered draft, at revision 24. Both clips loaded and played
with the audio layer; the second clip applied its two-second sequence offset.
Two single observations differed by about 9 ms and 45 ms between the video's
mapped clock and audio clock. These are playback observations, not latency or
quality benchmarks. The layer stopped at the cut boundary. Turning off the
checkbox stopped audio while video continued.

The draft and all eight imported source/sidecar files retained their hashes.
The checked preview choice was restored and the saved draft left open. Local
health/readiness passed. Only the static UI build was refreshed; no additional
backend restart was needed.

## Remaining acceptance

This preview covers one selected clip at a time. Browser media clocks, volume
controls and autoplay behavior do not establish sample-exact export parity.
Physical iOS Safari playback/volume behavior and human listening/mix quality
remain unverified. The existing exported MP4 is the artifact for final review;
full-sequence preview and multiple audio layers remain separate work.
