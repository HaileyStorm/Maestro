# Astra continuation handoff — 2026-09-06

## Current continuation pointer — 2026-09-12

The complete committed-tree backend suite at `4402792` passes 5,037 tests with
19 skips and zero failures. Python compilation, JSON grammar regressions and
the root tracked-publication guard pass. The latest unchanged UI source has
594 passing tests plus a passing production build and targeted lint. Earlier
failure lists and partial-suite checkpoints below are historical, not current
backlog; do not rerun them merely to rediscover resolved failures.

Improve before Generate now shares workflow eligibility between its control
and submission. Blend, avatar Edit and audio-only paths retain standalone
improvement without an inactive saved toggle blocking submission; see the
closure below. Continue the broader app-flow audit and remaining WIP review.

Current safe work also includes the broader app interaction/copy audit and review of the
remaining inventoried WIP: only the protected untracked storage-janitor
source/test pair. Tracked source is reconciled; launcher account-path forwarding
is adopted and the launch-file whitespace residue was verified and retired. The storage pair
requires explicit ownership recovery under FRESH_THREAD_HANDOFF.md before
claiming, modifying or adopting it.
H3 executable-plan startup handling and the related Studio tests have the
source/test closure recorded below. Inspect each
against the committed implementation before adopting, replacing or retiring it;
keep storage mutation disabled pending its separate ownership/acceptance gates.

Profiles, mode continuity, output restore, H3 attention selection and Inpaint
mask/preview continuity have source/test closures below. Their browser/live
acceptance remains separate. The pending browser scratch-filesystem question
has not been answered; preserve the E2E runner's cross-filesystem contract.
Launcher/restart, account/credit activation, provider and GPU boundaries remain
as stated in the owner updates and authorization sections below.

## Current GPU authorization — owner update, 2026-09-06

The owner explicitly authorized GPU work subject to coordinator grants and
required the installed `gpu-coordinator-client` skill. This supersedes the
GPU prohibition in the copied native Goal and historical CPU-only instructions
below. It permits the planned GPU acceptance work once the client contract is
satisfied; it does not establish that a lease exists or that any GPU acceptance
has passed. Service restarts, credits/SSO activation, new providers, and browser
filesystem permissions retain their separate existing boundaries.

The coordinator owner resolved the initial allowlist rejection (`harness-a97`).
This migrated checkout is now registered as `maestro-local`; former-checkout
identities remain separate. Registration is never GPU authority: validate the
durable grant, current status/epoch/time, and exactly matching raw ledger entry
before every start, stop by its end, and withdraw/confirm after completion.

### W4A8 runtime prerequisite — 2026-09-07 UTC

The first leased synthetic check failed because installed comfy-kitchen 0.2.26
lacked the W4A8 APIs. Its lease was withdrawn. The repaired installer uses UV
with the selected interpreter, builds archived pinned source, preserves a
rollback copy, invalidates the old marker, and restores package/marker on
validation failure. Package fingerprinting happens before import. Schema-2
markers bind the installed bytes as well as revision and runtime identity;
Sage capability remains independent.

The isolated candidate passed 80 CPU tests with 11 explicit skips (91 total),
syntax checks, and independent review. Native Windows execution remains
unverified. A second exact lease ran the small synthetic check on Linux with
RTX 5090 (SM 12.0), Torch 2.10.0+cu130, and Triton 3.6.0. Eager weight
quantization and Triton W4A8 linear dispatch passed with relative MAE
0.07095864661654136. The pinned package and matching schema-2 marker are now
installed in `app/env-rtx50`; the prior package remains in the private rollback
artifact. The GPU child exited and the lease was confirmed cancelled.

Receipts: `.artifacts-temp/astra-w4a8-installer-20260907/`,
`.artifacts-temp/astra-w4a8-kernel-20260907/`, and
`.artifacts-temp/astra-w4a8-repair-20260907/`. This is runtime/kernel evidence;
full checkpoint generation, visual quality, and human acceptance remain open
in `GPU_ACCEPTANCE.md`. No app restart or full-model run occurred.

### Observed offload recovery — 2026-09-07 UTC

Denoise OOM recovery now uses WGP's successfully loaded MMGP profile instead
of reconstructing task intent and applying the standing floor. Observed 4.5
retains its same-setup retry and subsequent escalation to 5 on the same canvas;
unknown or omitted profiles preserve the original failure without fabricating
a retry. Persistent host-limit denial requires an observed profile 5 and the
existing mid-denoise/exhaustion evidence. Existing host-limit history is retained.

The isolated HEAD-based candidate passes all 89 tests across observed-profile,
OOM policy, offload-plan, host-limit, memory-lifecycle, and WGP boundary suites.
Syntax checks pass. Receipts and preserved preimages are in
`.artifacts-temp/astra-observed-offload-20260907/`. Evidence is CPU/synthetic,
not live OOM recovery or a GPU memory/performance claim.

At this earlier checkpoint, the native-max floor still needed requested/effective
profile reconciliation and preserved completed-prefix/calibration semantics.
The subsequent native-max integration below satisfies that source gate and
removes the draft test that treated observed 4.5 as an attempted 5. Live GPU
memory/performance acceptance remains separate.

## Authored-shot packing — 2026-09-07 UTC

Short authored shots now pack with their neighbors into legal native windows
without losing their published timing. A shot splits only when its required
generation, including an end-anchor tail, exceeds the active ceiling. Final
window folding discards previous padding, preserves the required final tail,
and realigns the merged frame count instead of summing two grid values.

A continuing authored action gets an opening-only instruction in its first
window; later windows retain continuation and single-owner dialogue behavior.
Cuts inside packed windows retain their authored frame position. Timestamp and
range errors explain decimal seconds versus hours:minutes:seconds through
exact reviewed copy, without echoing authored input.

The final isolated candidate passes 452 planner/Studio/Director/duration/recovery
tests plus 64 frame-lattice/preflight/optimization/audio tests. Its deterministic
686-case packing matrix covers frame-grid legality, exact published totals,
end-anchor tails, and preservation of fitting shots; a separate timed case
covers the manual 192-frame ceiling. Independent candidate review is clean.
A synthetic plan compiled by the prior committed planner still passes saved-seal
validation byte-for-byte; changed new-plan instructions receive a different
seal. This is CPU/synthetic and local codec evidence, not live model quality or
GPU recovery acceptance. Receipts and source hashes are in
`.artifacts-temp/astra-shot-packing-20260907/`.

## NVFP4 native LoRA forwarding — 2026-09-07 UTC

NVFP4 modules now keep the native-forward marker as an instance attribute,
which the pinned MMGP router copies. Ordinary LoRA execution retains the
module's input scaling and real dequantization. The numerical CPU regression
uses the installed router load, hook, wrapper, and native forward; it checks
scaled base output plus factor delta, floating output, and unchanged packed
weight bytes across three ranks and both bias modes.

LightX2V inputs now pad rows to 128 and trim the result back to the original
shape. Empty inputs retain their dtype and do not report or dispatch a kernel.
Kernel RuntimeError/OOM propagation remains intact. The uncommitted global
LoRA monkeypatch and catch-all kernel fallback were removed instead of adopted;
the raw-integer transformer activation casts were subsequently retired after
the input-dtype diagnosis below; their original bytes remain in private evidence.

The isolated candidate passes 121 of 131 tests (10 explicit runtime skips),
plus the five NVFP4 CPU tests in the CUDA-13 environment. Both local MMGP 3.7.12
installations' source bytes match their installed RECORD entries. Independent
review is clean. Receipts/preimages are in
`.artifacts-temp/astra-nvfp4-native-20260907/`. These are CPU numerical and
mock-kernel dispatch checks, not live GPU kernel, full-model, or Windows proof.

Remaining task-owned compatibility work:

- `NVFP4-K32`: CPU scale-layout repair is complete. Full physical 128-by-4
  scale tiles are deswizzled before logical columns are cropped; extra complete
  tiles remain supported and incomplete tiles fail explicitly. Tests match the
  eager reference exactly for widths 32/64/96, both nibble layouts, and
  FP32/BF16/FP16. The full candidate passes 124 of 134 tests (10 runtime skips),
  plus eight focused CPU tests in the CUDA-13 environment. Source/reference
  hashes and independent clean review are retained under
  `.artifacts-temp/astra-nvfp4-scale-20260907/`.
