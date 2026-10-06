"""Local loading contract for the pinned original H3 FL2VA transformer.

The original export has native tensor names and head-interleaved fused QKV.
Read its explicit shards instead of MMGP's filename expansion, which in some
supported versions repeats shard 1 for indices 10 and above. No acquisition,
model-terms acceptance, profiling or GPU dispatch occurs here.
"""

from pathlib import Path
from contextlib import contextmanager
import json
import os
import re
import stat

import torch
from safetensors import safe_open


ORIGINAL_BASE_REPOSITORY = "MiniMaxAI/MiniMax-H3"
ORIGINAL_BASE_REVISION = "5d9b308a59ab12e67147f191e184baf704185bd1"
ORIGINAL_BASE_INDEX_SHA256 = "fb457a26ffa6294660e249b0ddd03a337f2e5393f770b5c34c8b8f90a29a7efb"
# Immutable LFS metadata for FL2VA/transformer, in shard order.
ORIGINAL_BASE_SHARDS = (
    ("model-00001-of-00013.safetensors", 5227812968, "0b3386565e476bfdea287e9ea9f269d036e5c649ed14bb9b4afac1dc4661bd2a"),
    ("model-00002-of-00013.safetensors", 5164578856, "9c98fd4579c9bc96d1b1bbf65c1243f9256df60bccd229570cecc61897773003"),
    ("model-00003-of-00013.safetensors", 5164578872, "fa484c940d4170199fb69f5016739df2daf6ecd1150631b12a297e06e3964730"),
    ("model-00004-of-00013.safetensors", 5164578896, "4a851df37cce2d14b39cb58df41def7b591b1f11b8520a32b421a5fa3e997c9f"),
    ("model-00005-of-00013.safetensors", 5164578896, "3fe6ff94dc9d3107c6776a25277d2b72b5443f88cf0f4ed835fd5b65a94ea5d4"),
    ("model-00006-of-00013.safetensors", 5164578896, "79b47e1b9ff03a2c6f06dd15a28972acf32c0867172ee34e5aa6e96b99494127"),
    ("model-00007-of-00013.safetensors", 5164578896, "6ac4d6ce639786b722a8dd99843ed56dcab48a0d174a4b0f9b8cdcbe5160fb0f"),
    ("model-00008-of-00013.safetensors", 5164578896, "03531812243fb0a333e01efa7e3d39750580bca8cbf2ba0529da419278eab736"),
    ("model-00009-of-00013.safetensors", 5164578896, "7e82a80b0d0d267026eb841b81523069cc7b956c1ebd8a18c07e462ffb262487"),
    ("model-00010-of-00013.safetensors", 5164578896, "ef0a1f6b65145232543dd2232d58d91a4d31f44b0da5084acf22cea123255481"),
    ("model-00011-of-00013.safetensors", 5164578896, "bf885b8f2f078c5499b75fded21094a26bfe0e6e00bee276e499443ecd2f298d"),
    ("model-00012-of-00013.safetensors", 5164578896, "dc717572d014ebf8527e35af815d10eb2f5a6bc87253b8a7d2f456965192db69"),
    ("model-00013-of-00013.safetensors", 4242305176, "8bfd852d5817e9836de1d3ec8dbac1c5446b167568b717371f08282b22291aa2"),
)


