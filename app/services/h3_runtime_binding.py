"""Private content identity for files actually consumed by an H3 loader.

Snapshots bracket loading with unchanged-file checks. They do not inspect live
parameter values or attest against hostile mutation inside the same process.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import marshal
import os
import stat
import sys
import types
import weakref
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

_MAX_FILES = 80
_MAX_FILE_BYTES = 1024**4
PROCESSOR_FILES = frozenset(
    {
        "chat_template.json",
        "merges.txt",
        "preprocessor_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "video_preprocessor_config.json",
        "vocab.json",
    }
)
_PACKAGES = (
    "torch",
    "diffusers",
    "transformers",
    "accelerate",
    "mmgp",
    "safetensors",
    "numpy",
    "tokenizers",
    "Pillow",
    "huggingface-hub",
)
_OPTIONAL_PACKAGES = ("comfy-kitchen", "flash-attn", "sageattention", "triton")
# MMGP fills these discovery caches during its first real checkpoint load.
# Their initial None value is JSON, but their populated qtype/class/set values
# are not. Neither representation is an implementation constant. Keep this
# exact module/name list narrow: routing priorities, functions and defaults
# still bind the implementation, and selected modules bind the loaded layout.
_IMPLEMENTATION_RUNTIME_STATE = {
    "mmgp.quant_router": ("_QTYPE_QMODULE_CACHE", "_QMODULE_BASE_ATTRS"),
}


class H3RuntimeBindingError(ValueError):
    pass


def _canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError):
        raise H3RuntimeBindingError(
            "H3 runtime contract is not finite JSON data."
        ) from None


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _code_bytes(code):
    # Paths and line numbers do not alter the runtime contract. Preserve actual
    # loaded bytecode/constants rather than re-reading possibly edited source.
    # Format 3+ records object sharing: lazy imports can change serialization
    # even when the code object and all its semantic fields remain identical.
    # Format 2 removes that reference-graph dependence. Encode constants
    # separately so unordered frozensets also survive a fresh hash seed.
    # Older saved binding digests remain mismatches; never reinterpret them.
    return marshal.dumps(
        (
            "code",
            marshal.dumps(
                code.replace(co_filename="", co_firstlineno=1, co_consts=()), 2
            ),
            tuple(_constant_bytes(value) for value in code.co_consts),
        ),
        2,
    )


def _constant_bytes(value):
    # Tags preserve container/type identity; sort encoded set members only.
    # Scalar marshal bytes retain signed zero, float bits and binary values.
    if isinstance(value, types.CodeType):
        return _code_bytes(value)
    if isinstance(value, tuple):
        encoded = ("tuple", tuple(_constant_bytes(item) for item in value))
    elif isinstance(value, frozenset):
        encoded = ("frozenset", tuple(sorted(_constant_bytes(item) for item in value)))
    else:
        encoded = ("scalar", marshal.dumps(value, 2))
    return marshal.dumps(encoded, 2)


def implementation_sha256(modules):
    """Hash loaded Python implementations and serializable module constants."""
    entries = {}

    def data(value):
        if isinstance(value, dict):
            return {str(key): data(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [data(item) for item in value]
        if value is None or type(value) in (str, int, float, bool):
            return value
        if type(value).__module__ == "torch" and type(value).__name__ == "dtype":
            return {"torch_dtype": str(value)}
        if inspect.isclass(value) or inspect.isfunction(value):
            return {"callable": value.__module__ + "." + value.__qualname__}
        # Unsupported runtime objects are not represented by repr (which may
        # contain paths or addresses). The explicit loaded contract handles
        # selected tensor/model data; the code fingerprint covers its defaults.
        return {"type": type(value).__module__ + "." + type(value).__qualname__}

    def visit(prefix, value, seen=frozenset()):
        if id(value) in seen:
            raise H3RuntimeBindingError("H3 implementation wrapper contains a cycle.")
        seen = seen | {id(value)}
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if isinstance(value, property):
            for name in ("fget", "fset", "fdel"):
                visit(prefix + "." + name, getattr(value, name), seen)
        elif inspect.isfunction(value):
            entries[prefix] = hashlib.sha256(_code_bytes(value.__code__)).hexdigest()
            entries[prefix + ".defaults"] = hashlib.sha256(
                _canonical(data((value.__defaults__, value.__kwdefaults__)))
            ).hexdigest()
            wrapped = getattr(value, "__wrapped__", None)
            if wrapped is not None and wrapped is not value:
                visit(prefix + ".wrapped", wrapped, seen)
        elif inspect.isclass(value):
            for name, member in vars(value).items():
                if inspect.isfunction(member) or isinstance(
                    member, (staticmethod, classmethod, property)
                ):
                    visit(prefix + "." + name, member, seen)
                elif not name.startswith("__"):
                    try:
                        encoded = _canonical(member)
                    except H3RuntimeBindingError:
                        continue
                    entries[prefix + "." + name] = hashlib.sha256(encoded).hexdigest()

    for module in modules:
        for name, value in vars(module).items():
            if name in _IMPLEMENTATION_RUNTIME_STATE.get(module.__name__, ()):
                continue
            if getattr(value, "__module__", None) == module.__name__:
                visit(module.__name__ + "." + name, value)
            elif name.isupper():
                try:
                    encoded = _canonical(value)
                except H3RuntimeBindingError:
                    continue
                entries[module.__name__ + "." + name] = hashlib.sha256(
                    encoded
                ).hexdigest()
    if not entries:
        raise H3RuntimeBindingError("H3 loaded implementation identity is missing.")
    return hashlib.sha256(
        _canonical({"python": sys.version, "entries": entries})
    ).hexdigest()


def installed_runtime_versions():
    try:
        versions = {name: metadata.version(name) for name in _PACKAGES}
    except metadata.PackageNotFoundError:
        raise H3RuntimeBindingError(
            "H3 private recovery requires installed runtime version evidence."
        ) from None
    for name in _OPTIONAL_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def diffusers_config_contract(config):
    """Keep config values bound; normalize only Diffusers' default-name set."""
    result = dict(config)
    if "_use_default_values" in result:
        names = result["_use_default_values"]
        if (
            type(names) is not list
            or any(type(name) is not str or not name for name in names)
            or len(set(names)) != len(names)
        ):
            raise H3RuntimeBindingError(
                "H3 Diffusers default-field metadata is invalid."
            )
        # ConfigMixin creates this list from a set. Its process-dependent order
        # has no model meaning; values and all other sequence orders stay exact.
        result["_use_default_values"] = sorted(names)
    return result


