# H3 decode capture: measured recovery result

## Complete matched capture

A longer coherent local GPU grant allowed the matched Guide job to finish at
source revision `6ad0f28d156a01634c7832a5d756337694d0590e`. The private manifest
is complete, has no errors, and binds the execution and source digests. Its four
stages are normalized latents, raw VAE output, model output and encoder input.
All three pixel stages contain 124 frames. The retained float32 latent tensor
has shape `[1, 24, 37, 48, 84]`.

The unchanged comparison recipe uses base FL2VA, seed `935314058`, 1344 × 768,
124 frames at 24 fps, 28 steps and the original still guides at frames 31 and 90.
No generation setting was changed to fix the fade.

The fade is already present in raw learned VAE output. Raw VAE and model-output
RGB means match across every frame; encoder conversion changes those means by
at most 0.5083 on the 0–255 scale. The sampled values below use full-resolution
clipped RGB means for captured stages. Encoded values use FFmpeg area-resampled
RGB; these different measurement paths are not exact pixel comparisons.

| Frame, zero-based | Raw VAE / model output | Encoder input | Encoded media |
| --- | ---: | ---: | ---: |
| 0 | 0.0204 | 0.0000 | 0.0000 |
| 1 | 0.3860 | 0.0428 | 0.0012 |
| 3 | 9.7140 | 9.2164 | 7.8031 |
| 12 | 94.7703 | 94.2714 | 92.6565 |
| 24 | 202.7000 | 202.2005 | 200.6762 |
| 90 | 216.0322 | 215.5259 | 213.8432 |
| 123 | 215.4616 | 214.9554 | 213.2883 |

The final HEVC/yuv420p export has 124 frames, 1344 × 768 resolution, 24 fps and
5.166667 seconds duration. Full FFmpeg decoding passed. Its SHA-256 is
`2fdc7987d46d052bde8eb636dc94b0c470630efdbb2c20d803078086f64b45b5`.
Encoding and the post-VAE conversion did not introduce this opening fade.

## Retained-latent spatial decoder comparison

