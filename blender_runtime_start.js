const { runtimeSecretEnv } = require("./launcher_secret_env")

module.exports = {
  daemon: true,
  run: [
    {
      when: "{{exists('app/tools/blender/runtime.json')}}",
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: "env",
        path: "app",
        message: [
          "python -m services.blender_mcp_service attest-runtime --marker tools/blender/runtime.json",
          "python scripts/start_blender_bridge.py"
        ],
        on: [{
          event: "/(MCP server started on 127\\.0\\.0\\.1:9876|Blender bridge already ready at 127\\.0\\.0\\.1:9876)/",
          done: true
        }]
      }
    }
  ]
}
