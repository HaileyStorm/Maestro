const fs = require("fs")
const path = require("path")
const { runtimeSecretEnv, shareHelperSecretEnv } = require("./launcher_secret_env")

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
    let value = match[2]
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1)
    } else {
      value = value.replace(/\s+#.*$/, "").trim()
    }
    values[match[1]] = value
  }
  return values
}

module.exports = async (kernel) => {
  let port = await kernel.port()
  const appEnvironment = readAppEnvironment()
  const effectiveEnvironmentValue = (key) => (
    Object.prototype.hasOwnProperty.call(appEnvironment, key)
      ? appEnvironment[key]
      : String((kernel.envs && kernel.envs[key]) || "")
  )
  const cloudflareEnabled = effectiveEnvironmentValue("PINOKIO_SHARE_CLOUDFLARE")
    .trim().toLowerCase() === "true"
  const localSharingEnabled = effectiveEnvironmentValue("PINOKIO_SHARE_LOCAL")
    .trim().toLowerCase() === "true"
  const stableShareConfigured = Boolean(
    effectiveEnvironmentValue("PINOKIO_STABLE_SHARE_URL").trim()
  )
  // SERVER_NAME is intentionally NOT set here. The host-binding
  // decision lives in launch.py, which reads PINOKIO_SHARE_LOCAL
  // from the merged shell env (per-app ENVIRONMENT overrides global
  // there). kernel.envs in this start.js context only exposes the
  // global ENVIRONMENT, so a per-app override of PINOKIO_SHARE_LOCAL
  // wouldn't be visible if we made the decision here. See launch.py
  // bottom for the full priority chain.
  return {
    requires: {
      bundle: "ai",
    },
    daemon: true,
    run: [
      // SAM service starts on demand (launched by the backend when inpaint is used)
      // — not started here to avoid holding a CUDA context that wastes VRAM
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
      },
      {
        method: "shell.run",
        params: {
          venv: "env",
          env: {
            ...runtimeSecretEnv,
            SERVER_PORT: port,
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
          stable_share_configured: stableShareConfigured,
          share_poll_attempt: 0,
          share_url: "",
          sharing: cloudflareEnabled
            ? (stableShareConfigured
              ? "Cloudflare tunnel is starting… (stable redirect configured)"
              : "Cloudflare tunnel is starting…")
            : (localSharingEnabled ? "LAN sharing enabled" : "Localhost only")
        }
      },
      {
        method: "process.wait",
        params: {
          url: "{{local.url}}/health"
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
          venv: "env",
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
        method: "shell.run",
        params: {
          venv: "env",
          path: "app",
          env: {
            ...shareHelperSecretEnv,
            MAESTRO_LOCAL_ORIGIN: "{{local.url}}",
            MAESTRO_QUICK_SHARE_URL: "{{local.$share.cloudflare[local.url]}}"
          },
          message: [
            "python scripts/register_share_url.py"
          ],
          on: [{
            "event": "/MAESTRO_SHARE_READY (https:\/\/[^ ]+) (stable|quick)/",
            "kill": true
          }]
        }
      },
      {
        when: cloudflareEnabled
          ? "{{local.$share && local.$share.cloudflare && local.$share.cloudflare[local.url]}}"
          : false,
        method: "local.set",
        params: {
          share_url: "{{input.event[1]}}",
          share_kind: "{{input.event[2]}}",
          sharing: "Cloudflare {{input.event[2]}}: {{input.event[1]}}"
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
        method: "shell.run",
        params: {
          venv: "env",
          path: "app",
          env: shareHelperSecretEnv,
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
          text: "{{local.restart_status_clear_result === 'MAESTRO_RESTART_STATUS_CLEARED' ? 'MAESTRO_RESTART_STATUS_CLEARED after direct local health verification.' : (local.restart_status_clear_result === 'MAESTRO_RESTART_STATUS_NOT_CLEARED' ? 'MAESTRO_RESTART_STATUS_RETAINED because no matching generation was active; any existing status remains unchanged.' : 'MAESTRO_RESTART_STATUS_CLEAR_FAILED; the published status remains truthful and will expire automatically.')}}"
        }
      },
      {
        when: "{{typeof args.restart_generation === 'string' && /^[A-Za-z0-9_-]{16,64}$/.test(args.restart_generation) && local.share_kind !== 'stable'}}",
        method: "log",
        params: {
          text: "MAESTRO_RESTART_STATUS_RETAINED because this launch did not verify the stable route; any matching published status will expire automatically."
        }
      }
    ]
  }
}