@contextmanager
def _open_captured(captured):
    """Hold the captured regular file through reads, without blocking on FIFOs."""
    from services.h3_runtime_binding import H3RuntimeBindingError, _signature

    if os.name == "nt":
        # A Windows read handle that denies write/delete sharing pins the
        # pathname while safetensors opens its own read mapping.
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        handle = create(captured.resolved, 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle(handle)
            raise
        reader_path = captured.resolved
    else:
        descriptor = os.open(captured.resolved, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                             | getattr(os, "O_NOFOLLOW", 0))
        # Linux and macOS safetensors can open an alias of the held descriptor,
        # so a pathname replacement cannot redirect its second open.
        reader_path = next((str(Path(root) / str(descriptor)) for root in ("/proc/self/fd", "/dev/fd")
                            if Path(root).is_dir()), None)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or _signature(info) != captured.signature:
            raise H3RuntimeBindingError("Original H3 base file changed before opening")
        if reader_path is None:
            raise H3RuntimeBindingError("Original H3 base requires descriptor-backed safetensors access")
        captured.verify()
        yield descriptor, reader_path
        if _signature(os.fstat(descriptor)) != captured.signature:
            raise H3RuntimeBindingError("Original H3 base file changed while reading")
        captured.verify()
    finally:
        os.close(descriptor)


def is_original_base_checkpoint(filename):
    paths = filename if isinstance(filename, (list, tuple)) else [filename]
    return any(re.fullmatch(r"model-\d{5}-of-00013\.safetensors", Path(p).name) for p in paths)


def load_original_base_into_model(model, filename, *, reorder_qkv, dtype, interrupted=None):
    """Load only the complete, pinned export and return its frozen file evidence."""
    from mmgp import offload
    from services.h3_runtime_binding import _hash_file

    if dtype not in (torch.bfloat16, torch.float32):
        raise ValueError("Original H3 base requires BF16 or FP32 native arithmetic")
    def check_cancelled():
        if interrupted is not None and interrupted():
            raise InterruptedError("Original H3 base loading cancelled")

    paths = filename if isinstance(filename, (list, tuple)) else [filename]
    paths = [Path(p).absolute() for p in paths]
    names = tuple(row[0] for row in ORIGINAL_BASE_SHARDS)
    if not paths or any(p.parent != paths[0].parent for p in paths):
        raise ValueError("Original H3 base shards must share one directory")
    if len(paths) == 1:
        if paths[0].name not in names:
            raise ValueError("Original H3 base requires a pinned shard filename")
    elif len(paths) != len(names) or set(p.name for p in paths) != set(names):
        raise ValueError("Original H3 base requires the complete pinned shard roster")
    directory = paths[0].parent
    index = directory / "model.safetensors.index.json"
    # Reject MMGP's implicit sidecar discovery, including dangling links.
    def verify_sidecars():
        for name in names:
            if os.path.lexists(directory / (name.removesuffix(".safetensors") + "_map.json")):
                raise ValueError("Original H3 base does not accept quantization sidecars")

    check_cancelled()
    verify_sidecars()
    # Inventory all sizes before reading tens of GB or materializing tensors.
    for name, size, _digest in ORIGINAL_BASE_SHARDS:
        if (directory / name).stat().st_size != size:
            raise ValueError("Original H3 base shard size differs from the pinned export")
    if not 0 < index.stat().st_size <= 1024 * 1024:
        raise ValueError("Original H3 base index has an unsafe size")
    index_evidence = _hash_file(index, interrupted)
    if index_evidence.sha256 != ORIGINAL_BASE_INDEX_SHA256:
        raise ValueError("Original H3 base index differs from the pinned export")
    with _open_captured(index_evidence) as (descriptor, _reader_path):
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw_index = stream.read(1024 * 1024 + 1)
    if len(raw_index) != index_evidence.size:
        raise ValueError("Original H3 base index changed during loading")
    weight_map = json.loads(raw_index)["weight_map"]
    index_evidence.verify()
    if not isinstance(weight_map, dict) or set(weight_map.values()) != set(names):
        raise ValueError("Original H3 base index has an incomplete shard roster")
    expected = model.state_dict()
    if set(weight_map) != set(expected):
        raise ValueError("Original H3 base index does not match the native tensor set")
    evidence = [index_evidence]
    for name, size, digest in ORIGINAL_BASE_SHARDS:
        captured = _hash_file(directory / name, interrupted)
        if captured.size != size or captured.sha256 != digest:
            raise ValueError("Original H3 base shard differs from the pinned original bytes")
        evidence.append(captured)

    def verify():
        check_cancelled()
        verify_sidecars()
        for captured in evidence:
            captured.verify()

    verify()
    state = {}
    for captured in evidence[1:]:
        check_cancelled()
        with _open_captured(captured) as (_descriptor, reader_path), \
             safe_open(reader_path, framework="pt", device="cpu") as reader:
            metadata = reader.metadata() or {}
            if "quantization_map" in metadata or "tied_weights_map" in metadata:
                raise ValueError("Original H3 base does not accept quantization or tied-weight maps")
            keys = set(reader.keys())
            indexed = {key for key, shard in weight_map.items() if shard == Path(captured.requested).name}
            if keys != indexed:
                raise ValueError("Original H3 base shard tensors differ from the sealed index")
            for key in keys:
                check_cancelled()
                tensor = reader.get_tensor(key)
                if tensor.shape != expected[key].shape or tensor.dtype not in (torch.float32, torch.bfloat16):
                    raise ValueError(f"Original H3 base tensor geometry/dtype mismatch: {key}")
                state[key] = tensor
        captured.verify()
    verify()

    def preprocess(state_dict, quantization_map, tied_weights_map):
        if quantization_map or tied_weights_map:
            raise ValueError("Original H3 base requires unquantized, untied tensors")
        verify()
        return reorder_qkv(state_dict)

    offload.load_model_data(
        model, (state, {}, {}), writable_tensors=False,
        preprocess_sd=preprocess, default_dtype=dtype,
        pre_load_callback=lambda _model: verify(),
    )
    verify()
    if any(value.device.type == "meta" for value in model.state_dict().values()):
        raise ValueError("Original H3 base left unloaded native tensors")
    return tuple(evidence)
