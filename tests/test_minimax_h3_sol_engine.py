"""Continuum-honest MiniMax H3 Sol Engine contracts.

The model keeps its scoped ``maybe_sol_attention`` routing while the normal
Install, Update, and Start actions share one hardware-aware runtime profile.
Legacy launch artifacts remain only as compatibility provenance.
"""

from __future__ import annotations

from inspect import signature
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestSolEngineSourceContracts(unittest.TestCase):
    def test_menu_recognizes_each_runtime_environment_as_installed(self):
        loader = r"""
const existing = new Set(JSON.parse(process.argv[1]));
const launcher = require('./pinokio.js');
const info = {
  exists: (candidate) => existing.has(candidate),
  running: () => false,
  local: () => ({}),
};
launcher.menu({}, info)
  .then((menu) => process.stdout.write(JSON.stringify(menu)))
  .catch((error) => { console.error(error); process.exit(1); });
"""

        for runtime_path in ("app/env", "app/env-sol", "app/env-rtx50"):
            with self.subTest(runtime_path=runtime_path):
                completed = subprocess.run(
                    ["node", "-e", loader, json.dumps([runtime_path])],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                hrefs = [item.get("href") for item in json.loads(completed.stdout)]
                self.assertIn("start.js", hrefs)
                self.assertIn("update.js", hrefs)

        completed = subprocess.run(
            ["node", "-e", loader, "[]"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        menu = json.loads(completed.stdout)
        self.assertEqual([item.get("href") for item in menu], ["install.js"])
        self.assertTrue(menu[0]["default"])

    def test_old_driver_uses_legacy_runtime_except_on_rtx50(self):
        loader = r"""
const assert = require('assert');
const { runtimeProfile } = require('./launcher_profile.js');
const start = require('./start.js');
const install = require('./install.js');
const update = require('./update.js');
const torch = require('./torch.js');

const compatibilityCases = [
  {gpu: 'nvidia', platform: 'linux', gpu_target: 'sm_89', gpu_model: 'RTX 4090', gpu_driver: '579.9'},
  {gpu: 'nvidia', platform: 'linux', gpu_target: 'sm_90', gpu_model: 'H100', gpu_driver: '579.9'},
];

(async () => {
  for (const kernel of compatibilityCases) {
    kernel.envs = {};
    kernel.port = async () => 42003;
    const profile = runtimeProfile(kernel);
    assert.equal(profile.env, 'env');
    assert.equal(profile.python, '3.10');
    assert.equal(profile.marker, 'app/env/.maestro_torch_v1.installed');

    const startPlan = await start(kernel);
    const backend = startPlan.run.find((step) =>
      step.method === 'shell.run' &&
      Array.isArray(step.params.message) &&
      step.params.message.some((command) => command.includes('python launch.py'))
    );
    assert.equal(backend.params.venv, 'env');
    assert.equal(backend.params.venv_python, '3.10');

    const installPlan = await install(kernel);
    assert(!installPlan.run.some((step) => step.when === true && step.next === null));
    assert(installPlan.run.some((step) => step.when === true && step.method === 'notify'));
    const requirements = installPlan.run.find((step) =>
      step.method === 'shell.run' &&
      Array.isArray(step.params.message) &&
      step.params.message.some((command) => command.includes('requirements.txt'))
    );
    assert.equal(requirements.params.venv, 'env');

    const updatePlan = await update(kernel);
    assert(!updatePlan.run.some((step) => step.when === true && step.next === null));
    const jump = updatePlan.run.find((step) => step.method === 'jump');
    assert(jump.params.id.includes("exists('app/env/.maestro_torch_v1.installed')"));

    const torchPlan = await torch(kernel);
    assert(torchPlan.run.some((step) =>
      step.method === 'fs.write' &&
      step.params.path === 'app/env/.maestro_torch_v1.installed'
    ));
    const installCommands = torchPlan.run
      .filter((step) => step.method === 'shell.run' && Array.isArray(step.params.message))
      .flatMap((step) => step.params.message);
    assert(installCommands.some((command) => command.includes('triton==3.3.1')));
    assert(!installCommands.some((command) => command.includes('torch==2.10.0')));
  }

  const rtx50 = {
    gpu: 'nvidia', platform: 'linux', gpu_target: 'sm_120',
    gpu_model: 'RTX 5090', gpu_driver: '579.9', envs: {},
    port: async () => 42003,
  };
  assert((await start(rtx50)).run.some((step) => step.next === null));
  assert((await install(rtx50)).run.some((step) => step.when === true && step.next === null));
  assert((await update(rtx50)).run.some((step) => step.when === true && step.next === null));
  await assert.rejects(() => torch(rtx50), /driver 580 or newer/);
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", loader],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.stdout, "ok")

    def test_profile_selected_children_and_classic_start(self):
        loader = r"""
const assert = require('assert');
const install = require('./install.js');
const update = require('./update.js');
const classic = require('./start_classic.js');
const torch = require('./torch.js');

const childNames = [
  'blender_mcp_install.js',
  'blender_runtime_install.js',
  'h3_acceleration_install.js',
  'h3_w4a8_runtime_install.js',
];

(async () => {
  const kernel = {
    gpu: 'nvidia', platform: 'linux', gpu_target: 'sm_89',
    gpu_model: 'RTX 4090', gpu_driver: '580.1', envs: {},
    port: async () => 42003,
  };
  for (const name of childNames) {
    const child = await require('./' + name)(kernel);
    const shells = child.run.filter((step) => step.method === 'shell.run');
    assert(shells.length > 0);
    assert(shells.every((step) => step.params.path === 'app'));
    assert(shells.every((step) =>
      step.params.venv === "{{args && args.venv ? args.venv : 'env-sol'}}"
    ));
    assert(shells.every((step) =>
      step.params.venv_python ===
        "{{args && args.venv_python ? args.venv_python : '3.11'}}"
    ));
    assert(shells.every((step) =>
      step.params.env && step.params.env.CLOUDFLARE_API_TOKEN === ''
    ));
  }

  const legacyKernel = {
    ...kernel, gpu_target: 'sm_90', gpu_model: 'H100', gpu_driver: '579.9',
  };
  const legacyChild = await require('./blender_mcp_install.js')(legacyKernel);
  assert(legacyChild.run.filter((step) => step.method === 'shell.run').every((step) =>
    step.params.venv === "{{args && args.venv ? args.venv : 'env'}}" &&
    step.params.venv_python ===
      "{{args && args.venv_python ? args.venv_python : '3.10'}}"
  ));

  const sm120 = {...kernel, gpu_target: 'sm_120', gpu_model: 'RTX 5090'};
  const sm120H3 = await require('./h3_acceleration_install.js')(sm120);
  const sageInstall = sm120H3.run.find((step) =>
    String(step.params.message).includes('install_h3_sageattention.py')
  );
  const evaluateWhen = (when, args = undefined) => new Function(
    'args', 'platform', 'gpu', 'exists', `return (${when.slice(2, -2)});`
  )(args, 'linux', 'nvidia', () => false);
  assert.equal(evaluateWhen(sageInstall.when), false);
  assert(sm120H3.run.some((step) =>
    String(step.params.message).includes('sol_attn_kijai')
  ));

  const installPlan = await install(kernel);
  const updatePlan = await update(kernel);
  for (const name of childNames) {
    const installCalls = installPlan.run.filter((step) =>
      step.method === 'script.start' && step.params.uri === name
    );
    const updateCalls = updatePlan.run.filter((step) =>
      step.method === 'script.start' && step.params.uri === name
    );
    assert.equal(installCalls.length, 1);
    assert.equal(updateCalls.length, 2);
    for (const call of [...installCalls, ...updateCalls]) {
      assert.deepEqual(call.params.params, {venv: 'env-sol', venv_python: '3.11'});
    }
  }
  const installTorch = installPlan.run.find((step) =>
    step.method === 'script.start' && step.params.uri === 'torch.js'
  );
  assert.equal(installTorch.params.params.venv_python, '3.11');
  const updateTorch = updatePlan.run.filter((step) =>
    step.method === 'script.start' && step.params.uri === 'torch.js'
  );
  assert(updateTorch.every((step) => step.params.params.venv_python === '3.11'));
  const fullTorchIndex = updatePlan.run.findIndex((step) =>
    step.method === 'script.start' &&
    step.params.uri === 'torch.js' &&
    step.params.params.xformers === true
  );
  const buildH3Index = updatePlan.run.map((step) => step.params && step.params.uri)
    .lastIndexOf('h3_acceleration_install.js');
  assert(fullTorchIndex >= 0 && buildH3Index > fullTorchIndex);

  const torchPlan = await torch(kernel);
  assert(torchPlan.run.filter((step) => step.method === 'shell.run').every((step) =>
    step.params.venv_python ===
      "{{args && args.venv_python ? args.venv_python : '3.11'}}"
  ));
  assert(torchPlan.run.some((step) =>
    String(step.params.message).includes(
      '--marker env-sol/.maestro_sol_flash_2_8_3_v1.installed'
    )
  ));
  const linuxSharedRepair = torchPlan.run.find((step) =>
    step.method === 'shell.run' && step.when === '{{args && args.flash_only}}'
  );
  assert(String(linuxSharedRepair.params.message).includes(
    'install_optional_cuda_acceleration.py --marker env-sol/.maestro_sol_flash_2_8_3_v1.installed'
  ));
  assert(!String(linuxSharedRepair.params.message).includes('--flash-only'));
  const updateSharedRepair = updatePlan.run.find((step) =>
    step.method === 'script.start' &&
    step.params.uri === 'torch.js' &&
    step.params.params.flash_only === true
  );
  assert(updateSharedRepair.when.includes("!exists('app/env-sol/.maestro_sol_flash_2_8_3_v1.installed')"));
  assert(!torchPlan.run.some((step) =>
    step.method === 'fs.write' &&
    step.params.path === 'app/env-sol/.maestro_sol_flash_2_8_3_v1.installed'
  ));
  const windowsTorch = await torch({...kernel, platform: 'win32'});
  const windowsRepair = windowsTorch.run.find((step) =>
    step.method === 'shell.run' && step.when === '{{args && args.flash_only}}'
  );
  assert(String(windowsRepair.params.message).includes('flash_attn-2.8.3'));
  assert(!String(windowsRepair.params.message).includes('install_optional_cuda_acceleration.py'));
  assert(windowsTorch.run.some((step) =>
    step.method === 'fs.write' &&
    step.params.path === 'app/env-sol/.maestro_sol_flash_2_8_3_v1.installed'
  ));

  const classicPlan = await classic(kernel);
  const classicBackend = classicPlan.run.find((step) =>
    step.method === 'shell.run' &&
    Array.isArray(step.params.message) &&
    step.params.message.some((command) => command.includes('python wgp.py'))
  );
  assert.equal(
    classicBackend.params.venv,
    "{{exists('app/env-sol/.maestro_sol_runtime_v1.installed') ? 'env-sol' : 'env'}}"
  );
  assert.equal(
    classicBackend.params.venv_python,
    "{{exists('app/env-sol/.maestro_sol_runtime_v1.installed') ? '3.11' : '3.10'}}"
  );
  assert.equal(classicBackend.params.on[0].event, '/(http://[0-9.:]+)/');
  assert.equal(
    classicPlan.run.find((step) => step.method === 'local.set').params.url,
    '{{input.event[1]}}'
  );

  const legacy = {...kernel, gpu_target: 'sm_90', gpu_model: 'H100', gpu_driver: '579.9'};
  const legacyPlan = await classic(legacy);
  const legacyBackend = legacyPlan.run.find((step) => step.method === 'shell.run');
  assert.equal(legacyBackend.params.venv, 'env');
  assert.equal(legacyBackend.params.venv_python, '3.10');

  const rtx50 = {...kernel, gpu_target: 'sm_120', gpu_model: 'RTX 5090'};
  const rtx50Plan = await classic(rtx50);
  assert(rtx50Plan.run.some((step) =>
    step.next === null && String(step.when).includes('.maestro_torch_rtx50_v2.installed')
  ));
  const oldRtx50 = await classic({...rtx50, gpu_driver: '579.9'});
  assert(oldRtx50.run.some((step) =>
    step.next === null && /driver update required/i.test(step.params.title)
  ));
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", loader],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.stdout, "ok")

    def test_runtime_repair_copy_uses_normal_update(self):
        optional_installer = (
            APP / "scripts" / "install_optional_cuda_acceleration.py"
        ).read_text(encoding="utf-8")
        preflight = (APP / "scripts" / "runtime_preflight.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("normal Update action", optional_installer)
        self.assertIn("normal Update action", preflight)
        self.assertIn("_publish_marker(marker, ready=all(results))", optional_installer)
        self.assertIn("marker.unlink(missing_ok=True)", optional_installer)
        self.assertNotIn("Advanced > Repair H3 Performance Runtime", optional_installer)
        self.assertNotIn("Advanced > Repair RTX 50 Runtime", preflight)

    def test_preferred_runtime_fallback_requires_ready_legacy_marker(self):
        loader = r"""
const assert = require('assert');
const builders = [require('./start.js'), require('./start_classic.js')];
const kernel = {
  gpu: 'nvidia', platform: 'linux', gpu_target: 'sm_89',
  gpu_model: 'RTX 4090', gpu_driver: '580.1', envs: {},
  port: async () => 42003,
};
const preferred = 'app/env-sol/.maestro_sol_runtime_v1.installed';
const legacy = 'app/env/.maestro_torch_v1.installed';
const evaluate = (when, existing) => new Function(
  'exists', `return (${when.slice(2, -2)});`
)((candidate) => existing.has(candidate));

(async () => {
  for (const build of builders) {
    const plan = await build(kernel);
    const guard = plan.run.find((step) =>
      step.next === null && step.params.title === 'Maestro runtime update required'
    );
    const fallback = plan.run.find((step) =>
      step.method === 'log' && /preserved compatibility runtime/.test(step.params.raw)
    );
    assert(guard && fallback);

    const states = [
      [[], true, false],
      [[legacy], false, true],
      [[preferred], false, false],
      [[preferred, legacy], false, false],
    ];
    for (const [paths, guardExpected, fallbackExpected] of states) {
      const existing = new Set(paths);
      assert.equal(evaluate(guard.when, existing), guardExpected);
      assert.equal(evaluate(fallback.when, existing), fallbackExpected);
    }
  }
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", loader],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.stdout, "ok")

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
        classic = (ROOT / "start_classic.js").read_text(encoding="utf-8")
        install = (ROOT / "install.js").read_text(encoding="utf-8")
        reset = (ROOT / "reset.js").read_text(encoding="utf-8")

        self.assertIn('env: "env-sol"', profile)
        self.assertIn('target === "sm_89"', profile)
        self.assertIn("needsCuda13DriverUpdate", profile)
        self.assertIn("isSolCapable(kernel) && !needsCuda13DriverUpdate(kernel)", profile)
        self.assertIn("legacyRuntimeProfile", profile)
        self.assertIn("triton-windows==3.6.0.post25", torch_script)
        self.assertIn("torch==2.10.0", torch_script)
        self.assertIn("install_optional_cuda_acceleration.py", torch_script)
        self.assertIn("verify_sol_runtime.py", torch_script)
        self.assertIn("path: runtime.marker", torch_script)
        self.assertIn("path: runtime.flashMarker", torch_script)
        self.assertIn("...runtimeSecretEnv", torch_script)
        # The same installer retains the CUDA 12.8 compatibility profiles.
        self.assertIn("triton-windows==3.3.1.post19", torch_script)
        self.assertIn("torch==2.7.1", torch_script)
        self.assertIn("torch==2.7.0", torch_script)
        self.assertIn("uv pip install triton==3.3.1", torch_script)
        self.assertIn('module.exports = require("./torch")', sol_script)
        self.assertNotIn("Start with H3 Sol Engine", menu)
        self.assertNotIn("Finish H3 Performance Runtime Upgrade", menu)
        self.assertNotIn("Start with Compatibility Runtime", menu)
        self.assertNotIn("Repair H3 Performance Runtime", menu)
        self.assertIn("runtimeProfile", start)
        self.assertIn("legacyRuntimeProfile", start)
        self.assertIn("exists('${runtime.marker}') ? '${runtime.env}'", start)
        self.assertIn("venv: selectedEnv", start)
        self.assertIn("venv_python: selectedPython", start)
        self.assertIn('url: "{{input.event[1]}}"', start)
        self.assertIn("python launch.py", start)
        self.assertNotIn("require(\"./start_sol\")", start)
        self.assertIn("runtimeProfile(kernel)", classic)
        self.assertIn("venv: selectedEnv", classic)
        self.assertIn("venv_python: selectedPython", classic)
        self.assertIn('url: "{{input.event[1]}}"', classic)
        self.assertIn("runtimeProfile", install)
        self.assertIn("venv: runtime.env", install)
        self.assertIn("venv_python: runtime.python", install)
        self.assertIn('path: "app/env-sol"', reset)

    def test_update_repairs_the_active_sol_runtime(self):
        updater = (ROOT / "update.js").read_text(encoding="utf-8")
        sol_script = (ROOT / "sol_torch.js").read_text(encoding="utf-8")

        self.assertIn("module.exports = async (kernel)", updater)
        self.assertIn("runtimeProfile(kernel)", updater)
        self.assertIn('uri: "torch.js"', updater)
        self.assertIn("exists('${runtime.marker}')", updater)
        self.assertIn("exists('${runtime.flashMarker}')", updater)
        self.assertIn("flash_only: true", updater)
        self.assertIn("venv: runtime.env", updater)
        self.assertIn("venv_python: runtime.python", updater)
        self.assertNotIn('uri: "sol_torch.js"', updater)
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

    def test_legacy_sol_launchers_are_canonical_compatibility_aliases(self):
        start_alias = (ROOT / "start_sol.js").read_text(encoding="utf-8")
        install_alias = (ROOT / "sol_install.js").read_text(encoding="utf-8")

        self.assertIn('module.exports = require("./start")', start_alias)
        self.assertIn('module.exports = require("./update")', install_alias)
        self.assertNotIn("MAESTRO_SOL_RUNTIME", start_alias)
        self.assertNotIn("torch==", install_alias)

        loader = r"""
const assert = require('assert');
const start = require('./start.js');
const startAlias = require('./start_sol.js');
const update = require('./update.js');
const installAlias = require('./sol_install.js');
assert.strictEqual(startAlias, start);
assert.strictEqual(installAlias, update);

const kernel = {
  gpu: 'nvidia', platform: 'linux', gpu_target: 'sm_89',
  gpu_model: 'RTX 4090', gpu_driver: '580.1', envs: {},
  port: async () => 42003,
};
(async () => {
  const startPlan = await startAlias(kernel);
  const backend = startPlan.run.find((step) =>
    step.method === 'shell.run' &&
    Array.isArray(step.params.message) &&
    step.params.message.some((command) => command.includes('python launch.py'))
  );
  assert.equal(
    backend.params.venv,
    "{{exists('app/env-sol/.maestro_sol_runtime_v1.installed') ? 'env-sol' : 'env'}}"
  );
  assert.equal(backend.params.on.find((handler) => handler.done).event, '/(http://[0-9.:]+)/');
  assert.equal(
    startPlan.run.find((step) => step.method === 'local.set' && step.params.url).params.url,
    '{{input.event[1]}}'
  );

  const updatePlan = await installAlias(kernel);
  const repair = updatePlan.run.find((step) =>
    step.method === 'script.start' &&
    step.params.uri === 'torch.js' &&
    step.params.params.flash_only === true
  );
  assert(repair.when.includes("!exists('app/env-sol/.maestro_sol_flash_2_8_3_v1.installed')"));

  const oldRtx50 = {
    ...kernel, gpu_target: 'sm_120', gpu_model: 'RTX 5090', gpu_driver: '579.9',
  };
  assert((await startAlias(oldRtx50)).run.some((step) => step.next === null));
  assert((await installAlias(oldRtx50)).run.some((step) =>
    step.when === true && step.next === null
  ));
  process.stdout.write('ok');
})().catch((error) => { console.error(error); process.exit(1); });
"""
        completed = subprocess.run(
            ["node", "-e", loader],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.stdout, "ok")

    def test_ui_uses_server_profiles_without_obsolete_sol_override_controls(self):
        types = (ROOT / "ui" / "src" / "types" / "index.ts").read_text(encoding="utf-8")
        retired_optimizations = ROOT / "ui/src/components/Sidebar/MiniMaxH3Optimizations.tsx"
        profiles = (ROOT / "ui/src/components/Sidebar/H3PerformanceProfiles.tsx").read_text(encoding="utf-8")
        advanced = (ROOT / "ui" / "src" / "components" / "Sidebar" / "AdvancedSettings.tsx").read_text(encoding="utf-8")
        store = (ROOT / "ui" / "src" / "stores" / "useStore.ts").read_text(encoding="utf-8")

        self.assertFalse(retired_optimizations.exists())
        self.assertIn("state.h3PerformanceProfiles", profiles)
        self.assertIn("state.applyH3PerformanceProfile", profiles)
        self.assertIn("const settings = profile.settings", store)
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
        dense_attention = types.ModuleType("shared.attention")
        dense_attention.get_default_attention_mode = lambda: "sdpa"
        with patch.dict(sys.modules, {"shared.attention": dense_attention}):
            policy._fallback("test failure")

        self.assertTrue(policy._runtime_failed)
        self.assertFalse(policy.enabled)


if __name__ == "__main__":
    unittest.main()
