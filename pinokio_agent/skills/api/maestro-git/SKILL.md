---
name: api-maestro-git
description: Continue Maestro repository work safely and use its bounded HTTP and restart-status operations.
---

# Maestro API

## Repository Continuation

Resolve the repository root dynamically; never rely on a checkout-specific host
path. The root must contain both `AGENTS.md` and the repo-root `.beads/` tracker:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
test -f "$REPO_ROOT/AGENTS.md" && test -d "$REPO_ROOT/.beads" || exit 1
cd "$REPO_ROOT"
```

Before editing, read `AGENTS.md` and run these commands serially:

```bash
bd where
bd prime
bd show ISSUE_ID
git status --short --branch
```

Inspect `.working`. Create a short owner/scope sentinel only when it is absent;
never overwrite another active owner's sentinel. Inventory and preserve every
pre-existing dirty path. Do not reset, checkout, clean, or implicitly stash
unrelated work, and stage only explicitly owned files.

## Operations

- `GET /api/v1/jobs` lists active Studio generation jobs.
- `GET /api/v1/status/{job_id}` returns one Studio job's current status.
- `POST /api/v1/cancel/{job_id}` requests cancellation of a Studio job.
- `GET /api/v1/outputs` lists gallery media and accepts pagination, filter, search, and workspace query parameters.
- `GET /api/v1/director/pipeline/{pipeline_id}` returns Director pipeline state.
- `GET /api/v1/llm/models` returns the path-free Chat catalog and server-owned prompting guides.
- `POST /api/v1/llm/chat` runs one project-authorized, role-preserving Chat turn.
- `POST /api/v1/llm/chat-upload` uploads an ephemeral project/session-owned image for a multimodal Chat turn.
- `GET /api/v1/llm/status` reports load/download state, actual CPU/CUDA backend, effective profile, vision state, and timing metrics without local paths or secrets.
- `GET /api/v1/system-stats` provides a lightweight readiness and resource check.
- `GET /openapi.json` is the authoritative runtime contract for additional operations.

## Runtime Inputs

Pass the currently reachable Maestro base URL at runtime. Job and pipeline identifiers are session-specific and must be discovered from API responses.

Chat requests must name an unlocked workspace. Remote callers may select only
catalog model IDs; arbitrary Hugging Face sources and host paths are local-owner
capabilities. Image references must come from the Chat upload route and must be
used with the same project/session that created them.

## Coordinated Restart Status

The operator environment supplies the stable-share configuration. Keep its
secret values and the restart generation untracked, unprinted, and out of
commits, issue comments, and handoffs. Create one opaque generation at runtime,
then set and show a bounded public notice before the restart:

```bash
RESTART_GENERATION="$(python -c 'from app.scripts.restart_status import new_generation; print(new_generation())')"
python app/scripts/restart_status.py set --state planned --reason restart \
  --message "Planned maintenance" --generation "$RESTART_GENERATION"
python app/scripts/restart_status.py show
```

Serialize restart-status writes; overlapping writers are unsupported. Preserve
the same untracked generation until recovery. Process visibility alone is not
health evidence. Verify the intended local health endpoint and, if reporting
remote recovery, exercise the configured stable access surface. Only then clear
that exact generation and confirm the resulting state:

```bash
python app/scripts/restart_status.py clear --generation "$RESTART_GENERATION"
python app/scripts/restart_status.py show
unset RESTART_GENERATION
```

An exact-generation clear that reports `NOT_CLEARED` is a safety stop: do not
clear a newer notice or retry with another generation.

## Outputs

Responses are JSON except media-file endpoints, which stream the requested asset.

## Evidence and Completion

Keep evidence levels distinct:

- Source inspection, compilation, and mocked tests are static evidence.
- A local process answering its health endpoint is local-runtime evidence.
- Exercising the configured LAN or stable-share URL is live-access evidence.
- Human acceptance must be reported only when a person actually confirmed it.

Run the full applicable tests and all CI-equivalent checks before completion;
do not use a filtered test as the final gate. After owned changes are committed,
run repository mutations serially: `git pull --rebase`, `bd sync`, `git push`,
then `git status --short --branch`. The final status must show the intended
branch up to date. Never discard or force through unrelated dirty work.

## Notes

Generation and Director pipelines have distinct status endpoints and lifecycle semantics; do not treat a Director pipeline identifier as a Studio job identifier.
