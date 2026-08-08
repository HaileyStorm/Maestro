// Optional MiniMax H3 acceleration companions. Profile/default policy remains
// in app code; this script only provisions pinned, capability-gated runtimes.
const revision = "0e334dc981cfe3b0ed926ee13ad43f64914b7f5b"
const sageRevision = "eb615cf6cf4d221338033340ee2de1c37fbdba4a"
const { runtimeSecretEnv } = require("./launcher_secret_env")

module.exports = {
  run: [
    {
      when: "{{!exists('app/services/sol_attn_kijai/.git')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        message: "git clone https://github.com/Kijai/ComfyUI-SolAttn_triton.git app/services/sol_attn_kijai"
      }
    },
    {
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        message: [
          "git -C app/services/sol_attn_kijai fetch origin",
          `git -C app/services/sol_attn_kijai checkout --detach ${revision}`
        ]
      }
    },
    {
      when: "{{platform === 'linux' && gpu === 'nvidia' && !exists('app/services/sageattention_thu_ml/.git')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        message: "git clone --branch v2.2.0 https://github.com/thu-ml/SageAttention.git app/services/sageattention_thu_ml"
      }
    },
    {
      when: "{{platform === 'linux' && gpu === 'nvidia'}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        message: [
          "git -C app/services/sageattention_thu_ml fetch origin tag v2.2.0",
          `git -C app/services/sageattention_thu_ml checkout --detach ${sageRevision}`
        ]
      }
    },
    {
      when: "{{platform === 'linux' && gpu === 'nvidia'}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: "env",
        path: "app",
        message: "python scripts/install_h3_sageattention.py"
      }
    }
  ]
}
