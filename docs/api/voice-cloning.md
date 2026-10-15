# Saved voices and reference-conditioned speech

A saved voice is a project Reference card with `asset_type: "voice"`. Each
kept `voice_reference` variant contains one recording and a paired transcript
in `metadata.transcript`. Several variants can represent different recordings
of the same speaker. These are reusable inputs for zero-shot cloning; saving
them does not train weights or establish that every model has reproduced the
speaker successfully. Retain generated takes separately from source recordings.

Discover available cards with
`GET /api/v1/projects/{project}/assets?asset_type=voice`. Use an actual card ID
and one kept variant ID from that response. Project access and source-media
privacy apply to both listing and media reads. Record the source license,
attribution, paired transcript and content hash in the variant metadata.

The clients provide a small composition of existing HTTP operations: list the
authorized voice cards, read the selected protected WAV/FLAC, verify its
recorded `metadata.wav_sha256` when present, and upload those exact bytes as a
new private session input. The recording is limited to 16 MiB. This operation
does not generate speech, accept model terms, acquire GPU authority, or expose
host paths or credentials.

```python
import os
from maestro_continuum import MaestroClient

with MaestroClient(os.environ["MAESTRO_URL"],
                   cookie_file=os.environ["MAESTRO_COOKIE_FILE"]) as maestro:
    project = os.environ["MAESTRO_PROJECT"]
    cards = maestro.request("GET", f"/api/v1/projects/{project}/assets",
                            query={"asset_type": "voice"})["assets"]
    for card in cards:
        print(card["id"], card["name"], [(v["id"], v["label"]) for v in card["variants"]])
    reference = maestro.prepare_voice_reference(
        project, os.environ["MAESTRO_VOICE_ASSET"], os.environ["MAESTRO_VOICE_VARIANT"])
    print(reference["upload"]["filename"], reference["transcript"])
```

TypeScript (Node uses the human-created cookie jar; browser clients use their
existing same-origin session):

```typescript
import { createNodeClient } from './sdk/typescript/dist/node.js'
const maestro = await createNodeClient(process.env.MAESTRO_URL!, {
  cookieFile: process.env.MAESTRO_COOKIE_FILE!,
})
const reference = await maestro.prepareVoiceReference(
  process.env.MAESTRO_PROJECT!, process.env.MAESTRO_VOICE_ASSET!,
  process.env.MAESTRO_VOICE_VARIANT!,
)
console.log(reference.upload.filename, reference.transcript)
```

The MCP bridge exposes `maestro_voice_reference_prepare` with `project`,
`asset_id` and `variant_id`. It returns structured `source`, `upload`,
`transcript`, `bytes` and `generation_submitted: false`. It is a mutation,
non-destructive and **not idempotent**. It is unavailable in read-only mode;
include the `references` feature when selecting a restricted tool set.
See the [API entrypoint](README.md) for authenticated MCP setup.

Use `upload.filename` as `audio_guide` and `audio_prompt_type: "A"` in a
fresh generation request. The upload belongs to the preparing session; another
authorized client should prepare its own upload from the project card. Supply
a fresh `generation_request_id` and reconcile an uncertain generation using
that same UUID. A timeout never cancels a job. Reference preparation itself has
no upload idempotency key: after an uncertain upload, preserve its result and
request owner resolution rather than blindly repeating it.

Model-specific inputs remain owned by discovery and the execution handler:

| Model | Reference transcript | Voice direction |
| --- | --- | --- |
| Drama 3 | `custom_settings.drama3_reference_text` | Speaker tags and `alt_prompt`; experimental, inspect actual output |
| Qwen3 TTS Base | `alt_prompt` | Reference-conditioned speech; CustomVoice is a separate preset-voice architecture |
| VoxCPM2 | Optional `custom_settings.voxcpm_reference_text` enables combined reference-and-continuation cloning with the same audio clip; exact transcript, at most 16000 UTF-8 bytes | Reference-only mode supports speaker/emotion tags and `alt_prompt`. Combined mode requires empty separate directions; delivery comes from the recording. |
| KugelAudio | Audio reference; no separate transcript input in the shipped pipeline | No separate character-description input; `num_inference_steps` controls native diffusion steps, 1–100 (default 20; legacy 0 uses 20) |
| Chatterbox | Audio reference; no separate transcript input | `custom_settings.exaggeration` and `pace` controls; `num_inference_steps` controls acoustic flow-matching steps, 1–100 (default 10; legacy 0 uses 10). `alt_prompt` is unused. |

