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
const target = "https://current-tunnel.trycloudflare.com"
const stable = "https://maestro.example.workers.dev"
const fetchedRequest = (input, init) => (
  input instanceof Request ? input : new Request(input, init)
)

const configureTarget = async (env, value = target) => {
  await env.MAESTRO_TARGETS.put("quick-tunnel-origin", value)
}

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

test("GET polling stays on the stable address and preserves request details", async () => {
  const env = environment()
  await configureTarget(env)
  const upstream = []
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    upstream.push(request)
    return new Response('{"state":"running"}', {
      headers: {
        "Content-Type": "application/json",
        "X-Upstream": "kept",
        Connection: "close",
      },
    })
  }
  const response = await worker.fetch(new Request(
    `${stable}/api/v1/jobs/synthetic?poll=1&x=%2F`,
    {
      headers: {
        Accept: "application/json",
        Origin: stable,
        "X-Forwarded-Host": "attacker.example",
        "X-Forwarded-Proto": "http",
      },
    },
  ), env)
  assert.equal(response.status, 200)
  assert.equal(response.headers.get("Location"), null)
  assert.equal(response.headers.get("X-Upstream"), "kept")
  assert.equal(response.headers.get("Connection"), null)
  assert.equal(response.headers.get("Cache-Control"), "no-store")
  assert.equal(await response.text(), '{"state":"running"}')

  assert.equal(upstream.length, 1)
  assert.equal(
    upstream[0].url,
    `${target}/api/v1/jobs/synthetic?poll=1&x=%2F`,
  )
  assert.equal(upstream[0].method, "GET")
  assert.equal(upstream[0].redirect, "manual")
  assert.equal(upstream[0].headers.get("Accept"), "application/json")
  assert.equal(upstream[0].headers.get("Origin"), stable)
  assert.equal(upstream[0].headers.get("X-Forwarded-Host"), null)
  assert.equal(upstream[0].headers.get("X-Forwarded-Proto"), null)
})

test("POST uploads stream body and session headers only to the registered target", async () => {
  const env = environment()
  await configureTarget(env)
  let received
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      assert.equal(request.headers.get("Cookie"), null)
      assert.equal(request.headers.get("Authorization"), null)
      return new Response(null, { status: 204 })
    }
    received = {
      url: request.url,
      method: request.method,
      redirect: request.redirect,
      contentType: request.headers.get("Content-Type"),
      cookie: request.headers.get("Cookie"),
      authorization: request.headers.get("Authorization"),
      origin: request.headers.get("Origin"),
      forwarded: request.headers.get("Forwarded"),
      body: await request.text(),
    }
    return new Response('{"accepted":true}', {
      status: 202,
      headers: { "Content-Type": "application/json" },
    })
  }
  const upload = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("synthetic-upload-"))
      controller.enqueue(new TextEncoder().encode("bytes"))
      controller.close()
    },
  })
  const response = await worker.fetch(new Request(`${stable}/api/v1/upload?slot=2`, {
    method: "POST",
    headers: {
      Authorization: "Bearer app-session",
      Cookie: "maestro_session=synthetic",
      "Content-Type": "application/octet-stream",
      Forwarded: "host=attacker.example;proto=http",
      Origin: stable,
    },
    body: upload,
    duplex: "half",
  }), env)

  assert.equal(response.status, 202)
  assert.deepEqual(received, {
    url: `${target}/api/v1/upload?slot=2`,
    method: "POST",
    redirect: "manual",
    contentType: "application/octet-stream",
    cookie: "maestro_session=synthetic",
    authorization: "Bearer app-session",
    origin: stable,
    forwarded: null,
    body: "synthetic-upload-bytes",
  })
})

test("same-target redirects are rewritten back onto the stable origin", async () => {
  const env = environment()
  await configureTarget(env)
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    return new Response(null, {
      status: 307,
      headers: {
        Location: `${target}/signin/continue?next=%2Fstudio#ready`,
        "Content-Location": "/signin/content",
      },
    })
  }
  const response = await worker.fetch(new Request(`${stable}/signin`), env)

  assert.equal(response.status, 307)
  assert.equal(
    response.headers.get("Location"),
    `${stable}/signin/continue?next=%2Fstudio#ready`,
  )
  assert.equal(response.headers.get("Content-Location"), `${stable}/signin/content`)
  assert.equal(response.headers.get("Cache-Control"), "no-store")
})

test("cross-target redirects are not followed and cannot replay credentials", async () => {
  const env = environment()
  await configureTarget(env)
  const nextTarget = "https://next-tunnel.trycloudflare.com"
  const requests = []
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    requests.push({
      url: request.url,
      cookie: request.headers.get("Cookie"),
      authorization: request.headers.get("Authorization"),
    })
    return new Response(null, {
      status: 302,
      headers: { Location: `${nextTarget}/capture` },
    })
  }
  const response = await worker.fetch(new Request(`${stable}/private`, {
    headers: {
      Authorization: "Bearer must-not-cross-targets",
      Cookie: "maestro_session=must-not-cross-targets",
    },
  }), env)

  assert.equal(response.status, 502)
  assert.equal(response.headers.get("Location"), null)
  assert.deepEqual(await response.json(), {
    ok: false,
    detail: "Maestro returned an unsafe redirect",
  })
  assert.deepEqual(requests, [{
    url: `${target}/private`,
    cookie: "maestro_session=must-not-cross-targets",
    authorization: "Bearer must-not-cross-targets",
  }])
})

