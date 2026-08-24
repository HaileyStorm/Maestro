const { runtimeSecretEnv } = require("./launcher_secret_env")
const {
  isRtx50,
  legacyRuntimeProfile,
  needsCuda13DriverUpdate,
  runtimeProfile,
} = require("./launcher_profile")

module.exports = async (kernel) => {
  let port = await kernel.port()
  const runtime = runtimeProfile(kernel)
  const legacyRuntime = legacyRuntimeProfile(kernel)
  const hasRecoveryRuntime = runtime.env !== legacyRuntime.env
  const selectedEnv = hasRecoveryRuntime
    ? `{{exists('${runtime.marker}') ? '${runtime.env}' : '${legacyRuntime.env}'}}`
    : runtime.env
  const selectedPython = hasRecoveryRuntime
    ? `{{exists('${runtime.marker}') ? '${runtime.python}' : '${legacyRuntime.python}'}}`
    : runtime.python
  const runtimeGuard = isRtx50(kernel)
    ? (needsCuda13DriverUpdate(kernel) ? [{
      method: "input",
      params: {
        title: "NVIDIA driver update required",
        description: `RTX 50 requires NVIDIA driver 580 or newer for Maestro's CUDA 13 runtime (found ${kernel.gpu_driver}). Update the driver, then run Update before starting Maestro.`
      },
      next: null
    }] : [{
      when: `{{!exists('${runtime.marker}')}}`,
      method: "input",
      params: {
        title: "RTX 50 runtime upgrade required",
        description: "Run Update once to install Maestro's Python 3.11 / CUDA 13 acceleration environment, then start Maestro again. Your existing environment is preserved."
      },
      next: null
    }])
    : []
  const recoveryGuard = hasRecoveryRuntime ? [{
    when: `{{!exists('${runtime.marker}') && !exists('${legacyRuntime.marker}')}}`,
    method: "input",
    params: {
      title: "Maestro runtime update required",
      description: "Neither the preferred H3 runtime nor the preserved compatibility runtime is ready. Run Update, then start Maestro again."
    },
    next: null
  }] : []
  // Keep the standalone classic surface aligned with the canonical server:
  // loopback by default, and 0.0.0.0 only after explicit LAN opt-in. The
  // per-app ENVIRONMENT is available through Pinokio's script template here.
  return {
    requires: {
      bundle: "ai",
    },
    daemon: true,
    run: [
      ...runtimeGuard,
      ...recoveryGuard,
      ...(hasRecoveryRuntime ? [{
        when: `{{!exists('${runtime.marker}') && exists('${legacyRuntime.marker}')}}`,
        method: "log",
        params: {
          raw: "The preferred H3 acceleration runtime is not ready; starting the preserved compatibility runtime. Run Update to finish the automatic migration."
        }
      }] : []),
      {
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
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
