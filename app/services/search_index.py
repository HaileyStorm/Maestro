"""Workspace-scoped search and artifact classification for the gallery.

Only direct children of a workspace are inspected.  Search metadata comes from
``.meta.json`` sidecars and is mapped back to the exact media basename before
it enters the inverted index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import json
import math
import os
import re
import shlex
import threading
import time
from typing import Iterable, Mapping, Optional


GALLERY_MEDIA_EXTENSIONS = {
    ".mp4", ".webm", ".mkv", ".mov", ".gif",
    ".png", ".jpg", ".jpeg", ".webp",
    ".wav", ".mp3", ".flac", ".ogg",
}
ARTIFACT_CLASSES = {"final", "component", "window", "temporary"}
_STRUCTURED_SEARCH_KEYS = {"model", "lora", "seed", "reference", "after", "before"}
_REFERENCE_PARAM_KEYS = {
    "image_start", "image_end", "image_refs", "image_guide", "video_guide",
    "video_guide2", "video_guide3", "video_source",
    "audio_guide", "audio_guide2", "audio_guide3", "reference_image_path",
    "reference_video_path", "reference_audio_path",
}


class ArtifactScope(str, Enum):
    FINAL = "final"
    ALL = "all"
    COMPONENTS = "components"
    COMPONENT = "component"
    WINDOW = "window"
    TEMPORARY = "temporary"


def _canonical_workspace(workspace_dir: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(workspace_dir)))


def _direct_file(workspace_dir: str, name: str) -> bool:
    lexical = os.path.abspath(os.path.join(workspace_dir, name))
    path = os.path.realpath(lexical)
    return (
        os.path.normcase(os.path.dirname(path)) == os.path.normcase(workspace_dir)
        and not os.path.islink(lexical)
        and os.path.isfile(path)
    )


def _media_names(workspace_dir: str) -> set[str]:
    try:
        entries = os.listdir(workspace_dir)
    except OSError:
        return set()
    return {
        name
        for name in entries
        if (
            not name.startswith(".")
            and os.path.splitext(name)[1].lower() in GALLERY_MEDIA_EXTENSIONS
            and _direct_file(workspace_dir, name)
        )
    }


def _sidecar_media_name(
    sidecar_name: str,
    meta: Mapping[str, object],
    media_by_stem: Mapping[str, list[str]],
    media_names: set[str],
) -> Optional[str]:
    """Resolve one sidecar to one exact media basename.

    New sidecars carry ``output_filename``.  Legacy sidecars are accepted only
    when their extensionless stem identifies exactly one real media file; an
    ambiguous stem must not leak metadata from one extension to another.
    """
    sidecar_stem = sidecar_name[: -len(".meta.json")]
    explicit = meta.get("output_filename")
    if (
        isinstance(explicit, str)
        and explicit == os.path.basename(explicit)
        and explicit in media_names
        and os.path.splitext(explicit)[0] == sidecar_stem
    ):
        return explicit

    candidates = media_by_stem.get(sidecar_stem, [])
    return candidates[0] if len(candidates) == 1 else None


def load_media_sidecars(
    workspace_dir: str,
    media_names: Optional[Iterable[str]] = None,
) -> dict[str, dict]:
    """Read direct-child gallery sidecars keyed by exact media filename."""
    workspace_dir = _canonical_workspace(workspace_dir)
    names = set(media_names) if media_names is not None else _media_names(workspace_dir)
    media_by_stem: dict[str, list[str]] = {}
    for name in sorted(names):
        media_by_stem.setdefault(os.path.splitext(name)[0], []).append(name)

    try:
        sidecar_names = sorted(
            name
            for name in os.listdir(workspace_dir)
            if name.endswith(".meta.json") and _direct_file(workspace_dir, name)
        )
    except OSError:
        return {}

    result: dict[str, dict] = {}
    for sidecar_name in sidecar_names:
        try:
            with open(os.path.join(workspace_dir, sidecar_name), "r", encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        media_name = _sidecar_media_name(sidecar_name, meta, media_by_stem, names)
        if media_name is not None:
            result[media_name] = meta
    return result


_TEMPORARY_RE = re.compile(
    r"(?:^_|(?:^|[._-])tmp(?:[._-]|$)|[._-](?:stitched|muxed)(?:[._-]|$)|\.dynaudnorm\.)",
    re.IGNORECASE,
)
_WINDOW_RE = re.compile(r"(?:^|[-_])window[-_]?\d+(?=[-_.]|$)", re.IGNORECASE)
_RECOVERY_FIXED_ROLES = {
    "h3_segment": "component",
    "h3_concat": "final",
    "h3_delivery": "final",
}


def _modern_producer_role(meta: Mapping[str, object]) -> Optional[str]:
    """Resolve modern recovery roles without trusting a media filename."""
    kind = meta.get("producer_unit_kind")
    if not isinstance(kind, str) or not kind:
        return None
    if meta.get("delivery_native_source") is True and meta.get("artifact_class") == "temporary":
        return "temporary"
    fixed = _RECOVERY_FIXED_ROLES.get(kind)
    if fixed is not None:
        return fixed
    declared = meta.get("producer_artifact_class")
    if kind == "ordinary_repeat" and declared in ARTIFACT_CLASSES:
        return str(declared)
    # A producer-unit marker proves this is modern evidence, but an unknown or
    # incomplete role does not prove finality. Keep it visible as a component.
    return "component"


def _is_sliding_window(meta: Mapping[str, object]) -> bool:
    params = meta.get("params")
    if not isinstance(params, Mapping):
        return False
    try:
        total = int(params.get("video_length") or 0)
        window = int(params.get("sliding_window_size") or 0)
    except (TypeError, ValueError):
        return False
    return window > 0 and total > window


def classify_gallery_artifacts(entries: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Classify listed media without removing any artifact from the listing.

    ``entries`` must contain ``name`` and may contain ``meta``, ``size`` and
    ``created_at``.  For cumulative sliding-window saves, each monotonic size
    run contributes one final; earlier cumulative saves are windows.  A size
    reset begins another repeat-generation run.
    """
    normalized = [dict(entry) for entry in entries]
    classes: dict[str, str] = {}
    explicit_classes: set[str] = set()
    sliding_groups: dict[tuple[str, str], list[dict]] = {}

    for entry in normalized:
        name = str(entry.get("name") or "")
        meta = entry.get("meta") if isinstance(entry.get("meta"), Mapping) else {}
        modern_role = _modern_producer_role(meta)
        if modern_role is not None:
            classes[name] = modern_role
            explicit_classes.add(name)
            continue
        explicit = meta.get("artifact_class")
        if isinstance(explicit, str) and explicit in ARTIFACT_CLASSES:
            classes[name] = explicit
            explicit_classes.add(name)
            continue
        if _TEMPORARY_RE.search(name):
            classes[name] = "temporary"
            continue
        if _WINDOW_RE.search(name):
            classes[name] = "window"
            explicit_classes.add(name)

        job_id = meta.get("job_id")
        params = meta.get("params") if isinstance(meta.get("params"), Mapping) else {}
        multi_clip = params.get("multi_clip_info")
        clip_key = ""
        if isinstance(multi_clip, Mapping):
            clip_key = str(multi_clip.get("index", ""))
        if job_id and _is_sliding_window(meta):
            sliding_groups.setdefault((str(job_id), clip_key), []).append(entry)

    # WGP saves a cumulative file after every window.  Detect repeat-generation
    # boundaries by a non-increasing size and retain one final per run.
    for group in sliding_groups.values():
        ordered = sorted(
            group,
            key=lambda entry: (float(entry.get("created_at") or 0), str(entry.get("name") or "")),
        )
        runs: list[list[dict]] = []
        for entry in ordered:
            size = int(entry.get("size") or 0)
            if runs and size <= int(runs[-1][-1].get("size") or 0):
                runs.append([])
            if not runs:
                runs.append([])
            runs[-1].append(entry)
        for run in runs:
            if len(run) < 2:
                continue
            final_entry = max(
                run,
                key=lambda entry: (int(entry.get("size") or 0), float(entry.get("created_at") or 0)),
            )
            final_name = str(final_entry.get("name") or "")
            for entry in run:
                name = str(entry.get("name") or "")
                if name == final_name and name not in explicit_classes:
                    classes.pop(name, None)
                elif name != final_name and name not in explicit_classes:
                    classes[name] = "window"

    for entry in normalized:
        name = str(entry.get("name") or "")
        if name in classes:
            continue
        meta = entry.get("meta") if isinstance(entry.get("meta"), Mapping) else {}
        params = meta.get("params") if isinstance(meta.get("params"), Mapping) else {}
        multi_clip = params.get("multi_clip_info")
        if (
            isinstance(multi_clip, Mapping)
            and "multiclip" not in name.lower()
        ) or meta.get("director_clip_index") is not None:
            classes[name] = "component"
        else:
            classes[name] = "final"
    return classes