Decoder step ranges bound request cost. More steps do not guarantee better
quality. Compare actual outputs when choosing a setting; a higher step count
does not establish that a voice or delivery is better.

Use the same exact recording and paired transcript provenance when comparing
models. Explain models that cannot consume a transcript or arbitrary direction;
do not pass unsupported prose and claim that the model used it. Model
availability, installed assets, applicable terms, native readiness and output
quality are separate facts. A completed download or saved card does not prove
a completed clone take.

## VoxCPM2 audio-plus-transcript cloning

Set `custom_settings.voxcpm_reference_text` to the exact words in the selected
recording. A nonempty transcript requires an existing `audio_guide` reference.
The adapter supplies that same recording as both upstream `reference_wav_path`
and `prompt_wav_path`, with the unchanged transcript as `prompt_text`. The
transcript must be a string containing at most 16000 UTF-8 bytes; whitespace-only
text is invalid. Leaving it empty preserves reference-only cloning or text-only
speech, according to whether reference audio is supplied.

Within the same open `MaestroClient` session after preparing `reference`, build
the body below. Select the actual VoxCPM2 model ID from discovery, then use the
held-job submission and uncertain-submission workflow in the next section.

```python
import uuid

model = os.environ["MAESTRO_VOXCPM_MODEL"]
defaults = maestro.request("GET", f"/api/v1/defaults/{model}")
body = {
    **defaults,
    "generation_request_id": str(uuid.uuid4()),
    "workspace": project,
    "model_type": model,
    "generation_mode": "audio",
    "audio_prompt_type": "A",
    "audio_guide": reference["upload"]["filename"],
    "custom_settings": {
        **(defaults.get("custom_settings") or {}),
        "voxcpm_reference_text": reference["transcript"],
    },
    "alt_prompt": "",
    "model_mode": "",
    "prompt": "Stay close. I know a safe path through these woods.",
    "repeat_generation": 1,
    "private_output": True,
    "enhance_before_generate": False,
    "_queue_mode": "held",
}
```

Combined cloning uses the recording's delivery. It requires empty `alt_prompt`
and `model_mode`, and no speaker emotion tags. In retained native comparisons,
combined mode spoke the parenthetical direction aloud and some takes lost
dialogue. Maestro therefore rejects this unsupported input combination before
loading the model. Clear the transcript to use directed reference-only cloning;
use a reference with the desired delivery for transcript-conditioned cloning.
Neither successful execution nor a higher step count establishes voice quality;
comparative listening is still needed.

## Saved inputs and prepared voice features

Project voice cards remain portable recordings, transcripts and provenance.
`prepare_voice_reference` / `prepareVoiceReference` copies the selected recording
into a private session upload. Prepared features are a separate, model-specific
cache used automatically during ordinary authorized generation. They are not
trained speaker weights and cannot be exchanged between model families.

| Model family | Prepared features reused |
| --- | --- |
| `drama3` | Reference codec codes; enabled by default with `custom_settings.drama3_reference_cache: 1`. Explicit `0` opts out for that generation. The FP8 expert weight cache is separate. |
| `voxcpm2` | Native prompt-cache features, including the applicable reference/continuation mode and transcript. |
| `qwen3_tts_base` | Voice-clone prompt codes and speaker embeddings. CustomVoice preset voices are a separate architecture. |
| `kugelaudio_0_open` | Encoded voice-prompt features. |
| `chatterbox` | Native voice-conditioning tensors. |

