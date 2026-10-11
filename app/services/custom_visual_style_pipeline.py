"""Bind custom visual styles to Director admission and durable recovery.

The existing queue journal owns authenticity and project authorization. SHA
digests detect drift; they are not authentication or native-delivery evidence.
"""
from copy import deepcopy

from services.custom_visual_styles import StyleConflict, digest, style_guidance, validate_snapshot


SELECTOR = "custom_visual_style_selection"
SNAPSHOT = "_director_custom_style_snapshot"
STAGES = {"video", "image_creator", "image_editor"}


def _scope(value):
    fields = {"project_id", "workspace_id", "project_instance", "owner_digest"}
    if (not isinstance(value, dict) or set(value) != fields
            or any(not isinstance(item, str) or not item or len(item) > 256 for item in value.values())):
        raise ValueError("current server-owned Director scope is required")
    return deepcopy(value)


def _stages(value):
    if not isinstance(value, dict) or not value or not set(value).issubset(STAGES):
        raise ValueError("selected Director stage bindings are required")
    for binding in value.values():
        if (not isinstance(binding, dict) or set(binding) != {"model_type", "mode", "capability"}
                or any(not isinstance(binding[key], str) or not binding[key] for key in ("model_type", "mode"))):
            raise ValueError("invalid Director stage binding")
    return deepcopy(value)


def admit_selection(library, raw_params, *, current_scope, stage_bindings, resolve_reference):
    """Called before planning, inside existing authorized admission.

    resolve_reference(stage, project, workspace, selection) must use the current
    admission's request/session and stage-specific terms/access checks. Never
    accept scope/capabilities/private snapshots from the request body.
    """
    if not isinstance(raw_params, dict):
        raise ValueError("Director parameters must be an object")
    if SNAPSHOT in raw_params:
        raise ValueError("client supplied a server-owned visual style snapshot")
    params = deepcopy(raw_params)
    selector = params.get(SELECTOR)
    if selector is None:
        return params
    if (not isinstance(selector, dict) or set(selector) != {"asset_id", "revision"}
            or not isinstance(selector["asset_id"], str) or not isinstance(selector["revision"], str)):
        raise ValueError("invalid custom visual style selection")
    scope = _scope(current_scope)
    stages = _stages(stage_bindings)
    snapshots = {}
    with library.store.publication_guard():
        for stage, binding in stages.items():
            snapshots[stage] = library.freeze(
                scope["project_id"], scope["workspace_id"], selector["asset_id"],
                expected_revision=selector["revision"], model_type=binding["model_type"], mode=binding["mode"],
                capability=binding["capability"],
                resolve_reference=lambda project, workspace, selection, stage=stage:
                    resolve_reference(stage, project, workspace, selection),
            )
    snapshot = {"version": 1, "scope_digest": digest(scope), "stage_bindings": stages,
                "selector": deepcopy(selector), "stages": snapshots}
    snapshot["digest"] = digest(snapshot)
    params[SNAPSHOT] = snapshot
    return params


def _validate_unsupported_frozen_stage(frozen):
    if (set(frozen) != {"version", "phase", "style", "model_type", "mode",
                        "adapter_id", "references", "application"}
            or type(frozen["version"]) is not int or frozen["version"] != 1
            or frozen["application"] != "text_only" or frozen["adapter_id"] != ""):
        raise StyleConflict("Saved unsupported visual style delivery changed.")
    selections = frozen["style"]["definition"]["references"]
    references = frozen["references"]
    if len(references) != len(selections):
        raise StyleConflict("Saved unsupported visual style references changed.")
    for reference, selection in zip(references, selections):
        if (set(reference) != {"selection", "sha256", "terms", "access_policy", "delivery", "reason"}
                or reference["delivery"] != "skipped" or reference["reason"] != "mode_unsupported"
                or reference["selection"] != selection or reference["sha256"] != selection["expected_sha256"]
                or not isinstance(reference["access_policy"], dict)
                or reference["access_policy"].get("authorized") is not True
                or reference["access_policy"].get("project_id") != frozen["style"]["project_id"]
                or reference["access_policy"].get("workspace_id") != frozen["style"]["workspace_id"]):
            raise StyleConflict("Saved unsupported visual style references changed.")


def recover_selection(params, *, current_scope, stage_bindings):
    """Validate durable server-owned state without rereading the style library.

    Deleted/edited cards cannot rewrite saved creative intent. Actual reference
    bytes and current terms/access are independently revalidated by the native
    adapter before GPU admission/delivery.
    """
    if not isinstance(params, dict):
        raise ValueError("Director parameters must be an object")
    result = deepcopy(params)
    selector = result.get(SELECTOR)
    snapshot = result.get(SNAPSHOT)
    if selector is None and snapshot is None:
        return result
    if not isinstance(snapshot, dict):
        raise StyleConflict("Saved visual style contents are unavailable. Review this request.")
    body = deepcopy(snapshot)
    expected = body.pop("digest", None)
    if expected != digest(body):
        raise StyleConflict("Saved visual style contents changed.")
    current_stages = _stages(stage_bindings)
    if (set(body) != {"version", "scope_digest", "stage_bindings", "selector", "stages"}
            or type(body["version"]) is not int or body["version"] != 1 or body["selector"] != selector
            or body["scope_digest"] != digest(_scope(current_scope))
            or not isinstance(body["stages"], dict) or set(body["stages"]) != set(current_stages)):
        raise StyleConflict("Saved visual style scope or model selection changed.")
    try:
        saved_stages = _stages(body["stage_bindings"])
    except ValueError:
        raise StyleConflict("Saved visual style stage bindings changed.") from None
    if set(saved_stages) != set(current_stages):
        raise StyleConflict("Saved visual style stage bindings changed.")
    for stage, stage_snapshot in body["stages"].items():
        frozen = validate_snapshot(stage_snapshot)
        binding = saved_stages[stage]
        current_binding = current_stages[stage]
        if (frozen["phase"] != "planned" or frozen["model_type"] != binding["model_type"]
                or current_binding["model_type"] != binding["model_type"]
                or current_binding["mode"] != binding["mode"]
                or frozen["mode"] != binding["mode"]
                or frozen["style"]["project_id"] != current_scope["project_id"]
                or frozen["style"]["workspace_id"] != current_scope["workspace_id"]
                or frozen["style"]["asset_id"] != selector["asset_id"]
                or frozen["style"]["revision"] != selector["revision"]):
            raise StyleConflict("Saved visual style stage binding changed.")
        if binding["capability"] is None:
            _validate_unsupported_frozen_stage(frozen)
        elif binding != current_binding:
            raise StyleConflict("Saved visual style capability changed.")
    return result


def planner_guidance(params, stage, *, current_scope, stage_bindings):
    """Supply separate borrow guidance; preserve existing visual_style authority."""
    recovered = recover_selection(params, current_scope=current_scope, stage_bindings=stage_bindings)
    snapshot = recovered.get(SNAPSHOT)
    if snapshot is None:
        return ""
    if stage not in snapshot["stages"]:
        raise ValueError("visual style stage was not admitted")
    return style_guidance(snapshot["stages"][stage])
