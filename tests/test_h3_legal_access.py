"""CPU-only regressions for MiniMax H3 legal availability and admission."""

from __future__ import annotations

import ast
import asyncio
import copy
import hmac
import os
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.h3_legal_access import (
    H3_EXCLUDED_TERRITORIES,
    H3_LEGAL_BLOCKED_DETAIL,
    H3_LICENSE_SHA256,
    H3_LICENSE_URL,
    H3_RECOGNIZED_TERRITORIES,
    H3_REGISTERED_MODEL_TYPES,
    H3_UPSTREAM_REVISION,
    H3LegalAccessError,
    H3LocationDeclarationError,
    h3_operating_location_status,
    h3_public_availability,
    is_registered_h3_family,
    record_h3_operating_location,
    require_h3_execution_allowed,
)


def _launch_function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def _load_launch_function(source: str, name: str, namespace: dict) -> None:
    tree = ast.parse(source)
    node = next(
        copy.deepcopy(item) for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    node.decorator_list = []
    module = ast.Module(body=[node], type_ignores=[])
    exec(  # noqa: S102 - executes one AST-extracted test fixture only
        compile(ast.fix_missing_locations(module), "launch-slice", "exec"), namespace,
    )


class H3LegalAccessPolicyTests(unittest.TestCase):
    def _services(self, territory="US"):
        services = {}
        record_h3_operating_location(
            services,
            territory_code=territory,
            owner_attested=True,
            license_revision=H3_UPSTREAM_REVISION,
            license_sha256=H3_LICENSE_SHA256,
            declared_at_unix=1_700_000_000,
        )
        return services

    def test_official_license_binding_and_excluded_territories_are_frozen(self):
        self.assertEqual(
            H3_UPSTREAM_REVISION,
            "42ed227ee7df40d41602854ae760620d6eb651fe",
        )
        self.assertEqual(
            H3_LICENSE_SHA256,
            "59b99642b95ea21630e311198ddbfffbfe05aadba0c2f5d884cbdf4efcc90f44",
        )
        self.assertEqual(
            H3_LICENSE_URL,
            "https://huggingface.co/MiniMaxAI/MiniMax-H3/resolve/"
            "42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE",
        )
        self.assertIn("US", H3_EXCLUDED_TERRITORIES)
        self.assertIn("GB", H3_EXCLUDED_TERRITORIES)
        self.assertIn("KR", H3_EXCLUDED_TERRITORIES)
        self.assertNotIn("CA", H3_EXCLUDED_TERRITORIES)
        self.assertEqual(len(H3_RECOGNIZED_TERRITORIES), 249)
        self.assertTrue(H3_EXCLUDED_TERRITORIES < H3_RECOGNIZED_TERRITORIES)

    def test_all_registered_variants_fail_closed(self):
        self.assertEqual(len(H3_REGISTERED_MODEL_TYPES), 4)
        for model_type in sorted(H3_REGISTERED_MODEL_TYPES):
            with self.subTest(model_type=model_type):
                self.assertTrue(is_registered_h3_family(model_type))
                self.assertEqual(
                    h3_public_availability(model_type),
                    {
                        "availability_status": "location_declaration_required",
                        "execution_allowed": False,
                    },
                )
                with self.assertRaisesRegex(
                    H3LegalAccessError, "does not infer this from an IP address",
                ):
                    require_h3_execution_allowed([model_type])

    def test_manual_location_is_vpn_independent_and_excluded_locations_block(self):
        allowed = self._services("CA")
        blocked = self._services("US")
        self.assertEqual(
            h3_public_availability("minimax_h3", services=allowed),
            {"availability_status": "available", "execution_allowed": True},
        )
        require_h3_execution_allowed(["minimax_h3"], services=allowed)
        self.assertEqual(
            h3_public_availability("minimax_h3", services=blocked),
            {"availability_status": "legal_blocked", "execution_allowed": False},
        )
        with self.assertRaisesRegex(H3LegalAccessError, "written MiniMax authorization"):
            require_h3_execution_allowed(["minimax_h3"], services=blocked)

    def test_location_declaration_binds_current_license_and_rejects_tamper(self):
        services = self._services("uk")
        status = h3_operating_location_status(services)
        self.assertEqual(status["territory_code"], "GB")
        self.assertEqual(status["availability_status"], "legal_blocked")
        for key, value in (
            ("license_revision", "stale"),
            ("owner_attested", False),
            ("territory_code", "USA"),
        ):
            changed = copy.deepcopy(services)
            changed["h3_operating_location"][key] = value
            self.assertFalse(h3_operating_location_status(changed)["declared"])
        with self.assertRaises(H3LocationDeclarationError):
            record_h3_operating_location(
                {}, territory_code="CA", owner_attested=True,
                license_revision="stale", license_sha256=H3_LICENSE_SHA256,
            )
        with self.assertRaises(H3LocationDeclarationError):
            record_h3_operating_location(
                {}, territory_code="ZZ", owner_attested=True,
                license_revision=H3_UPSTREAM_REVISION,
                license_sha256=H3_LICENSE_SHA256,
            )

    def test_registered_architecture_covers_derivatives_without_name_matching(self):
        derivative = {
            "architecture": "minimax_h3",
            "name": "A derivative whose display name is irrelevant",
            "URLs": ["https://example.invalid/not-inspected"],
        }
        self.assertTrue(
            is_registered_h3_family("owner_variant_1", model_def=derivative),
        )
        with self.assertRaises(H3LegalAccessError):
            require_h3_execution_allowed(
                ["owner_variant_1"],
                model_defs={"owner_variant_1": derivative},
                services=self._services("US"),
            )
        self.assertFalse(
            is_registered_h3_family(
                "name_contains_minimax_h3_but_is_unregistered",
                model_def={"architecture": "unrelated"},
            ),
        )

    def test_unknown_and_environment_values_cannot_enable_h3(self):
        prior = os.environ.get("MAESTRO_H3_LICENSE_AUTHORIZATION")
        os.environ["MAESTRO_H3_LICENSE_AUTHORIZATION"] = "accepted"
        try:
            with self.assertRaises(H3LegalAccessError):
                require_h3_execution_allowed(["minimax_h3"])
        finally:
            if prior is None:
                os.environ.pop("MAESTRO_H3_LICENSE_AUTHORIZATION", None)
            else:
                os.environ["MAESTRO_H3_LICENSE_AUTHORIZATION"] = prior
        for malformed in ({}, {"h3_operating_location": "CA"}):
            with self.subTest(malformed=malformed), self.assertRaises(H3LegalAccessError):
                require_h3_execution_allowed(["minimax_h3"], services=malformed)

    def test_non_h3_models_have_no_h3_projection(self):
        self.assertEqual(
            h3_public_availability(
                "flux_1", model_def={"architecture": "flux"},
            ),
            {},
        )
        require_h3_execution_allowed(
            ["flux_1"], model_defs={"flux_1": {"architecture": "flux"}},
        )

    def test_public_projection_is_content_free_and_bounded(self):
        projection = h3_public_availability("minimax_h3_ref2va")
        self.assertEqual(
            set(projection), {"availability_status", "execution_allowed"},
        )
        self.assertNotIn("US", repr(projection))
        self.assertNotIn(H3_LICENSE_SHA256, repr(projection))
        self.assertIn("Accepting model terms does not grant access", H3_LEGAL_BLOCKED_DETAIL)


class H3LegalAccessWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launch = (APP / "launch.py").read_text(encoding="utf-8")
        cls.model_selector = (
            ROOT / "ui/src/components/Sidebar/ModelSelector.tsx"
        ).read_text(encoding="utf-8")
        cls.client = (ROOT / "ui/src/api/client.ts").read_text(
            encoding="utf-8",
        )
        cls.inputs_panel = (
            ROOT / "ui/src/components/Sidebar/InputsPanel.tsx"
        ).read_text(encoding="utf-8")
        cls.generate_button = (
            ROOT / "ui/src/components/Sidebar/GenerateButton.tsx"
        ).read_text(encoding="utf-8")
        cls.plan_dialog = (
            ROOT / "ui/src/components/H3GenerationPlanDialog.tsx"
        ).read_text(encoding="utf-8")
        cls.system_settings = (
            ROOT / "ui/src/components/SettingsDrawer/SystemSettingsPanel.tsx"
        ).read_text(encoding="utf-8")

    def test_catalog_download_submission_and_worker_are_gated(self):
        self.assertIn("**legal_availability", self.launch)
        self.assertIn("_require_h3_legal_execution(newly_enabled)", self.launch)
        self.assertGreaterEqual(
            self.launch.count("_require_h3_legal_execution([model_type])"),
            2,
        )
        self.assertIn(
            "_require_h3_legal_execution(_h3_job_model_types(job))",
            self.launch,
        )
        self.assertIn(
            '"_recovery_reason_code": "h3_legal_access_required"',
            self.launch,
        )

    def test_manual_location_routes_are_owner_reauth_bound_and_vpn_independent(self):
        self.assertIn('@api.get("/api/v1/h3/legal-access")', self.launch)
        self.assertIn('@api.put("/api/v1/h3/legal-access")', self.launch)
        self.assertGreaterEqual(
            self.launch.count("_require_owner_policy_control(request)"), 4,
        )
        self.assertIn('"location_source": "manual_owner_declaration"', self.launch)
        self.assertIn('"network_location_used": False', self.launch)
        gate = _launch_function_source(self.launch, "_require_h3_legal_execution")
        self.assertIn('services=wgp.server_config.get("services", {})', gate)
        parameters = ast.parse(gate).body[0].args.args
        self.assertEqual([parameter.arg for parameter in parameters], ["model_types"])

    def test_manual_location_route_persists_only_after_owner_authority(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        events = []
        writes = []
        body = {
            "territory_code": "CA",
            "owner_attested": True,
            "license_revision": H3_UPSTREAM_REVISION,
            "license_sha256": H3_LICENSE_SHA256,
        }

        async def request_body(_request):
            events.append("body")
            return dict(body)

        wgp = types.SimpleNamespace(
            server_config={"services": {}},
            server_config_filename="server.json",
        )
        namespace = {
            "Request": object,
            "HTTPException": FakeHTTPException,
            "copy": copy,
            "_services_config_lock": threading.RLock(),
            "_require_owner_policy_control": lambda _request: events.append("owner"),
            "_account_request_body": request_body,
            "_atomic_write_json": lambda path, value: writes.append((path, copy.deepcopy(value))),
            "wgp": wgp,
            "record_h3_operating_location": record_h3_operating_location,
            "h3_operating_location_status": h3_operating_location_status,
            "H3LocationDeclarationError": H3LocationDeclarationError,
        }
        _load_launch_function(self.launch, "update_h3_legal_access", namespace)
        result = asyncio.run(namespace["update_h3_legal_access"](object()))
        self.assertEqual(events, ["owner", "body"])
        self.assertEqual(result["availability_status"], "available")
        self.assertFalse(result["network_location_used"])
        self.assertEqual(writes[0][0], "server.json")
        self.assertEqual(
            writes[0][1]["services"]["h3_operating_location"]["territory_code"],
            "CA",
        )

    def test_director_preview_estimate_and_recovery_share_the_gate(self):
        self.assertIn("_require_h3_legal_execution(requested_models)", self.launch)
        self.assertIn("_require_h3_legal_execution([selected_model])", self.launch)
        self.assertIn("_require_h3_legal_execution(_h3_job_model_types(job))", self.launch)
        self.assertIn("if _job_uses_registered_h3(runtime):", self.launch)
        self.assertIn(
            'segment.get("model_type")',
            _launch_function_source(self.launch, "_h3_job_model_types"),
        )
        requirements = _launch_function_source(
            self.launch, "_h3_generation_requirements",
        )
        self.assertIn("**legal_availability", requirements)
        self.assertIn('"available": False', requirements)
        resume = _launch_function_source(
            self.launch, "_resume_recovered_job",
        )
        self.assertIn('"h3_legal_access_required"', resume)

    def test_transport_does_not_change_the_server_owned_legal_decision(self):
        director = _launch_function_source(
            self.launch, "_director_require_visible_model",
        )
        self.assertLess(
            director.index("_require_remote_visible_models"),
            director.index("_require_h3_legal_execution"),
        )
        legal_gate = _launch_function_source(
            self.launch, "_require_h3_legal_execution",
        )
        referenced_names = {
            node.id.casefold()
            for node in ast.walk(ast.parse(legal_gate))
            if isinstance(node, ast.Name)
        }
        self.assertTrue(
            {"request", "remote", "origin"}.isdisjoint(referenced_names),
        )

    def test_completed_outputs_shares_and_delivery_only_recovery_stay_available(self):
        for name in (
            "list_outputs", "serve_file", "create_output_share",
            "serve_output_share_media",
        ):
            with self.subTest(name=name):
                self.assertNotIn(
                    "_require_h3_legal_execution",
                    _launch_function_source(self.launch, name),
                )
        materialize = _launch_function_source(
            self.launch, "_queue_recovery_materialize_job",
        )
        self.assertLess(
            materialize.index('if status in {"completed", "failed"}:'),
            materialize.index("if _job_uses_registered_h3(runtime):"),
        )
        for name in ("_start_generation_worker", "_run_generation"):
            worker = _launch_function_source(self.launch, name)
            self.assertLess(
                worker.index("_queue_recovery_delivery_pending(job) is None"),
                worker.index("_require_h3_legal_execution"),
            )

    def test_future_authorized_legal_resume_preserves_recovery_attempt(self):
        class FakeHTTPException(Exception):
            def __init__(self, *, status_code, detail):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        starts = []

        class FakeThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                starts.append(True)

        job = {
            "id": "h3-legal-resume",
            "kind": "studio_generation",
            "status": "queued",
            "queue_held": True,
            "recovery_state": "blocked",
            "recovery_attempt": 2,
            "_recovery_reason_code": "h3_legal_access_required",
            "_recovery_owner_digest": "owner",
            "workspace": "project-a",
            "out_dir": "/project-a",
            "params": {"model_type": "minimax_h3"},
        }
        namespace = {
            "Request": object,
            "HTTPException": FakeHTTPException,
            "threading": types.SimpleNamespace(Thread=FakeThread),
            "hmac": hmac,
            "MAX_RECOVERY_ATTEMPTS": 3,
            "_queue_recovery_checkpoint_lock": threading.RLock(),
            "_QUEUE_RECOVERY_REASON_TEXT": {
                "h3_legal_access_required": "License required",
            },
            "_require_owned_job": lambda _job_id, _request: job,
            "_queue_recovery_reason_code": (
                lambda candidate: candidate["_recovery_reason_code"]
            ),
            "_require_project_access": lambda *_args, **_kwargs: "/project-a",
            "owner_principal_digest": lambda *_args: "owner",
            "_session_secret": lambda: b"secret",
            "_queue_recovery_revalidate_job": lambda _job: True,
            "_queue_recovery_delivery_pending": lambda _job: None,
            "_require_job_runtime_model_admission": lambda _job: None,
            "_queue_recovery_worker": lambda _job: (lambda _job_id: None),
            "next_recovery_attempt": lambda _job: self.fail(
                "legal resume consumed a recovery attempt",
            ),
            "_queue_recovery_checkpoint": (
                lambda candidate, **updates: candidate.update(updates) or True
            ),
            "update_queue_job": lambda candidate, **updates: (
                candidate.update(updates) or True
            ),
        }
        _load_launch_function(
            self.launch, "_resume_recovered_job", namespace,
        )
        request = types.SimpleNamespace(
            state=types.SimpleNamespace(maestro_session_id="session"),
        )
        result = namespace["_resume_recovered_job"](
            job["id"], request, requested_action="resume",
        )
        self.assertEqual(result["recovery_attempt"], 2)
        self.assertEqual(job["recovery_attempt"], 2)
        self.assertEqual(starts, [True])

    def test_ui_disables_instead_of_offering_terms_as_unlock(self):
        self.assertIn("currentModelLegalBlocked", self.model_selector)
        self.assertIn("location_declaration_required", self.model_selector)
        self.assertIn("!currentModelLegalBlocked", self.model_selector)
        self.assertIn("disabled={w4a8Unavailable || legalBlocked}", self.model_selector)
        self.assertIn("h3ExecutionBlocked", self.inputs_panel)
        self.assertIn("needsRef2VA && !legalBlocked", self.plan_dialog)
        self.assertIn("disabled={legalBlocked || reviewLoading", self.plan_dialog)
        self.assertIn("disabled={isExecutionBlocked(m)}", self.system_settings)
        self.assertIn("location_declaration_required", self.system_settings)
        self.assertIn("const legalBlocked = useStore", self.generate_button)
        self.assertIn("'Location needed' : 'License required'", self.generate_button)
        self.assertIn(
            "m.availability_status !== 'legal_blocked'",
            self.model_selector,
        )
        self.assertIn(
            "m.availability_status !== 'location_declaration_required'",
            self.model_selector,
        )
        self.assertGreaterEqual(self.client.count("res.status === 451"), 3)
        for source in (
            self.model_selector,
            self.inputs_panel,
            self.plan_dialog,
            self.generate_button,
        ):
            self.assertIn("separate written minimax authorization is required", source.casefold())


if __name__ == "__main__":
    unittest.main()
