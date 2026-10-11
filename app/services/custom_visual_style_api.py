"""Project-authorized custom visual style routes and generic mutation guards.

Callers supply project authorization, workspace reservation and reference access.
Generic guards must remain under the store publication guard through mutation.
"""
from contextlib import contextmanager
from copy import deepcopy

from fastapi import APIRouter, HTTPException, Request
from services.project_assets import ProjectAssetNotFoundError, ProjectAssetPersistenceError
from services.custom_visual_styles import KEY, StyleConflict, normalize_definition, validate_source_terms


def _error(error):
    if isinstance(error, HTTPException):
        details = {400: "Invalid visual style request", 401: "Visual style access denied",
                   403: "Visual style access denied", 404: "Visual style or reference not found",
                   409: "The style or reference changed. Reload it and try again.",
                   503: "Visual style storage is unavailable"}
        return HTTPException(error.status_code, details.get(error.status_code, "Visual style operation failed"))
    if isinstance(error, StyleConflict):
        return HTTPException(409, "This visual style changed. Reload it and try again.")
    if isinstance(error, ProjectAssetNotFoundError):
        return HTTPException(404, "Visual style not found")
    if isinstance(error, PermissionError):
        return HTTPException(403, "Visual reference access or terms acceptance is required")
    if isinstance(error, ValueError):
        return HTTPException(400, "Invalid visual style request")
    if isinstance(error, ProjectAssetPersistenceError):
        return HTTPException(503, "Visual style storage is unavailable")
    return HTTPException(500, "Visual style operation failed")


def guard_generic_asset_mutation(current_asset=None, body=None):
    """Used for generic create/update/delete/variant writes, under store lock."""
    metadata = body.get("metadata") if isinstance(body, dict) else None
    asset_type = body.get("asset_type") if isinstance(body, dict) else None
    if isinstance(asset_type, str):
        asset_type = asset_type.strip()
    if (isinstance(current_asset, dict) and current_asset.get("asset_type") == "visual_style"
            or asset_type == "visual_style"
            or isinstance(metadata, dict) and KEY in metadata):
        raise HTTPException(409, "Use Visual Styles to change this style.")


def _body(body, fields):
    if not isinstance(body, dict) or set(body) != set(fields):
        raise ValueError("invalid request fields")
    return body


