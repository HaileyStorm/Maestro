"""Prepare custom-style references through H3 binding and materialization.

The caller owns authorization, sealed descriptors, capability declarations,
prompt mapping and delivery receipts. Prepared pixels do not prove application.
"""
from copy import deepcopy

from models.minimax_h3.reference_manifest import (MINIMAX_H3_MAX_REFERENCE_IMAGES,
                                                MINIMAX_H3_MAX_REFERENCES)
from services.h3_reference_binding import build_h3_reference_binding, materialize_h3_reference_image
from services.custom_visual_styles import StyleConflict, validate_snapshot
from services.h3_custom_style_text import scoped_style_relationship

ADAPTER_ID = "maestro_h3_custom_visual_style_v1"


def prepare_style_handoff(snapshot, native_params, descriptors, *, resolve_current,
                          validate_descriptor, has_audio, reserved_image_slots=0, reserved_mixed_slots=0):
    """Append exact style selections after existing images without renumbering.

    resolve_current(ref, field) rechecks current media/terms/access and returns
    {source: the path-free source identity, descriptor: a current sealed input
    descriptor}. No client descriptor or stored prior authorization is authority.
    The returned transient pixels go to the existing native task binder. The
    returned semantic manifest must pass the explicit scoped-text branch before
    the legacy renderer; it is not enabled merely by returning this object.
    """
    body = validate_snapshot(snapshot)
    if body["phase"] != "planned" or not isinstance(native_params, dict) or not isinstance(descriptors, list):
        raise ValueError("planned style and current native input descriptors are required")
    if native_params.get("model_type") != body["model_type"]:
        raise StyleConflict("Selected native style model changed.")
    raw = deepcopy(native_params)
    selected_descriptors = deepcopy(descriptors)
    planned = [ref for ref in body["references"] if ref["delivery"] == "planned"]
    if not planned:
        return {"raw_inputs": raw, "descriptors": selected_descriptors,
                "style_pixels": [], "semantic_manifest": None, "prepared": []}
    if (body["model_type"] != "minimax_h3_ref2va" or body["mode"] != "ref2va"
            or body["adapter_id"] != ADAPTER_ID):
        raise ValueError("scoped style image adapter is not declared for this mode")
    for value, maximum in ((reserved_image_slots, MINIMAX_H3_MAX_REFERENCE_IMAGES),
                           (reserved_mixed_slots, MINIMAX_H3_MAX_REFERENCES)):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("invalid server-owned continuation reservation")
    flags = raw.get("video_prompt_type") or ""
    if not isinstance(flags, str):
        raise ValueError("native reference selection must be text")
    if (("I" not in flags and raw.get("image_refs"))
            or ("K" in flags and not raw.get("image_refs"))):
        raise StyleConflict("Select or clear existing image references before adding this style.")
    images = raw.get("image_refs")
    if images is None:
        images = []
    if type(images) is not list:
        raise ValueError("native image references must be a list")
    images = list(images)
    if len(images) + len(planned) + reserved_image_slots > MINIMAX_H3_MAX_REFERENCE_IMAGES:
        raise StyleConflict("Style reference capacity changed. Review this request.")
    fields = []
    for ref in planned:
        field = f"image_refs:{len(images)}"
        current = resolve_current(deepcopy(ref), field)
        expected_source = {key: deepcopy(ref[key]) for key in ("selection", "sha256", "terms", "access_policy")}
        if (not isinstance(current, dict) or set(current) != {"source", "descriptor"}
                or current["source"] != expected_source):
            raise StyleConflict("Style source identity, terms, or access changed.")
        descriptor = current["descriptor"]
        if (not isinstance(descriptor, dict) or descriptor.get("field") != field
                or descriptor.get("sha256") != ref["sha256"]):
            raise StyleConflict("Style source descriptor changed.")
        # The existing binder validates path/size/current authority and the
        # existing materializer checks no-follow bytes before decoding them.
        images.append(descriptor.get("path")); selected_descriptors.append(deepcopy(descriptor)); fields.append(field)
    raw["image_refs"] = images
    # WanGP ignores image_refs without I. Preserve existing selected inputs and
    # K dimension ownership; never activate an inactive upload or make the
    # first new style image define the output dimensions.
    raw["video_prompt_type"] = flags if "I" in flags else flags + "I"
    manifest = build_h3_reference_binding(raw, descriptors=selected_descriptors,
                                         validate_descriptor=validate_descriptor, has_audio=has_audio)
    # Validate the completed manifest: a new style image can supply the visual
    # reference required by an existing authorized standalone audio reference.
    if len(manifest) + reserved_mixed_slots > MINIMAX_H3_MAX_REFERENCES:
        raise StyleConflict("Style reference capacity changed. Review this request.")
    semantic = deepcopy(manifest)
    bindings = {item["source_key"]: item for item in manifest}
    for item in semantic:
        if item["source_key"] in fields:
            item["image_intent"] = "style"
            item["role"] = body["style"]["name"]
            item["custom_visual_style"] = {"version": 1, "style_snapshot_digest": snapshot["digest"],
                                          "borrow_scope": deepcopy(body["style"]["definition"]["borrow_scope"])}
            scoped_style_relationship(item, int(item["source_key"].split(":")[1]) + 1)
    pixels = [materialize_h3_reference_image(bindings[field]) for field in fields]
    prepared = [{"style_digest": snapshot["digest"], "selection": deepcopy(ref["selection"]),
                 "sha256": ref["sha256"], "model_type": body["model_type"], "mode": body["mode"],
                 "adapter_id": ADAPTER_ID, "status": "prepared"} for ref in planned]
    return {"raw_inputs": raw, "descriptors": selected_descriptors, "style_pixels": pixels,
            "semantic_manifest": semantic, "prepared": prepared}
