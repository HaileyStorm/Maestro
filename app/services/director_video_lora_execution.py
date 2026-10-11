"""Server-owned Director LoRA consumer with exact repeat/window coverage.

The worker supplies authenticated native controls and retained repeat seeds.
Version 1 has a fixed window plan; version 2 retains real preparation results
before the consuming call. Neither version builds expectations in before_window
or grants GPU authority, authorizes output, or replaces producer validation.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import re
import random
import stat


_HASH = re.compile(r"^[0-9a-f]{64}$")
NATIVE_FIELDS = (
    "model_type", "base_model_type", "model_filename", "activated_loras",
    "loras_multipliers", "loras_slists", "model_def_nb_phases",
    "num_inference_steps", "guidance_phases", "model_switch_phase",
    "guidance_scale", "guidance2_scale", "guidance3_scale",
    "switch_threshold", "switch_threshold2", "negative_prompt", "sample_solver",
)
WINDOW_FIELDS = ("repeat_index", "window_index", "seed", "frames", "height", "width", "fps")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class DirectorLoraExecutionError(ValueError):
    pass


def choose_repeat_seeds(requested_seed, repeat_count, *, draw=None):
    """Resolve randomness once, before sealing a new task plan.

    Recovery must read the retained plan instead of calling this again.
    No model imports, CUDA seeding, or execution authority are involved.
    """
    if (type(repeat_count) is not int or repeat_count < 1
            or (requested_seed is not None and type(requested_seed) is not int)):
        raise DirectorLoraExecutionError("Director repeat seed request is invalid")
    draw = draw or random.SystemRandom().randint
    seeds = []
    for index in range(repeat_count):
        seed = requested_seed if index == 0 and requested_seed is not None and requested_seed >= 0 else draw(0, 999999999)
        if type(seed) is not int or seed < 0:
            raise DirectorLoraExecutionError("Director repeat seed selection is invalid")
        seeds.append(seed)
    return seeds


def _media_binding(value, *, include_fingerprint=False):
    """Pin the media actually returned by validation, without decoding videos."""
    from PIL import Image

    if value is None or (type(value) is str and not value):
        return value
    if isinstance(value, (list, tuple)):
        return [_media_binding(item) for item in value]
    if isinstance(value, Image.Image):
        result = {
            "kind": "decoded_image", "mode": value.mode, "size": list(value.size),
            "pixels_sha256": hashlib.sha256(value.tobytes()).hexdigest(),
            "rgba_sha256": hashlib.sha256(value.convert("RGBA").tobytes()).hexdigest(),
        }
        filename = getattr(value, "filename", None)
        if type(filename) is str and filename:
            result["source"] = _media_binding(filename)
        return result
    if type(value) is not str or not os.path.isabs(value):
        raise DirectorLoraExecutionError("Director validated media identity is invalid")
    resolved = os.path.realpath(value)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(resolved, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DirectorLoraExecutionError("Director validated media is not a regular file")
        hasher = hashlib.sha256()
        while block := os.read(descriptor, 1024 * 1024):
            hasher.update(block)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if (identity(before) != identity(after) or os.path.realpath(value) != resolved
            or identity(after) != identity(os.stat(resolved, follow_symlinks=False))):
        raise DirectorLoraExecutionError("Director validated media changed while being retained")
    result = {"kind": "file", "path": value, "resolved_path": resolved,
              "bytes": after.st_size, "sha256": hasher.hexdigest()}
    if include_fingerprint:
        result["fingerprint"] = list(identity(after))
    return result


def validated_task_binding(task, params, *, attachment_keys, excluded_fields=()):
    """Bind a real split task and its effective post-validation controls.

    Opaque worker state and admission credentials are excluded by the owner.
    Attachment bytes, decoded stills and predecessor paths in nested settings
    remain part of the task. This is retained input, not a native window proof.
    """
    from PIL import Image

    task_id = task.get("id") if type(task) is dict else None
    if (type(task_id) not in (int, str)
            or (type(task_id) is int and task_id < 0)
            or (type(task_id) is str and not task_id)
            or type(task.get("prompt", "")) is not str or type(params) is not dict):
        raise DirectorLoraExecutionError("Director validated task identity is invalid")

    attachments = set(attachment_keys) | {"voice_reference", "voice_clone_refs"}
    media_path_keys = attachments | {"path", "source_path", "video_path", "audio_path",
                                    "image_path", "continuation_path", "predecessor_path"}

    def nested_media(value, field=None):
        if isinstance(value, Image.Image):
            return _media_binding(value)
        if isinstance(value, dict):
            return {key: nested_media(item, key) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [nested_media(item, field) for item in value]
        if field in media_path_keys and type(value) is str and os.path.isabs(value):
            return _media_binding(value)
        return value

    excluded = set(excluded_fields) | {"state"}
    controls = {}
    for key, value in params.items():
        if key in excluded:
            continue
        controls[key] = (_media_binding(value) if key in attachments else
                         nested_media(value) if key in {"custom_settings", "multi_clip_info"} else value)
    try:
        return json.loads(canonical({"runtime_task_id": task_id,
            "authored_prompt": task.get("prompt", ""), "params": controls}))
    except (TypeError, ValueError, OverflowError):
        raise DirectorLoraExecutionError("Director validated task controls are not retainable") from None


def retained_task_recipe(binding, *, child_digest, retained=None, commitment):
    """Read a sealed task before selecting any new repeat randomness."""
    if type(child_digest) is not str or _HASH.fullmatch(child_digest) is None:
        raise DirectorLoraExecutionError("Director retained child identity is invalid")
    count = binding["params"].get("repeat_generation", 1)
    requested_seed = binding["params"].get("seed")
    if type(count) is not int or count < 1 or (requested_seed is not None and type(requested_seed) is not int):
        raise DirectorLoraExecutionError("Director validated repeat controls are invalid")
    if retained is not None:
        if (type(retained) is not dict or set(retained) != {"binding", "digest"}
                or type(retained.get("binding")) is not dict
                or type(retained.get("digest")) is not str
                or not hmac.compare_digest(retained["digest"], commitment(retained["binding"]))):
            raise DirectorLoraExecutionError("Director retained task seal changed")
        recipe = retained["binding"]
        seeds = recipe.get("repeat_seeds")
        if (set(recipe) != {"version", "child_digest", "task", "repeat_seeds"}
                or type(recipe.get("version")) is not int or recipe["version"] != 1
                or recipe.get("child_digest") != child_digest or recipe.get("task") != binding
                or type(seeds) is not list or len(seeds) != count
                or any(type(seed) is not int or seed < 0 for seed in seeds)
                or (requested_seed is not None and requested_seed >= 0 and seeds[0] != requested_seed)):
            raise DirectorLoraExecutionError("Director retained task or repeat coordinates changed")
        return copy.deepcopy(retained)
    recipe = {"version": 1, "child_digest": child_digest, "task": binding,
              "repeat_seeds": choose_repeat_seeds(requested_seed, count)}
    return {"binding": recipe, "digest": commitment(recipe)}


def native_task_controls(task_binding, *, model_def, base_model_type, model_filename, adapter_groups):
    """Project retained validated controls with the real native multiplier parser.

    The owner supplies catalog/handler/user/managed selections in consuming
    order, after resolving their files without loading a transformer. No plain
    Studio eligibility or media/window restriction applies here.
    """
    from services.native_video_preparation import native_lora_phases, append_native_lora_group

    params = task_binding["params"]
    phases, multiplier_phases = native_lora_phases(model_def, params["guidance_phases"])
    schedule, paths, choices = None, [], []
    for files, strengths, label in adapter_groups:
        choices, schedule = append_native_lora_group(files, strengths,
            steps=params["num_inference_steps"], phases=multiplier_phases,
            model_switch_phase=params["model_switch_phase"], schedule=schedule, label=label)
        paths.extend(files)
    native = {key: params[key] for key in NATIVE_FIELDS
              if key not in {"base_model_type", "model_filename", "loras_slists", "model_def_nb_phases"}}
    native.update(base_model_type=base_model_type, model_filename=model_filename,
                  guidance_phases=phases, model_def_nb_phases=multiplier_phases, loras_slists=schedule)
    return json.loads(canonical({"native": native, "adapter_paths": paths,
        "adapter_ids": [str(i) for i in range(len(paths))], "multiplier_choices": choices}))


def build_native_task_plan(task_recipe, child_snapshot, *, runtime):
    """Select and pin the actual installed adapter stack without model loading.

    The worker authenticates these private records before calling. Native
    selector and multiplier code is shared with WGP; its ordinary acquisition
    path stays with the model owner. Fingerprints bind the bytes hashed here,
    so callbacks can detect changes without rereading large files each window.
    """
    from services.native_video_preparation import (
        select_wan_transformer_loras, select_ltx_video_transformer_loras,
        select_existing_ltx2_transformer_loras,
    )

    task = task_recipe["binding"]["task"]
    params = task["params"]
    consumption = child_snapshot["binding"]["consumption"]
    model_type = params["model_type"]
    base = runtime.get_base_model_type(model_type)
    model_def = copy.deepcopy(runtime.get_model_def(model_type))
    family = runtime.get_model_family(model_type)
    selection = {**params, "model_type": model_type, "base_model_type": base,
                 "model_def": model_def}
    flags = model_def.get("set_video_prompt_type")
    if flags is not None:
        selection["video_prompt_type"] = runtime.update_video_prompt_type_flags(
            params.get("video_prompt_type", ""), add_flags=flags)
    if params.get("outpaint_official_stack", False):
        catalog = ([], [])
    else:
        catalog = runtime.get_transformer_loras(model_type, create_primary=False)
    extras = ([], [])
    if family == "wan":
        extras = select_wan_transformer_loras(
            runtime.get_model_recursive_prop, runtime.fl.locate_file, **selection)
    elif family == "ltxv":
        extras = select_ltx_video_transformer_loras(
            runtime.get_model_recursive_prop, runtime.fl.locate_file, **selection)
    elif family in {"ltx2", "ltx25"}:
        from models.ltx2 import ltx2_handler
        extras = select_existing_ltx2_transformer_loras(
            runtime.get_model_recursive_prop,
            arch_spec=ltx2_handler._get_arch_spec(base),
            lora_directory=runtime.get_lora_dir(model_type, create=False),
            locate_file=runtime.fl.locate_file,
            resolve_lora_path=lambda selected, filename: runtime.resolve_lora_path(
                selected, filename, create_primary=False), **selection)
    users = [runtime.resolve_lora_path(model_type, filename, create_primary=False)
             for filename in params["activated_loras"]]
    groups = [(catalog[0], catalog[1], "Transformer Loras"),
              (extras[0], extras[1], "Extra Transformer Loras"),
              (users, params["loras_multipliers"], "Loras")]
    user_offset = len(catalog[0]) + len(extras[0])

    from services.h3_turbo import (
        H3_TURBO_STRENGTH, turbo_requested, turbo_model_variant,
        validate_turbo_request, resolve_turbo_assets,
    )
    custom = params.get("custom_settings")
    if turbo_requested(custom):
        validate_turbo_request(base_model_type=base, model_def=model_def,
            custom_settings=custom, authored_steps=params["num_inference_steps"],
            activated_loras=params["activated_loras"],
            loras_multipliers=params["loras_multipliers"],
            skip_steps_cache_type=params.get("skip_steps_cache_type"))
        variant = turbo_model_variant(model_def)
        if variant["backbone"] == "residual_output" and any(files for files, _, _ in groups):
            raise DirectorLoraExecutionError("This H3 Turbo checkpoint cannot stack other adapters")
        assets = resolve_turbo_assets(verify=True)
        if variant["backbone"] == "mmgp":
            groups.append(([str(assets.lora_path)], str(H3_TURBO_STRENGTH), "H3 Turbo"))
    from services.h3_lightx2v import (
        H3_LIGHTX2V_EFFECTIVE_SCALE, lightx2v_requested,
        validate_lightx2v_request, resolve_lightx2v_assets,
    )
    if lightx2v_requested(custom):
        clip = params.get("multi_clip_info")
        validate_lightx2v_request(selected_model_type=model_type, model_def=model_def,
            custom_settings=custom, authored_steps=params["num_inference_steps"],
            semantic_references=bool(model_def.get("minimax_h3_reference_mode")),
            multisegment=isinstance(clip, dict) and int(clip.get("total", 1) or 1) > 1,
            activated_loras=params["activated_loras"],
            loras_multipliers=params["loras_multipliers"],
            skip_steps_cache_type=params.get("skip_steps_cache_type"),
            native_boundary=params.get("h3_native_boundary_conditioning") is True)
        assets = resolve_lightx2v_assets(verify=True)
        groups.append(([str(assets.lora_path)], str(H3_LIGHTX2V_EFFECTIVE_SCALE), "LightX2V"))

    controls = native_task_controls(task, model_def=model_def,
        base_model_type=base, model_filename=runtime.get_model_filename(base),
        adapter_groups=groups)
    pinned, fingerprints = {}, []
    adapter_sources = []
    for index, path in enumerate(controls["adapter_paths"]):
        original = os.path.abspath(path)
        resolved = os.path.realpath(original)
        if resolved not in pinned:
            pinned[resolved] = _media_binding(original, include_fingerprint=True)
        pin = pinned[resolved]
        if os.path.realpath(original) != pin["resolved_path"]:
            raise DirectorLoraExecutionError("Director selected adapter changed during retention")
        fingerprints.append({"path": original, "resolved_path": resolved,
                             "identity": pin["fingerprint"]})
        adapter_sources.append({"id": str(index), "path": resolved, "sha256": pin["sha256"]})
    sources = [{"id": filename, "path": row["path"], "sha256": row["sha256"]}
               for filename, row in zip(params["activated_loras"],
                   adapter_sources[user_offset:user_offset + len(users)])]
    if sources != consumption["sources"]:
        raise DirectorLoraExecutionError("Director selected adapter disagrees with admitted source")
    expansions = [expansion["text"]
        for selected in consumption["selections"]
        if "generation" in selected["resolved_scopes"]
        for expansion in selected["parameter_expansions"]
        if "generation" in expansion["scopes"] and not expansion["roles"]
        and expansion["text"]]
    plan = {"version": 2, "child_digest": child_snapshot["digest"],
            "task_digest": task_recipe["digest"], "sources": sources,
            "expansions": expansions, "native": controls["native"],
            "repeat_seeds": task_recipe["binding"]["repeat_seeds"],
            "adapter_ids": controls["adapter_ids"], "adapter_sources": adapter_sources,
            "user_adapter_offset": user_offset}
    # Same branch as the native loader; LTX's two denoising phases alone do
    # not imply a second transformer. The worker retains the selected catalog.
    transformer_count = 2 if "URLs2" in model_def else 1
    return json.loads(canonical(plan)), transformer_count, fingerprints


def require_adapter_fingerprints(fingerprints):
    """Require the same file objects and bytes pinned by the initial read."""
    for pin in fingerprints:
        path = pin["path"]
        resolved = pin["resolved_path"]
        info = os.stat(resolved, follow_symlinks=False)
        identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if (os.path.realpath(path) != resolved or not stat.S_ISREG(info.st_mode)
                or identity != pin["identity"]):
            raise DirectorLoraExecutionError("Director adapter changed after native retention")


class DirectorLoraConsumer:
    """One worker-owned native invocation with explicit repeat/window coverage."""

    def __init__(self, plan, *, validate_current, transformer_count=None,
                 retain_preparation=None):
        try:
            plan = json.loads(canonical(plan))
            incremental = type(plan.get("version")) is int and plan["version"] == 2
            fields = {"version", "child_digest", "task_digest", "sources", "expansions", "native"}
            fields |= {"repeat_seeds", "adapter_ids", "adapter_sources", "user_adapter_offset"} if incremental else {"windows"}
            if (set(plan) != fields
                    or type(plan["version"]) is not int or plan["version"] not in (1, 2)
                    or any(type(plan[key]) is not str or _HASH.fullmatch(plan[key]) is None
                           for key in ("child_digest", "task_digest"))
                    or not callable(validate_current) or not isinstance(plan["native"], dict)
                    or (transformer_count is not None and (
                        type(transformer_count) is not int or transformer_count not in (1, 2)))
                    or set(plan["native"]) != set(NATIVE_FIELDS)
                    or not isinstance(plan["sources"], list) or not plan["sources"]
                    or not isinstance(plan["expansions"], list)
                    or any(type(item) is not str or not item.strip() for item in plan["expansions"])):
                raise ValueError
            paths = []
            for source in plan["sources"]:
                if (not isinstance(source, dict) or set(source) != {"id", "path", "sha256"}
                        or type(source["id"]) is not str or not source["id"]
                        or type(source["path"]) is not str or not os.path.isabs(source["path"])
                        or os.path.realpath(source["path"]) != source["path"]
                        or type(source["sha256"]) is not str or _HASH.fullmatch(source["sha256"]) is None):
                    raise ValueError
                paths.append(source["path"])
            if len(set(paths)) != len(paths) or plan["native"]["activated_loras"] != [s["id"] for s in plan["sources"]]:
                raise ValueError
            if incremental:
                seeds = plan["repeat_seeds"]
                adapter_ids = plan["adapter_ids"]
                adapter_sources = plan["adapter_sources"]
                user_offset = plan["user_adapter_offset"]
                if (not callable(retain_preparation) or type(seeds) is not list or not seeds
                        or any(type(seed) is not int or seed < 0 for seed in seeds)
                        or type(adapter_ids) is not list or len(adapter_ids) < len(paths)
                        or adapter_ids != [str(i) for i in range(len(adapter_ids))]
                        or type(adapter_sources) is not list or len(adapter_sources) != len(adapter_ids)
                        or type(user_offset) is not int or not 0 <= user_offset <= len(adapter_ids) - len(paths)):
                    raise ValueError
                for index, source in enumerate(adapter_sources):
                    if (type(source) is not dict or set(source) != {"id", "path", "sha256"}
                            or source["id"] != str(index) or type(source["path"]) is not str
                            or not os.path.isabs(source["path"])
                            or os.path.realpath(source["path"]) != source["path"]
                            or type(source["sha256"]) is not str or _HASH.fullmatch(source["sha256"]) is None):
                        raise ValueError
                if [{key: source[key] for key in ("path", "sha256")}
                        for source in adapter_sources[user_offset:user_offset + len(paths)]] != [
                        {key: source[key] for key in ("path", "sha256")} for source in plan["sources"]]:
                    raise ValueError
            elif type(plan["windows"]) is not list or not plan["windows"]:
                raise ValueError
            windows = {}
            for record in ([] if incremental else plan["windows"]):
                if (not isinstance(record, dict) or set(record) != set(WINDOW_FIELDS) | {"prompt"}
                        or type(record["prompt"]) is not str
                        or any(type(record[key]) is not int or record[key] < 0
                               for key in ("repeat_index", "seed"))
                        or any(type(record[key]) is not int or record[key] < 1
                               for key in ("window_index", "frames", "height", "width"))
                        or type(record["fps"]) not in (int, float) or record["fps"] <= 0):
                    raise ValueError
                key = record["repeat_index"], record["window_index"]
                if key in windows:
                    raise ValueError
                windows[key] = record
            for repeat in {key[0] for key in windows}:
                indexes = sorted(key[1] for key in windows if key[0] == repeat)
                if indexes != list(range(1, len(indexes) + 1)):
                    raise ValueError
                if len({record["seed"] for record in windows.values() if record["repeat_index"] == repeat}) != 1:
                    raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            raise DirectorLoraExecutionError("Director native LoRA plan is invalid") from None
        self._plan = plan
        self._windows = windows
        self._ordered = sorted(windows)
        self._incremental = incremental
        self._retain_preparation = retain_preparation
        self._preparation_finished = {}
        self._validate_current = validate_current
        # The owning factory must derive this from its authenticated checkpoint
        # selection. Guidance phases do not establish transformer count. None
        # keeps old preparation-only callers usable but cannot complete a window.
        self._transformer_count = transformer_count
        self._sealed_repeats = {}
        self.reset_attempt()

    @property
    def incremental_preparation(self):
        return self._incremental

    def worker_binding(self):
        """Copy a pre-dispatch binding, never a transformer or live observer.

        The server has already authenticated any restored producer prefix.
        A child reconstructs its own consumer so activation identity remains
        local to the actual transformer. This snapshot grants no execution
        authority and must not be reused after an uncertain child invocation.
        """
        self._current()
        if self._loaded or self._pending is not None or self._activation is not None:
            raise DirectorLoraExecutionError("Director consumer already entered native execution")
        indexes = sorted(self._sealed_repeats)
        if indexes != list(range(len(indexes))):
            raise DirectorLoraExecutionError("Director completed prefix is not contiguous")
        return copy.deepcopy({"plan": self._plan,
            "transformer_count": self._transformer_count,
            "completed_repeats": [self._sealed_repeats[index] for index in indexes]})

    def validate_worker_binding(self):
        """Revalidate through the owning server closure for a child RPC."""
        self._current()
        fields = ("child_digest", "task_digest", "sources", "adapter_sources") if self._incremental else (
            "child_digest", "task_digest", "sources")
        return {key: copy.deepcopy(self._plan[key]) for key in fields}

    def retain_worker_preparation(self, expected):
        """Commit the child's real preparation through the existing owner."""
        self._current()
        if (type(expected) is not dict or set(expected) != {"binding", "digest"}
                or type(expected.get("binding")) is not dict):
            raise DirectorLoraExecutionError("Director worker preparation is invalid")
        binding = expected["binding"]
        if (set(binding) != {"version", "child_digest", "task_digest", "preparation"}
                or type(binding["version"]) is not int or binding["version"] != 1
                or binding["child_digest"] != self._plan["child_digest"]
                or binding["task_digest"] != self._plan["task_digest"]
                or expected["digest"] != digest(binding)):
            raise DirectorLoraExecutionError("Director worker preparation binding changed")
        self._retain_prepared(copy.deepcopy(binding["preparation"]))
        return copy.deepcopy(expected)

    def _repeat_indexes(self):
        return (list(range(len(self._plan["repeat_seeds"]))) if self._incremental
                else sorted({key[0] for key in self._windows}))

    def _retain_prepared(self, preparation):
        """The owner commits or reconciles this record before acknowledging it.

        The callback must use the existing immutable manifest/journal path and
        stop on an uncertain outcome. Its acknowledgement is not producer or
        GPU authority; this guard only checks the returned record's identity.
        """
        binding = {"version": 1, "child_digest": self._plan["child_digest"],
                   "task_digest": self._plan["task_digest"], "preparation": preparation}
        expected = {"binding": binding, "digest": digest(binding)}
        try:
            acknowledgement = self._retain_preparation(copy.deepcopy(expected))
            self._current()
            if canonical(acknowledgement) != canonical(expected):
                raise DirectorLoraExecutionError("Director preparation retention changed")
        except BaseException:
            self.discard()
            raise

    def prepare_window(self, record):
        """Retain facts from real preprocessing, before the consuming call.

        Native adapter controls remain independently bound by the task plan.
        Prepared media geometry and the handler's prompt are runtime facts,
        not a second decode pass or an expectation copied from before_window.
        """
        self._current()
        if not self._incremental:
            return
        try:
            record = self._prepared_record(record)
            repeat = record["repeat_index"]
            key = repeat, record["window_index"]
            if (repeat in self._sealed_repeats or repeat in self._preparation_finished
                    or self._pending is not None):
                raise ValueError
            if key in self._windows:
                if canonical(self._windows[key]) != canonical(record):
                    raise ValueError
                return
            previous = sorted(k for k in self._windows if k[0] == repeat)
            if (key[1] != len(previous) + 1
                    or any(k not in self._completed for k in previous)
                    or any(index not in self._sealed_repeats for index in range(repeat))):
                raise ValueError
            self._retain_prepared({"window": record})
            self._windows[key] = record
            self._ordered = sorted(self._windows)
        except (KeyError, TypeError, ValueError, OverflowError):
            self.discard()
            raise DirectorLoraExecutionError("Director prepared window changed") from None

    def _prepared_record(self, record):
        record = json.loads(canonical(record))
        repeat = record["repeat_index"]
        if (set(record) != set(WINDOW_FIELDS) | {"prompt"}
                or type(record["prompt"]) is not str
                or type(repeat) is not int or repeat not in self._repeat_indexes()
                or type(record["seed"]) is not int or record["seed"] != self._plan["repeat_seeds"][repeat]
                or any(type(record[field]) is not int or record[field] < 1
                       for field in ("window_index", "frames", "height", "width"))
                or type(record["fps"]) not in (int, float) or record["fps"] <= 0):
            raise ValueError
        return record

    def _repeat_proof(self, repeat_index, rows, *, windows=None, finish=None):
        proof = {"version": 2 if self._incremental else 1,
            "child_digest": self._plan["child_digest"], "task_digest": self._plan["task_digest"],
            "sources_digest": digest(self._plan["sources"]),
            "expansions_digest": digest(self._plan["expansions"]),
            "native_digest": digest(self._plan["native"]), "repeat_index": repeat_index,
            "windows": rows}
        if self._incremental:
            proof.update(repeat_seeds_digest=digest(self._plan["repeat_seeds"]),
                         adapter_ids_digest=digest(self._plan["adapter_ids"]),
                         adapter_sources_digest=digest(self._plan["adapter_sources"]),
                         user_adapter_offset=self._plan["user_adapter_offset"],
                         prepared_windows=windows, preparation_finish=finish)
        return {"binding": proof, "digest": digest(proof)}

    def finish_preparation(self, repeat_index, *, reason):
        """Close only a normal native boundary after every prepared window ran."""
        self._current()
        if not self._incremental:
            return
        keys = sorted(key for key in self._windows if key[0] == repeat_index)
        if (type(repeat_index) is not int or repeat_index not in self._repeat_indexes()
                or repeat_index in self._sealed_repeats
                or reason not in {"window_limit", "latent_tail", "control_short_tail", "control_exhausted"}
                or not keys or self._pending is not None
                or any(key not in self._completed for key in keys)):
            self.discard()
            raise DirectorLoraExecutionError("Director preparation did not finish normally")
        finish = {"repeat_index": repeat_index, "reason": reason,
                  "windows_digest": digest([self._windows[key] for key in keys])}
        if repeat_index in self._preparation_finished:
            if self._preparation_finished[repeat_index] != finish:
                self.discard()
                raise DirectorLoraExecutionError("Director preparation finish changed")
            return
        self._retain_prepared({"finish": finish})
        self._preparation_finished[repeat_index] = finish

    def reset_attempt(self):
        if self._sealed_repeats:
            raise DirectorLoraExecutionError("Director cannot replay sealed native repeats")
        self._preparation_finished = {}
        self._loaded = False
        self._load_request = None
        self._completed = {}
        self._pending = None
        self._activation = None
        self._failed = False

    def discard(self):
        self._loaded = False
        self._load_request = None
        # An orchestration failure cannot revoke an already sealed safe unit.
        # Drop all unpublished rows; fresh source validation still controls
        # whether the immutable earlier proof can be retrieved.
        self._completed = {key: value for key, value in self._completed.items()
                           if key[0] in self._sealed_repeats}
        self._pending = None
        self._activation = None
        self._failed = True

    def start_invocation(self, repeat_index=None):
        if repeat_index is None:
            self.reset_attempt()
            return
        if (type(repeat_index) is not int or repeat_index < 0
                or repeat_index in self._sealed_repeats
                or repeat_index not in self._repeat_indexes()):
            raise DirectorLoraExecutionError("Director native repeat reset changed")
        self._completed = {key: value for key, value in self._completed.items() if key[0] < repeat_index}
        self._loaded = False
        self._load_request = None
        self._pending = None
        self._activation = None
        self._failed = False
        # Re-run actual preparation after an unpublished failed attempt. The
        # retained window records remain immutable and must match on retry.
        self._preparation_finished = {key: value for key, value in self._preparation_finished.items()
                                      if key < repeat_index}

    def discard_invocation(self, repeat_index=None):
        if repeat_index is None:
            self.discard()
            return
        self._completed = {key: value for key, value in self._completed.items() if key[0] < repeat_index}
        self._loaded = False
        self._load_request = None
        self._pending = None
        self._activation = None
        self._failed = True

    def _current(self):
        if self._failed:
            raise DirectorLoraExecutionError("Director LoRA attempt is no longer current")
        self._validate_binding()

    def _validate_binding(self):
        try:
            current = self._validate_current()
        except BaseException:
            self.discard()
            raise
        fields = {"child_digest", "task_digest", "sources"}
        if self._incremental:
            fields.add("adapter_sources")
        if (not isinstance(current, dict) or set(current) != fields
                or canonical(current) != canonical({key: self._plan[key] for key in current})):
            self.discard()
            raise DirectorLoraExecutionError("Director LoRA source or retained task changed")

    def require_repeat_count(self, count):
        self._current()
        if (type(count) is not int or count < 1
                or self._repeat_indexes() != list(range(count))):
            self.discard()
            raise DirectorLoraExecutionError("Director repeat count changed")

    def restore_completed_prefix(self, *, validate_completed):
        """Restore receipts authenticated and media-checked by the server.

        The private callback must verify retained producer signatures, child
        identity and accepted media before returning consumption receipts.
        Matching a receipt digest alone does not authenticate an output.
        """
        self._current()
        if (not callable(validate_completed) or self._loaded or self._completed
                or self._pending is not None or self._sealed_repeats):
            raise DirectorLoraExecutionError("Director completed prefix cannot be restored")
        try:
            receipts = validate_completed()
            if type(receipts) is not list:
                raise ValueError
            completed, sealed = {}, {}
            restored_windows, restored_finishes = dict(self._windows), {}
            for index, receipt in enumerate(receipts):
                if self._incremental:
                    if index not in self._repeat_indexes():
                        raise ValueError
                    records = receipt["binding"]["prepared_windows"]
                    if type(records) is not list or not records:
                        raise ValueError
                    records = [self._prepared_record(record) for record in records]
                    if any(record["repeat_index"] != index or record["window_index"] != ordinal
                           for ordinal, record in enumerate(records, 1)):
                        raise ValueError
                    finish = receipt["binding"]["preparation_finish"]
                    if (type(finish) is not dict or set(finish) != {"repeat_index", "reason", "windows_digest"}
                            or type(finish["repeat_index"]) is not int or finish["repeat_index"] != index
                            or finish["reason"] not in {"window_limit", "latent_tail", "control_short_tail", "control_exhausted"}
                            or finish["windows_digest"] != digest(records)):
                        raise ValueError
                    restored_windows.update({(index, record["window_index"]): record for record in records})
                    restored_finishes[index] = finish
                keys = sorted(key for key in restored_windows if key[0] == index)
                if not keys:
                    raise ValueError
                rows = []
                for key in keys:
                    record = restored_windows[key]
                    prompt = record["prompt"]
                    expanded = prompt + " " + " ".join(self._plan["expansions"]) if self._plan["expansions"] else prompt
                    rows.append({"window": {field: record[field] for field in WINDOW_FIELDS},
                        "native_digest": digest(self._plan["native"]),
                        "authored_prompt_digest": digest(prompt),
                        "consumed_prompt_digest": digest(expanded)})
                expected = self._repeat_proof(index, rows,
                    windows=records if self._incremental else None,
                    finish=restored_finishes.get(index))
                if canonical(receipt) != canonical(expected):
                    raise ValueError
                completed.update(zip(keys, copy.deepcopy(rows)))
                sealed[index] = copy.deepcopy(expected)
            # Revalidate after server authentication/media I/O, then install
            # all validated rows together. No partial prefix survives failure.
            self._current()
            self._completed, self._sealed_repeats = completed, sealed
            self._windows, self._ordered = restored_windows, sorted(restored_windows)
            self._preparation_finished = restored_finishes
        except BaseException:
            self.discard()
            raise

    def seed_for_repeat(self, repeat_index):
        self._current()
        if (type(repeat_index) is not int or repeat_index < 0
                or repeat_index in self._sealed_repeats
                or repeat_index not in self._repeat_indexes()):
            self.discard()
            raise DirectorLoraExecutionError("Director repeat seed is unavailable")
        return (self._plan["repeat_seeds"][repeat_index] if self._incremental
                else self._windows[repeat_index, 1]["seed"])

    def before_lora_load(self, paths, model_type, *, adapter_paths=None):
        self._current()
        if self._incremental:
            paths = [os.path.realpath(path) for path in paths]
        if (model_type != self._plan["native"]["model_type"]
                or canonical(paths) != canonical([s["path"] for s in self._plan["sources"]])):
            self.discard()
            raise DirectorLoraExecutionError("Director consumes different LoRA files")
        if self._loaded or self._load_request is not None:
            self.discard()
            raise DirectorLoraExecutionError("Director LoRA loading is already pending or complete")
        request = (tuple(paths), model_type)
        if self._incremental:
            if (type(adapter_paths) is not list
                    or canonical([os.path.realpath(path) for path in adapter_paths])
                       != canonical([source["path"] for source in self._plan["adapter_sources"]])):
                self.discard()
                raise DirectorLoraExecutionError("Director consumes a different complete adapter stack")
            request += (tuple(os.path.realpath(path) for path in adapter_paths),)
        self._load_request = request

    def lora_load_succeeded(self, paths, model_type, *, adapter_paths=None):
        """Called only after loader errors, synchronization and finalization.

        This is a consuming-path observation, not model/component attestation
        or GPU authority. The owner must supply those separate live checks.
        """
        self._current()
        request = (tuple(paths), model_type)
        if self._incremental:
            if type(adapter_paths) is not list:
                self.discard()
                raise DirectorLoraExecutionError("Director complete adapter load is unavailable")
            request = (tuple(os.path.realpath(path) for path in paths), model_type,
                       tuple(os.path.realpath(path) for path in adapter_paths))
        if (self._loaded or self._load_request is None
                or canonical(self._load_request) != canonical(request)):
            self.discard()
            raise DirectorLoraExecutionError("Director LoRA load completion changed")
        self._loaded = True
        self._load_request = None

    def lora_unloaded(self):
        # Cleanup can run after failure; it must not require current authority
        # or revoke previously sealed outputs, and must never permit a window.
        self._loaded = False
        self._load_request = None
        self._activation = None

    def before_window(self, *, native, window, prompt):
        self._current()
        try:
            key = window["repeat_index"], window["window_index"]
            expected = self._windows[key]
            expected_window = {field: expected[field] for field in WINDOW_FIELDS}
            if (not self._loaded or self._pending is not None or key in self._completed
                    or key != self._ordered[len(self._completed)]
                    or canonical(native) != canonical(self._plan["native"])
                    or canonical(window) != canonical(expected_window)
                    or type(prompt) is not str or prompt != expected["prompt"]):
                raise ValueError
            expanded = prompt
            if self._plan["expansions"]:
                expanded = prompt + " " + " ".join(self._plan["expansions"])
            receipt = {"window": copy.deepcopy(window), "native_digest": digest(native),
                "authored_prompt_digest": digest(prompt), "consumed_prompt_digest": digest(expanded)}
            token = digest({"child": self._plan["child_digest"], "task": self._plan["task_digest"], "receipt": receipt})
            self._pending = (key, token, receipt)
            self._activation = None
            return expanded, token
        except (KeyError, TypeError, ValueError, OverflowError):
            self.discard()
            raise DirectorLoraExecutionError("Director native LoRA window changed") from None

    def activation_observer(self, token, *, transformers):
        """Observe installed schedules for this window, without model authority.

        Transformer references come from the actual consuming path. Proving
        their loaded checkpoint identity remains the owning residency gate's
        responsibility; this observer cannot replace that gate.
        """
        self._current()
        if (self._pending is None or self._pending[1] != token
                or self._activation is not None or self._transformer_count is None
                or type(transformers) is not tuple
                or len(transformers) != self._transformer_count
                or any(item is None for item in transformers)
                or len({id(item) for item in transformers}) != len(transformers)):
            self.discard()
            raise DirectorLoraExecutionError("Director activation targets changed")
        activation = {"token": token, "targets": transformers, "completed": 0}
        self._activation = activation

        def observed(*, transformer, adapter_ids, schedules, raw_schedule,
                     steps, phase_switch_step, phase_switch_step2, timesteps):
            self._current()
            try:
                native = self._plan["native"]
                if (self._pending is None or self._pending[1] != token
                        or self._activation is not activation
                        or activation["completed"] >= len(transformers)
                        or transformer is not transformers[activation["completed"]]
                        or canonical(raw_schedule) != canonical(native["loras_slists"])
                        or type(steps) is not int or type(timesteps) is not list
                        or len(timesteps) != steps
                        or any(type(t) not in (float, int) or not math.isfinite(t)
                               for t in timesteps)):
                    raise ValueError
                # The native scheduler supports two capped timestep recipes.
                # This count comes from the retained solver/step request,
                # never from the activation arguments being observed.
                requested = native["num_inference_steps"]
                solver = native["sample_solver"]
                expected_steps = (min(requested, 9) if solver == "causvid"
                                  else min(requested, 8) if solver == "lcm"
                                  else requested)
                if (type(requested) is not int or not 1 <= requested <= 128
                        or steps != expected_steps
                        or type(phase_switch_step) is not int
                        or type(phase_switch_step2) is not int):
                    raise ValueError
                from shared.utils.loras_mutipliers import expand_slist, get_model_switch_steps
                expected_first, expected_second, _ = get_model_switch_steps(
                    timesteps, native["guidance_phases"],
                    native["model_switch_phase"] if len(transformers) == 2 else 0,
                    native["switch_threshold"], native["switch_threshold2"])
                if (phase_switch_step != expected_first or phase_switch_step2 != expected_second
                        or not 0 <= expected_first <= expected_second <= steps):
                    raise ValueError
                # Model/handler adapters precede user adapters in the native
                # stack. Version 2 binds that complete independently resolved
                # stack; version 1 keeps its historical user-only contract.
                expected_adapter_ids = (self._plan["adapter_ids"] if self._incremental
                               else [str(i) for i in range(len(native["activated_loras"]))])
                count = len(expected_adapter_ids)
                expected_schedule = [expand_slist(native["loras_slists"], i, steps,
                                    expected_first, expected_second) for i in range(count)]
                if (canonical(adapter_ids) != canonical(expected_adapter_ids)
                        or canonical(schedules) != canonical(expected_schedule)):
                    raise ValueError
                activation["completed"] += 1
            except (KeyError, TypeError, ValueError, OverflowError):
                self.discard()
                raise DirectorLoraExecutionError("Director installed LoRA activation changed") from None
            except BaseException:
                self.discard()
                raise

        return observed

    def window_succeeded(self, token):
        self._current()
        if (not self._loaded or self._pending is None or self._pending[1] != token
                or self._activation is None or self._activation["token"] != token
                or self._activation["completed"] != self._transformer_count):
            self.discard()
            raise DirectorLoraExecutionError("Director native LoRA completion changed")
        key, _, receipt = self._pending
        self._completed[key] = receipt
        self._pending = None
        self._activation = None

    def completed_repeat(self, repeat_index, *, expected_windows):
        planned = sorted(key for key in self._windows if key[0] == repeat_index)
        if (type(repeat_index) is not int or repeat_index < 0 or type(expected_windows) is not int
                or expected_windows < 1 or len(planned) != expected_windows):
            raise DirectorLoraExecutionError("Director native LoRA coverage is incomplete")
        if repeat_index in self._sealed_repeats:
            self._validate_binding()
            return copy.deepcopy(self._sealed_repeats[repeat_index])
        self._current()
        if (self._pending is not None
                or any(key not in self._completed for key in planned)
                or self._incremental and repeat_index not in self._preparation_finished):
            raise DirectorLoraExecutionError("Director native LoRA coverage is incomplete")
        sealed = self._repeat_proof(repeat_index,
            [copy.deepcopy(self._completed[key]) for key in planned],
            windows=[copy.deepcopy(self._windows[key]) for key in planned] if self._incremental else None,
            finish=copy.deepcopy(self._preparation_finished.get(repeat_index)))
        if repeat_index in self._sealed_repeats and self._sealed_repeats[repeat_index] != sealed:
            raise DirectorLoraExecutionError("Director sealed native repeat changed")
        self._sealed_repeats[repeat_index] = copy.deepcopy(sealed)
        return sealed
