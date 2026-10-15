# Drama 3 directed speech

Drama 3 uses Continuum's existing `POST /api/v1/generate`, project authorization,
Queue, cancellation and Gallery outputs. Python, TypeScript and MCP use those
same operations. This is experimental local inference, built with Fish Audio.
Discover `GET /api/v1/models`, `/api/v1/model-options/drama3` and
`/api/v1/defaults/drama3` before submission. `speech_capabilities` distinguishes
installed weights and optional runtime dependencies from native qualification.
An installed model is not a GPU grant or a quality verdict.

Native observations on 2026-10-14 established production API generation with
the official FP8 weights, routed CPU offload and CPU codec on the local Linux
NVIDIA host. One retained directed take speaks both requested sentences. Two
later takes omit the second sentence and contain a long quiet tail, despite
ending below their frame cap. Drama remains experimental and is excluded from
automatic routing; inspect every take's words and ending before using it.
One later matched-seed take with explicit turns generated separately speaks
both sentences, as checked by CPU transcription. This establishes one mitigation
result, not the cause of omissions or a completeness guarantee. The owner also
reported cavernous acoustics in the first word-complete take; its performance
quality is not accepted.
The delivered reference-conditioned comparison now contains both authored dialogue
turns. The speech-chunk builder adds global direction after splitting the speech,
and codec decoding preserves the generated codes used for continuation history.
A later clean-reference diagnostic also speaks the previously missing sentence.
New HTTP requests use the current settings version when it is omitted, avoiding
legacy vocal extraction on clean recordings; explicit saved old versions retain
their migration behavior. These results support the delivered takes, not a
guarantee for new text or voices.
These observations do not qualify every reference-voice or multi-speaker input,
another platform, or subjective performance quality.

## Installation and terms