VoxCPM2, Qwen3 TTS Base, KugelAudio and Chatterbox use this cache automatically;
no separate cache setting is required. The first cache miss uses the model's
native reference preparation during generation, then saves reusable features.
Model/codec files, preparation implementation, source recording bytes, reference
roles, transcript and preparation settings bind each entry. Changed or corrupt
entries are cache misses: ordinary generation falls back to native preparation.
The private format stores data-only tensors with their exact dtype and bounded
JSON metadata; it does not deserialize arbitrary Torch/pickle voice profiles.

Disk entries live under `prepared_voices` in the configured checkpoint root.
The shared cache retains at most 128 entries / 256 MiB in CPU RAM, at most
64 MiB per entry, and at most 1024 entries / 4 GiB on disk. Limits can evict
reusable entries. The owner's **Free Resources** action clears resident prepared
features, including explicit preloads; disk entries persist for later reuse.
Cached CPU features do not imply that model weights are loaded, GPU memory is
ready, generation is admitted, or voice quality has been accepted.

### Inspect, load and evict

All three operations select a project-authorized voice asset and one kept
`voice_reference` variant containing exactly one WAV or FLAC recording. The
optional `family` query accepts the five family IDs above; omission includes
all matching families. No operation accepts a request body or arbitrary path,
exports tensor payloads or credentials, loads a model, dispatches GPU work, or
submits generation. Existing session, project, source-media privacy and mutation
origin requirements apply.

Let `P` be
`/api/v1/projects/{project}/assets/{asset_id}/variants/{variant_id}/prepared-voices`,
with each path field URL-encoded separately.

| HTTP operation | Operation / MCP tool ID | Permission and effect |
| --- | --- | --- |
| `GET P` | `prepared_voice_status` | `project.read`; read-only inspection without preloading. |
| `POST P/load` | `prepared_voice_load` | `project.mutate`; idempotently loads valid existing disk features into bounded CPU RAM. It does not create missing features. |
| `DELETE P` | `prepared_voice_evict` | `project.mutate`; idempotently removes matching disk and CPU cache entries. Original audio, cards and models remain. Content-identical references can share the removed features. |

Responses contain `asset_id`, `variant_id`, `entries`, integer counts `matched`,
`valid`, `memory_ready`, `evicted`, `failures`, and
`readiness_scope: "cpu_features"`, `generation_submitted: false`, `next_action`.
Each entry contains an opaque SHA-256 `key`, `family`, `bytes`, and booleans
`valid`, `disk_ready`, `memory_ready`. Follow `next_action`; a missing entry
requires ordinary authorized generation with that reference before CPU preload
can help. After an uncertain mutation, inspect current state; the clients never
automatically retry.

Within an authenticated Python client session:

```python
state = maestro.inspect_prepared_voice(project, asset_id, variant_id, family="voxcpm2")
state = maestro.load_prepared_voice(project, asset_id, variant_id, family="voxcpm2")
print(state["readiness_scope"], state["next_action"])
# Explicit cache removal, when wanted:
state = maestro.evict_prepared_voice(project, asset_id, variant_id, family="voxcpm2")
```

TypeScript uses the same authenticated client and server contract:

```typescript
const state = await maestro.inspectPreparedVoice(project, assetId, variantId, 'voxcpm2')
await maestro.loadPreparedVoice(project, assetId, variantId, 'voxcpm2')
// Explicit cache removal, when wanted:
await maestro.evictPreparedVoice(project, assetId, variantId, 'voxcpm2')
```

For curl, set `MAESTRO_PREPARED_VOICE_URL` to the current configured Maestro
origin plus `P`, substituting the URL-encoded authorized IDs. Run only the
operation wanted:

```bash
curl --cookie "$MAESTRO_COOKIE_FILE" "$MAESTRO_PREPARED_VOICE_URL?family=voxcpm2"
curl --cookie "$MAESTRO_COOKIE_FILE" -H "Origin: $MAESTRO_ORIGIN" \
  -X POST "$MAESTRO_PREPARED_VOICE_URL/load?family=voxcpm2"
curl --cookie "$MAESTRO_COOKIE_FILE" -H "Origin: $MAESTRO_ORIGIN" \
  -X DELETE "$MAESTRO_PREPARED_VOICE_URL?family=voxcpm2"
```

