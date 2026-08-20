"""Continuum-honest MiniMax H3 Sol Engine contracts.

Locks leftover 1.9.0 names (`_sol_attention_status`, `triton-windows==3.6.0.post25`,
`torch==2.10.0` on Continuum `torch.js`, `exists('${runtime.marker}')` on
`start.js`/`update.js`, `override_attention?: '' | 'sol'`) to the Continuum
path: `maybe_sol_attention`, shared `get_sol_attention_status`, CUDA 12.8
`torch.js`, and a separate optional `start_sol.js`. Do not invent those
dropped launcher/type helpers.
"""

from __future__ import annotations

from inspect import signature
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestSolEngineSourceContracts(unittest.TestCase):
    def test_h3_declares_sol_without_exposing_it_as_a_global_backend(self):
        handler = (APP / "models" / "minimax_h3" / "minimax_h3_handler.py").read_text(encoding="utf-8")
        launch = (APP / "launch.py").read_text(encoding="utf-8")
        engine = (APP / "wgp.py").read_text(encoding="utf-8")
        attention = (APP / "shared" / "attention.py").read_text(encoding="utf-8")
        transformer = (APP / "models" / "minimax_h3" / "transformer.py").read_text(encoding="utf-8")

        self.assertIn('custom_settings.setdefault("h3_attention_engine", "sol_attn")', handler)
        self.assertIn("from services.h3_acceleration import maybe_sol_attention", transformer)
        self.assertIn("def get_sol_attention_status", attention)
        self.assertIn("def get_override_attention_modes", attention)
        self.assertIn("def get_supported_override_attention_modes", attention)
        self.assertNotIn('ret.append("sol")', attention)
        # Leftover 1.9.0 catalog/launch names Continuum never restored.
        self.assertNotIn('"sol_attention": True', handler)
        self.assertNotIn('"sol_attention_status": _sol_attention_status', launch)
        self.assertNotIn('attn == "sol" and not model_def.get("sol_attention"', engine)
        self.assertNotIn("get_supported_override_attention_modes", engine)

    def test_sol_package_and_upstream_license_are_bundled(self):
        package = APP / "shared" / "sol_attn"
        self.assertTrue((package / "interface.py").is_file())
        self.assertTrue((package / "saganaki" / "LICENSE").is_file())
        self.assertIn(
            "Apache License",
            (package / "saganaki" / "LICENSE").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "SPDX-License-Identifier: Apache-2.0",
            (package / "interface.py").read_text(encoding="utf-8"),
        )

    def test_sol_runtime_is_default_on_supported_hardware_with_legacy_fallback(self):
        profile = (ROOT / "launcher_profile.js").read_text(encoding="utf-8")
        torch_script = (ROOT / "torch.js").read_text(encoding="utf-8")
        sol_script = (ROOT / "sol_torch.js").read_text(encoding="utf-8")
        menu = (ROOT / "pinokio.js").read_text(encoding="utf-8")
        start = (ROOT / "start.js").read_text(encoding="utf-8")
        reset = (ROOT / "reset.js").read_text(encoding="utf-8")

        self.assertIn('env: "env-sol"', profile)
        self.assertIn('target === "sm_89"', profile)
        self.assertIn("needsCuda13DriverUpdate", profile)
        self.assertIn("isSolCapable(kernel) && !needsCuda13DriverUpdate(kernel)", profile)
        self.assertIn("legacyRuntimeProfile", profile)
        self.assertIn("triton-windows==3.3.1.post19", torch_script)
        self.assertIn("torch==2.7.1", torch_script)
        self.assertIn("torch==2.7.0", torch_script)
        self.assertNotIn("triton-windows==3.6.0.post25", torch_script)
        self.assertNotIn("torch==2.10.0", torch_script)
        self.assertNotIn("install_optional_cuda_acceleration.py", torch_script)
        self.assertNotIn("verify_sol_runtime.py", torch_script)
        self.assertNotIn("git+https://github.com/thu-ml/SageAttention.git", torch_script)
        self.assertNotIn("uv pip install flash-attn --no-build-isolation", torch_script)
        self.assertIn('module.exports = require("./torch")', sol_script)
        self.assertNotIn("Start with H3 Sol Engine", menu)
        self.assertNotIn("Finish H3 Performance Runtime Upgrade", menu)
        self.assertNotIn("Start with Compatibility Runtime", menu)
        self.assertNotIn("Repair H3 Performance Runtime", menu)
        self.assertIn('venv: "env"', start)
        self.assertIn("python launch.py", start)
        self.assertNotIn("legacyRuntimeProfile", start)
        self.assertNotIn("exists('${runtime.marker}') ? '${runtime.env}'", start)
        self.assertNotIn("require(\"./start_sol\")", start)
        self.assertIn('path: "app/env-sol"', reset)

    def test_update_repairs_the_active_sol_runtime(self):
        updater = (ROOT / "update.js").read_text(encoding="utf-8")
        sol_script = (ROOT / "sol_torch.js").read_text(encoding="utf-8")

        self.assertIn('uri: "torch.js"', updater)
        self.assertIn("exists('app/env/.maestro_torch_v2.installed')", updater)
        self.assertNotIn("exists('${runtime.marker}')", updater)
        self.assertNotIn("exists('${runtime.flashMarker}')", updater)
        self.assertNotIn('uri: "sol_torch.js"', updater)
        self.assertNotIn("flash_only: true", updater)
        self.assertIn('module.exports = require("./torch")', sol_script)

    def test_runtime_preflight_reports_sol_readiness(self):
        preflight = (APP / "scripts" / "runtime_preflight.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("H3 Sol Engine=", preflight)
        self.assertIn("triton-windows", preflight)

    def test_sol_upstream_notice_is_bundled(self):
        notice = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        self.assertIn("46031940ba8af5d18054217e571149579424c0b1", notice)
        self.assertIn("ComfyUI-sol-attn", notice)

    def test_sol_start_uses_required_url_capture_contract(self):
        start = (ROOT / "start_sol.js").read_text(encoding="utf-8")

        self.assertIn('path: "app"', start)
        self.assertIn('"event": "/(http:\\/\\/[0-9.:]+)/"', start)
        self.assertIn('url: "{{input.event[1]}}"', start)

    def test_ui_persists_and_strips_model_scoped_override(self):
        types = (ROOT / "ui" / "src" / "types" / "index.ts").read_text(encoding="utf-8")
        optimizations = (ROOT / "ui" / "src" / "components" / "Sidebar" / "MiniMaxH3Optimizations.tsx").read_text(encoding="utf-8")
        advanced = (ROOT / "ui" / "src" / "components" / "Sidebar" / "AdvancedSettings.tsx").read_text(encoding="utf-8")
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(encoding="utf-8")

        self.assertIn("H3 Optimizations", optimizations)
        self.assertIn("Sol Engine", optimizations)
        self.assertIn("params.override_attention === 'sol'", optimizations)
        self.assertNotIn("modelOptions?.sol_attention && (", advanced)
        # Leftover 1.9.0 persist/strip names were never added to Continuum types/store.
        self.assertNotIn("override_attention?: '' | 'sol'", types)
        self.assertNotIn("delete params.override_attention", store)
        self.assertNotIn("p.override_attention === 'sol'", store)


class TestSolAttentionRouting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import torch
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(
                "MiniMax H3 Sol routing tests require PyTorch"
            ) from exc

        cls.torch = torch

    def test_main_h3_blocks_do_not_use_dropped_shared_sol_policy_attr(self):
        from models.minimax_h3.transformer import MiniMaxH3Attention, MiniMaxH3Transformer

        self.assertNotIn("sol_attention", signature(MiniMaxH3Attention.__init__).parameters)
        self.assertNotIn("sol_attention", signature(MiniMaxH3Transformer.__init__).parameters)
        transformer = (APP / "models" / "minimax_h3" / "transformer.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("self.sol_attention", transformer)
        self.assertNotIn("attn.sol_attention", transformer)
        self.assertIn("from services.h3_acceleration import maybe_sol_attention", transformer)

    def test_attention_routes_eligible_call_through_maybe_sol_attention(self):
        from models.minimax_h3.transformer import MiniMaxH3Attention

        self.assertNotIn("sol_attention", signature(MiniMaxH3Attention.__init__).parameters)
        attention = (APP / "models" / "minimax_h3" / "transformer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("attended = maybe_sol_attention(", attention)
        self.assertNotIn("sol_attention=probe", attention)
        self.assertNotIn("use_for_layer(", attention)

    def test_kernel_failure_stays_on_dense_fallback_for_process(self):
        from models.minimax_h3.sol_attention import MiniMaxH3SolAttention

        policy = MiniMaxH3SolAttention()
        policy.enabled = True
        policy._fallback("test failure")

        self.assertTrue(policy._runtime_failed)
        self.assertFalse(policy.enabled)


if __name__ == "__main__":
    unittest.main()
