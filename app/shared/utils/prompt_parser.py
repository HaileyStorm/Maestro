import math
import re


_TIMELINE_TIME = (
    r"(?:(?:\d{1,2}:){1,2}\d{1,2}(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?:sec(?:ond)?s?|s)?"
)
GLOBAL_TIMELINE_RANGE_RE = re.compile(
    rf"^\s*(?:[-*]\s*)?[\[(]?\s*({_TIMELINE_TIME})\s*"
    rf"(?:-|–|—|\bto\b)\s*({_TIMELINE_TIME})\s*[\])]?\s*:?[ \t]*(.+?)\s*$",
    re.IGNORECASE,
)
GLOBAL_TIMELINE_POINT_RE = re.compile(
    rf"^\s*(?:[-*]\s*)?\(?\s*at\s+({_TIMELINE_TIME})\s*"
    rf"(?:[:,]|[-–—])?[ \t]*(.+?)\)?\s*$",
    re.IGNORECASE,
)
GLOBAL_TIMELINE_BARE_POINT_RE = re.compile(
    rf"^\s*(?:[-*]\s*)?[\[(]?\s*({_TIMELINE_TIME})\s*[\])]?\s*"
    rf"(?:[:,]|[-–—])?[ \t]+(.+?)\s*$",
    re.IGNORECASE,
)
SHOT_PREFIX_RE = re.compile(
    r"^\s*(\[\s*(?:shot|scene)\s+\d+(?:\s*[^\]|]*)?\])\s*(.*)$",
    re.IGNORECASE,
)
SHOT_WITH_TIME_RE = re.compile(
    rf"^\s*\[\s*((?:shot|scene)\s+\d+(?:\s*[^\]|]*)?)\s*"
    rf"(?:\||@|,|[-–—])\s*({_TIMELINE_TIME})\s*\]\s*:?[ \t]*(.+?)\s*$",
    re.IGNORECASE,
)
_CONTINUOUS_TRANSITION_RE = re.compile(
    r"\b(?:continuous(?:ly)?|same\s+shot|single\s+take|one\s+take|"
    r"without\s+(?:a\s+)?cut|no\s+cut|camera\s+continues)\b",
    re.IGNORECASE,
)
_SOFT_TRANSITION_RE = re.compile(
    r"\b(?:cross[ -]?dissolve|dissolve|fade(?:s|d)?(?:\s+(?:in|out|to))?|wipe)\b",
    re.IGNORECASE,
)
_HARD_CUT_RE = re.compile(
    r"\b(?:hard\s+cut|smash\s+cut|match\s+cut|jump\s+cut|cut(?:s)?\s+to|"
    r"scene\s+change|new\s+scene)\b",
    re.IGNORECASE,
)
_H3_INLINE_SHOT_MARKER_RE = re.compile(
    r"\[\s*(?:shot|scene)\s+\d+[^\]]*\]",
    re.IGNORECASE,
)
_H3_DIALOGUE_TOKEN_RE = re.compile(r"<\s*(/?)\s*d\s*>", re.IGNORECASE)
_H3_DIALOGUE_BLOCK_RE = re.compile(
    r"<d>\s*\[[^\]\r\n]+\]\s+.*?</d>", re.IGNORECASE | re.DOTALL,
)


def _protect_h3_dialogue(prompt):
    source = str(prompt or "")
    blocks = []
    salt = 0
    prefix = "__H3_TIMELINE_DIALOGUE_SLOT_0_"
    while prefix in source:
        salt += 1
        prefix = f"__H3_TIMELINE_DIALOGUE_SLOT_{salt}_"

    def replace(match):
        token = f"{prefix}{len(blocks)}__"
        blocks.append((token, match.group(0)))
        return token

    return _H3_DIALOGUE_BLOCK_RE.sub(replace, source), blocks


def _restore_h3_dialogue(value, blocks):
    restored = str(value or "")
    for token, block in blocks:
        restored = restored.replace(token, block)
    return restored


