const TARGET_KEY = "quick-tunnel-origin"
const STATUS_KEY = "restart-status"
const HEALTH_PATH = "/.well-known/maestro-share/health"
const UPDATE_PATH = "/.well-known/maestro-share/target"
const DIRECT_PATH = "/.well-known/maestro-share/direct"
const STATUS_PATH = "/.well-known/maestro-share/status"
const ORIGIN_HEALTH_PATH = "/health"
const HEALTH_CACHE_SECONDS = 8
const MAX_STATUS_HORIZON_MS = 24 * 60 * 60 * 1000
const STATUS_STATES = new Set([
  "planned",
  "waiting_for_boundary",
  "restarting",
  "verifying",
  "complete",
  "postponed",
  "forced_emergency",
])
const STATUS_REASONS = new Set([
  "restart",
  "maintenance",
  "shutdown",
  "incident",
])
const STATUS_KEYS = [
  "schema_version",
  "generation",
  "state",
  "reason",
  "message",
  "issued_at",
  "expires_at",
  "eta",
]
const GENERATION_PATTERN = /^[A-Za-z0-9_-]{16,64}$/
const UTC_SECONDS_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ$/
const CONTROL_PATTERN = /\p{Cc}/u
const REDIRECT_STATUSES = new Set([300, 301, 302, 303, 307, 308])
const REQUEST_HEADERS_TO_DROP = new Set([
  "cdn-loop",
  "cf-connecting-ip",
  "cf-connecting-ipv6",
  "cf-ew-via",
  "cf-ray",
  "connection",
  "forwarded",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "true-client-ip",
  "upgrade",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-real-ip",
])
const RESPONSE_HEADERS_TO_DROP = new Set([
  "connection",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
])

