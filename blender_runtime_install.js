// Official Blender 5.1.2 portable runtime, pinned by Blender Foundation SHA-256.
const { runtimeSecretEnv } = require("./launcher_secret_env")
const { runtimeProfile } = require("./launcher_profile")

module.exports = async (kernel) => {
  const runtime = runtimeProfile(kernel)
  const selectedVenv = `{{args && args.venv ? args.venv : '${runtime.env}'}}`
  const selectedPython = `{{args && args.venv_python ? args.venv_python : '${runtime.python}'}}`
  return {
    run: [
    {
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "python -m services.blender_mcp_service provision-runtime --checkout services/blender_mcp --marker tools/blender/runtime.json"
      }
    },
    {
      when: "{{platform === 'linux' && !exists('app/tools/blender/runtime.json')}}",
      method: "fs.download",
      params: {
        uri: "https://download.blender.org/release/Blender5.1/blender-5.1.2-linux-x64.tar.xz",
        path: "app/tools/blender/downloads/blender-5.1.2-linux-x64.tar.xz"
      }
    },
    {
      when: "{{platform === 'linux' && !exists('app/tools/blender/runtime.json')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "python scripts/install_blender_runtime.py --archive tools/blender/downloads/blender-5.1.2-linux-x64.tar.xz --sha256 aaccb355f50183979b698bcce7467103a76261b5fa59f4972295842662a285fb --checkout services/blender_mcp --version 5.1.2"
      }
    },
    {
      when: "{{platform === 'win32' && !exists('app/tools/blender/runtime.json')}}",
      method: "fs.download",
      params: {
        uri: "https://download.blender.org/release/Blender5.1/blender-5.1.2-windows-x64.zip",
        path: "app/tools/blender/downloads/blender-5.1.2-windows-x64.zip"
      }
    },
    {
      when: "{{platform === 'win32' && !exists('app/tools/blender/runtime.json')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "python scripts/install_blender_runtime.py --archive tools/blender/downloads/blender-5.1.2-windows-x64.zip --sha256 345bedea7b0acf7cc9666423d8553f9129622aea34ded65c23e8cb70f83f14ff --checkout services/blender_mcp --version 5.1.2"
      }
    }
    ]
  }
}
