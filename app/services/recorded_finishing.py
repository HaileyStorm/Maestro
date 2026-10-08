"""Bounded observed finishing records from exact admitted Gallery sidecars."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat

STEPS = {"upscale", "delivery_fit", "film_grain", "voice_clone", "audio_normalization"}
OUTCOMES = {"applied", "not_applied", "unconfirmed"}
METHOD = re.compile(r"(?:flashvsr2pass|flashvsr|lanczos|dlss5\*)\d+(?:\.\d+)?", re.IGNORECASE)


def _method(value):
    return type(value) is str and len(value) <= 40 and METHOD.fullmatch(value)


def sanitize_history(history, *, _depth=0, _budget=None):
    """Retain source boundaries, never infer events from recipe settings."""
    budget = [64] if _budget is None else _budget
    if (type(history) is not dict or type(history.get("version")) is not int
            or history['version'] not in {1, 2} or type(history.get('steps')) is not list
            or type(history.get('omitted_steps', 0)) is not int
            or not 0 <= history.get('omitted_steps', 0) <= 1_000_000 or budget[0] <= 0):
        return None
    budget[0] -= 1
    steps = []
    for record in history['steps']:
        if (type(record) is dict and type(record.get('step')) is str and record['step'] in STEPS
                and type(record.get('outcome')) is str and record['outcome'] in OUTCOMES):
            event = {'step': record['step'], 'outcome': record['outcome']}
            if record['step'] == 'upscale' and _method(record.get('method')):
                event['method'] = record['method']
            steps.append(event)
    omitted = history.get('omitted_steps', 0) + max(0, len(steps) - 32)
    if omitted > 1_000_000:
        raise ValueError('Recorded finishing history exceeds its supported limit.')
    result = {'version': history['version'], 'steps': steps[-32:]}
    if omitted:
        result['omitted_steps'] = omitted
    if history['version'] == 2:
        branches = history.get('branches')
        dropped = history.get('omitted_branches', 0)
        if (type(branches) is not list or type(dropped) is not int or not 0 <= dropped <= 1_000_000):
            return None
        kept = []
        for branch in branches:
            if (type(branch) is not dict or type(branch.get('name')) is not str
                    or not 1 <= len(branch['name']) <= 255 or branch['name'] in {'.', '..'}
                    or any(c in branch['name'] for c in ('/', '\\', '\x00'))
                    or type(branch.get('revision')) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}', branch['revision'])
                    or any(type(branch.get(k)) not in (int, float) or not 0 <= branch[k] <= 1_000_000_000
                           or not math.isfinite(branch[k]) for k in ('source_in', 'duration'))):
                continue
            if (_depth >= 3 or len(kept) >= 8 or budget[0] <= 0
                    or (type(branch.get('history')) is dict and budget[0] < 2)):
                dropped += 1
                continue
            budget[0] -= 1
            clean = {k: branch[k] for k in ('name', 'revision', 'source_in', 'duration')}
            child = sanitize_history(branch.get('history'), _depth=_depth + 1, _budget=budget)
            if child is not None:
                clean['history'] = child
            kept.append(clean)
        result['branches'] = kept
        if dropped:
            result['omitted_branches'] = min(dropped, 1_000_000)
    return result if steps or omitted or result.get('branches') or result.get('omitted_branches') else None


def append_observed_tool(history, tool, params):
    clean = sanitize_history(history)
    if tool not in {'upscale', 'revoice'}:
        return clean
    clean = clean or {'version': 1, 'steps': []}
    event = {'step': 'upscale' if tool == 'upscale' else 'voice_clone', 'outcome': 'applied'}
    if tool == 'upscale' and _method(params.get('method')):
        event['method'] = params['method']
    clean['steps'].append(event)
    omitted = clean.get('omitted_steps', 0) + max(0, len(clean['steps']) - 32)
    if omitted > 1_000_000:
        raise ValueError('Recorded finishing history exceeds its supported limit.')
    clean['steps'] = clean['steps'][-32:]
    if omitted:
        clean['omitted_steps'] = omitted
    return clean


def read_admitted_history(manifest, source_path, field, *, changed_error=ValueError):
    descriptors = [item for item in manifest.get('inputs', [])
                   if item.get('field') == field and item.get('path') == source_path]
    descriptor = descriptors[0] if len(descriptors) == 1 else None
    history = None
    if descriptor is not None and descriptor.get("scope") == "project":
        sidecar_path = os.path.splitext(source_path)[0] + ".meta.json"
        if descriptor.get("sidecar_path") != sidecar_path:
            raise changed_error("The recorded source metadata changed.")
        descriptor_fd = -1
        try:
            descriptor_fd = os.open(sidecar_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                    | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            before = os.fstat(descriptor_fd)
            def identity(value):
                return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                        value.st_ctime_ns, value.st_nlink)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size <= 0
                    or identity(before) != identity(os.lstat(sidecar_path))):
                raise changed_error("The recorded source metadata is unavailable.")
            # History parsing has a budget; larger admitted sidecars still
            # require exact streamed verification and normal publication.
            raw = bytearray() if before.st_size <= 1024 * 1024 else None
            size, digest = 0, hashlib.sha256()
            while size <= before.st_size:
                chunk = os.read(descriptor_fd, min(65536, before.st_size + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                if raw is not None:
                    raw.extend(chunk)
            if (size != descriptor.get("sidecar_size")
                    or digest.hexdigest() != descriptor.get("sidecar_sha256")
                    or identity(os.fstat(descriptor_fd)) != identity(before)
                    or identity(os.lstat(sidecar_path)) != identity(before)):
                raise changed_error("The recorded source metadata changed.")
            if raw is not None:
                metadata = json.loads(raw)
                if isinstance(metadata, dict):
                    history = metadata.get("postprocessing")
        except (OSError, ValueError):
            raise changed_error("The recorded source metadata is unavailable.") from None
        finally:
            if descriptor_fd >= 0:
                os.close(descriptor_fd)
    return sanitize_history(history)
