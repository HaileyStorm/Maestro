# Future storage tiers

Maestro has a dormant, inspection-only storage plan for the two anticipated
drives. Nothing here mounts a drive, creates a directory, copies data, changes
an environment variable, edits `wgp_config.json`, or moves existing TVBox
content.

## Intended roles

| Role | Intended medium | Purpose |
| --- | --- | --- |
| `hot` | Existing NVMe | Active temporary files and latency-sensitive scratch space |
| `warm_models` | New SSD | Primary model/checkpoint and reusable model-cache storage |
| `warm_bulk` | Future fast HDD | Generated outputs and other large, write-active bulk data |
| `cold` | Fast HDD after verified migration; TVBox initially/for overflow | Existing models kept as read-only linked checkpoints and other cold data |

The future fast HDD can later take over some cold data after a verified copy;
there is no requirement to migrate all existing TVBox content. Roles are
logical and do not guess a mount point, device name, filesystem, or capacity.

## Owner-supplied plan

Keep the JSON file outside the repository (or in an explicitly ignored local
location) and set its absolute path only in the ignored `ENVIRONMENT` file:

```text
MAESTRO_STORAGE_TIER_PLAN_FILE=/absolute/path/chosen/after/the/drives/arrive.json
```

Start with unbound roots. Replace `null` only after the exact mounted roots and,
when used, filesystem identities have been verified on this host:

```json
{
  "schema_version": 1,
  "tiers": {
    "hot": {"root": null, "write_intent": "read_write"},
    "warm_models": {"root": null, "write_intent": "read_write"},
    "warm_bulk": {"root": null, "write_intent": "read_write"},
    "cold": {"root": null, "write_intent": "read_only"}
  }
}
```

An optional identity object can pin a bound role to the filesystem actually
inspected rather than a potentially reused mount path:

```json
"identity": {"filesystem_uuid": "owner-verified-filesystem-uuid"}
```

`partition_uuid` is also supported. A supplied identity must match the
read-only `findmnt` observation; if identity cannot be observed, inspection
fails closed. Bound roots must already exist, be absolute directories, and
resolve to disjoint locations. Any symlink path component in a root or proposed
child binding, duplicate root, or parent / child overlap is rejected. The
inspector never tests writability by writing.

Run the read-only inspection from `app/`:

```bash
python scripts/storage_tier_plan.py
```

No configured file is a normal state and reports `not_configured` with exit
code 0. A valid plan with pending roots reports `unbound`; a fully bound valid
plan reports `ready`. Invalid or drifted bindings exit 2.

## Proposed bindings, not live settings

The report proposes this fixed layout beneath the owner-bound role roots:

- `HF_HOME`, `TORCH_HOME`, and `MAESTRO_LLM_CACHE` under `warm_models`.
- `GRADIO_TEMP_DIR` under `hot`.
- WGP checkpoint primary under `warm_models` as its writable first
  `checkpoints_paths` entry.
- The `cold` checkpoint location as a later read-only linked entry. This keeps
  useful TVBox models searchable without making linked content a write target.
- WGP `save_path` under `warm_bulk`.

Every proposal includes `apply: false` and reports its directory as `ready`,
`missing`, `unbound`, `unsafe_symlink`, or `unsafe_escape`. A missing proposed
child is not created. Binding and directory creation belong to the later
cutover, after the final drive layout is known.

## Future cutover checklist

1. **Identify** — record the exact mounted roots and stable filesystem or
   partition identities; confirm the plan is `ready` and contains no aliases or
   overlaps.
2. **Quiesce** — stop generation and downloads; preserve the current
   environment and `wgp_config.json` as rollback inputs.
3. **Copy** — copy one bounded data class at a time. Do not delete the source.
4. **Verify** — compare file counts, byte counts, and content hashes for the
   copied class; confirm ownership and free-space headroom.
5. **Cut over** — separately authorize and apply only the verified environment
   and WGP bindings, keeping cold checkpoints linked read-only.
6. **Accept** — start Maestro, verify catalog visibility, a small download,
   output creation, and rollback reachability. GPU inference acceptance remains
   a separate gate.
7. **Retain rollback** — keep the source copy unchanged for an agreed window.
   On any mismatch, stop writes and restore the preserved bindings.
8. **Retire selectively** — remove an old copy only after explicit target
   review and approval. Existing TVBox content may remain as cold/overflow.
