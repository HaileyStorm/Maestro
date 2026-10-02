# H3 opening fade: private decode evidence

## Purpose and acceptance

The retained same-seed Guide comparison did not fix the opening fade and
introduced a dark ending. Its measurements are retained in
[GPU acceptance](GPU_ACCEPTANCE.md). Another denoising run needs evidence
inside the learned decode path.

This change adds a disabled-by-default observation for one host-selected H3
job. It does not change generation parameters, normalization, guide positions,
frame count, encoding, or creative content. It is not a quality fix.

Capture stages, in order:

1. Final normalized target latents, after condition rows have been excluded.
2. Raw VAE output, before the production pixel conversion and clamp.
3. Model video received on CPU by WGP, before trimming and postprocessing.
4. Final chunks handed to the encoder, after trimming and postprocessing.

The latent snapshot is an independent CPU copy. Pixel stages retain per-frame
RGB means, extrema, clamp fractions, and 64 × 36 previews in contact sheets.
They do not retain a full decoded tensor. Preview resampling uses Torch area
resampling; previously retained FFmpeg-scaled numbers use a different
resampler and should not be treated as exact pixel equality.

## Exact local activation

Use a fresh checkout-bound GPU lease for the actual model workload, validate
the whole planned operation and stop margin before starting, observe coherent
authority at most ten seconds apart, then unload and withdraw. A capture
selector does not grant GPU authority.

The backend must have `MAESTRO_H3_DECODE_CAPTURE_PLAN` set to an operator-owned
JSON file inside the resolved checkout's `.artifacts-temp` tree. No HTTP
parameter or saved recipe enables this observation. Restart through the
coordinated Pinokio protocol when changing the backend environment.

Create the job with the queue paused, record its actual task parameters, and
write this selector before releasing it. Values below are schema examples,
not permission or executable evidence:

```json
{
  "schema_version": 1,
  "revision": "full-current-git-revision",
  "directory": ".artifacts-temp/operator-run/capture",
  "match": {
    "job_id": "exact-held-job",
    "task_index": 0,
    "model_type": "minimax_h3",
    "seed": 935314058,
    "resolution": "1344x768",
    "video_length": 124,
    "num_inference_steps": 28
  }
}
```

The capture parent must already exist; its final directory must be new.
Selectors outside private artifacts, stale revisions, mismatched jobs or
tasks, multiple repeats/batches, source continuation, and oversized or invalid
selectors are disabled. Execution records the actual seed, canvas, frame
count, repeat, window, FPS and selected model filename. A changed execution
invalidates the evidence. Source file digests accompany the revision because
a dirty checkout alone cannot be identified by HEAD.

The manifest and media remain private local artifacts. The runtime callback
is removed before saved settings and sidecar serialization. Clear the
temporary environment value and restore queue state after the run.

## Interpretation and limits

Only a manifest with `complete: true`, all four stages, a matching execution,
and no errors is complete decode evidence. An absent manifest, rejected
selector, duplicate stage, capture failure, interrupted generation, or missing
stage is incomplete. Observation failure preserves generation; it does not
turn missing evidence into acceptance. Successful capture does not prove
successful encoding or publication; verify the final media separately.

Compare the stage measurements with the encoded media to locate the earliest
observed fade. Retained latents allow decoder replay under a fresh lease
without repeating denoising. Context-shifted or padded latent replay can
demonstrate decoder sensitivity, but cannot uniquely assign the cause to
the denoiser or decoder. Stronger attribution needs an independently verified
reference decode of identical latents. Human visual acceptance stays separate.

Copies and measurements synchronize CUDA and can alter timing or memory
pressure. This diagnostic is not an uninstrumented performance benchmark.

## Verification checkpoint

CPU checks passed: 12 capture tests, 8 preview tests, 11 existing loaded-profile
observation tests, and 1 shared native decode recipe test. Source compilation
and diff checks passed. The installed runtime has no pytest; these checks used
its existing unittest runner with CUDA hidden. No package installation or
full-suite rerun was needed.

Independent review identified a risk from diagnostic arithmetic on CUDA.
Each observed pixel frame now moves to an independent CPU copy before
conversion, reduction, or resampling. The focused capture checks passed after
that correction, and the scoped review reported no remaining blocker.

No learned capture, decoder replay, opening-quality improvement, or human
acceptance is claimed by this source checkpoint. Record live results after
the instrumented workload separately.