def artifact_matches_scope(artifact_class: str, scope: ArtifactScope | str) -> bool:
    value = scope.value if isinstance(scope, ArtifactScope) else str(scope)
    if value == ArtifactScope.ALL.value:
        return True
    if value == ArtifactScope.COMPONENTS.value:
        return artifact_class != "final"
    if value in {"component", "window", "temporary"}:
        return artifact_class == value
    return artifact_class == "final"


def artifact_lineage(meta: Mapping[str, object]) -> Optional[tuple[str, ...]]:
    """Return stable sidecar lineage suitable for contained cleanup.

    Explicit lineage wins.  Multi-clip groups intentionally span seeds, while
    ordinary jobs use the resolved seed to avoid sweeping unrelated repeats.
    No filename-derived fallback is permitted.
    """
    explicit = meta.get("artifact_lineage")
    if isinstance(explicit, str) and explicit.strip():
        return ("explicit", explicit.strip())

    job_id = meta.get("job_id")
    if job_id in (None, ""):
        return None
    params = meta.get("params") if isinstance(meta.get("params"), Mapping) else {}
    multi_clip = params.get("multi_clip_info")
    if isinstance(multi_clip, Mapping):
        group_id = multi_clip.get("group_id")
        if group_id not in (None, ""):
            return ("group", str(job_id), str(group_id))
    group_id = meta.get("group_id")
    if group_id not in (None, ""):
        return ("group", str(job_id), str(group_id))

    seed = params.get("seed")
    if seed not in (None, "", -1, "-1"):
        return ("seed", str(job_id), str(seed))
    return None


