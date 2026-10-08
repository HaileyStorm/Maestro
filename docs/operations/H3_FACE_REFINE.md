# H3 face crop preparation and composition

This CPU tool prepares a clip of reviewed face crops and puts an explicitly
supplied replacement crop clip back into the original video. It is the first
implemented stage of the selected [H3 FaceRefine adaptation](https://github.com/Carasibana/ComfyUI-H3-FaceRefine).
Automatic face detection, identity tracking, H3 crop regeneration, feathered
masking and Gallery/Queue integration remain unfinished. It does not run a
model, install packages, download assets or promise better faces.

The current stage takes zero-start, constant-rate 24-fps videos, with at most
345 frames, 4096 pixels per side and 64 MiB per encoded input. Composition
caps combined decoded source and replacement RGB at 512 MiB. Crops have a
32-pixel-aligned canvas, 64–1536 pixels per side. Clips on another clock are
refused rather than resampled. The complete source frame count is retained;
a future H3 executor must resolve its temporal lattice explicitly.

Each frame has a reviewed rectangle in source pixels, `[x0, y0, x1, y1]`, or
`null` when the subject is unresolved. Coordinates must stay inside the source.
The caller supplies shot starts. Smoothing uses only a contiguous sequence of
observations in one shot; it resets at missing observations and cuts. There is
no detector, inferred identity, other-person fallback or gap interpolation.
Missing frames are black in the crop preview and retain their entire original
decoded image during composition.

For a four-frame clip, an observation document can be:

```json
{
  "shots": [0],
  "boxes": [[16, 16, 48, 48], [18, 16, 50, 48], null, [24, 16, 56, 48]],
  "canvas": [768, 768],
  "padding": 1.5,
  "smoothing": 3
}
```

From the repository root, use the installed Pinokio Python environment:

```sh
app/env/bin/python scripts/h3_face_refine.py prepare \
  --source source.mp4 --observations observations.json --destination face-crops
```

`face-crops/crops.mkv` is a lossless FFV1 intermediate. `plan.json` seals the
source bytes and geometry, reviewed observations, actual clamped inverse
rectangles and crop bytes. It is written last and marks successful preparation.

After separately obtaining a replacement crop clip with exactly the same
canvas, frame count and clock, pin its bytes before composition. For example,
the digest can be obtained with Python's `hashlib.sha256` or `sha256sum`:

```sh
app/env/bin/python scripts/h3_face_refine.py compose \
  --source source.mp4 --replacement replacement.mkv \
  --replacement-sha256 sha256:REPLACE_WITH_64_HEX_DIGITS \
  --plan face-crops/plan.json --destination face-composite
```

`face-composite/composite.mkv` is a lossless intermediate with rectangular
pastes. Pixels outside each recorded crop, and all unresolved frames, remain
unchanged after decoding. Original audio streams are copied without
re-encoding or truncation. Before writing `receipt.json`, the tool checks video
timing and geometry, every audio packet's digest, stream order, codec and
relative timestamps to within Matroska's one-millisecond time base. Unsupported
audio or alignment changes fail before a completion receipt is published.
FFV1 intermediates need a compatible media player; browser delivery remains a
separate export step.

Both destinations must be new directories. Files are private to the local
owner; source media and existing destinations are never overwritten. A failure
can leave an incomplete private directory for inspection, without its final
readiness marker. Retain it as failure evidence or choose a new destination;
do not treat a bare media file as a completed bundle. Ctrl-C and termination
request cancellation; owned FFmpeg children are stopped and reaped through the
existing bounded media lifecycle.

The Python entry points are `services.h3_face_refine.prepare_crops` and
`compose_crops`, both with an optional `cancel_check` callback. This is an
operator CLI, not an HTTP endpoint; there is no JavaScript or Curl API yet.
Callers adding a future HTTP/Queue route must preserve project authorization,
source policy, source revision and durable publication/recovery contracts.

Regression command:

```sh
app/env/bin/python -m unittest discover -s tests -p test_h3_face_refine.py
```

The real CPU checks cover inverse geometry, original pixels outside repairs,
unresolved frames, full-canvas round trips, multiple audio streams and audio
packet/timestamp preservation. They do not establish native H3 generation,
automatic subject tracking, human face quality or other-host acceptance.
