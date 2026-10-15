"""Private, data-only prepared voice features; never native execution authority."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from services.h3_checkpoint_receipts import _exclusive_lock

_CONTRACT = "maestro-prepared-voice-v1"
_ENTRY_LIMIT = 64 * 1024**2
_RAM_LIMIT = 256 * 1024**2
_DISK_LIMIT = 4 * 1024**3
_JSON_LIMIT = 256 * 1024
_FAMILY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}")
_KEY = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.RLock()
_HASHES = OrderedDict()
_MEMORY = OrderedDict()


def _json(value):
    def check(item, depth=0):
        if depth > 32:
            raise ValueError("Prepared voice JSON is too deep")
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif not (item is None or type(item) in (str, bool, int)
                  or (type(item) is float and math.isfinite(item))):
            raise ValueError("Prepared voice settings must be JSON data")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _fingerprint(path):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Prepared voice source must be a regular file")
    identity = _identity(before)
    key = (str(path), *identity)
    with _LOCK:
        digest = _HASHES.get(key)
    if digest is None:
        hasher = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            if _identity(os.fstat(source.fileno())) != identity:
                raise ValueError("Prepared voice source changed")
            for block in iter(lambda: source.read(8 * 1024**2), b""):
                hasher.update(block)
            if _identity(os.fstat(source.fileno())) != identity:
                raise ValueError("Prepared voice source changed")
        digest = hasher.hexdigest()
    if _identity(path.stat()) != identity:
        raise ValueError("Prepared voice source changed")
    with _LOCK:
        _HASHES[key] = digest
        _HASHES.move_to_end(key)
        while len(_HASHES) > 512:
            _HASHES.popitem(last=False)
    return dict(path=str(path), identity=identity, sha256=digest)


def _current(records):
    return all(_fingerprint(record["path"]) == record for record in records)


def _root_path():
    from shared.utils import files_locator
    requested = Path(files_locator.get_download_location("prepared_voices"))
    return requested.parent.resolve() / requested.name


def _private(info, directory=False):
    mode = stat.S_ISDIR if directory else stat.S_ISREG
    return (mode(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and (not hasattr(os, "getuid") or info.st_uid == os.getuid())
            and (os.name != "posix" or stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600))
            and (directory or info.st_nlink == 1))


@contextmanager
def _locked_root(create=False):
    root = _root_path()
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    before = root.lstat()
    if not _private(before, True):
        raise ValueError("Prepared voice storage is not private")
    descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        with _exclusive_lock(root / ".store.lock"):
            if _identity(root.lstat())[:2] != _identity(os.fstat(descriptor))[:2]:
                raise ValueError("Prepared voice storage changed")
            yield root, descriptor
    finally:
        os.close(descriptor)


def _read(directory, name, limit):
    expected = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not _private(expected) or expected.st_size > limit:
        raise ValueError("Prepared voice file is unsafe or oversized")
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not _private(before) or _identity(before) != _identity(expected):
            raise ValueError("Prepared voice file is unsafe or oversized")
        data = source.read(limit + 1)
        if len(data) > limit or _identity(os.fstat(source.fileno())) != _identity(before):
            raise ValueError("Prepared voice file changed")
        return data


def _write(directory, name, data):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600, dir_fd=directory)
    owned = _identity(os.fstat(descriptor))[:2]
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if _identity(os.stat(name, dir_fd=directory, follow_symlinks=False))[:2] == owned:
            os.unlink(name, dir_fd=directory)
        raise


def _manifest(directory, name):
    if not name.endswith(".json") or _KEY.fullmatch(name[:-5]) is None:
        raise ValueError("Unrecognized prepared voice entry")
    record = json.loads(_read(directory, name, _JSON_LIMIT))
    if (set(record) != {"contract", "key", "identity", "pins", "tree", "data", "sha256", "bytes"}
            or record["contract"] != _CONTRACT or record["key"] != name[:-5]
            or record["key"] != _digest(record["identity"])
            or set(record["identity"]) != {"family", "models", "implementation", "options", "references", "settings"}
            or type(record["identity"]["references"]) is not dict
            or not record["identity"]["references"]
            or not all(type(k) is str and type(v) is str and _KEY.fullmatch(v)
                       for k, v in record["identity"]["references"].items())
            or not record["identity"]["models"]
            or any(type(record["identity"][field]) is not list for field in ("models", "implementation"))
            or not all(type(value) is str and _KEY.fullmatch(value)
                       for value in record["identity"]["models"] + record["identity"]["implementation"])
            or not all(type(record["identity"][field]) is str and _KEY.fullmatch(record["identity"][field])
                       for field in ("settings", "options"))
            or _FAMILY.fullmatch(record["identity"]["family"]) is None
            or re.fullmatch(record["key"] + r"\.[0-9a-f]{32}\.safetensors", record["data"]) is None
            or type(record["bytes"]) is not int or not 0 < record["bytes"] <= _ENTRY_LIMIT
            or _KEY.fullmatch(record["sha256"]) is None):
        raise ValueError("Unrecognized prepared voice manifest")
    if (type(record["pins"]) is not list or not record["pins"]
            or [p["sha256"] for p in record["pins"]] != record["identity"]["models"] + record["identity"]["implementation"]):
        raise ValueError("Invalid prepared voice source binding")
    _tree_names(record["tree"])
    info = os.stat(record["data"], dir_fd=directory, follow_symlinks=False)
    if not _private(info) or info.st_size != record["bytes"]:
        raise ValueError("Prepared voice payload is unsafe")
    return record


def _entries(directory):
    records = []
    for name in os.listdir(directory):
        if name.endswith(".json") and _KEY.fullmatch(name[:-5]):
            try:
                record = _manifest(directory, name)
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                records.append((info.st_mtime_ns, record))
            except Exception:
                continue
    return sorted(records, key=lambda pair: pair[0])


def _disk_bytes(directory):
    # Interrupted and quarantined publications still consume the disk budget.
    return sum(os.stat(name, dir_fd=directory, follow_symlinks=False).st_size
               for name in os.listdir(directory) if name != ".store.lock")


def _remove(directory, record):
    # Only a recognized manifest and its exact private payload are managed.
    checked = _manifest(directory, record["key"] + ".json")
    if checked != record:
        raise ValueError("Prepared voice entry changed")
    os.unlink(record["key"] + ".json", dir_fd=directory)
    os.unlink(record["data"], dir_fd=directory)


def _pack(value):
    import torch
    tensors, total, nodes = {}, 0, 0

    def visit(item, depth=0):
        nonlocal total, nodes
        nodes += 1
        if depth > 32 or nodes > 10000:
            raise ValueError("Prepared voice structure is oversized")
        if isinstance(item, torch.Tensor):
            total += item.numel() * item.element_size()
            if total > _ENTRY_LIMIT - _JSON_LIMIT or item.layout != torch.strided:
                raise ValueError("Prepared voice tensors are oversized or unsupported")
            name = "t" + str(len(tensors))
            tensors[name] = item.detach().to(device="cpu").contiguous().clone()
            return ["tensor", name]
        if type(item) is dict and all(type(key) is str for key in item):
            return ["dict", [[key, visit(child, depth + 1)] for key, child in item.items()]]
        if type(item) in (list, tuple):
            return ["tuple" if type(item) is tuple else "list", [visit(child, depth + 1) for child in item]]
        if item is None or type(item) in (str, bool, int) or (type(item) is float and math.isfinite(item)):
            return ["value", item]
        raise ValueError("Prepared voice value is not data-only")

    tree = visit(value)
    if len(_json(tree)) > _JSON_LIMIT // 2:
        raise ValueError("Prepared voice metadata is oversized")
    return tree, tensors


def _unpack(tree, tensors=None):
    used, nodes = set(), 0

    def visit(node, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 10000 or type(node) is not list or len(node) != 2:
            raise ValueError("Invalid prepared voice structure")
        tag, value = node
        if tag == "tensor" and type(value) is str and re.fullmatch(r"t[0-9]+", value):
            if value in used or (tensors is not None and value not in tensors):
                raise ValueError("Repeated prepared tensor reference")
            used.add(value)
            return tensors[value].clone() if tensors is not None else None
        if tag == "value" and (value is None or type(value) in (str, bool, int)
                               or (type(value) is float and math.isfinite(value))):
            return value
        if tag in ("list", "tuple") and type(value) is list:
            result = [visit(child, depth + 1) for child in value]
            return tuple(result) if tag == "tuple" else result
        if tag == "dict" and type(value) is list:
            result = {}
            for key, child in value:
                if type(key) is not str or key in result:
                    raise ValueError("Invalid prepared voice dictionary")
                result[key] = visit(child, depth + 1)
            return result
        raise ValueError("Unknown prepared voice tag")

    result = visit(tree)
    if tensors is None:
        return used
    if used != set(tensors):
        raise ValueError("Unrecognized prepared voice tensors")
    return result


def _tree_names(tree):
    """Validate tree structure without loading Torch or allocating tensor copies."""
    return _unpack(tree)


def _remember(root, record, tensors, owner):
    key = (str(root), record["key"])
    with _LOCK:
        owners = _MEMORY.pop(key, {}).get("owners", set()) | {owner}
        _MEMORY[key] = dict(record=record, tensors=tensors, owners=owners,
                            size=record["bytes"] + len(_json(record)))
        while len(_MEMORY) > 128 or sum(v["size"] for v in _MEMORY.values()) > _RAM_LIMIT:
            _MEMORY.popitem(last=False)


def _load(directory, record):
    from safetensors.torch import load
    if not _current(record["pins"]):
        raise ValueError("Prepared voice model or implementation changed")
    data = _read(directory, record["data"], _ENTRY_LIMIT)
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("Prepared voice payload is corrupt")
    tensors = load(data)
    if set(tensors) != _tree_names(record["tree"]) or not _current(record["pins"]):
        raise ValueError("Prepared voice structure or source changed")
    return tensors


class PreparedVoiceCache:
    def __init__(self, family: str, model_files, implementation_files=(), options=None):
        if type(family) is not str or _FAMILY.fullmatch(family) is None:
            raise ValueError("Invalid prepared voice family")
        self.family, self.enabled, self._owner = family, True, uuid.uuid4().hex
        self.stats = dict(ram_hits=0, disk_hits=0, misses=0, write_failures=0,
                          read_failures=0, identity_failures=0, source_changes=0, disabled=0)
        try:
            if options is not None and type(options) is not dict:
                raise ValueError("Prepared voice options must be a JSON object")
            self._pins = [_fingerprint(path) for path in [*model_files, *implementation_files]]
            if not model_files:
                raise ValueError("Prepared voices require model identity")
            self._base = dict(family=family,
                              models=[p["sha256"] for p in self._pins[:len(model_files)]],
                              implementation=[p["sha256"] for p in self._pins[len(model_files):]],
                              options=_digest(options or {}))
        except Exception:
            self._pins = None
            self.stats["identity_failures"] += 1

    def get_or_prepare(self, references, settings, prepare):
        if not self.enabled:
            self.stats["disabled"] += 1
            return prepare()
        try:
            if not self._pins or not _current(self._pins) or type(settings) is not dict:
                raise ValueError("Prepared voice identity unavailable")
            if type(references) is not dict or not references or not all(type(role) is str for role in references):
                raise ValueError("Prepared voices require named reference recordings")
            sources = {role: _fingerprint(path) for role, path in references.items()}
            identity = dict(self._base, references={role: r["sha256"] for role, r in sources.items()},
                            settings=_digest(settings))
            key = _digest(identity)
            root = _root_path()
        except Exception:
            self.stats["identity_failures"] += 1
            self.stats["misses"] += 1
            return prepare()

        def unchanged():
            return (_current(self._pins) and set(references) == set(sources)
                    and _digest(settings) == identity["settings"]
                    and all(_fingerprint(references[role]) == source for role, source in sources.items()))

        try:
            with _LOCK:
                entry = _MEMORY.get((str(root), key))
                if entry is not None and _current(entry["record"]["pins"]):
                    result = _unpack(entry["record"]["tree"], entry["tensors"])
                    if _current(entry["record"]["pins"]) and unchanged():
                        entry["owners"].add(self._owner)
                        _MEMORY.move_to_end((str(root), key))
                        self.stats["ram_hits"] += 1
                        return result
            with _locked_root() as (root, directory):
                record = _manifest(directory, key + ".json")
                tensors = _load(directory, record)
                if not unchanged():
                    raise ValueError("Prepared voice reference changed")
                _remember(root, record, tensors, self._owner)
                self.stats["disk_hits"] += 1
                os.utime(key + ".json", None, dir_fd=directory, follow_symlinks=False)
                return _unpack(record["tree"], tensors)
        except FileNotFoundError:
            pass
        except Exception:
            self.stats["read_failures"] += 1
        self.stats["misses"] += 1
        value = prepare()  # Producer exceptions must propagate; never call it twice.
        try:
            tree, tensors = _pack(value)
            result = _unpack(tree, tensors)
            from safetensors.torch import save
            data = save(tensors)
            if len(data) > _ENTRY_LIMIT:
                raise ValueError("Prepared voice payload is oversized")
            if not unchanged():
                self.stats["source_changes"] += 1
                return result
            record = dict(contract=_CONTRACT, key=key, identity=identity, pins=self._pins,
                          tree=tree, data=key + "." + uuid.uuid4().hex + ".safetensors",
                          sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            manifest = _json(record)
            if len(manifest) > _JSON_LIMIT:
                raise ValueError("Prepared voice manifest is oversized")
            if len(manifest) + len(data) > _ENTRY_LIMIT:
                raise ValueError("Prepared voice entry is oversized")
            _remember(root, record, tensors, self._owner)
            with _locked_root(create=True) as (root, directory):
                temporary = ".publish-" + uuid.uuid4().hex
                created = []
                try:
                    try:
                        existing = _manifest(directory, key + ".json")
                    except FileNotFoundError:
                        pass
                    except Exception:
                        # Preserve an unreadable private manifest, but do not
                        # let it permanently block this exact content key.
                        info = os.stat(key + ".json", dir_fd=directory, follow_symlinks=False)
                        if not _private(info):
                            raise ValueError("Unsafe existing prepared voice manifest")
                        os.replace(key + ".json", ".invalid-" + key + "-" + uuid.uuid4().hex,
                                   src_dir_fd=directory, dst_dir_fd=directory)
                    else:
                        try:
                            _load(directory, existing)
                        except Exception:
                            _remove(directory, existing)
                        else:
                            return result
                    entries = _entries(directory)
                    size = _disk_bytes(directory)
                    while entries and (len(entries) >= 1024 or size + len(data) + len(manifest) > _DISK_LIMIT):
                        _, oldest = entries.pop(0)
                        _remove(directory, oldest)
                        size = _disk_bytes(directory)
                    if size + len(data) + len(manifest) > _DISK_LIMIT:
                        raise ValueError("Prepared voice disk budget is full")
                    _write(directory, record["data"], data)
                    created.append(record["data"])
                    _write(directory, temporary, manifest)
                    created.append(temporary)
                    if not unchanged():
                        self.stats["source_changes"] += 1
                        with _LOCK:
                            _MEMORY.pop((str(root), key), None)
                        return result
                    os.replace(temporary, key + ".json", src_dir_fd=directory, dst_dir_fd=directory)
                    created.clear()
                    os.fsync(directory)
                finally:
                    for name in created:
                        os.unlink(name, dir_fd=directory)
            _remember(root, record, tensors, self._owner)
            return result
        except Exception:
            self.stats["write_failures"] += 1
            return locals().get("result", value)

    def clear_memory(self):
        with _LOCK:
            for key, entry in list(_MEMORY.items()):
                entry["owners"].discard(self._owner)
                if not entry["owners"]:
                    del _MEMORY[key]

    def close(self):
        self.clear_memory()
        self.enabled = False


def _manage(reference_path, family, operation):
    if family is not None and (type(family) is not str or _FAMILY.fullmatch(family) is None):
        raise ValueError("Invalid prepared voice family")
    result = dict(entries=[], matched=0, valid=0, memory_ready=0, evicted=0, failures=0,
                  readiness_scope="cpu_features")
    try:
        source = _fingerprint(reference_path)
        root, directory = _root_path(), None
        with ExitStack() as stack:
            try:
                root, directory = stack.enter_context(_locked_root())
            except FileNotFoundError:
                pass
            except Exception:
                result["failures"] += 1
            with _LOCK:
                candidates = {key: entry["record"] for (location, key), entry in _MEMORY.items()
                              if location == str(root)}
            disk = {record["key"]: record for _, record in _entries(directory)} if directory is not None else {}
            candidates = {**disk, **candidates}
            for record in candidates.values():
                if (source["sha256"] not in record["identity"]["references"].values()
                        or (family is not None and record["identity"]["family"] != family)):
                    continue
                public = dict(key=record["key"], family=record["identity"]["family"],
                              bytes=record["bytes"], valid=False, disk_ready=False, memory_ready=False)
                result["matched"] += 1
                try:
                    if _fingerprint(reference_path) != source:
                        raise ValueError("Prepared voice reference changed")
                    if operation == "evict":
                        if record["key"] in disk:
                            _remove(directory, disk[record["key"]])
                        with _LOCK:
                            _MEMORY.pop((str(root), record["key"]), None)
                        result["evicted"] += 1
                    else:
                        with _LOCK:
                            memory = _MEMORY.get((str(root), record["key"]))
                            if memory is not None and _current(memory["record"]["pins"]):
                                public["memory_ready"] = True
                                if operation == "preload":
                                    memory["owners"].add("preload")
                        if record["key"] in disk:
                            saved = disk[record["key"]]
                            try:
                                data = _read(directory, saved["data"], _ENTRY_LIMIT)
                                if hashlib.sha256(data).hexdigest() != saved["sha256"]:
                                    raise ValueError("Prepared voice data is corrupt")
                                from safetensors import deserialize
                                if ({name for name, _ in deserialize(data)} != _tree_names(saved["tree"])
                                        or not _current(saved["pins"])):
                                    raise ValueError("Prepared voice data or model changed")
                                public["disk_ready"] = True
                                if operation == "preload" and not public["memory_ready"]:
                                    tensors = _load(directory, saved)
                                    if _fingerprint(reference_path) != source:
                                        raise ValueError("Prepared voice reference changed")
                                    _remember(root, saved, tensors, "preload")
                                    public["memory_ready"] = True
                            except Exception:
                                result["failures"] += 1
                        public["valid"] = public["disk_ready"] or public["memory_ready"]
                        result["valid"] += int(public["valid"])
                        result["memory_ready"] += int(public["memory_ready"])
                except Exception:
                    result["failures"] += 1
                result["entries"].append(public)
            # Preloading a later entry may evict an earlier one from the LRU.
            with _LOCK:
                for entry in result["entries"]:
                    memory = _MEMORY.get((str(root), entry["key"]))
                    entry["memory_ready"] = bool(operation != "evict" and memory is not None
                                                 and _current(memory["record"]["pins"]))
                    entry["valid"] = entry["disk_ready"] or entry["memory_ready"]
                result["memory_ready"] = sum(entry["memory_ready"] for entry in result["entries"])
                result["valid"] = sum(entry["valid"] for entry in result["entries"])
        if _fingerprint(reference_path) != source:
            return dict(entries=[], matched=0, valid=0, memory_ready=0, evicted=result["evicted"], failures=1,
                        readiness_scope="cpu_features")
    except FileNotFoundError:
        pass
    except Exception:
        result["failures"] += 1
    return result


def inspect_prepared(reference_path, family=None):
    return _manage(reference_path, family, "inspect")


def preload_prepared(reference_path, family=None):
    return _manage(reference_path, family, "preload")


def evict_prepared(reference_path, family=None):
    return _manage(reference_path, family, "evict")


def clear_prepared_memory():
    with _LOCK:
        count = len(_MEMORY)
        size = sum(entry["size"] for entry in _MEMORY.values())
        _MEMORY.clear()
    return dict(cleared=count, bytes=size)


def prepared_memory_status():
    """Describe resident CPU features without loading models or cache files."""
    with _LOCK:
        return dict(entries=len(_MEMORY), bytes=sum(entry["size"] for entry in _MEMORY.values()))
