# Astra continuation handoff — 2026-09-06

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