def tensor_layout_sha256(components):
    """Bind effective post-offload types/shapes/dtypes, never read tensor values."""
    entries = []
    for index, component in enumerate(components):
        loras = getattr(component, "_loras_model_data", None)
        # MMGP installs {owned_submodule: {}} before any adapter is loaded.
        # Empty support hooks delegate to the original forward unchanged.
        # Reject actual adapter entries and uncertain metadata shapes.
        if loras is not None and (
            type(loras) is not dict
            or any(type(value) is not dict or value for value in loras.values())
        ):
            raise H3RuntimeBindingError(
                "H3 private recovery requires no loaded LoRA adapters."
            )
        for kind, method in (
            ("parameter", "named_parameters"),
            ("buffer", "named_buffers"),
        ):
            for name, tensor in getattr(component, method)():
                entries.append(
                    (index, kind, name, list(tensor.shape), str(tensor.dtype))
                )
        module_ids = set()
        for name, module in component.named_modules():
            module_ids.add(id(module))
            adapter_data = getattr(module, "_mm_lora_data", None)
            if adapter_data is not None and (
                type(adapter_data) is not dict or adapter_data
            ):
                raise H3RuntimeBindingError(
                    "H3 private recovery requires no loaded LoRA adapters."
                )
            if (
                getattr(module, "_h3_turbo_prepared", False)
                or getattr(module, "_h3_turbo_active", False)
                or getattr(module, "_h3_turbo_lora", None) is not None
            ):
                raise H3RuntimeBindingError(
                    "H3 private recovery requires no Turbo overlays."
                )
            entries.append(
                (
                    index,
                    "module",
                    name,
                    type(module).__module__,
                    type(module).__qualname__,
                )
            )
        if loras is not None and any(id(module) not in module_ids for module in loras):
            raise H3RuntimeBindingError(
                "H3 private recovery requires owned empty LoRA support hooks."
            )
    return hashlib.sha256(_canonical(entries)).hexdigest()


