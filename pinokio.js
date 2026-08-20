const path = require('path')
const fs = require('fs')
const http = require('http')

const continuumVersion = fs.readFileSync(path.join(__dirname, 'CONTINUUM_VERSION'), 'utf8').trim()
const maestroBaseVersion = fs.readFileSync(path.join(__dirname, 'VERSION'), 'utf8').trim()
const DIRECT_HEALTH_TIMEOUT_MS = 500

const directLoopbackOrigin = (rawUrl) => {
  if (typeof rawUrl !== 'string' || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}\/?$/.test(rawUrl)) {
    return undefined
  }
  try {
    const parsed = new URL(rawUrl)
    if (
      parsed.protocol !== 'http:' ||
      parsed.hostname !== '127.0.0.1' ||
      !parsed.port ||
      parsed.username ||
      parsed.password ||
      parsed.pathname !== '/' ||
      parsed.search ||
      parsed.hash
    ) {
      return undefined
    }
    return parsed.origin
  } catch (_error) {
    return undefined
  }
}

const probeDirectHealth = (rawUrl) => new Promise((resolve) => {
  const origin = directLoopbackOrigin(rawUrl)
  if (!origin) {
    resolve(false)
    return
  }

  let settled = false
  const finish = (result) => {
    if (settled) return
    settled = true
    resolve(result)
  }
  try {
    const request = http.get(`${origin}/health`, {
      agent: false,
      headers: { Accept: 'application/json' },
    }, (response) => {
      response.resume()
      finish(response.statusCode === 200)
    })
    request.setTimeout(DIRECT_HEALTH_TIMEOUT_MS, () => {
      request.destroy()
      finish(false)
    })
    request.once('error', () => finish(false))
  } catch (_error) {
    finish(false)
  }
})