Review the [Fish Audio Community License](https://huggingface.co/fishaudio/drama-3/blob/e0a9218aa48888270f06ef1ed9ea67622c0e17d2/LICENSE)
and accept the displayed Drama 3 terms through Maestro's normal model-terms
flow. Acceptance for Music3 does not cover Drama 3. AI tools cannot accept terms
on the owner's behalf. After acceptance, use the normal model download operation;
assets go to the host's configured model storage, including fast-storage where
selected. The pinned model revision is
`e0a9218aa48888270f06ef1ed9ea67622c0e17d2`, approximately 41.5 GB including codec.

In Pinokio's app terminal, with the selected Maestro environment active:

```bash
cd app
python scripts/install_drama3_runtime.py
```

The optional installer retains the installed Torch, CUDA and tokenizer bindings.
It installs the codec dependencies; it loads no model. The pinned Fish native
runtime ships with Maestro, with its license and source manifest.
Normal **Install** and **Update** run this step on Linux with NVIDIA. Windows and
other platforms are not qualified for this native Triton/offload path.

Inference requires NVIDIA CUDA, an SM89 or newer device and Triton. The official
block FP8 expert weights stay FP8; non-expert tensors use BF16. CPU offload stages
only routed experts, and the FP32 codec defaults to CPU. Optional guarded GPU decoding is described
below. CUDA graphs are disabled.
`drama3_gpu_budget_gib` defaults to 22, accepts 8–26 GiB, and covers resident
weights plus worst-case expert staging. The optional expert cache below shares a
26 GiB total allowance with that budget. Activations, KV caches, the CUDA context
and kernel workspace still need additional headroom. A smaller budget may be rejected if fixed tensors cannot fit.
There is no supported Q6/GGUF path. CPU offload does not imply CPU-only inference.
Other GPU devices and platforms require their own native qualification.

## Warm synthesis and optional caches

Qwen3 Base can use the existing CUDA-graph LM engine when its main model is fully
resident: submit `override_profile: 3` (or another supported full-residency
profile). The host LM decoder setting must be Auto or `cg`; a host explicitly
set to Legacy keeps that selection. Offloaded profiles 2, 4 and 5 fall back to
Legacy. Select the BF16 model explicitly; a memory profile alone does not select
weight precision. These are existing execution controls, available to HTTP,
Python, TypeScript and MCP callers through the ordinary generation payload.

Drama exposes these optional `custom_settings`:

| Setting | Values | Behavior |
| --- | --- | --- |
| `drama3_reference_cache` | `0` or `1`, default `1` | Saves exact encoded reference audio in private data-only disk storage and reuses bounded CPU memory. Content hashes, codec and preparation code bind entries. Explicit `0` bypasses reuse. See [prepared voices](voice-cloning.md#inspect-load-and-evict) for project-scoped inspection, CPU preload and eviction. |
| `drama3_expert_cache_gib` | `0` through `4` | Keeps recently used original FP8 experts in GPU memory, with bounded eviction and original FP8 arithmetic. May add one expert layer of pinned CPU staging. |
| `drama3_restricted_speech_head` | `0` or `1`, default `0` | Experimental compact projection using the original 4097 EOS/semantic BF16 rows. Retains all ten audio codebooks and sampler settings; smaller matrix geometry can change rounding. |
| `drama3_bulk_expert_pack` | `0` or `1`, default `0` | Experimental batched byte copies for cached original FP8 experts. Requires a nonzero expert cache to have an effect. |
| `drama3_compile_recurrence` | `0` or `1`, default `0` | Experimental per-model compilation of single-token FP32 recurrence. Preserves prefill, sampler and weight precision. First use compiles; fused arithmetic can change speech delivery. CUDA graphs stay disabled. |
| `drama3_gpu_codec` | `0` or `1`, default `0` | Experimental FP32 GPU waveform decoding. Restores the codec to CPU after each chunk. Chunks above 256 code frames or insufficient free VRAM use the original CPU decoder. |
| `drama3_gpu_codec_bf16` | `0` or `1`, default `0` | Requires `drama3_gpu_codec: 1`. Uses a private BF16 decoder copy; the reference encoder remains original CPU FP32. Changes waveform arithmetic and needs listening comparison. |

`drama3_gpu_budget_gib + drama3_expert_cache_gib` must be at most 26.
For a qualified 32 GiB host, the measured cache candidate uses:

```json
{"custom_settings": {
  "drama3_gpu_budget_gib": 22,
  "drama3_expert_cache_gib": 4,
  "drama3_reference_cache": 1
}}
```

Merge these fields with the required reference transcript and other existing
settings. Discover current model options before submission. The configured
26 GiB allowance is not total peak VRAM; leave room for the desktop, CUDA,
activations and KV state. Smaller cards or longer contexts need their own
resource assessment. The GPU expert cache ends with model unloading; prepared
reference features persist on disk and can be reloaded into CPU memory. Source
generation remains serialized.

A short-passage native pipeline study on an RTX 5090 (32 GiB), Torch 2.10 /
CUDA 13, measured synchronized warm `generate()` wall time divided by actual
output duration. Loading, a different-text warmup, queue/HTTP time and file
writing were excluded. Reference preparation and CPU waveform decoding were
included. Each row is one controlled take, not an aggregate benchmark:

| Variant | Wall seconds / audio second | Peak process VRAM, GiB |
| --- | ---: | ---: |
| Qwen3 1.7B Base BF16, offloaded Profile 2 / Legacy | 3.389 | 2.70 |
| Qwen3 1.7B Base BF16, resident Profile 3 / Legacy | 1.064 | 4.49 |
| Qwen3 1.7B Base BF16, resident Profile 3 / CUDA graph | 0.366 | 4.52 |
| VoxCPM2 BF16, compiled, 30 steps | 0.287 | 7.50 |
| VoxCPM2 BF16, compiled, 100 steps | 0.624 | 7.42 |
| Drama original FP8, 22 GiB static budget | 5.068 | 22.50 |
| Drama original FP8, 26 GiB static budget | 4.431 | 26.20 |
| Drama original FP8, 22 GiB static + 4 GiB expert cache + reference cache | 2.730 | 26.49 |

Qwen's two Legacy WAVs were byte-identical. Its graph output differed and is a
listening comparison. All three Drama WAVs were byte-identical for this passage.
A prior Vox reference-cache experiment produced the same WAV but less than 1%
end-to-end timing difference. Persistent prepared voices now reuse that native
preparation through the shared disk/CPU store; this is principally useful for
swapping and reloading references rather than accelerating its speech decoder.
No quantization was reduced. Neither sample identity nor speed proves quality
for other voices, text or contexts; human listening remains separate.

These timings used shipped model pipelines and standard MMGP profiles without
additional MMGP weight quantization. The HTTP scheduler and main WGP profile-budget adapter
were outside the timing harness. Process VRAM was sampled through NVML during
warm synthesis, including the CUDA context; cold loading/compilation may peak
higher. They are hardware- and passage-specific measurements, not latency or
memory guarantees. See the upstream [Qwen graph engine research](https://github.com/andimarafioti/faster-qwen3-tts),
[Vox runtime](https://github.com/OpenBMB/VoxCPM), and
[Drama expert-offload implementation](https://github.com/PedroMarinhoDev/ComfyUI-Drama3)
for alternative engines and their separate dependency/hardware constraints.

A later matched short-passage comparison retained the same WAV bytes for the
original implementation, compact speech projection, batched expert packing and
both together. On that one RTX 5090 run, warm wall seconds per audio second were
2.967, 2.916, 2.818 and 2.826 respectively; peak process VRAM was 26.46, 26.27,
26.46 and 26.27 GiB. These opt-in controls remain experimental and default to
zero. Their small differences are not an aggregate benchmark or evidence that
combining options always helps. Existing API, Python, TypeScript and MCP
submission paths pass them through `custom_settings`; rediscover model options
after updating Maestro.

The recurrent compilation option binds only to that loaded Drama instance;
it does not patch a global function or another model. Changing it reloads the
model. Compilation happens on first recurrent decoding, so cold compilation is
outside the warm timing claim. A process-local prototype measured 2.429 wall
seconds per generated second for a different 8.499-second delivery of the same
text, versus 2.826 for the uncompiled 5.016-second take. CPU ASR found the full
requested words in both. The changed duration and delivery prevent treating the
RTF ratio as an isolated kernel or universal throughput speedup. Retained WAVs
provide the listening comparison. This is an optional experiment, not a new
quality default.

A separate W6A8 expert-kernel probe ran on the RTX 5090 using comfy-kitchen
0.2.37. Its six-bit weights and eight-bit activations changed numerical output;
one resident expert layer was slower than original FP8, including when both
were captured in CUDA graphs. This establishes kernel feasibility only. Maestro
does not currently offer a full Q6 model loader, a Q6 quality verdict or a Q6
speed claim. The retained hybrid WAV uses W6 for only one of forty routed layers on one-row
calls; other layers and larger prefill calls remain original FP8. It does not
qualify a fully quantized model. The first GPU-codec experiment exposed about 4.69 GiB of loaded codec tensors,
including three unused 1 GiB superclass causal masks. Windowed attention already
supplies its own masks. Maestro now allocates the superclass fallback mask only
when it is actually needed, bounded by the original 32768-position limit. This
preserves windowed attention and checkpoint weights while removing those unused
buffers. The initial lower-residency GPU timing predates this fix.

GPU decoding keeps a 3 GiB free-memory reserve in addition to actual codec
tensors, and releases only unused allocator blocks before checking. A long chunk
or insufficient headroom uses original CPU FP32 decoding; it does not shorten
speech, lower precision or retry generation. A CUDA execution failure propagates
through ordinary job recovery instead of regenerating. BF16 uses a private copy
that remains in CPU memory between chunks and is released with the model;
reference encoding stays original FP32. GPU transfer and CPU restoration count
in the measured warm wall time. These options remain disabled by default.
The final production-path matched comparison used a 22 GiB static + 2 GiB
expert cache layout, compact projection and batched packing. CPU FP32 measured
2.869 wall seconds/audio second and 24.25 GiB peak process VRAM; lazy-mask GPU
FP32 measured 2.328 and 25.77 GiB; GPU BF16 measured 2.308 and 24.79 GiB. All
three generated identical codec indices and 5.016 seconds of speech. GPU FP32
and BF16 WAVs differ, so their listening comparison remains separate from timing.
BF16 saved about 0.98 GiB peak VRAM versus GPU FP32 with less than 1% additional
latency improvement in this take. Neither compiler+GPU-codec combinations nor
other cards, texts and context lengths were measured by this comparison.

Every change affecting waveform or speech has a retained WAV comparison.

## Speech and performance direction

`prompt` contains the words to speak. Native notation uses `<|speaker:0|>` through
`<|speaker:15|>` and inline cues such as `[whispering]`, `[angry]`, `[laughing]` or
`[sad]`. Maestro also translates lines beginning `Speaker 1:` through `Speaker 16:`
into the corresponding zero-based native speaker tokens. `Speaker 1 [whispering]:`
is accepted. `alt_prompt` adds one performance cue to every speaker. Cues influence
the model; they do not guarantee a particular performance.

For reference-free direction, use short free-form bracketed descriptions, for
example `[natural conversational voice, warm and relaxed]`. These follow Fish's
[documented style-prefix syntax](https://arxiv.org/html/2610.18010v1#A3).
`alt_prompt` becomes a bracketed cue after each speaker token; it is not a
separate voice identity or system prompt. No reference means the model chooses
the timbre. A seed does not establish a repeatable voice, and textual requests
for dry acoustics do not guarantee removal of room sound. To anchor a voice,
provide a reference and its actual transcript through the fields below.

Up to three optional reference voices use `audio_guide`, `audio_guide2` and
`audio_guide3`, mapped to native speakers 0, 1 and 2. Upload references through the
existing project-scoped upload operation and use its returned filename. Set
`audio_prompt_type` to `A`, `AB` or `ABC`. Each selected reference must contain
at most 60 seconds and have its **actual transcript** in the corresponding
`custom_settings.drama3_reference_text`, `drama3_reference_text2` or
`drama3_reference_text3` (at most 16000 UTF-8 bytes each). Performance direction
is separate from those transcripts. No arbitrary host path is accepted.

Long text is chunked using Fish's native splitter, with first and recent speech
history carried within the same job. There is no cross-request history handle.
Set `custom_settings.drama3_separate_turns` to `1` to generate each explicitly
marked speaker turn separately, including two consecutive `Speaker 1:` lines.
The default `0` retains Fish's native batching. Separate turns still carry native
audio history for voice continuity and share the job's duration budget. This is
an experimental mitigation for skipped turns, not a word-completeness guarantee;
inspect the words, pauses and voice continuity. Untagged paragraphs retain the
native splitter. No automatic regeneration or ASR-based retry is performed.
`duration_seconds` is an upper bound, 1–300 seconds, default 20. Reaching it can
truncate words; it does not stretch speech to that duration. An individual native
chunk also has a frame limit. Outputs are mono 44.1 kHz audio. Inspect the actual
audio duration and ending before accepting a take. LoRA training, cross-job
continuation and a streaming-audio endpoint are not supported by this integration.
Cancellation aborts the job; **Stop early** retains completed chunks and takes
effect at the next chunk boundary rather than inside the current spoken phrase.

Use Fish's recommended sampling baseline: `temperature: 0.7`, `top_p: 0.7`,
`top_k: 30`. Increasing these numbers is not a quality upgrade. A generous
duration cap, such as 60 seconds for a short passage, leaves room for a natural
ending; it does not request 60 seconds of speech. Keep the native sample rate.
Standalone output format comes from the host's `audio_stand_alone_output_codec`
setting. Select `wav` for 24-bit PCM WAV without MP3 compression. New WAVs retain
the native sample rate; existing files are not re-encoded. This is a host setting,
not a per-generation format override.

For comparisons, VoxCPM2 uses `num_inference_steps: 30`, `guidance_scale: 2`,
and `custom_settings: {"voxcpm_max_len": 2400, "voxcpm_retry_badcase": 0}`
for the default-step speed comparison. A separate 100-step WAV is retained for
quality comparison; this does not replace a user's preferred step count. VoxCPM ignores temperature,
top-p, top-k, seed and the duration slider. IndexTTS2 accepts 1–100 decoder steps
through `num_inference_steps` (default 25); its GPT sampler remains separate.
Qwen3 and Drama 3 are autoregressive models without an iterative step control.
Higher decoder steps or sampler values do not establish a universal quality
optimum. Compare the actual endings, words and performances by listening.

## Runnable Python submission

Use the [API setup and authentication guide](README.md). The owner supplies the
private cookie-file path; never put cookie contents or passwords in a prompt.

```python
import json, os, uuid
from pathlib import Path
from maestro_continuum import MaestroClient

with MaestroClient(os.environ["MAESTRO_URL"],
                   cookie_file=os.environ["MAESTRO_COOKIE_FILE"]) as maestro:
    print(maestro.request("GET", "/api/v1/model-options/drama3")["speech_capabilities"])
    body = maestro.request("GET", "/api/v1/defaults/drama3")
    # The defaults operation returns a settings object; retain its model fields.
    body.update(workspace=os.environ["MAESTRO_PROJECT"], model_type="drama3",
                generation_mode="audio",
                generation_request_id=str(uuid.uuid4()),
                prompt="Speaker 1 [whispering]: I thought you had gone.\n"
                       "Speaker 1 [relieved]: You're really here.",
                alt_prompt="", duration_seconds=60, seed=322,
                temperature=0.7, top_p=0.7, top_k=30,
                audio_prompt_type="", _queue_mode="now")
    body["custom_settings"] = {"drama3_gpu_budget_gib": 22}
    # Retain the exact request privately before sending; never regenerate its ID.
    saved = Path("drama3-submission.json")
    with saved.open("x", encoding="utf-8") as handle:
        json.dump(body, handle)
    os.chmod(saved, 0o600)
    result = maestro.request("POST", "/api/v1/generate", body=body)
    print(result)  # admission/job identifiers, not proof of completed speech
```

For a Node client, use the same private login jar and retain the request before
submission. Run this as an `.mjs` file in the repository root after building the
TypeScript SDK:

```javascript
import { writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { createNodeClient } from './sdk/typescript/dist/node.js';

const maestro = await createNodeClient(process.env.MAESTRO_URL, {
  cookieFile: process.env.MAESTRO_COOKIE_FILE,
});
const body = await maestro.request('GET', '/api/v1/defaults/drama3');
Object.assign(body, {
  workspace: process.env.MAESTRO_PROJECT, model_type: 'drama3',
  generation_mode: 'audio',
  generation_request_id: randomUUID(),
  prompt: 'Speaker 1 [whispering]: I thought you had gone.\n' +
          "Speaker 1 [relieved]: You're really here.",
  alt_prompt: '', duration_seconds: 60, seed: 322,
  temperature: 0.7, top_p: 0.7, top_k: 30,
  audio_prompt_type: '', _queue_mode: 'now',
  custom_settings: { drama3_gpu_budget_gib: 22 },
});
await writeFile('drama3-submission.json', JSON.stringify(body),
                { flag: 'wx', mode: 0o600 });
console.log(await maestro.request('POST', '/api/v1/generate', { body }));
```

Defaults may contain extra settings for compatibility; they are not guarantees of
support. The authored OpenAPI `Drama3GenerationSettings` and
`Drama3CustomSettings` describe this model's actual inputs. TypeScript's
`MaestroClient.request()` and MCP's discovered Studio generation tool accept the
same body; they do not import Fish or load models themselves. For curl, save the
same JSON body and submit:

```bash
curl --fail-with-body -b "$MAESTRO_COOKIE_FILE" -H "Origin: $MAESTRO_ORIGIN" \
  -H 'Content-Type: application/json' --data-binary @drama3-submission.json \
  "$MAESTRO_URL/api/v1/generate"
```

Read `/status/{job_id}` in the same workspace and retrieve outputs through
authorized Gallery/file routes. For held queues, release the returned existing
job only after its prerequisites are satisfied. Use the existing cancel operation
for that job. A client timeout never cancels it. Reconcile a lost admission reply
using the retained `generation_request_id` and the submission read operation;
never send a new UUID for the same uncertain job. See the API guide for the full
held/recovery and cancellation contracts. Model terms, resource admission and
fresh GPU coordination still apply equally to local, LAN and Cloudflare clients.
