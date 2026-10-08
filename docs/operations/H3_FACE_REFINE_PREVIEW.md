# Face repair source reader

`app/services/h3_face_refine_preview.py` prepares the CPU source-reading part of
the [reviewed Gallery editor](H3_FACE_REFINE_UI.md). It is an internal helper;
the GET routes and catalog capability remain unwired. Installing this source
does not activate face repair or prove native face quality.

`read_face_source(resolved_path, cancel_check=...)` returns an immutable source
record. `public_facts()` provides measured width, height, frame count, canonical
24-fps clock and zero-based audio ordinals with bounded generated labels.
The internal record also binds the private snapshot's digest and size.
Source titles and arbitrary metadata never become labels.

`read_face_frame(resolved_path, frame_index, expected_source=..., cancel_check=...)`
returns the measured record and a lossless RGB PNG. Selection uses an integer
decoded frame ordinal, including the exact first and last frames. It performs
no approximate timestamp seek, resampling, resizing or substitute-frame return.
FFV1 sources use the same CPU path as browser-compatible source codecs.

Both functions reuse the existing bounded, no-follow, single-link regular-file
snapshot and owned FFmpeg/FFprobe child lifecycle. An original-file identity
check also covers changes during inspection and decoding. A supplied source
record must match digest, size, facts and audio listing completely. A single
30-second operation budget covers copying, inspection, decoding and PNG
encoding; cancellation and timeout still drain the owned decoder before
removing its private snapshot. This is a cooperative budget checked between
work chunks, rather than a hard return-time guarantee; the existing child
termination grace also applies.

The reader accepts the worker's zero-start 24-fps CFR clock and H3 Base frame
lengths 124–345 of the form `17n+5`. Encoded sources are limited to 64 MiB.
Packet-count geometry rejects decoded RGB sizes above 512 MiB before full
frame inspection. Audio selection is limited to 16 ordered tracks. Non-square
display pixels and display matrices are rejected until the crop worker and
editor can share an explicitly supported display transform. This is an
input-shape restriction, without inspecting creative subject matter.

The eventual HTTP adapter must resolve only the authorized final Gallery
source, require the FaceRefine flags and current model/project access, bind the
exact listing revision, and recheck revision, access and privacy before sending
the response. It must connect request cancellation, bound concurrent reads and
send private responses with `Cache-Control: no-store`. The helper accepts a
resolved path from that trusted adapter; it does not implement authorization,
Gallery resolution, a public file endpoint, GPU admission or media publication.
Those adapter changes must wait for closure of any qualification that pins it.

CPU media regressions compare exact decoded pixels for MOV and FFV1 first,
interior and last frames; exercise two audio ordinals; and reject changed
sources, foreign bindings, links, invalid indices, unsupported clocks/lengths,
display transforms and resource overflow. Cancellation and aggregate timeout
checks retain distinct evidence from live HTTP, browser, Queue, native model,
LAN, physical-device and human quality acceptance.
