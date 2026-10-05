"""Private offline worker. Model imports occur only after explicit byte admission."""
from __future__ import annotations

import hashlib
import ctypes
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import sys
import uuid

MAX_PROTOCOL_BYTES = 262144


def bounded_private_text(value, limit):
    return str(value).encode("utf-8", "replace")[:limit].decode("utf-8", "ignore")


def record_private_failure(result, error):
    diagnostic = result.setdefault("private_diagnostic", {})
    diagnostic.update(exception_type=bounded_private_text(
        type(error).__module__ + "." + type(error).__qualname__, 256),
        exception_message=bounded_private_text(error, 8192))
    result.update(state="failed", error_code="offline_worker_failed")


def write_private_result(path, result):
    """Publish an owner-private bounded checkpoint, including partial candidates."""
    document = dict(result, private_diagnostic=dict(result.get("private_diagnostic", {})))
    for key in ("base_candidate", "adapted_candidate"):
        if key in document:
            original = document[key]
            document[key] = bounded_private_text(original, 65536)
            if original != document[key]:
                document["private_diagnostic"][key + "_truncated"] = True
                document.update(state="failed", error_code="candidate_bound_exceeded")
    data = canonical(document)
    if len(data) > MAX_PROTOCOL_BYTES:
        document.update(state="failed", error_code="result_bound_exceeded")
        document["private_diagnostic"]["protocol_truncated"] = True
        for key in ("base_candidate", "adapted_candidate"):
            if key in document:
                document[key] = bounded_private_text(document[key], 8192)
                document["private_diagnostic"][key + "_truncated"] = True
        data = canonical(document)
        if len(data) > MAX_PROTOCOL_BYTES:
            runtime = document.pop("runtime", None)
            if runtime is not None:
                document["runtime_metadata_sha256"] = hashlib.sha256(canonical(runtime)).hexdigest()
            data = canonical(document)
    if len(data) > MAX_PROTOCOL_BYTES:
        raise ValueError("private result exceeds protocol bound")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("private result identity changed")
    temporary = path.with_name(".result-" + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return document["state"]


def protect_parent_lifetime(expected_parent_pid):
    """Linux kills this exact child if its creating parent thread disappears.

    Checking after prctl closes the race where the parent dies before the
    kernel installs the signal. No threaded-parent preexec_fn is involved.
    """
    if sys.platform != "linux" or type(expected_parent_pid) is not int or expected_parent_pid <= 1:
        raise ValueError("parent lifetime protection unavailable")
    if os.getppid() != expected_parent_pid:
        raise ValueError("owned parent already unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise ValueError("parent lifetime protection unavailable")
    if os.getppid() != expected_parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)
        raise ValueError("owned parent disappeared")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def read_request(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_PROTOCOL_BYTES:
            raise ValueError("private protocol boundary")
        return json.loads(stream.read(MAX_PROTOCOL_BYTES + 1))


def verify_file(seal):
    path = Path(seal["path"])
    if path.resolve(strict=True) != path:
        raise ValueError("asset path changed")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        fields = {"dev": "st_dev", "inode": "st_ino", "size_bytes": "st_size", "mtime_ns": "st_mtime_ns", "ctime_ns": "st_ctime_ns"}
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o022 or any(seal[key] != getattr(before, attr) for key, attr in fields.items()):
            raise ValueError("asset identity changed")
        digest = hashlib.sha256()
        while block := os.read(fd, 1024 * 1024):
            digest.update(block)
        after = os.fstat(fd)
        if digest.hexdigest() != seal["sha256"] or any(getattr(before, attr) != getattr(after, attr) for attr in fields.values()) or path.stat().st_ino != before.st_ino:
            raise ValueError("asset bytes changed")
    finally:
        os.close(fd)


def runtime_metadata(payload):
    if platform.python_implementation() != "CPython" or platform.python_version() != "3.12.14" or sys.platform != "linux":
        raise ValueError("selected interpreter unavailable")
    libc_name, libc_version = platform.libc_ver()
    if platform.machine() != "x86_64" or libc_name != "glibc" or tuple(map(int, libc_version.split(".")[:2])) < (2, 35):
        raise ValueError("selected platform unavailable")
    versions = {name: importlib.metadata.version(name) for name in payload["package_pins"]}
    if versions != payload["package_pins"]:
        raise ValueError("selected packages unavailable")
    inventory = {}
    for distribution in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        if name in inventory:
            raise ValueError("duplicate installed distribution")
        inventory[name] = distribution.version
    if inventory != payload["runtime_inventory"]:
        raise ValueError("qualified inventory changed")
    environment_identity = hashlib.sha256(canonical({"prefix": str(Path(sys.prefix).resolve()),
                                                     "executable": str(Path(sys.executable).absolute())})).hexdigest()
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": sys.platform, "architecture": platform.machine(), "glibc": libc_version,
            "packages": versions, "inventory": inventory,
            "environment_identity_sha256": environment_identity}


def verify_asset_roster(payload):
    expected_files = {seal["asset_id"]: seal["path"] for seal in payload["assets"]}
    expected_directories = {entry["path"]: entry for entry in payload["model_directories"]}
    if len(expected_files) != len(payload["assets"]) or len(expected_directories) != len(payload["model_directories"]):
        raise ValueError("duplicate model manifest member")
    files, directories = {}, set()
    for role in ("adapter", "base"):
        root = Path(payload[role + "_directory"])
        for path in [root] + sorted(root.rglob("*")):
            if path.resolve(strict=True) != path:
                raise ValueError("linked model member")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                expected = expected_directories.get(str(path))
                if expected is None or (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid) != (expected["dev"], expected["inode"], expected["mode"], expected["uid"]) or expected["mode"] != 0o700 or expected["uid"] != os.getuid():
                    raise ValueError("model directory identity changed")
                directories.add(str(path))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files[role + "/" + path.relative_to(root).as_posix()] = str(path)
            else:
                raise ValueError("unsafe model member")
    if files != expected_files or directories != set(expected_directories):
        raise ValueError("model directory roster changed")


def rewrite(payload, evidence=None, checkpoint=None):
    evidence = {} if evidence is None else evidence

    def phase(name):
        evidence.setdefault("private_diagnostic", {})["phase"] = name
        if checkpoint is not None:
            checkpoint()

    phase("imports")
    verify_asset_roster(payload)
    for seal in payload["assets"] + payload["images"]:
        verify_file(seal)
    verify_asset_roster(payload)
    template_spec = importlib.util.spec_from_file_location("h3_reviewed_prompt_template", payload["template_path"])
    template = importlib.util.module_from_spec(template_spec)
    template_spec.loader.exec_module(template)
    # These imports are intentionally isolated from Maestro's resident runtime.
    import torch
    from PIL import Image, ImageOps
    from transformers import AutoProcessor, Qwen3VLForConditionalGeneration
    from peft import PeftModel

    controls, request = payload["controls"], payload["request"]
    device = payload["device"]
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("selected device unavailable")
    phase("processorload")
    processor = AutoProcessor.from_pretrained(payload["base_directory"], local_files_only=True,
                trust_remote_code=False, min_pixels=controls["min_pixels"], max_pixels=controls["max_pixels"])
    verify_asset_roster(payload)
    phase("basemodelload")
    model = Qwen3VLForConditionalGeneration.from_pretrained(payload["base_directory"], local_files_only=True,
                trust_remote_code=False, torch_dtype=torch.bfloat16, attn_implementation="sdpa",
                low_cpu_mem_usage=True, device_map={"": device})
    model.eval()
    phase("basegenerate")
    messages = template.build_messages(request["original_prompt"], task=request["mode"],
                                        duration=controls["duration"], resolution=controls["resolution"])
    rendered = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    images = []
    for seal in payload["images"]:
        with Image.open(seal["path"]) as image:
            images.append(ImageOps.exif_transpose(image).convert("RGB").copy())
    # Transformers 4.57.1 Qwen3-VL consumes image placeholders and grid tensors;
    # its generation interface rejects the optional mm_token_type_ids field.
    kwargs = {"text": [rendered], "return_tensors": "pt", "padding": False, "return_mm_token_type_ids": False}
    if images:
        kwargs["images"] = images
    inputs = processor(**kwargs)
    inputs = {key: value.to(device) if isinstance(value, torch.Tensor) else value for key, value in inputs.items()}
    generation = {"max_new_tokens": controls["max_new_tokens"], "do_sample": not controls["greedy"]}
    if not controls["greedy"]:
        generation.update(temperature=controls["temperature"], top_p=controls["top_p"])

    def generate(candidate_model):
        torch.manual_seed(controls["seed"])
        if device == "cuda":
            torch.cuda.manual_seed_all(controls["seed"])
        with torch.inference_mode():
            output = candidate_model.generate(**inputs, **generation)
        return processor.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

    base = generate(model)
    evidence["base_candidate"] = bounded_private_text(base, 65536)
    phase("adapterload")
    verify_asset_roster(payload)
    adapted_model = PeftModel.from_pretrained(model, payload["adapter_directory"], local_files_only=True)
    adapted_model.eval()
    phase("adaptedgenerate")
    adapted = generate(adapted_model)
    evidence["adapted_candidate"] = bounded_private_text(adapted, 65536)
    phase("finalize")
    verify_asset_roster(payload)
    # No completed receipt survives a detected asset mutation during loading/use.
    for seal in payload["assets"] + payload["images"]:
        info = Path(seal["path"]).stat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (seal["dev"], seal["inode"], seal["size_bytes"], seal["mtime_ns"], seal["ctime_ns"]):
            raise ValueError("asset changed during execution")
    return {"request_commitment": request["commitment"], "base_candidate": base, "adapted_candidate": adapted}


def main():
    if len(sys.argv) != 3:
        return 2
    result_path = Path(sys.argv[2])
    result = {"state": "running", "private_diagnostic": {"phase": "admission"}}

    def checkpoint():
        if write_private_result(result_path, result) != "running":
            raise ValueError("private checkpoint exceeds protocol bound")
    try:
        payload = read_request(sys.argv[1])
        protect_parent_lifetime(payload["parent_pid"])
        result.update(operation=payload["operation"], nonce=payload["nonce"])
        checkpoint()
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != payload["worker_sha256"]:
            raise ValueError("worker changed")
        verify_file(payload["executable"])
        if Path(sys.executable).resolve() != Path(payload["executable"]["path"]):
            raise ValueError("interpreter changed")
        metadata = runtime_metadata(payload)
        result["runtime"] = metadata
        if payload["operation"] == "rewrite":
            result.update(rewrite(payload, result, checkpoint))
            result["runtime_metadata_sha256"] = hashlib.sha256(canonical(metadata)).hexdigest()
        elif payload["operation"] != "probe":
            raise ValueError("unknown operation")
        result.pop("error_code", None)
        result["state"] = "completed"
    except Exception as error:
        record_private_failure(result, error)
    written_state = write_private_result(result_path, result)
    return 0 if written_state == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
