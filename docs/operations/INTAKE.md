# Maestro candidate intake

This is the reusable procedure for a Hailey dump: links, papers, model cards,
attention kernels, LoRAs, Comfy graphs, tweets, workflows, “cool new things,”
or a mixed research tab set.

**A dump is not a build list.** Do not implement the items as-is. Deduplicate,
filter, triage, merge, prioritize, extract, tweak, read comments, and use the
survivors as jumping-off points against the shipped Maestro baseline.

Aliases: ingest, injest, research dump, tab set, candidate intake.

This procedure is **decision work**. It does not install a node, download a
model, inspect private generation data, start GPU jobs, or change the current
Maestro checkpoint unless the user later asks to implement a *decided* item.

## How this differs from nearby procedures

| Procedure | When to use |
| --- | --- |
| This file | A pile of external candidates to judge before any code |
| `AGENTS.md` **Incremental user-input triage** | Follow-ups during *already active* Maestro work (“oh, add this,” a test result, a correction) |
| `AGENTS.md` **Diversified brainstorming** | Open-ended product or architecture questions where variety has material value |
| Feature-wave / watch notes | The *output* of a completed intake, not a substitute for this procedure |

## Prior practice (review these first)

Intake already happened as dated notes, not as a named procedure. Before a new
dump, re-open the closest prior notes and copy their decision grain:

1. `docs/development/feature-wave-2026-08.md` — mixed public repos, papers,
   model cards, and community threads. Establishes: a saved link is not an
   endorsement; start from the shipped baseline; extract invariants instead of
   importing frameworks; per-candidate adopt / experiment / extract / reject;
   comments and issues are part of the evidence; merge train and acceptance
   boundary before implementation.
2. `docs/development/feature-wave-2026-08-media-models.md` — later media/model
   companion. Establishes: an upstream workflow is not a Maestro dependency;
   product table of candidate → decision → Maestro outcome; obsolescence audit;
   do not add a second catalog, queue, or publication path.
3. `docs/research/H3-ecosystem-watch-2026-08-18.md` — later H3 tab-set intake.
   Establishes: freeze the current checkpoint; cluster competing nodes as one
   capability; “watch, not adoption”; user reports nominate probes, official or
   source evidence controls defaults; promotion gates.
4. `docs/development/minimax-h3-fast-runtime-research.md` — earlier supplied
   workflow dump. Establishes: inventory the actual graphs; compare strategy
   against Maestro’s existing named modes; complementary ideas stay separately
   named; community speed claims are not universal defaults.

If a new dump overlaps those notes, **merge into the existing decision record**
(or write a dated successor that cites what is still current) instead of
starting a parallel universe of the same candidates.

## Hard rules

- Preserve the raw dump as received. Do not drop a link silently; reject it
  with a reason.
- Prefer an existing Maestro capability when it already has stronger recovery,
  privacy, provenance, cancellation, or cross-platform behavior.
- Extract a useful invariant or technique. Do not import an upstream agent
  framework, Comfy graph, or runtime wholesale.
- Keep experimental runtimes and model artifacts opt-in until exact model,
  quality, cancellation, and resource gates pass.
- Preserve per-model and per-task engine choice. A faster engine or attention
  path for one model must not silently become the engine for every local model.
- Treat community performance claims as benchmark leads, not defaults.
- Keep locally processed creative content local. No candidate authorizes a new
  Maestro moderation, scanning, or third-party classification layer. See
  `AGENTS.md` **Local Content Neutrality**.
- Windows (or any other declared platform) is a separate acceptance target.
  Linux success is not Windows acceptance.
- Do not start local GPU/VRAM work during intake. Intake is source review and
  decision writing.

## Procedure

Run these steps in order. Stop after the intake note is written unless the
user explicitly asked to implement a decided slice.

### 1. Capture the dump

- Record date, source thread, and the list as given (URLs, files, screenshots,
  workflow JSON, model IDs).
- Classify each item: paper, repo, model card, LoRA/tune, attention/kernel,
  workflow/graph, social post, issue/comment, product demo, unrelated.
- Note any owner emphasis (“this one first,” “just FYI,” “we already have X”).

### 2. Normalize and expand just enough

For each unique source, collect public facts only:

- identity (name, revision, license, hardware envelope);
- what it claims to do;
- whether Maestro already covers that job under another name;
- **comments, issues, discussions, and model-card warnings** — these often
  contain the real constraints (silent fallbacks, VRAM, identity drift, broken
  speculative paths, license holes).

Do not scrape private tabs, private generations, or operator secrets.

### 3. Deduplicate

Collapse mirrors, forks, re-uploads, the same social post repeated, and
“new” wrappers around one artifact. Keep one canonical citation plus aliases
in the evidence index.

A later watch note that repeats an earlier feature-wave candidate is a
**refresh**, not a new product. Update status; do not re-litigate from zero
unless the source contract changed.

### 4. Filter

Drop or park before deep work when any of these hold:

- already shipped in Maestro under a truthful name;
- wrong product surface (coding-agent memory, provider router, training-only
  speedrun) with no Maestro user job;
