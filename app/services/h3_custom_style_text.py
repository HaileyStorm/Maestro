"""Scoped H3 style relationships for explicitly admitted references.

The existing role serializer and Picture ordinal remain authoritative. Paths
and names do not create roles; prompt wording is not native delivery proof.
"""
import re

from models.minimax_h3.reference_manifest import (MINIMAX_H3_MAX_REFERENCE_IMAGES,
                                                reference_role_text)


SUPPORTED_IMAGE_SCOPES = ("palette", "materials", "lighting")


def scoped_style_relationship(reference, picture_no):
    """Return definitions/retention/detail for an explicitly admitted style.

    The native adapter must create custom_visual_style from the frozen snapshot
    after exact reference authorization; never copy this field from client input
    or asset metadata. Return None for unchanged legacy references.
    Camera-motion borrowing retains text guidance until an adapter declares
    suitable motion references; a still image does not establish motion support.
    """
    if not isinstance(reference, dict):
        raise ValueError("reference must be an object")
    if "custom_visual_style" not in reference:
        return None
    envelope = reference["custom_visual_style"]
    if (reference.get("type") != "image" or reference.get("image_intent") != "style"
            or type(picture_no) is not int or not 1 <= picture_no <= MINIMAX_H3_MAX_REFERENCE_IMAGES
            or not isinstance(envelope, dict)
            or set(envelope) != {"version", "style_snapshot_digest", "borrow_scope"}
            or type(envelope["version"]) is not int or envelope["version"] != 1
            or not isinstance(envelope["style_snapshot_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", envelope["style_snapshot_digest"]) is None):
        raise ValueError("invalid admitted scoped style reference")
    scopes = envelope["borrow_scope"]
    if (not isinstance(scopes, list) or not scopes
            or any(not isinstance(scope, str) or scope not in SUPPORTED_IMAGE_SCOPES for scope in scopes)
            or len(set(scopes)) != len(scopes)):
        raise ValueError("unsupported or ambiguous image borrow scope")
    role = reference.get("role", "")
    if not isinstance(role, str) or len(role) > 500:
        raise ValueError("invalid style reference label")
    scope_text = ", ".join(scope for scope in SUPPORTED_IMAGE_SCOPES if scope in scopes)
    label = f"<Picture {picture_no}>"
    suffix = f" for {reference_role_text(role)}" if role else ""
    preservation = "Preserve the authored medium, character identity, wardrobe, composition, and explicit shot instructions."
    return (
        f"{label} supplies only {scope_text} reference{suffix}.",
        f"{label}: weak_reference - borrow only {scope_text}. {preservation}",
        f"{label} may guide only {scope_text}; other visual attributes retain their authored values.",
    )
