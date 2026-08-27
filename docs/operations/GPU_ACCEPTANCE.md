# Maestro Deferred GPU Acceptance

This is the single GPU-required acceptance ledger for the current product
waves. The underlying source, model-free tests, UI tests, and build checks may
already be complete; do not describe them as blocked merely because no GPU was
available. Close a row only with the live GPU/runtime or generated-output
evidence named here.

The owner has authorized activating every CPU-safe account, support, sharing,
implemented supporter-benefit, credit, and queue surface now. The implemented
default benefits are supporter recognition and bounded queue priority;
promotional Maestro credits are the separate hosted allowance. GPU absence
delays only model/runtime and real-demand acceptance. It is not a reason to
leave the credit system or other CPU-safe product surfaces disabled.

## Common gate

Before a GPU row:

1. Confirm the GPU is available to Maestro and not reserved by higher-priority
   owner or agent work. Use the current runtime and current dynamically resolved
   loopback URL; never reuse an old port or readiness record.
2. Confirm accounts and the zero-quarantine project migration are active, the
   owner is signed in, and the intended test project is authorized. Do not turn
   accounts off to make a legacy benchmark client work.
3. Read the existing H3 legal-access record. Japan (`JP`) is the owner's current
   declaration; do not request another declaration when the live signed record
   is current.
4. Keep prompts, project names, account IDs, URLs, cookies, model paths, and
   credentials out of tracked reports. A report may retain case ID, model and
   asset revisions, settings, seed, timings, memory measurements, output digest,
   and the evidence label.
5. Run one case at a time. Stop after a load, compatibility, CUDA, OOM, output,
   or cancellation failure; preserve the failed row instead of silently routing
   to a different model or attention backend.

Where the current loopback benchmark runner has an authorized project-access
path, named H3 cases use:

```bash
python app/scripts/benchmark_h3_profiles.py \
  --base-url "$READY_URL" \
  --project "$TEST_PROJECT" \
  --case CASE_ID \
  --output-dir "$UNTRACKED_REPORT_DIR"
```

Keep the variable values outside tracked files. If active account membership
cannot be presented to this runner, execute the same named model/profile through
the signed-in owner UI; do not weaken account enforcement or substitute a static
`--dry-run` result.

## Acceptance matrix

