"""Selected Ref2VA input slots shared by admission and preprocessing.

Selection is not file admission or content-identity proof. Values remain opaque.
"""
from __future__ import annotations

from collections.abc import Sequence
import os
import logging
from typing import Any

VIDEO_KEYS = ('video_guide', 'video_guide2', 'video_guide3')


class H3ReferenceInputError(ValueError):
    """A selected reference input cannot be represented truthfully."""


def selected_h3_video_slots(
    video_prompt_type: str | None,
    video_slots: Sequence[Any],
) -> tuple[tuple[int, Any], ...]:
    """Return one-based physical slots and values in native presentation order.

    Values can be paths or loaded tensors. Never truth-test a loaded value.
    A supplied third slot is selected by V even without the second-slot flag,
    matching the native model's established behavior.
    """
    if len(video_slots) != 3:
        raise H3ReferenceInputError('H3 reference video selection needs three physical slots.')
    flags = video_prompt_type or ''
    if not isinstance(flags, str):
        raise H3ReferenceInputError('H3 reference video selection must be text.')
    indices = []
    if 'V' in flags:
        indices.append(0)
        if '+' in flags:
            indices.append(1)
        if '++' in flags or video_slots[2] is not None:
            indices.append(2)
    return tuple((index + 1, video_slots[index]) for index in indices
                 if video_slots[index] is not None)


def prepare_h3_reference_video_slots(video_prompt_type, video_slots, *, prepare):
    """Prepare selected videos without moving them to other physical slots."""
    result = [None, None, None]
    for slot, value in selected_h3_video_slots(video_prompt_type, video_slots):
        result[slot - 1] = prepare(value)
    return tuple(result)


def extract_h3_reference_soundtracks(
    video_prompt_type, video_slots, *, has_audio, destination, extract, register, cleanup,
):
    """Extract exactly one durable soundtrack per selected video, in order.

    The caller owns extraction, path confinement and cleanup. Register each
    destination before extraction so partial writes remain cleanup-owned. Failed
    attempts, including cancellation, roll back only their registered outputs.
    """
    selected = selected_h3_video_slots(video_prompt_type, video_slots)
    if not selected:
        raise H3ReferenceInputError('Soundtrack references need a selected reference video.')
    pending = []
    result = []
    try:
        for ordinal, (_slot, path) in enumerate(selected, start=1):
            stage = 'read'
            try:
                if not has_audio(path):
                    raise H3ReferenceInputError(
                        f'Reference video {ordinal} has no soundtrack. Choose a video with audio '
                        'or disable soundtrack references.'
                    )
                stage = 'prepare its soundtrack output'
                output = os.fspath(destination(ordinal, path))
                if not output:
                    raise H3ReferenceInputError('Reference soundtrack output path is missing.')
                register(output)
                pending.append(output)
                stage = 'extract its soundtrack'
                extracted = extract(path, output)
                if extracted is None or os.fspath(extracted) != output:
                    raise H3ReferenceInputError('Reference soundtrack extraction changed its output path.')
                result.append(output)
            except (InterruptedError, H3ReferenceInputError):
                raise
            except Exception as exc:
                raise H3ReferenceInputError(
                    f'Reference video {ordinal} could not {stage}. '
                    'Choose another video or disable soundtrack references.'
                ) from exc
        return tuple(result + [None] * (3 - len(result)))
    except BaseException:
        try:
            cleanup(pending)
        except Exception:
            logging.getLogger(__name__).warning(
                'Some temporary reference soundtracks could not be removed.'
            )
        raise