The MCP bridge generates these tools from the current authored OpenAPI contract.
Select `references.voices.prepared` (or its parent `references`) to include them.
Read-only mode includes inspection only. Load is non-destructive; eviction is
destructive only to cached features; both are idempotent and local in scope.
These interfaces grant no extra project, credential or native execution authority.

The 15 October 2026 native comparison exercised preparation, RAM reuse,
speaker changes and fresh-process disk reuse for all five families. Drama,
VoxCPM2, Qwen3 Base and KugelAudio produced byte-identical matching-reference
WAVs through disk reload. Chatterbox matched within one process; its disk-loaded
conditioning matched fresh native preparation exactly in all ten fields.
Chatterbox initializes an unregistered decoder noise bank when constructing the
model, before the per-generation seed, so a fresh model instance can produce a
different waveform with identical saved conditioning. Caching does not change
that native behavior.

The deployed authenticated Python and MCP workflow exercised inspection and
preloading; Python also exercised eviction and repeated eviction without
deleting recordings or submitting generation. CPU readiness, model
`native_qualified` status, measured cache reuse and subjective listening
acceptance remain separate evidence. Windows disk persistence is unqualified;
unsupported private-file operations fall back to native preparation.

## Submit speech from a saved voice

This example uses **Qwen3 TTS Base**, whose `alt_prompt` is the reference
transcript. Select the actual Base model ID from this server's discovery;
CustomVoice is a different architecture and cannot replace it in this recipe.
Use one kept source `voice_reference` variant, rather than a generated
`voice_clone_take`, when comparing models against the original speaker.

The reference upload and generation must use the same authorized client
session. This submits one private **held** job. It does not release a queue,
accept terms, or obtain GPU permission. Model readiness, terms and execution
prerequisites still apply when the job is released.

```python
import json, os, uuid
from maestro_continuum import MaestroClient, UncertainSubmission

project = os.environ["MAESTRO_PROJECT"]
model = os.environ["MAESTRO_QWEN_BASE_MODEL"]
with MaestroClient(os.environ["MAESTRO_URL"],
                   cookie_file=os.environ["MAESTRO_COOKIE_FILE"]) as maestro:
    reference = maestro.prepare_voice_reference(
        project, os.environ["MAESTRO_VOICE_ASSET"],
        os.environ["MAESTRO_VOICE_VARIANT"])
    request_id = str(uuid.uuid4())
    defaults = maestro.request("GET", f"/api/v1/defaults/{model}")
    body = {**defaults, "generation_request_id": request_id,
            "workspace": project, "model_type": model,
            "generation_mode": "audio", "model_mode": "english",
            "audio_prompt_type": "A",
            "audio_guide": reference["upload"]["filename"],
            "alt_prompt": reference["transcript"],
            "prompt": "Stay close. I know a safe path through these woods.",
            "repeat_generation": 1, "private_output": True,
            "enhance_before_generate": False, "_queue_mode": "held"}
    # Save the exact UUID and body before a potentially uncertain mutation.
    with os.fdopen(os.open(f"voice-{request_id}.json",
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(body, f)
    try:
        admission = maestro.request("POST", "/api/v1/generate", body=body)
    except UncertainSubmission:
        admission = maestro.request(
            "GET", f"/api/v1/generate/submissions/{request_id}",
            query={"workspace": project})
    print(admission)
```

The TypeScript client uses the same public operations:

```typescript
import { randomUUID } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { createNodeClient } from './sdk/typescript/dist/node.js'

const maestro = await createNodeClient(process.env.MAESTRO_URL!, {
  cookieFile: process.env.MAESTRO_COOKIE_FILE!,
})
const project = process.env.MAESTRO_PROJECT!
const model = process.env.MAESTRO_QWEN_BASE_MODEL!
const reference = await maestro.prepareVoiceReference(
  project, process.env.MAESTRO_VOICE_ASSET!, process.env.MAESTRO_VOICE_VARIANT!,
)
const requestId = randomUUID()
const defaults = await maestro.request('GET', `/api/v1/defaults/${model}`) as Record<string, unknown>
const body = { ...defaults, generation_request_id: requestId,
  workspace: project, model_type: model, generation_mode: 'audio',
  model_mode: 'english', audio_prompt_type: 'A',
  audio_guide: reference.upload.filename, alt_prompt: reference.transcript,
  prompt: 'Stay close. I know a safe path through these woods.',
  repeat_generation: 1, private_output: true,
  enhance_before_generate: false, _queue_mode: 'held' }
await writeFile(`voice-${requestId}.json`, JSON.stringify(body), { flag: 'wx', mode: 0o600 })
// If this call is uncertain, reconcile this UUID; do not submit a new one.
console.log(await maestro.request('POST', '/api/v1/generate', { body }))
```