def linked_component_names(
    target_name: str,
    sidecars: Mapping[str, Mapping[str, object]],
    classes: Mapping[str, str],
) -> list[str]:
    """Return sidecar-backed non-final siblings sharing target lineage."""
    target_meta = sidecars.get(target_name)
    if target_meta is None or classes.get(target_name) != "final":
        return []
    lineage = artifact_lineage(target_meta)
    if lineage is None:
        return []
    linked = []
    for name, meta in sidecars.items():
        artifact_class = classes.get(name)
        if (
            name == target_name
            or artifact_class not in {"component", "window", "temporary"}
            or artifact_lineage(meta) != lineage
        ):
            continue
        explicit = meta.get("artifact_class")
        if explicit == artifact_class:
            linked.append(name)
            continue
        params = meta.get("params") if isinstance(meta.get("params"), Mapping) else {}
        multi_clip = params.get("multi_clip_info")
        structurally_component = (
            meta.get("director_clip_index") is not None
            or isinstance(multi_clip, Mapping)
        )
        # Conservative legacy compatibility: multi-clip structure is an
        # unambiguous producer relationship. Sliding-window settings alone are
        # not: an unrelated, legitimate final can have those settings and a
        # filename that resembles a window. Producer-written artifact_class is
        # therefore mandatory for destructive window/temporary cleanup.
        if artifact_class == "component" and structurally_component:
            linked.append(name)
    return sorted(linked)


