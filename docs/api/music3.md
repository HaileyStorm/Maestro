# Music3 through Continuum

Use the existing Continuum HTTP API, Python/TypeScript clients and MCP bridge.
There is no separate music server. Follow [API setup and authentication](README.md)
and use the current Maestro URL and an owner-authorized client session.

## Discover and submit

Discover `minimax_music3` in `/api/v1/models`, then read
`/api/v1/model-options/minimax_music3` and `/api/v1/defaults/minimax_music3`.
Installation, accepted terms and native execution readiness are separate states.
Read `/api/v1/host-terms?workspace=PROJECT`; the human must accept the current
Music3 term through the existing flow before download or generation. An AI
cannot supply that acceptance or a private execution authority object.

Submit to `/api/v1/generate` with the existing generation request identity:

```json
{
  "workspace": "PROJECT",
  "model_type": "minimax_music3",
  "generation_mode": "audio",
  "generation_request_id": "PERSISTED-UUID",
  "prompt": "[Instrumental]",
  "alt_prompt": "### Global Metadata\nAcoustic instrumental at 92 BPM in D minor.\n\n### Vocal Details\nInstrumental, no lyrics.\n\n### Arrangement\nFelt piano with a sparse marimba response and a quiet ending.",
  "duration_seconds": 18,
  "num_inference_steps": 30,
  "guidance_scale": 1.7,
  "seed": 456001,
  "private_output": true,
  "_queue_mode": "held"
}
```

For vocals, put sung lyrics and bare section tags in `prompt`; put musical and
vocal direction in `alt_prompt`. Discover current defaults rather than treating
the example as a universal quality preset. `Music3GenerationSettings` in OpenAPI
describes the model-specific projection; other generation fields remain flexible.

Persist the caller UUID before the POST and retain the returned `job_id`.
After an uncertain submission, read
`/api/v1/generate/submissions/UUID?workspace=PROJECT`. Do not create another UUID
or blindly repeat a potentially completed mutation. A held admission starts no
model work. Releasing it requires current execution readiness and fresh exact
GPU authority through the existing owner/coordinator path. The HTTP API does
not issue that authority. See [held queues and recovery](README.md#generation-held-queues-and-uncertain-submission).

## Requested length and measured length

Music3's `duration_seconds` is a **semantic decoding ceiling**, from 5 to 300
seconds, default 120. The model can sample an end token before that ceiling.
Codec rounding can also make the final waveform slightly longer than the
semantic-frame duration. Maestro preserves the native waveform; it does not
automatically pad, stretch or regenerate it to reach a requested length.

`model-options` exposes `duration_semantics` with
`kind:"semantic_frame_ceiling"`, `native_frame_rate:25` and
`can_end_early:true`. Undeclared semantics for other models appear as null;
do not transfer Music3's behavior to another model.

The saved `params.duration_seconds` remains the requested setting. To measure an
authorized output, use the existing metadata operation with `probe=true`:

```bash
curl --fail-with-body --get --max-time 75 \
  -b "$MAESTRO_COOKIE_FILE" \
  --data-urlencode "workspace=$MAESTRO_PROJECT" \
  --data-urlencode 'probe=true' \
  "$MAESTRO_URL/api/v1/outputs/$MAESTRO_OUTPUT/metadata"
```

`MAESTRO_OUTPUT` is the returned safe output filename. For general filenames,
URL-encode the path segment. The private cookie file is created by the human
login flow; never put cookie contents in a prompt, repository or tool argument.

Python:

```python
import os
from urllib.parse import quote
from maestro_continuum import MaestroClient

with MaestroClient(os.environ["MAESTRO_URL"],
                   cookie_file=os.environ["MAESTRO_COOKIE_FILE"], timeout=75) as maestro:
    info = maestro.request(
        "GET", "/api/v1/outputs/" + quote(os.environ["MAESTRO_OUTPUT"], safe="") + "/metadata",
        query={"workspace": os.environ["MAESTRO_PROJECT"], "probe": True},
    )
    print("requested", info.get("params", {}).get("duration_seconds"))
    print("measured", info["media_info"]["duration"])
```

TypeScript/Node, from the repository root after building the client:

```javascript
import { createNodeClient } from './sdk/typescript/dist/node.js'
const maestro = await createNodeClient(process.env.MAESTRO_URL, {
  cookieFile: process.env.MAESTRO_COOKIE_FILE,
  timeoutMs: 75_000,
})
const info = await maestro.request('GET',
  `/api/v1/outputs/${encodeURIComponent(process.env.MAESTRO_OUTPUT)}/metadata`, {
    query: { workspace: process.env.MAESTRO_PROJECT, probe: true },
  })
console.log({ requested: info.params?.duration_seconds, measured: info.media_info.duration })
```

The existing MCP output-metadata tool exposes the same optional `probe` query
field through its live contract. Inspect its current tool schema; use the
returned output name and authorized workspace. No credentials or host paths are
tool inputs. Probing uses the existing CPU media inspector, loads no model and
submits no generation. It can take up to the existing 60-second inspector timeout.
Without `probe`, metadata reads keep their prior behavior. A `409` means the
source changed: refresh Gallery. A `422` means inspection failed: read metadata
without probing or check the local media tools. Neither error cancels a job.

`media_info` reports actual duration, size, audio channels/sample rate and any
video geometry/frame rate. Access and content revision are checked again after
inspection. Original files, producer seals and requested settings remain intact.

## Director and interruption recovery

Director accepts `minimax_music3` in its normal preparation/generate-music flow.
Keep its `director_request_id` separate from generation jobs and pipeline IDs.
Feed the returned authorized audio identifier into `/api/v1/audio/analyze` and
then use its **measured** `analysis.duration` for structure planning. Omit
`total_duration` unless intentionally selecting a different planning span; do
not substitute the requested Music3 ceiling. Full analysis can load models and
requires its own resource readiness and authority; the CPU metadata probe is
not beat, lyric, section or speaker analysis. Generated music still needs review
before approval and video execution.

Cancellation is terminal. An original supervised job held after a runtime
interruption can instead report `recovery_requires_gpu_reservation:true` and
`recovery_reason:"gpu_reservation_required"`. Renew that exact job's authority
through its owning path, then release the original held job once. Do not submit
its generation request again. Observe the same UUID/job through completion and
Gallery; a client timeout never means cancellation. Other failed/blocked states
retain their own advertised recovery actions and prerequisites.

Native Studio instrumental and lyrical completion, cancellation, and a real
interruption/fresh-authority same-job completion have been observed. In one
retained recovery output an 18-second request produced 13.607 seconds of audio.
This establishes neither strict-duration generation nor musical/ending quality.
Director's complete generated-soundtrack-to-video journey and human acceptance
remain separate qualifications.
