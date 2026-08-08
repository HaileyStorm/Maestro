"""Data-only updater for MiniMax H3's official prepared-style catalog.

Maestro does not install, execute, or follow upstream agent skills.  It reads
only bounded text files from the official MiniMax-H3 GitHub repository,
normalizes a small display/prompt schema, and atomically publishes that schema
as a last-known-good cache for Studio's optional style selector.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


SOURCE_PAGE = "https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills"
_CONTENTS_ROOT = (
    "https://api.github.com/repos/MiniMax-AI/MiniMax-H3/contents/"
)
CONTENTS_API = _CONTENTS_ROOT + "skills/README.md?ref=main"
UPDATE_INTERVAL_SECONDS = 24 * 60 * 60
SCHEMA_VERSION = 2
MAX_STYLES = 32
MAX_LINKED_FILES = 48
MAX_README_BYTES = 512 * 1024
MAX_SKILL_BYTES = 512 * 1024
MAX_TEMPLATE_BYTES = 96 * 1024
MAX_TOTAL_LINKED_BYTES = 4 * 1024 * 1024
MAX_TEMPLATES_PER_STYLE = 2

_STYLE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_HEADING = re.compile(r"^###\s+([a-z0-9][a-z0-9-]{1,79})\s*$", re.MULTILINE)
_SECTION_HEADING = re.compile(r"^#{2,4}\s+(.{1,120})\s*$", re.MULTILINE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+[^)]*)?\)")
_MARKDOWN_MARKS = re.compile(r"[`*_>#]")
_STYLE_SECTION = re.compile(
    r"(?:style|visual|look|aesthetic|motion|camera|sound|negative)", re.IGNORECASE,
)
_TEMPLATE_NAME = re.compile(r"(?:template|style|prompt)", re.IGNORECASE)
_UNSAFE_DATA = re.compile(
    r"(?:<\s*script|javascript:|data:|file:|https?://|\{\{|\{%|"
    r"ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"tool[_ -]?call|execute\s+(?:a\s+)?command|run\s+(?:this\s+)?command|"
    r"shell\s+command|powershell|(?:^|\s)(?:curl|wget)\s|(?:^|\s)rm\s+-)",
    re.IGNORECASE,
)
_LEADING_INSTRUCTION = re.compile(
    r"^(?:ask|call|choose|confirm|create|delete|do|download|execute|generate|"
    r"install|open|provide|read|remove|run|send|use|write)\b",
    re.IGNORECASE,
)


_BUILTIN_STYLES = [
    {
        "id": "papercraft-stop-motion-explainer",
        "label": "Papercraft stop-motion explainer",
        "description": "Tactile handmade paper explainers with layered sets, props, visual metaphors, motion, transitions, and sound.",
        "prompt_brief": "Tactile cut paper, layered diorama sets, handmade props, readable visual metaphors, staged stop-motion, and paper-like sound.",
    },
    {
        "id": "paper-collage-explainer-generator",
        "label": "Paper-collage explainer",
        "description": "Tactile halftone collage explainers built from approved stills and stop-motion clips.",
        "prompt_brief": "Halftone paper collage, tactile cutouts, abstract visual metaphors, stop-motion movement, and collage sound effects.",
    },
    {
        "id": "3d-animation-short-generator",
        "label": "Stylized 3D animation short",
        "description": "Narrative 3D shorts with character, environment, shot, continuity, performance, camera, and audio planning.",
        "prompt_brief": "Stylized 3D narrative animation with consistent character cards, environments, performances, camera language, continuity, and sound.",
    },
    {
        "id": "minimalist-product-ad-generator",
        "label": "Minimalist product ad",
        "description": "Clean premium product shorts with concise copy, beat-synced typography, and polished camera language.",
        "prompt_brief": "Premium clean product film, concise on-screen copy, controlled typography, polished camera motion, and clear selling-point beats.",
    },
    {
        "id": "brand-promo-video-generator",
        "label": "Brand / product promo",
        "description": "Fact-grounded promotional shorts for products, sites, apps, shops, and personal projects.",
        "prompt_brief": "Fact-grounded promotional short with a clear narrative direction, capability and use-case beats, authorized assets, and a call to action.",
    },
    {
        "id": "music-video-subtitle-generator",
        "label": "Music video + lyric typography",
        "description": "Beat-aware connected music-video shots with lyric typography and long-work stitching guidance.",
        "prompt_brief": "Beat-reactive connected shots, spatial lyric typography, stable character and scene references, and audio-timed transitions.",
    },
    {
        "id": "co-op-game-intro-generator",
        "label": "Co-op game menu intro",
        "description": "Two-player character-led menu or opening animations with coordinated UI and interaction motion.",
        "prompt_brief": "Two-character game-menu opening with stable identity cues, coordinated player cards, UI copy, icons, and timed menu interaction.",
    },
    {
        "id": "handdrawn-live-video-generator",
        "label": "Hand-drawn + live-action fusion",
        "description": "Surreal shorts combining rough glowing hand-drawn animation with live-action spaces.",
        "prompt_brief": "Rough glowing hand-drawn animation interacting physically with live-action space, continuous morphing, and delayed handheld camera response.",
    },
]
_BUILTIN_BY_ID = {style["id"]: style for style in _BUILTIN_STYLES}


def _plain_text(value: str, *, limit: int) -> str:
    value = _MARKDOWN_LINK.sub(r"\1", str(value or ""))
    value = _MARKDOWN_MARKS.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].strip()


def _safe_text(value: str, *, limit: int) -> str:
    raw = str(value or "")
    if _UNSAFE_DATA.search(raw):
        return ""
    return _plain_text(raw, limit=limit)


def _normalize_repo_path(path: str, *, kind: str, skill_dir: str = "") -> str:
    candidate = urllib.parse.unquote(str(path or "").split("#", 1)[0].split("?", 1)[0])
    if not candidate or candidate.startswith(("/", "\\")) or ":" in candidate:
        raise ValueError("Official H3 catalog linked an unsupported path")
    candidate = candidate.replace("\\", "/")
    normalized = posixpath.normpath(candidate)
    if normalized.startswith("../") or normalized in (".", ".."):
        raise ValueError("Official H3 catalog linked outside its skills folder")
    components = normalized.split("/")
    if any(not _PATH_COMPONENT.fullmatch(component) for component in components):
        raise ValueError("Official H3 catalog linked an invalid path")
    if not normalized.startswith("skills/"):
        normalized = posixpath.join("skills", normalized)

    if kind == "readme":
        if normalized != "skills/README.md":
            raise ValueError("Unexpected H3 catalog entry point")
    elif kind == "skill":
        if len(normalized.split("/")) != 3 or not normalized.endswith("/SKILL.md"):
            raise ValueError("Only official per-style SKILL.md files are accepted")
    elif kind == "template":
        if not skill_dir or not normalized.startswith(skill_dir.rstrip("/") + "/"):
            raise ValueError("Style template escaped its official skill folder")
        suffix = Path(normalized).suffix.lower()
        if suffix not in (".md", ".txt") or not _TEMPLATE_NAME.search(Path(normalized).stem):
            raise ValueError("Only linked text style/prompt templates are accepted")
    else:
        raise ValueError("Unknown official H3 content kind")
    return normalized


def _readme_entries(markdown: str) -> list[dict[str, str]]:
    text = str(markdown or "")
    if len(text.encode("utf-8")) > MAX_README_BYTES:
        raise ValueError("Official H3 skills README exceeds the size limit")
    matches = list(_HEADING.finditer(text))
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        style_id = match.group(1)
        if style_id == "h3-prompt-writing" or style_id in seen:
            continue
        if not _STYLE_ID.fullmatch(style_id):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end():end]
        skill_path = ""
        for _label, href in _MARKDOWN_LINK.findall(section):
            try:
                candidate = _normalize_repo_path(href, kind="skill")
            except ValueError:
                continue
            skill_path = candidate
            break

        prose: list[str] = []
        for paragraph in re.split(r"\n\s*\n", section):
            if _MARKDOWN_LINK.search(paragraph):
                continue
            compact = _safe_text(paragraph, limit=800)
            if not compact or compact.startswith("Installable under"):
                continue
            prose.append(compact)
            if sum(len(item) for item in prose) >= 500:
                break
        description = _safe_text(" ".join(prose), limit=600)
        builtin = _BUILTIN_BY_ID.get(style_id)
        if len(description) < 20 and not builtin:
            continue
        entries.append({
            "id": style_id,
            "skill_path": skill_path,
            "readme_description": description,
        })
        seen.add(style_id)
        if len(entries) > MAX_STYLES:
            raise ValueError("Official H3 skill catalog exceeds the style limit")
    if not entries:
        raise ValueError("Official H3 skill catalog did not match the bounded schema")
    return entries


def _frontmatter(markdown: str) -> dict[str, str]:
    lines = str(markdown or "").replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(index for index in range(1, min(len(lines), 80)) if lines[index].strip() == "---")
    except StopIteration:
        return {}
    result: dict[str, str] = {}
    index = 1
    while index < end:
        match = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]{0,39}):\s*(.*)$", lines[index])
        if not match:
            index += 1
            continue
        key, raw_value = match.group(1).lower(), match.group(2).strip()
        values: list[str] = [] if raw_value in ("|", ">") else [raw_value]
        index += 1
        while index < end:
            if re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}:\s*", lines[index]):
                break
            if lines[index].strip():
                values.append(lines[index].strip())
            index += 1
        if key in ("name", "description"):
            result[key] = " ".join(values)
    return result


def _style_fragments(markdown: str) -> list[str]:
    """Extract inert declarative style phrases from explicitly named sections."""
    text = str(markdown or "")
    headings = list(_SECTION_HEADING.finditer(text))
    fragments: list[str] = []
    for index, heading in enumerate(headings):
        if not _STYLE_SECTION.search(heading.group(1)):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.end():end]
        for raw in section.splitlines():
            bullet = re.match(r"^\s*[-*]\s+(.{3,240})\s*$", raw)
            if not bullet:
                continue
            value = _safe_text(bullet.group(1), limit=180)
            if not value or _LEADING_INSTRUCTION.search(value):
                continue
            fragments.append(value.rstrip("."))
            if len(fragments) >= 8:
                return fragments
    return fragments


def _template_paths(skill_path: str, markdown: str) -> list[str]:
    skill_dir = posixpath.dirname(skill_path)
    result: list[str] = []
    for _label, href in _MARKDOWN_LINK.findall(str(markdown or "")):
        if urllib.parse.urlsplit(href).scheme or href.startswith(("/", "\\")):
            continue
        joined = posixpath.normpath(posixpath.join(skill_dir, href))
        try:
            candidate = _normalize_repo_path(joined, kind="template", skill_dir=skill_dir)
        except ValueError:
            continue
        if candidate not in result:
            result.append(candidate)
        if len(result) >= MAX_TEMPLATES_PER_STYLE:
            break
    return result


def _normalize_style(style: dict[str, Any]) -> dict[str, str]:
    style_id = str(style.get("id") or "")
    if not _STYLE_ID.fullmatch(style_id):
        raise ValueError("Invalid H3 style identifier")
    label = _safe_text(style.get("label", ""), limit=100)
    description = _safe_text(style.get("description", ""), limit=600)
    prompt_brief = _safe_text(style.get("prompt_brief", ""), limit=400)
    if not label or len(description) < 20 or len(prompt_brief) < 20:
        raise ValueError("Incomplete H3 style data")
    return {
        "id": style_id,
        "label": label,
        "description": description,
        "prompt_brief": prompt_brief,
    }


def parse_official_skills_readme(
    markdown: str,
    *,
    revision: str,
    skill_documents: dict[str, str] | None = None,
    template_documents: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize official text as inert catalog data; never follow instructions."""
    skill_documents = skill_documents or {}
    template_documents = template_documents or {}
    styles: list[dict[str, str]] = []
    for entry in _readme_entries(markdown):
        style_id = entry["id"]
        builtin = _BUILTIN_BY_ID.get(style_id, {})
        skill_text = skill_documents.get(entry["skill_path"], "")
        metadata = _frontmatter(skill_text)
        linked_description = ""
        if metadata.get("name") in (None, "", style_id):
            linked_description = _safe_text(metadata.get("description", ""), limit=600)
        description = linked_description or entry["readme_description"] or str(builtin.get("description") or "")

        fragments = _style_fragments(skill_text)
        for template_path in _template_paths(entry["skill_path"], skill_text) if entry["skill_path"] else []:
            fragments.extend(_style_fragments(template_documents.get(template_path, "")))
            if len(fragments) >= 8:
                break
        derived_brief = ", ".join(fragments[:8])
        brief = str(builtin.get("prompt_brief") or derived_brief or description[:360])
        label = str(builtin.get("label") or style_id.replace("-", " ").title())
        try:
            styles.append(_normalize_style({
                "id": style_id,
                "label": label,
                "description": description,
                "prompt_brief": brief,
            }))
        except ValueError:
            continue
    if not 1 <= len(styles) <= MAX_STYLES:
        raise ValueError("Official H3 skill catalog did not match the bounded schema")
    return {
        "source": SOURCE_PAGE,
        "revision": _safe_text(str(revision or ""), limit=80),
        "styles": styles,
    }