- hardware or artifact envelope that cannot run on the intended host class
  (record as watch/reject, keep any transferable idea);
- missing or incompatible license for a managed catalog listing;
- requires a second operation manager, model catalog, project store, or
  output publication path;
- would add Maestro-side content inspection;
- depends on an open private URL or an unpinned “latest” nightlies as the
  only install path.

Filtered items still appear in the note with a one-line reason.

### 5. Cluster and merge

Group remaining items by **Maestro capability**, not by upstream brand.
Examples: segment planning / continuation; latent vs decoded upscale;
interpolation; face repair; local LLM engine; music generation; preview
decode; attention / step policy.

Competing repos that attack the same job become **one evaluation cluster**
(compare, then select or compose). Do not plan to install all of them.

### 6. Extract, do not copy

For each cluster, write the transferable idea in Maestro terms:

- What invariant is actually useful (audio-role split, seam-safe recovery,
  capability-gated engine, approximate preview vs delivery, …)?
- What must stay upstream-shaped and therefore stay out (Comfy node graphs,
  their compatibility shims, their UI)?
- What existing Maestro surface should grow instead of a parallel one?

Use the dump as a jumping-off point. The right Maestro design may share no
files with the source.

### 7. Decide

Every cluster gets exactly one decision:

| Decision | Meaning |
| --- | --- |
| **Adopt** | Integrate into Maestro, usually as a native slice, after a named plan |
| **Adapt / extract** | Take the invariant; do not depend on the upstream package |
| **Experiment** | Isolated, labeled, opt-in path; never a silent default |
| **Benchmark lead** | Add to a controlled A/B matrix; no profile or default change yet |
| **Watch** | Recheck on a stated trigger (release, distill, license, consumer envelope) |
| **Defer** | Useful later; blocked on an explicit dependency or wave |
| **Reject** | Not a Maestro module; record why and any leftover design prompt |

A saved link is not an endorsement. “Interesting” is not Adopt.

### 8. Prioritize and sequence

Write a merge train, not a pile:

- bird-in-the-hand user-visible milestones first;
- baseline fixtures and capability contracts before new engines or kernels;
- measured deltas against shipped profiles before default changes;
- catalog listings only after license, hash, family compatibility, and
  examples are complete;
- one writer per shared file or symbol cluster;
- explicit obsolescence audit for anything a candidate would replace.

Do not promote community step/attention anecdotes into defaults.

### 9. Brainstorm only when the remaining question is open

If several adaptions are still plausible, use `AGENTS.md` **Diversified
brainstorming**: at least two independent perspectives, then consolidate.
Do not fan out bounded citation lookups or spawn implementers.

### 10. Write the intake note, then stop

The intake is done when the note exists and every candidate has a decision.
Implementation is a later, separately owned wave.

## Where to write the note

| Dump shape | Destination |
| --- | --- |
| Broad mixed dump (engines, papers, workflows, models) | `docs/development/feature-wave-YYYY-MM.md`, plus a companion file if media/models would bury the first note |
| Domain refresh of an active watch (for example H3) | `docs/research/<topic>-watch-YYYY-MM-DD.md` |
| Single supplied workflow or kernel comparison | `docs/development/<topic>-research.md` |

Do not put live ports, host paths, account secrets, or private prompt/media
into these notes.

## Required sections in an intake note

1. **Status and date** — evidence captured; not an implementation claim.
2. **Current checkpoint** — what Maestro already does that this dump must not
   reimplement or silently overwrite.
3. **Decision rules** — copy or cite the hard rules above if they still apply.
4. **Per-candidate or per-cluster decisions** — decision + intended Maestro
   outcome + why, including comments/issues that changed the call.
5. **Next-wave order / merge train** — or an explicit “watch only” list.
6. **Watch, not adoption** — parked items and their recheck trigger.
7. **Public evidence index** — canonical URLs after dedup.
8. **What this note did not do** — no install, no download, no default change,
   no private data.

## Promotion gates (after intake, before it becomes product)

A later implementation wave may promote an experiment or watch item only with:

- isolated install and rollback plan;
- exact model, node, or kernel revisions;
- representative evidence on the intended GPU class (this host: RTX 5090),
  without treating one run as a universal default;
- private prompt/media boundaries;
- failure and cancellation settlement checks;
- an obsolescence audit against the path it replaces;
- truthful labeling when the path is approximate, creative, or platform-limited.

User reports can nominate a probe. Official or source-bound evidence controls
defaults.

## Acceptance boundary

The dump has been ingested when:

- the raw list is accounted for (kept, merged, or rejected with reason);
- no candidate depends on an open private tab as its only source;
- every cluster has an adopt / adapt / experiment / benchmark-lead / watch /
  defer / reject decision;
- the note starts from the shipped baseline;
- shared-file ownership and merge order are explicit if any adopt/adapt work
  is proposed;
- no research agent, memory framework, attention kernel, or workflow was
  imported merely because it was in the dump.

Then report the decisions to Hailey. Do not start the merge train unless that
was part of the ask.
