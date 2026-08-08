# Scheduled public research

Maestro's research cycle discovers public model, tune, tool, and LoRA metadata,
analyzes one bounded candidate at a time, reconciles duplicates and contradictory
claims, and records findings as `ready_for_review`. It does not edit product code,
download model files, inspect Maestro content, or start an implementation.

## Privacy and provider boundary

Discovery is limited to fixed HTTPS JSON endpoints on GitHub, Hugging Face, and
Civitai. Responses are byte- and record-limited. Remote prose is untrusted,
bounded, and obvious instruction-shaped content is withheld before analysis.
The cycle has no input or code path for projects, prompts, jobs, media, logs,
credentials, personal data, private repositories, or arbitrary URLs.

Each candidate first uses the configured hardened Nous bridge directly over
stdio MCP. Only `web_run` is callable, using the exact
`deepseek/deepseek-v4-flash-0731` model at `max` effort and at most six tool
calls. DeepSeek is accepted as the final analyst only when retained bridge
evidence proves the provider-reported model and effort, all-2xx transport,
exact-gate eligibility, source retention, and a matching validated receipt.
Maestro persists only bounded non-sensitive proof fields.

If that mechanical gate fails, GPT-5.6 Luna at `high` is the only fallback. Luna
runs with a disposable isolated `CODEX_HOME`, `--ignore-user-config`, an empty
temporary working directory, a minimal environment, an ephemeral read-only
sandbox, and no MCP configuration. Its JSONL transport is rejected if any tool
call is observed. There is no silent provider substitution. `NOUS_API_KEY`
remains environment-only inside the existing bridge and is never copied into
arguments, prompts, state, or documentation. Maestro intentionally excludes it
from the child environment: the configured owner-controlled bridge launcher must
self-provision environment-only authentication (the installed launcher loads its
owner-only env file before executing the bridge). A confirmed exact-gate, quota,
credit, payment, model-availability, or transport failure opens a per-cycle
circuit breaker; remaining candidates use zero-call Luna fallback.

## Schedule

Enabling research stores an anchored schedule:

- every six hours from enablement through day 7;
- daily from day 7 through day 14;
- weekly thereafter.

The next due time is durable. On restart or after downtime the cycle runs at most
once, then selects the first future anchored slot; missed intervals never create
a catch-up burst. Re-enabling an already enabled schedule is idempotent. Disabling
and later re-enabling begins a new schedule.

The ignored runtime store is the fixed canonical `app/storage/research`; the CLI
cannot redirect production state. Tests must explicitly opt into a bounded
temporary root. Every path component and file is opened without following
symlinks, reads are byte-limited, and `state.json` and `current.json` use
directory-relative atomic durable replacement. Research and implementation
start transactions reject each other atomically. Bounded event files are
immutable once created; the oldest are pruned after the retention limit. An
existing lease is never reclaimed automatically: ambiguous crash leftovers fail
closed and require explicit local review, so recovery cannot remove a replacement
or live implementation lease.

## Commands

Run these from the repository root with the application environment:

```text
app/env/bin/python app/scripts/run_research_cycle.py status
app/env/bin/python app/scripts/run_research_cycle.py enable
app/env/bin/python app/scripts/run_research_cycle.py enable --batch-size 6
app/env/bin/python app/scripts/run_research_cycle.py run
app/env/bin/python app/scripts/run_research_cycle.py run --dry-run
app/env/bin/python app/scripts/run_research_cycle.py run --force
app/env/bin/python app/scripts/run_research_cycle.py disable
```

`run --dry-run` performs no discovery, provider call, state write, or directory
creation. `--force` is explicit and bypasses only the due/enable check. It does
not weaken source, provider, schema, sandbox, or write boundaries. The run command
is safe for a launcher or service scheduler to invoke frequently because it exits
without work until `next_due_at`.

Scheduled cycles analyze six candidates by default. An explicit batch override
may select 1 through 24; 24 remains the hard per-cycle ceiling. Round-robin
source selection prevents an earlier lane from consuming the entire batch.

`packet` emits one digest-addressed, deterministically reconciled packet:

```text
app/env/bin/python app/scripts/run_research_cycle.py packet
app/env/bin/python app/scripts/run_research_cycle.py packet --force
```

The default readiness threshold is three findings. The force flag is recorded in
the packet when it emits fewer. Packet generation remains read-only. A separate
controller must explicitly call `ResearchStore.begin_implementation_run` before
any strong-agent implementation and `finish_implementation_run` afterward; this
research tool never executes that work itself.

## Status contract

`ResearchStore.read_model()` exposes only sanitized operational metadata:
schedule state, last and next cycle times, queued candidate count, active research
phase, implementation activity, chunk count, readiness threshold/reason, up to
three concise pending summaries, and the last implementation-run record. It does
not expose source excerpts or any Maestro user content.

Contradictions remain durable and cannot enter an implementation packet until a
local reviewer explicitly calls `ResearchStore.resolve_finding_conflicts` with a
bounded resolution record. That record and the append-only event retain the
resolved claims and resolution summary for audit. If later evidence reopens a
conflict, the current resolution marker is cleared while a bounded resolution
history in `current.json` preserves the earlier review independently of event
pruning or a crash between the index update and auxiliary event append.
