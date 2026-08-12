# Contributing to Maestro

Thanks for your interest in improving Maestro! This is a local-first AI
video/image/music studio built on the [Wan2GP](https://github.com/deepbeepmeep/Wan2GP)
pipeline and distributed through [Pinokio](https://pinokio.computer).

## Getting set up

Maestro is a Pinokio app, so the easiest dev loop is:

1. Install Maestro through Pinokio (see the [README](README.md)). This creates
   the Python environment in `app/env/` and installs the app.
2. Edit the source in place. The layout:
   - **Launcher scripts** (`install.js`, `start.js`, `update.js`, `reset.js`,
     `pinokio.js`) live at the repo root.
   - **Backend** — `app/`: FastAPI endpoints in `app/launch.py`, the generation
     pipeline in `app/wgp.py`, and services (LLM, Director, recipes, etc.) in
     `app/services/`.
   - **Frontend** — `ui/`: a React + TypeScript + Tailwind app; global state in
     `ui/src/stores/useStore.ts`.
3. After changing the UI, rebuild it:
   ```
   cd ui
   npm install
   npm run build
   ```
   Pinokio's **Update** flow does this automatically; during active dev you can
   run it yourself.

## Continuing coordinated work

Never assume the checkout path or continue from an arbitrary nested directory.
Resolve and validate the repository root first:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
test -f "$REPO_ROOT/AGENTS.md" && test -d "$REPO_ROOT/.beads" || exit 1
cd "$REPO_ROOT"
```

Then recover the current contract and inventory the workspace before editing:

```bash
bd where
bd prime
bd show ISSUE_ID
git status --short --branch
```

Read `AGENTS.md`, the issue, and `.working`. If `.working` is absent, create a
short ownership/scope sentinel before edits; if it belongs to another active
owner, coordinate instead of overwriting it. Record all pre-existing dirty
paths and preserve them. Do not reset, checkout, clean, or implicitly stash
someone else's changes, and stage only the exact paths you own.

For a coordinated restart, keep the operator configuration and a newly
generated status-generation value in the untracked runtime environment. Do not
paste their values into documentation, issue comments, commits, or logs. Set a
bounded public status and confirm what the Worker accepted:

```bash
RESTART_GENERATION="$(python -c 'from app.scripts.restart_status import new_generation; print(new_generation())')"
python app/scripts/restart_status.py set --state planned --reason restart \
  --message "Planned maintenance" --generation "$RESTART_GENERATION"
python app/scripts/restart_status.py show
```

Serialize every status operation; overlapping writers are unsupported. After
the intended service is healthy—and after the stable access surface is verified
if the handoff claims remote recovery—clear only that exact generation, then
confirm the result:

```bash
python app/scripts/restart_status.py clear --generation "$RESTART_GENERATION"
python app/scripts/restart_status.py show
unset RESTART_GENERATION
```

A stale generation must not clear a newer operator's notice. A `NOT_CLEARED`
result is a safety stop; do not retry with another generation. Do not clear on
process visibility alone: verify the relevant health endpoint and access path
first.

Report evidence honestly:

- **Static:** source/diff inspection, compilation, and tests using fakes or
  mocks. This does not prove a running service.
- **Local runtime:** the intended local process answered its health check and
  relevant logs were inspected. This does not prove LAN or stable-share access.
- **Live access surface:** the configured LAN or stable-share URL was exercised
  successfully from the claimed surface. Discovery or configuration alone is
  not live verification.
- **Human acceptance:** a person confirmed the user-visible workflow. Do not
  infer this from any automated check.

## Before you open a PR

Run the full applicable suite, not a filtered test as the final gate. These are
the CI-equivalent checks:

```bash
python scripts/verify_clean_repo.py
python -m compileall -q app/services app/launch.py scripts
python -m unittest discover -s tests -p "test_*.py"
python tests/test_call_llm_json_grammar.py
(cd ui && npm ci && npm run build)
```

Also run `(cd ui && npm test)` when frontend behavior is in scope, and the
applicable browser/E2E checks when their dependencies are available. Record any
gate you could not run and why; do not relabel static or mocked evidence as live
acceptance.

Once all intended changes are committed, finish repository state changes
serially—never in parallel:

```bash
git pull --rebase
bd sync
git push
git status --short --branch
```

The final status must show the intended branch up to date with its remote. If
unrelated dirty work prevents a safe rebase or push, coordinate with its owner;
do not hide, discard, or force through it. Remove only the `.working` sentinel
you own after the handoff is complete.

### The clean-repo guard

`scripts/verify_clean_repo.py` enforces that certain **locally-generated or
machine-specific artifacts never get committed** — downloaded weights, CivitAI
metadata sidecars, per-LoRA generated guides, and per-checkpoint finetune JSONs.
These are all gitignored by design; the guard is the backstop that keeps them
out of the published tree. If it fails, it prints exactly what leaked and where.
Don't work around it — fix the leak (usually a file that should be gitignored
got `git add`-ed).

## Conventions

- **Match the surrounding code.** Follow the naming, structure, and comment
  style already in the file you're editing.
- **Keep the app local-first.** No telemetry, no phone-home, no required
  accounts. External API providers (OpenAI/Anthropic/etc.) stay strictly
  opt-in and off by default.
- **Third-party components keep their own licenses.** Notably the GPL-3.0
  seed-vc voice component is fetched from its own repository at install time
  (see the README license section) rather than vendored here — don't commit it
  back into `app/postprocessing/seedvc/`.

## Reporting bugs

Please use the **Bug report** issue template — it asks for your logs
(`logs/api/latest` in the Pinokio app folder) and GPU/VRAM/OS, which is almost
always what's needed to reproduce a local-generation issue.

## License

Maestro is released under the WanGP Non-Commercial Evaluation License (inherited
from upstream Wan2GP). By contributing you agree your contributions are licensed
under the same terms. See [LICENSE](LICENSE).
