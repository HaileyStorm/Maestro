// W4A8 support merged after comfy-kitchen 0.2.26 but has not reached PyPI.
// Build a pinned Python/Triton wheel; this deliberately avoids the optional
// native extension so it also works with PyTorch CUDA runtimes below 12.8.
const revision = "b812819a97ac11d01f4a3a16ba47dd38de3b2519"
const { runtimeSecretEnv } = require("./launcher_secret_env")

module.exports = {
  run: [
    {
      when: "{{!exists('app/services/comfy_kitchen_w4a8/.git')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        message: "git clone --filter=blob:none --no-recurse-submodules https://github.com/Comfy-Org/comfy-kitchen.git app/services/comfy_kitchen_w4a8"
      }
    },
    {
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        message: [
          "git -C app/services/comfy_kitchen_w4a8 fetch origin",
          `git -C app/services/comfy_kitchen_w4a8 checkout --detach ${revision}`
        ]
      }
    },
    {
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: "env",
        path: "app",
        message: [
          "uv pip install wheel 'nanobind>=2' 'cmake>=3.26' ninja",
          "python scripts/install_h3_w4a8_runtime.py"
        ]
      }
    }
  ]
}
