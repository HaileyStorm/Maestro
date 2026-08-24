// Official Blender Lab MCP support. The checkout is pinned and attested again
// by Maestro before every connection; caller-provided Python is never exposed.
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
        message: "python -m services.blender_mcp_service provision-mcp --destination services/blender_mcp"
      }
    },
    {
      when: "{{!exists('app/services/blender_mcp/mcp/blmcp/__init__.py')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: "git clone --depth 1 --branch v1.0.0 https://projects.blender.org/lab/blender_mcp.git services/blender_mcp"
      }
    },
    {
      when: "{{exists('app/services/blender_mcp/mcp/blmcp/__init__.py') && !exists('app/services/blender_mcp/.maestro-attested')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: selectedVenv,
        venv_python: selectedPython,
        path: "app",
        message: [
          "git -C services/blender_mcp remote set-url origin https://projects.blender.org/lab/blender_mcp.git",
          "git -C services/blender_mcp fetch origin tag v1.0.0 --depth 1",
          "git -C services/blender_mcp checkout --detach 03004fd0216bfe5e0a3d9ac9b47d5efadc3d78c4"
        ]
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
          "python -m services.blender_mcp_service attest-mcp --checkout services/blender_mcp",
          'uv pip install "mcp[cli]==1.12.4" services/blender_mcp/mcp'
        ]
      }
    }
    ]
  }
}