def _validate_catalog(catalog: Any) -> dict[str, Any]:
    if not isinstance(catalog, dict) or catalog.get("source") != SOURCE_PAGE:
        raise ValueError("Invalid cached H3 catalog source")
    revision = _safe_text(catalog.get("revision", ""), limit=80)
    raw_styles = catalog.get("styles")
    if not revision or not isinstance(raw_styles, list) or not 1 <= len(raw_styles) <= MAX_STYLES:
        raise ValueError("Invalid cached H3 catalog")
    styles = [_normalize_style(style) for style in raw_styles]
    if len({style["id"] for style in styles}) != len(styles):
        raise ValueError("Duplicate cached H3 styles")
    return {"source": SOURCE_PAGE, "revision": revision, "styles": styles}


def builtin_catalog() -> dict[str, Any]:
    return {
        "source": SOURCE_PAGE,
        "revision": "bundled",
        "checked_at": None,
        "update_status": "bundled_fallback",
        "styles": [dict(style) for style in _BUILTIN_STYLES],
    }


class H3SkillCatalogUpdater:
    def __init__(
        self,
        cache_path: str | os.PathLike[str],
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.opener = opener
        self.now = now
        self._lock = threading.RLock()

    def _fetch_document(self, path: str, *, kind: str, max_bytes: int, skill_dir: str = "") -> tuple[str, str]:
        normalized = _normalize_repo_path(path, kind=kind, skill_dir=skill_dir)
        url = _CONTENTS_ROOT + urllib.parse.quote(normalized, safe="/") + "?ref=main"
        parsed_url = urllib.parse.urlsplit(url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "api.github.com":
            raise ValueError("Official H3 catalog host validation failed")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Maestro-H3-style-catalog",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        envelope_limit = max_bytes * 2 + 16_384
        with self.opener(request, timeout=12) as response:
            if int(getattr(response, "status", 200)) != 200:
                raise OSError("GitHub catalog request failed")
            raw = response.read(envelope_limit + 1)
        if len(raw) > envelope_limit:
            raise ValueError("GitHub catalog response exceeds the size limit")
        envelope = json.loads(raw.decode("utf-8"))
        encoded = envelope.get("content")
        if not isinstance(encoded, str) or envelope.get("encoding") != "base64":
            raise ValueError("GitHub catalog response has an unsupported encoding")
        # GitHub's contents API wraps base64 at fixed line lengths.  Remove
        # only ASCII whitespace, then keep strict alphabet/padding validation.
        decoded = base64.b64decode(re.sub(r"[\t\r\n ]+", "", encoded), validate=True)
        if len(decoded) > max_bytes:
            raise ValueError("Official H3 document exceeds the size limit")
        return decoded.decode("utf-8"), _safe_text(envelope.get("sha", ""), limit=80)

    def _atomic_store(self, catalog: dict[str, Any], checked_at: float) -> None:
        payload = {
            "schema": SCHEMA_VERSION,
            "checked_at": checked_at,
            "catalog": _validate_catalog(catalog),
        }
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=self.cache_path.name + ".",
            suffix=".tmp",
            dir=self.cache_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.cache_path)
            try:
                directory_fd = os.open(self.cache_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if payload.get("schema") != SCHEMA_VERSION:
                    raise ValueError("Unsupported H3 catalog cache schema")
                parsed = _validate_catalog(payload.get("catalog"))
                parsed.update({
                    "checked_at": float(payload.get("checked_at") or 0) or None,
                    "update_status": "cached",
                })
                return parsed
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                return builtin_catalog()

    def refresh(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            current = self.load()
            checked_at = float(current.get("checked_at") or 0)
            if not force and checked_at and self.now() - checked_at < UPDATE_INTERVAL_SECONDS:
                return current
            try:
                readme, readme_sha = self._fetch_document(
                    "skills/README.md", kind="readme", max_bytes=MAX_README_BYTES,
                )
                entries = _readme_entries(readme)
                skill_documents: dict[str, str] = {}
                template_documents: dict[str, str] = {}
                revisions = {"skills/README.md": readme_sha}
                total_bytes = 0
                linked_count = 0
                for entry in entries:
                    skill_path = entry["skill_path"]
                    if not skill_path:
                        continue
                    linked_count += 1
                    if linked_count > MAX_LINKED_FILES:
                        raise ValueError("Official H3 catalog has too many linked documents")
                    skill_text, skill_sha = self._fetch_document(
                        skill_path, kind="skill", max_bytes=MAX_SKILL_BYTES,
                    )
                    total_bytes += len(skill_text.encode("utf-8"))
                    if total_bytes > MAX_TOTAL_LINKED_BYTES:
                        raise ValueError("Official H3 linked content exceeds the total size limit")
                    skill_documents[skill_path] = skill_text
                    revisions[skill_path] = skill_sha
                    skill_dir = posixpath.dirname(skill_path)
                    for template_path in _template_paths(skill_path, skill_text):
                        linked_count += 1
                        if linked_count > MAX_LINKED_FILES:
                            raise ValueError("Official H3 catalog has too many linked documents")
                        template_text, template_sha = self._fetch_document(
                            template_path,
                            kind="template",
                            max_bytes=MAX_TEMPLATE_BYTES,
                            skill_dir=skill_dir,
                        )
                        total_bytes += len(template_text.encode("utf-8"))
                        if total_bytes > MAX_TOTAL_LINKED_BYTES:
                            raise ValueError("Official H3 linked content exceeds the total size limit")
                        template_documents[template_path] = template_text
                        revisions[template_path] = template_sha

                digest = hashlib.sha256()
                for path, revision in sorted(revisions.items()):
                    digest.update(path.encode("utf-8"))
                    digest.update(b"\0")
                    digest.update(revision.encode("utf-8"))
                    digest.update(b"\0")
                parsed = parse_official_skills_readme(
                    readme,
                    revision=digest.hexdigest(),
                    skill_documents=skill_documents,
                    template_documents=template_documents,
                )
                checked_at = self.now()
                self._atomic_store(parsed, checked_at)
                parsed.update({"checked_at": checked_at, "update_status": "updated"})
                return parsed
            except Exception as error:
                current = self.load()
                current["update_status"] = "offline_fallback"
                current["update_error"] = str(error)[:300]
                return current
