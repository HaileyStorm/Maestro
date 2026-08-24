# Trusted Local CI

Maestro does not spend GitHub-hosted Actions minutes. The repository defines no
GitHub Actions workflows while it has no approved self-hosted runner. Pushes,
pull requests, and schedules therefore allocate no Actions worker at all.

The canonical check runner is `scripts/run_local_ci.py`. It uses the Python
environment already maintained at `app/env/` and the existing dependencies in
`ui/node_modules/`. It never runs `pip`, `npm ci`, or another installer. Missing
dependencies are a preflight failure with an instruction to use Maestro's
normal Pinokio install/update path outside CI.

## Run checks directly

From the repository root:

```bash
app/env/bin/python scripts/run_local_ci.py --gate guard
app/env/bin/python scripts/run_local_ci.py --gate backend
app/env/bin/python scripts/run_local_ci.py --gate ui
app/env/bin/python scripts/run_local_ci.py --gate all
app/env/bin/python scripts/run_local_ci.py --gate h3-upstream
```

Use `--dry-run` with any gate to print its exact commands without probing the
environment or executing anything.

| Gate | Checks |
| --- | --- |
| `guard` | Tracked-publication boundary and Python syntax. |
| `backend` | `guard`, complete Python unittest discovery, and the standalone JSON grammar regression. |
| `ui` | Existing UI tests, then the TypeScript/Vite production build. |
| `all` | `backend` followed by `ui`; this is the complete local CI gate. |
| `h3-upstream` | Read-only comparison of the pinned H3 Turbo observation with Hugging Face main. |

Every child process receives empty CUDA/HIP/ROCm visibility and
`NVIDIA_VISIBLE_DEVICES=void`. The defined command list contains no generation
step. This environment masking is a runtime hint, not hardware isolation, and
does not prove that arbitrary future test code cannot touch another accelerator
or load a model. It does not replace the real-GPU acceptance matrix. Python
bytecode/cache writes use a process-owned temporary directory rather than the
repository's storage tier.

## GitHub Actions stay off

Do not add a GitHub Actions workflow merely to make checks look automatic. Run
the wrapper locally and record the selected gates with the change. The H3
upstream check prints its current observation for human review; it no longer
has a schedule or automatic issue mutation.

A future self-hosted workflow is a separate activation. Before adding one,
verify an owner-controlled Linux x64 runner, a clean disposable checkout,
pre-provisioned dependency paths outside that checkout, immutable action SHAs,
least-privilege credentials, bounded concurrency/timeouts, and trusted-ref-only
execution. There must still be no hosted fallback and no automatic execution of
unreviewed pull-request code.

## Evidence boundary

A passing direct wrapper run proves the selected checks in that checkout and
environment. Source review proves only that Actions workflows are absent.
Record any later runner acceptance separately, and continue to track model
generation in [GPU_ACCEPTANCE.md](GPU_ACCEPTANCE.md).