| Area | Live action | Accept only when |
| --- | --- | --- |
| H3 Base FL2VA and managed assets | Run `base_native_sdpa`, `base_turbo_4_sdpa`, and `base_turbo_8_sdpa`. Then run `base_high_native_sol` explicitly. | Each exact managed checkpoint, conditioner, VAE, audio asset, and Turbo asset resolves from its declared revision; the selected case completes without fallback; video and synchronized audio are finite, playable, correctly timed, and visibly match the synthetic brief. High must use the requested Sol settings rather than a hidden dense fallback. |
| H3 Ref2VA | Run `ref2va_native_sdpa`, `ref2va_turbo_4_sdpa`, and `ref2va_turbo_8_sdpa` with the runner's procedural reference. | The Ref2VA checkpoint—not the FL2VA alias—is loaded; ordered reference identity is visible in the result; video/audio complete; exported frames show the red-circle/yellow-triangle identity; no request is silently rerouted to Base FL2VA. |
| H3 W4A8 | First run the runtime's `python scripts/validate_h3_w4a8.py` from its installed environment, then run `w4a8_turbo_8_sdpa`. | The finite-output marker is bound to the current GPU, Torch, Triton, runtime revision, and checkpoint; the full generated sample is playable and visually acceptable; no dequantization or different checkpoint fallback is reported. |
| H3 PinkCherry FL2VA | Select **PinkCherry H3 FL2VA (Explicit)** in the signed-in owner UI and generate one minimum-duration private sample. An allocation-only scout may use `pinkcherry_high_allocation_p4`, but it cannot close this row. | The exact PinkCherry checkpoint and required assets load, the explicit model remains the selected producer, audio/video complete, and the owner accepts the output. A two-step allocation probe or a Base fallback is insufficient. |
| H3 aliases and linked assets | For each Base, Ref2VA, W4A8, and PinkCherry row, capture the canonical model ID plus the resolved asset revision/name from the live model/download projection before and after generation. | Both supported linked-install aliases resolve to the same intended canonical bytes where declared, stale partial assets are not accepted, every variant keeps its own checkpoint identity, and deleting/rechecking one alias cannot make an unrelated or incomplete file appear ready. |
| H3 Director | Create one short Director project using H3 Base and one using the intended reference-conditioned path. | Preview, approval, queue handoff, clip generation, audio, final join, saved pipeline state, and rerun/recovery all retain the selected H3 model/profile, exact dialogue, reference labels, timing, and project authorization. Human review accepts the final, not merely the clip plan. |
| FlashVSR delivery | Run `base_1080p_delivery`, `base_ultra_delivery`, and the exact 4K gate with `python app/scripts/benchmark_h3_profiles.py --base-url "$READY_URL" --project "$TEST_PROJECT" --live-4k-acceptance --output-dir "$UNTRACKED_REPORT_DIR"`. | The native H3 result is preserved, FlashVSR is the recorded delivery stage, output geometry is exactly 1920x1080, 2688x1536, and 3840x2160 respectively, audio remains aligned, final publication selects only the explicit delivery output, and the owner accepts detail/identity without a mislabeled native-4K claim. |
| Official H3 SageAttention2++ | Run `base_native_sage2`, then the same-seed `base_fast_864_turbo_8_sdpa` / `base_fast_864_turbo_8_sage2` pair. | The pinned source build and validation record bind to the current GPU/Torch/CUDA/Triton/checkpoint; kernel execution is proven with no fallback; video and audio are both reviewed; any speed statement separates cold load from generation. Do not promote Sage to W4A8, Ref2VA, or PinkCherry from Base-only evidence. |
| Sol runtime | From the installed Sol environment run `python scripts/verify_sol_runtime.py`, then complete `base_exact_dense_sol` and `base_high_native_sol`. | Verification reports the exact supported CUDA capability and required Python/PyTorch/CUDA/Triton versions; generated cases record `sol_attn`; output is finite with synchronized audio and no silent SDPA/Sage fallback. Compatibility aliases `start_sol.js` and `sol_install.js` must still enter the canonical start/update flows rather than a separate runtime. |
| Optional FlashAttention | Run Maestro's normal startup preflight with the installed optional FlashAttention wheel, then exercise one feature that selects it and one forced incompatibility/fallback check. | The current wheel imports and executes on the actual GPU/runtime when compatible. When incompatible or broken, Maestro disables it once, reports the bounded fallback, and successfully uses SageAttention/SDPA without poisoning later imports. Import success alone is not kernel acceptance. |
| Music3 Studio generation | Verify the published runtime with `python app/scripts/start_music3_runtime.py verify --pinokio-root "$PINOKIO_HOME"`, start it through the normal Pinokio flow, and generate one private lyrical song plus one instrumental song in Studio. | The exact pinned runtime/model generation is active, LAN/Cloudflare Music3 access remains off as intended, cancellation works, both outputs are playable with requested duration/style, lyric sections stay ordered, and the owner accepts musical and vocal quality. Runtime verification without a song does not close this row. |
| Music3 to Director | Use **Send to Director** from an edited Lyric Playground song, generate the soundtrack, approve it, and continue through one short Director output. | Director receives the exact workspace, model, style, lyrics, duration, and instrumental state; stale preparation is rejected after an edit; the accepted song's measured structure drives the video plan; the final audio/video is aligned and accepted by the owner. |
| Scene Kit to Generate | Build a Character/Location selection from at least two kept variants with known output IDs and apply it to a reference-capable generation. | The submitted job contains the exact ordered output IDs, paths, labels, and asset/variant identities selected; no path-only recovered row is accepted; the generated output visibly follows both references; changing project/account/asset state during staging cannot commit a partial set. |
| Scene Kit to Director | Apply a Cast Board/Scene Kit selection, create a Director preview, then generate and rerun one clip. | Preview, queued clip, saved project, final join, and rerun preserve the exact selected output IDs and labels; project/account epoch changes fail atomically; the final clip visibly retains the intended cast and setting. UI attachment alone does not close the row. |
| Krea owner generation | While signed in as the owner, record the live Krea v2 attestation only when the selected profile/runtime is ready, then run one private Krea 2 generation and one Quad Krea Character Sheet. | The server resolves role scope to `noncommercial`; no client-selected scope can override it; the exact Krea profile and license revision remain attached; both outputs complete and the owner accepts identity/layout quality. |
| Krea member generation | With a non-owner user account, run one authorized Krea 2 generation in a project where that member can generate. | The server resolves role scope to `commercial_under_1m`; project membership and generation permission are enforced; no owner/noncommercial scope leaks to the member; the output completes under the selected Krea profile. |
| Dynamic Krea experimental | Explicitly select **Dynamic — Krea 2 (experimental)** and generate one Character Sheet after the standard Quad rows pass. | It remains visibly experimental and opt-in, never replaces Quad FLUX as the safe default, preserves output/repair lineage, and passes owner visual review. Failure does not block the standard Krea/FLUX sheet path. |
| Credit queue under real GPU demand | With hosted enforcement live, enqueue otherwise-valid jobs representing funded, partial/zero, and owner-exempt accounts while a real GPU job occupies the worker; include hold/resume and a restart/recovery cycle. | All submissions become durable jobs; funded work receives only bounded priority; partial/zero allowance remains in the lowest ordinary FIFO band and eventually receives starvation-bound capacity; the owner and local/authenticated-LAN execution consume no allowance; reservations conserve units across consume, release, cancellation, restart, and recovery; no path returns a flat credit `402`. |

