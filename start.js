const fs = require("fs")
const path = require("path")
const {
  isRtx50,
  needsCuda13DriverUpdate,
  legacyRuntimeProfile,
  runtimeProfile,
} = require("./launcher_profile")
const { runtimeSecretEnv } = require("./launcher_secret_env")

const parseEnvironmentValue = (rawValue) => {
  let quote = ""
  let escaped = false
  let commentIndex = -1
  for (let index = 0; index < rawValue.length; index += 1) {
    const character = rawValue[index]
    if (escaped) {
      escaped = false
      continue
    }
    if (quote === '"' && character === "\\") {
      escaped = true
      continue
    }
    if (quote) {
      if (character === quote) quote = ""
      continue
    }
    if (character === '"' || character === "'") {
      quote = character
      continue
    }
    if (
      character === "#" &&
      (index === 0 || /\s/.test(rawValue[index - 1]))
    ) {
      commentIndex = index
      break
    }
  }

  let value = (commentIndex >= 0 ? rawValue.slice(0, commentIndex) : rawValue).trim()
  if (
    value.length >= 2 &&
    ((value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'")))
  ) {
    value = value.slice(1, -1)
  }
  return value
}

const readAppEnvironment = () => {
  let contents
  try {
    contents = fs.readFileSync(path.resolve(__dirname, "ENVIRONMENT"), "utf8")
      .replace(/^\uFEFF/, "")
  } catch (error) {
    if (error && error.code === "ENOENT") return {}
    throw error
  }

  const values = {}
  for (const line of contents.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/)
    if (!match) continue
    values[match[1]] = parseEnvironmentValue(match[2])
  }
  return values
}

module.exports = async (kernel) => {
  const runtime = runtimeProfile(kernel)
  const legacyRuntime = legacyRuntimeProfile(kernel)
  const hasRecoveryRuntime = runtime.env !== legacyRuntime.env
  const selectedEnv = hasRecoveryRuntime
    ? `{{exists('${runtime.marker}') ? '${runtime.env}' : '${legacyRuntime.env}'}}`
    : runtime.env
  const selectedPython = hasRecoveryRuntime
    ? `{{exists('${runtime.marker}') ? '${runtime.python}' : '${legacyRuntime.python}'}}`
    : runtime.python
  const runtimeGuard = isRtx50(kernel) && needsCuda13DriverUpdate(kernel) ? [{
      method: "input",
      params: {
        title: "NVIDIA driver update required",
        description: `RTX 50 requires NVIDIA driver 580 or newer for Maestro's CUDA 13 runtime (found ${kernel.gpu_driver}). Update the driver, then run Update before starting Maestro.`
      },
      next: null
    }] : []
  const recoveryGuard = hasRecoveryRuntime ? [{
    when: `{{!exists('${runtime.marker}') && !exists('${legacyRuntime.marker}')}}`,
    method: "input",
    params: {
      title: "Maestro runtime update required",
      description: "Neither the preferred H3 runtime nor the preserved compatibility runtime is ready. Run Update, then start Maestro again."
    },
    next: null
  }] : []
  const appEnvironment = readAppEnvironment()
  const effectiveEnvironmentValue = (key) => (
    Object.prototype.hasOwnProperty.call(appEnvironment, key)
      ? appEnvironment[key]
      : String((kernel.envs && kernel.envs[key]) || "")
  )
  const backendEnvironment = {
    MAESTRO_ACCOUNTS_ENABLED: effectiveEnvironmentValue("MAESTRO_ACCOUNTS_ENABLED"),
    MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED: effectiveEnvironmentValue("MAESTRO_ACCOUNT_BOOTSTRAP_ENABLED"),
    MAESTRO_PUBLIC_REGISTRATION_ENABLED: effectiveEnvironmentValue("MAESTRO_PUBLIC_REGISTRATION_ENABLED"),
    MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED: effectiveEnvironmentValue("MAESTRO_HOSTED_CREDIT_ENFORCEMENT_ENABLED"),
    MAESTRO_COMPUTE_EXECUTION_REALM: effectiveEnvironmentValue("MAESTRO_COMPUTE_EXECUTION_REALM"),
    PINOKIO_SHARE_CLOUDFLARE: effectiveEnvironmentValue("PINOKIO_SHARE_CLOUDFLARE"),
    PINOKIO_SHARE_LOCAL: effectiveEnvironmentValue("PINOKIO_SHARE_LOCAL"),
    PINOKIO_STABLE_SHARE_URL: effectiveEnvironmentValue("PINOKIO_STABLE_SHARE_URL"),
  }
  const effectiveStableShareUpdateSecret = effectiveEnvironmentValue(
    "PINOKIO_STABLE_SHARE_UPDATE_SECRET",
  ).trim()
  const cloudflareEnabled = backendEnvironment.PINOKIO_SHARE_CLOUDFLARE
    .trim().toLowerCase() === "true"
  const localSharingEnabled = backendEnvironment.PINOKIO_SHARE_LOCAL
    .trim().toLowerCase() === "true"
  const stableShareConfigured = Boolean(
    backendEnvironment.PINOKIO_STABLE_SHARE_URL.trim()
  )
  // SERVER_NAME is intentionally NOT set here. The host-binding
  // decision lives in launch.py, which reads PINOKIO_SHARE_LOCAL from
  // the freshly picked backend environment below. See launch.py bottom
  // for the full priority chain.
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
          raw: "The preferred H3 acceleration runtime is not ready; starting the preserved compatibility runtime. Run Update to finish the automatic migration.",
        },
      }] : []),
      // SAM service starts on demand (launched by the backend when inpaint is used)
      // — not started here to avoid holding a CUDA context that wastes VRAM
      {
        when: "{{exists('app/tools/blender/runtime.json')}}",
        method: "shell.run",
        params: {
          env: runtimeSecretEnv,
          venv: selectedEnv,
          venv_python: selectedPython,
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
      },
      {
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
          env: {
            ...backendEnvironment,
            ...runtimeSecretEnv,
            SERVER_PORT: "{{port}}",
          },
          path: "app",
          message: [
            "python launch.py {{args.compile ? '--compile' : ''}}"
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
          backend_ready: false,
          stable_share_configured: stableShareConfigured,
          quick_share_url: "",
          share_poll_attempt: 0,
          share_rotation_published: false,
          share_rotation_pending: false,
          share_tunnel_missing: false,
          share_url: "",
          sharing: cloudflareEnabled
            ? (stableShareConfigured
              ? "Cloudflare tunnel is starting… (stable redirect configured)"
              : "Cloudflare tunnel is starting…")
            : (localSharingEnabled ? "LAN sharing enabled" : "Localhost only")
        }
      },
      {
        method: "shell.run",
        params: {
          env: runtimeSecretEnv,
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          message: [
            "python scripts/share_registration_watch.py --origin {{local.url}} --wait-backend-only"
          ],
          on: [{
            "event": "/MAESTRO_BACKEND_READY/",
            "kill": true
          }, {
            "event": "/MAESTRO_BACKEND_WAIT_FAILED ([a-z_]+)/",
            "break": true
          }]
        }
      },
      {
        method: "local.set",
        params: {
          backend_ready: true
        }
      },
      {
        id: "wait-for-cloudflare",
        when: cloudflareEnabled
          ? "{{!(local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url])}}"
          : false,
        method: "shell.run",
        params: {
          env: runtimeSecretEnv,
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          message: [
            "python -c \"import time; time.sleep(1)\""
          ]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{!(local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url])}}"
          : false,
        method: "local.set",
        params: {
          share_poll_attempt: "{{local.share_poll_attempt + 1}}",
          sharing: "Cloudflare tunnel is starting… (attempt {{local.share_poll_attempt + 1}})"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{!(local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url])}}"
          : false,
        method: "jump",
        params: {
          id: "wait-for-cloudflare"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url]}}"
          : false,
        method: "local.set",
        params: {
          quick_share_url: "{{local.$share.cloudflare[local.url]}}",
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.quick_share_url}}"
          : false,
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          env: runtimeSecretEnv,
          message: [
            "python scripts/quick_tunnel_supervisor.py --origin {{local.url}} --publish-url {{local.quick_share_url}}"
          ],
          on: [{
            "event": "/MAESTRO_QUICK_TUNNEL_READY (https:\/\/[^ ]+)/",
            "kill": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url]}}"
          : false,
        method: "log",
        params: {
          text: "Cloudflare quick tunnel captured: {{local.$share.cloudflare[local.url]}}"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.quick_share_url}}"
          : false,
        method: "log",
        params: {
          text: "Registering Cloudflare share URL from quick tunnel: {{local.quick_share_url}}"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url]}}"
          : false,
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          env: {
            ...runtimeSecretEnv,
            PINOKIO_STABLE_SHARE_UPDATE_SECRET: effectiveStableShareUpdateSecret || runtimeSecretEnv.PINOKIO_STABLE_SHARE_UPDATE_SECRET,
            MAESTRO_LOCAL_ORIGIN: "{{local.url}}",
            PINOKIO_STABLE_SHARE_URL: backendEnvironment.PINOKIO_STABLE_SHARE_URL,
          },
          message: [
            "python scripts/register_share_url.py --watch"
          ],
          on: [{
            "event": "/MAESTRO_SHARE_READY (https:\/\/[^ ]+) (stable|quick)/",
            "done": true
          }, {
            "event": "/MAESTRO_SHARE_WATCH_ALREADY_RUNNING/",
            "done": true
          }, {
            "event": "/MAESTRO_SHARE_WATCH_FAILED ([a-z_]+)/",
            "break": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{input.event && input.event[1] && input.event[2]}}"
          : false,
        method: "local.set",
        params: {
          share_url: "{{input.event[1]}}",
          share_kind: "{{input.event[2]}}",
          sharing: "Cloudflare {{input.event[2]}}: {{input.event[1]}}"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_kind === 'quick' || local.share_kind === 'stable'}}"
          : false,
        method: "log",
        params: {
          text: "Cloudflare share registration completed with {{local.share_kind}} URL {{local.share_url}}."
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind === 'stable'}}",
        method: "process.wait",
        params: {
          url: "{{local.url}}/health"
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind === 'stable'}}",
        method: "process.wait",
        params: {
          url: "{{local.url}}/ready"
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind === 'stable'}}",
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          env: {
            ...runtimeSecretEnv,
            PINOKIO_STABLE_SHARE_UPDATE_SECRET: effectiveStableShareUpdateSecret || runtimeSecretEnv.PINOKIO_STABLE_SHARE_UPDATE_SECRET,
          },
          message: [
            "python scripts/restart_status.py clear --generation {{args.restart_generation}}"
          ],
          on: [{
            event: "/(MAESTRO_RESTART_STATUS_CLEARED|MAESTRO_RESTART_STATUS_NOT_CLEARED|Maestro restart-status request failed)/",
            kill: true
          }]
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind === 'stable'}}",
        method: "local.set",
        params: {
          restart_status_clear_result: "{{input.event[1]}}"
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind === 'stable'}}",
        method: "log",
        params: {
          text: "{{local.restart_status_clear_result === 'MAESTRO_RESTART_STATUS_CLEARED' ? 'MAESTRO_RESTART_STATUS_CLEARED after direct local health and recovery-readiness verification.' : (local.restart_status_clear_result === 'MAESTRO_RESTART_STATUS_NOT_CLEARED' ? 'MAESTRO_RESTART_STATUS_RETAINED because no matching generation was active; any existing status remains unchanged.' : 'MAESTRO_RESTART_STATUS_CLEAR_FAILED; the published status remains truthful and will expire automatically.')}}"
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind !== 'stable'}}",
        method: "log",
        params: {
          text: "MAESTRO_RESTART_STATUS_RETAINED because this launch did not verify the stable route; any matching published status will expire automatically."
        }
      },
      {
        id: "monitor-cloudflare-share",
        when: cloudflareEnabled,
        method: "process.wait",
        params: {
          sec: 2
        }
      },
      {
        when: cloudflareEnabled,
        method: "local.set",
        params: {
          observed_quick_share_url: "{{local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url] ? local.$share.cloudflare[local.url] : ''}}",
          share_rotation_published: false,
          share_rotation_pending: "{{!!(local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url] && local.$share.cloudflare[local.url] !== local.quick_share_url)}}",
          share_tunnel_missing: "{{!!(local.quick_share_url && !(local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url]))}}"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_tunnel_missing}}"
          : false,
        method: "shell.run",
        params: {
          env: runtimeSecretEnv,
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          message: [
            "python scripts/quick_tunnel_supervisor.py --origin {{local.url}} --clear-url"
          ],
          on: [{
            "event": "/MAESTRO_QUICK_TUNNEL_CLEARED/",
            "kill": true
          }, {
            "event": "/MAESTRO_QUICK_TUNNEL_FAILED ([a-z_]+)/",
            "kill": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_tunnel_missing && input.event && input.event[0] === 'MAESTRO_QUICK_TUNNEL_CLEARED'}}"
          : false,
        method: "local.set",
        params: {
          quick_share_url: "",
          share_url: "",
          share_kind: "",
          share_tunnel_missing: false,
          sharing: "Cloudflare tunnel is reconnecting…"
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_rotation_pending}}"
          : false,
        method: "shell.run",
        params: {
          env: runtimeSecretEnv,
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          message: [
            "python scripts/quick_tunnel_supervisor.py --origin {{local.url}} --publish-url {{local.observed_quick_share_url}}"
          ],
          on: [{
            "event": "/MAESTRO_QUICK_TUNNEL_READY (https:\/\/[^ ]+)/",
            "kill": true
          }, {
            "event": "/MAESTRO_QUICK_TUNNEL_FAILED ([a-z_]+)/",
            "kill": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_rotation_pending && input.event && input.event[1] === local.observed_quick_share_url}}"
          : false,
        method: "local.set",
        params: {
          share_rotation_published: true
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_rotation_pending && local.share_rotation_published}}"
          : false,
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          env: {
            ...runtimeSecretEnv,
            PINOKIO_STABLE_SHARE_UPDATE_SECRET: effectiveStableShareUpdateSecret || runtimeSecretEnv.PINOKIO_STABLE_SHARE_UPDATE_SECRET,
            MAESTRO_LOCAL_ORIGIN: "{{local.url}}",
            PINOKIO_STABLE_SHARE_URL: backendEnvironment.PINOKIO_STABLE_SHARE_URL,
          },
          message: [
            "python scripts/register_share_url.py --watch"
          ],
          on: [{
            "event": "/MAESTRO_SHARE_READY (https:\/\/[^ ]+) (stable|quick)/",
            "done": true
          }, {
            "event": "/MAESTRO_SHARE_WATCH_ALREADY_RUNNING/",
            "done": true
          }, {
            "event": "/MAESTRO_SHARE_WATCH_FAILED ([a-z_]+)/",
            "kill": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_rotation_pending && local.share_rotation_published}}"
          : false,
        method: "shell.run",
        params: {
          venv: selectedEnv,
          venv_python: selectedPython,
          path: "app",
          env: {
            ...runtimeSecretEnv,
            MAESTRO_LOCAL_ORIGIN: "{{local.url}}",
          },
          message: [
            "python scripts/register_share_url.py --wait-watch {{local.observed_quick_share_url}}"
          ],
          on: [{
            "event": "/MAESTRO_SHARE_READY (https:\/\/[^ ]+) (stable|quick)/",
            "kill": true
          }, {
            "event": "/MAESTRO_SHARE_WATCH_FAILED registration_timeout/",
            "kill": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.share_rotation_pending && input.event && input.event[1] && input.event[2]}}"
          : false,
        method: "local.set",
        params: {
          quick_share_url: "{{local.observed_quick_share_url}}",
          share_url: "{{input.event[1]}}",
          share_kind: "{{input.event[2]}}",
          share_rotation_published: false,
          share_rotation_pending: false,
          sharing: "Cloudflare {{input.event[2]}}: {{input.event[1]}}"
        }
      },
      {
        when: cloudflareEnabled,
        method: "jump",
        params: {
          id: "monitor-cloudflare-share"
        }
      }
    ]
  }
}