@dataclass
class _WorkspaceState:
    index: dict[str, set[str]] = field(default_factory=dict)
    indexed_files: set[str] = field(default_factory=set)
    documents: dict[str, "_SearchDocument"] = field(default_factory=dict)
    snapshot: tuple = ()
    built: bool = False
    last_build_time: float = 0


@dataclass(frozen=True)
class _SearchDocument:
    model: str = ""
    loras: tuple[str, ...] = ()
    seed: str = ""
    has_reference: bool = False
    created_at: float = 0.0


def _list_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _has_reference_metadata(meta: Mapping[str, object], params: Mapping[str, object]) -> bool:
    upload_filenames = meta.get("upload_filenames")
    sources: list[Mapping[str, object]] = [params]
    if isinstance(upload_filenames, Mapping):
        sources.append(upload_filenames)
    return any(
        key in source and bool(source.get(key))
        for source in sources
        for key in _REFERENCE_PARAM_KEYS
    )


def _sidecar_created_at(meta: Mapping[str, object], fallback: float = 0.0) -> float:
    raw = meta.get("created_at")
    if raw not in (None, ""):
        try:
            parsed = float(raw)
            if math.isfinite(parsed) and parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
                if math.isfinite(parsed) and parsed > 0:
                    return parsed
            except (TypeError, ValueError):
                pass
    normalized_fallback = float(fallback or 0.0)
    return normalized_fallback if math.isfinite(normalized_fallback) and normalized_fallback > 0 else 0.0


def _parse_search_query(query: str) -> tuple[list[str], dict[str, str]]:
    """Split user text from UI-generated field selectors.

    Quoted selector values are supported (``lora:"cinematic light"``). Unknown
    prefixes remain ordinary search text, so a user's prose cannot silently
    turn into a new filter when the UI and backend are on different versions.
    """
    try:
        parts = shlex.split(query)
    except ValueError:
        parts = query.split()
    text_parts: list[str] = []
    filters: dict[str, str] = {}
    for part in parts:
        key, separator, value = part.partition(":")
        normalized_key = key.casefold()
        if separator and normalized_key in _STRUCTURED_SEARCH_KEYS and value.strip():
            filters[normalized_key] = value.strip()
        else:
            text_parts.append(part)
    return text_parts, filters


def _date_boundary(value: str, *, exclusive_end: bool = False) -> float | None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    if exclusive_end:
        parsed += timedelta(days=1)
    return parsed.timestamp()


def _matches_structured_filters(document: _SearchDocument, filters: Mapping[str, str]) -> bool:
    model = filters.get("model", "").casefold()
    if model and model not in document.model.casefold():
        return False
    lora = filters.get("lora", "").casefold()
    if lora and not any(lora in name.casefold() for name in document.loras):
        return False
    seed = filters.get("seed", "")
    if seed and seed != document.seed:
        return False
    reference = filters.get("reference", "").casefold()
    if reference in {"with", "yes", "true", "1"} and not document.has_reference:
        return False
    if reference in {"without", "no", "false", "0"} and document.has_reference:
        return False
    after = _date_boundary(filters.get("after", ""))
    if after is not None and document.created_at < after:
        return False
    before = _date_boundary(filters.get("before", ""), exclusive_end=True)
    if before is not None and document.created_at >= before:
        return False
    return True


