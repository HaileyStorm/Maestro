# H3 decode capture: recovery checkpoint

## Observed result

The coordinated Pinokio restart loaded source revision
`b474ffd4d61e1d917ada5266d0ef035eaf9ecb9a`, including the private decode
observation hooks and the idle queue pause fix. The live Queue showed
**Paused — queued jobs will not start**. One submitted Guide job remained
waiting with zero running jobs until **Start next** explicitly admitted it.

The matched comparison used base FL2VA, seed `935314058`, 1344 × 768,
124 frames at 24 fps, 28 steps, and the original two still guides at
frames 31 and 90. The capture manifest recorded the matching execution,
selected model filename and source digests. No generation setting was changed
to fix the fade.

The initial coherent authority check covered the displayed 517-second estimate
plus a 120-second stop margin. During denoising, the updated estimate no longer
fit that margin. An additional-time reply did not extend the durable grant.
The job was stopped, its incomplete manifest was retained, and the old backend
exited through a coordinated restart before the exact lease was withdrawn.

No normalized latents or decoded pixel stages were captured in this attempt.
There is no final media, decoder attribution, quality improvement, performance
benchmark or human acceptance from it. The receipt is interrupted execution
evidence only.

## Next acceptance

A separate longer local request is queued. It grants no current GPU authority.
Before another attempt, obtain a fresh coherent grant with enough time for
setup, the complete generation, capture overhead and orderly unload. Start the
bounded authority observer promptly. Recreate the exact matched Guide job
while paused, pin the selector to its actual identity and current revision,
and use a new capture directory so the first receipt is preserved.

After generation, require all four stages, matching execution and no errors.
Compare per-frame values across raw VAE output, model output and encoder input,
then inspect the final encoded media separately. Retained latents may support a
later decoder comparison under its own validated authority. They do not by
themselves distinguish denoiser and decoder causes.

The temporary selector environment is restored before leaving the ordinary
runtime available. Private manifests, source receipts, lease history and the
exact resume instructions remain in the local diagnostic checkpoint.

## Verification scope

The existing focused source evidence is reused: 32 capture-related CPU checks;
two queue endpoint checks, five lifecycle pause checks and 31 affected UI
checks; compilation, build, scoped lint and independent review. This recovery
adds live pause/admission and coordinated restart observations. No full-suite
rerun was performed, following the owner's instruction to run it less often.