module.exports = {
  version: "8.0",
  title: "Maestro // Continuum",
  description: `Maestro Continuum v${continuumVersion} · Built on Maestro ${maestroBaseVersion}. An all-in-one, 100% local AI video, image & music studio. Its Director mode turns a single prompt into a full music video or short film — LLM-planned, shot by shot. Built on the WanGP pipeline (Wan 2.1/2.2, LTX-2.3, Qwen, Hunyuan Video, Flux). Requires an NVIDIA GPU (6GB+ VRAM).`,
  icon: "maestro_simplified_icon_alpha.png",
  menu: async (kernel, info) => {
    // Do not gate this menu on kernel.gpu. Pinokio can render an app menu
    // before its hardware inventory has populated that property, which would
    // hide Start from supported systems. install.js retains the documented
    // execution-time NVIDIA check for fresh installations.
    let installed = info.exists("app/env")
    let running = {
      install: info.running("install.js"),
      restart: info.running("restart.js"),
      start: info.running("start.js"),
      start_classic: info.running("start_classic.js"),
      update: info.running("update.js"),
      reset: info.running("reset.js")
    }
    if (running.install) {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Installing",
        href: "install.js",
      }]
    } else if (installed) {
      if (running.restart) {
        return [{
          icon: "fa-solid fa-rotate",
          text: "Restarting Maestro",
          href: "restart.js",
        }]
      } else if (running.start) {
        let local = info.local("start.js")
        if (local && local.url) {
          const backendAlive = local.backend_ready === true && await probeDirectHealth(local.url)
          const stable = local.share_kind === "stable" ? local.share_url : undefined
          const capturedQuick = local.$share && local.$share.cloudflare
            ? local.$share.cloudflare[local.url]
            : undefined
          const quick = local.quick_share_url || capturedQuick || (local.share_kind === "quick" ? local.share_url : undefined)
          const remoteMenu = []
          if (stable) {
            remoteMenu.push({
              icon: "fa-brands fa-cloudflare",
              text: `<div><strong>Open / copy Cloudflare stable URL</strong><div>${stable}</div></div>`,
              href: stable,
            })
          }
          if (quick && quick !== stable) {
            remoteMenu.push({
              icon: "fa-brands fa-cloudflare",
              text: `<div><strong>Open / copy direct Quick Tunnel URL</strong><div>${quick}</div><div>Bypasses the Worker proxy hop; use if the Worker quota or stable route is unavailable.</div></div>`,
              href: quick,
            })
          }
          if (!backendAlive) {
            return [...remoteMenu, {
              icon: 'fa-solid fa-triangle-exclamation',
              text: "Backend unavailable or still recovering · Open terminal",
              href: "start.js",
            }, {
              icon: "fa-solid fa-rotate",
              text: "Restart Maestro",
              href: "restart.js",
            }]
          }
          let menu = [{
            default: true,
            icon: "fa-solid fa-rocket",
            text: "Open Web UI",
            href: local.url,
          }, {
            icon: "fa-solid fa-rocket",
            text: "Open Classic UI",
            href: local.url + "/classic",
          }, {
            icon: 'fa-solid fa-terminal',
            text: `Terminal · ${local.sharing || "Localhost only"}`,
            href: "start.js",
          }]
          if (remoteMenu.length) {
            menu.splice(1, 0, ...remoteMenu)
          } else if (local.sharing && local.sharing.includes("Cloudflare")) {
            menu.splice(1, 0, {
              icon: "fa-brands fa-cloudflare",
              text: "Cloudflare tunnel is starting…",
              href: "start.js",
            })
          }
          if (local.share_kind === "stable") {
            menu.push({
              icon: "fa-solid fa-rotate",
              text: "Restart Maestro",
              href: "restart.js",
            })
          }
          return menu
        } else {
          return [{
            icon: 'fa-solid fa-terminal',
            text: "Terminal",
            href: "start.js",
          }]
        }
      } else if (running.start_classic) {
        let local = info.local("start_classic.js")
        if (local && local.url) {
          return [{
            default: true,
            icon: "fa-solid fa-rocket",
            text: "Open Classic UI",
            href: local.url,
          }, {
            icon: 'fa-solid fa-terminal',
            text: `Terminal · ${local.sharing || "Localhost only"}`,
            href: "start_classic.js",
          }]
        } else {
          return [{
            icon: 'fa-solid fa-terminal',
            text: "Terminal",
            href: "start_classic.js",
          }]
        }
      } else if (running.update) {
        return [{
          default: true,
          icon: 'fa-solid fa-terminal',
          text: "Updating",
          href: "update.js",
        }]
      } else if (running.reset) {
        return [{
          default: true,
          icon: 'fa-solid fa-terminal',
          text: "Resetting",
          href: "reset.js",
        }]
      } else {
        return [{
          icon: "fa-solid fa-power-off",
          text: "<div><strong>Start</strong><div>Cloudflare app sharing is enabled by default; LAN binding stays off. With active accounts, remote visitors sign in and can access only assigned projects. The pre-migration compatibility path uses project passwords. Remote visitors cannot access machine controls. The live share URL appears after launch.</div></div>",
          href: "start.js",
        }, {
          icon: "fa-solid fa-display",
          text: "<div><strong>Start (Classic UI)</strong><div>Classic UI is local-only even while Cloudflare app sharing is enabled.</div></div>",
          href: "start_classic.js",
        }, {
          icon: "fa-solid fa-power-off",
          text: "Advanced",
          menu: [{
            icon: "fa-solid fa-power-off",
            text: "Compiled (Faster but may not work)",
            href: "start.js",
            params: {
              compile: true
            }
          }, {
            icon: "fa-solid fa-power-off",
            text: "Classic Compiled",
            href: "start_classic.js",
            params: {
              compile: true
            }
          }]
        }, {
          icon: "fa-regular fa-folder-open",
          text: "T2V Loras (save lora files here)",
          href: "app/loras",
          fs: true
        }, {
          icon: "fa-regular fa-folder-open",
          text: "I2V Loras (save lora files here)",
          href: "app/loras_i2v",
          fs: true
        }, {
          icon: "fa-solid fa-plug",
          text: "Update",
          href: "update.js",
        }, {
          icon: "fa-solid fa-plug",
          text: "Install",
          href: "install.js",
        }, {
          // Install / re-install the SAM 3.1 segmentation service
          // (separate Python 3.12 conda env, takes ~5 min). Only
          // needed for the experimental Inpaint feature in Edit
          // mode — most users never need it, which is why install.js
          // no longer runs sam_install.js automatically. Label flips
          // to "Update Inpaint Support" once installed so users can
          // refresh SAM independently of the main app update.
          icon: "fa-solid fa-vector-square",
          text: info.exists("app/services/sam/env")
            ? "Update Inpaint Support (SAM 3.1)"
            : "Install Inpaint Support (SAM 3.1)",
          href: "sam_install.js",
        }, {
          icon: "fa-solid fa-cube",
          text: info.exists("app/services/blender_mcp/mcp/blmcp/__init__.py")
            ? "Verify / Repair Blender MCP Support"
            : "Install Blender MCP Support",
          href: "blender_mcp_install.js",
        }, {
          icon: "fa-solid fa-clapperboard",
          text: info.exists("app/tools/blender/runtime.json")
            ? "Verify / Repair Blender Runtime"
            : "Install Blender Runtime",
          href: "blender_runtime_install.js",
        }, {
          icon: "fa-regular fa-circle-xmark",
          text: "<div><strong>Reset</strong><div>Revert to pre-install state</div></div>",
          href: "reset.js",
          confirm: "Are you sure you wish to reset the app?"
        }]
      }
    } else {
      return [{
        default: true,
        icon: "fa-solid fa-plug",
        text: "Install",
        href: "install.js",
      }]
    }
  }
}