class SearchIndex:
    def __init__(self):
        self._workspaces: dict[str, _WorkspaceState] = {}
        self._lock = threading.Lock()

    def search(self, query: str, workspace_dir: str) -> set[str]:
        """Return exact media filenames matching text and field selectors."""
        if not query.strip():
            return set()
        workspace = _canonical_workspace(workspace_dir)
        text_parts, filters = _parse_search_query(query)
        tokens = self._tokenize(" ".join(text_parts))
        if not tokens and not filters:
            return set()

        with self._lock:
            state = self._workspaces.setdefault(workspace, _WorkspaceState())
            snapshot = self._snapshot(workspace)
            if not state.built or snapshot != state.snapshot:
                self._rebuild(workspace, state, snapshot)

            result: Optional[set[str]] = (
                {
                    name for name, document in state.documents.items()
                    if _matches_structured_filters(document, filters)
                }
                if filters else None
            )
            for token in tokens:
                matches = state.index.get(token, set())
                result = set(matches) if result is None else result & matches
                if not result:
                    return set()
            return result or set()

    def invalidate(self):
        """Force all workspace indexes to refresh on their next search."""
        with self._lock:
            for state in self._workspaces.values():
                state.built = False

    def remove_file(self, filename: str):
        """Invalidate cached workspaces after a gallery deletion or move."""
        del filename  # The caller lacks a workspace id; refresh every safe scope.
        self.invalidate()

    @staticmethod
    def _snapshot(workspace_dir: str) -> tuple:
        try:
            names = os.listdir(workspace_dir)
        except OSError:
            return ()
        snapshot = []
        for name in names:
            if not (
                name.endswith(".meta.json")
                or os.path.splitext(name)[1].lower() in GALLERY_MEDIA_EXTENSIONS
            ):
                continue
            try:
                stat = os.stat(os.path.join(workspace_dir, name))
            except OSError:
                continue
            if not _direct_file(workspace_dir, name):
                continue
            snapshot.append((name, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size))
        return tuple(sorted(snapshot))

    def _rebuild(self, workspace: str, state: _WorkspaceState, snapshot: tuple):
        started = time.time()
        state.index.clear()
        state.indexed_files.clear()
        state.documents.clear()
        sidecars = load_media_sidecars(workspace)
        for media_name, meta in sidecars.items():
            media_created_at = 0.0
            media_path = os.path.join(workspace, media_name)
            if _direct_file(workspace, media_name):
                try:
                    media_created_at = os.path.getmtime(media_path)
                except OSError:
                    pass
            self._index_file(state, media_name, meta, media_created_at=media_created_at)
        state.snapshot = snapshot
        state.built = True
        state.last_build_time = time.time()
        print(
            f"[SearchIndex] Built workspace index: {len(sidecars)} files in "
            f"{time.time() - started:.2f}s, {len(state.index)} tokens"
        )

    def _index_file(
        self,
        state: _WorkspaceState,
        media_name: str,
        meta: dict,
        *,
        media_created_at: float = 0.0,
    ):
        state.indexed_files.add(media_name)
        searchable_parts = [media_name]
        params = meta.get("params", {})
        if not isinstance(params, Mapping):
            params = {}
        model = str(params.get("model_type") or meta.get("model_type") or "")
        loras = tuple(
            os.path.basename(value)
            for value in _list_strings(params.get("activated_loras"))
        )
        weights = params.get("lora_weights")
        if isinstance(weights, Mapping):
            loras = tuple(dict.fromkeys((*loras, *(os.path.basename(str(key)) for key in weights))))
        seed_value = params.get("seed")
        seed = "" if seed_value in (None, "") else str(seed_value)
        state.documents[media_name] = _SearchDocument(
            model=model,
            loras=loras,
            seed=seed,
            has_reference=_has_reference_metadata(meta, params),
            created_at=_sidecar_created_at(meta, media_created_at),
        )
        for key in ("prompt", "negative_prompt", "model_type"):
            value = params.get(key)
            if value:
                searchable_parts.append(str(value))
        searchable_parts.extend(loras)
        if seed:
            searchable_parts.append(seed)
        window_prompts = params.get("window_prompts")
        if isinstance(window_prompts, (list, tuple)):
            for window_prompt in window_prompts:
                if window_prompt:
                    searchable_parts.append(str(window_prompt))
        mode = meta.get("generation_mode")
        if mode:
            searchable_parts.append(str(mode))
        for token in self._tokenize(" ".join(searchable_parts)):
            state.index.setdefault(token, set()).add(media_name)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = re.split(r'[\s,._\-/\\()\[\]{}:;!?"+]+', text.lower())
        return [token for token in tokens if len(token) >= 2]


_search_index = SearchIndex()


def get_search_index() -> SearchIndex:
    return _search_index
