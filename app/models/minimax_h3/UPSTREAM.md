# MiniMax H3 upstream components

The video VAE, audio VAE, and scheduler in this directory are derived from
the Hugging Face Diffusers MiniMax H3 implementation at commit
`abc5e9bf71fd38f53cd471bc3acaa84bc5ecbfdc`.

Those files retain their upstream Apache-2.0 copyright and license headers.
Maestro-specific model loading, packing, memory management, and Studio
integration are implemented separately in this directory.

The default runtime stack is pinned to:

- `MiniMaxAI/MiniMax-H3` commit `5d9b308a59ab12e67147f191e184baf704185bd1`
  for the official processor and text-encoder configuration.
- `Comfy-Org/MiniMax-H3` commit `0543966fbdce5ba05709a8f2031c94bdba629b4a`
  for the scaled-FP8 FL2VA transformer, NVFP4-AWQ conditioner, and compact
  video/audio VAE checkpoints.
- `Comfy-Org/MiniMax-H3` commit `eb8a16107c595128b3a578f82d2ce2f75920c355`
  for the separate scaled-FP8 Ref2VA transformer.
- `deepbeepmeep/Wan2GP` commit `fa79896eadbcb048dc13e76233b3b72486b522a8`
  as the Ref2VA reference for mixed-media presentation, VAE conditioning,
  packed row ordering, and reference timestep/rotary geometry.
- `deepbeepmeep/Wan2GP` commit `110abb49b8f97d31ef8d6e9ef32c6144113b001f`
  as the reference for checkpoint-header probing and the original full-width
  timestep/AdaLN path used by compatible community FL2VA checkpoints.

Those model weights are downloaded at runtime and are not distributed in the
Maestro repository. They remain governed by their respective model terms and
any authorization or waiver required for the user's location.

The Ref2VA profile is opt-in and uses the ordinary Hugging Face download path
only after the user selects it. Maestro does not prefetch the checkpoint or
attempt to bypass repository access controls, license acceptance, or regional
terms.

Ref2VA is semantic reference conditioning, not first/last-frame conditioning.
Its profile keeps those modes mutually exclusive and records the released
limits: up to 9 images, 3 videos, 3 audio clips, and 12 mixed files; reference
video/audio clips are 2–15 seconds with at most 15 seconds per modality, and
generated clips are 4–15 seconds. FL2VA remains the separate keyframe profile.

Maestro contains an experimental 18-frame video/audio boundary-conditioning
adapter derived from the reviewed WanGP packing work, but it is not a public
capability. The fixed-seed live gate on 2026-08-08 passed ordinary Base and
pure Ref2VA generation while the mixed native Ref2VA cut case failed during
generation. The adapter therefore remains fail-closed unless a developer
deliberately sets `MAESTRO_H3_NATIVE_BOUNDARY_EXPERIMENTAL=1`; fresh and saved
user settings keep it off. Legacy FL2VA-to-Ref2VA automatic routing remains the
supported long-form path until the full live matrix and visual review pass.

Maestro probes H3 tensor headers before allocating the transformer. A released
`adaln_t_table` selects the compact curve path; original `time_embedder`
weights select the full-width timestep path. Quantization metadata is routed
independently: scaled FP8 and NVFP4 descriptors remain with MMGP, while only
declared INT8 ConvRot or W4A8 tensors are replaced by their specialized
modules. FL2VA/Ref2VA conditioning, modulation geometry, and weight storage
format are intentionally not inferred from one another.

## WanGP 12.44 selective integration ledger

Maestro's reviewed WanGP 12.44 source anchor is the immutable upstream commit
`5c8b4ac3c5e15135b6510d9b6d4d57002e4bb5e4`, whose `wgp.py` declares
`WanGP_version = "12.44"`. This is a provenance anchor, not a wholesale merge:
the anchor also changes MiniMax H3 packing, handler, pipeline, transformer, and
VAE code that Maestro deliberately does not import through this integration.
The H3 sources and model revisions recorded above remain authoritative.

The selectively incorporated runtime/dependency records are:

- FlashVSR crash guard from `deepbeepmeep/Wan2GP` commit
  `ecf8cf24f7eb9eabc5866a1dc4244c105cc9b3ca` (post-change runtime blob
  `fc2feee1141f04a4a3be286ca1b3a768e21e79fb`): the public
  `upscale_video` entry point runs under `torch.inference_mode()`.
- MMGP `3.7.12`, required by both the FlashVSR commit's upstream requirements
  and the 12.44 anchor. The published PyPI source distribution has SHA256
  `c49d021d43838f2fc41b14b0b2310796bc2232f5792271cb4df9f53ab22124e6`;
  its universal wheel has SHA256
  `2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf`.
  Maestro's requirement binds that wheel's official `files.pythonhosted.org`
  URL and SHA256 fragment. The existing MMGP entry points used by Maestro
  retain compatible signatures and add optional quantization/load callbacks.