const OFFLINE_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maestro is offline</title><style>
:root{color-scheme:dark;font-family:ui-sans-serif,system-ui,sans-serif}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1017;color:#edf2ff}main{max-width:36rem;margin:2rem;padding:2.5rem;border:1px solid #2c3445;border-radius:1.25rem;background:#151a24;box-shadow:0 1.5rem 5rem #0008}h1{margin:.2rem 0 1rem;font-size:clamp(2rem,7vw,3.4rem)}p{color:#bdc7da;line-height:1.65}.action{color:#edf2ff}.ember{color:#ff9b62;font-size:1.5rem}small{display:block;margin-top:1.5rem;color:#7f8aa1}
</style></head><body><main><div class="ember" aria-hidden="true">◆</div><h1>Maestro is offline</h1><p>We can’t reach the studio right now. Maestro may be stopped, restarting, or still bringing its private connection online.</p><p class="action">Try this page again in a moment. If you own this studio, open Maestro locally on the studio computer and start it from Pinokio.</p><small>No tracking, sign-in, or content is loaded on this page.</small></main></body></html>`

const jsonResponse = (body, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
  },
})

const exactKeys = (value, expected) => (
  value !== null
  && typeof value === "object"
  && !Array.isArray(value)
  && Object.keys(value).length === expected.length
  && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key))
)

const utcSecond = (value) => {
  if (typeof value !== "string" || !UTC_SECONDS_PATTERN.test(value)) return null
  const milliseconds = Date.parse(value)
  if (!Number.isFinite(milliseconds)) return null
  if (new Date(milliseconds).toISOString().replace(".000Z", "Z") !== value) return null
  return milliseconds
}

const validatedEta = (eta, issuedAt, expiresAt) => {
  if (eta === null) return null
  if (!exactKeys(eta, eta?.kind === "at" ? ["kind", "at"] : ["kind", "earliest", "latest"])) {
    return undefined
  }
  if (eta.kind === "at") {
    const at = utcSecond(eta.at)
    if (at === null || at < issuedAt || at > expiresAt) return undefined
    return { kind: "at", at: eta.at }
  }
  if (eta.kind === "range") {
    const earliest = utcSecond(eta.earliest)
    const latest = utcSecond(eta.latest)
    if (
      earliest === null
      || latest === null
      || earliest < issuedAt
      || latest > expiresAt
      || earliest > latest
    ) return undefined
    return { kind: "range", earliest: eta.earliest, latest: eta.latest }
  }
  return undefined
}

export const validatedRestartStatus = (value) => {
  if (!exactKeys(value, STATUS_KEYS)) return null
  if (value.schema_version !== 1) return null
  if (typeof value.generation !== "string" || !GENERATION_PATTERN.test(value.generation)) {
    return null
  }
  if (!STATUS_STATES.has(value.state) || !STATUS_REASONS.has(value.reason)) return null
  if (
    typeof value.message !== "string"
    || Array.from(value.message).length < 1
    || Array.from(value.message).length > 240
    || CONTROL_PATTERN.test(value.message)
  ) return null
  const issuedAt = utcSecond(value.issued_at)
  const expiresAt = utcSecond(value.expires_at)
  if (
    issuedAt === null
    || expiresAt === null
    || expiresAt <= issuedAt
    || expiresAt - issuedAt > MAX_STATUS_HORIZON_MS
  ) return null
  const eta = validatedEta(value.eta, issuedAt, expiresAt)
  if (eta === undefined) return null
  return {
    schema_version: 1,
    generation: value.generation,
    state: value.state,
    reason: value.reason,
    message: value.message,
    issued_at: value.issued_at,
    expires_at: value.expires_at,
    eta,
  }
}

const readRestartStatus = async (env) => {
  let stored
  try {
    stored = await env.MAESTRO_TARGETS.get(STATUS_KEY)
  } catch {
    return { kind: "error" }
  }
  if (stored === null || stored === undefined) return { kind: "absent" }
  let parsed = stored
  if (typeof stored === "string") {
    try {
      parsed = JSON.parse(stored)
    } catch {
      return { kind: "invalid" }
    }
  }
  const status = validatedRestartStatus(parsed)
  return status ? { kind: "valid", status } : { kind: "invalid" }
}

const readBoundedJson = async (request) => {
  const declaredLength = Number(request.headers.get("Content-Length") || "0")
  if (Number.isFinite(declaredLength) && declaredLength > 4096) return { kind: "large" }
  let text
  try {
    text = await request.text()
  } catch {
    return { kind: "invalid" }
  }
  if (new TextEncoder().encode(text).byteLength > 4096) return { kind: "large" }
  try {
    return { kind: "valid", value: JSON.parse(text) }
  } catch {
    return { kind: "invalid" }
  }
}

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

const restartStatus = async (request, env) => {
  if (!(await authorized(request, env.UPDATE_TOKEN))) {
    return jsonResponse({ ok: false }, 401)
  }

  if (request.method === "GET") {
    const stored = await readRestartStatus(env)
    if (stored.kind === "absent") return jsonResponse({ ok: true, status: null })
    if (stored.kind !== "valid") return jsonResponse({ ok: false }, 503)
    return jsonResponse({ ok: true, status: stored.status })
  }

  const body = await readBoundedJson(request)
  if (body.kind === "large") return jsonResponse({ ok: false }, 413)
  if (body.kind !== "valid") return jsonResponse({ ok: false }, 400)

  if (request.method === "PUT") {
    const proposed = validatedRestartStatus(body.value)
    if (!proposed) return jsonResponse({ ok: false }, 400)
    const stored = await readRestartStatus(env)
    if (stored.kind === "error") return jsonResponse({ ok: false }, 503)
    if (stored.kind === "valid") {
      if (
        stored.status.generation === proposed.generation
        && JSON.stringify(stored.status) === JSON.stringify(proposed)
      ) return jsonResponse({ ok: true, status: proposed })
      if (
        stored.status.generation === proposed.generation
        || utcSecond(proposed.issued_at) <= utcSecond(stored.status.issued_at)
      ) return jsonResponse({ ok: false }, 409)
    }
    try {
      await env.MAESTRO_TARGETS.put(STATUS_KEY, JSON.stringify(proposed))
    } catch {
      return jsonResponse({ ok: false }, 503)
    }
    return jsonResponse({ ok: true, status: proposed })
  }

  if (
    !exactKeys(body.value, ["generation"])
    || typeof body.value.generation !== "string"
    || !GENERATION_PATTERN.test(body.value.generation)
  ) {
    return jsonResponse({ ok: false }, 400)
  }
  const stored = await readRestartStatus(env)
  if (stored.kind === "error" || stored.kind === "invalid") {
    return jsonResponse({ ok: false }, 503)
  }
  if (stored.kind === "absent" || stored.status.generation !== body.value.generation) {
    return jsonResponse({ ok: true, cleared: false })
  }
  try {
    await env.MAESTRO_TARGETS.delete(STATUS_KEY)
  } catch {
    return jsonResponse({ ok: false }, 503)
  }
  return jsonResponse({ ok: true, cleared: true })
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

const isBrowserNavigation = (request) => {
  const pathname = new URL(request.url).pathname
  if (pathname === "/api" || pathname.startsWith("/api/")) return false
  return request.method === "GET" && (
    request.headers.get("Sec-Fetch-Mode") === "navigate"
    || (request.headers.get("Accept") || "").toLowerCase().includes("text/html")
  )
}

const escapedHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;",
})[character])

const humanized = (value) => value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase())

const STATUS_HEADINGS = {
  planned: "Service work is planned",
  waiting_for_boundary: "Finishing current work",
  restarting: "Maestro is restarting",
  verifying: "Checking that Maestro is ready",
  complete: "Service work is complete",
  postponed: "Service work was postponed",
  forced_emergency: "Maestro is recovering",
}

const statusEta = (eta) => {
  if (eta === null) return "No estimate is available yet."
  if (eta.kind === "at") return "Open Technical details for the expected return time."
  return "Open Technical details for the expected return window."
}

const statusEtaDetails = (eta) => {
  if (eta === null) return "Not available"
  if (eta.kind === "at") return eta.at
  return `${eta.earliest} to ${eta.latest}`
}

const statusOfflineHtml = (status) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maestro service update</title><style>
:root{color-scheme:dark;font-family:ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1017;color:#edf2ff}main{width:min(42rem,calc(100% - 2rem));margin:1rem;padding:clamp(1.5rem,6vw,3rem);border:1px solid #2c3445;border-radius:1.25rem;background:#151a24;box-shadow:0 1.5rem 5rem #0008}h1{margin:.35rem 0 1rem;font-size:clamp(2rem,7vw,3.4rem)}p{color:#d4dbea;line-height:1.65}.eyebrow{color:#ff9b62;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.message{font-size:1.15rem;color:#edf2ff}.eta{margin-bottom:1.5rem}details{border-top:1px solid #2c3445;padding-top:1rem;color:#8f9ab0}summary{cursor:pointer;color:#bdc7da}dl{display:grid;grid-template-columns:max-content 1fr;gap:.65rem 1rem;margin:1rem 0 0}dt{color:#8f9ab0}dd{margin:0;color:#d4dbea;overflow-wrap:anywhere}small{display:block;margin-top:1.5rem;color:#8f9ab0}@media(max-width:30rem){dl{grid-template-columns:1fr;gap:.25rem}dd{margin-bottom:.5rem}}
</style></head><body><main aria-labelledby="status-title"><div class="eyebrow">Maestro service update</div><h1 id="status-title">${escapedHtml(STATUS_HEADINGS[status.state])}</h1><p class="message">${escapedHtml(status.message)}</p><p class="eta">${escapedHtml(statusEta(status.eta))}</p><details><summary>Technical details</summary><dl><dt>Status</dt><dd>${escapedHtml(humanized(status.state))}</dd><dt>Reason</dt><dd>${escapedHtml(humanized(status.reason))}</dd><dt>Estimated availability</dt><dd>${escapedHtml(statusEtaDetails(status.eta))}</dd><dt>Update issued</dt><dd>${escapedHtml(status.issued_at)}</dd><dt>Status expires</dt><dd>${escapedHtml(status.expires_at)}</dd></dl></details><small>This page contains no scripts, tracking, sign-in, or remote content.</small></main></body></html>`

