const { runtimeSecretEnv } = require("./launcher_secret_env")

module.exports = async (kernel) => {
  let port = await kernel.port()
  // Keep the standalone classic surface aligned with the canonical server:
  // loopback by default, and 0.0.0.0 only after explicit LAN opt-in. The
  // per-app ENVIRONMENT is available through Pinokio's script template here.
  return {
    requires: {
      bundle: "ai",
    },
    daemon: true,
    run: [
      {
        method: "shell.run",
        params: {
          venv: "env",
          env: {
            ...runtimeSecretEnv,
            SERVER_PORT: port,
            SERVER_NAME: "{{env.PINOKIO_SHARE_LOCAL === 'true' ? '0.0.0.0' : '127.0.0.1'}}"
          },
          path: "app",
          message: [
            "python wgp.py --multiple-images {{args.compile ? '--compile' : ''}}"
          ],
          on: [{
            "event": "/(http:\/\/[0-9.:]+)/",
            "done": true
          }]
        }
      },
      {
        method: "local.set",
        params: {
          url: "{{input.event[1]}}",
          sharing: "{{env.PINOKIO_SHARE_CLOUDFLARE === 'true' ? 'Cloudflare sharing enabled' : (env.PINOKIO_SHARE_LOCAL === 'true' ? 'LAN sharing enabled' : 'Localhost only')}}"
        }
      }
    ]
  }
}