def build_router(library, *, require_project_access, reserve_workspace_operations, resolve_reference,
                 resolve_current_reference=None, project_instance=None):
    router = APIRouter(prefix="/api/v1/projects/{project}/visual-styles")

    @contextmanager
    def boundary(request, project, *, mutate=False):
        # Same explicit permissions as current project metadata APIs. The
        # workspace is server-selected; body/query values cannot redirect it.
        require_project_access(request, project, existing_only=True,
                               permission="project.mutate" if mutate else "project.read")
        with reserve_workspace_operations(project):
            stamp = None
            expected_stamp = request.headers.get("X-Maestro-Project-Instance")
            if project_instance is not None:
                stamp = project_instance(project, "main")
                if not isinstance(stamp, str) or not stamp.strip():
                    raise HTTPException(503, "Project identity is unavailable")
            elif expected_stamp is not None:
                raise HTTPException(503, "Project identity is unavailable")
            if expected_stamp is not None and expected_stamp != stamp:
                raise HTTPException(409, "Project identity changed")
            yield project, "main", stamp

    def validate_references(request, project, workspace, definition):
        # Reuse the exact freeze validation before saving a library card. No
        # temporary style is persisted and no model capability is invented.
        for selection in definition["references"]:
            source = resolve_reference(request, project, workspace, deepcopy(selection))
            if not isinstance(source, dict) or set(source) != {"selection", "sha256", "terms", "access_policy"}:
                raise ValueError("invalid reference identity")
            if source["selection"] != selection or source["sha256"] != selection["expected_sha256"]:
                raise StyleConflict("visual reference changed")
            policy = source["access_policy"]
            validate_source_terms(source["terms"])
            if (not isinstance(policy, dict) or policy.get("authorized") is not True
                    or policy.get("project_id") != project or policy.get("workspace_id") != workspace):
                raise PermissionError("reference authorization unavailable")

    @router.post("/references/resolve")
    async def resolve_current_selection(project: str, request: Request):
        try:
            with boundary(request, project) as (project_id, workspace, _stamp):
                selector = _body(await request.json(), ("asset_id", "variant_id", "output_index"))
                normalize_definition({"version": 1, "direction": "Reference selection", "borrow_scope": ["palette"],
                                      "references": [{**selector, "expected_sha256": "0" * 64}]})
                if resolve_current_reference is None:
                    raise HTTPException(503, "Visual reference resolution is unavailable")
                with library.store.publication_guard():
                    source = resolve_current_reference(request, project_id, workspace, deepcopy(selector))
                    if not isinstance(source, dict) or set(source) != {"selection", "sha256", "terms", "access_policy"}:
                        raise ValueError("invalid current reference identity")
                    selection = normalize_definition({
                        "version": 1, "direction": "Reference selection", "borrow_scope": ["palette"],
                        "references": [source["selection"]],
                    })["references"][0]
                    if ({key: selection[key] for key in selector} != selector
                            or source["sha256"] != selection["expected_sha256"]):
                        raise StyleConflict("visual reference changed")
                    validate_source_terms(source["terms"])
                    policy = source["access_policy"]
                    if (not isinstance(policy, dict) or policy.get("authorized") is not True
                            or policy.get("project_id") != project_id or policy.get("workspace_id") != workspace):
                        raise PermissionError("reference authorization unavailable")
                    return selection
        except Exception as error:
            raise _error(error) from error

    @router.get("")
    def list_styles(project: str, request: Request):
        try:
            with boundary(request, project) as (project_id, workspace, stamp):
                result = {"styles": [library._view(project_id, workspace, asset)
                                     for asset in library.store.list_assets(project_id, workspace, asset_type="visual_style")]}
                if project_instance is not None:
                    result["project_instance"] = stamp
                return result
        except Exception as error:
            raise _error(error) from error

    @router.get("/{asset_id}")
    def get_style(project: str, asset_id: str, request: Request):
        try:
            with boundary(request, project) as (project_id, workspace, _stamp):
                return library.get(project_id, workspace, asset_id)
        except Exception as error:
            raise _error(error) from error

    @router.post("")
    async def create_style(project: str, request: Request):
        try:
            with boundary(request, project, mutate=True) as (project_id, workspace, _stamp):
                body = _body(await request.json(), ("name", "definition"))
                definition = normalize_definition(body["definition"])
                with library.store.publication_guard():
                    validate_references(request, project_id, workspace, definition)
                    return library.create(project_id, workspace, name=body["name"], definition=definition)
        except Exception as error:
            raise _error(error) from error

    @router.patch("/{asset_id}")
    async def update_style(project: str, asset_id: str, request: Request):
        try:
            with boundary(request, project, mutate=True) as (project_id, workspace, _stamp):
                body = _body(await request.json(), ("name", "definition", "expected_revision"))
                definition = normalize_definition(body["definition"])
                with library.store.publication_guard():
                    if library.get(project_id, workspace, asset_id)["revision"] != body["expected_revision"]:
                        raise StyleConflict("visual style changed")
                    validate_references(request, project_id, workspace, definition)
                    return library.update(project_id, workspace, asset_id, name=body["name"],
                                          definition=definition, expected_revision=body["expected_revision"])
        except Exception as error:
            raise _error(error) from error

    @router.delete("/{asset_id}")
    async def delete_style(project: str, asset_id: str, request: Request):
        try:
            with boundary(request, project, mutate=True) as (project_id, workspace, _stamp):
                body = _body(await request.json(), ("expected_revision",))
                return {"deleted": library.delete(project_id, workspace, asset_id,
                                                   expected_revision=body["expected_revision"])}
        except Exception as error:
            raise _error(error) from error

    return router
