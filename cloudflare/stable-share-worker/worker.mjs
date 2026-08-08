const TARGET_KEY = "quick-tunnel-origin"
const HEALTH_PATH = "/.well-known/maestro-share/health"
const UPDATE_PATH = "/.well-known/maestro-share/target"
const ORIGIN_HEALTH_PATH = "/health"
const HEALTH_CACHE_SECONDS = 8

const OFFLINE_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maestro is offline</title><style>
:root{color-scheme:dark;font-family:ui-sans-serif,system-ui,sans-serif}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1017;color:#edf2ff}main{max-width:36rem;margin:2rem;padding:2.5rem;border:1px solid #2c3445;border-radius:1.25rem;background:#151a24;box-shadow:0 1.5rem 5rem #0008}h1{margin:.2rem 0 1rem;font-size:clamp(2rem,7vw,3.4rem)}p{color:#bdc7da;line-height:1.65}.ember{color:#ff9b62;font-size:1.5rem}small{display:block;margin-top:1.5rem;color:#7f8aa1}
</style></head><body><main><div class="ember" aria-hidden="true">◆</div><h1>Maestro is offline</h1><p>The studio or its private tunnel is not available right now. This address is stable, so you can bookmark it and try again after Maestro has started.</p><small>No tracking, sign-in, or content is loaded on this page.</small></main></body></html>`

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  },
})

export const canonicalQuickTunnelOrigin = (value) => {
  if (typeof value !== "string" || value.length > 512) return null
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    return null
  }
  const hostname = parsed.hostname
  const canonical = `https://${hostname}`
  if (
    parsed.protocol !== "https:" ||
    !hostname.endsWith(".trycloudflare.com") ||
    hostname === "trycloudflare.com" ||
    parsed.port ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    value !== canonical
  ) return null
  return canonical
}

const digest = async (value) => new Uint8Array(await crypto.subtle.digest(
  "SHA-256",
  new TextEncoder().encode(value),
))

const authorized = async (request, expectedSecret) => {
  const header = request.headers.get("Authorization") || ""
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : ""
  if (!expectedSecret || !supplied) return false
  const [left, right] = await Promise.all([
    digest(String(expectedSecret)),
    digest(supplied),
  ])
  let mismatch = left.length ^ right.length
  const length = Math.max(left.length, right.length)
  for (let index = 0; index < length; index += 1) {
    mismatch |= (left[index % left.length] ^ right[index % right.length])
  }
  return mismatch === 0
}

const authenticatedHealth = async (request, env) => {
  if (!(await authorized(request, env.UPDATE_TOKEN))) {
    return jsonResponse({ ok: false }, 401)
  }
  const target = canonicalQuickTunnelOrigin(await env.MAESTRO_TARGETS.get(TARGET_KEY))
  return jsonResponse(
    target ? { ok: true, configured: true, target } : { ok: false, configured: false },
    target ? 200 : 503,
  )
}

const updateTarget = async (request, env) => {
  if (!(await authorized(request, env.UPDATE_TOKEN))) {
    return jsonResponse({ ok: false }, 401)
  }
  const contentLength = Number(request.headers.get("Content-Length") || "0")
  if (contentLength > 4096) return jsonResponse({ ok: false }, 413)
  let body
  try {
    body = await request.json()
  } catch {
    return jsonResponse({ ok: false }, 400)
  }
  const target = canonicalQuickTunnelOrigin(body?.target)
  if (!target) return jsonResponse({ ok: false }, 400)
  await env.MAESTRO_TARGETS.put(TARGET_KEY, target)
  return jsonResponse({ ok: true, configured: true, target })
}

const cachedOriginHealth = async (target, env) => {
  const cache = env.__TEST_CACHE || globalThis.caches?.default
  const cacheKey = new Request(
    `https://maestro-origin-health.invalid/check?target=${encodeURIComponent(target)}`,
  )
  if (cache) {
    try {
      const cached = await cache.match(cacheKey)
      if (cached) return (await cached.text()) === "1"
    } catch {}
  }

  let healthy = false
  try {
    const healthFetch = env.__TEST_FETCH || globalThis.fetch
    const response = await healthFetch(target + ORIGIN_HEALTH_PATH, {
      method: "GET",
      redirect: "manual",
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(2000),
    })
    healthy = response.status >= 200 && response.status < 300
    try { await response.body?.cancel() } catch {}
  } catch {
    healthy = false
  }

  if (cache) {
    try {
      await cache.put(cacheKey, new Response(healthy ? "1" : "0", {
        headers: { "Cache-Control": `max-age=${HEALTH_CACHE_SECONDS}` },
      }))
    } catch {}
  }
  return healthy
}

const isBrowserNavigation = (request) => (
  request.method === "GET" && (
    request.headers.get("Sec-Fetch-Mode") === "navigate"
    || (request.headers.get("Accept") || "").toLowerCase().includes("text/html")
  )
)

const offlineResponse = (request) => {
  if (!isBrowserNavigation(request)) {
    return jsonResponse({ ok: false, detail: "Maestro is offline" }, 503)
  }
  return new Response(OFFLINE_HTML, {
    status: 503,
    headers: {
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
      "Content-Type": "text/html; charset=utf-8",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  })
}

export default {
  async fetch(request, env) {
    const incoming = new URL(request.url)
    if (incoming.pathname === HEALTH_PATH) {
      if (request.method !== "GET") return jsonResponse({ ok: false }, 405)
      return authenticatedHealth(request, env)
    }
    if (incoming.pathname === UPDATE_PATH) {
      if (request.method !== "PUT") return jsonResponse({ ok: false }, 405)
      return updateTarget(request, env)
    }

    const target = canonicalQuickTunnelOrigin(await env.MAESTRO_TARGETS.get(TARGET_KEY))
    if (!target || !(await cachedOriginHealth(target, env))) return offlineResponse(request)
    const destination = new URL(target)
    destination.pathname = incoming.pathname
    destination.search = incoming.search
    return new Response(null, {
      status: 307,
      headers: {
        "Cache-Control": "no-store",
        Location: destination.href,
        "Referrer-Policy": "no-referrer",
      },
    })
  },
}

export const paths = { HEALTH_PATH, UPDATE_PATH }
