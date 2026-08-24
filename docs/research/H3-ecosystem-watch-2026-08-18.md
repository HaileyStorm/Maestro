# H3 ecosystem intake - 2026-08-18

This intake deduplicates a public-source research tab set. It does not install a node, download a model, inspect private generation data, or change the current Maestro checkpoint.

**Refresh:** mixed dumps on 2026-08-19 updated overlapping H3 clusters
in `docs/research/candidate-intake-2026-08-19.md` (Batch 1: latent
masks, RAVEN vs Turbo, sheet-as-Ref2VA, LTX enhance; Batch 2 evening:
Ref2VA encode-size, sheet role-split, Turbo audio tradeoff, plus
watch-only OrbitSheets / Sigma-refiner / 3d→real slider). Decisions
below still stand unless that successor says otherwise.

## Current checkpoint

- Keep Maestro's native H3 reference controls source-bound to the official workflow: up to nine image, three video, and three audio references, joint audio/video output, and the documented frame-grid behavior. Reference audio is conditioning, not passthrough audio.
- Keep the current Quality/High step policy. A universal 25-step minimum is not supported: official base recipes and task-specific 4/8-step Turbo LoRAs have different contracts.
- Keep cancellation settlement server-measured and capped. Latent preview or postprocess estimates must not create a second refund for the same canceled work.
- Add no new dependency before the private usable checkpoint is accepted.

## Next-wave order

1. Evaluate [H3 Multishot](https://github.com/jlucasmcrell/ComfyUI-H3-Multishot) and [H3 Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender) together as one segment-planning/continuation capability. Compare drift, audio continuity, disk cache, restart behavior, and final mux evidence before selecting or composing them.
2. Benchmark an AV-preserving latent-upscale branch such as [ComfyUI-MiniMaxH3_LatentUpscaler](https://github.com/Tr1dae/ComfyUI-MiniMaxH3_LatentUpscaler). Require preserved reference conditioning, fixed audio timing, explicit VRAM estimates, no surprise model download, and before/after artifact inspection.
3. Add RIFE only as an explicit post-upscale interpolation branch with duration/audio reconciliation. Do not promote LTX-2.5 re-rendering as a faithful default: public reports show useful enhancement but also identity, text, logo, and marking drift.
4. Evaluate [H3 FaceRefine](https://github.com/Carasibana/ComfyUI-H3-FaceRefine) as an optional decoded-video branch that preserves the original audio stream. Its detector, InsightFace, and ONNX runtime dependencies require an isolated install plan.
5. Model [official H3 Turbo](https://github.com/ModelTC/Minimax-H3-Turbo) as task/resolution-specific 4-step and 8-step profiles with their own cost/quality labels. Never apply a Turbo LoRA or step count globally.
6. Treat [LTX-2.5 Multiple Subject Reference](https://huggingface.co/LiconStudio/LTX-2.5-Multiple-Subject-Reference) as an LTX re-encode/refine workflow, not an H3-latent connection.

## Watch, not adoption

- [Qwen-Video-Edit](https://yunpeng1998.github.io/Qwen-Video-Edit-Page/) is a promising separate edit backend, not an H3-native extension.
- [Kijai's experimental H3 weights](https://huggingface.co/Kijai/MiniMax-H3-experimental), Sage/Comfy-Kitchen reports, SPEED/parallel claims, and H3 latent schedulers need source-bound implementation plus hardware-specific evidence.
- [MiniMax Music 3](https://huggingface.co/MiniMaxAI/MiniMax-Music3) belongs in a future audio-asset lane, not the H3 enhancement path.

## Compatibility Watch sources

- On each release: ComfyUI core releases and official workflow templates.
- Weekly while active: Multishot, Extender, FaceRefine, latent-upscaler repositories, and their issues/releases.
- Weekly while active: Turbo, Kijai experimental, LTX MSR, and Music 3 model cards and discussions.
- Evidence intake: reproducible `r/comfyui` and `r/StableDiffusion` reports for upscale, interpolation, motion, VRAM, and audio behavior. User reports can nominate a probe; official/source evidence controls defaults.

Promotion requires an isolated install/rollback plan, exact model and node revisions, representative 5090 evidence, private prompt/media boundaries, failure and cancellation settlement checks, and an obsolescence audit against the path it replaces.