@dataclass(frozen=True)
class _FileEvidence:
    requested: str
    resolved: str
    signature: tuple
    size: int
    sha256: str

    def verify(self):
        try:
            if str(Path(self.requested).resolve(strict=True)) != self.resolved:
                raise H3RuntimeBindingError(
                    "H3 runtime asset resolution changed during loading."
                )
            info = os.stat(self.resolved, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or _signature(info) != self.signature:
                raise H3RuntimeBindingError("H3 runtime asset changed during loading.")
        except OSError:
            raise H3RuntimeBindingError(
                "H3 runtime asset is unavailable during verification."
            ) from None


def _hash_file(path, abort_check):
    handle = -1
    try:
        requested = str(Path(path).absolute())
        resolved = str(Path(requested).resolve(strict=True))
        handle = os.open(
            resolved,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        before = os.fstat(handle)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 0 < before.st_size <= _MAX_FILE_BYTES
        ):
            raise H3RuntimeBindingError("H3 runtime asset has an unsafe type or size.")
        digest = hashlib.sha256()
        count = 0
        while True:
            if abort_check is not None and abort_check():
                raise InterruptedError("H3 runtime identity capture cancelled.")
            chunk = os.read(handle, 1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > before.st_size:
                raise H3RuntimeBindingError(
                    "H3 runtime asset grew during identity capture."
                )
            digest.update(chunk)
        if count != before.st_size or _signature(before) != _signature(
            os.fstat(handle)
        ):
            raise H3RuntimeBindingError(
                "H3 runtime asset changed during identity capture."
            )
        evidence = _FileEvidence(
            requested, resolved, _signature(before), count, digest.hexdigest()
        )
        evidence.verify()
        return evidence
    except InterruptedError:
        raise
    except OSError:
        raise H3RuntimeBindingError(
            "H3 runtime asset could not be read safely."
        ) from None
    finally:
        if handle >= 0:
            os.close(handle)


@dataclass(frozen=True)
class _ProcessorEvidence:
    requested: str
    resolved: str
    names: tuple

    def verify(self):
        current = _processor_evidence(self.requested)
        if current != self:
            raise H3RuntimeBindingError("H3 processor inventory changed after capture.")


def _processor_evidence(path):
    try:
        requested = str(Path(path).absolute())
        resolved = str(Path(requested).resolve(strict=True))
        names = []
        with os.scandir(resolved) as entries:
            for entry in entries:
                # Hugging Face download metadata is never read by the processor.
                if entry.name == ".cache" and entry.is_dir(follow_symlinks=False):
                    continue
                if not entry.is_file():
                    raise H3RuntimeBindingError(
                        "H3 processor has an unsupported asset type."
                    )
                names.append(entry.name)
                if len(names) > _MAX_FILES:
                    raise H3RuntimeBindingError("H3 processor inventory is too large.")
        if not PROCESSOR_FILES.issubset(names):
            raise H3RuntimeBindingError("H3 processor inventory is incomplete.")
        return _ProcessorEvidence(requested, resolved, tuple(sorted(names)))
    except OSError:
        raise H3RuntimeBindingError("H3 processor inventory is unavailable.") from None


@dataclass(frozen=True)
class H3RuntimeSnapshot:
    files: tuple
    contract_json: bytes
    processor: _ProcessorEvidence

    def __getstate__(self):
        raise TypeError("H3 runtime file evidence is private and cannot be serialized.")

    def verify(self):
        self.processor.verify()
        for _name, evidence in self.files:
            evidence.verify()

    def bind(self, components, loaded_contract):
        self.verify()
        if len(components) != 6 or any(value is None for value in components):
            raise H3RuntimeBindingError(
                "H3 runtime identity requires six loaded native components."
            )
        payload = {
            "schema": 1,
            "contract": json.loads(self.contract_json),
            "loaded": loaded_contract,
            "files": {
                name: {"size": file.size, "sha256": file.sha256}
                for name, file in self.files
            },
        }
        return H3RuntimeBinding(
            self,
            tuple(weakref.ref(value) for value in components),
            _canonical(loaded_contract),
            hashlib.sha256(_canonical(payload)).hexdigest(),
        )


@dataclass(frozen=True)
class H3RuntimeBinding:
    snapshot: H3RuntimeSnapshot
    components: tuple
    loaded_json: bytes
    sha256: str

    def __getstate__(self):
        raise TypeError("H3 loaded runtime binding cannot be serialized.")

    def verified_digest(self, components, loaded_contract):
        if len(components) != len(self.components) or any(
            reference() is not value or value is None
            for reference, value in zip(self.components, components)
        ):
            raise H3RuntimeBindingError(
                "H3 loaded components changed after identity capture."
            )
        if _canonical(loaded_contract) != self.loaded_json:
            raise H3RuntimeBindingError(
                "H3 loaded runtime contract changed after identity capture."
            )
        self.snapshot.verify()
        return self.sha256


def snapshot_h3_runtime_files(files, contract, *, processor_dir, abort_check=None):
    """Hash caller-resolved assets once before loading; verify again afterward."""
    if (
        type(files) is not dict
        or not 5 <= len(files) <= _MAX_FILES
        or not all(
            isinstance(name, str) and name and isinstance(path, (str, os.PathLike))
            for name, path in files.items()
        )
    ):
        raise H3RuntimeBindingError("H3 runtime asset inventory is invalid.")
    required = {"transformer", "video_vae", "audio_vae", "text_config", "conditioner_0"}
    if not required.issubset(files) or any(
        name.startswith("processor:") for name in files
    ):
        raise H3RuntimeBindingError("H3 runtime asset inventory is incomplete.")
    processor = _processor_evidence(processor_dir)
    files = dict(files)
    files.update(
        {
            "processor:" + name: os.path.join(processor.resolved, name)
            for name in processor.names
        }
    )
    if len(files) > _MAX_FILES:
        raise H3RuntimeBindingError("H3 runtime asset inventory is too large.")
    entries = tuple(
        (name, _hash_file(path, abort_check)) for name, path in sorted(files.items())
    )
    snapshot = H3RuntimeSnapshot(entries, _canonical(contract), processor)
    snapshot.verify()
    return snapshot
