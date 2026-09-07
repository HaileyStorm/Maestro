# Maestro MMGP DoRA protocol package

This PEP 517 source package builds `mmgp==3.7.12+maestro1` from the exact
MMGP 3.7.12 wheel pinned by Maestro. The build verifies the upstream wheel,
checks the exact `mmgp/offload.py` preimage, and applies Maestro's bounded
`_mm_effective_weight_input_scale` protocol patch. No MMGP source tree is
vendored here.

The protocol lets a quantized module publish a one-dimensional effective
weight input scale. MMGP applies that scale to the materialized base weight
before it merges ordinary LoRA deltas and performs its existing DoRA blend.
Zero-strength DoRA keeps the native module forward path. Non-finite or
mis-shaped scales and an effective DoRA/LoKr combination fail explicitly.

`backend.py` uses only the Python standard library. It downloads no more than
the bounded upstream wheel, validates every archive member and the complete
upstream `RECORD`, preserves package files, license, dependencies, and metadata
apart from the local version, and replaces the stale setuptools `WHEEL`
generator field with the actual backend identity. It then emits a deterministic
wheel with a complete new `RECORD` and a provenance document.

The upstream wheel's GPL-3.0 `LICENSE.md` member is retained byte-for-byte in
the rebuilt wheel.

The standalone dependency rollback is an exact no-dependency reinstall of the
upstream URL and SHA-256 exposed as `UPSTREAM_URL` and `UPSTREAM_SHA256` in
`backend.py`:

```shell
uv pip install --no-deps --reinstall "mmgp @ https://files.pythonhosted.org/packages/d1/da/df5d4be821577120eb4370dbbce9bbdd87e1fb4aa65e37c8dba0916ae1ea/mmgp-3.7.12-py3-none-any.whl#sha256=2cfb809c1000a0945101c885c687e68ad44eb37278a373a3d65b8ce747f222cf"
```

That command alone is not a complete Maestro rollback: the same change must
restore Maestro's MMGP version gate and requirements pin to 3.7.12 and remove
the qtype-side protocol publication. Remove this package recipe once an
upstream MMGP release with an equivalent reviewed protocol has passed Maestro's
CPU and GPU acceptance and the requirements pin has moved to that release.
