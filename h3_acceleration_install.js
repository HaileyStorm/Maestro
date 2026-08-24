// Optional MiniMax H3 acceleration companions. Profile/default policy remains
// in app code; this script only provisions pinned, capability-gated runtimes.
const revision = "0e334dc981cfe3b0ed926ee13ad43f64914b7f5b"
const sageRevision = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"
const { runtimeSecretEnv } = require("./launcher_secret_env")
const { runtimeProfile } = require("./launcher_profile")

module.exports = async (kernel) => {
  const runtime = runtimeProfile(kernel)
  const selectedVenv = `{{args && args.venv ? args.venv : '${runtime.env}'}}`
  const selectedPython = `{{args && args.venv_python ? args.venv_python : '${runtime.python}'}}`
  const legacySageWhen = (condition) => (
    `{{(args && args.venv_python ? args.venv_python : '${runtime.python}') === '3.10' && ${condition}}}`
  )
  return {
    run: [
    {
      when: "{{!exists('app/services/sol_attn_kijai/.git')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "git clone https://github.com/Kijai/ComfyUI-SolAttn_triton.git services/sol_attn_kijai"
      }
    },
    {
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: [
          "git -C services/sol_attn_kijai fetch origin",
          `git -C services/sol_attn_kijai checkout --detach ${revision}`
        ]
      }
    },
    {
      when: legacySageWhen("platform === 'linux' && gpu === 'nvidia' && !exists('app/services/sageattention_thu_ml/.git')"),
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "git clone --branch v2.2.0 https://github.com/thu-ml/SageAttention.git services/sageattention_thu_ml"
      }
    },
    {
      when: legacySageWhen("platform === 'linux' && gpu === 'nvidia'"),
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: [
          "git -C services/sageattention_thu_ml fetch origin tag v2.2.0",
          `git -C services/sageattention_thu_ml checkout --detach ${sageRevision}`
        ]
      }
    },
    {
      when: legacySageWhen("platform === 'linux' && gpu === 'nvidia'"),
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "python scripts/install_h3_sageattention.py"
      }
    }
    ]
  }
}
