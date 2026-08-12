const crypto = require("crypto")
const { shareHelperSecretEnv } = require("./launcher_secret_env")

module.exports = async () => {
  const generation = crypto.randomBytes(24).toString("hex")
  return {
    run: [{
      method: "shell.run",
      params: {
        env: shareHelperSecretEnv,
        venv: "env",
        path: "app",
        message: [
          `python scripts/restart_status.py set --state restarting --reason restart --message "Maestro is restarting. Please try again shortly." --ttl-seconds 900 --generation ${generation}`
        ],
        on: [{
          event: "/MAESTRO_RESTART_STATUS_SET restarting/",
          kill: true
        }]
      }
    }, {
      method: "script.restart",
      params: {
        uri: "start.js",
        params: {
          restart_generation: generation
        }
      }
    }]
  }
}
