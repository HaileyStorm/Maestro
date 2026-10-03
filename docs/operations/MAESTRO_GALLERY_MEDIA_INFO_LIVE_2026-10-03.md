# Gallery Media Info: signed-in browser check — 2026-10-03

The current local signed-in Gallery now has a browser receipt for image/video
measurements, private reveal and selection changes. Existing retained outputs
were reused; no model was loaded, no media was generated, and no production
source changed.

## Observed states

| Selected media | Visible Media Info | Independent file facts |
| --- | --- | --- |
| H3 H.264 browser copy | 1344 × 768 · 5.17s · 456 KB | 1344 × 768, 5.166667 seconds, 466,532 bytes |
| Native H3 HEVC unsupported by this browser | 2 MB; actionable browser-copy/download message | 2,377,655 bytes; no successful browser decode claimed |
| Private Editor output before reveal | No Media Info; no video element mounted | Private reveal gate retained |
| Same private output after Show preview | 608 × 352 · 4s · 173 KB | 608 × 352, 4 seconds, 176,955 bytes |
| PNG selected after the video | 848 × 480 · 342 KB; no prior video duration | 848 × 480, 350,168 bytes |

Browser video metadata and image natural dimensions matched FFprobe on the
retained files. Switching from H.264 to unsupported HEVC removed the previous
decoded dimensions/duration. Switching from the revealed private video to PNG
removed its duration and video element. Loaded URLs included the selected
project and listing revision.

All four source-media files and their four sidecars retained identical hashes,
sizes and modification times. Private receipts preserve the exact selected
files, browser states, URL revisions, FFprobe facts and screenshots.

## Limits and closure

This is local signed-in browser evidence. Stable-share health/readiness was
verified separately in the preceding milestone; this check does not claim a
signed-in remote Gallery session, Windows behavior, or human playback/quality
acceptance. A live stale-file replacement check remains open. The listing
revision is still an mtime/size token, not a cryptographic content identity.

No full suite was repeated for this evidence-only milestone. The retained
browser observations and before/after file receipts were checked directly,
with the publication guard and diff check before closure. The historical
tracker and foreign dirty work were preserved. Launcher gates are inapplicable
because no launcher was edited. The broader sprint remains active.
