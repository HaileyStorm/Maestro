const crypto = require("crypto")
const { runtimeSecretEnv, shareHelperSecretEnv } = require("./launcher_secret_env")

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
      method: "shell.run",
      params: {
        env: runtimeSecretEnv,
        venv: "env",
        path: "app",
        message: "python scripts/pinokio_restart_stop.py",
        on: [{
          event: "/MAESTRO_OLD_BACKEND_STOPPED/",
          kill: true
        }, {
          event: "/MAESTRO_OLD_BACKEND_STOP_FAILED/",
          break: true
        }]
      }
    }, {
      method: "script.start",
      params: {
        uri: "start.js",
        params: {
          restart_generation: generation
        }
      }
    }]
  }
}