## Closure record

For each row record: date, source revision, host platform, GPU/runtime identity,
model and asset revisions, case/profile, output digest, pass/fail, evidence class,
and residual gap. Keep the actual media in the project's normal private output
store, not in Git. A model-free test, downloaded asset, runtime marker, printed
URL, completed queue plan, or screenshot of controls is not generated-output or
human acceptance.

### 2026-08-27 PinkCherry beta-0.6 runtime receipt

- Source revision: `5a30ce7`; Linux `7.0.0-30-generic`; RTX 5090; NVIDIA
  `595.84`; CUDA compiler `12.8.93`.
- Producer: `minimax_h3_pinkcherry_fl2va`; checkpoint
  `PinkCherry_fl2va_MiniMax_H3_pruned_int8_convrot-beta-0.6.safetensors`
  at pinned revision `8642ce26b8ff3d671fb8370de70d8fd1b36b070c`, SHA-256
  `0cb2812f061003d9f345186d58f1bafbf902c6ad2b4c064590b4fc4811634ad1`;
  Heretic INT8 conditioner; no LoRA, managed Turbo, TeaCache, or model fallback.
- One private member-project job completed 28 Sol-Attn steps and produced a
  7,661,687-byte HEVC MP4 with 124 frames at 1344x768/24 fps plus 32 kHz stereo
  AAC. Output SHA-256:
  `d36103f99d9579637ef9bf35e17f7548a5368cba27c490e36a1782dd471054fd`.
- Observed peak VRAM was 27,333 MiB. Sampled frames retained one coherent adult
  performer, dress, stage, lighting, and plausible anatomy/motion; the audio
  stream was finite and non-silent (peak -19.34 dBFS, RMS -32.80 dBFS).
- Evidence class: live local GPU generation plus agent visual and technical A/V
  review. The runtime/output portion passes. The strict matrix row remains open
  only for the separately worded signed-in-owner UI action and direct human
  keep/reject confirmation; do not rerun merely to replace the authorized
  generic account unless that remaining distinction is decision-changing.

When all applicable rows pass, update this matrix rather than deleting it. Keep
failed or intentionally unsupported variants as provenance, and remove only a
superseded command after its replacement is both documented and accepted.