After saving a prepared request, curl can submit or reconcile it using the same
human-created cookie jar and configured origin. Do not add automatic retries:

```bash
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" -c "$MAESTRO_COOKIE_FILE" \
  -H "Origin: $MAESTRO_ORIGIN" -H 'Content-Type: application/json' \
  --data-binary @"voice-$REQUEST_ID.json" "$MAESTRO_URL/api/v1/generate"
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" \
  "$MAESTRO_URL/api/v1/generate/submissions/$REQUEST_ID?workspace=$MAESTRO_PROJECT"
```

For MCP, call `maestro_voice_reference_prepare`, then select the Studio
generation operation from the bridge's discovered tools and supply the same
fields. A read-only bridge can inspect authorized cards and jobs but cannot
prepare a new upload or submit speech.

Keep the returned `job_id` and original `generation_request_id`. Inspect
`GET /api/v1/status/{job_id}` and Queue for held prerequisites, progress and
outputs. Release only that job through its documented authorized queue action
when its prerequisites are satisfied; never release all held jobs to run one
voice take. A supervised GPU-reservation prerequisite requires the operator's
renewed exact authority and cannot be bypassed by an API client. See the
[generation and recovery workflow](README.md#generation-held-queues-and-uncertain-submission)
for release, cancellation, reconnection and output access.

To compare another cloning model, prepare the **same source variant** in its
client session and use that model's documented transcript and direction inputs
from the table above. Do not reuse another session's private upload, pass an
unused direction field and claim it was consumed, or present separate
reference-conditioned takes as trained voice weights. Retain the exact model
settings and source recording hash with each result.

## Reference quality, length and accent

A short clip establishing that cloning works is not a demonstrated quality
optimum. Qwen advertises three-second cloning; Fish's general cloning guidance
suggests at least ten seconds, and two or three consistent 15–20 second clips.
Those vendor descriptions are starting points, not comparative measurements of
Maestro's installed runtimes. Longer references can add useful evidence of
vowels, consonants, rhythm and accent, but extra duration does not guarantee an
improvement. Silence, echo, music, distortion or a different delivery can add
unhelpful conditioning.

For a quality comparison, keep the speaker, microphone/room, target text,
reference transcript and generation settings fixed. Compare a clean short
reference against a longer clean excerpt with varied natural speech. Prefer
phonetic and sentence variety within one consistent voice/delivery; keep markedly
different emotions or character voices as separate references. Repeating the
same sentence adds less phonetic variety. Preserve exact transcripts for modes
that use them, including Qwen's full reference conditioning and Drama. Qwen's
x-vector-only mode is a different quality tradeoff, not an equivalent substitute.
No length/phonetic-variety optimum has been qualified here.

VoxCPM2 combined audio-plus-transcript continuation cloning is available through
`custom_settings.voxcpm_reference_text` as described above. Its quality still
needs a separate comparison against reference-only cloning with the same source
recording and settings; the presence of the option does not prove better output.

Corpus accent/region labels describe source speakers. Record a listener's
perceived generated accent and accent retention separately from speaker identity,
word accuracy, emotion control and audio naturalness. A changed accent does not
by itself mean every other aspect of the cloned identity failed.

Sources: [Qwen Base model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base),
[Fish cloning guidance](https://docs.fish.audio/developer-guide/best-practices/voice-cloning),
and [VoxCPM2 conditioning modes](https://huggingface.co/openbmb/VoxCPM2).
