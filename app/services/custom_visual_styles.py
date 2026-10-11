"""Project-scoped custom visual styles and frozen delivery bindings.

Authentication, model capability discovery, and native delivery are caller
boundaries. The library uses the existing ProjectAssetStore publication guard.
"""
from copy import deepcopy
import hashlib
import json
import re
import uuid


SCOPES = ("palette", "materials", "lighting", "camera_motion")
KEY = "custom_visual_style"


class StyleConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} requires non-empty text of at most {limit} characters")
    return value


def _id(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError(f"invalid {field}")
    return value


def validate_source_terms(value):
    """Validate a server-resolved record; the resolver verifies source signatures.

    Imported media can have no recorded model provenance. Preserve that unknown
    state rather than claiming acceptance. Known sources carry current terms.
    """
    if not isinstance(value, dict):
        raise ValueError("invalid visual reference terms")
    if value.get("accepted") is False or value.get("status") == "revoked":
        raise PermissionError("visual reference terms are not accepted")
    if value.get("status") == "not_recorded":
        if (set(value) != {"status", "source_model", "accepted"}
                or value["source_model"] is not None or value["accepted"] is not None):
            raise ValueError("invalid unrecorded visual reference terms")
        return deepcopy(value)
    if (set(value) != {"status", "source_model", "accepted", "requirements"}
            or value["status"] != "verified" or value["accepted"] is not True
            or not isinstance(value["source_model"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}", value["source_model"])
            or not isinstance(value["requirements"], list)):
        raise ValueError("invalid verified visual reference terms")
    fields = {"term", "version", "accepted", "title", "license_url", "review_mode", "notice"}
    seen = set()
    for record in value["requirements"]:
        if isinstance(record, dict) and record.get("accepted") is False:
            raise PermissionError("visual reference terms are not accepted")
        if (not isinstance(record, dict) or not {"term", "version", "accepted"}.issubset(record)
                or not set(record).issubset(fields) or record["accepted"] is not True
                or any(not isinstance(item, str) for key, item in record.items() if key not in {"accepted", "version"})
                or not record["term"].strip() or type(record["version"]) is not int or record["version"] < 1
                or record["term"] in seen):
            raise ValueError("invalid visual reference term requirement")
        seen.add(record["term"])
    return deepcopy(value)


def normalize_definition(value):
    if not isinstance(value, dict) or set(value) != {"version", "direction", "borrow_scope", "references"}:
        raise ValueError("invalid custom visual style definition")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported custom visual style version")
    direction = _text(value["direction"], "direction", 10000)
    scope = value["borrow_scope"]
    if (not isinstance(scope, list) or not 1 <= len(scope) <= len(SCOPES)
            or any(not isinstance(item, str) or item not in SCOPES for item in scope)
            or len(set(scope)) != len(scope)):
        raise ValueError("choose one or more distinct borrow scopes")
    refs = value["references"]
    if not isinstance(refs, list) or len(refs) > 8:
        raise ValueError("references must be a list of at most eight selections")
    normalized = []
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"asset_id", "variant_id", "output_index", "expected_sha256"}:
            raise ValueError("invalid visual reference selection")
        asset = _id(ref["asset_id"], "asset_id")
        variant = _id(ref["variant_id"], "variant_id")
        index = ref["output_index"]
        if type(index) is not int or not 0 <= index < 32:
            raise ValueError("invalid output_index")
        if not isinstance(ref["expected_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", ref["expected_sha256"]):
            raise ValueError("visual reference requires its selected content identity")
        identity = (asset, variant, index)
        if identity in seen:
            raise ValueError("duplicate visual reference")
        seen.add(identity)
        normalized.append(dict(ref))
    return {"version": 1, "direction": direction,
            "borrow_scope": [item for item in SCOPES if item in scope], "references": normalized}


class CustomVisualStyleLibrary:
    def __init__(self, store):
        self.store = store

    def _view(self, project, workspace, asset):
        if asset.get("asset_type") != "visual_style" or asset.get("variants"):
            raise ValueError("asset is not a metadata-only custom visual style")
        envelope = (asset.get("metadata") or {}).get(KEY)
        if (not isinstance(envelope, dict) or set(envelope) != {"incarnation", "edit_revision", "definition"}
                or not isinstance(envelope["incarnation"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", envelope["incarnation"])
                or type(envelope["edit_revision"]) is not int or envelope["edit_revision"] < 1):
            raise ValueError("invalid custom visual style revision")
        definition = normalize_definition(envelope["definition"])
        _text(asset.get("name"), "name", 200)
        # Timestamps are observation metadata, never edit authority. Bind other
        # fields too, so writes through existing generic CRUD retire selection.
        revision = digest({key: value for key, value in asset.items()
                           if key not in {"created_at", "updated_at"}})
        return {"project_id": project, "workspace_id": workspace, "asset_id": asset["id"],
                "name": asset["name"], "definition": definition, "revision": revision,
                "incarnation": envelope["incarnation"], "edit_revision": envelope["edit_revision"]}

    def get(self, project, workspace, asset_id):
        return self._view(project, workspace, self.store.get_asset(project, workspace, asset_id))

    def create(self, project, workspace, *, name, definition, asset_id=None):
        definition = normalize_definition(definition)
        asset = self.store.create_asset(project, workspace, name=name, asset_type="visual_style",
                                        description=definition["direction"],
                                        metadata={KEY: {"incarnation": uuid.uuid4().hex, "edit_revision": 1,
                                                        "definition": definition}}, asset_id=asset_id)
        return self._view(project, workspace, asset)

    def update(self, project, workspace, asset_id, *, name, definition, expected_revision):
        definition = normalize_definition(definition)
        with self.store.publication_guard():
            asset = self.store.get_asset(project, workspace, asset_id)
            current = self._view(project, workspace, asset)
            if expected_revision != current["revision"]:
                raise StyleConflict("This visual style changed. Reload it before editing.")
            metadata = deepcopy(asset["metadata"])
            metadata[KEY] = {"incarnation": current["incarnation"],
                             "edit_revision": current["edit_revision"] + 1, "definition": definition}
            result = self.store.update_asset(project, workspace, asset_id,
                                            {"name": name, "description": definition["direction"],
                                             "metadata": metadata})
            return self._view(project, workspace, result)

    def delete(self, project, workspace, asset_id, *, expected_revision):
        with self.store.publication_guard():
            if self.get(project, workspace, asset_id)["revision"] != expected_revision:
                raise StyleConflict("This visual style changed. Reload it before deleting.")
            return self.store.delete_asset(project, workspace, asset_id)

    def freeze(self, project, workspace, asset_id, *, expected_revision,
               resolve_reference, model_type, mode, capability=None):
        """Freeze after project authorization, using server-owned boundaries.

        resolve_reference(project, workspace, selection) must return current
        exact source identity plus path-free terms/access policy. It must reject
        inaccessible or explicitly unaccepted selections, including text-only mode.
        capability is a server adapter declaration, never client input. A plan
        records intent; only a later exact delivery receipt proves application.
        """
        _text(model_type, "model_type", 200)
        _text(mode, "mode", 100)
        with self.store.publication_guard():
            view = self.get(project, workspace, asset_id)
            if view["revision"] != expected_revision:
                raise StyleConflict("The selected visual style changed. Select it again.")
            definition = view["definition"]
            refs = []
            for selection in definition["references"]:
                source = resolve_reference(project, workspace, deepcopy(selection))
                if not isinstance(source, dict) or set(source) != {"selection", "sha256", "terms", "access_policy"}:
                    raise ValueError("reference authorization returned an invalid identity")
                if source["selection"] != selection or not re.fullmatch(r"[0-9a-f]{64}", str(source["sha256"])):
                    raise ValueError("reference source identity changed")
                if source["sha256"] != selection["expected_sha256"]:
                    raise StyleConflict("The selected visual reference changed. Select it again.")
                validate_source_terms(source["terms"])
                if not isinstance(source["access_policy"], dict):
                    raise ValueError("reference access policy is required")
                policy = source["access_policy"]
                if (policy.get("authorized") is not True
                        or policy.get("project_id") != project or policy.get("workspace_id") != workspace):
                    raise PermissionError("visual reference terms or access authorization are missing")
                refs.append(deepcopy(source))
            maximum = 0
            adapter = ""
            supported = False
            if capability is not None:
                if (not isinstance(capability, dict)
                        or set(capability) != {"model_type", "mode", "adapter_id", "max_images", "borrow_scope"}
                        or capability["model_type"] != model_type or capability["mode"] != mode):
                    raise ValueError("visual reference capability does not match the selected mode")
                adapter = _text(capability["adapter_id"], "adapter_id", 200)
                maximum = capability["max_images"]
                cap_scopes = capability["borrow_scope"]
                if (type(maximum) is not int or not 1 <= maximum <= 8 or not isinstance(cap_scopes, list)
                        or any(item not in SCOPES for item in cap_scopes)):
                    raise ValueError("invalid visual reference capability")
                supported = set(definition["borrow_scope"]).issubset(cap_scopes)
            for index, ref in enumerate(refs):
                planned = supported and index < maximum
                ref["delivery"] = "planned" if planned else "skipped"
                ref["reason"] = ("" if planned else "reference_limit" if supported
                                 else "borrow_scope_unsupported" if capability else "mode_unsupported")
            snapshot = {"version": 1, "phase": "planned", "style": view, "model_type": model_type, "mode": mode,
                        "adapter_id": adapter, "references": refs,
                        "application": "references_planned" if any(ref["delivery"] == "planned" for ref in refs)
                        else "text_only"}
            snapshot["digest"] = digest(snapshot)
            return snapshot


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("missing frozen visual style")
    body = deepcopy(snapshot)
    expected = body.pop("digest", None)
    if expected != digest(body):
        raise StyleConflict("Frozen visual style contents changed.")
    if body.get("phase") not in {"planned", "delivered"}:
        raise ValueError("invalid visual style snapshot phase")
    normalize_definition(body["style"]["definition"])
    if not isinstance(body.get("references"), list):
        raise ValueError("invalid frozen visual references")
    for reference in body["references"]:
        if not isinstance(reference, dict):
            raise ValueError("invalid frozen visual reference")
        validate_source_terms(reference.get("terms"))
    return body


def style_guidance(snapshot):
    body = validate_snapshot(snapshot)
    definition = body["style"]["definition"]
    names = {"palette": "palette", "materials": "materials",
             "lighting": "lighting", "camera_motion": "camera motion"}
    scope = ", ".join(names[item] for item in definition["borrow_scope"])
    return (f"CUSTOM VISUAL STYLE — borrow only {scope}:\n{definition['direction']}\n"
            "Preserve the authored medium, character identity, wardrobe, and explicit shot instructions. "
            "Keep other visual attributes unchanged unless explicitly authored elsewhere.")


def record_delivery(snapshot, receipts):
    """Return a detached render fact; callers must supply native adapter receipts.

    Receipt binding does not establish authenticity. Call this from the
    native adapter after verified file delivery.
    """
    body = validate_snapshot(snapshot)
    if body["phase"] != "planned":
        raise StyleConflict("Visual reference delivery is already finalized.")
    planned = [ref for ref in body["references"] if ref["delivery"] == "planned"]
    if not isinstance(receipts, list) or len(receipts) != len(planned):
        raise ValueError("every planned visual reference requires a delivery receipt")
    for ref, receipt in zip(planned, receipts):
        expected = {"style_digest": snapshot["digest"], "selection": ref["selection"],
                    "sha256": ref["sha256"], "model_type": body["model_type"], "mode": body["mode"],
                    "adapter_id": body["adapter_id"]}
        if not isinstance(receipt, dict) or set(receipt) != set(expected) | {"outcome", "reason"}:
            raise ValueError("invalid visual reference delivery receipt")
        if any(receipt[key] != value for key, value in expected.items()):
            raise StyleConflict("visual reference delivery binding changed")
        if receipt["outcome"] not in {"applied", "skipped"} or not isinstance(receipt["reason"], str):
            raise ValueError("invalid visual reference outcome")
        if receipt["outcome"] == "skipped" and not receipt["reason"].strip():
            raise ValueError("a skipped visual reference requires a reason")
        ref["delivery"] = receipt["outcome"]
        ref["reason"] = receipt["reason"]
    body["source_snapshot_digest"] = snapshot["digest"]
    body["phase"] = "delivered"
    body["application"] = "references_applied" if any(ref["delivery"] == "applied" for ref in body["references"]) else "text_only"
    body["digest"] = digest(body)
    return body
