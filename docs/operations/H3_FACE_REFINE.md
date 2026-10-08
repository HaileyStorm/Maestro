# H3 face crop preparation and composition

This CPU tool prepares a clip of reviewed face crops and puts an explicitly
supplied replacement crop clip back into the original video. It is the first
implemented stage of the selected [H3 FaceRefine adaptation](https://github.com/Carasibana/ComfyUI-H3-FaceRefine).
Automatic face detection, identity tracking, native-weight crop regeneration
acceptance, feathered masking and a Gallery editing interface remain unfinished.
An experimental processed-tool API now connects reviewed Gallery sources to
Queue, private crop generation, composition and durable publication.
The CPU CLI does not run a model, install packages, download assets or promise
better faces. A separate private native sampler path is described below.

The current stage takes zero-start, constant-rate 24-fps videos, with at most
345 frames, 4096 pixels per side and 64 MiB per encoded input. Composition
caps combined decoded source and replacement RGB at 512 MiB. Crops have a
32-pixel-aligned canvas, 64–1536 pixels per side. Clips on another clock are
refused rather than resampled. The complete source frame count is retained;
the private native sampler accepts only exact Base-H3 lattice lengths, without
silently extending or trimming a crop clip.

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
operator CLI. The separate experimental HTTP job API is described below; it
reuses project authorization, source policy, source revision and the existing
durable processed-tool publication/recovery contracts.

Regression command:

```sh
app/env/bin/python -m unittest discover -s tests -p test_h3_face_refine.py
```

The real CPU checks cover inverse geometry, original pixels outside repairs,
unresolved frames, full-canvas round trips, multiple audio streams and audio
packet/timestamp preservation. They do not establish native H3 generation,
automatic subject tracking, human face quality or other-host acceptance.

## Private native sampler

`MiniMaxH3Model.generate(..., _h3_face_refine=payload)` now supports crop-video
initialization behind `MAESTRO_H3_FACE_REFINE_EXPERIMENTAL=1`. This is an internal
tensor handoff, excluded from saved settings and ordinary output metadata. Its caller
must own source/plan validation, exact decoding, existing model residency and
a fresh GPU-coordinator grant. The CPU CLI has no native-generation command.
The private worker decoder and WGP transport are implemented below. The
experimental job adapter adds a second gate; neither gate is enabled by this
source change.

`models.minimax_h3.face_refine.H3FaceRefinePayload` contains:

- `video`: exact CPU float32 RGB `[1,3,frames,height,width]`, in `[0,1]`, bounded
  to 512 MiB. The native VAE deterministically encodes its posterior mode using
  the loaded VAE statistics; it never resizes, pads or trims the supplied crop.
- `strength`: positive denoise strength, at most one. The video scheduler builds
  `int(steps/strength)` full intervals, then keeps the last requested number of
  evaluations. The full grid is capped at 4096 intervals.
- `frame_multipliers`: a tuple of one explicit `[0,1]` multiplier per decoded
  frame. They map linearly to latent frames with aligned endpoints, following
  upstream FaceRefine. Zero holds the source; one fully advances the prediction.
- `waveform`: optional selected-source CPU float32 stereo `[2,samples]` at
  32 kHz. Its exact conditioning clock is `round(frames/24*40)*800` samples.
  It is encoded through the loaded audio VAE's posterior mode and native
  statistics. Both channel blocks stay at clean conditioning time and their
  source rows are restored after every paired prediction.

The admitted first path is one independent Base output at exactly 24 fps,
124–345 frames with `frames % 17 == 5`, and a 32-pixel-aligned canvas at most
1536 pixels per side. Use 2–100 evaluations and exactly
`custom_settings={"h3_attention_engine": "sdpa"}`. References, keyframes,
guides, Control, continuation, source-audio experiments, LoRAs, cache, Turbo,
Lightx2V, Spectrum and PDD are rejected before encoding. In particular, a
22-frame CPU preparation fixture is not a supported native sampling window.

Only generated video rows are initialized from source crops. At every Euler
step, held rows are mixed with the source re-noised to the next sigma; the
multiplier stays in the sampler and does not change model token timesteps.
Both modality updates publish together and cancellation resets both clocks.
Ordinary requests retain their existing sampler arithmetic and random draws.

With no waveform, audio retains its independently generated ordinary clock.
With a waveform, source audio conditions the joint transformer and remains
locked throughout sampling. In both cases discard the generated audio export
and use the CPU compositor to preserve every original encoded audio stream.
Full-weight audio-conditioned face quality remains unverified.

## Sealed worker handoff

`services.h3_face_refine_worker.make_face_refine_dispatch` accepts the original
source file, prepared crop file and plan, plus the explicitly expected plan
SHA, strength, frame multipliers, sampling steps and audio-stream ordinal.
It snapshots both files, rechecks byte commitments and full video clocks, and
decodes exact RGB crops without resizing or resampling. Unresolved rectangles
require multiplier zero. Native payloads remain capped at 512 MiB; the worker
also bounds coexisting captures and validation/pipe scratch to 2 GiB before
decoding.

Choose `audio_stream=0` for the first source audio stream, `1` for the second,
and so on. The choice is required; `None` deliberately disables source-audio
conditioning. A missing selected stream fails rather than choosing another.
The conditioning copy preserves initial timestamp gaps as silence, then pads
or trims to the rounded 40 Hz clock. For example, 124 video frames need 207
audio ticks, or 165600 stereo samples at 32 kHz. This conditioning-only rounding
does not change the original clip or the final compositor's packet copy.

The returned `H3FaceRefineDispatch` contains no file paths. An owning generation
worker passes it as `wgp.generate_video(..., _h3_face_refine_dispatch=dispatch)`.
WGP revalidates and captures it before model preparation, preserves the exact
native frame count, and forwards its typed payload to H3. It excludes the
dispatch from UI setting enumeration and refuses OOM relief that would silently
retry with different geometry, steps or profile. Private crop runs do not
calibrate ordinary generation limits.

This handoff grants no project or compute authority and publishes no media.
The dedicated job adapter owns source revision/privacy checks, a single-use
private result sink, crop-result provenance, compositor integration and durable
publication/recovery. Operator-driven native qualification still requires its
own generation inputs, model residency and fresh exact GPU grant.

The long-grid tail and temporal hold mapping follow
[upstream nodes.py at the pinned revision](https://github.com/Carasibana/ComfyUI-H3-FaceRefine/blob/d8521d14fe0d721d80cd9417fff5a559cbc21aba/nodes.py).
CPU checks exercise the native VAE and transformer with miniature random
weights, exact held rows, ordinary seeded parity, and cancellation followed by
ordinary generation. They prove numerical wiring, not full-weight CUDA
execution, lip sync or improved faces:

```sh
app/env/bin/python -m unittest discover -s tests -p test_h3_face_refine_native.py
app/env/bin/python -m unittest discover -s tests -p test_h3_face_refine_worker.py
```

Worker checks use real CPU FFmpeg crop/audio decoding, including an explicitly
selected delayed second audio stream. Source-audio sampler checks use an audio
encoder fixture to prove normalization, packing, clean token timing and row
locking; they do not establish native audio-VAE weight acceptance.

## Experimental reviewed-source job API

`POST /api/v1/tools/h3-face-refine` is available only when both
`MAESTRO_H3_FACE_REFINE_EXPERIMENTAL=1` and
`MAESTRO_H3_FACE_REFINE_GALLERY_EXPERIMENTAL=1`. Keep it disabled pending
full-weight native execution, cancellation and face-quality acceptance. The
current checks use real CPU media and model-free job execution; they do not
establish CUDA execution, improved faces or live browser/LAN acceptance.

Select a final Gallery video by `workspace`, `name` and its current `revision`.
The server requires project generation permission, authorized source access,
current model visibility, H3 legal admission and recipe terms. It captures the
source bytes, clock and privacy policy in the private durable request manifest
before a worker starts. Caller paths, tensors, result sinks and unknown fields
are refused. Source bytes, revision and policy are rechecked before generation,
composition publication and recovery. Private or explicit sources retain their
corresponding output flags; callers cannot lower them.

Prepare `face-request.json` from reviewed observations. For a 124-frame clip,
this Python example constructs the complete array; replace the example boxes
with the actual per-frame review and use the selected Gallery revision:

```python
import json
boxes = [[16, 16, 48, 48] for _ in range(124)]
boxes[5:8] = [None, None, None]
request = {
    "workspace": "my-project", "name": "source.mkv",
    "revision": "REPLACE_WITH_CURRENT_GALLERY_REVISION",
    "prompt": "Describe the intended appearance and movement.",
    "observations": {"shots": [0, 62], "boxes": boxes,
                     "canvas": [64, 64], "padding": 1, "smoothing": 3},
    "strength": 0.5, "frame_multipliers": [0 if box is None else 0.8 for box in boxes],
    "audio_stream": 0,
    "settings": {"num_inference_steps": 20, "seed": 42, "override_profile": 3},
    "private_output": True, "explicit_output": False
}
with open("face-request.json", "w") as output:
    json.dump(request, output)
```

`audio_stream` is required: use an ordinal or `null` to disable conditioning.
Sampling settings accept only steps 2–100, a fixed nonnegative seed and integer
profile 1–5. Strength must be between `steps/4096` and one. Multipliers have
exactly one value per source frame and must be zero for unresolved frames.
The full clip must have 124–345 frames with `frames % 17 == 5`, at exact 24 fps.
Memory bounds are checked before pixel decoding and model work.

Use the normal authenticated API session and origin rules documented in
[README](../../README.md#api-examples). From the Maestro browser origin:

```javascript
const response = await fetch('/api/v1/tools/h3-face-refine', {
  method: 'POST', credentials: 'same-origin',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(reviewedRequest)
});
if (!response.ok) throw new Error(await response.text());
const {job_id} = await response.json();
```

With an authenticated Python session:

```python
result = session.post(base + "/api/v1/tools/h3-face-refine",
                      headers={"Origin": base}, json=request)
result.raise_for_status()
job_id = result.json()["job_id"]
```

With the existing authenticated cookie jar and dynamically discovered `BASE`:

```sh
curl -fsS -b cookies.txt -H "Origin: $BASE" -H 'Content-Type: application/json' \
  --data-binary @face-request.json "$BASE/api/v1/tools/h3-face-refine"
```

The response is `{job_id, status: "queued"}`. The dedicated worker uses the
existing generation lock, native GPU execution slot, local-LLM exclusion,
current runtime admission and sealed offload plan. It appends typed crop input
and a result sink only after manifest parsing. WGP captures the generated crop
as private FFV1 and returns before ordinary output encoding, sidecars, Gallery
files or callbacks. Generated audio is discarded. The sealed CPU compositor
restores original pixels outside the crop and every original audio stream.
Only that verified composite enters the existing atomic media/metadata
publisher. Its metadata records `face_refine` provenance and has `params: null`,
so a composite is not offered as an ordinary whole-frame generation recipe.

Private attempt directories, including failed or cancelled samples, remain
hidden beneath the project for inspection. A bare crop or composite is not a
ready Gallery result. Interrupted unsealed generation stays held until an
explicit owner Retry; remote recovery also requires owner reauthentication.
A sealed final publication can be reconciled without loading H3 or denoising
again. Cancellation and foreign or changed publication members retain the
existing processed-tool finality and cleanup protections.

Run the focused integration checks with:

```sh
app/env/bin/python -m unittest discover -s tests -p test_h3_face_refine_job.py
```

There is no automatic detector, identity inference, feathering, new Gallery
repair editor or public activation in this change. Native weights, lip sync,
face quality and human acceptance remain separate unfinished evidence gates.
