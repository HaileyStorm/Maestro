# Maestro owner checklist

This is the short list of actions and decisions that genuinely need the owner.
Engineering work belongs in Beads, not here. Update this file whenever an owner
gate is added, answered, completed, or removed.

Last reviewed: 2026-08-15

## Actions for you now

There is no required website click or credential step right now.

- **MiniMax Music 3:** its Hugging Face repository is public and ungated, so
  there are no Hugging Face model terms to click through. Before Maestro
  downloads or runs it, review the upstream Community License and acceptable-use
  terms and answer the local-use question below. Maestro will keep the model
  unavailable until that server-owned gate exists.
- **GPU scheduling:** no action is needed yet. Maestro will ask both Palimpsest
  tasks for a bounded 5090 window only when a real Music 3 install or benchmark
  is ready. Development and benchmarking have priority; sample generation does
  not.

## Questions for you

### Unanswered

1. After reviewing the [MiniMax Music 3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE),
   do you approve a **local-only** installation and benchmark on this computer,
   with visible `MiniMax-Music3` attribution and no LAN or Cloudflare exposure
   until the separate hosted-service terms conflict is resolved?

### Answered

- **Character Sheet product direction:** use a FLUX-generated/imported anchor;
  make Quad FLUX.2 Klein the conservative default; expose Krea choices only as
  explicit dropdown choices; keep Dynamic Krea 2 experimental; use the local
  VLM plus Qwen Image Edit for review and targeted repair.
- **MiniMax Music 3 priority:** required feature, built incrementally rather
  than treated as optional research.
- **GPU priority:** Maestro development, benchmarking, and experiments have top
  local-GPU priority, but Maestro must coordinate with both Palimpsest tasks and
  must not monopolize the 5090 for an extended period. Demo/sample generation
  has bottom priority. Rented compute is not authorized.
- **Direct compute contribution:** keep it locked until verified net USD
  development-cost recovery reaches exactly **$1,000**.
- **Account safety:** the single owner account must not be disableable.

### Deferred until the feature exists

- Whether to pursue a separate written MiniMax license for H3 in the United
  States. Maestro will preserve existing H3 code and outputs while blocking new
  execution. Ask only when there is a concrete licensing route to evaluate.
- Whether to pursue Krea 2 license clarification. Ask only when the exact
  technical profile is ready and the license-versus-content-neutrality conflict
  can be presented as a bounded decision.
- Whether Music 3 should later be exposed over LAN or Cloudflare. Its hosted
  service obligations are not yet compatible with Maestro's current local
  content-neutrality policy.
- Human listening acceptance for Music 3 output quality and control adherence.
- Human visual acceptance for Character Sheet identity, panel consistency, and
  repair quality.

## Blockers that are not ordinary acceptance clicks

- **MiniMax H3:** the current upstream license excludes the United States from
  its applicable territory. Accepting generic Hugging Face or Ref2VA terms does
  not authorize H3 here. A separate written MiniMax license is required before
  new inference, benchmarking, training, download, or recovery execution.
- **Character Sheet with FLUX.2 Klein:** the base model is gated and
  non-commercial. Maestro must bind the exact base revision and present its
  terms before enabling that profile; do not accept a guessed repository or a
  generic substitute yet.
- **Character Sheet with Krea 2:** both the base-model license and the
  Character Sheet artifact terms need exact binding. The current content-filter
  obligation conflicts with Maestro policy, so a click alone would not clear
  the blocker.
- **Oracle:** the requested expert first-draft path is blocked by the Oracle
  tool's disconnected-Git-root identity check. No project material was
  submitted and no user action is currently useful; the shared harness needs a
  reviewed project-identity repair.

## No action needed

- The stable Cloudflare Worker is deployed, live-checked, and Wrangler was
  logged out after deployment.
- Existing projects are already attached to the active owner account. Do not
  rerun project migration or use the obsolete Connect existing projects flow.
- The reviewed offline status copy is already in the deployed Worker. A real
  outage remains the honest live acceptance opportunity; Maestro should not
  manufacture one merely to produce evidence.
- Beads is operating on the managed tracker. Do not initialize or migrate it.
