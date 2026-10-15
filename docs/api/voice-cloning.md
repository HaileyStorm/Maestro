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

## Saved inputs and prepared voice memory

Project voice cards save portable recordings, transcripts and provenance. Loading
a card prepares a session upload; it does not load trained speaker weights or a
persisted native tensor cache. The same source card can be used by different
models through their respective inputs. Native prepared representations are
model-specific and are not interchangeable.

| Model | Current retention and reuse | Lower-level possibility, not a saved-voice API |
| --- | --- | --- |
| Drama 3 | By default, reference codes are rebuilt for each generation job. The opt-in `custom_settings.drama3_reference_cache: 1` implementation retains up to six exact-reference code tensors, at most 8 MiB total, in CPU RAM while the pipeline remains loaded; release clears them. It defaults to off. Retained native WAV comparisons establish execution and byte-identical audio for the measured cache case; they do not establish subjective quality for every voice. | Encoded reference codes can condition further speech. No disk save/load of prepared voice tensors is exposed. The separate FP8 expert cache holds model weights, not a prepared voice. |
| VoxCPM2 | The loaded model is retained, but each high-level generation call rebuilds reference features. Combined mode encodes the same recording for its two roles, with different padding; the wrapper retains no prepared voice cache across turns or jobs. | Native `build_prompt_cache` returns CPU-RAM latent features plus transcript and mode; `generate_with_prompt_cache` can reuse them. Maestro has not integrated that reuse or persisted those tensors. Conditioning is transferred to the model device during generation. |
| Qwen3 TTS Base | Reference speech codes and speaker embeddings are prepared for the request. Split speech and dialogue paths reuse request-local prompt items; a new job rebuilds them. No project-card restore of prepared tensors is exposed. | Native `create_voice_clone_prompt` items can be supplied to `generate_voice_clone`. That makes reuse possible, but does not establish cross-job RAM/VRAM retention or a portable tensor format. CustomVoice uses preset voices and is a separate architecture. |
| KugelAudio | The wrapper reloads/processes the reference into each generation's inputs. It does not retain a prepared voice cache for later jobs. | Lower-level `encode_voice_prompt` and `voice_cache` support prepared features; the wrapper does not use that path as a saved-voice facility. |
| Chatterbox | The loaded model retains its latest conditioning on the model device, but the wrapper prepares the supplied reference again on each request. A saved card does not restore that conditioning directly; release drops the loaded model. | Native generation can reuse current conditioning without another audio path. A save helper exists, but the installed multilingual loader does not honor the requested file and loads bundled conditioning instead. Those helpers do not establish working prepared-voice save/load. |

There is no general public API here for saving prepared voice tensors to disk,
loading arbitrary tensor files, or pinning a named voice in RAM or VRAM. Do not
deserialize arbitrary Torch/pickle files as voice profiles. Use the authorized
project recording and transcript; any future prepared-cache persistence needs
explicit model/codec identity, bounded memory and a supported private format.
These are source-level retention descriptions, not memory measurements or native
quality acceptance. A model's `native_qualified` status remains separate.

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
