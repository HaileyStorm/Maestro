import assert from "node:assert/strict"
import test from "node:test"

import worker, { canonicalQuickTunnelOrigin, paths } from "./worker.mjs"
import { extractNamespaceId } from "./provision_helpers.mjs"

const secret = "test-secret-that-is-long-enough-for-tests"

const environment = () => {
  const values = new Map()
  return {
    UPDATE_TOKEN: secret,
    __TEST_FETCH: async () => new Response('{"status":"ok"}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
    MAESTRO_TARGETS: {
      get: async (key) => values.get(key) || null,
      put: async (key, value) => values.set(key, value),
    },
  }
}

const auth = { Authorization: `Bearer ${secret}` }

test("provisioner parses Wrangler JSONC and TOML namespace output", () => {
  const id = "0123456789abcdef0123456789abcdef"
  assert.equal(extractNamespaceId(`{ "binding": "TARGETS", "id": "${id}" }`), id)
  assert.equal(extractNamespaceId(`binding = "TARGETS"\nid = "${id}"`), id)
})

test("accepts only canonical HTTPS Quick Tunnel origins", () => {
  assert.equal(
    canonicalQuickTunnelOrigin("https://current-tunnel.trycloudflare.com"),
    "https://current-tunnel.trycloudflare.com",
  )
  for (const value of [
    "http://current-tunnel.trycloudflare.com",
    "https://trycloudflare.com",
    "https://current-tunnel.trycloudflare.com/",
    "https://current-tunnel.trycloudflare.com/path",
    "https://current-tunnel.trycloudflare.com.evil.test",
    "https://user@current-tunnel.trycloudflare.com",
    "https://current-tunnel.trycloudflare.com:8443",
  ]) assert.equal(canonicalQuickTunnelOrigin(value), null, value)
})

test("update and health require the bearer secret", async () => {
  const env = environment()
  const updateUrl = `https://maestro.example.workers.dev${paths.UPDATE_PATH}`
  const target = "https://current-tunnel.trycloudflare.com"
  for (const headers of [{}, { Authorization: "Bearer wrong" }]) {
    const response = await worker.fetch(new Request(updateUrl, {
      method: "PUT",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
    }), env)
    assert.equal(response.status, 401)
  }
  const hidden = await worker.fetch(new Request(
    `https://maestro.example.workers.dev${paths.HEALTH_PATH}`,
  ), env)
  assert.equal(hidden.status, 401)

  const unavailable = await worker.fetch(new Request(
    "https://maestro.example.workers.dev/share/not-ready",
  ), env)
  assert.equal(unavailable.status, 503)
  assert.equal(unavailable.headers.get("Cache-Control"), "no-store")
})

test("authenticated health confirms the exact stored target", async () => {
  const env = environment()
  const target = "https://current-tunnel.trycloudflare.com"
  const update = await worker.fetch(new Request(
    `https://maestro.example.workers.dev${paths.UPDATE_PATH}`,
    {
      method: "PUT",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({ target }),
    },
  ), env)
  assert.equal(update.status, 200)

  const health = await worker.fetch(new Request(
    `https://maestro.example.workers.dev${paths.HEALTH_PATH}`,
    { headers: auth },
  ), env)
  assert.equal(health.status, 200)
  assert.deepEqual(await health.json(), { ok: true, configured: true, target })
})

test("redirect preserves path, query, and request method with 307", async () => {
  const env = environment()
  await env.MAESTRO_TARGETS.put(
    "quick-tunnel-origin", "https://current-tunnel.trycloudflare.com",
  )
  const response = await worker.fetch(new Request(
    "https://maestro.example.workers.dev/share/a%20b?download=1&x=%2F",
    { method: "POST", body: "preserved-by-307", redirect: "manual" },
  ), env)
  assert.equal(response.status, 307)
  assert.equal(
    response.headers.get("Location"),
    "https://current-tunnel.trycloudflare.com/share/a%20b?download=1&x=%2F",
  )
  assert.equal(response.headers.get("Cache-Control"), "no-store")

  const doubleSlash = await worker.fetch(new Request(
    "https://maestro.example.workers.dev//evil.example/path?x=1",
    { redirect: "manual" },
  ), env)
  assert.equal(
    doubleSlash.headers.get("Location"),
    "https://current-tunnel.trycloudflare.com//evil.example/path?x=1",
  )
  const tripleSlash = await worker.fetch(new Request(
    "https://maestro.example.workers.dev///evil.example/path",
    { redirect: "manual" },
  ), env)
  assert.equal(
    tripleSlash.headers.get("Location"),
    "https://current-tunnel.trycloudflare.com///evil.example/path",
  )
})

test("offline browser navigation gets static no-tracking HTML", async () => {
  const env = environment()
  await env.MAESTRO_TARGETS.put(
    "quick-tunnel-origin", "https://expired-tunnel.trycloudflare.com",
  )
  const probes = []
  env.__TEST_FETCH = async (url, options) => {
    probes.push({ url, options })
    return new Response(null, { status: 530 })
  }
  const response = await worker.fetch(new Request(
    "https://maestro.example.workers.dev/project/path?private=value",
    { headers: { Accept: "text/html,application/xhtml+xml" } },
  ), env)
  assert.equal(response.status, 503)
  assert.match(response.headers.get("Content-Type"), /^text\/html/)
  assert.equal(response.headers.get("Cache-Control"), "no-store")
  assert.match(response.headers.get("Content-Security-Policy"), /default-src 'none'/)
  const html = await response.text()
  assert.match(html, /Maestro is offline/)
  assert.doesNotMatch(html, /private=value|<script|https?:\/\//)
  assert.equal(probes.length, 1)
  assert.equal(probes[0].url, "https://expired-tunnel.trycloudflare.com/health")
  assert.equal(probes[0].options.redirect, "manual")
  assert.equal(probes[0].options.method, "GET")
})

test("offline API and non-navigation requests get 503 JSON without redirect", async () => {
  const env = environment()
  await env.MAESTRO_TARGETS.put(
    "quick-tunnel-origin", "https://expired-tunnel.trycloudflare.com",
  )
  const probes = []
  env.__TEST_FETCH = async (url, options) => {
    probes.push({ url, options })
    throw new Error("tunnel unavailable")
  }
  const response = await worker.fetch(new Request(
    "https://maestro.example.workers.dev/api/v1/outputs",
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: '{"prompt":"must-not-be-forwarded"}',
    },
  ), env)
  assert.equal(response.status, 503)
  assert.equal(response.headers.get("Location"), null)
  assert.deepEqual(await response.json(), { ok: false, detail: "Maestro is offline" })
  assert.equal(probes.length, 1)
  assert.equal(probes[0].url, "https://expired-tunnel.trycloudflare.com/health")
  assert.equal(probes[0].options.method, "GET")
  assert.equal(probes[0].options.body, undefined)
})

test("brief health cache limits origin probes and is keyed by validated target", async () => {
  const env = environment()
  await env.MAESTRO_TARGETS.put(
    "quick-tunnel-origin", "https://current-tunnel.trycloudflare.com",
  )
  const cached = new Map()
  env.__TEST_CACHE = {
    match: async (request) => cached.get(request.url)?.clone() || undefined,
    put: async (request, response) => cached.set(request.url, response.clone()),
  }
  let probes = 0
  env.__TEST_FETCH = async () => {
    probes += 1
    return new Response(null, { status: 204 })
  }
  for (const path of ["/", "/share/token"] ) {
    const response = await worker.fetch(new Request(
      `https://maestro.example.workers.dev${path}`,
      { redirect: "manual" },
    ), env)
    assert.equal(response.status, 307)
  }
  assert.equal(probes, 1)
  assert.equal(cached.size, 1)
})
