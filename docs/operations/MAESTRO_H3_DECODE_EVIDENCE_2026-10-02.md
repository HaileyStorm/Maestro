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