def _h3_timeline_lines(prompt):
    """Expand one-line H3 Context-IR shot timelines for existing parsing.

    Multiline Studio syntax is returned unchanged. Only a line containing two
    or more explicit H3 Shot/Scene markers is split, and any leading Context-IR
    field label remains a separate global line.
    """
    lines = []
    dialogue_depth = 0
    for raw_line in str(prompt or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        markers = []
        events = [
            (match.start(), "dialogue", match)
            for match in _H3_DIALOGUE_TOKEN_RE.finditer(raw_line)
        ] + [
            (match.start(), "marker", match)
            for match in _H3_INLINE_SHOT_MARKER_RE.finditer(raw_line)
        ]
        for _, kind, match in sorted(events, key=lambda item: item[0]):
            if kind == "dialogue":
                dialogue_depth += -1 if match.group(1) else 1
                dialogue_depth = max(0, dialogue_depth)
            elif dialogue_depth == 0:
                markers.append(match)
        if len(markers) < 2:
            lines.append(raw_line)
            continue
        prefix = raw_line[:markers[0].start()].strip()
        if prefix:
            lines.append(prefix)
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(raw_line)
            lines.append(raw_line[marker.start():end].strip())
    return lines


def _timeline_seconds(value):
    """Parse seconds, MM:SS, or HH:MM:SS from one timestamp token."""
    token = re.sub(
        r"\s*(?:sec(?:ond)?s?|s)\s*$", "", str(value or "").strip(),
        flags=re.IGNORECASE,
    )
    try:
        parts = [float(part) for part in token.split(":")]
    except (TypeError, ValueError):
        return None
    if not parts or len(parts) > 3 or any(part < 0 for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60.0 + part
    return seconds


def parse_global_timeline_prompt(prompt):
    """Return ``(global_lines, timed_events)`` from a Studio prompt.

    Timed range syntax accepts forms such as ``[00:10-00:18] action`` and
    ``(10-18s): action``. Point cues accept ``(at 10 seconds: action)``,
    ``At 00:10.000, action``, ``[00:10.000] action``, and a leading H3-style
    shot label such as ``[Shot 2] At 00:15.000, action``. A first untimed
    ``[Shot 1]`` is treated as starting at zero when later shot timestamps
    establish that the prompt is a timeline. Lines without a valid timestamp
    are global direction and are deliberately preserved for every generated
    window.
    """
    protected_prompt, dialogue_blocks = _protect_h3_dialogue(prompt)
    global_lines = []
    timed_events = []
    pending_first_shot = None
    for order, raw_line in enumerate(_h3_timeline_lines(protected_prompt)):
        line = raw_line.strip()
        if not line:
            continue
        bracketed_shot_time = SHOT_WITH_TIME_RE.match(line)
        if bracketed_shot_time:
            at = _timeline_seconds(bracketed_shot_time.group(2))
            if at is not None:
                timed_events.append({
                    "kind": "shot", "start": at, "end": at,
                    "text": (
                        f"[{bracketed_shot_time.group(1).strip()}] "
                        f"{bracketed_shot_time.group(3).strip()}"
                    ),
                    "order": order,
                })
                continue

        shot_match = SHOT_PREFIX_RE.match(line)
        shot_label = shot_match.group(1).strip() if shot_match else ""
        timeline_line = shot_match.group(2).strip() if shot_match else line
        range_match = GLOBAL_TIMELINE_RANGE_RE.match(timeline_line)
        if range_match:
            marker_line = re.sub(r"^\s*[-*]\s*", "", timeline_line)
            marker_tokens = (range_match.group(1), range_match.group(2))
            has_time_marker = (
                marker_line.startswith(("[", "("))
                or any(":" in token for token in marker_tokens)
                or any(
                    re.search(r"(?:sec(?:ond)?s?|s)\s*$", token, re.IGNORECASE)
                    for token in marker_tokens
                )
            )
            start = _timeline_seconds(range_match.group(1))
            end = _timeline_seconds(range_match.group(2))
            if has_time_marker and start is not None and end is not None and end > start:
                timed_events.append({
                    "kind": "range", "start": start, "end": end,
                    "text": " ".join(filter(None, (
                        shot_label, range_match.group(3).strip(),
                    ))),
                    "order": order,
                })
                continue
        point_match = GLOBAL_TIMELINE_POINT_RE.match(timeline_line)
        if point_match:
            at = _timeline_seconds(point_match.group(1))
            if at is not None:
                timed_events.append({
                    "kind": "shot" if shot_label else "point",
                    "start": at, "end": at,
                    "text": " ".join(filter(None, (
                        shot_label,
                        point_match.group(2).strip().rstrip(")").strip(),
                    ))),
                    "order": order,
                })
                continue
        bare_point_match = GLOBAL_TIMELINE_BARE_POINT_RE.match(timeline_line)
        if bare_point_match:
            marker = bare_point_match.group(1)
            # Avoid interpreting ordinary numbered prose as a timeline. A
            # bare marker must look like a clock, include a time unit, be
            # bracketed, or belong to an explicit Shot/Scene label.
            marker_like_time = (
                bool(shot_label)
                or timeline_line.startswith(("[", "("))
                or ":" in marker
                or bool(re.search(
                    r"(?:sec(?:ond)?s?|s)\s*$", marker, re.IGNORECASE,
                ))
            )
            at = _timeline_seconds(marker)
            if marker_like_time and at is not None:
                timed_events.append({
                    "kind": "shot" if shot_label else "point",
                    "start": at, "end": at,
                    "text": " ".join(filter(None, (
                        shot_label, bare_point_match.group(2).strip(),
                    ))),
                    "order": order,
                })
                continue
        if shot_label and re.match(r"^\[\s*(?:shot|scene)\s+1\b", shot_label, re.IGNORECASE):
            pending_first_shot = {
                "kind": "shot", "start": 0.0, "end": 0.0,
                "text": line, "order": order,
                "global_index": len(global_lines),
            }
            continue
        global_lines.append(line)
    if pending_first_shot is not None and any(
        event["kind"] == "shot" for event in timed_events
    ):
        timed_events.append(pending_first_shot)
    elif pending_first_shot is not None:
        global_lines.insert(
            pending_first_shot["global_index"], pending_first_shot["text"],
        )
    global_lines = [
        _restore_h3_dialogue(line, dialogue_blocks)
        for line in global_lines
    ]
    for event in timed_events:
        event["text"] = _restore_h3_dialogue(event.get("text"), dialogue_blocks)
    return global_lines, timed_events


def has_global_timeline(prompt):
    """Whether a prompt contains at least one valid global timestamp cue."""
    return bool(parse_global_timeline_prompt(prompt)[1])


def classify_timeline_clip_boundaries(prompt, *, clip_frame_counts, fps):
    """Classify native-clip joins as continuous, cut, or transition.

    Only authored events that land on the actual model-grid join affect the
    join. A cut occurring later inside the next native clip remains that
    clip's responsibility and must not disable continuity at its earlier
    boundary. Shot/Scene markers imply a cut unless their text explicitly
    says the camera remains in one continuous take.
    """
    try:
        fps_value = float(fps)
        counts = [int(value) for value in clip_frame_counts]
    except (TypeError, ValueError):
        return []
    if fps_value <= 0 or len(counts) <= 1 or any(value <= 0 for value in counts):
        return []
    _, events = parse_global_timeline_prompt(prompt)
    tolerance = max(0.05, 1.0 / fps_value)
    boundaries = []
    frame_cursor = 0
    for count in counts[:-1]:
        frame_cursor += count
        at_seconds = frame_cursor / fps_value
        aligned = [
            event for event in events
            if abs(float(event.get("start", -1)) - at_seconds) <= tolerance
        ]
        aligned.sort(key=lambda event: int(event.get("order", 0)))
        boundary_type = "continuous"
        source = "model_grid"
        event_text = ""
        for event in aligned:
            text = str(event.get("text") or "")
            if _CONTINUOUS_TRANSITION_RE.search(text):
                boundary_type, source, event_text = "continuous", "explicit_continuity", text
                continue
            if _SOFT_TRANSITION_RE.search(text):
                boundary_type, source, event_text = "transition", "explicit_transition", text
                break
            if _HARD_CUT_RE.search(text) or event.get("kind") == "shot":
                boundary_type, source, event_text = "cut", "explicit_cut", text
                break
        if boundary_type == "continuous":
            upcoming = sorted(
                (
                    event for event in events
                    if tolerance < float(event.get("start", -1)) - at_seconds <= 1.5
                    and not _CONTINUOUS_TRANSITION_RE.search(
                        str(event.get("text") or "")
                    )
                    and (
                        event.get("kind") == "shot"
                        or _HARD_CUT_RE.search(str(event.get("text") or ""))
                        or _SOFT_TRANSITION_RE.search(str(event.get("text") or ""))
                    )
                ),
                key=lambda event: float(event.get("start", 0)),
            )
            if upcoming:
                event = upcoming[0]
                boundary_type = "precut"
                source = "precut_lead_in"
                event_text = str(event.get("text") or "")
        boundaries.append({
            "type": boundary_type,
            "at_seconds": at_seconds,
            "source": source,
            "event": event_text,
        })
    return boundaries


def plan_transition_aware_clip_frames(
    total_frames,
    *,
    prompt,
    fps,
    minimum_frames,
    maximum_frames,
    align_frame_count,
    lead_seconds=0.5,
):
    """Plan legal clips with joins shortly before nearby authored cuts.

    Dynamic programming keeps every clip on the model's exact frame grid and
    first minimizes total overrun, then boundary distance.  The minimal clip
    count is retained whenever it can represent the nearby authored joins. If
    an alignment tail (for example FL2VA's reserved final-frame tail) makes a
    pre-cut join impossible at that count, one additional legal clip per
    relevant transition may be used. Only a cut/transition reasonably near a
    balanced join influences the plan, avoiding pathological tiny scenes.
    """
    base = plan_consecutive_clip_frames(
        total_frames,
        minimum_frames=minimum_frames,
        maximum_frames=maximum_frames,
        align_frame_count=align_frame_count,
    )
    if len(base) <= 1:
        return base
    try:
        fps_value = float(fps)
    except (TypeError, ValueError):
        return base
    if fps_value <= 0:
        return base
    _, events = parse_global_timeline_prompt(prompt)
    transitions = []
    for event in events:
        text = str(event.get("text") or "")
        if _CONTINUOUS_TRANSITION_RE.search(text):
            continue
        if (
            event.get("kind") == "shot"
            or _HARD_CUT_RE.search(text)
            or _SOFT_TRANSITION_RE.search(text)
        ):
            point = float(event.get("start", 0)) * fps_value
            if 0 < point < int(total_frames):
                transitions.append(point)
    if not transitions:
        return base
    transitions = sorted(set(transitions))

    legal = sorted({
        int(align_frame_count(value))
        for value in range(int(minimum_frames), int(maximum_frames) + 1)
        if int(minimum_frames) <= int(align_frame_count(value)) <= int(maximum_frames)
    })
    if not legal:
        return base
    base_boundaries = []
    cursor = 0
    for count in base[:-1]:
        cursor += count
        base_boundaries.append(cursor)
    search_radius = 6.0 * fps_value
    lead_frames = max(1, int(round(float(lead_seconds) * fps_value)))

    # Preserve the old relevance rule: an authored transition only changes
    # geometry when it is near a boundary in the minimal balanced plan.
    relevant_transitions = []
    used = set()
    for base_boundary in base_boundaries:
        candidates = [
            (abs((point - lead_frames) - base_boundary), point_index, point)
            for point_index, point in enumerate(transitions)
            if point_index not in used
            and abs((point - lead_frames) - base_boundary) <= search_radius
        ]
        if candidates:
            _, point_index, point = min(candidates)
            used.add(point_index)
            relevant_transitions.append(point)
    if not relevant_transitions:
        return base

    def solve(segment_count):
        # Evenly spaced boundaries are only matching anchors; the DP below is
        # still free to choose any legal grid value for every segment.
        balanced = [
            int(round(int(total_frames) * index / segment_count))
            for index in range(1, segment_count)
        ]
        desired = list(balanced)
        weights = [1.0] * len(desired)
        matched = set()
        for index, boundary in enumerate(balanced):
            candidates = [
                (abs((point - lead_frames) - boundary), point_index, point)
                for point_index, point in enumerate(relevant_transitions)
                if point_index not in matched
                and abs((point - lead_frames) - boundary) <= search_radius
            ]
            if candidates:
                _, point_index, point = min(candidates)
                matched.add(point_index)
                desired[index] = max(1, int(round(point - lead_frames)))
                weights[index] = 6.0

        # sum -> (boundary cost, segment plan)
        states = {0: (0.0, [])}
        max_sum = int(total_frames) + int(maximum_frames)
        for segment_index in range(segment_count):
            next_states = {}
            for current_sum, (cost, plan) in states.items():
                for length in legal:
                    new_sum = current_sum + length
                    if new_sum > max_sum:
                        continue
                    remaining_slots = segment_count - segment_index - 1
                    if new_sum + remaining_slots * legal[0] > max_sum:
                        continue
                    if new_sum + remaining_slots * legal[-1] < int(total_frames):
                        continue
                    boundary_cost = cost
                    if segment_index < segment_count - 1:
                        boundary_cost += weights[segment_index] * (
                            new_sum - desired[segment_index]
                        ) ** 2
                    previous = next_states.get(new_sum)
                    if previous is None or boundary_cost < previous[0]:
                        next_states[new_sum] = (
                            boundary_cost, [*plan, length],
                        )
            states = next_states
            if not states:
                return None
        feasible = [
            (total - int(total_frames), cost, plan)
            for total, (cost, plan) in states.items()
            if total >= int(total_frames)
        ]
        if not feasible:
            return None
        return min(feasible, key=lambda item: (item[0], item[1]))[2]

    def represents_relevant_transitions(plan):
        boundaries = []
        cursor = 0
        for length in plan[:-1]:
            cursor += length
            boundaries.append(cursor)
        precut_limit = max(lead_frames, int(round(1.5 * fps_value)))
        return all(
            any(0 <= point - boundary <= precut_limit for boundary in boundaries)
            for point in relevant_transitions
        )

    # Keep the minimal count if it can express every relevant cut. A reserved
    # alignment tail can make that impossible even though the unreserved
    # duration fit; add no more than one segment per affected transition.
    fallback = base
    for segment_count in range(
        len(base), len(base) + len(relevant_transitions) + 1,
    ):
        plan = solve(segment_count)
        if plan is None:
            continue
        if segment_count == len(base):
            fallback = plan
        if represents_relevant_transitions(plan):
            return plan
    return fallback


def _format_timeline_seconds(value):
    value = max(0.0, float(value))
    if abs(value - round(value)) < 0.0005:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def plan_consecutive_clip_frames(
    total_frames,
    *,
    minimum_frames,
    maximum_frames,
    align_frame_count,
):
    """Split a long request into legal, consecutive native-model clips.

    The smallest possible clip count is used.  Each provisional clip is
    passed through the model's own frame aligner, then the plan is advanced on
    that same grid if rounding would otherwise shorten the requested movie.
    A small alignment surplus is preferable to silently dropping the tail.
    """
    total = int(total_frames)
    minimum = int(minimum_frames)
    maximum = int(maximum_frames)
    if total <= 0 or minimum <= 0 or maximum < minimum:
        raise ValueError("Invalid total/minimum/maximum frame count")

    def align(value):
        aligned = int(align_frame_count(int(value)))
        if not minimum <= aligned <= maximum:
            raise ValueError(
                f"Frame aligner returned {aligned}; expected {minimum}..{maximum}"
            )
        return aligned

    clip_count = max(1, math.ceil(total / maximum))
    if clip_count * minimum > total and clip_count > 1:
        # This can occur only for unusual models with a very narrow legal
        # duration band.  It remains the only feasible plan that respects the
        # native maximum, so keep the minimum-sized clips.
        return [align(minimum) for _ in range(clip_count)]

    remaining = total
    plan = []
    for index in range(clip_count):
        slots = clip_count - index
        target = math.ceil(remaining / slots)
        target = max(minimum, min(maximum, target))
        aligned = align(target)
        plan.append(aligned)
        remaining -= aligned

    # A floor-aligning model can leave the aggregate plan short. Advance one
    # clip at a time using the very same aligner until the requested duration
    # is covered or the model maximum makes that impossible.
    while sum(plan) < total:
        advanced = False
        for index in range(len(plan) - 1, -1, -1):
            current = plan[index]
            for candidate in range(current + 1, maximum + 1):
                aligned = align(candidate)
                if aligned > current:
                    plan[index] = aligned
                    advanced = True
                    break
            if advanced:
                break
        if not advanced:
            raise ValueError("Unable to cover requested duration on model frame grid")
    return plan


def _build_timeline_prompts_for_ranges(prompt, *, fps, frame_ranges):
    """Clip and rebase a global timeline over explicit inclusive ranges."""
    try:
        fps_value = float(fps)
    except (TypeError, ValueError):
        return None
    if fps_value <= 0 or not frame_ranges:
        return None
    global_lines, events = parse_global_timeline_prompt(prompt)
    if not events:
        return None

    total_frames = max(int(end) for _, end in frame_ranges) + 1
    total_seconds = max(0.0, total_frames / fps_value)
    prompts = []
    for frame_start, frame_end in frame_ranges:
        start_seconds = int(frame_start) / fps_value
        end_seconds = min(total_seconds, (int(frame_end) + 1) / fps_value)
        local_lines = list(global_lines)
        timed_count = 0
        ordered_events = sorted(events, key=lambda item: item["order"])
        shot_starts = sorted(
            event["start"] for event in ordered_events
            if event["kind"] == "shot"
        )
        for event in ordered_events:
            event_start = max(0.0, min(total_seconds, event["start"]))
            if event["kind"] == "point":
                if start_seconds <= event_start < end_seconds:
                    local_at = event_start - start_seconds
                    local_lines.append(
                        f"(at {_format_timeline_seconds(local_at)} seconds: "
                        f"{event['text']})"
                    )
                    timed_count += 1
                continue
            if event["kind"] == "shot":
                event_end = next(
                    (start for start in shot_starts if start > event_start),
                    total_seconds,
                )
            else:
                event_end = event["end"]
            event_end = max(0.0, min(total_seconds, event_end))
            intersection_start = max(start_seconds, event_start)
            intersection_end = min(end_seconds, event_end)
            if intersection_end <= intersection_start:
                continue
            local_start = intersection_start - start_seconds
            local_end = intersection_end - start_seconds
            local_lines.append(
                f"[{_format_timeline_seconds(local_start)}-"
                f"{_format_timeline_seconds(local_end)}s] {event['text']}"
            )
            timed_count += 1
        if timed_count == 0:
            local_lines.append("Continue the established action and visual continuity.")
        prompts.append("\n".join(local_lines).strip())
    return prompts


def build_global_timeline_clip_prompts(prompt, *, clip_frame_counts, fps):
    """Map one global Studio timeline onto consecutive native-size clips.

    Untimed prompts are repeated intact so long-form conversion also remains
    useful for direct API callers that provide prose without timestamps.
    """
    counts = [int(value) for value in clip_frame_counts]
    if not counts or any(value <= 0 for value in counts):
        return None
    ranges = []
    cursor = 0
    for count in counts:
        ranges.append((cursor, cursor + count - 1))
        cursor += count
    mapped = _build_timeline_prompts_for_ranges(
        prompt, fps=fps, frame_ranges=ranges,
    )
    if mapped is not None:
        return mapped
    return [str(prompt or "").strip() for _ in counts]


def sliding_window_prompt_ranges(
    total_frames, window_size, discard_last_frames=0, reuse_frames=0,
):
    """Return exact model-visible frame ranges for a sliding generation.

    Ranges include each later window's reused prefix, exclude discarded tails,
    and clamp the final partial window. Values are inclusive frame indices.
    """
    total = max(1, int(total_frames))
    window = max(1, int(window_size))
    discard = max(0, int(discard_last_frames))
    reuse = max(0, min(int(reuse_frames), window - 1))
    stride = window - discard - reuse
    if window >= total or stride <= 0:
        return [(0, total - 1)]

    ranges = []
    model_start = 0
    raw_end = window - 1
    window_count = 1 + math.ceil((total - window + discard) / stride)
    for window_index in range(window_count):
        final = window_index == window_count - 1
        visible_end = min(raw_end, total - 1) if final else raw_end - discard
        visible_end = max(model_start, visible_end)
        ranges.append((model_start, visible_end))
        if final:
            break
        fresh_start = visible_end + 1
        model_start = max(0, fresh_start - reuse)
        raw_end = raw_end - discard - reuse + window
    return ranges


def build_global_timeline_window_prompts(
    prompt,
    *,
    total_frames,
    fps,
    window_size,
    discard_last_frames=0,
    reuse_frames=0,
):
    """Clip and rebase one global Studio timeline into exact local windows.

    Returns ``None`` when there is no valid timestamp cue or only one window,
    preserving all legacy/non-sliding behavior. A timed range crossing a
    window boundary is included in every window whose model-visible range it
    intersects, with timestamps relative to that window's first input frame.
    """
    ranges = sliding_window_prompt_ranges(
        total_frames, window_size, discard_last_frames, reuse_frames,
    )
    if len(ranges) <= 1:
        return None
    return _build_timeline_prompts_for_ranges(
        prompt, fps=fps, frame_ranges=ranges,
    )

# Scenema's per-speaker option lines look like:
#   Speaker 1{voice="...", gender="male", scene="..."}: text
# Maestro's prompt_parser would otherwise interpret `{voice="..."}` as a
# variable reference and fail with "Unknown variable". Skip variable-check
# for these lines (upstream Wan2GP does the same). Match Scenema only:
# starts with "Speaker N{" through to the closing "}:" on the same line.
SPEAKER_OPTIONS_LINE_RE = re.compile(r"^\s*Speaker\s*\d+\s*\{[^{}\n]*\}\s*:", re.IGNORECASE)


def is_speaker_options_line(line):
    return SPEAKER_OPTIONS_LINE_RE.search(line or "") is not None


def process_template(input_text, keep_comments=False, keep_empty_lines=False):
    """
    Process a text template with macro instructions and variable substitution.
    Supports multiple values for variables to generate multiple output versions.
    Each section between macro lines is treated as a separate template.
    
    Args:
        input_text (str): The input template text
        
    Returns:
        tuple: (output_text, error_message)
            - output_text: Processed output with variables substituted, or empty string if error
            - error_message: Error description and problematic line, or empty string if no error
    """
    normalized_input = str(input_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_input.split("\n") if keep_empty_lines else normalized_input.strip().split("\n")
    current_variables = {}
    current_template_lines = []
    all_output_lines = []
    error_message = ""
    
    # Process the input line by line
    line_number = 0
    while line_number < len(lines):
        orig_line = lines[line_number]
        line = orig_line.strip()
        line_number += 1
        
        # Skip empty lines or comments
        if not line:
            if keep_empty_lines:
                current_template_lines.append("")
            continue

        if line.startswith('#') and not keep_comments:
            continue

        # Handle macro instructions
        if line.startswith('!'):
            # Process any accumulated template lines before starting a new macro
            if current_template_lines:
                # Process the current template with current variables
                template_output, err = process_current_template(current_template_lines, current_variables)
                if err:
                    return "", err
                all_output_lines.extend(template_output)
                current_template_lines = []  # Reset template lines
            
            # Reset variables for the new macro
            current_variables = {}
            
            # Parse the macro line
            macro_line = line[1:].strip()
            
            # Check for unmatched braces in the whole line
            open_braces = macro_line.count('{')
            close_braces = macro_line.count('}')
            if open_braces != close_braces:
                error_message = f"Unmatched braces: {open_braces} opening '{{' and {close_braces} closing '}}' braces\nLine: '{orig_line}'"
                return "", error_message
            
            # Check for unclosed quotes
            if macro_line.count('"') % 2 != 0:
                error_message = f"Unclosed double quotes\nLine: '{orig_line}'"
                return "", error_message
            
            # Split by optional colon separator
            var_sections = re.split(r'\s*:\s*', macro_line)
            
            for section in var_sections:
                section = section.strip()
                if not section:
                    continue
                    
                # Extract variable name
                var_match = re.search(r'\{([^}]+)\}', section)
                if not var_match:
                    if '{' in section or '}' in section:
                        error_message = f"Malformed variable declaration\nLine: '{orig_line}'"
                        return "", error_message
                    continue
                    
                var_name = var_match.group(1).strip()
                if not var_name:
                    error_message = f"Empty variable name\nLine: '{orig_line}'"
                    return "", error_message
                
                # Check variable value format
                value_part = section[section.find('}')+1:].strip()
                if not value_part.startswith('='):
                    error_message = f"Missing '=' after variable '{{{var_name}}}'\nLine: '{orig_line}'"
                    return "", error_message
                
                # Extract all quoted values
                var_values = re.findall(r'"([^"]*)"', value_part)
                
                # Check if there are values specified
                if not var_values:
                    error_message = f"No quoted values found for variable '{{{var_name}}}'\nLine: '{orig_line}'"
                    return "", error_message
                
                # Check for missing commas between values
                # Look for patterns like "value""value" (missing comma)
                if re.search(r'"[^,]*"[^,]*"', value_part):
                    error_message = f"Missing comma between values for variable '{{{var_name}}}'\nLine: '{orig_line}'"
                    return "", error_message
                
                # Store the variable values
                current_variables[var_name] = var_values
        
        # Handle template lines
        else:
            if not line.startswith('#') and not is_speaker_options_line(line):
                # Check for unknown variables in template line
                var_references = re.findall(r'\{([^}]+)\}', line)
                for var_ref in var_references:
                    if var_ref not in current_variables:
                        error_message = f"Unknown variable '{{{var_ref}}}' in template\nLine: '{orig_line}'"
                        return "", error_message
                
            # Add to current template lines
            current_template_lines.append(line)
    
    # Process any remaining template lines
    if current_template_lines:
        template_output, err = process_current_template(current_template_lines, current_variables)
        if err:
            return "", err
        all_output_lines.extend(template_output)
    
    return '\n'.join(all_output_lines), ""

def process_current_template(template_lines, variables):
    """
    Process a set of template lines with the current variables.
    
    Args:
        template_lines (list): List of template lines to process
        variables (dict): Dictionary of variable names to lists of values
        
    Returns:
        tuple: (output_lines, error_message)
    """
    if not variables or not template_lines:
        return template_lines, ""
    
    output_lines = []
    
    # Find the maximum number of values for any variable
    max_values = max(len(values) for values in variables.values())
    
    # Generate each combination
    for i in range(max_values):
        for template in template_lines:
            output_line = template
            for var_name, var_values in variables.items():
                # Use modulo to cycle through values if needed
                value_index = i % len(var_values)
                var_value = var_values[value_index]
                output_line = output_line.replace(f"{{{var_name}}}", var_value)
            output_lines.append(output_line)
    
    return output_lines, ""


def extract_variable_names(macro_line):
    """
    Extract all variable names from a macro line.
    
    Args:
        macro_line (str): A macro line (with or without the leading '!')
        
    Returns:
        tuple: (variable_names, error_message)
            - variable_names: List of variable names found in the macro
            - error_message: Error description if any, empty string if no error
    """
    # Remove leading '!' if present
    if macro_line.startswith('!'):
        macro_line = macro_line[1:].strip()
    
    variable_names = []
    
    # Check for unmatched braces
    open_braces = macro_line.count('{')
    close_braces = macro_line.count('}')
    if open_braces != close_braces:
        return [], f"Unmatched braces: {open_braces} opening '{{' and {close_braces} closing '}}' braces"
    
    # Split by optional colon separator
    var_sections = re.split(r'\s*:\s*', macro_line)
    
    for section in var_sections:
        section = section.strip()
        if not section:
            continue
            
        # Extract variable name
        var_matches = re.findall(r'\{([^}]+)\}', section)
        for var_name in var_matches:
            new_var = var_name.strip()
            if not new_var in variable_names: 
                variable_names.append(new_var)

    return variable_names, ""

def extract_variable_values(macro_line):
    """
    Extract all variable names and their values from a macro line.
    
    Args:
        macro_line (str): A macro line (with or without the leading '!')
        
    Returns:
        tuple: (variables_dict, error_message)
            - variables_dict: Dictionary mapping variable names to their values
            - error_message: Error description if any, empty string if no error
    """
    # Remove leading '!' if present
    if macro_line.startswith('!'):
        macro_line = macro_line[1:].strip()
    
    variables = {}
    
    # Check for unmatched braces
    open_braces = macro_line.count('{')
    close_braces = macro_line.count('}')
    if open_braces != close_braces:
        return {}, f"Unmatched braces: {open_braces} opening '{{' and {close_braces} closing '}}' braces"
    
    # Check for unclosed quotes
    if macro_line.count('"') % 2 != 0:
        return {}, "Unclosed double quotes"
    
    # Split by optional colon separator
    var_sections = re.split(r'\s*:\s*', macro_line)
    
    for section in var_sections:
        section = section.strip()
        if not section:
            continue
            
        # Extract variable name
        var_match = re.search(r'\{([^}]+)\}', section)
        if not var_match:
            if '{' in section or '}' in section:
                return {}, "Malformed variable declaration"
            continue
            
        var_name = var_match.group(1).strip()
        if not var_name:
            return {}, "Empty variable name"
        
        # Check variable value format
        value_part = section[section.find('}')+1:].strip()
        if not value_part.startswith('='):
            return {}, f"Missing '=' after variable '{{{var_name}}}'"
        
        # Extract all quoted values
        var_values = re.findall(r'"([^"]*)"', value_part)
        
        # Check if there are values specified
        if not var_values:
            return {}, f"No quoted values found for variable '{{{var_name}}}'"
        
        # Check for missing commas between values
        if re.search(r'"[^,]*"[^,]*"', value_part):
            return {}, f"Missing comma between values for variable '{{{var_name}}}'"
        
        variables[var_name] = var_values
    
    return variables, ""

def generate_macro_line(variables_dict):
    """
    Generate a macro line from a dictionary of variable names and their values.
    
    Args:
        variables_dict (dict): Dictionary mapping variable names to lists of values
        
    Returns:
        str: A formatted macro line (including the leading '!')
    """
    sections = []
    
    for var_name, values in variables_dict.items():
        # Format each value with quotes
        quoted_values = [f'"{value}"' for value in values]
        # Join values with commas
        values_str = ','.join(quoted_values)
        # Create the variable assignment
        section = f"{{{var_name}}}={values_str}"
        sections.append(section)
    
    # Join sections with a colon and space for readability
    return "! " + " : ".join(sections)