- WanGP Community License 2.0 exactly as published at the 12.44 anchor,
  SHA256 `67c8e68389c945423c560c13936f0a960e5d2ffdcc5bb2ded4122fe1b095960f`.

Future selective updates use `scripts/cherry_pick_upstream.py`. The tool accepts
only commits contained in fetched refs from the official Wan2GP remote, maps
known upstream paths beneath `app/`, rejects unsafe or unmapped paths, refuses
license/notice deletion, and records the exact upstream repository and full
commit ID as commit trailers. H3 model sources, defaults, profiles, and the
shared upstream model registry are protected from automatic application. The
tool never treats this ledger as permission to overwrite Maestro-owned
launcher, UI, or independently maintained H3 files.

## Managed H3 Turbo accelerator

The optional `h3_turbo_v4` profile uses LarryVRH's
`minimax_h3_turbo_v4_step600_ema.safetensors` from Hugging Face revision
`afc0346516372a17162c14df3c5264de1d9aa1c0` (779,849,816 bytes, SHA256
`5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3`).
Its compact-base timestep companion, `h3_silu_temb_grid.safetensors`, is pinned
to `Larryvrh/ComfyUI-MiniMax-H3-Turbo` commit
`55fee864dd7b2976b1c4ce3c3d5f7968f181409f` (5,510,600 bytes, SHA256
`30eb3c2cc7fb6b470d9717ff840d359313ac27cd64b705e32da1baa10f72d6a8`).
Neither source uses a moving `main` reference.

Maestro validates the LoRA's exact 518-key BF16 header and shape map before
publishing the LoRA and companion grid as one managed release. MMGP continues
to own the backbone runtime adapters. On the compact/pruned base only, Maestro
removes the 51 AdaLN A/B pairs from the generic loader and adds
`(B @ A @ silu(t_emb).T).T` to each compact AdaLN projection without
registering those large weights in the module state tree. Profile unload and
ordinary LoRA cleanup remove the custom tensors and grid.

The managed Turbo strength remains fixed at 1.0 and the authored range remains
4–8 actual model evaluations. Scaled-FP8 Base FL2VA and Ref2VA use MMGP's
backbone adapters; W4A8 and PinkCherry/INT8 use quantization-safe
activation-space residual hooks (`base(x) + B(A(x))`) because applying their
deltas to packed/INT8 weights is neither shape-safe nor dtype-safe. Independent
user LoRAs may stack through MMGP only when MMGP's per-target key/shape checks
accept them. Tea/Mag cache combinations remain explicitly unsupported because
`MiniMaxH3Transformer` has no cache execution hook at 4–8 evaluations.

Dense SDPA is supported. Sol-Attn is structurally equivalent only when
`h3_sol_dense_steps` covers every Turbo evaluation, forcing its exact SDPA
fallback for the whole run; sparse Sol still needs a quality gate. Ref2VA is
shape-compatible and available to the synthetic validation lane, but remains
unavailable by default until both 4- and 8-evaluation outputs pass recorded
visual checks for semantic-reference adherence, motion, coherence, and lack of
collapse. Maestro's scheduler takes grid points and executes one fewer
evaluation, so Turbo's authored 4–8 range is translated to 5–9 grid points while
retaining separate video shift-12 and audio shift-3 schedules of equal length.
The ordinary H3 20-grid path is unchanged.

## Ordinary H3 LoRA compatibility

Ordinary user-LoRA normalization and AdaLN affine conversion selectively port
the official `deepbeepmeep/Wan2GP` commits
`55c508821dc9df1a635dd342be91d94d7c7656c3` (module mapping and rank-8 affine
packages), `de258bf136d701a96d52e63b3984355b429eaa1c` (rank-64 conversion), and
`6a9f5a62d063e4dab96c75d960650a5be77ff83b` (Diffusers/PEFT names and fused-FC1
ordering). Maestro does not import those commits' model profiles, download URLs,
or defaults. The managed Turbo file remains on its separate exact 518-key
validation and preprocessing-index path.

The four vendored safetensors packages are byte-identical to those commits:

| Package | Bytes | SHA256 |
| --- | ---: | --- |
| `fl2va_rank8.sft` | 130072 | `a42778e02ab2708dc70e23837ec4d3061b44f938c940decbc7a5b91f2c27c59e` |
| `ref2va_rank8.sft` | 130072 | `7179899e59fce9c36038cd6c0c57edaced0032c769c436cef234b07bf809381f` |
| `fl2va_rank64.sft` | 955640 | `df40361cba88c9d6cf300a90d506ed349b349bd23babb6b94f15ab2df1b00f6e` |
| `ref2va_rank64.sft` | 955640 | `4b661b03438d5d5fcc86be3dad2d9dbbd129720f089f8e94914b369eee198cee` |

