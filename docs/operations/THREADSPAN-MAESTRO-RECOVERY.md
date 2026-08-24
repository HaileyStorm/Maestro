# Threadspan Maestro recovery note

Date: 2026-08-17

This checkout remains the paused Maestro feature-wave source of record. Threadspan did not commit, reset, merge, or overwrite unrelated work.

## Why a recovery clone exists

Recursive scans of this NTFS3-mounted checkout repeatedly entered kernel D-state. Kernel logs reported `ntfs3` run-list warnings, and `ntfsfix` repaired an `$MFTMirr` record mismatch before the volume was remounted. Direct bounded reads, writes, fsync, unlink, and Git status passed after remount, but recursive sentinel discovery still reproduces the driver stall.

The verified staging clone is:

`/home/hailey/AI/Maestro-continuum-recovery`

The original tracked dirty patch and base commit are preserved under:

`~/.codex/rollback/harness-71a/20260817T1815Z-linux/maestro-recovery`

## Deployed account-era fixes

The live checkout received only the files reserved by `harness-71a-maestro-deploy`:

- exact registered-origin CORS for the Quick Tunnel and stable Worker;
- mandatory account sign-in before project or creative surfaces;
- monotonic account-project migration state;
- passwordless account-owned project creation after migration;
- opaque 404 responses for legacy password routes and cross-account resources;
- rebuilt `ui/dist` from the tested recovery clone.

The previous compiled UI remains as `ui/dist.threadspan-before-20260817T235003Z` for rollback. The complete source rollback snapshot is recorded in:

`~/.codex/rollback/harness-71a/20260817T1815Z-linux/maestro-live-deploy-20260817T235003Z`

## Ownership boundary

`ui/src/stores/useStore.ts` is reserved by `Maestro.git-director-auto-default-off`. Threadspan did not copy its build-only cleanup back into this checkout. The deployed bundle was built from the recovery clone, where an unused import was removed so TypeScript could compile. Before the paused owner rebuilds UI, reconcile that one cleanup with its existing store work.

Do not delete account auth, migration, membership, session-secret, or recovery-code artifacts during rollback. Restoring legacy access means restoring the saved environment/UI/source and restarting; it does not mean resetting account state.
