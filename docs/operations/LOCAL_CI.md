# Local publication and build checks

Maestro defines no GitHub Actions workflows and allocates no hosted Actions
worker. `scripts/run_local_ci.py` uses the installed `app/env/` and
`ui/node_modules/`; it does not install dependencies, start generation, contact
GitHub, or restart services.

```bash
# Default: tracked-publication boundary only.
app/env/bin/python scripts/run_local_ci.py
# Build the UI only when its inputs changed.
app/env/bin/python scripts/run_local_ci.py --gate ui
# Publication boundary plus UI build.
app/env/bin/python scripts/run_local_ci.py --gate all
```

`guard` names the default publication boundary. `backend` is a compatibility
alias for it. `--dry-run` lists the selected commands without executing them.
These checks exclude private/runtime artifacts from publication and compile
the shipped UI. They do not run Python unittest discovery, Node unit tests,
a broad Python syntax sweep, or the retired mocked Playwright matrix.

Child processes receive masked GPU visibility. This is an environment hint,
not hardware isolation or a GPU lease. No native generation is included.
Optional upstream research uses `scripts/check_h3_turbo_upstream.py` directly;
it is not a routine acceptance gate.

For behavior acceptance, use the small real Maestro journeys described in
[CONTRIBUTING.md](../../CONTRIBUTING.md). Retained test sources, private CPU
receipts, and archived synthetic fixtures are historical evidence, outside the
routine run list. Preserve formal proof obligations and frozen dependencies;
do not relabel mocked browser checks as real E2E or a build as native acceptance.
Runtime authorization and execution safeguards remain in force.

A future self-hosted runner needs separate activation with an owner-controlled
host, provisioned dependencies, trusted-ref execution and no hosted fallback.
Do not add a workflow merely to make these checks automatic.
