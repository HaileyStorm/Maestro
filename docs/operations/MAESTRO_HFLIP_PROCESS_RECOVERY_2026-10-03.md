# Gallery flip process recovery — 2026-10-03

The Gallery horizontal-flip worker now has a real POSIX process-death
qualification in addition to the existing simulated crash checks. No
production behavior changed for this milestone.

## Observed results

| Crash boundary | Fresh worker result | Encoder calls across both workers |
| --- | --- | --- |
| Media and sidecar published; completion not persisted | Adopted the sealed pair; completed the same persisted job | 1 |
| Sidecar published; media not promoted | Removed the exact orphan marker; published one result | 2 |
| Sealed pair published; source sidecar then changed | Failed before adoption; preserved the pair | 1 |

Each killed worker reached an explicit publication barrier after its CPU
encoder had finished. The parent sent SIGKILL to that exact owned worker,
confirmed its signal exit, and launched another process. The replacement
loaded the running job from the actual revision-fenced `QueueRecoveryJournal`;
the completion was absent from the journal before recovery.

The successful cases used a real one-second, 12-frame H.264/AAC source.
Assertions checked every decoded pixel against the horizontal reversal of
the original, within a mean channel error below 3 for the lossy encode.
Decoded PCM audio was identical. The source media hash stayed unchanged.
Recovered completion listed exactly one output with private/final metadata,
its media hash and the original source revision.

Adoption preserved both published files' hashes, inodes, sizes and modification
times. Sidecar-only recovery preserved a deliberately unrelated marker.
Source-change rejection preserved the sealed pair and returned no output.

## Verification and evidence ceiling

Run the focused checks from the repository root:

```sh
PYTHONPATH=app:tests app/env/bin/python -m unittest discover \
  -s tests -p 'test_tool_process_crash.py' -v
```

All three focused cases passed. An initial run also passed the 25 adjacent
input/publication checks because of an imported test-class discovery issue;
the new module now imports the fixture module rather than exposing its class,
so discovery runs only the three new cases. No full suite was rerun, following
the owner's request to use full suites less often.

These tests execute the production flip worker, input validation, sealed
publication, cursor reconciliation and adoption functions extracted from
`app/launch.py`, the actual CPU transform, and the actual journal. Application
locks, route authorization, manifest lookup, owner/project identity and
lifecycle callbacks use fixture wiring. Requeue is explicit synthetic wiring;
the tests do not exercise live owner reauthentication or the queue supervisor.
Workers are separate forked processes, not fresh interpreter imports.

This evidence qualifies process/filesystem recovery at the tested publication
boundaries. Whole-service crash/restart acceptance, Windows behavior, and
human playback acceptance remain open. A killed worker can leave its private
staging directory; this milestone does not claim abandoned staging cleanup.
The harness removes only its own disposable temporary tree after each test.

Private evidence is retained under the milestone's local artifact directory:
focused test output, per-case journal snapshots, media, sidecars and receipts.
The live service was left running; local and stable-share health/readiness
checks returned 200. No model was loaded and no GPU work was performed.
