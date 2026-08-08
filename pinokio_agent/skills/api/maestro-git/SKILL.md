---
name: api-maestro-git
description: Use Maestro's HTTP API to inspect generation, Director, output, LLM Chat, and runtime state.
---

# Maestro API

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

## Outputs

Responses are JSON except media-file endpoints, which stream the requested asset.

## Notes

Generation and Director pipelines have distinct status endpoints and lifecycle semantics; do not treat a Director pipeline identifier as a Studio job identifier.