A bounded standalone child compared native streamed spatial tiles against
buffered spatial tiles followed by `_stitch_tiles`, using the same local VAE
weights, retained tensor, temporal decode and float16 autocast. The buffered
algorithm follows the pinned
[Diffusers MiniMax H3 implementation](https://github.com/huggingface/diffusers/blob/578c9b2c6636ab2424a0e56186268b83623656b2/src/diffusers/models/autoencoders/autoencoder_kl_minimax_h3.py).
This is a comparison of spatial stitching, not an independent pretrained stack.
The configured first existing checkpoint was required before model loading.

The native replay reproduced captured per-frame RGB means with a maximum
error of 0.000031 on the 0–255 scale. Buffered stitching also retained the fade:
its frame 0 mean was 0.0204 and frame 24 mean was 202.6839. Across all frames,
the maximum difference in RGB means between stitching variants was 0.0198.
Changing spatial stitching did not remove the fade in this matched sample.

The variants are not pixel-identical. Maximum raw absolute pixel difference
was 0.9017 at frame 67; the largest per-frame mean raw absolute difference was
0.001138. Raw decoder values use their native VAE scale. These localized
pixel differences need separate spatial analysis before any parity claim.

The retained latent file SHA-256 measured at replay is
`47e589170b73f82f8d8e11527daacb797eb158de6ada455b5fdb52f3d95bc711`;
the VAE file SHA-256 is
`7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522`.
The capture did not store a capture-time latent digest, so provenance rests on
the retained file, matching execution/source receipts and brightness
reproduction. It is not a cryptographic capture-to-replay identity proof.

## Recovery and evidence limits

The earlier shorter attempt was stopped during denoising when its updated ETA
exceeded the remaining lease plus stop margin. Its incomplete receipt is
preserved separately. An additional-time reply did not extend that grant.

The successful capture and replay used fresh whole-operation authority checks
and five-second coherent observers. The replay supervisor enforced a
480-second deadline and owned only its spawned child. Its direct worker CLI
was removed after independent review. Both variants completed and the child
exited before withdrawal. The exact longer request is now withdrawn, with a
durable denied response and no GPU authority remaining.

The temporary selector environment was restored byte-for-byte, the diagnostic
backend exited through a coordinated restart, and the normal app is available.
Direct and stable-share health/readiness checks return 200; the public restart
notice is cleared. The Gallery identified the HEVC playback limitation and
created a separate local H.264 browser copy while preserving the original.
That copy loaded at 1344 × 768 and 5.17 seconds with no browser media error. Private manifests, latents, source receipts, lease history
and resume instructions remain in the local diagnostic checkpoint.

This result narrows the fade to the learned decode boundary or its incoming
latents. It does not distinguish denoiser-generated latents from the temporal
VAE decoder. No fade fix, continuation-quality acceptance, performance
benchmark or human acceptance is claimed. Production decoder defaults remain
unchanged.

## Verification scope and next acceptance

Existing focused source evidence is reused: 32 capture-related CPU checks;
two queue endpoint checks, five lifecycle pause checks and 31 affected UI
checks; compilation, build, scoped lint and independent review. Recovery adds
live pause/admission, complete learned capture, full media decoding and the
bounded spatial replay. The private replay script compiled, and its rejected
worker entry was checked before GPU execution. No full-suite rerun was
performed, following the owner's instruction to run it less often.

Next, inspect localized spatial differences using the retained data and design
a bounded temporal-decoder comparison before spending another generation.
Any further GPU work requires fresh coherent authority. Keep denoiser,
temporal decoder, spatial stitching, codec and human judgments separate.

## Spatial parity repair after the diagnostic

The measured pixel difference led to a concrete source mismatch: streamed
assembly retained already-blended bottom/right edges. Buffered `_stitch_tiles`
reads the original decoded neighbours. The streamed path now clones those
original edges before blending, retaining its bounded tile memory strategy.

A context-dependent CPU decoder reproduced the difference at corners and
three-way overlaps in float16 and float32 before the repair. After the repair,
all ten dtype/layout combinations match the buffered path exactly, including
single-tile, horizontal, vertical, corner and three-way layouts. Inputs remain
unchanged; a separate disabled-tiling case also passes. Independent review
found no source-level blocker. Compilation and scoped diff checks passed.

This fixes the proven spatial assembly mismatch. The subsequent learned GPU
comparison below verifies parity on the retained sample. Peak memory remains
unmeasured. No temporal or opening-fade improvement is claimed by the repair.

## Learned spatial parity and precision sensitivity

A fresh, coherently validated local lease admitted a supervised decoder-only
replay. The child completed all three variants and exited; its exact request
was withdrawn and the durable response no longer authorized GPU work.

Corrected streaming and buffered spatial stitching produced identical raw
pixels across all 124 frames: maximum absolute and frame-mean absolute
differences were both zero. Corrected RGB means also reproduced the previous
buffered reference exactly. This verifies the spatial assembly repair for this
retained tensor, checkpoint and runtime, without another denoising run.

Upcasting the same compact checkpoint to float32 weights under float16
autocast changed pixels slightly: maximum absolute raw difference 0.0087890625
and maximum per-frame mean absolute difference 0.0003278540. It retained the
opening fade. Corrected opening RGB means on the 0–255 scale were 0.02043,
0.38606, 1.66318 and 9.71831 for frames 0–3; upcast means were 0.02132,
0.38608, 1.66351 and 9.71676. Upcasting cannot restore precision already lost
when the checkpoint was made, so this does not establish full-precision
checkpoint parity or uniquely attribute the fade.

Private replay records retain all frame metrics, latent/checkpoint digests and
executed source identity. Temporal decode, denoiser behavior, independent
pretrained-stack parity, peak-memory measurement and human quality acceptance
remain separate. The next diagnostic should compare temporal decode behavior
or a separately authorized verified checkpoint against these same retained
latents before spending another generation.


## First-chunk temporal isolation

The native temporal assembly matches the pinned
[Diffusers implementation](https://github.com/huggingface/diffusers/blob/578c9b2c6636ab2424a0e56186268b83623656b2/src/diffusers/models/autoencoders/autoencoder_kl_minimax_h3.py#L730).
The opening uses latent indices 0–6, yields 28 raw frames, and trims three
leading frames. Raw frames 3–19 form the first 17 output frames, before any
inter-chunk overlap blend. One bounded read-only review confirmed this mapping.

A fresh exact lease admitted a supervised three-variant clip decode using the
retained tensor and same compact checkpoint. The actual first chunk reproduced
all 17 accepted opening RGB means exactly: maximum mean error zero. The fade
therefore exists before temporal overlap assembly; these retained metrics do
not establish full-pixel equality with the earlier full decode.

Two synthetic controls repeated one latent plane seven times. Repeating latent
0 kept mean RGB near black (0.0789–0.0922 on sampled raw frames 3–19). Repeating
latent 10 produced bright frames (sampled means 156.0456–201.6577). Identical
planes still vary by output frame because the decoder uses temporal positions
and full self-attention. These controls demonstrate input-sensitive behavior;
they cannot uniquely assign cause to the incoming denoised latents or learned
VAE. They are not repaired videos or creative acceptance evidence.

All three variants completed; the supervised child exited, its model was
unloaded, and withdrawal was confirmed by the durable coordinator response.
Private records retain 28 raw-frame metrics per variant, all 37 input latent
statistics, source/checkpoint digests, and a reproducible PNG/SVG plot. No new
generation, downloads, production defaults, model prompt, timing or source
media was changed. The following known-bright round trip tests the installed encode/decode path
without another denoising generation.


## Known bright stationary-video round trip

A distinct fresh lease admitted one deterministic VAE encode/decode round trip
of the existing bright robot still, resized to the matched 1344 × 768 canvas
and repeated for 22 frames. The encoder uses its normal 17-frame chunks and
trailing pad/drop geometry; posterior mode yields seven latent frames, and
normal decode returns exactly 22 frames. No denoiser, prompt encoder or model
transformer runs in this control. Input media is pinned by SHA-256.

Input mean RGB was 203.6659 on the 0–255 scale. Decoded means stayed between
203.1189 and 203.2955 across all 22 frames; frame 0 was 203.2646. The installed
compact VAE can reconstruct this bright stationary input from its first output
frame without the captured fade. That contradicts a universal forced fade in
this encode/decode path. It does not prove all-video quality, official
full-precision parity, or uniquely diagnose how the generated opening latents
acquired their dark content.

The supervised child completed and exited, its owned model/tensors unloaded,
and its exact lease withdrawal was confirmed. The retained plot now includes
this round trip alongside the captured opening and repeated-latent controls.
The next source/runtime comparison belongs to generated latent conditioning
and denoising, while preserving existing prompts, creative content, source
media, timing, defaults and separate human acceptance.

## Ordinary Extend: complete live sample

The earlier matched capture used interior still guides at frames 31 and 90.
It was not an ordinary Extend run. A separate Gallery Extend action now
completed with the preserved browser-compatible source, its last frame as the
first-frame anchor, base FL2VA, 1344 × 768, 24 fps, 28 steps and Sol attention.
The request used a random seed; the Gallery records realized seed `738560997`.
Prompt improvement, creative guides and LoRAs were disabled. The job retained
its private preview flag and finished in 712 seconds.

The source contains 124 frames. Extend generated another 124 frames and
published a joined H.264/AAC output containing 248 frames. Its frame clock is
10.333333 seconds; container duration is 10.334 seconds. Full software FFmpeg
decoding passed for source, generated tail and joined output. Chrome loaded
the joined output at 1344 × 768, played across the 5.166667-second join and
reached the end without a media error. A private browser screenshot records
the generated side of the join.

The original source file remains unchanged, with SHA-256
`1de799b8dcd8cb4e6607b21dd26d240c4d6be61d22a14ffdffbbe751c946d58a`.
The joined output SHA-256 is
`b25ca36ec534e643b17d45e8f47c04db2961bba9ccba71ddb390b72e0306b9bc`.
Its source-prefix descriptor records all 124 retained frames, last-frame
conditioning and preservation of source audio followed by generated audio.
Joining re-encodes video and audio; decoded prefix pixels and AAC samples are
not claimed to be identical. At 64 × 36 area-resampled RGB, prefix mean
absolute difference was 0.065668 and maximum absolute difference was 4.0 on
the 0–255 scale. These are descriptive resized measurements.

The generated tail begins bright: first-frame mean RGB is 211.8501, and its
first 25 frame means remain between 211.8501 and 212.3219. The source's final
frame mean is 213.3487. This sample has no black opening fade in its new tail.
The existing source opening, including its fade, is preserved in the prefix.
This does not establish all-content motion continuity, audio quality or human
acceptance. Native experimental audio/video boundary conditioning remained
disabled; this sample does not qualify that separate path.

## Conditioning layout comparison

A disposable CPU comparison extracted the pure packing geometry from pinned
[Diffusers H3 preparation](https://github.com/huggingface/diffusers/blob/578c9b2c6636ab2424a0e56186268b83623656b2/src/diffusers/modular_pipelines/minimax_h3/before_denoise.py)
and compared it with the native packing helper. It used the captured geometry:
37 video latent frames, a 48 × 84 latent canvas, 207 audio latents, `(1, 2, 2)`
patches and 42 text tags including nine visual tags. Position IDs, token tags,
video/audio/text indices and condition counts matched exactly in all cases.

| Supplied anchors | Packed rows | Tensor mismatches |
| --- | ---: | ---: |
| None | 37,752 | 0 |
| First | 38,760 | 0 |
| Last | 38,760 | 0 |
| First and last | 39,768 | 0 |

The upstream source SHA-256 is
`c0c3484b238b7cf529fb494bcfd02c286db8b34ce3e23fd78a17eeda257764d1`.
This is numerical CPU layout evidence, not learned denoiser or pretrained-stack
parity. No production packing change or full-suite rerun was needed.

## Extend cleanup and next gate

The coherent observer enforced a 900-second total runtime cap. Preparation
consumed part of that interval; the job had completed and published before the
cap stopped the idle owned backend. The supervisor exited with status 1 for
that cap, while its last coherent lease receipt still authorized work. This
was not a coordinator revocation or generation failure. The exact request was
withdrawn and the durable coordinator response confirms withdrawal.

The owner resource-release API required recent reauthentication and returned
403; that gate was preserved. Cleanup used the authorized Pinokio stop/restart
flow. The prior backend exited, the restarted app reports no generation model
loaded, direct and stable-share health/readiness return 200, and the public
restart notice is cleared. Owner resource-release API acceptance remains open.

Continuation audit corrected this next-action note: the experimental native
boundary ON/OFF pair and retained prefix retry already passed execution and
browser playback, as recorded in [GPU acceptance](GPU_ACCEPTANCE.md) under
“Native H3 boundary” and the retained request retry. The matched pair did not
improve the measured seam: resized RGB MAE was 2.746528 OFF versus 3.208623 ON;
the adjacent audio sample jump was 1.418422e-08 OFF versus 0.00058821874 ON.
Keep the native boundary capability experimental and disabled by default.

Reuse that accepted evidence; another identical pair is not the next gate.
The later [cumulative execution and prefix readback](GPU_ACCEPTANCE.md#retained-cumulative-prefix-readback-2026-10-03-utc)
qualified two native windows and a 141-frame publication. Exact decoded prefix
identity was not achieved; broader motion/audio quality and human acceptance
remain open. Preserve ordinary Extend and the earlier interior-guide fade as
separate samples. Any new GPU execution requires a fresh exact coherent grant.

## Conditioning noise and denoising arithmetic comparison (2026-10-03 UTC)

A disposable CPU comparison at source revision `f4c0940` extended the earlier
layout comparison to actual native noise preparation, schedulers, row timestep
plans and paired advancement. It extracted the corresponding methods from
pinned [Diffusers preparation](https://github.com/huggingface/diffusers/blob/578c9b2c6636ab2424a0e56186268b83623656b2/src/diffusers/modular_pipelines/minimax_h3/before_denoise.py),
[paired denoising](https://github.com/huggingface/diffusers/blob/578c9b2c6636ab2424a0e56186268b83623656b2/src/diffusers/modular_pipelines/minimax_h3/denoise.py)
and [scheduler](https://github.com/huggingface/diffusers/blob/578c9b2c6636ab2424a0e56186268b83623656b2/src/diffusers/schedulers/scheduling_minimax_h3.py).
No learned component was loaded and CUDA was hidden.

The captured geometry was retained: video noise `[1,24,37,48,84]`, 414 audio
rows of width 32, `(1,2,2)` patches and seed `935314058`. Synthetic clean
conditioning covered zero, one, two and three stills plus mixed one-, two-
and five-latent-frame references. Condition noise and mixing matched exactly,
as did the subsequent target video/audio noise and generator state.

| Comparison | Tensor checks | Mismatches | Maximum absolute error |
| --- | ---: | ---: | ---: |
| Condition noise, target noise and generator state | 24 | 0 | 0 |
| Video/audio sigma grids and timesteps | 12 | 0 | 0 |
| Row timestep values and indices | 400 | 0 | 0 |
| Paired updates and immutable conditioning slices | 480 | 0 | 0 |
| Total | 916 | 0 | 0 |

Schedules covered 4, 8 and 28 authored evaluations, using equal
terminal-inclusive grid sizes of 5, 9 and 29. The reference counts grid points;
Maestro's UI counts evaluations. Row plans covered no anchors, first, last,
first/last and the original interior frame indices 31/90. The interior cases
compare timestep arithmetic only: upstream layout does not establish Maestro's
arbitrary-frame guide semantics. Paired updates used step-varying synthetic
velocities at the full captured geometry, with no conditioning, two image
conditions, and image plus audio conditions. Every leading conditioning slice
remained unchanged at every step.

Reference SHA-256 values are the preparation hash recorded above,
`4c2bc8b9856c38ba1692b02e35aa68ec92beda4db273e2e71f0ebbf997e19c79`
for paired denoising, and
`307d5bf755337ef00c47237f9ac8be116e627d26e1df3b5f0bd504a80f9de8dd`
for the scheduler. The reproducible private script and per-check report retain
native source hashes and the CPU environment (Python 3.10.20, PyTorch 2.7.0).

This comparison found no arithmetic mismatch to repair. It does not establish
posterior encoding, tokenizer, learned denoiser, attention, checkpoint, GPU or
quality parity, nor does it resolve the two-interior-still fade. Half-precision
scheduler inputs were not tested; the captured generated rows use float32.
The next useful diagnosis is a bounded comparison of learned predictions and
attention/checkpoint behavior on the failing recipe. Repeating an identical
uninstrumented generation would not distinguish these remaining possibilities.
No production change or full-suite rerun was needed for this numerical audit.

## Per-clip Guide attention control (2026-10-03 UTC)

Source revision `c58754f` adds an optional attention choice to Gallery H3 Guide:
model default, Dense SDPA or Sol. An explicit choice applies to the queued clip
and its sealed offload schedule; omission preserves configured model defaults.
The server rejects other values before queuing. Still revision checks, guide
order, seed and inherited privacy/explicit flags retain their existing path.

Thirty focused backend checks, fifteen focused UI checks, scoped lint and the
production UI build passed. One independent source review corrected the copy:
Sol can fall back to dense attention. The rendered form and sealed native
request verified Dense SDPA with the original two-still controls. No full-suite
rerun was needed for this bounded addition.

The older failing sample records requested Sol attention, but its metadata does
not prove effective Sol kernel execution. The new comparison preserves the
original recipe and uses explicit Dense SDPA. A visible improvement alone would
require a fresh current-revision Sol control and effective-kernel evidence before
attributing the difference to attention.

## Matched Dense SDPA result (2026-10-03 UTC)

The signed-in Gallery Guide run at `c58754f` completed in 723 seconds with the
original seed `935314058`, base FL2VA checkpoint, two source stills at frames
31/90, 1344 × 768, 124 frames and 28 steps. The sealed request and published
sidecar agree on those controls and explicit Dense SDPA. Fresh-process runtime
counters remained zero for Sol and Sage kernel calls throughout the run.

Full software FFmpeg decoding passed for all 124 HEVC frames and the stereo
32 kHz AAC stream. Video duration is 5.166667 seconds; audio duration is
5.152000 seconds. The output SHA-256 is
`666dde22846503af3acf930a704e989297b119b503db0b0c3d84195582e9b7dd`.
The public sidecar contains the truthful two-guide execution receipt and no
private guide transport fields. Existing preview flags remain unchanged.

The opening fade persists: frame 0 has mean decoded RGB 0 on the 0–255 scale;
frames 0–2 have at least 99% of pixels with all channels below 10. A retained
measurement plot shows the opening rise alongside the prior sample. This is
positive evidence that Dense SDPA can produce the same failure: Sol attention
is not a necessary cause for this recipe. It does not prove learned-stack
parity or identify the remaining conditioning/checkpoint cause. The prior
sample's effective Sol path remains unproven, and revisions differ.

The original video hash, both source image hashes and both source-sidecar hashes
remain unchanged; the original video sidecar matches its retained JSON content.
The queue became idle before cleanup. The coordinated Pinokio restart stopped
the exact owned backend; the guard's exit records that expected backend exit,
not a generation or authority failure. The lease was withdrawn and a fresh
coherent validation rejects it. Maestro was restored with local/stable health
and readiness verified and the public restart notice cleared. Owner whole-clip
and listening acceptance remain separate.

Reuse this negative comparison. The next diagnosis should inspect how arbitrary
interior guides affect learned predictions and temporal conditioning; another
uninstrumented Dense/Sol repeat cannot isolate those paths.
## Compact final-head precision repair (2026-10-03 UTC)

Source inspection after the matched Dense negative found a separate numerical
mismatch. Pinned [WanGP compact final heads](https://github.com/deepbeepmeep/Wan2GP/blob/fa79896eadbcb048dc13e76233b3b72486b522a8/models/minimax_h3/transformer.py#L334)
normalize in the backbone dtype, convert those normalized rows to FP32, apply
the compact shift/scale in FP32, and then run FP32 output projections. Maestro
previously rounded the shift/scale to BF16 or FP16 before modulation.

A disposable CPU comparison extracted the pinned `FinalLayer` and `AdalnProj`
methods and supplied both paths with the same stored projection values. Three
seeds, two output modalities and three backbone dtypes gave eighteen cases.
Before repair, all twelve BF16/FP16 cases differed; the largest BF16 output
error was 0.00936544. The six FP32 cases matched exactly. After repair, all
eighteen cases match exactly. Eighteen additional chunked-head comparisons
also match exactly on these small tensors.

The compact path now retains FP32 modulation. The original full-width
timestep path retains its previous backbone modulation. Conventional and
Spectrum output heads gather and process at most 8,192 rows per chunk, so the
repair does not require a full packed-sequence FP32 activation. Output order,
separate video/audio clocks, condition-row output geometry and sealed Spectrum
features are preserved. Module calls retain their ordinary pre/post hooks.

One bounded independent review identified per-chunk device synchronization in
clock preparation. The helper now prepares clock runs once and clips the
small run table locally for each chunk. Focused regressions cover that call
count, mixed clocks, reordered output indices, BF16/FP16/FP32 math, chunk
boundaries, module hooks, source immutability and the unchanged full-width
precision path. Twenty-three CPU head/Spectrum tests pass; four additional
existing tiny transformer/projection checks pass on CPU. Compilation,
publication guard and scoped diff checks pass. No full suite was repeated.

This repairs a demonstrated final-head source mismatch. It does not establish
learned checkpoint parity, actual MMGP hook residency, GPU peak memory or
throughput, or an opening-fade improvement. The original two-still negative
remains open. The matched learned run after coordinated rollout is recorded
below. Previous generations must not be relabelled as repaired output.

## Matched native result after compact final-head repair (2026-10-03 UTC)

The coordinated rollout of `dd1346b` completed the original two-still Dense SDPA
recipe in 724 seconds. All sealed request parameters and input descriptors
match the retained Dense baseline exactly: seed `935314058`, guides at frames
31/90, base FL2VA, 1344 × 768, 124 frames, 28 evaluations, profile 1 and no LoRA.
The published parameters and two-guide execution receipt also match. Fresh
runtime counters remain zero for Sol, Sage and acceleration errors.

Full CPU software decoding passed for all 124 HEVC frames and stereo 32 kHz
AAC audio. Video duration is 5.166667 seconds; audio duration is 5.152000 seconds.
The output SHA-256 is
`7f35800f6ffb10d74bd9ee446a7bc1eb420d31b16225c5cdb9e0c69595188e69`.
The published sidecar omits private guide transport fields. Both source stills,
their sidecars, the retained Dense video and its sidecar remain byte-identical.

The opening fade persists after the precision repair. Frame 0 has mean decoded
RGB 0 on the 0–255 scale; frames 0–2 again have at least 99% of pixels with every
channel below 10. Across the first 25 frames, the mean absolute difference in
frame-average RGB brightness from the prior Dense run is 0.412558; the maximum
is 0.916802. The retained before/after plot and sampled frames show closely
matching opening rises. These are two individual samples, not a throughput or
quality benchmark. The proven numerical repair is retained, but it is not a
remedy for this recipe's opening fade.

The fresh exact lease was supervised independently with coherent validation
at five-second intervals. After publication and an idle queue, the coordinated
Pinokio restart stopped the pinned backend and released its model resources.
The guard recorded that expected backend exit; it was not a generation failure.
The exact request was withdrawn, and subsequent coherent validation denies it.
Maestro was restored with local/stable health and readiness passing, no loaded
model, and the public restart notice cleared.
No full test suite was repeated; the accepted CPU precision/regression evidence
is reused, with this native run and focused media/control checks added.

This establishes successful native execution of the repaired conventional
heads for the matched recipe. It does not establish learned checkpoint parity,
peak GPU memory, repeatable throughput, native browser HEVC playback, or owner
whole-clip/listening acceptance. Further fade diagnosis should capture bounded
prediction and temporal-conditioning observations against the pinned learned
path; another unchanged Dense repeat would add little evidence.

## Matched private pinned-rotary clip (2026-10-05 UTC)

One private native clip tested the split-half `mul_`/`addcmul_` rotary
arithmetic from pinned WanGP commit
`fa79896eadbcb048dc13e76233b3b72486b522a8`. The substitution applied only to
the master denoising schedule; production source arithmetic remains unchanged.
The recipe retained seed `935314058`, ordered guides at frames 31/90, base
FL2VA, 1344 × 768, 124 frames, 28 evaluations, Dense SDPA, no LoRA and the
disabled AV boundary option. Requested offload profile 1 resolved to the
observed H3 floor of 5. Model binding and all eight initial packed video,
audio, prompt and guide tensor digests match the retained control. The
worker executed exactly one generation and one complete 28-step schedule.

Generation completed in approximately 795 seconds. Full CPU software decode
passed for all 124 HEVC frames and stereo 32 kHz AAC audio. Video duration is
5.166667 seconds; audio duration is 5.152000 seconds, matching the accepted
final-head-repair control. The private candidate SHA-256 is
`d5e678337efc78438760c51547cbb8639dbbe28469d408f3613cefa10cd0b825`.
The control media was rehashed and retains
`7f35800f6ffb10d74bd9ee446a7bc1eb420d31b16225c5cdb9e0c69595188e69`.

The opening fade persists: both clips have at least 99% near-black pixels in
frames 0–2. Across the first 25 frames, the mean absolute difference in
frame-average decoded RGB brightness is 1.155420 on the 0–255 scale. The
sampled opening frames and brightness curves confirm the remaining fade.
This is one matched clip per condition, not a quality or throughput benchmark.
Pinned rotary arithmetic changes the output but does not remedy this fade.
Do not repeat this comparison or promote the arithmetic as a quality fix.

The standalone worker saved a private output rather than publishing a queue
job or Gallery sidecar. Its model cleanup restored private hooks, and the
supervised child exited with code zero. The fresh checkout-bound lease was
validated every five seconds; withdrawal was acknowledged and a subsequent
coherent authority check rejects the request. Maestro remained online. This
evidence does not establish native browser HEVC playback, whole-clip motion
quality, listening acceptance, or general learned-path parity.

### CPU follow-up: coordinates and historical latent structure

A CPU-only reconstruction preserved the exact video, audio and text row-index
hashes. Source-derived guide positions remain at displayed frames 31/90, with
separate conditioning rows; target latent starts include 30 and 34 around the
first guide. The reconstructed coordinate bytes did not match the retained
live receipt. WGP selects CUDA as the default tensor device after model load,
whereas this audit used CPU. Reduction arithmetic is a possible explanation,
not a verified cause. This check does not establish exact live coordinate
parity or identify a concrete packing defect.

The retained normalized tensor from source revision
`6ad0f28d156a01634c7832a5d756337694d0590e` passed its original hash, shape and
finite-value checks on CPU. Its mean per-channel spatial variance rises from
0.014191 at latent frame 0 to 0.361645 at latent frame 9, then is 0.257556 at
latent frame 10. This is historical evidence predating the head repair and
matched rotary candidate. Spatial variance alone cannot prove decoder meaning,
a causal guide effect, or learned-stack parity. No model was loaded, no GPU
work was performed, and no production change follows from this audit.

### Tensor-only device follow-up (2026-10-05 UTC)

A separately leased tensor-only check now reproduces the retained live
coordinate SHA-256 exactly on CUDA:
`86123c76f2e3e8a1053f51fa9ff0eea6e20c5bc1c656a568098d65cc980bac8e`.
A second construction on CUDA is byte-identical. Video, audio and text
row-index hashes also match the retained receipt and the CPU reconstruction.
No checkpoint or generative model was loaded and no media was generated.

The CPU/CUDA coordinate difference is confined to 1,008 time-coordinate
elements in one late target-video latent frame. The largest absolute
difference is `4.547473508864641e-13`; spatial coordinates and audio/text
coordinates agree. Building only the temporal grid on CPU and then moving it
to CUDA restores the whole CPU coordinate hash. This resolves the earlier
device-arithmetic uncertainty for this exact geometry. It does not establish
a packing defect, learned-path parity, or a cause or repair for the opening
fade. Production arithmetic remains unchanged.

The owned child exited successfully, its exact lease was withdrawn, and a
subsequent coherent validator rejected authority. A later review found and
fixed a private supervisor cleanup gap on watchdog-construction failure.
An owned CPU child verified that failure cleanup while preserving the
injected error; the successful tensor check's original script is retained
separately. No tensor workload was repeated for that supervisor repair.