- `NVFP4-LIGHTX-SHAPES`: a fresh validated lease attempted nine small native
  LightX2V cases on RTX 5090 / Torch 2.10.0+cu130. The first case (logical M=1,
  N=128, K=64; padded activation rows=128) reached quantization and GEMM but
  failed with `cuBLAS error: 7`. Zero numerical cases completed. The process
  exited and the lease was withdrawn/confirmed cancelled. No retry, fallback,
  package mutation, model load, or service restart occurred.
  A subsequent source-informed, separately leased process-only
  `LIGHTX2V_NVFP4_GEMM=cutlass` comparison passed all nine identical shapes
  (relative MAE 0.00694–0.05556; 8,785,920 peak allocated bytes). Its lease is
  confirmed cancelled and its selector expired with the child process.
  Maestro's default remains unchanged; this is small synthetic kernel evidence,
  not full-model quality/performance acceptance.

  ELF/loader inspection found the extension requires `libcublasLt.so.12`,
  resolving to CUDA 12.0's `libcublasLt.so.12.0.2.224`; that header lacks the
  FP4 A/B scale-mode attributes used by the inspected source. The default
  cuBLAS path is therefore strongly implicated, but the exact failing API call
  is not instrumented. Source snapshot and installed binary selector strings
  agree; there is no build attestation linking that snapshot to the wheel.
  [Inspected source](https://github.com/deepbeepmeep/kernels/blob/2808bfb073bd91e4fe3ef83712f600b8d642579b/lightx2v_kernel/csrc/gemm/nvfp4_scaled_mm_kernels_sm120.cu).

  A second, separately leased diagnostic exposed an existing RECORD-verified
  cuBLASLt 12.8.3.14 library through one task-private symlink and a child-only
  loader path. Actual process maps confirmed the intended library. The default
  cuBLAS route moved past the prior error 7 but failed on the same first case
  with `Unable to find suitable cuBLAS GEMM algorithm`; zero numerical cases
  completed. The process exited, its lease is confirmed cancelled, and the exact
  temporary link was removed. No loader path, kernel default, or installation
  was changed permanently.

  Therefore library resolution contributes to the original failure, but a
  newer CUDA-12 library alone is not sufficient acceptance. The instrumented
  CUDA-13 diagnostic below now passes the same case. Next: build the complete
  pinned extension for the selected runtime, verify package/ELF dependencies,
  and stage rollback before a separate installed-runtime acceptance lease.
  Preserve rollback and the working CUTLASS diagnostic, but do not promote a
  permanent route from these small tensors. Source and acceptance must be
  checked independently on Windows. Receipts are in
  `.artifacts-temp/astra-lightx-cublas128-20260907/`; the completed CUTLASS and
  ABI records are in `.artifacts-temp/astra-lightx-route-20260907/`.
- `NVFP4-DORA`: MMGP's DoRA branch bypasses the native-forward marker and
  needs separate scaled-base/dequantization analysis and numerical acceptance.
  Ordinary low-rank LoRA evidence cannot close this item. Do not restore broad
  monkeypatches or integer casts as a workaround.

  A CPU reproduction using the actual installed MMGP router and the fixture
  in `tests/test_nvfp4_linear.py` now confirms a numerical failure even at
  adapter strength zero. With the fixture's nonuniform input scale, input
  `arange(128).reshape(2, 64) / 128`, no bias, and DoRA magnitude equal to
  1.1 times the dequantized weight's row norm, the routed output differs from
  the unchanged native base by maximum absolute error 16.0. Output is finite
  FP32 and packed weight bytes remain unchanged. This rules out treating a
  zero-strength DoRA as harmless on the current path; it does not establish
  a correct nonzero-strength repair. The process used the CUDA-13 environment
  with `CUDA_VISIBLE_DEVICES=''` and loaded no model. Preserve a zero-strength
  identity regression plus independent nonzero-strength numerical references
  when repairing the dependency integration.

  Independent source review places the repair in MMGP's DoRA weight assembly:
  fold the module's input scale into only the dequantized base columns before
  merging ordinary LoRA and applying DoRA normalization/magnitude blending.
  Ordinary adapter deltas must remain in unscaled input coordinates. The
  current dependency has no clean module hook for that step; use an explicit
  dependency protocol and a verified pinned package rather than a production
  wrapper duplicating private adapter logic. Acceptance must cover mixed
  ordinary-LoRA/DoRA, zero strength, nonuniform scales, and unchanged packed
  bytes. Do not alter the qtype while a queued build still binds its source hash.

## CUDA-13 diagnostic result — 2026-09-07 UTC

The local CUDA 13.0 compiler, selected environment's cuBLASLt 13 headers/library,
and Ninja/G++ are available. A task-private diagnostic extracts the unchanged
cuBLAS math/descriptor path from the recorded upstream snapshot, removes only
unused CUTLASS includes, adds failing-call/line diagnostics, and registers an
independent Torch operator. Review confirmed parity; the runner supplies the
upstream tensor dtype/device/contiguity/shape checks before calling it.

Artifacts: `.artifacts-temp/astra-lightx-cu13-build-20260907/` contains
`cu13_diagnostic.cu`, `build-manifest.json`, `gpu-plan.json`, and `run_once.py`.
The coordinator granted the queued request after the other project released
its reservation. The source-bound runner validated grant/status/epoch/raw
ledger before compilation and again before GEMM. Compilation used CUDA 13.0,
one Ninja worker, and the selected CUDA-13 Python environment. ELF inspection
confirmed `libcublasLt.so.13` and `libcudart.so.13` dependencies, with no CUDA-12
dependency in the diagnostic extension. Process maps confirmed the selected
CUDA-13 libraries (the unchanged original quantizer also loaded its own older
libraries).

The single M=128/N=128/K=64 diagnostic GEMM passed with finite output and
relative MAE 0.0069444444961845875. Binary SHA-256:
`dc0d700b1f712928414e4eef5d86bb2e1167f86a28fa17e2d9c332a08cf4e577`.
The process exited and the lease is confirmed cancelled. No installed package,
launcher, default selector, or service was changed. This is native diagnostic
GEMM evidence, not a complete extension rebuild, installed-runtime rollout,
full-model generation, performance, or Windows acceptance.

Next: produce a reproducible complete package build against the selected
runtime, assert resolved ABI dependencies and source/package provenance,
preserve the installed baseline for rollback, and perform installed-runtime
acceptance under a new exact lease. Do not promote the task-private operator
or process-only CUTLASS selector into the default path.

The complete nine-translation-unit package candidate is prepared under
`.artifacts-temp/astra-lightx-package-20260907/`, with exact source/header
manifests, one Ninja worker, CUDA-13 dependency and process-map checks, and
nine synthetic default-cuBLAS numerical cases. Its first leased build stopped
at compilation: Torch's extension helper disables half/BF16 conversions that
the upstream quantizer requires. No package or numerical validation completed;
the process exited and its lease is confirmed cancelled. `attempt1/` preserves
the original runner, plan, grant, log, and result. Independent flag review
confirmed that undefining the four injected suppression macros restores the
upstream CMake contract without changing kernel source.

The corrected runner subsequently received and validated a fresh lease. All
nine translation units built successfully, and the private complete package
passed all nine default-cuBLAS synthetic cases: widths 32/64/96, logical rows
1/50/129, and output width 128. Outputs were finite with relative MAE
0.0069444–0.0555556 against the dequantized reference. The process recorded
native quantization/GEMM dispatch and 42,283,520 peak allocated bytes. ELF
dependencies and actual process maps confirmed CUDA-13 cuBLASLt/runtime;
no CUDA-12 cuBLASLt/runtime was mapped. The selector was absent, so this
acceptance used the default cuBLAS route, not the earlier CUTLASS override.
Extension SHA-256:
`b2fde2c3b1f4a0748457396c6d716d3c859568fe214fe707cb517fb1dfeeb0e7`.

The process exited successfully and `withdrawal-r2.json` confirms the request
no longer grants authority and its ledger lease is cancelled. Build, package
hash, source, process-map, and numerical receipts remain in the artifact above.
No installed package, launcher, default selector, or running service changed.
`package_wheel.py` now packages the exact verified bytes with source/CUTLASS
licenses and Python/runtime metadata. Repacking produces the identical wheel
hash, and every RECORD entry passes an independent hash/size check. Wheel:
`lightx2v_kernel-0.0.2+torch2.10.0.cu130.maestro1-cp311-cp311-linux_x86_64.whl`,
SHA-256 `a7ebaf98817810394e24ea5e93e9b4774b615bbcb3c4db17d4574f201bac0be7`.
This is deterministic packaging of the tested local build, not independently
reproduced compilation or a cross-platform distribution release.
The installed-runtime validation and managed launcher integration below
complete the local package repair.
Full-model generation, visual quality, performance, and Windows remain open.

### Installed LightX runtime — 2026-09-07 UTC

`app/env-rtx50` now contains the verified
`lightx2v-kernel==0.0.2+torch2.10.0.cu130.maestro1` wheel. A fresh validated
lease covered installation and nine synthetic default-cuBLAS cases through
the installed package, without a private package import path or
`LD_LIBRARY_PATH` override. All cases passed with finite outputs and relative
MAE 0.0069444–0.0555556. Actual process maps confirmed the installed extension
and selected CUDA-13 libraries, with no CUDA-12 cuBLASLt/runtime mapped.

The installer binds the wheel, source, environment, and original 15-file
package snapshot; it rejects duplicate metadata and unexpected/linked payload
files before GPU work. CPU simulations verified exact rollback after partial
installation and validation failures without changing unrelated files. Five
inventory checks covered valid contents, extra payload, duplicate metadata,
symlinks, and unknown cache files. Independent review's inventory finding was
fixed before execution. A fresh post-run metadata/hash read verifies all five
installed payload files. The original package remains in the private rollback
directory; all 15 backup hashes still match. No rollback was needed in the
successful real run.

Receipts and runners: `.artifacts-temp/astra-lightx-installed-20260907/`.
The GPU child and installer exited successfully, the lease is confirmed
cancelled, and the exact package reservation was released. No service restart
occurred; this does not prove an already-running service adopted the new
package. Full-model, quality/performance, DoRA, and Windows gates remain open.

The managed integration below replaces the older public wheel in the normal
Linux install/update paths and standalone setup configuration. The mandatory
preflight resolved Pinokio home from its configuration and confirmed this
checkout lies in its `api` tree. Logs and the required `mochi/torch.js`
example were inspected; its platform-selected `shell.run`, relative path,
and selected venv pattern apply. `PINOKIO.md` documents a project-local
Conda path and separate venv activation.

Primary-source toolchain research favors a dedicated project-local Conda
environment for `nvidia/label/cuda-13.0.2::cuda-nvcc=13.0.88`: its dependency
metadata includes host GCC/G++, runtime/driver development files, NVVM/CRT,
and sysroot. The pip `nvidia-cuda-nvcc==13.0.88` package does not supply a
host compiler or declare CCCL; do not treat it alone as a proven build kit.
The implemented helper resolves compiler/header/library paths from that
managed environment and preserves the selected Torch/CUDA ABI. The fresh
managed-build receipt below establishes this path on the current Linux host.
Sources: [NVIDIA CUDA Linux installation](https://docs.nvidia.com/cuda/archive/13.0.3/cuda-installation-guide-linux/index.html),
[NVIDIA Conda compiler](https://anaconda.org/nvidia/cuda-nvcc),
[PyPI compiler metadata](https://pypi.org/pypi/nvidia-cuda-nvcc/13.0.88/json).

### Managed LightX launcher integration — 2026-09-07 UTC

Linux CUDA-13 Install, both Update branches, and standalone setup now use
`app/scripts/install_lightx2v_runtime.py`. Existing runtime markers do not
skip its check. Windows and older-driver compatibility routes retain their
existing choices. Standalone setup passes the selected interpreter as an
argument vector and confines installer scripts to the app's scripts directory.

The helper fetches exact Git revisions, verifies patched source/CUTLASS tree
digests, provisions a project-local CUDA 13.0.88 / GCC 14 / CCCL 13.0.85
toolchain, and builds all nine translation units with the verified flags and
runtime-relative library search path. The real Conda layout places CCCL in
`targets/x86_64-linux/include/cccl`; discovery and its regression cover that
layout. Receipts bind installer recipe, source, runtime, and installed bytes.
Fresh-process ABI checks reject duplicate metadata, wrong payloads, and CUDA-12
library mappings. State/runtime locks serialize competing invocations;
catchable termination stops subprocess groups, and installation failures
restore package/receipt bytes while retaining all rollback attempts.

The HEAD-based candidate passed 107 applicable CPU/launcher tests with one
existing skip; the final header-layout correction also passed the complete
20-test helper suite. Independent integration/helper review is clean. The raw
worktree's broader launcher test still encounters the separately unadopted
three-line account-path forwarding WIP in `start.js`; the same full launcher
suite passes in the isolated candidate. That WIP was preserved unchanged.

A fresh validated lease ran the normal helper with the provisioned toolchain
(resolved GCC/G++ 14.4.0), installed its output in `app/env-rtx50`, and passed
all nine installed default-cuBLAS numerical cases. Outputs were finite with
relative MAE 0.0069444–0.0555556; peak allocated tensor memory was 42,283,520
bytes. Actual process maps matched the selected CUDA-13 libraries with no
CUDA-12 runtime/cuBLASLt mapping. Wheel SHA-256:
`5f84543721b83053634c6d7df46fbe28fc21edaabc2c4d1f457059e4243104d4`;
extension SHA-256:
`e4e2219255b6bef545168d402071b144cd8277d6721b14a078b207af94f1a86b`.
A second normal-helper invocation reused the verified package, created no new
attempt, and left the receipt unchanged. This is verified reuse, not a claim
that independent recompilations produce identical binaries.

Receipts: `.artifacts-temp/astra-lightx-managed-20260907/`; candidate and
source hashes: `.artifacts-temp/astra-lightx-launcher-20260907/`; managed build
and rollback: `app/.lightx2v-runtime/`. An earlier dispatch was refused before
work because too little lease time remained. A bounded task-private client
waiter then started the same guarded workload promptly on a fresh grant,
completed it, and confirmed withdrawal. All GPU children exited and the lease
is cancelled. The temporary waiter is stopped and retained only as provenance.

No service restart or native Pinokio UI invocation occurred. The helper's real
execution and launcher plan tests are distinct evidence. Full-model generation,
visual quality, performance, DoRA, and native Windows acceptance remain open.

### DoRA protocol candidate — 2026-09-07 UTC

Official MMGP 3.7.14 at
[`589ba050`](https://github.com/deepbeepmeep/mmgp/commit/589ba050d320c4df879f48d11b23d6a88e6c68c8)
still has the same unscaled DoRA base path, so upgrading alone does not close
the earlier numerical failure. An isolated copy of installed MMGP 3.7.12 now
contains a minimal proposed `_mm_effective_weight_input_scale` protocol: fold
the channel scale into the materialized base before ordinary LoRA merging,
leave ordinary adapter deltas unscaled, and keep native-forward dispatch when
DoRA has zero effective strength.

Four CPU tests cover ten numerical cases across bias states, ranks, ordering,
nonuniform scales, and packed-byte immutability. The baseline reproduces error
16.0; candidate DoRA reference error is zero, with maximum error across all
reference checks 3.814697265625e-06. CUDA remains uninitialized. Patch dry-run
and every source/artifact hash check pass. Files are in
`.artifacts-temp/astra-dora-protocol-20260907/`. Those receipts describe the
unadopted prototype. Retain them as provenance; the managed package integration
below supersedes that prototype.

### Managed MMGP DoRA integration — 2026-09-07 UTC

The normal requirements flow now builds `mmgp==3.7.12+maestro1` through the
small PEP 517 recipe in `app/dependencies/mmgp`. It verifies the exact upstream
wheel and RECORD, applies a hash-bound patch, preserves the upstream GPL-3.0
payload and dependencies, and emits truthful version/generator metadata,
provenance, and a new RECORD. The source tree is not vendored and no runtime
monkeypatch is installed. Package-local Git attributes preserve the patch's
required CRLF bytes and normalize recipe text to LF across checkouts.

The real NVFP4 qtype publishes its input scale for MMGP's materialized base;
ordinary LoRA deltas retain their original input coordinates. Zero-effective
DoRA retains native forwarding, malformed/nonfinite scales fail explicitly,
and simultaneous effective DoRA and LoKr fails instead of silently dropping
DoRA. The startup version gate matches the package. Install, full Update,
standalone requirements, and already-current Update consume the same recipe.

Both installed local runtimes match every final wheel payload byte. Wheel
SHA256: `9e55e53d7ad975df7ab7d18dde82e0720b333a80a315fc189073af1fdd61c4cf`;
installed `offload.py` SHA256:
`972451f19d3471bf96c47e241bffb1c6dca1a64e5fab3c75755fc5cd2fab5f15`.
Each runtime passes all 16 NVFP4 CPU tests with CUDA confirmed uninitialized;
the final isolated integration suite passes 117 tests with one skip. Package
builds and isolated installs work through UV and pip; independent package and
provenance review is clean. Original packages remain preserved for rollback.

The full HEAD-based backend candidate ran 4,703 tests: 15 failures, two errors,
and 19 skips. Sixteen reported cases reproduce on unchanged HEAD with original
MMGP: Blender semantic mapping, Director anchor cleanup, LLM routing, LTX
integration, branding/navigation, research-child cleanup, stable-share path
handling, and checkpoint provenance. The remaining one-second audio-lane
timing failure passes baseline and candidate replay; the separate test repair
below strengthens its synchronization and cleanup.
These remain explicit overall-goal work, not evidence against the focused
DoRA acceptance or a claim of a green full suite.

Receipts and the unchanged-HEAD failure replay are under
`.artifacts-temp/astra-mmgp-integration-20260907/`. A fresh coordinator grant
then covered eight synthetic installed legacy-layout GPU cases on RTX 5090 / Torch
2.10.0+cu130. Zero-strength DoRA retained native LightX quantization/GEMM with
zero reference error; nonzero DoRA plus ordinary LoRA matched the pinned BF16
rounding reference bit-for-bit, with ideal-reference relative MAE
0.0001430–0.0002503. All outputs were CUDA BF16 and finite; packed bytes were
unchanged. Actual maps showed the selected CUDA-13 cuBLAS, cuBLASLt, and runtime
libraries without CUDA-12 mappings. Peak allocated tensor memory was
42,388,480 bytes. The bounded child exited successfully, the waiter confirmed
withdrawal, and a separate read verified the exact cancelled ledger row and
unchanged bound source/package files.

This closes the small installed DoRA numerical/native-dispatch boundary. No
service restart, full-model generation, visual quality, performance, or Windows
acceptance is claimed. The stopped waiter and original packages remain as
provenance and rollback material.

### Audio-analysis native-lane test ordering — 2026-09-07 UTC

The CPU-only audio-lane test now waits for explicit native-wait, native-acquired,
analysis-entered, and analysis-finished signals. It proves the worker reaches
the held Classic lane before release, then verifies task completion and release
of generation, native, and audio-analysis gates. Failure cleanup releases only
the test-owned lane and settles or cancels the task within a bounded wait.
This removes the extra default-executor waiter and its one-second timing race.

The focused case and full 68-test remote-access module pass in the app runtime
with CUDA masked. Parent review confirms the source matches the worker's clean
preimage and inspected delta. This changes the test only and supplies no live
GPU or service evidence. The 4,703-test suite was not repeated for this isolated
test change; the 16 independently reproduced baseline failures/errors remain
overall-goal work.

### Baseline fixture and request-contract repairs — 2026-09-07 UTC

Five of the 16 reproduced baseline cases are repaired without changing
production behavior. The Director fresh-start test now verifies that the
caller-owned request remains unchanged while the owned copy and durable state
exclude the forged generated anchor. The Blender regression executes the
current control-video classification against video, image, audio, and missing
media metadata, retaining its semantic-prompt and Generate handoff assertions.

The real research-child shutdown fixture now uses the selected test interpreter
for both parent and descendant, and always stops its owned runtime if readiness
fails. It no longer depends on an ignored `app/env` directory. The task/capability
routing test keeps its in-memory routing/default checks and no longer reads or
asserts the owner's private `wgp_config.json`. The manual-checkpoint regression
loads the actual compatibility resolver with the downloader and still proves
that a missing local checkpoint without a URL causes no network call.

All 139 tests across the five full modules pass in a HEAD-based source copy
containing neither the ignored environment nor private runtime configuration.
Three further shell assertions now follow the existing composed mobile UI:
the shared sidebar identity supplies embedded provenance in both layouts, and
the mobile sidebar owns the machine-settings action. Both full shell modules
pass (16 tests); no UI behavior changed.

Evidence: `.artifacts-temp/astra-baseline-repair-20260907/`. Eight recorded
baseline cases are resolved by these test repairs. The seven LTX cases remain
separate work.

### Stable-share runtime-file identity — 2026-09-07 UTC

The reproduced destination replacement failure was an inode-reuse race. The
shared publisher now retains descriptors for both the existing destination and
the temporary file through validation and atomic replacement. Clear uses the
same retained destination identity. Both paths recheck owner, type, mode,
single-link status, and pathname identity; cleanup removes only a temporary
name still bound to the held single-link file. Substituted or hardlinked files
are preserved. Temporary-name failure occurs before an anchor is acquired.
The quick-tunnel supervisor delegates to these shared primitives; its duplicate
publication and clear machinery is removed.

All 50 stable-share tests pass in an isolated source copy under development
mode with warnings treated as errors. This includes real Linux temporary-file
replacement/link/cleanup checks and mocked Windows handle flags/ownership.
Independent review is clean for that scope. Windows uses non-inherited,
reparse-point-aware handles with delete sharing so anchors remain open through
replacement; the API contract follows Microsoft's
[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)
and [MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
documentation. Native Windows replacement/deletion with held handles remains
unverified and requires the full stable-share suite on that host.

No live share files or running service were changed or restarted. These checks
close the recorded Linux inode-reuse defect, not every possible filesystem race
or live service/Windows acceptance. Nine of the 16 recorded baseline cases are
were covered at this checkpoint; the seven LTX cases are recovered below.

### LTX-2.5 audio and decoder recovery — 2026-09-07 UTC

Standalone soundtracks recover their missing source selector consistently in
the API, native validation, and UI submission copy. Explicit source flags and
image/control-video modes are preserved; malformed HTTP recovery inputs fail
clearly, and malformed saved/internal selectors cannot become flags through
string conversion. Audio strength uses `audio_scale`, and Load Settings safely
restores its finite value. LTX-specific vocal isolation keeps the original song
for delivery.

Director's separate conditioning stem now survives attachment/recovery and
sidecar handling, passes the same project-media authorization as the soundtrack,
and stays out of public campaign settings. Only declared LTX-2.5 runtimes may
use it; unsupported families fail before model loading. Requested and finalized
residency evidence both include it. Audio-window decisions count samples across
channel-first and sample-first layouts. Director respects the handler's explicit
voice-reference mode.

Fast/NAD decoder selection now travels through model options, controls,
submission, settings restoration, and native model kwargs. A matching resident
decoder is reused; a changed decoder triggers reload. Cross-model restoration
hydrates the intended options while preserving a later manual model switch.
Defaults migration 10 discovers distilled `ltx2_25` once, preserves the selected
model and later hides, retains migration 9, and leaves Dev/NVFP4 optional.

The clean broad backend run executed 4,717 tests and found four stale contract
assertions plus a Python 3.10 `tomllib` test import. All five were repaired:
callback ordering remains compatible, version-specific image migration tests
retain their own version boundary, and Python 3.10 uses the installed `tomli`
parser. The final affected-module run passed 465 tests with one explicit skip
(466 total). Final clean UI tests passed 486/486; TypeScript/Vite build, Python
syntax, and JSON grammar checks passed. Independent review is clean. Broad-run
source hashes and final correction evidence are retained separately under
`.artifacts-temp/astra-ltx-recovery-20260907/`; the broad run is not relabeled
as a fresh full-suite pass on the corrected revision.

The seven LTX baseline cases now have verified repairs. This is CPU/static,
mock, and build evidence; no LTX model generation, live browser, GPU quality,
service restart, or native Windows acceptance occurred. Unrelated H3/API/UI WIP
remains unadopted. The continuous Goal and the remaining planned backlog stay
active.

## Native-max offload floor — CPU integration, 2026-09-07

Explicit native-max H3 requests (1344 on the long edge) now start at MMGP
profile 5. Smaller, missing, malformed, or auto resolution inputs retain the
existing 4.5 baseline; resolution-free preloads are reprofiled when an explicit
native-max generation requires 5. Resolution classification does not authorize
an otherwise unsupported model or canvas.

Queue stamping, requested residency identity, the generation wrapper and
implementation, model loading, loaded identity, and expected-profile queries
use the same standing floor. Different integer requests that resolve to the
same effective profile share a residency key; a loaded 4.5 configuration is
released/reloaded for an effective 5 request. Observed recovery still uses the
actually loaded profile, so historical 4.5 is not relabeled as an attempted 5.

The integer request helper is now explicitly separated from the floating
expected-runtime helper. Sealed v1 plans, manual/default request values, and
pre-v1 completed-prefix provenance remain unchanged. Expected runtime policy
is not observed evidence; observations retain the task-bound callback and
policy-revision gates above. The obsolete unused standing-floor helper and
incorrect draft retry assertions were removed with preimages preserved.

All 312 tests in the final ten-module integration suite pass, along with syntax
and diff checks. Independent review is clean and separately checked floor,
legacy-prefix, and observed-retry contracts. This is CPU/static and synthetic
verification, not evidence of GPU memory usage, performance, OOM avoidance, or
full-model quality. No GPU/model run, restart, browser, or Windows acceptance
occurred. Receipts and exact source hashes are under
`.artifacts-temp/astra-h3-native-floor-final-20260907/`.

The native-floor source integration is complete. Continue the separate
FlashVSR whole-file delivery WIP and remaining native-conditioning/prompt/UI
work. Live GPU acceptance still requires an exact coordinator grant; browser
acceptance retains its pending test-directory permission.

## Task-bound offload observations — CPU integration, 2026-09-07

H3 benchmark and allocation observations now require an ephemeral callback
capture from the actual generation attempt. The callback is injected only into
runtime arguments, survives nested one-output dispatch, and is removed from
output settings after overrides. It never enters persisted task parameters.
The existing wrapped function signature remains intact.

The collector accepts one actual execution and one matching loaded-profile
capture. Reset cannot erase cumulative attempts or ambiguity. Retries, repeated
outputs, batches, multiple windows, dynamic extra repeats/windows, missing
residency, and changed model/geometry/steps cannot become single-execution
calibration authority. Capture occurs only at finalized geometry; a load or
preprocessing failure before that boundary remains unobserved. Internally
aligned or reshaped jobs still execute, but are skipped by this request-based
benchmark accounting rather than labeled with guessed dimensions/frame counts.

Cold/resident state now follows the actual load/reuse/reprofile decision,
replacing the speculative pre-call resident check. Each worker owns its timing
dictionary explicitly, so a delayed worker cannot label a later task. The first
failed task's params and validated profile are snapshotted together after its
stream exits; final OOM accounting cannot borrow the last loop task's values.
Fractional 4.5 remains 4.5. Missing or mismatched captures skip observation.

Calibrated-recovery policy revision 2 excludes requested-profile-era evidence;
old ledger/cache files are not migrated or rewritten. Sealed v1 integer profile
requests and completed-prefix provenance remain unchanged. Fractional records
are retained accurately but still cannot alias an integer v1 recovery request.

All 346 tests in the final assembled benchmark, capture, observed-offload,
sealed-plan, WGP, audio, acceleration, and queue-recovery suites pass. Independent
review is clean, including early OOM, actual load state, aggregate work, and
timing isolation. This is CPU/static and synthetic execution evidence. No model
load, GPU work, generation, service restart, browser, or Windows acceptance ran.
Exact source hashes, preimages, prior failed fixtures, and final receipts are in
`.artifacts-temp/astra-h3-observed-capture-20260907/`; collector lineage is in
`.artifacts-temp/astra-h3-observation-collector-20260907/`.

The subsequent native-max checkpoint above completes floor propagation and
its requested-versus-effective query contract on this capture boundary. Preserve
unrelated FlashVSR, native-conditioning, prompt-adapter, and input-policy WIP.
Browser acceptance still awaits its separately requested test-directory gate.

## Offload-profile precision prerequisite — 2026-09-07

Benchmark construction, cache normalization, and allocation evidence now
preserve supported fractional MMGP profiles 3.5 and 4.5. Whole-number profiles
remain integers, retaining the existing canonical integer key/digest. Explicit
malformed, Boolean, nonfinite, or unsupported profiles are rejected without
echoing their values; an absent legacy optional field remains absent. No live
ledger or historical record was migrated or rewritten.

The legacy v1 recovery selector no longer accepts a fractional observation as
its truncated integer profile, and nonfinite conversion cannot escape as an
overflow. Valid integer recovery behavior is retained. The WGP wrapper now
updates either an existing positional argument or a keyword, so positional and
mixed calls no longer receive duplicate profile arguments. Executable tests
cover all three calling forms through an OOM retry, including the updated task
parameters. Independent review is clean. All 322 tests in the assembled
benchmark, caller/wrapper, observed-offload, sealed-plan, audio, acceleration,
and queue-recovery suites pass. Syntax and publication checks are separate
final gates recorded in the private manifest.

Native-max floor adoption remains open. Its source map distinguishes immutable
v1 integer profile requests from the floating profile actually loaded. At this
precision checkpoint, benchmark/allocation callers still derived their profile
from a pre-floor request helper. The subsequent task-bound observation change
above closes that provenance gap. Historical ledgers and completed-prefix
requested-profile provenance remain preserved rather than relabeled.

Then finish resolution propagation through queue identity, wrapper, load,
loaded identity/reprofile, and recovery. Observed 4.5 must remain 4.5 until a
real escalation runs. The parked native-floor candidate, exact incoming WIP,
source probes, and next actions are under
`.artifacts-temp/astra-h3-native-offload-20260907/`; serializer evidence is under
`.artifacts-temp/astra-h3-profile-serialization-20260907/`. Incoming native-floor
and FlashVSR WIP is preserved separately from this precision checkpoint. No
GPU, model loading, generation, service restart, or Windows acceptance occurred.

## H3 quantized projection input dtype — 2026-09-07

H3 entry projections now use a module's declared floating output dtype before
considering its weight dtype, and packed integer storage uses the explicit
floating fallback. Previously INT8 or W4A8 entry weights caused video, audio,
and text inputs to be cast to integer codes before reaching the kernel,
discarding fractional values. Ordinary floating-weight behavior is preserved.

The regression executes a tiny joint transformer with the real INT8 and W4A8
wrapper classes and synthetic CPU kernels. All three kernels receive the exact
fractional inputs, and outputs match the corresponding dense reference exactly.
The original source fails both wrapper cases and the declared-dtype check;
the corrected focused suite passes all nine tests. This is CPU numerical and
mock-kernel evidence, not actual INT8/W4A8 kernel or full-model acceptance.

The broad clean H3 run executed 183 tests with 13 skips and found one stale
adaptive Load Settings source assertion. It now checks restoration's explicit
boolean handling; the existing UI state regression executes both adaptive and
pinned restoration. The corrected Ref2VA module passes 17 tests with two skips
using its explicit app import path. Independent review is clean; syntax and
diff checks pass. The earlier
broad run remains separately recorded rather than being relabeled a full pass
on the corrected fixture.

The uncommitted post-projection raw-integer cast/dequantize fallback and its two
matching tests were removed, with exact preimages preserved. Casting raw codes
without their scale cannot reconstruct activations. Historical Char failures
occurred at different boundaries and do not establish that this source repair
fixes a current full-checkpoint run. Receipts and the retained draft are under
`.artifacts-temp/astra-h3-activation-20260907/`. No model load, GPU work, service
restart, or Windows acceptance occurred. Other native/offload/input-policy and
prompt-adapter WIP remains separate.

## Adaptive H3 controls — CPU integration checkpoint, 2026-09-07

Adaptive mode now exposes separate Text & frames and References checkpoint
choices and LoRA controls. Both checkpoint preferences survive refresh; working
LoRA settings retain the existing clean-slate refresh policy and restore through
Load Settings. Explicit model choices take precedence over the selected FL2VA
flavor and Base fallback. Explicit-output metadata never selects PinkCherry.
Unavailable or malformed saved model choices remain visible for repair, and
invalid model pairs cannot construct model-specific LoRA requests or submit.

Missing/null split LoRA lists inherit compatible shared selections; explicit
empty lists clear only that architecture. Full asset identity and positional or
phase weights survive editing, including colliding stable IDs and literal hash
characters in local filenames. Malformed saved LoRAs cannot prevent entering
adaptive mode or repairing a checkpoint choice. The repair controls preserve
raw malformed values until an explicit edit or clear; submission remains strict.
Inventory and delayed defaults responses are fenced against changed selections.

The assembled HEAD-based candidate passes all 514 UI tests, production
TypeScript/Vite build, E2E TypeScript, and scoped ESLint. The unchanged backend
adaptive contract passes 23 tests; 64 Python/UI request-list and compatibility
projection cases match. Independent review, including malformed-state repair,
is clean. These receipts cover CPU/static and mocked behavior.
The build retains its existing large-chunk warning. Private source manifests,
preimages, clean counterparts, and logs are under
`.artifacts-temp/astra-adaptive-ui-20260907/`.

Three sanitized image concepts were compared and the stacked-group layout was
implemented using native components. Fresh desktop/mobile rendering, keyboard
and accessibility acceptance, and the image-first fidelity ledger remain open.
The synthetic browser scenarios are prepared and typechecked but have not run:
the existing harness requires a different-filesystem cache/result directory,
and the exact create-only copy plan still awaits the owner's permission.
Do not infer that permission from GPU authorization, and do not claim the design
cycle or browser acceptance complete. No real backend, model, GPU, generation,
service restart, or Windows acceptance occurred in this slice.

The obsolete uncommitted metadata-to-PinkCherry Python helper and its matching
test were removed with preserved preimages. Other backend and UI WIP remains
separate, including blanket mixed-input/text-only restrictions and prompt
adaptation. Continue the open browser gate and remaining planned backlog; this
checkpoint does not complete the continuous Goal.

### Prompt-adapter audit — not adopted

While waiting, a direct CPU audit of the uncommitted `h3_prompt_adapt.py` found
two reproducible blockers to integration: an embedded `summary:` label inside
canonical dialogue causes the exact dialogue span to disappear during family
mapping, and freeform dialogue appears twice after Ref2VA wrapping (summary
plus detailed description). The recorded source hash and synthetic results are
in `prompt-adapter-audit.json` under the diagnostic artifact. Do not adopt that
adapter unchanged or treat its eight simple tests as preservation evidence.
Keep single-owner dialogue, literal authored text, and sealed execution digests
as the acceptance conditions when replacing its field parser/summary behavior.
The clip/model-count probe retained both clips and is not a failure finding.

A subsequent unadopted candidate protects dialogue with the planner's exact
placeholder mechanism and shared top-level parser. Fourteen CPU unit tests
pass, including embedded field names, multiline dialogue, placeholder-like
authored strings, and rejection of duplicate/mixed fields and unsupported
Ref2VA-to-Base text. Review exposed additional execution requirements: a
generated Ref2VA summary needs its reference-derived official task-type
prefix; summary-only input must compile to actual shot records rather than
`detailed_description: N/A`; any Base visual plus Ref-only field is mixed;
and unsupported summary/reference text needs exact shot ownership before
relocation. Noncanonical wrapper headers are not a valid preservation fix.

Candidate source/tests, original preimages, hashes, and open requirements are
in `.artifacts-temp/astra-prompt-preservation-20260907/`. The original untracked
workspace files were restored byte-for-byte; the candidate is not dispatched
or adopted. Continue through the canonical compiler and target-schema
postconditions, then review the existing event-restoration/resealing helpers
and callsites before integration. Passing parser unit tests alone is not
executable-prompt acceptance.

## Successor progress

The successor's native Goal is active and continuous. The original handoff
below remains provenance; these completed slices supersede its first failure
list and part of its account UI work:

- `945ab68`: all seven named recovery failures are resolved. Five were stale
  source probes; two exposed continuity text whose execution digests were not
  updated. Plan construction now seals the final bytes, and Director restores
  matching digests when normalizing carry. Projection leaves sealed source
  plans unchanged. Duplicate WGP probes were removed in favor of the existing
  implementation tests. The isolated candidate passed 322 tests across the
  complete recovery, planner, visual-continuity, WGP implementation, and
  Director H3 suites, plus syntax/publication checks and independent review.
- `55153e1`: required account sign-in precedes Welcome and has matching focus
  and visual priority above optional dialogs. Optional account/support behavior
  is preserved. The isolated candidate passed 472 UI tests, 68 remote-access
  contracts, TypeScript, production build, and independent review.
- `e3bcb62`: reference-pack quality checks are optional and default to Off in
  the UI. Retry/Edit honor the current checker choice in both directions;
  disabled review ignores stale reviewer fields. New Off results have an
  explicit unreviewed public state without failure reasons or pending-grade
  badges. Recovery binds that state to the exact Off metadata, retaining the
  existing single-candidate selection marker and historical metadata reads.
  The API's omitted-review default is preserved for compatibility. The final
  isolated candidate passed 215 backend tests, 473 UI tests, application/E2E
  TypeScript, production build, syntax/publication checks, and independent
  review. Private evidence is in `.artifacts-temp/astra-optional-review-20260906/`.

- `dd23be5`: the H3 resolution-report parser uses the existing declared
  `tomli` fallback under Python 3.10. Its 60-test suite passes on Python 3.10
  and 3.11, with the exact pinned-uv offline probe intentionally skipped once
  on each runtime. No dependency installation or resolution run was performed.
- `5173276`: model and projector download failures have bounded messages and
  suppressed private causal tracebacks. A registered vision model cannot
  silently become text-only; failed replacement preserves the incumbent
  runtime, retry clears loading state, and unregistered text fallback remains
  available without raw error logging. All 80 isolated LLM tests pass. The
  hardware-identity test uses explicit visibility stubs under the CPU mask.

The current Krea declaration update uses schema 3 and binds GET-displayed text
and revision into PUT. Valid historical v1/v2 declarations require explicit
owner reconfirmation; reads do not rewrite them. The separate host-term v2
notice contains clarification only, so it does not silently gain the new
deletion acknowledgment. UI consent resets on identity changes, and stale
responses cannot announce success. The isolated candidate passes 165 backend
tests, 474 UI tests, TypeScript, production build, and independent review.
This prepares a future owner-facing confirmation; no live account or policy
record was changed.

The hash-named slices are pushed. Their staged bytes matched isolated candidates;
unrelated changes remain unstaged. Private receipts and preimages are under
`.artifacts-temp/astra-recovery-contracts-20260906/` and
`.artifacts-temp/astra-required-signin-20260906/`. The generic visual-carry
helper's pre-existing digest mutation was superseded by the construction and
normalization fixes; its original preimage is retained privately.
Additional private receipts are under `.artifacts-temp/astra-toml-compat-20260906/`,
`.artifacts-temp/astra-llm-artifacts-20260906/`, and
`.artifacts-temp/astra-krea-declaration-20260906/`.

`0a9d6fc` retains thread-local model-release authority and passes
an explicit captured slot to Listener output callbacks. Ordinary repeats keep
that slot; an explicit hold or queue pause yields native exclusion before the
generation lock, and a cancelled resume does not reacquire it. The proposed
process-global ownership state is superseded because it could let an unrelated
thread bypass model-release exclusion. Its private preimage is retained.
The isolated candidate passes 166 LLM/lifecycle tests and all 52 lifecycle wiring
tests, including synthetic cross-thread exclusion and execution of both parking
branches. A per-slot guard fences duplicate release and post-context
reacquisition; the worker-thread round trip and persistence-failure paths are
covered. Independent review, syntax, and publication checks pass. This is CPU
synchronization evidence, not live model residency or GPU acceptance. Receipts are in `.artifacts-temp/astra-native-slot-20260906/`.
H3 segment checkpoint and native-conditioning WIP remain separate and must
be integrated with their own dependencies and acceptance.

The delivery-native slice keeps intermediate media in artifact lineage while
excluding it from public finals until the protected delivery pass. Metadata
refresh preserves its temporary role, and resealing follows the current
project-scoped sidecar format without requiring an obsolete browser-session
owner field. Nested orphan-recovery owner checks remain intact. Executable
callback/metadata tests replace superseded source-string probes; the reseal
fixture uses the real policy stamp and rejects invalid project/job/private/role
state, forged producer units, and changed media. All three new regressions fail
against the prior source. The broader recovery fixture now supplies the captured
slot state used by the already-shipped native-slot fix. All 293 applicable
delivery, wiring, output-privacy, and queue-recovery tests pass, along with
syntax/publication checks and independent review. Private evidence is in
`.artifacts-temp/astra-delivery-native-20260906/`. This remains synthetic CPU
and temporary-filesystem evidence, not live delivery or generation acceptance.

Checkpoint error attribution now distinguishes H3 audio/video decode, audio
mux checkpoints, and rendered-segment sealing. Model-checkpoint loading and
sealed-reference planning remain generic generation phases. Missing predecessor
units or artifact hashes carry an explicit segment-checkpoint stage/code through
sanitization, independent of a stale decode message. This slice does not change
completed-unit validation or callback timing. All 246 applicable wiring,
delivery, and queue-recovery tests pass, as do syntax/publication checks.
Independent review found no issues; private candidate/test evidence is in
`.artifacts-temp/astra-checkpoint-errors-20260906/`.

The checkpoint-ordering slice seals every completed video component before
concat, including single-component/deferred-concat hooks, and binds the callback
to its current segment/group. It removes the ambiguous reversed-file fallback.
Missing producer evidence fails before another task can run. Normal completion
and replay share the CPU handoff helper; replay reconstructs a handoff only when
the descriptor is absent and the next same-group task explicitly requires one.
Present null, malformed, missing-file, hash-changed, or invalid native-boundary
descriptors remain rejected by both journal and orphan-sidecar reconciliation.

Continuation enrichment binds the current unit ID, dependencies, settings,
private staging location, size, and hash. It fsyncs the file and (on Linux) its
directory, then atomically updates only existing producer metadata before the
journal. An update failure does not quarantine the sealed video. Reconciliation
defers quarantine long enough to adopt a valid same-media sidecar update over
an old journal descriptor; replaced media cannot be adopted under that old
identity, and all producer sidecars must agree on the continuation. Private
handoffs are retained rather than treated as consumed-file exceptions.

`H3-RECOVERY-ALTERNATE-SIDECAR` is resolved for the supported Linux path.
Reconciliation protects every accepted media and sidecar name, then retires only
an alternate header matching the old size/hash and job/producer/output identity.
The metadata-only primitive rechecks content and file identity through directory
handles under a process-local guard before moving it into private quarantine.
Changed, malformed, linked, protected, or unsupported-platform metadata remains
in place. Its snapshot/move guarantee relies on the repository's existing
writer-coordination contract. The isolated candidate passes 294 tests, with one
Windows-only test skipped; syntax/publication checks and independent review pass.
Evidence is in `.artifacts-temp/astra-alternate-sidecar-20260906/` and includes
real temporary-filesystem moves plus replacement-at-primitive-entry tests.

The isolated candidate passes all 464 applicable lifecycle, queue recovery,
Studio, native-boundary, audio-safety, delivery, and LLM tests. Its six code/test
files stayed byte-identical throughout the final run. Syntax and publication
checks pass; independent-review blockers were addressed with regression coverage.
Private preimages, isolated candidates, regression output, and closure receipts
are in `.artifacts-temp/astra-segment-checkpoint-20260906/`. Tests include CPU
handoff construction from synthetic frames, old-journal/updated-sidecar recovery,
metadata-write failure preservation, replaced-media rejection, strict malformed
continuation cases, and callback/replay ordering. This does not establish live
GPU, encoded native-AV handoff, generation, or human acceptance. Native
conditioning, offload/quality changes, and the remaining WIP are still open.

The CPU LoRA contract slice now uses one per-checkpoint resolver for admission,
direct/restored workers, asset preparation, task manifests, and estimates.
Missing/null architecture lists inherit shared choices; an explicit empty list
clears that side. Paths and positional weights retain their selected identity;
only compatibility classification normalizes basenames across path separators.
Empty/duplicate assets, incompatible explicit lists, exclusive stacks, invalid
Dasiwa step/strength settings, and surplus weights reject before model work.
Ordinary missing weights retain the existing 1.00 default.

Estimate routing uses the existing adaptive resolver on a private presence-only
request. Segment Turbo validation and profile previews preserve architecture
selection precedence. Single-task aliases resolve before mutation, and manifest
projection runs before parsing and recovery-sidecar snapshots. The existing
Dasiwa runtime artifact/receipt checks remain intact. Superseded clip-only
projection and duplicate validation code have been removed. This slice does not
adopt checkpoint-default, prompt-adaptation, native-boundary, or offload WIP.

The final frozen candidate passes 359 of 368 tests; nine optional runtime tests
are skipped. Its seven code/test files stayed byte-identical through the run.
Python syntax and tracked publication checks pass. Private
preimages, review fixes, CPU logs, and candidate digests are under
`.artifacts-temp/astra-h3-lora-contract-20260906/`. Independent static review's
four findings were resolved and have executable AST/spy regressions. This is
CPU/synthetic evidence, not model, GPU, provider, rendered-UI, or human acceptance.

The stale fixtures exposed by the broader run are corrected: CPU-text and
credit admission fixtures now supply captured native-slot state; residency
assertions inspect `_generate_video_impl` and verify that the retry wrapper
delegates there; UI source assertions track held-job responses, normalized
resolution, and the dynamic inference-step ceiling while retaining its 50-step
cap. All 195 tests in the four affected suites pass on an isolated HEAD-based
candidate. The four test files stayed byte-identical during verification;
production source and unrelated WIP were not changed. Evidence is under
`.artifacts-temp/astra-stale-fixtures-20260906/`. This closes those recorded
fixture failures, not the entire historical backend inventory.

Public failure copy now has a shared reviewed-message publication boundary.
Exact approved contract messages and the bounded numeric frame-limit template
survive live production and restored envelopes. Arbitrary prefixed text, unknown
fallbacks, string subclasses, and malformed values fall back to content-free
stage/planning copy. Stage messages have one immutable owner in
`services/public_failure_copy.py`; the prefix-only rules and duplicate stage
mapping are removed. Exact TypeError preparation failures preserve actionable
parameter/custom-settings/LoRA copy. OOM detection and stage/code classification
are unchanged.

All 401 tests in the affected planning, delivery, lifecycle, recovery, and LLM
suites pass on the isolated candidate. A subsequent contract-comment correction
was proved AST-equivalent outside that docstring and passed all 12 final copy
boundary tests. Independent review findings were resolved, and final candidate
bytes were checked against the staged index. Evidence is under
`.artifacts-temp/astra-public-failure-copy-20260906/`. These checks are
CPU/static/synthetic; no GPU lease or workload was started.

The subsequent native-max checkpoint above carries the resolution-aware policy
through requested identity, loading, generation, and retry. Its source evidence
remains separate from GPU memory/performance acceptance. Other UI, routing,
prompt, native-boundary, and delivery WIP remains independently scoped.

Adaptive estimate requests now carry explicit FL2VA/Ref2VA selections and
independent LoRA lists/weights. Missing or null lists retain the shared-list
fallback; an explicit empty list remains a clear selection. Compatibility
queries for another FL2VA flavor use that queried model rather than a stale
picker. The shared request type owns these fields for estimates and generation.
The server honors explicit FL2VA selection, then preserves the selected flavor,
then falls back to Base; output metadata never selects PinkCherry. Invalid
adaptive IDs fail validation, and remote visibility runs before model/settings
validation so hidden and unknown IDs share the same response. Effective-model
access checks remain in place after routing.

The final isolated candidate passes 136 backend tests and 478 UI tests, Python
syntax, TypeScript, and the production build. Independent review is clean. One
stale submission-source assertion now matches the held-job argument while
preserving its ordering checks. Receipts and exact source hashes are under
`.artifacts-temp/astra-adaptive-request-20260907/`. This is CPU, synthetic
endpoint, and build evidence; no browser/server/GPU acceptance is claimed.

Remaining adaptive UI work includes picker integration, persistence/seeding,
TypeScript basename normalization against the Python contract, and rendered
acceptance. Do not adopt the dirty helpers that make `explicit_output` choose
PinkCherry: that behavior is superseded by the existing metadata-only contract.
The broad picker/helper/default WIP remains uncommitted and separate.

Browser acceptance is still deferred pending permission for task-specific
external cache/result directories on a different filesystem. Existing browser
binaries are available, so no download is needed for the prepared attempt.
Keep the cross-filesystem and synthetic-network gates intact. This is not
authorization to start Maestro or perform live account, LAN, GPU, model, or
provider work. All original task boundaries and the historical tracker hold
remain in force.

Next, inspect the remaining account/support, project-access, H3/native
conditioning, lifecycle, and LLM/runtime WIP against its paired tests. The
historical full-backend failure inventory below is a triage reference, not a
current failure count: the completed bounded suites above supersede their
covered entries. Keep storage-janitor and other untracked source/test pairs
intact. Do not stage mixed files wholesale or claim that the entire backend,
live deployment, or broader Goal is complete.

## Authority and Goal

The owner explicitly requested a new task using GPT-6 Astra at medium to
enable task features, with a native Goal. The predecessor is paused by the
owner and must not be resumed, woken, or treated as a parallel project writer.
This is an authorized successor, not completion of the broader objective.

Create the successor's native Goal first, without an invented token budget:

> Complete all actionable work in ASTRA_CONTINUATION_HANDOFF_2026-09-06.md,
> beginning with current-state verification, recovery test failures, and
> account/project usability; then continue integrating the already planned
> Maestro features, testing and finishing existing WIP, removing superseded
> machinery, and committing/pushing coherent verified milestones. Keep making
> useful authorized progress beyond this handoff until the planned backlog is
> complete or the owner stops or redirects the work. Preserve unrelated work
> and all authorization, privacy, ownership, and evidence boundaries.

This workflow is explicitly continuous. A milestone, empty/unavailable Beads
ready result, or one blocked acceptance lane is not broad completion. Use the
explicit next actions here and in versioned project plans; retain unresolved
gates and keep independent safe work moving. Do not mark the Goal complete
merely because the handoff has been read or the first slice shipped.

Use the saved local Maestro project directly, never a worktree or the former
checkout. Resolve its root from the task environment. Preserve Astra medium.
The phrase "enable features" refers to this new-task setup; it does not
override the existing prohibition on GPU/model execution, generation, lease
requests, Maestro/Pinokio restarts, credits/SSO activation, or new providers.
Those remain deferred until the owner explicitly changes the boundary.

## Read first and inspect current state

1. Read `AGENTS.md`, `CONTRIBUTING.md`, this file,
   `MIGRATED_CHECKOUT_HANDOFF_2026-09-05.md`, `CONTINUATION.md`, and
   `GPU_ACCEPTANCE.md`. Newer user constraints above override historical
   activation/restart instructions in older handoffs.
2. Run the shared read-only activation audit and inventory Git and reservations.
   The prior audit reports Beads 1.2.1 embedded Dolt, while repo policy holds
   the historical tracker. Preserve that hold: no Beads lifecycle, migration,
   init, or sync on inference alone. Use the approved Coordination fallback
   for deferred issue tracking.
3. The source baseline at preparation is `164d834` on `main`. The index is
   empty; the working tree remains heavily dirty. Fresh source and status are
   authoritative. Private preimages/status/digests are retained under
   `.artifacts-temp/astra-handoff-20260906/`; never commit these artifacts.
4. Acquire exact reservations before edits using the installed shared tool.
   Bind both task-id and session-id to the native task ID, not a descriptive
   session label. All predecessor implementation claims were released; its
   handoff-only claim will be released before successor creation.

## Shipped and verified

- `aea3e7d`: account-scoped terminal memory stays private until fresh project
  authorization; handles revoked membership, expired unlock, no-selection,
  account/workspace races, and existing unscoped live tool placeholders.
- `b4e4fbd`: output-privacy test extracts only its 15 asserted routes; all 50
  tests passed in 8.6 seconds without changing assertion semantics.
- `9fae1e6`: polling assertion includes queue readiness; 15 tests pass.
- `164d834`: failed-generation retry projection, endpoint, and UI; unknown
  reasons stay opaque, attempts and safe worker are checked, current owner,
  project.generate, sealed-input, and durable-checkpoint gates remain intact.

Latest slice: 475 working UI tests, 471 isolated candidate UI tests, application
and config TypeScript, isolated Vite build, 21 queue UI contract tests, and
publication guard passed. The staged files matched the candidate byte-for-byte.
No live account, mounted browser, LAN, GPU, generation, or human acceptance was
claimed. See `.artifacts-temp/failed-retry-current/` for exact receipts.

## Next work, in order

1. Resolve the seven remaining queue-recovery test failures/errors in bounded
   slices. The isolated 159-test file had three failures and four errors; all
   seven also reproduce against HEAD without the retry patch. Exact tests:
   `test_all_recovery_preprocessing_audio_uses_private_unit_prefix`,
   `test_recovered_v2_h3_worker_preserves_replan_contract_to_parser`,
   `test_sample_arms_are_excluded_from_generic_public_job_projections`,
   `test_semantic_execution_slices_dispatch_exact_children`,
   `test_enhance_before_generate_admits_then_prepares_without_gpu_slot`,
   `test_native_recovery_uses_private_stable_target_before_promotion`, and
   `test_wgp_completed_repeat_offset_skips_only_outer_dispatch`.
   Distinguish stale source extraction from real behavioral regressions;
   replace obsolete assertions with meaningful executable coverage, not weaker
   expectations. Candidate/baseline logs are in `failed-retry-current`.
2. Finish the existing account/project UI WIP coherently: App bootstrap,
   account/support panels, access projection, project-reference UI, and paired
   tests. Scout the actual diffs before deciding a slice. Restore synthetic
   browser acceptance through compliant cache/result paths; do not weaken the
   E2E runner's cross-filesystem requirement. Its default cache now shares the
   migrated checkout filesystem and is rejected. No real server start is
   authorized as a shortcut.
3. Reconcile the remaining CPU backend failures and their matching WIP source:
   Director H3/schema/semantic execution, native conditioning, lifecycle,
   LTX/runtime/launcher assumptions, project references, and storage janitor.
   Preserve all unpublished source and restored tests, including untracked
   `h3_lora_compat.py`, `h3_prompt_adapt.py`, `storage_janitor.py`, and their
   tests. Do not bulk-stage or publish tests against uncommitted dependencies.
4. Continue existing planned feature integration from `CONTINUATION.md`,
   project plans, and the approved intake decisions. Prefer user-visible,
   complete CPU-safe milestones; research intake is not a build list. Keep
   deferred GPU/activation/provider work out of the critical path. Obtain new
   authority only where it is actually required, with concrete prepared work.

The earlier complete CPU backend run was not green: 4,583 tests, 35 failures,
48 errors, 14 skips. Its private `terminal-recovery-current/backend.log` is
about 8.7 MB and contains enormous assertion lines: inspect sizes, then stream
projected failure headers/short excerpts, never broad regex output. One import
error is Python 3.10 missing tomllib. Reuse unchanged evidence; run focused
checks during development and full applicable checks for each final slice.
Do not repeatedly rerun the entire long suite just to rediscover the baseline.

## Shared tooling and closure

Universal Harness task `01a007ff-393c-71d1-8156-9992c3679753` repaired the
reservation scanner. Installed Linux helper SHA-256 at acceptance:
`421770b03125295ce9f5c1bd8ae48cc6e92e44f9196d5939b4df64c29c6bf4cd`.
Recheck current tooling rather than restoring older bytes. Supported full
read-only status uses `--include-pruned --max-directories 65536
--max-entries 1048576 --max-seconds 60`; reconciliation accepts the same budget
flags without include-pruned. Exact app-root scanning returned complete,
zero skipped scopes, followed by successful reconcile/acquire/release. Do not
manually edit registries, bypass unknown links, or create local scanner shims.
Windows acceptance is the shared owner's separate gate.

Use minimal useful parallel scouting/review, one writer per file, and serial
Git operations. Preserve the dirty tree; no reset, clean, implicit stash,
worktree, or broad staging. Verify isolated staged bytes when mixed files carry
unrelated hunks. Commit/push each coherent slice and report evidence honestly.
Rebase pulls are blocked by unrelated dirt; previously a fresh fetch proved no
incoming commits, permitting ordinary fast-forward pushes without disturbing
that dirt. If incoming changes now exist, reconcile safely rather than force.

The predecessor performs no further feature work after writing this handoff.
The successor owns the new Goal and continuation, not the paused predecessor's
native state. Retain the unfinished broad objective until genuinely satisfied.

### FlashVSR delivery publication checkpoint (2026-09-07)

Whole-file delivery now checks the segment writer's success result, coded pixel
canvas and frame count before transfer; muxing must preserve the upscaled
canvas. Private unique scratch files are registered for cleanup before encoding.
In-place publication uses atomic replacement and preserves original bytes on
encoding, muxing, cancellation, or replacement failure. Exact fit uses coded
pixels and square sample aspect ratio; display metadata cannot authorize
interpolation enlargement in place of learned upscaling. API deferral uses the
bridge classifier, including supported mixed-case values and the empty-mode path.

The uncommitted blanket WGP H3 deferral is removed: Classic, CLI and headless
callers retain inline upscaling because they do not enter Continuum's whole-file
delivery layer. The unlink-before-rename workaround, ineffective post-rename
byte ratio, duplicate release, raw diagnostics, and associated draft assertions
are superseded; exact preimages remain in the private evidence directory.
A stale Studio predicted-residency assertion now follows the already-shipped
observed-load-state contract.

The clean candidate passes all 409 tests across delivery files, H3 memory
lifecycle, job wiring, delivery OOM recovery, queue recovery, Studio and H3
profiles. Evidence includes real CPU FFmpeg coded-canvas/SAR/audio-presence
checks and owned-child cancellation/timeout reaping. Independent source review
has no blocking publication findings. Syntax and patch checks pass. Evidence:
`.artifacts-temp/astra-flashvsr-delivery-20260907/`.

Next delivery action: extend the shared imageio segment writer and audio mux
with optional abort/deadline controls, preserving codec and audio metadata
behavior and generic callers. Those calls remain non-cooperative; cancellation
acceptance here covers only concat and exact-fit encoder processes. Require
real-child stop/reap and partial-output cleanup regressions before closing that
gap. GPU/model, live service, browser and native Windows acceptance remain
unproven. The owner has authorized GPU work only under fresh coordinator grants;
this checkpoint requested no lease and performed no GPU or model work. Preserve
the historical tracker hold and all unrelated WIP. The continuous Goal remains
active with this and the existing planned backlog outstanding.

### Shared delivery encoder cancellation (2026-09-07)

The prior segment-writer/audio-mux cancellation gap is now addressed through
optional shared abort/deadline controls. The owned encoder controller monitors
blocked pipe writes and finalization, terminates then kills/reaps only its child,
and joins its monitor. Controlled muxing bounds the duration probe and mux under
one deadline. Delivery and Tools Upscale pass those controls; concat/exact-fit
reuse the same controller. Calls without controls retain the legacy imageio
path and existing empty-audio video-copy behavior.

Controlled encoding preserves imageio's effective omitted-quality behavior,
explicit codec options, channel conversion, macroblock sizing and frame-rate
formatting. Private staging and atomic publication preserve existing destination
bytes on failure or cancellation. Cleanup retries transient permission locks,
reports residual private temporaries without paths, and preserves the primary
error. Tools removes its owned final if cancellation arrives after muxing but
before output recording; the source remains untouched.

Evidence includes the 439-test affected integration run and 81-test final
closure, actual CPU decoded-frame parity and audio metadata checks, blocked-pipe
and child-reaping tests, and mocked Windows process flags. Windows execution,
live GPU/model acceptance and browser acceptance remain separate. Evidence and
exact source hashes: `.artifacts-temp/astra-delivery-cancellation-20260907/`.
The reviewer was interrupted by a quota event; closure resumed after a native
usage read confirmed capacity. No reset credit, GPU, model, service restart,
Beads mutation or external provider was used. Preserve unrelated WIP and continue
the existing prompt/native-conditioning, compatibility and browser backlog.

### Canonical dialogue preservation prerequisite (2026-09-07)

Director physical-record normalization now preserves authored whitespace inside
recognized `<d>[language]...</d>` blocks instead of collapsing spaces and tabs.
Repeated occurrences retain their order and exact bytes. Multiline dialogue is
rejected before per-line normalization because this physical-record schema
cannot retain its line breaks; no silent flattening is accepted. This is a
structural representability check, not a content filter. The clean candidate
passes 175 applicable tests, including a baseline-failing literal regression,
full target-record validation, Director invariants/preflight, shared shot
planning and visual continuity. Evidence:
`.artifacts-temp/astra-canonical-literals-20260907/`.

The adapter remains unadopted. Reuse `h3_shot_planner` for semantic compilation,
physical clip geometry, event ownership and seals; no Director compiler
extraction is needed. Use `h3_sequence_planner.compile_h3_reference_sequence_prompts`
with validated reference manifests for reference-derived task prefixes. Simple
reference counts do not establish those roles. Direct freeform output from
`compile_h3_official_prompt` still fails strict physical-record validation;
field wrapping alone cannot satisfy executable-prompt acceptance. Continue
adapter integration with clip frames/FPS/source IDs, exact dialogue ownership,
and final target-schema/seal checks. No GPU/model/runtime acceptance occurred.

### Dialogue-aware field extraction (2026-09-07)

The shared Context-IR field extractor now excludes apparent field headings
inside balanced dialogue spans while retaining legacy inline headings outside
those spans. Both official and physical validators accept original Base/Ref2VA
prompt bytes containing labels such as `summary:` in authored speech. Nested,
unbalanced and noncanonical dialogue still fails validation. The clean candidate
passes 178 applicable tests; independent review passes all seven focused cases.
The baseline parser truncates the synthetic embedded-label literal. Evidence:
`.artifacts-temp/astra-adapter-contract-20260907/evidence/parser-*`.

The unadopted resealing draft also failed two CPU audits: it could reseal a plan
whose previous seal already disagreed, and a malformed segment index left input
partly mutated after failure. An unadopted copy/validate/reseal transaction
candidate passes five tests; it requires complete prompt/shot counts, prior seal
validation, source index ownership and preservation of existing event payloads.
Do not use append-only event restoration to repair dispatch identity. Candidate
integration, stronger nested ownership checks, mapper target-schema validation,
and launch/Director geometry/reference-role wiring remain open. No GPU or model
execution occurred; the continuous Goal remains active.

The new pure mapper prototype is retained under
`.artifacts-temp/astra-adapter-contract-20260907/candidate/`; its ten CPU tests
pass independently in the parent. It requires explicit duration, a concrete
no-file-read reference manifest and a Base canonicalizer callback, and calls
both actual production validators. It is not adopted. An additional ordinary
speech-free input fails: the physical compiler emits canonical
`dialogue_and_vocalizations: none`, while the full vocal validator requires a
separate explicit silence phrase. Resolve that shared contract mismatch with
speech-free target tests before integration. Summary-only Ref input and
Ref-to-Base also remain intentionally unsupported pending exact field ownership;
those representability errors are open work, not the intended final product.
Pair the mapper with the copy/validate/reseal transaction and real caller
geometry/reference-role inputs only after those remaining gates pass.

### Canonical no-dialogue validation (2026-09-07)

The full vocal validator now recognizes the existing physical compiler's
`dialogue_and_vocalizations: none` contract when every record uses that exact
value and the complete physical Context-IR validates. Native I2VA/FL2VA/L2VA
alignment headers remain supported; the full prompt validator still enforces
the caller's selected mode. Incidental prose, malformed/mixed records and
nonempty vocal fields do not acquire silence status. Explicit expected dialogue
still requires its exact tagged lines; legacy dialogue and mapped-audio paths
remain unchanged. No subject-matter inference or added prompt prose is used.

All 235 applicable CPU tests pass. The ordinary speech-free mapper probe now
passes both Base and Ref2VA direct validators with unchanged authored action,
closing the silence-contract mismatch recorded above. Evidence:
`.artifacts-temp/astra-canonical-silence-20260907/`. The pure mapper and
copy/validate/reseal transaction remain unadopted: exact ownership for Ref-only
fields, complete nested event/dialogue checks, and real per-clip geometry and
runtime reference-role caller integration are still open. No GPU, model,
service restart, browser or native Windows acceptance is claimed.

### Shared execution-plan transaction (2026-09-07)

Dispatch validation now lives in `services.h3_execution_contract`; launch keeps
its existing wrapper, error type, legacy paths and returned-copy behavior.
The extracted validator's AST matches the predecessor exactly after renaming.
`rewrite_h3_execution_prompts` validates a copied sealed v2 plan before updating
anything. It reconstructs events from the semantic prompt with the existing
segment compiler, validates dialogue identity from the semantic source, preserves
per-segment event multiplicity and exact dialogue bytes/order, and rejects
mismatched nested metadata. Target callbacks must confirm success; the supplied
prompt list is snapshotted before callbacks. Only the copy's prompt mirrors and
executable digests change, then it is resealed and dispatch-validated again.

All 422 applicable tests pass. Independent review closed callback-list mutation
and forged/duplicated event findings through snapshotting and source reconstruction. Focused transaction
coverage includes stale seals, failed target checks, malformed owners, coherent
payload forgery, continuations, serialized multi-source plans, and intentional
repeated dialogue. Legacy dispatch fixtures remain intact.

Four CPU mapper/transaction probes pass with ordinary and spoken input across
Base and Ref2VA. Canonicalize source records before constructing their reviewed
shot plan; mapping freeform after sealing can break contiguous event-payload
identity even when individual words survive. The prototype mapper remains
unadopted, including unresolved Ref-only field ownership and real caller
reference-role/geometry wiring. Use the shared transaction instead of the earlier
unadopted ad hoc resealer. Evidence:
`.artifacts-temp/astra-execution-contract-20260907/`. No GPU/model/service or
browser acceptance is claimed, and the continuous Goal remains active.

### Literal media tags in canonical dialogue (2026-09-07)

The shared H3 media ordinal validator and audio remapper now distinguish actual
reference tags from literal text inside balanced canonical dialogue. They preserve
those dialogue bytes unchanged, including media-like wording; outside references
still require canonical tags and valid ordinal namespaces. Nested, unbalanced or
noncanonical dialogue does not exempt tags from validation. The native conditioner
continues to receive the unchanged prompt after its separate media prefixes.

The clean candidate ran 114 applicable CPU tests successfully with two skipped.
Independent review found no consequential issues and independently passed 28 audio
and 10 canonical-literal tests. The conditioner regression extracts its actual
Python method with a fake tokenizer: this proves string preservation and ordering,
not tokenizer semantics or GPU/model quality. Evidence:
`.artifacts-temp/astra-adaptive-integration-20260907/`.

The mapper prototype remains unadopted. Its remaining review findings include stale
same-schema reference-role binding, structural role delimiter handling, incomplete
callback source-conservation checks, and whitespace-only input. Runtime reference
integration must preserve active-slot ordering and positional paired soundtracks;
the prompt contract validator still needs paired-video audio accounting. Existing
Ref-only field ownership and geometry/caller work remain open. No content scanner
is authorized or needed for role prose. GPU work is owner-authorized only through
a fresh validated coordinator grant; none was requested or used for this repair.

### Paired soundtrack prompt accounting (2026-09-07)

Director's Ref2VA compiler now assigns an Audio ordinal to each included video
soundtrack indicated by audio_path or has_audio, emits its paired relationship,
partially_copy retention, and audio-reuse task type. Validation counts the same
namespace. A later standalone audio entry therefore follows paired audio rather
than reusing its label. Disabled or absent video audio does not consume an ordinal.
A label found only inside dialogue cannot satisfy reference mapping validation.

Paired reference audio does not activate the separate driving-audio branch, which
can remove unstructured authored vocal tags. The regression preserves the exact
authored line. The clean nine-module CPU gate passes 255 tests with two skipped.
Independent review confirmed the paired correction and driving-audio separation.
Evidence: `.artifacts-temp/astra-paired-audio-20260907/`. These are prompt-contract
checks: native K mode currently suppresses standalone A/B/C reference audio, so
the paired-plus-voice fixture does not establish mixed runtime support.

Standalone drive intent remains inconsistent between the planner's exact-target
prose and Director's numbered reference-audio contract. The reference_manifest
split/remap helpers have no current production callers; do not activate them as
a workaround. Resolve the actual target-audio versus reference-audio contract in
the caller integration. Mapper source preservation, manifest binding, Ref-only
field ownership, and real geometry/reference wiring remain open. No GPU/model,
restart, browser, mixed-K runtime or Windows acceptance is claimed.

### Shared physical-record canonicalizer (2026-09-07)

The existing Director compiler now lives in services.h3_canonical_prompt. Director
keeps its canonical-prompt wrapper and forwards prompt, duration, events, and mode
unchanged; unused local payload/time helpers and regex copies are removed. The
initial extraction is AST-identical after identifier renaming. Input guards
then reject text-free requests without events, malformed or blank event payloads,
and invalid, Boolean, overflowing, nonpositive or nonfinite durations. Explicit timed events remain a
valid source when prompt text is empty, including the Director window-prompts path.

The clean ten-module gate passes all 525 tests. Independent review closed the
empty-event-source and blank-event-payload findings. Final focused checks pass
after removing an unused import and obsolete test alias. A fresh subprocess compiles an
ordinary prompt without importing Director or torch. Wrapper output/error parity
and exact dialogue preservation are covered, and four synthetic Base/Ref2VA
mapper probes pass using the shared compiler. Evidence:
`.artifacts-temp/astra-canonical-service-20260907/`. The rejected blanket blank
guard and its reproduced multi-window regression are retained as provenance.

Use canonicalize_h3_prompt directly when replacing the prototype's arbitrary
canonicalizer callback. This extraction does not adopt the mapper or establish
its source-conservation guarantee: stale reference binding, structural role
encoding, Ref-only field ownership, source geometry and real reference/drive
routing remain open. No GPU/model, service, browser or Windows acceptance is
claimed. The historical tracker remains held and unrelated WIP is preserved.

### Mapper provenance candidate and obsolete helper removal (2026-09-07)

Removed the unused split_exact_drive_audio_reference and
apply_exact_drive_audio_prompt_contract functions, plus their regex/import, from
reference_manifest. Their former Studio routing path is gone; no production or
test caller remains. The active manifest validator is AST-unchanged. All 145
applicable CPU tests pass with two skipped; this does not resolve drive routing.

The refreshed mapper prototype directly calls the shared canonicalizer, rejects
blank input/overflow durations, and conserves non-field source prefixes. Its
provenance companion binds exact source/schema/origin, recipe, normalized manifest,
duration and output. It regenerates changed generated bindings from stored source
and requires current-manifest/duration validation. Bare Ref creation is rejected;
the separate authored constructor records explicit caller authority and must never
be used to relabel recovery or generated strings. Independent review closed the
prototype's origin and stale-binding findings. Final combined candidate tests pass
23 cases, including two parent closure checks for Base semantic-input rejection
and nested existing-plan seal drift. Evidence:
`.artifacts-temp/astra-mapper-binding-20260907/`.

These mapping files remain unadopted. Next: resolve structural role encoding and
Ref-only field ownership, define the runtime-owned allowlisted reference/asset
binding, enforce authoritative authoring ingress, and integrate structured records
into atomic prompt rewriting plus dispatch/recovery with current input checks.
Use a stable recipe version; unsupported old mappings retain exact sealed replay
and must not acquire fabricated remapping proof. The artifact's nested-seal test
proves storage coverage, not deployed integration. No GPU/model, service, browser
or Windows acceptance is claimed; the continuous Goal remains active.

### Structural reference-role serialization (2026-09-07)

Planner and Director now share reference_role_text from reference_manifest.
Ordinary/default roles stay readable and unchanged. Roles containing field/media
syntax, JSON quoting, line separators, controls or lone surrogates become lossless
JSON string literals with structural punctuation escaped. Formatting runs after
manifest normalization, so expansion is not truncated to the raw role limit.
Director preserves the role text for serialization while using a separately
trimmed copy for existing subject matching; compiler-owned Subject labels remain
structural. No phrase/content classifier or placeholder substitution was added.

The clean seven-module CPU gate passes 173 tests with two skipped; all 23 mapper/
provenance candidate tests also pass. Coverage includes helper JSON round trips,
UTF-8 output for surrogate input, long escaped roles, unchanged defaults, content
neutrality, preserved subject matching, and field/speech/media-tag isolation.
A synthetic full-mapper probe preserves the raw role in binding metadata while
validating the resulting prompt and media namespace. Evidence:
`.artifacts-temp/astra-literal-roles-20260907/`. Native model understanding/quality
of encoded roles is unverified; raw invalid-Unicode metadata persistence is also
a separate input/binding concern. No GPU/model/service/browser/Windows acceptance
is claimed.

The mapper still needs Ref-only field ownership and actual caller integration,
including runtime-owned allowlisted asset binding, authoritative authoring ingress,
per-segment sealed persistence, stable recipe versions and current-input checks
at dispatch/recovery. The structural role formatting prerequisite is implemented;
the mapper/provenance artifacts remain unadopted.

Next binding finding: h3_sequence_plan_signature already owns an allowlisted
reference projection, but omits has_audio. A CPU probe confirms that toggling
has_audio changes _reference_context while leaving the cache signature unchanged
when audio_path is absent (`next-binding-gap.json` in this slice's evidence).
Repair and reuse that owner rather than inventing a second projection. Separately
audit the inherited _UNREQUESTED_SPECTACLE_PATTERNS prompt/plan keyword checks in
h3_sequence_planner, h3_planner_helpers and h3_story_ledger against the project's
local-content-neutrality rule; they were encountered during this source pass and
are not covered by the role-formatting neutrality tests.

### Reference metadata cache binding (2026-09-07)

The sequence-plan signature now includes has_audio, so a changed video soundtrack
flag cannot reuse the old reference plan. Its existing ordered field projection
now lives in reference_manifest.reference_binding_projection; the caller uses
that shared owner rather than maintaining another list. The projection snapshots
metadata and preserves kind aliases while excluding unrelated UI keys. Admission
validation remains separate, and a path is not proof of immutable file contents.

All 155 applicable CPU tests pass with two skipped. Regressions prove soundtrack
presence changes both reference context and cache signature; roles, asset paths,
intents and order remain bound; irrelevant UI metadata is excluded; and JSON
round trips are stable. Evidence: `.artifacts-temp/astra-reference-binding-20260907/`.
Use this projection with validated active inputs for mapper integration, adding
separately proven asset identity when available. No GPU/model/service/browser or
Windows acceptance is claimed. The inherited keyword-check audit remains separate.

### Planner subject-keyword enforcement removed (2026-09-07)

Removed UNREQUESTED_SPECTACLE_PATTERNS, the ledger/segment keyword rejection
branches and error copy, and the helper alias/import. The unused direct-sequence
_schema and _plan_violations functions and orphaned minimax_h3_reference_sequence
guide are removed. The two actively loaded story guides now express fidelity
through immutable source events and global context, without topic-specific lists.
Source-event IDs/order, exact dialogue and materialization, beat ownership, shape,
shot count and contiguous timing checks remain unchanged. Validator AST comparison
confirms that only the keyword scans and their local string copies were removed.

All 211 clean-candidate CPU tests pass, including formerly listed effects and
adult/violent/controversial wording in otherwise-valid ledger/segment fields.
Independent review found no loss of active contracts and identified the two
unused predecessors removed before the final gate. Evidence:
`.artifacts-temp/astra-planner-neutrality-20260907/`. Source/guides and deleted
preimages remain available as provenance; no GPU/model/service acceptance is
claimed.

This closes the specific spectacle-word enforcement, not every neutrality or
literal-preservation question. Next audit the age/style fragment stripping in
h3_story_ledger._story_fragments and the automatic dialogue/camera
classifiers in h3_planner_helpers. Preserve explicit source syntax and owner
settings while removing subject/age heuristics; do not replace them with another
word list or silently invent a fixed creative policy. Also inspect whether
sanitize_h3_prompt_text changes exact authored dialogue when escaping template
or Context-IR syntax. Mapper/caller integration and the broader backlog remain
open; the continuous Goal remains active.

### Story dialogue literals and H3 template boundary (2026-09-07)

Recognized quoted speech now retains its exact payload through extraction, story
materialization and window rendering, including spaces, tabs, braces and field
labels. Generated and locked payloads cannot contain H3 dialogue delimiters: an
early structural check prevents one D-ID from becoming multiple speech blocks.
The dialogue renderer also validates speaker/language/delivery/action metadata
and language-label delimiters before wrapping a spoken block.
Planning prose still uses its separate syntax sanitizer.

process_template has a default-off preserve_h3_dialogue option. Strict balanced
canonical blocks are protected before line normalization, using an input-absent
private-use delimiter and one-pass restoration. Outside macros still expand;
malformed tags fail structurally, and concatenated macro values cannot forge
another literal token. Both WGP entrypoints opt in only after resolving the exact
Base/Ref2VA architecture. Other models and legacy timeline helpers are unchanged.
Reference-role serialization also escapes braces, so role data cannot become a
WGP template variable.

The clean six-module gate passes 248 CPU tests; the final role-brace and
metadata closure passes 85 focused tests, followed by nine literal tests after
moving language-label validation before truncation. Independent review closed the dialogue-delimiter
injection finding and verified the template/caller boundary. Coverage includes
quoted-source to mocked staging to compiled output, actual AST-extracted WGP
flags, ordinary macros, malformed tags, multiline bytes and token collisions.
Only one resolver fixture line is taken from test_llm_runtime; its pre-existing
root WIP remains excluded. Evidence: `.artifacts-temp/astra-story-literals-20260907/`.

Remaining literal work: recover_h3_plain_story still unwraps/sanitizes authored
Ref2VA dialogue, and raw quoted input before WGP enhancement is not canonical
<d> markup and is outside this opt-in. Preserve those distinctions rather than
claiming enhancer-model or Ref-recovery byte fidelity. Age/style stripping,
automatic classifiers, mapper/caller integration, and the broader backlog remain
open. No model/GPU/service/browser/Windows acceptance is claimed.

### Ownership correction: stop expanding the retired sequence path (2026-09-07)

A fresh repository-wide call search establishes that h3_sequence_planner's
sequence/manual builders, source resolver and compilers have no production
caller; h3_story_ledger planning and the window compiler are reached only by
that dormant chain or tests. The removed /api/v1/llm/plan-h3-sequence endpoint
is explicitly kept absent by test_h3_sequence_planner. The reference helper is
also used by the unadopted mapper artifact, which is not runtime adoption.
Earlier changes to this chain establish library/CPU correctness only. In
contrast, the shared canonicalizer, execution validator and WGP template calls
have active owners. Do not conflate those evidence levels.

A raw-source replay/hash candidate passed 154 CPU tests, but was not adopted
after this ownership finding. The two root edits were restored exactly to their
preimages; the patch, test result and status remain in
`.artifacts-temp/astra-ref-source-replay-20260907/`. Do not spend the next phase
finishing age/style or recovery patches in this orphaned chain. Retire/minimize
its superseded machinery after moving any actually needed helper into the
active adapter's owner; preserve historical inputs and replay fixtures.

The active integration owners are launch._plan_generation_submission,
launch._prepare_h3_long_studio_request and
director_pipeline._prepare_director_h3_longform. Their current uncommitted WIP
calls the flawed untracked h3_prompt_adapt; those calls are not in HEAD. Finish
that coherent feature with per-segment geometry, active reference inputs and
rewrite_h3_execution_prompts, replacing the append-only event restoration and
resealing draft. Canonicalize before ownership is sealed while retaining the
actual authored source; do not silently relabel canonicalized text as original
authoring or mutate an existing sealed source contract.

Generated Ref mappings with verified Base/freeform provenance can switch back
to Base by regenerating from their retained original source; do not parse away
their generated Ref fields. Extend the provenance recipe's target-switch path
with that invariant. Authored/legacy Ref text lacks that proof and must never
be treated as generated scaffolding. The active caller must preserve capability
and authoring boundaries while the remaining Ref-only ownership is resolved.

If a genuine future owner adopts Ref-to-story recovery, use a typed bundle that
separates nonspoken summary context from authoritative detailed dialogue. Keep
exact_block, language and occurrence D-IDs; None means derive plain quotes, while
an explicit empty catalogue means canonical no-speech. Detailed offsets cannot
be repurposed as summary event positions. This is a design finding, not deployed
recovery. The broader active backlog and browser acceptance remain open, and the
continuous Goal remains active.


### Active source compiler and replay prerequisite (2026-09-07)

The active shared shot planner now supports an explicit
source_canonicalization="t2va" recipe before ownership compilation. It retains
exact authored input separately, including edge whitespace, and seals a closed
recipe containing mode, version, duration, FPS and published frames. Sparse
ranges and point cues use the existing timeline compiler before canonicalization;
an untimed first-shot marker is lowered without losing its action or speech.
The source helper does not load a model or call Director.

Legacy replay input v1 and default planner output remain unchanged. New recipe
plans use closed replay input v2. Active recovery, duration editing, Director
provenance validation and transactional prompt rewriting use the same helpers.
Recovery retains the frozen recipe. An explicitly approved duration edit creates
a new recipe from approved geometry and the retained authored input; it does not
mutate the old plan or silently retime authored timestamps outside the new range.
The duplicated launch replay dictionaries were replaced by the shared projection.

The final clean-candidate gate passes all 544 CPU tests across planner, execution,
canonical literals, Director, Studio, queue recovery, generation planning,
duration and native-boundary contracts. The final nested-point/zero-FPS closure
passes two additional targeted checks; compileall and tracked-publication checks
pass. Independent review closed blanket Studio activation, zero-FPS validation,
and first-shot/point normalization findings. Four captured legacy plans and an
independent five-source plan/replay probe remain byte-equivalent. Evidence and
exact reviewed hashes: `.artifacts-temp/astra-active-source-20260907/`.

This is a prerequisite, not adaptive mapper adoption. The attempted unconditional
Base Studio switch was rejected and is preserved only in the evidence artifact.
Manual Base retains its existing freeform behavior: multiline speech, literal
separators and loose subject declarations must not acquire strict schema rules
merely because they enter Studio. Some of these sources cannot yet be represented
by the strict mapper without an explicit authoring policy; do not invent aliases,
strip literal material or weaken the subject validator to hide that gap.

Next integrate the recipe only at the actual adaptive schema-mapping owner,
together with per-segment reference/geometry binding, original-source provenance
and rewrite_h3_execution_prompts. Replace the unadopted append-and-reseal draft;
do not re-enable the rejected broad Studio switch or expand the retired sequence
chain. Generated Ref-to-Base target switching must rebuild from proven retained
Base/freeform source. The broader backlog, rendered-browser checks and live
acceptance remain open. No GPU/model/service/browser/Windows acceptance is added
by this CPU milestone; the continuous Goal remains active.


### Active reference handoff and soundtrack consistency (2026-09-07)

The active reference-input owners are minimax_h3_handler admission and
minimax_h3_main preparation, including their V/V+/V++ slot selection and K
paired-soundtrack behavior. reference_manifest.validate_reference_manifest is
currently reached by the dormant sequence chain, not generation admission.
Do not substitute that planning manifest or raw attachment counts for active
runtime inputs when integrating the adaptive mapper.

The legacy Ref2VA continuation is a silent 56-frame video, separate from native
AV boundary handling. Soundtrack-pairing mode now declines that silent video
and uses the existing still-reference fallback when capacity permits; it never
clears K or manufactures a missing soundtrack. K suppresses standalone ABC
inputs in the mixed-reference capacity count. A video handoff appends after
existing selected physical slots, preserving authored Video ordinals and never
enabling an inactive upload or filling a gap ahead of an existing reference.

Fresh and recovered temporal tails now share their slot writer. The selected
slot survives continuation metadata and durable descriptor projection; recovery
recomputes legal capacity and validates an explicit stored slot before mutation.
Historical descriptors without a slot derive the legal append position from
current inputs. Incompatible soundtrack/slot state fails closed rather than
silently dropping or renumbering references.

Removed the predecessor-audio cache override, its accepted runtime setting,
audio cache payload and unused alias, and superseded tests. Authored soundtracks
are encoded from their durable input. The video-latent cache and its decoded
video fallback remain. Historical audio-setting residue is removed when a tail
is bound; it no longer introduces an audio reference or replaces an authored
waveform on a warm instance.

The final clean-candidate gate runs 407 tests with no failures/errors and 13
explicit skips for unavailable CUDA runtime or managed Turbo assets. Source
hashes remain unchanged during the gate. Syntax and tracked-publication checks
pass. Independent review closed fresh/recovered slot divergence and the retained
audio override; parent closure verified the existing crash-recovery harness and
removed the obsolete alias/fixtures. Evidence:
`.artifacts-temp/astra-h3-reference-handoff-20260907/`. This is CPU/AST/synthetic
control-flow evidence, not live model, warm/cold generation, GPU, browser or
Windows acceptance. Unrelated root WIP remains unadopted.

The mapper still needs a two-stage reference binding: planning knows uploaded
inputs and intended predecessor dependencies, while _prepare_task_continuation
materializes the actual late reference. Bind to the active selected slot semantics,
paired-audio rules, verified asset identity and per-segment geometry; do not claim
a late asset is authored or allow mapping to rewrite sealed source/event ownership.
The original-source target-switch recipe and full caller integration remain open,
as do rendered-browser and live acceptance. Continue from these active owners;
the full continuous Goal remains active.


### Selected runtime references and multiple soundtracks (2026-09-07)

Admission, semantic video preprocessing, native model selection and continuation
capacity now share selected_h3_video_slots. Prepared videos retain their physical
slots until the native model presents them in compacted order. This fixes an
implicit third reference being moved into an inactive second slot and then
silently discarded. Capacity counts selected inputs rather than phantom selector
slots or inactive uploads; upstream required-file admission remains unchanged.

H3 K mode extracts one soundtrack for every selected reference video in the same
presentation order, using separate recovery destinations. Outputs are registered
before extraction and a failed attempt removes its completed and partial files,
including on cancellation. Extraction now runs inside the generation error
boundary. Missing or unreadable soundtracks fail with bounded actionable errors;
ordinary probe, destination, registration and decoder errors do not expose source
paths or stderr in that message. Cancellation still propagates. Semantic reference
audio bypasses generic timeline trimming, normalization, target-window slicing,
its continuation-audio fallback, and silent-audio synthesis,
so unequal soundtrack lengths cannot shorten the requested output. The non-H3
single-video extraction branch retains its exact prior control flow.

The unused selected-path snapshot was deferred with its tests; its pre-removal
source is retained in private evidence. It supplies no filesystem admission,
immutable asset identity, inferred reference role or content judgment. The separate
h3_prompt_mapping core and its composition probes remain unadopted under
`.artifacts-temp/astra-h3-mapping-runtime-20260907/`; no active mapper caller is
introduced by this runtime repair.

The final runtime gate runs 423 tests with no failures/errors and 13 explicit
CUDA/managed-asset skips, with stable source hashes. Syntax checks pass. A real
CPU ffprobe/ffmpeg probe extracts distinct 440/880 Hz synthetic soundtracks from
physical video slots one and three in the correct compacted order. Evidence is
retained under `.artifacts-temp/astra-h3-mapping-runtime-20260907/evidence/`.
Independent review closed the cleanup, error-boundary, output-duration and
window-slicing findings; the unused snapshot was deferred. These are
CPU/AST/synthetic and local codec checks, not model generation, GPU,
rendered-browser, Windows or human acceptance.

Next bind the original-source mapping recipe at the actual adaptive caller.
Preserve raw source paths before _parse_task_manifest materializes images, then
reconcile the late continuation reference and resolved soundtrack inputs before
transactional per-segment prompt rewriting. Planning metadata alone cannot prove
the selected asset or authorize resealing an existing plan. Generated Ref-to-Base
switching must rebuild retained original source; authored Ref ownership remains
separate. Browser and live acceptance remain open, and the continuous Goal stays
active. The historical tracker hold and service/provider boundaries are unchanged.


### Shared reference text and mapper adoption gates (2026-09-07)

Director now uses services.h3_reference_text.reference_relationships as the
single reference-text renderer. Explicit identity, scene, style, composition,
voice and drive intents retain Director's established subject binding. Inputs
that reach the renderer without image/audio intent remain neutral supplied
references and do not invoke subject matching or invent identity/voice ownership.
Roles remain escaped literal data. The old Director renderer body is replaced by
a small context adapter. The dormant manifest/sequence producers still insert
some defaults upstream; this milestone does not claim those producers changed.

The corrected mapper candidate delegates to this same renderer. Record validation
now requires the current target schema, including empty-reference target switches.
Direct Ref2VA passthrough is rejected outside explicit authoring/retained-lineage
record flow, and malformed type/intent/origin values produce bounded errors.
The mapper and its tests remain unadopted in the candidate until real callers and
recovery binding are connected; do not copy the older candidate over these fixes.

The combined candidate passes 399 CPU tests (370 active-path tests plus 29
mapper-candidate tests), with no skips/failures/errors and stable source hashes.
An independent 27-case explicit-intent comparison matches the preceding Director
output byte-for-byte. Independent closure review is clean; compileall passes.
Evidence, corrected candidate and the
bounded caller design are retained under
`.artifacts-temp/astra-adaptive-caller-20260907/`. These checks do not establish
GPU/model generation, browser, Windows or human acceptance.

The verified runtime integration point is the task loop before wgp.validate_task:
it reads task["prompt"] and expands templates. Update both task prompt and its
execution/sidecar projections in one validated transaction. Continuation has
already mutated the next task there, while h3_task_sidecar_params remains an
immutable preparse source snapshot and misses newly attached references.

Load/revalidate the existing request-manifest input descriptors using the job's
manifest pointer and owner/project evidence, then overlay only the proven late
continuation descriptor and predecessor dependency. Do not mint authority from
raw path hashes or treat selected K intent as proof of a soundtrack. Reuse the
selected-slot owner and bind actual source-video audio evidence in order.

Keep source plan separate from the copied whole execution plan. Use
rewrite_h3_execution_prompts before assigning any child output, preserving
source/event/dialogue ownership and geometry. Add the versioned mapping receipt
to segment recovery settings before calculating unit IDs, compare it during
completed-unit reuse, and leave legacy units without receipts unchanged. Replace
all unadopted h3_prompt_adapt calls, including the dispatch append-and-reseal call,
only when their actual replacement is complete. Preserve manual Base freeform
behavior; current strict mapping still rejects some ambiguous authored forms.
The full continuous Goal and caller integration remain active.

### Adaptive H3 task binding and replay (2026-09-07)

Studio and Director now retain the original sealed source and map each adaptive
child after input admission and verified continuation binding, before WGP task
validation. The mapping transaction changes only executable prompt text. Its
receipt binds the selected checkpoint, current published/generated geometry,
source lineage, reference identities, and mapping record. Unchanged completed
prefixes retain their receipts through suffix-only duration/peak replanning.
Authored Ref2VA text cannot be converted to Base by dropping its fields; that
conversion requires retained original Base source.

Source-template recipe 2 resolves WGP macros before Base canonicalization while
preserving raw authored text. The separate `template` recipe resolves same-Base
long-job macros before child splitting without forcing canonicalization. WGP's
one-time template skip comes from the validated server plan and a private keyword
argument; client parameters cannot enable it, and it is removed from returned
validated settings. Literal dialogue and literal braces produced by macros are
preserved.

Selected images are decoded from bytes matching the admitted size/SHA. Selected
video/audio inputs use private copies with the same identities; K soundtrack
presence is verified on those copies, and physical video slots stay stable.
Cached previews made before binding are cleared. Copies are released before the
next child and in the outer worker cleanup. Hidden identity journals support
bounded startup/terminal orphan cleanup, preserving live jobs and rejecting
foreign or changed entries. Real process-exit regressions cover interruption
before a second copy's journal and after the first cleanup unlink. A hard exit
before the first durable identity can still leave a zero-byte temporary or an
empty directory; no reference media bytes are written during those windows.

Automatic mapping with video/audio references is unavailable outside the POSIX
implementation; use a manual checkpoint there. Experimental source-audio roles
also require a manual checkpoint. Neither limitation changes those existing
manual execution paths. Independent review passed 64 focused CPU tests. Real CPU
probes exercised the WGP parser/validator, pinned image replacement, frame
alignment, and paired soundtracks in physical video slots 1 and 3 after their
original files were replaced. Evidence is under
`.artifacts-temp/astra-mapper-dispatch-20260907/evidence/`.

This is source/CPU evidence. No service restart, model loading, GPU generation,
browser acceptance, or Windows acceptance is established by this milestone.
The owner has separately authorized GPU work through current coordinator grants;
use `gpu-coordinator-client` when a concrete GPU check is needed. The historical
tracker hold and the other activation/restart/provider restrictions remain.

Final expanded verification passed 1,768 tests with 17 explicit skips and stable
source hashes. The superseded unadopted `h3_prompt_adapt.py` and its tests are
retired after replacing their active WIP call sites; their preimages remain in
the milestone evidence. Unrelated pre-existing backend, UI, launcher, and
storage-janitor WIP remains outside this source milestone.

The final caller regression maps every child of an actual Studio plan. Known
planner carry/seam text is folded into the first visual payload without losing
context, dialogue, or original source provenance; all eight seam keys must be
present in order. A real WGP CPU probe also validates both carried children.
The pending automatic Ref/FL alternation and blanket mixed/text-only rejection
overlays were discarded after review: they could lose supplied references and
override manual checkpoint choices. Semantic runs and explicit selections stay
intact. Related WIP tests were aligned with those retained contracts, including
metadata-only `explicit_output`; none may silently choose PinkCherry.


## H3 handoff lifecycle closure (2026-09-07)

Fresh and recovered H3 handoffs now produce the same complete task parameters.
The unused fresh-only result marker and unsupported pending `user_semantic_refs`
mode are removed. Existing selected references remain present; when capacity
permits, the previous shot still is appended through the supported semantic-still
mode. Full reference capacity retains the supported prompt-only mode.

Cancellation/preemption bypasses ordinary continuity fallback and generation
retry/failure handling. Private partial tails and companion stills are cleaned
when preparation aborts; unused successful companion stills are also removed.
Ordinary tail failures retain the supported still fallback with bounded public
copy. Missing continuation stills fail before reference mutation.

The six-module applicable CPU gate passed 316 tests, including nine lifecycle
regressions exercising extracted production helpers, actual caller exception
handlers, and private staging. Evidence is retained in
`.artifacts-temp/astra-h3-handoff-closure-20260907/evidence/`. This is CPU/static
and synthetic caller evidence, not live worker, GPU, browser, or Windows
acceptance. Other existing WIP remains outside this milestone.

## Owner-directed profile and interaction contract (2026-09-07)

Saved profiles in Generate and comparable workflows must round-trip all
user-adjustable settings. Audit capture, storage, restore order, model-default
loading, and every secondary settings surface; do not treat a passing test for
a small allowlist as proof of completeness. Keep project/account authorization
and transient runtime identity separate from reusable settings.

Place profiles near the top of Generate, with save/update access from Advanced.
Simplify Generate and Advanced, then apply the same interaction rules throughout
Director, project/reference flows, and settings: common actions first, consistent
labels and control placement, eligible combinations offered through the controls,
and longer explanations behind optional details. Preserve explicit selections
and provide an actionable path when a saved selection is unavailable; do not
silently replace it or use paragraphs of warnings as the main interaction.

This is an addition to the full continuous objective, not a replacement for the
active reference-import closure or the remaining backend work. Image-first
exploration uses only a synthetic brief; no private project material is sent to
an image provider. Browser acceptance still requires the existing filesystem
and synthetic-network gates; source/build evidence alone is insufficient.

## Adaptive inputs and settings preservation closure (2026-09-07)

Explicit adaptive H3 offers both attachment families for separately planned
segments. Manual model and performance-profile restrictions remain, as does the
short mixed-input guard: a native-length request cannot preserve both channels.
Prompt review describes the actual late Base-to-Ref mapping and does not promise
unsupported Ref-to-Base conversion.

Project-reference packs are prepared before input mutation, retaining authored
order and roles, existing video/audio paths, and measured durations. Capacity,
terms, account/project identity, selected outputs, and current input state are
checked before commit. All potentially failing preview allocations happen
before setters; preparation failure leaves Generate inputs unchanged. H3 alone
receives its duration bounds; generic media retains positive finite-duration
validation. An interrupted operation can leave an unused uploaded file, but
cannot attach it to a changed project or partially apply the pack.

Submode restoration retains the live Auto toggle, checkpoint pair, and both
LoRA groups/weights. Film-grain edits preserve the full current parameter
snapshot and duration instead of replacing it with four fields. Comprehensive
saved-profile capture/server-schema/restore remains open under the owner
contract above; these fixes do not establish that broader requirement.

The exact candidate passed all 526 UI tests and the production build, plus 104
backend bridge/Studio tests with two explicit skips. Independent import review
is closed. The initial isolated UI run lacked the root version metadata; after
copying those unchanged HEAD files, the complete rerun passed. Evidence and
source digests are retained under
`.artifacts-temp/astra-adaptive-ui-closure-20260907/evidence/`. This is
CPU/static/synthetic/build evidence, not browser, GPU, Windows, or human
acceptance. The six synthetic Generate/Advanced layout concepts and selection
are retained under that artifact's `design/` directory; implementation and
rendered fidelity remain open.

## Full generation-profile source milestone (2026-09-07)

New saves use one versioned settings contract shared by the browser and durable
preset store. It covers declared generation parameters, model-specific tuning,
independent adaptive model/LoRA choices, and top-level timing, output, voice,
blend, and outpaint settings. A classification regression covers every declared
generation parameter; unclassified settings stop saving rather than disappear.
Required fields, ranges, enums, technical identifiers, multiplier structure,
and account/project storage boundaries are validated. The real HTTP route
uses the same versioned validator as durable storage.

Full-profile restoration clears omitted optional settings and applies captured
values after model options arrive. Edits or account/project/media changes during
that read cancel restoration. Empty selections, disabled values, zeroes, custom
settings, timing and voice-card state have round-trip coverage, including an
actual browser-generated payload passed through the Python validator. Spatial
upsampling has one authoritative saved value, and an explicit empty selection
also clears the submitted value. Older partial presets remain readable and
change only the fields they originally recorded.

Saved profiles now appear near the top of Generate and at the top of Advanced,
sharing selection and avoiding duplicate fetches. Profiles for unavailable
models remain visible, with Load disabled and Delete available. Advanced uses
concise groups and optional detail disclosures. Save/load/delete feedback waits
for confirmed results. Prompt text, attached media, account/consent state and
job identities remain with the current job under the stated default; the
owner's optional question about including creative inputs is still pending.

The frozen candidate passed 538 UI tests, 27 complete preset/backend tests,
TypeScript and the production build. Targeted component/helper lint and E2E
typechecking pass. Independent review findings are closed through focused
regressions; the final speech-mode check uses the real `speech` enum and passes
its generated payload through Python validation. Source/test hashes stayed
stable through the gate. Evidence is retained under
`.artifacts-temp/astra-full-profiles-20260907/evidence/`.

Rendered acceptance remains open. A synthetic desktop/mobile profile browser
case is prepared in `ui/e2e/generation-profiles.spec.ts`; it has been typechecked,
not executed. The owner has been asked to authorize one task-specific test tree
under `/dev/shm/maestro-profile-review-*`, because the runner requires a different
filesystem and project policy requires approval for external writes. Existing
browser versions match; no download or Maestro restart is needed. Do not treat
the generated concepts, source tests, or build as rendered acceptance.

Continue the broader owner-directed work: complete comparable saved-settings
flows and per-mode persistence, simplify the main Generate inputs/compatibility
flow, and apply the same interaction principles through Director, project and
reference workflows, and machine settings. The full continuous objective and
all runtime, historical-tracker, privacy and ownership boundaries remain active.

## Settings and compatibility continuation (2026-09-07)

In-session mode snapshots now use the generation-profile UI field contract,
retaining the separate prompt/media snapshots and canonical spatial-upscaling
value. Returning modes load model capabilities without replacing restored or
subsequently edited settings. First visits and removed-model fallbacks receive
current model defaults. The deliberate clean-refresh boot policy is unchanged;
H3 style workflow and Director identity guidance remain global across mode
switches. Explicit saved profiles still capture the complete profile contract.
Source and validation receipts belong under
`.artifacts-temp/astra-mode-settings-20260907/`.

The controls-first audit identifies four concrete remaining compatibility paths:

- Short adaptive H3 frame-plus-semantic combinations need separate segments;
  attachment choices and project-reference packs now enforce the native
  duration boundary before submission. Long mixed plans remain supported.
- Semantic audio references cannot outnumber visual references. The final
  combined project-reference pack is validated before downloads, independent of
  file iteration order. One shared helper also owns remaining media capacities.
- SageAttention2++ must not appear usable when adaptive routing requires Ref2VA;
  coordinate the selector with the actual engine contract instead of silently
  presenting a different effective engine. The attention-selection slice below
  closes this source gap.
- Previously attached inputs must stay visible and removable when a model
  change makes them incompatible. Replace repeated explanations with concise
  actionable controls; retain the final submission checks.

Director's existing generation-profile shortcut imports only LoRAs into its
role and is now labelled as that specific action rather than a full restore.
Audit output-sidecar restoration separately for value loss and late-response
overwrites; retain intentional metadata migrations and single-output batch size.

H3 frame actions now expose only start/end slots. New KFI injection is not
offered under H3 because the runtime treats every `image_refs` entry as semantic
conditioning; restored entries remain visible and removable. Generic-model
frame injection is preserved. Existing audio/video reference lengths must be
measured before related additions or project-pack downloads proceed. Pending
and unreadable lengths have separate actionable states, while independent
image additions remain available. Project-pack completion also checks current
duration, model-options identity and generation mode in addition to the prior
account, project, selection and media fences.

Direct semantic audio/video additions share one in-flight guard and a visible
busy state. They verify the original render's account/project/model/settings
and reference identities before measuring and after each asynchronous step,
so stale picker callbacks and overlapping drops cannot replace current inputs.
Removing restored KFI entries keeps paths, positions and process letters aligned.

The final combined candidate passes all 552 UI tests and the production build.
The first combined run found one stale function-signature test marker, which
was corrected before the complete rerun. Independent review caught Director
identity guidance leaking into mode snapshots and attachment-contract gaps;
all findings are closed with focused regressions and parent verification.
Exact source hashes remained unchanged
through the final gate. This is source/synthetic/build evidence; browser,
Windows, live generation and human acceptance remain open.

The bounded output-restore review found concrete follow-up work in
`useStore.ts:loadSettingsFromOutput`: same-model gallery selection does not
invalidate a pending restore, later clip/edit-media callbacks lack the same
identity fence, several canonical sampler fields are omitted, zero guidance
becomes 5, and delivery/discard settings can remain stale. After the mode-switch
writer finishes, reuse the canonical technical-field contract while retaining
sidecar migrations and single-output batch size. Acceptance must exercise
same-model A-to-B response races, late media responses, every canonical field's
presence and zero/false/empty values, delivery fields and window discard. This
was the static review intake for the repair below.

## Output settings restoration (2026-09-07)

Output-sidecar restoration now projects the 76 canonical technical parameters
and 21 declared custom settings through the shared profile field catalogue.
Present zero, false, empty and supported null values survive; omitted optional
values clear unrelated editor state. Unknown/private/creative nested custom
keys are excluded. Existing H3 long-form lineage, Recast migration, LTX decoder
and audio constraints, creative/media restoration and single-output batch size
remain explicit transformations. Zero guidance and film-grain saturation are
preserved, as are sampler, delivery and window-discard settings.

Model options are read before authored state is changed. Output, account,
project, model/settings and media identities fence the read. Late image, clip,
edit-source, repaint and recast hydration cannot overwrite a changed selection
or replacement input. Edit-source metadata fills source properties without
resetting the saved trim end. Explicit H3 output restores remain Custom and
refresh estimates without applying a matching profile or fallback.
The saved H3 style-workflow value also populates its authoritative UI selection.
Audio duration uses the saved seconds value, including automatic duration,
instead of an unrelated video-frame calculation.

The pre-commit fence covers all authored fields that restoration writes,
including music descriptions, edit mappings and mask results. An AST check
classifies direct restore writes as authored state or lifecycle bookkeeping.
Models absent from the current catalogue fail before model-options
loading or authored-state mutation, after documented compatibility remaps.

Gallery actions start against the clicked selection immediately. Restore
returns a confirmed result; reroll stops when restoration is missing or
cancelled, and no longer schedules generation after an arbitrary delay.
Preview hydration remains asynchronous and uses preserved server media paths.

The final combined candidate passes 565 UI tests, the production build,
and 21 H3 profile tests. Two obsolete Python checks of frontend source strings
were retired because the real store is now exercised by the profile, mode and
output lifecycle suites. Source/test digests and validation receipts are under
`.artifacts-temp/astra-output-restore-20260907/`. Independent review findings
are closed through focused regressions and parent verification; exact candidate
hashes remain stable through validation. Browser, Windows, live generation and
human acceptance remain open.

The targeted Recast/Inpaint/Post Processing control audit found no additional
visible technical knobs missing from profiles in those checked surfaces.
Mapping targets, reference alignment/counts and SAM targets remain current-job
content or derived metadata; hidden legacy Recast flags are not new profile
controls. Do not expose or profile them merely because sidecars retain them.

Continue the top-level editor/UI projection audit, including absent mask/target
metadata and settings whose submitted value comes from UI state, before claiming
whole-app restoration completeness. The remaining app-wide interaction work
above and rendered acceptance remain open.

## Attention selection and submission (2026-09-08)

Generate and Advanced use the same SageAttention2++ eligibility rule: the
effective text/frame checkpoint must be Base H3 and no semantic-reference
route may require Ref2VA. An incompatible saved selection remains visible,
with an explicit Use Dense SDPA action that changes only the engine. The
selector offers available combinations and removes unconditional warning
paragraphs. Current image, video and audio references participate in the rule.

Submission rechecks capability before uploads or queue creation and cancels
if account, project, mode, settings or inputs change while that check is
pending. The server preserves the requested engine during adaptive routing
and rejects a Sage request if any trusted segment needs another checkpoint.
Known incompatibility is checked before prompt enhancement; trusted-plan
validation and admission precede CUDA benchmark setup. Intentional curated
profile bundles, Sol operation fallback and the explicit Sage kernel's
fail-closed behavior remain unchanged.

Actual store regressions cover rejected routes, unavailable capability,
unchanged-engine success and late input/context changes. Backend fake execution
proves rejected requests do not reach prompt enhancement or CUDA setup.
Obsolete frontend source-string assertions were removed in favor of the
profile/output/attention store suites; preparation fixtures now admit the
new early capability check while retaining their separate failure contracts.

The complete CI-masked root run exercised 5,012 tests with 19 skips. It
reported 12 old structural/fixture failures that reproduce on unchanged HEAD,
plus one LLM unload-timing failure. That LLM test passed on unchanged HEAD and
the complete 82-test LLM module subsequently passed; retain the intermittent
full-suite timing failure as a reliability follow-up rather than a production
fix or a claim of a single clean full-suite run. The baseline test repairs
pass all 201 cases across their six full modules (two expected CUDA skips);
the exact candidate hashes and closure results are retained with the main run.
The deterministic lease-test repair below closes the test-reliability follow-up.

UI validation passes all 574 tests and the production build, with unchanged
candidate hashes. Acceptance excludes the earlier unmasked Python invocations;
their correction record and the replacement CI-masked run are retained with
the isolated HEAD-based candidate under
`.artifacts-temp/astra-attention-flow-20260907/`. Browser, Windows, live
generation and human acceptance remain open.

The next bounded restore audit found Inpaint metadata gaps: output sidecars
retain the effective target but restoration does not populate the explicit
SAM target; mask inversion is neither persisted in the sidecar nor restored
to its UI owner. Fix those with restore/resubmit regressions and clear absent
mask/target metadata so previous output state cannot leak into a new restore.
SAM targets remain current-job content, outside reusable profiles. Reconcile
cached-mask invalidation when inversion or other segmentation settings change.


## Inpaint selection and mask continuity (2026-09-08)

Inpaint sidecars retain the explicit SAM selection, including an empty
selection, separately from the effective detected target. They also retain
mask inversion. Output restoration repopulates these controls, uses the
historical detected target only when explicit-selection metadata is absent,
and clears old mask paths, previews and target labels when metadata is absent.
Retake and Inpaint restore the prompt-strength slider that their requests
actually submit; zero guidance remains explicit.

One store rule invalidates derived masks when the source, range, target,
inversion, canvas or account/project scope changes. It covers direct controls,
resolution/aspect selectors, mode/profile restoration and source replacement;
unrelated strength edits preserve the mask. Replacing a source invalidates
pending previews even when its path is unchanged. Output restoration publishes
its input state before its saved mask metadata so this rule preserves the
newly restored mask. The inversion checkbox subscribes to its state owner.

Mask previews now publish through a store operation fenced by request sequence,
account epoch, restore generation, mode and the exact segmentation inputs.
Late responses, including changes away and back, cannot replace a newer
preview or restored selection. The component separately fences its local
busy/error/display state. The old component publication and profile-only
cache invalidation were removed.

The isolated candidate passes all 581 UI tests, the production build and scoped
lint. Actual endpoint fake execution passes all four explicit-target/inversion
combinations with CUDA masked and SAM/model/worker calls forbidden. Focused
store tests exercise restore/resubmit, missing metadata, direct input changes,
profile changes, newer previews, restoration races and same-path replacement.
Independent review has no remaining findings; account-epoch and component-local
sequence behavior have static review in addition to the surrounding shared
identity tests. Exact source hashes and receipts are under
`.artifacts-temp/astra-inpaint-restore-20260908/`.

This is source, synthetic execution and build evidence. Browser, live generation,
Windows and human acceptance remain open. Continue the broader app interaction
and copy audit; these two source milestones do not establish whole-app rendered
acceptance. The deterministic lease-test repair below addresses the intermittent full-suite
LLM assertion; its historical runtime timing remains uninstrumented.


## Deterministic LLM lease regression (2026-09-08)

The unload-ordering test no longer measures an unrelated garbage-collection
deadline. A controlled three-second collector delay reproduced its previous
assertion failure after model state had already cleared and showed the worker
outliving the old test. This demonstrates the test's timing vulnerability;
the earlier full-suite run did not capture a collector stack or timing trace.

The replacement observes the unload worker's actual failed nonblocking acquire
on the same real re-entrant lock held by generation. It then checks that model
activity finalization completed before that acquire returns. Collection is
mocked out for this contract. The fake request ends only when explicitly
released; a finally block releases it and joins all started workers. Assertions
reject surviving workers, unexpected errors, stale loaded state and idle timers.

The complete 82-test LLM module passes with the shared CPU-only environment.
Mutation probes reject both a removed generation lease and removed unload
locking, and confirm both workers exit on those failure paths. The valid test
also passes independently of the collector's behavior. Independent review's
remaining inner-timeout finding is removed and the module/probes are rerun.
No production runtime code changed. Source, preserved WIP, controlled failure
and closure receipts are under `.artifacts-temp/astra-llm-lease-test-20260908/`.


## Closed LLM preparation error copy (2026-09-08)

Model download and image-support failures now use one immutable copy mapping
shared by the loader and the public load endpoint. The messages tell the user
to try loading the model again. Only an exact built-in RuntimeError carrying
one exact reviewed string receives this specific copy. Unexpected errors,
subclasses, structured arguments and messages with appended data use the fixed
preparation fallback; the public projection never stringifies exceptions.

The unadopted prefix-forwarding draft was replaced and its preimage retained.
Existing local-control admission, authorized-operation routing, success shape,
HTTP 500 behavior and cancellation boundaries are preserved. Actual extracted
endpoint tests cover failure and success, while adversarial cases verify that
provider/request details cannot be appended to public messages. The existing loader failure/retry tests also check the exact messages against
the same canonical mapping. Existing timestamp
and Dasiwa public-error regression cases were also verified and adopted.

The six applicable LLM/failure modules pass 175 tests under the shared CPU-only
environment. Independent review has no findings. Exact source hashes, the
isolated candidate, preserved unrelated WIP and receipts are under
`.artifacts-temp/astra-llm-load-errors-20260908/`. This is source and synthetic
execution evidence; no live model, provider, server restart or browser action
was performed.


## H3 saved-plan startup and recovery flow — 2026-09-08

A saved H3 plan now has an early execution-contract check before CUDA benchmark
setup, model/version preparation, or reference/LoRA provisioning. The check
binds the actual multi-clip prompt parser, requested frame counts, publication
and trim geometry, and semantic execution slices to the saved plan. Empty or
malformed stored plans and incompatible execution modes fail explicitly;
server-prepared plans are preserved rather than silently regenerated. Legacy
aggregate final-trim plans remain supported. The late dispatch check remains
as a defense against changes after preparation and uses the same resolver.

A mismatch finishes with the structured `h3_plan_mismatch` failure code and
clears stale OOM details. The queue card shows a short state description and,
for users allowed to generate, an **Open Generate** action. It does not offer
an exact retry of the invalid saved plan. Read-only users see no unavailable
action instruction. The action uses normal Generate navigation and does not submit a job or
restore the failed job's settings.

The former inline multi-clip parser is replaced by one shared non-mutating
helper. A 288-case synthetic comparison matched the prior parser's values and
parameter removal behavior. Existing authored-shot Studio test additions passed
against the prior committed source (97 tests) before adoption. The obsolete
source-format assertion now verifies shared-parser wiring and authored
multi-line prompt preservation.

Verification receipts are under `.artifacts-temp/astra-h3-startup-20260908/`.
The full UI suite passes all 581 tests and the production build passes. Python
checks use the local CI CPU environment mask; no model load, GPU generation,
restart, browser, Windows, or human acceptance is claimed. Independent review
closed the frame, plan-presence, permission-copy, aggregate-duration and clip-count
findings. The final 435-case CPU gate (nine skips) found only a recovery fixture
missing the producer's duration and aggregate-trim fields. Those two fields were
added, then the complete 163-test recovery module passed; the other nine modules retain
their passing evidence against unchanged source. Compilation, JSON-grammar,
publication-boundary and diff checks pass. Staged source/test hashes match the
isolated candidate, and unrelated whitespace and test reordering remain intact.


## Automatic conditioning repair and prompt-helper copy — 2026-09-12

Retained H3 inputs that conflict with manual conditioning now offer **Use
Automatic** beside a short state description. Attachments remain visible and
removable. The shot-matching checkbox description is concise and its existing
technical disclosure remains available.

Enabling Automatic from Ref2VA previously selected FL2VA through ordinary
model defaults, overwriting unrelated authored settings. Adaptive checkpoint
selection now updates model identity and fetches metadata only, preserving
technical settings, timing, attachments, delivery values and architecture-owned
LoRAs. Existing sequence guards invalidate earlier default/profile/option
responses; subsequent edits survive delayed metadata. Preserved profile values
are labelled Custom. Ordinary explicit model selection keeps its existing
behavior and final compatibility validation remains authoritative.

The Studio prompt-helper default option now says **Maestro default prompt
helper**, matching the backend's dedicated default rather than promising
Director inheritance. Unsupported performance comparisons and duplicate helper
copy were removed.

All 583 UI tests, production build, targeted ESLint and diff checks pass on the
isolated candidate. The actual-store regression covers Ref2VA-to-Automatic
settings/media preservation and delayed metadata with later LoRA edits.
Independent bounded review delivered a clean closure before its turn subsequently
reported a usage-limit error; the delivered review evidence is retained. On
resume every owned source/test hash matched that tested candidate, so no
unchanged suite was repeated. Receipts live under
`.artifacts-temp/astra-attachment-repair-20260908/`. This is source, synthetic
component/store and build evidence; browser, live generation and Windows
acceptance remain open. The next concrete prompt-improvement flow issue is
recorded in the current continuation pointer.


## Prompt preparation workflow eligibility — 2026-09-12

Improve before Generate is offered only where the generation endpoint accepts
that preparation step. The control and submission share `supportsPromptPreparation`;
Blend, avatar Edit and audio-only models omit the checkbox and keep their
existing standalone improvement actions. The saved preference is retained but
inactive in unsupported workflows, so a previous selection no longer causes a
late rejection or prevents a prompt-optional workflow from submitting. Returning
to a supported workflow restores the preference. Explicit empty-prompt checks
remain for active prompt preparation.

The obsolete late alert and duplicated endpoint predicate are removed. The
regression matrix exercises the actual submission expression across modes,
image modes, edit submodes, audio capability and saved-toggle values, including
returning from Blend. All 585 UI tests, the production build, targeted ESLint,
97 Studio tests under the CPU-only environment, and diff checks pass. Evidence:
`.artifacts-temp/astra-enhance-eligibility-20260912/`. Browser, live generation
and Windows acceptance remain separate; no service or model was started.


## Unused adaptive checkpoint helper retirement — 2026-09-12

The uncommitted `default_adaptive_ref2va_model` helper, duplicate constant and
export had no production callers. Their proposed validation was already owned
by `_apply_h3_adaptive_checkpoint`. The draft and its helper-only test were
preserved as private preimages under
`.artifacts-temp/astra-ref-helper-retirement-20260912/evidence/` and retired.
The service now matches its committed baseline byte-for-byte.

The replacement regression executes the actual launch selection function for
both H3 starting architectures, both explicit checkpoint fields, and unknown
or malformed choices. It proves rejection occurs without mutating authored
request fields or substituting another checkpoint. All 27 tests across the
LoRA compatibility and adaptive-execution modules pass in the isolated CPU-only
candidate. No GPU, model load, runtime restart or browser acceptance occurred.
Remaining launcher, test-reordering, queue and storage drafts are preserved.


## Queue regression and test-residue reconciliation — 2026-09-12

The queue polling-readiness draft is now executable coverage of the actual
component effect. It verifies the ready state is untouched, loss of admission
increments the request sequence and aborts the pending request, clears the
scheduler snapshot and active campaign state, and retains failed/cancelled
cards with their original identity and failure details. Repeated admission loss
does not re-abort the old controller. All 586 UI tests and targeted ESLint pass;
application source is unchanged, so the preceding build evidence remains valid.

The remaining LLM-test reorder and recovery-test blank-line drafts were proven
AST-identical to the committed tests (with unique class methods normalized by
name). Their preimages and parity receipt are preserved under
`.artifacts-temp/astra-queue-reconcile-20260912/evidence/`; the committed ordering
was restored without deleting or changing any test body. The queue source-only
assertion was replaced rather than retained alongside its executable replacement.
No runtime, GPU or browser acceptance is inferred from these checks.

Current planned-work review found storage-janitor drafts are a pure policy core,
but FRESH_THREAD_HANDOFF.md explicitly reserves their adoption behind ownership
recovery. Do not treat their absence from imports as permission to delete them.
Remaining safe work is the broader app interaction/settings audit and launcher
WIP inspection under the launcher-specific workflow; live account, provider,
service restart and storage activation remain separately gated.


## Profile-list failure and access recovery — 2026-09-12

Generation profile refresh no longer turns a network/server failure into an
empty saved-profile list. Same-scope transient failures retain the last successful
records and show a short error with Retry in the shared Generate/Advanced panel.
An initial failure shows Profiles unavailable, distinct from a confirmed empty
list. Successful retry replaces the list and clears the error.

The API preserves HTTP status in the existing protected-read error type. Current
401/403/423 responses clear cached profiles and selection, then request the
existing account/project recovery flow; 404 also clears inaccessible records.
Only the still-current request/account/project may publish errors, alter loading
state or request recovery. Account/project resets clear the error state, and no
raw transport/server details are shown. Retained records remain scope-bound by
the existing preset application checks.

All 589 UI tests, production build, targeted ESLint and diff checks pass in the
isolated candidate. Added actual-store tests cover failed refresh retention,
successful retry, newer-success and project-switch races, and all four access
statuses with the expected recovery events. Receipts and candidate hashes:
`.artifacts-temp/astra-profile-refresh-20260912/`. No browser, runtime, GPU or
Windows acceptance is claimed. Continue auditing other saved-profile consumers
(such as Director's LoRA-only import) for consistent loading/error affordances.


## Shared profile feedback in Director — 2026-09-12

Director's LoRAs from saved profile area no longer disappears during loading
or an unsuccessful initial fetch. It shows loading state or the same Retry
feedback used by Generate and Advanced. Matching cached LoRA selections remain
usable during transient refresh failures; explicit access denial still clears
records through the shared store. The action retains its LoRA-only label and
payload, without implying a full generation-settings restore.

The stateless GenerationProfileRefreshStatus component owns common failure
copy layout and retry behavior without importing sidebar state or model-picker
dependencies. The previous inline status implementation is removed. Synthetic
component tests cover empty/loading/error states, disabled retry, retry dispatch,
and exact LoRA-only import payload. All 591 UI tests, production build,
targeted ESLint and diff checks pass on the isolated candidate. Receipts: `.artifacts-temp/astra-profile-consumers-20260912/`.
Browser and live acceptance remain separate.


## Account path forwarding in Start — 2026-09-12

Adopted the three-line launcher draft forwarding `MAESTRO_ACCOUNT_STORE_PATH`,
`MAESTRO_ACCOUNT_PROJECT_MIGRATION_PATH` and
`MAESTRO_ACCOUNT_PROJECT_MEMBERSHIP_PATH` through the existing freshly resolved
backend environment. Per-app configuration wins over global values; explicit
empty values select backend defaults. No path is hardcoded, and forwarding does
not enable accounts, bootstrap or migration, move data, or change credentials.
README documents the path behavior.

The launcher checklist resolved the current checkout under the configured
Pinokio home, inspected the latest start log, and checked the existing shell.run
environment pattern against the mochi example. The project's stronger captured
URL rule remains unchanged (`input.event[1]`), as do daemon mode, relative app
working directory and runtime selection. The complete launcher compatibility
suite passes 34 tests with one existing skip; Node syntax and diff checks pass.
Tests evaluate launcher definitions with synthetic configuration, including
quoted spaces, hash characters, Windows-style paths, empty overrides and global
fallback. They do not start the application. Receipts and preflight record:
`.artifacts-temp/astra-account-path-launcher-20260912/`. Live restart, migration,
account activation and Windows execution remain separately unverified/gated.


## Full committed-tree CPU integration — 2026-09-12

A clean archive of `4402792` completed the full Python unittest discovery:
5,037 tests in 1,361 seconds, with 19 skips and no failures. The run used the
existing app interpreter and `scripts/run_local_ci.py:cpu_only_environment()`;
only the archive's app directory was added to its Python import path. Untracked
storage-janitor drafts and machine-local models/configuration were excluded.
The synthetic failure line printed by the local-CI runner regression was an
expected test fixture; the authoritative unittest result and process exit are
both successful. Python syntax, JSON grammar and tracked publication-boundary
checks also pass. The latest unchanged UI source retains its 591-test/build/lint
pass; no redundant UI rerun was needed for this backend integration checkpoint.

The remaining three blank-line edits in `app/launch.py` were verified against
HEAD by equal nonblank lines and equal AST, preserved as an exact preimage, and
retired. The tracked worktree is now reconciled. The literal control-key audit
found 55 distinct setParam keys in component TSX files, all classified by the
profile schema or its explicit exclusions. This does not prove coverage of
all dynamic keys, content/media fields, or every UI-only state variable.

Receipts: `.artifacts-temp/astra-integrated-cpu-20260912/` contains source
identity, full test log/result, auxiliary check logs, profile key audit and
whitespace parity/preimage. Evidence is CPU/static/synthetic; it does not prove
browser, native Windows, provider, live-device, acoustic/visual or human
acceptance. The broader Goal remains active. Next source audit: profile-panel
local notices and pending actions across account/project changes, followed by
remaining app interaction consistency. Preserve separate browser filesystem,
storage ownership, activation and GPU authorization gates.


## Profile panel lifetime and delayed deletion — 2026-09-12

The shared profile panel now remounts its local form state on account identity
epoch, account, project or generation-mode changes. Draft names, confirmations,
notices and pending-action busy state cannot carry into the new context; old
async completions target their old unmounted panel. The prior mode-only notice
reset is superseded by this keyed lifetime.

Removed the component's unconditional selected-profile clear after deletion.
The existing store action owns that update and clears only the deleted ID in
the same account/project, preserving a newer selection. Regressions exercise
the delayed-delete panel callback and the actual store action across selection,
project and account changes, plus collision-safe scope keys. The existing mock
renderer now executes the nested panel and supplies the account epoch.

All 594 UI tests, production build, targeted ESLint and diff checks pass on the
isolated candidate at `.artifacts-temp/astra-profile-scope-20260912/`. The full
5,037-test backend result remains applicable to unchanged backend source.
Browser and live acceptance remain separate. This closes the profile-panel
local-state audit identified at the integration checkpoint.


## Offline recovery action and browser availability — 2026-09-12

Read-only browser inspection found the existing stable Maestro tab displaying
its offline page, and the user Continuum service reports inactive. No service
was started or restarted. This proves the inspected access surface was offline,
not that the newly committed app UI has passed rendered acceptance.

The offline Worker source now offers a keyboard-focusable **Try again** link
on both generic offline and current service-update pages. It navigates to the
same page without scripts or reflected request URLs. The generic explanation
is reduced to the reachability result and a short owner action. Repeated
no-tracking/no-content disclaimers are removed from primary copy; the same
security headers, static-page restrictions and API 503 JSON contracts remain.
One shared link/style definition keeps the two pages consistent.

All 35 Worker tests, Node syntax and diff checks pass. The tests retain escaped
status, unavailable-origin, API/browser distinction, no external URL reflection
and restrictive-CSP coverage, and require the retry link for every status state.
Receipts and a synthetic offline HTML artifact are under
`.artifacts-temp/astra-offline-retry-20260912/`. Browser URL policy rejected the
local-file preview; no alternate browser route or security-policy workaround
was attempted. The source change is not deployed, and its rendered appearance
remains unverified. A separately authorized Worker deployment would be required
to change the live offline page.

The Worker reservation registry initially required reconciliation. A complete
read-only status found zero sentinels, and the supported reconcile command
rebuilt the empty registry before acquisition. No foreign claim was removed.
