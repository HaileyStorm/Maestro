# Native timed audio in Editor — 2026-10-02

## Contract

Editor accepts one audio file from the current project's loaded Gallery. Add
audio, choose its source start/end, set an absolute sequence start, and adjust
volume from 0–100% or mute it. Remove audio changes the draft. Original files
remain untouched. If no audio is listed, return to Gallery, show Audio and load
this project's audio, then reopen the edit.

The trimmed interval mixes with original sound without looping or extending
the video. Its sequence time stays fixed when clips are trimmed or reordered.
A shortened cut can leave audio beyond its end: the draft saves, and export
requires adjusting or removing that audio. Additive mixing preserves original
volume; lower the audio layer volume if the mix distorts.

Video preview plays the selected video's original sound. Audition audio plays
only the selected audio slice, at its saved volume. Private audio requires an
explicit reveal. Audition stops on edits, clip changes, video playback, pending
Editor operations and leaving Editor. The combined soundtrack is available in
the exported MP4. Live mixed preview, multiple audio layers, fades, looping,
image layers and overlapping video remain outside this slice.

## Source, privacy and export

The server imports an authorized same-project Gallery source, probes its audio,
and pins both media and sidecar content. Client-supplied paths, media facts,
source identities and effects are not accepted. Saves use the existing revision
check; submission seals a closed audio plan with current source identities.
Dispatch and publication recheck every source and its current privacy policy.
Audio is an explicit input in the existing queue recovery descriptor. A muted
or zero-volume private layer still contributes its source privacy.

CPU FFmpeg mixes at a 48 kHz sample clock, with finite delay/padding and no
automatic attenuation of original sound. A single cut keeps every original
audio stream and its channel layout; a file without a speaker mask retains its
channel count using FFmpeg's standard default. A silent cut gains one stereo
stream when the layer is active. A sequence keeps the existing first audio
stream per clip or silence, then adds the layer to that stereo mix. Muted or
zero-volume layers preserve the prior original-only encoding behavior.

Output remains H.264/AAC MP4, with existing staging, cancellation, collision
refusal and atomic sidecar publication. Titles and exact sequence frame
boundaries remain supported. Audio transforms retain source revision,
source trim, timeline start, gain and mute provenance. A mixed output has no
inherited generation recipe. Missing source policy retains the existing
live-only export behavior and blocks restart recovery.

## Verification checkpoint

Focused evidence: 49 affected backend checks, one additional six-channel real
media check after the missing-mask repair, and a focused finality check for a
changed source before a distinct export publication. Fifteen UI helper/API
checks and two synthetic desktop/mobile browser cases passed. TypeScript,
e2e TypeScript, scoped ESLint and Vite build passed. The existing bundle-size
warning remains. No full suite or GPU workload was run for this milestone.

Real CPU fixtures verify source trimming, interval delay, gain, mute, exact
original-only behavior at zero gain, multiple original audio streams, six
channels, silent video, a titled join, finite duration and immutable sources.
Synthetic browser cases verify Gallery import pinning, private reveal,
save/reopen, range warnings, export, remove focus, viewport reflow and no
serious/critical accessibility violations. They do not establish live media
playback. One independent read-only review found no actionable findings in
source/finality, privacy, recovery, mixing or audition boundaries.

Live native Chrome acceptance: a retained project audio file was imported into
the saved two-clip/four-second titled draft. Source seconds 1–3, timeline
seconds 1–3 and 25% volume saved and reopened. Audition loaded the pinned audio,
played from its source start without a media error, and paused on mute. This
live source was public; the private audio reveal gate was verified synthetically.

The native queue produced a 608×352 / 24 fps MP4 with 96 video frames, four
seconds of 48 kHz stereo AAC and both existing title plans. Full decode passed,
all three source hashes were unchanged, and Gallery playback reached its end
without a media error. The output remained private through its video sources;
its sidecar retained the audio trim/placement/gain, source revisions, title
plans, canvas and saved revision, with no inherited generation recipe.
Detailed mixing timing/amplitude evidence remains the real CPU fixture.

Local and stable health/readiness passed after the coordinated Pinokio restart,
and the launcher returned the exact restart-notice clear receipt; an authenticated
read confirmed the notice is empty. Live desktop
focus and mobile DOM reflow were checked: no horizontal overflow at 390 px,
44 px controls and a 44 px mute-label target. The mobile visual capture uses
the synthetic native component; its fixed header overlays the top of the tall
panel capture. The sanitized image-first evidence record validates. Windows,
browser zoom, variable-frame-rate timing and human listening quality remain
unverified.