const offlineResponse = async (request, env) => {
  if (!isBrowserNavigation(request)) {
    return jsonResponse({ ok: false, detail: "Maestro is offline" }, 503)
  }
  const stored = await readRestartStatus(env)
  const now = Number.isFinite(env.__TEST_NOW) ? env.__TEST_NOW : Date.now()
  const issuedAt = stored.kind === "valid" ? utcSecond(stored.status.issued_at) : null
  const expiresAt = stored.kind === "valid" ? utcSecond(stored.status.expires_at) : null
  const html = (
    stored.kind === "valid"
    && issuedAt <= now
    && now < expiresAt
  ) ? statusOfflineHtml(stored.status) : OFFLINE_HTML
  return new Response(html, {
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

const upstreamRequest = (request, destination) => {
  const headers = new Headers(request.headers)
  for (const name of REQUEST_HEADERS_TO_DROP) headers.delete(name)

  // Constructing from the original Request preserves a streaming body without
  // buffering uploads.  The second Request forces redirects to remain visible
  // to this Worker instead of replaying cookies or Authorization elsewhere.
  return new Request(new Request(destination, request), {
    headers,
    redirect: "manual",
  })
}

const setCookieValues = (headers) => {
  try {
    if (typeof headers.getAll === "function") {
      return headers.getAll("Set-Cookie")
    }
    if (typeof headers.getSetCookie === "function") {
      return headers.getSetCookie()
    }
  } catch {}
  const single = headers.get("Set-Cookie")
  return single ? [single] : []
}

const proxyResponseHeaders = (response) => {
  const headers = new Headers()
  const cookies = setCookieValues(response.headers)
  response.headers.forEach((value, name) => {
    const lower = name.toLowerCase()
    if (lower !== "set-cookie" && !RESPONSE_HEADERS_TO_DROP.has(lower)) {
      headers.append(name, value)
    }
  })
  for (const cookie of cookies) headers.append("Set-Cookie", cookie)
  // Cloudflare owns transfer framing for streamed Worker responses. Do not
  // promise an upstream Content-Length that the runtime may strip or replace.
  headers.set("Cache-Control", "no-store")
  headers.set("Referrer-Policy", "no-referrer")
  headers.set("X-Content-Type-Options", "nosniff")
  return headers
}

const stableRedirect = (value, destination, target, stableOrigin) => {
  let redirected
  try {
    redirected = new URL(value, destination)
  } catch {
    return null
  }
  if (
    redirected.origin !== target
    || redirected.username
    || redirected.password
  ) return null

  const stable = new URL(stableOrigin)
  stable.pathname = redirected.pathname
  stable.search = redirected.search
  stable.hash = redirected.hash
  return stable.href
}

const proxyToTarget = async (request, env, target, incoming) => {
  const destination = new URL(target)
  destination.pathname = incoming.pathname
  destination.search = incoming.search

  let response
  try {
    const targetFetch = env.__TEST_FETCH || globalThis.fetch
    response = await targetFetch(upstreamRequest(request, destination.href))
  } catch {
    return offlineResponse(request, env)
  }

  const headers = proxyResponseHeaders(response)
  const location = response.headers.get("Location")
  if (location && REDIRECT_STATUSES.has(response.status)) {
    const rewritten = stableRedirect(
      location, destination.href, target, incoming.origin,
    )
    if (!rewritten) {
      try { await response.body?.cancel() } catch {}
      return jsonResponse({ ok: false, detail: "Maestro returned an unsafe redirect" }, 502)
    }
    headers.set("Location", rewritten)
  }

  const contentLocation = response.headers.get("Content-Location")
  if (contentLocation) {
    const rewritten = stableRedirect(
      contentLocation, destination.href, target, incoming.origin,
    )
    if (rewritten) headers.set("Content-Location", rewritten)
    else headers.delete("Content-Location")
  }

  // Passing the body stream through keeps polling responses, uploads, and
  // downloads incremental and avoids charging Worker CPU for media buffering.
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  })
}

const directTunnelResponse = (target, incoming = null) => {
  const destination = new URL(target)
  if (incoming) {
    destination.pathname = incoming.pathname
    destination.search = incoming.search
  }
  return new Response(null, {
    status: 307,
    headers: {
      "Cache-Control": "no-store",
      Location: destination.href,
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
    if (incoming.pathname === STATUS_PATH) {
      if (!new Set(["GET", "PUT", "DELETE"]).has(request.method)) {
        return jsonResponse({ ok: false }, 405)
      }
      return restartStatus(request, env)
    }
    if (incoming.pathname === DIRECT_PATH && request.method !== "GET") {
      return jsonResponse({ ok: false }, 405)
    }

    let target
    try {
      target = canonicalQuickTunnelOrigin(await env.MAESTRO_TARGETS.get(TARGET_KEY))
    } catch {
      return offlineResponse(request, env)
    }
    if (!target || !(await cachedOriginHealth(target, env))) return offlineResponse(request, env)
    if (incoming.pathname === DIRECT_PATH) return directTunnelResponse(target)
    const shareMode = String(env.SHARE_MODE || "proxy").trim().toLowerCase()
    if (shareMode === "proxy") return proxyToTarget(request, env, target, incoming)
    return directTunnelResponse(target, incoming)
  },
}

export const paths = { DIRECT_PATH, HEALTH_PATH, STATUS_PATH, UPDATE_PATH }
