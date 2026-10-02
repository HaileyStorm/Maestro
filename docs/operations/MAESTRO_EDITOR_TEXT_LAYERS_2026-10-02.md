# Native timed text in Editor — 2026-10-02

## Contract

Editor accepts up to eight text layers over its existing one-to-eight video clips.
Use Add text, select a row, enter up to three lines / 160 characters, set start
and end in sequence seconds, and choose Top, Center or Bottom. Text layers can
overlap; later rows render above earlier rows. Remove text changes only the draft.
The source videos remain untouched.

Titles retain absolute sequence times through clip trim/reorder. A shortened
cut may leave titles beyond its end: the draft still saves, and export explains
that those titles must be shortened or removed. A title must last at least one
video frame to export; shorter draft ranges remain editable. Sequence time uses each clip's
frame-rounded exported length; preview plays the selected clip, not the full join.

Preview and CPU export use the bundled DejaVu Sans font. White text and a
translucent black rectangle fit the canvas width. Font, color, free positioning,
animation, extra audio/image layers and overlapping video are not yet editable.
Long lines shrink; at the minimum font size they compress horizontally to avoid
clipping on very small canvases. A blank title produces no visible overlay but
still uses the saved even-sized export canvas.
Font coverage is limited to the bundled font; scripts/emoji outside its glyph
coverage have not been accepted. Browser and Pillow rasterization can differ
slightly; pixel-identical text rendering is not claimed.

## Source, privacy and finality

Save accepts only trim/order and the closed title format; assets, source identity,
canvas and other persisted fields remain server-owned. Unsupported text effects
and populated unsupported tracks fail export rather than disappearing. Title
items keep overlap/stacking order in persistence; generic other lanes retain
existing sequential normalization.

Submission seals a validated title plan, canvas, saved revision and all source
identities in the existing private queue recovery request. Dispatch and publication
recheck every source. Titles are local RGBA images made with Pillow; user text
never enters FFmpeg filter expressions, host paths or shell code. Numeric overlay
intervals are half-open (start included, end excluded). Existing cancellation,
staging, collision refusal, atomic sidecar and source privacy combination apply.
A titled output has no inherited generation recipe: its sidecar retains title/cut
provenance and the Gallery cannot offer a misleading source regeneration.

A single titled cut keeps every source audio stream and source frame cadence.
A joined sequence keeps the existing canvas/fps, first audio stream per clip or
silence, exact frame boundaries, and finite duration. Output remains H.264/AAC MP4.

## Verification checkpoint

Focused evidence: 42 affected backend checks, then two focused real-media checks
after the final width repair (one repeated and one new), 13 UI helper/API checks,
two synthetic desktop/mobile browser cases,
TypeScript/Vite build, scoped ESLint and e2e TypeScript. The real CPU fixture
checks title absence before/at end, presence across a join, literal filter-like
punctuation, all single-cut audio streams, exact frame count and immutable source.
Additional regressions cover sub-frame export refusal, malformed tracks,
blank titles on odd source dimensions, top/bottom placement, overlapping row
order and long-line fit on a small canvas.
Synthetic browser evidence covers title add/edit/remove focus, save/reopen,
Back during a concurrent save, private reveal, viewport reflow and serious/critical
accessibility violations. These tests are separate from live runtime acceptance.

Independent review identified the sub-frame and blank-title canvas cases; both
were fixed and verified. No full suite or GPU workload was run for this milestone.

Live native Chrome acceptance: two overlapping titles saved and reopened,
preview was empty before their intervals and showed both inside them, and the
one-frame export warning blocked a too-short range. A real CPU export appeared
in Gallery and played to its end with no media error. It retained 608×352,
24 fps / 350 frames, AAC audio and a 14.583-second duration; full FFmpeg decode
passed and the original hash was unchanged. Its private sidecar sealed both
title plans and canvas, with no inherited generation recipe. Local and stable
health/readiness passed after the coordinated Pinokio restart; the exact restart
notice cleared through the launcher.

Live desktop controls and mobile DOM reflow/touch targets were inspected. The
mobile visual capture uses the same native component in the synthetic Chromium
fixture because live Chrome capture emulation returned a scaled image. The
sanitized image-first evidence record validated successfully. Browser zoom,
Windows, variable-frame-rate timing and human visual-quality acceptance remain
unverified.

Live titled join acceptance: two retained test videos were trimmed to two seconds
each, saved and reopened as a four-second draft. Top and bottom titles spanned
the two-second seam. The native queue produced a 608×352 / 24 fps MP4 with
96 video frames and four seconds of AAC audio. Full decode passed, both source
hashes were unchanged, and Gallery playback ended without a media error. Its
private sidecar retained both source identities, both absolute title intervals,
the canvas and saved revision, with no inherited generation recipe. The existing
real CPU fixture remains the detailed title-pixel boundary evidence; live playback
is technical media acceptance and does not establish human visual quality.