Compatibility is deliberately fail-closed:

| Input | Compact target (4/8/64) | Full target (2688) |
| --- | --- | --- |
| Native MMGP, Kohya, Diffusers, or PEFT `.default`/dotted factors | Supported | Supported |
| FL2VA-authored affine LoRA on FL2VA | Supported | Supported |
| Ref2VA-authored affine LoRA on Ref2VA | Supported | Supported |
| Separate Diffusers Q/K/V and Diffusers SwiGLU FC1 ordering | Fused/reordered before MMGP | Fused/reordered before MMGP |
| Unknown model/module, mixed-dialect/width, orphan, malformed, or colliding state | Rejected | Rejected |

Only explicitly named Maestro H3 variants map to FL2VA or Ref2VA affine
geometry. A future profile name must be added deliberately; it is never inferred
from an arbitrary filename or substring. LoRA files do not carry authoritative
FL2VA/Ref2VA authorship metadata, so users must select the matching checkpoint;
Maestro applies only that selected checkpoint's pinned affine package.

## Shared native-shot planning

Maestro's shared Studio/Director H3 shot contract adapts the model-independent
independent-shot context, structured dialogue, and final-blocking ideas from
the official `blizaine/maestro` v1.6.5 commit
`d500f58e0c2be948800c757fd106c5254c70b605`, principally
`app/services/director_video_strategy.py`,
`app/services/director/h3_dialogue.py`, and the H3 planning integration in
`app/services/director_pipeline.py`. The implementation here is a smaller
deterministic shared planner: Studio and Director author one global narrative,
then persist the exact native segment prompts, semantic boundaries, dialogue
manifest, final blocking, and selected profile-pressure policy for recovery.

Draft and Fast use soft preferred automatic segment sizes of 192 and 243 legal
frames respectively for lower time to first completed segment. This is not a
claim of lower total runtime and does not reduce the model's native legality
ceiling. Explicit timestamps and manual segment ceilings remain authoritative;
indivisible action/dialogue beats and additional seam/audio exposure can
suppress the preference. Quality, High, delivery, Ultra, and 4K profiles keep
the ordinary native maximum unless authored semantics change geometry.
Authored timestamps are not rounded to metronomic intervals: arbitrary
monotonic boundaries produce independent legal generated lengths plus exact
per-segment published/tail-trim arrays. Untimed authored action density may
also produce unequal native lengths without splitting an indivisible dialogue
or action unit. Those arrays are persisted and replayed unchanged; native
17-frame history-prefix discard remains a separate concat operation.

No source or code from `NikoDemon80/ComfyUI-H3-Motion-Context` is copied into
this planner. That separately licensed ComfyUI project was reviewed only as a
research lead; this integration derives from Maestro's own project history and
the existing Wan2GP/MiniMax runtime contracts recorded above.

## Optional SageAttention2++ engine

Advanced settings expose `sage2` as a capability-gated engine. Maestro
builds only the official `thu-ml/SageAttention` v2.2.0 source at immutable
commit `eb615cf6cf4d221338033340ee2de1c37fbdba4a`; it does not consume
community wheels. Provisioning is restricted to Linux, CUDA toolkit/runtime
12.8 or newer, and NVIDIA SM120. If the host compiler is older, Pinokio creates
an isolated CUDA 12.8.1 toolkit from NVIDIA's version-labeled Conda channel;
it does not replace the system toolkit. Other systems keep the existing
Sol-Attn and exact dense-SDPA paths.

H3 reaches the official dispatcher with its native B x tokens x heads x 128
(`NHD`) BF16/FP16 tensors and `is_causal=False`. A padding mask, a different
layout/head dimension/dtype/device, missing or mismatched source revision, or
any kernel exception rejects explicit `sage2` before an output can be labeled
or benchmarked as Sage; the error directs the user to exact noncausal SDPA.

The tracked Sage validation record is SHA-256-pinned in the runtime and binds
the official Sage source/distribution, Torch/CUDA/Triton/GPU envelope, Base
checkpoint revision, managed Turbo LoRA/grid hashes, 3,100 actual Sage kernel
calls with zero fallbacks/errors, benchmark specifications, and explicit human
visual/audio review. Output success alone cannot satisfy the gate. Within the
exact recorded envelopes, Draft selects Sage at 608x352/Turbo 4 and Fast at
864x480/Turbo 8. Fast's SDPA comparison loaded the model cold, so its wall time
is provenance rather than a speed claim. Quality remains Sol-Attn and Ultra
remains dense SDPA. W4A8, PinkCherry, and Ref2VA
remain structurally reachable but unvalidated, and the curated profiles never
select Sage for them. Successful timing records carry both requested and
effective engine IDs.
