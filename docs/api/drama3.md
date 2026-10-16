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
below. Full-model CUDA graphs remain disabled with routed CPU offload; an
optional codebook-tower graph is described below.
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
| `drama3_prefix_cache` | `0` or `1`, default `0` | Experimental one-entry CPU prompt-prefix cache, at most 96 MiB per loaded model. Reuses the first complete 128-row backbone/tower chunk only for an exact 129-row input and matching model/settings/runtime layout. Original continuation and sampler remain unchanged. Model unloading or changing the option clears it. Memory-only; separate from disk-backed reference codes. |
| `drama3_tower_graph` | `0` or `1`, default `0` | Experimental BF16 codebook-tower graphs. Requires prefix reuse, compact speech projection and explicit `temperature: 0.8`, `top_k: 30`, `top_p: 0.8`. First eligible call captures; warm calls reuse 256/384-row KV buckets. Positions from 384 onward and unsupported inputs use ordinary decoding. Capture, CUDA and nonfinite errors fail the call without regenerating it. Turning this option off preserves the loaded backbone, codec and prefix cache. |
| `drama3_reference_cache` | `0` or `1`, default `1` | Saves exact encoded reference audio in private data-only disk storage and reuses bounded CPU memory. Content hashes, codec and preparation code bind entries. Explicit `0` bypasses reuse. See [prepared voices](voice-cloning.md#inspect-load-and-evict) for project-scoped inspection, CPU preload and eviction. |
| `drama3_expert_cache_gib` | `0` through `4` | Keeps recently used original FP8 experts in GPU memory, with bounded eviction and original FP8 arithmetic. May add one expert layer of pinned CPU staging. |
| `drama3_restricted_speech_head` | `0` or `1`, default `0` | Experimental compact projection using the original 4097 EOS/semantic BF16 rows. Retains all ten audio codebooks and sampler settings; smaller matrix geometry can change rounding. |
| `drama3_bulk_expert_pack` | `0` or `1`, default `0` | Experimental batched byte copies for cached original FP8 experts. Requires a nonzero expert cache to have an effect. |
| `drama3_compile_recurrence` | `0` or `1`, default `0` | Experimental per-model compilation of single-token FP32 recurrence. Preserves prefill, sampler and weight precision. First use compiles; fused arithmetic can change speech delivery. Independent of the optional tower graph; full-model graphs stay disabled. |
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

For the optional tower experiment, merge the following into an otherwise valid
generation body containing its project, reference audio and exact transcript:

```python
body.update(temperature=0.8, top_k=30, top_p=0.8)
body["custom_settings"].update(
    drama3_prefix_cache=1, drama3_restricted_speech_head=1,
    drama3_tower_graph=1, drama3_bulk_expert_pack=1,
    drama3_gpu_budget_gib=22, drama3_expert_cache_gib=2,
    drama3_gpu_codec=1, drama3_gpu_codec_bf16=1,
)
```

These explicit settings do not change defaults or automatically enable missing
prerequisites. Only the current 13-layer, ten-codebook BF16 tower and compact
4097-row speech head qualify. The backbone stays eager and CPU-offloaded.
Changing native cache buffers discards their captured graphs before replacement;
the affected call uses ordinary decoding. The postcapture memory-growth limit
is 256 MiB, in addition to model and execution memory; transient peaks can be
higher. First-use capture time belongs to cold latency. All native codebooks,
sampling draws and finite checks are retained. Padded attention and graph RNG
can change numerical output, so compare the retained WAVs before choosing it.
Longer speech may spend only part of its time in the graph. Ordinary terms,
authorization and GPU coordination still apply.

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
latency improvement in this take. Other cards, texts and context lengths were
outside this comparison.

A follow-up combined the production recurrent compiler with the same 22+2 GiB
layout and GPU decoding. FP32 measured 2.471 wall seconds/audio second and
27.51 GiB peak process VRAM; BF16 measured 2.408 and 26.47 GiB. Both produced
the same codec indices and 4.319 seconds of speech; their WAVs differ. CPU ASR
found the full requested text in both. The combination did not beat the earlier
2.308 result and used more VRAM. Its changed codec indices and delivery prevent
attributing the difference solely to compilation. Separate warmups precede these
timings; cold model loading and compilation are excluded. All experimental
controls remain disabled by default, with listening comparisons retained.

Further matched native experiments retained the original sampler and reused
the accepted comparison files. Thirty resident W6A8 expert layers measured
4.875 wall seconds/audio second and 26.96 GiB observed warm VRAM; prompt
processing and the remaining ten layers still used original FP8. This partial
quantization was slower and was not adopted. CUDA graphs for the original
twenty resident FP8 expert layers measured 2.964 and 24.52 GiB, with byte-identical
codes, raw audio and WAV to the 2.869 CPU-codec baseline; they also offered no
speed benefit and were not adopted. Isolated expert-kernel gains did not
establish end-to-end speedups.

The memory-only first-chunk prefix prototype measured 2.557 wall seconds/audio
second and 24.49 GiB observed warm VRAM, about 10.9% less wall time than that
same CPU-codec baseline. It restored a 67.65625 MiB snapshot in 6.16 ms and
produced byte-identical codes, raw audio and WAV. These are single warm calls
on the same short passage, not general throughput guarantees. The original
prompt suffix, speech sampler and seed remained unchanged; expert-cache warmth
can differ when prompt work is skipped. Disk persistence of these larger
language-model states is not enabled.

The integrated prefix option combined with the guarded GPU BF16 decoder
measured 2.016 wall seconds/audio second and 26.12 GiB observed warm VRAM.
The same 5.016-second passage took 10.114 seconds: about 12.6% less wall time
than the earlier 2.308 GPU BF16 take, or 29.7% less than the CPU-codec baseline.
The production prefix hit restored 67.65625 MiB in 6.54 ms. Codec indices matched
the retained GPU BF16 take exactly; its waveform differed slightly (samplewise
correlation 0.999948), so both WAVs remain the quality A/B. An unprompted CPU ASR
pass recovered every requested word. This is not subjective quality acceptance.
Native execution and authenticated local API discovery were observed after the
coordinated deployment; local and configured Cloudflare readiness remained
healthy. The prefix option is disabled by default and remains memory-only.

A further isolated tower-only graph experiment retained the same original
FP8/BF16 weights, ten codebooks, reference, transcript, sampling settings and
GPU BF16 codec. It measured 8.666 seconds for the same 5.016-second passage:
1.728 wall seconds/audio second, 14.3% less wall time than the retained 2.016
prefix/GPU-BF16 take. Observed warm process VRAM was 26.22 GiB. Its 109 graph
replays retained all 1090 finite gates and 1199 sampling draws. Audio-code
hashes matched; decoded PCM differed slightly (correlation 0.999948), so both
WAVs are retained for listening. Capture setup took 0.143 seconds during the
different-text warmup and was excluded from the measured call. This is one
short native call, not a universal throughput or subjective-quality result.
Production option discovery and lifecycle qualification are separate evidence.

A later production-option comparison used fresh dialogue and the same loaded
model, codecs, reference and prefix cache. With `drama3_tower_graph: 1`, warm
synthesis took 16.005 seconds for 9.474 seconds of audio (1.689 wall seconds per
audio second), versus 17.318 seconds (1.828) after switching the option to zero:
7.6% less wall time on this one passage. Both PCM24 WAVs and audio codes were
identical. Sampled peak process VRAM was 26.20 and 26.62 GiB respectively; these
single-process observations do not establish a memory saving. The graph made
137 replays with 1370 finite gates and 1507 sampling draws, then continued with
68 native eager calls beyond its position limit. Switching off preserved the
backbone, reference codec, BF16 decoder and prefix-cache identities. Capture
took 0.141 seconds in the different-text warmup, outside both measured calls.
An unprompted CPU transcription recovered every requested word. The deployed
authenticated API advertised the default-off setting and accepted a held request
with it; that request was cancelled without running a model or changing existing
held jobs. Native timings used the production model pipeline, excluding HTTP
and queue latency. Cache-growth recovery, other hardware, multi-speaker inputs
and subjective performance quality remain separately unqualified.

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
