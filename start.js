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
      }
    ]
  }
}