test("download responses preserve body and separate Set-Cookie headers", async () => {
  const env = environment()
  await configureTarget(env)
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    const headers = new Headers({
      "Content-Disposition": 'attachment; filename="synthetic.bin"',
      "Content-Location": "https://unrelated.example/private",
    })
    headers.append("Set-Cookie", "maestro_session=one; Path=/; HttpOnly; Secure")
    headers.append("Set-Cookie", "maestro_refresh=two; Path=/; HttpOnly; Secure")
    return new Response("synthetic-download-bytes", { headers })
  }
  const response = await worker.fetch(new Request(`${stable}/api/v1/download`), env)

  assert.equal(await response.text(), "synthetic-download-bytes")
  assert.equal(
    response.headers.get("Content-Disposition"),
    'attachment; filename="synthetic.bin"',
  )
  assert.equal(response.headers.get("Content-Location"), null)
  assert.deepEqual(response.headers.getSetCookie(), [
    "maestro_session=one; Path=/; HttpOnly; Secure",
    "maestro_refresh=two; Path=/; HttpOnly; Secure",
  ])
})

test("Range, 206, Content-Range, and response streaming pass through", async () => {
  const env = environment()
  await configureTarget(env)
  let proxyRequests = 0
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    proxyRequests += 1
    assert.equal(request.headers.get("Range"), "bytes=40-51")
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("partial-data"))
        controller.close()
      },
    })
    const upstream = new Response(stream, {
      status: 206,
      headers: {
        "Accept-Ranges": "bytes",
        "Content-Length": "12",
        "Content-Range": "bytes 40-51/1000",
      },
    })
    upstream.arrayBuffer = () => { throw new Error("proxy buffered upstream body") }
    upstream.bytes = () => { throw new Error("proxy buffered upstream body") }
    upstream.json = () => { throw new Error("proxy buffered upstream body") }
    upstream.text = () => { throw new Error("proxy buffered upstream body") }
    return upstream
  }
  const response = await worker.fetch(new Request(`${stable}/media/synthetic.bin`, {
    headers: { Range: "bytes=40-51" },
  }), env)

  assert.equal(proxyRequests, 1)
  assert.equal(response.status, 206)
  assert.equal(response.headers.get("Content-Length"), null)
  assert.equal(response.headers.get("Content-Range"), "bytes 40-51/1000")
  assert.equal(response.headers.get("Accept-Ranges"), "bytes")
  assert.equal(await response.text(), "partial-data")
})

test("HEAD requests preserve metadata without creating a body", async () => {
  const env = environment()
  await configureTarget(env)
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    assert.equal(request.method, "HEAD")
    assert.equal(request.body, null)
    return new Response(null, {
      headers: { "Content-Length": "987654321", ETag: '"synthetic"' },
    })
  }
  const response = await worker.fetch(new Request(`${stable}/media/synthetic.bin`, {
    method: "HEAD",
  }), env)

  assert.equal(response.status, 200)
  assert.equal(response.body, null)
  assert.equal(response.headers.get("Content-Length"), null)
  assert.equal(response.headers.get("ETag"), '"synthetic"')
})

test("direct fallback and explicit redirect rollback stay bounded to the validated target", async () => {
  const env = environment()
  await configureTarget(env)
  let proxyRequests = 0
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    proxyRequests += 1
    return new Response("unexpected proxy")
  }

  const direct = await worker.fetch(new Request(
    `${stable}${paths.DIRECT_PATH}?next=https://evil.example/capture`,
  ), env)
  assert.equal(direct.status, 307)
  assert.equal(direct.headers.get("Location"), `${target}/`)

  env.SHARE_MODE = "redirect"
  const rollback = await worker.fetch(new Request(
    `${stable}/project/a%20b?download=1&x=%2F`,
  ), env)
  assert.equal(rollback.status, 307)
  assert.equal(
    rollback.headers.get("Location"),
    `${target}/project/a%20b?download=1&x=%2F`,
  )
  assert.equal(proxyRequests, 0)
})

test("a proxy network failure returns the bounded offline response", async () => {
  const env = environment()
  await configureTarget(env)
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    throw new Error("synthetic upstream disconnect")
  }
  const response = await worker.fetch(new Request(`${stable}/api/v1/poll`, {
    headers: { Accept: "application/json" },
  }), env)

  assert.equal(response.status, 503)
  assert.equal(response.headers.get("Location"), null)
  assert.deepEqual(await response.json(), { ok: false, detail: "Maestro is offline" })
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
  await configureTarget(env)
  const cached = new Map()
  env.__TEST_CACHE = {
    match: async (request) => cached.get(request.url)?.clone() || undefined,
    put: async (request, response) => cached.set(request.url, response.clone()),
  }
  let healthProbes = 0
  let proxied = 0
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      healthProbes += 1
      return new Response(null, { status: 204 })
    }
    proxied += 1
    return new Response("ok")
  }
  for (const path of ["/", "/share/token"] ) {
    const response = await worker.fetch(new Request(
      `https://maestro.example.workers.dev${path}`,
      { redirect: "manual" },
    ), env)
    assert.equal(response.status, 200)
  }
  assert.equal(healthProbes, 1)
  assert.equal(proxied, 2)
  assert.equal(cached.size, 1)
})
