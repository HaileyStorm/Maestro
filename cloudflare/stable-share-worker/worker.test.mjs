import assert from "node:assert/strict"
import test from "node:test"

import worker, {
  canonicalQuickTunnelOrigin,
  paths,
  validatedRestartStatus,
} from "./worker.mjs"
import {
  extractNamespaceId,
  extractWhoamiAccountId,
  isWhoamiLoggedOut,
} from "./provision_helpers.mjs"

const secret = "test-secret-that-is-long-enough-for-tests"

const environment = () => {
  const values = new Map()
  const env = {
    UPDATE_TOKEN: secret,
    __TEST_VALUES: values,
    __TEST_FETCH: async () => new Response('{"status":"ok"}', {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
    MAESTRO_TARGETS: {
      get: async (key) => values.get(key) || null,
      put: async (key, value) => values.set(key, value),
      delete: async (key) => values.delete(key),
    },
  }
  return env
}

const auth = { Authorization: `Bearer ${secret}` }
const target = "https://current-tunnel.trycloudflare.com"
const stable = "https://maestro.example.workers.dev"
const statusUrl = `${stable}${paths.STATUS_PATH}`
const now = Date.parse("2030-01-02T03:04:05Z")
const statusRecord = (overrides = {}) => ({
  schema_version: 1,
  generation: "generation_000001",
  state: "restarting",
  reason: "maintenance",
  message: "Maestro is restarting for a planned update.",
  issued_at: "2030-01-02T03:00:00Z",
  expires_at: "2030-01-02T04:00:00Z",
  eta: {
    kind: "range",
    earliest: "2030-01-02T03:15:00Z",
    latest: "2030-01-02T03:30:00Z",
  },
  ...overrides,
})
const fetchedRequest = (input, init) => (
  input instanceof Request ? input : new Request(input, init)
)

const configureTarget = async (env, value = target) => {
  await env.MAESTRO_TARGETS.put("quick-tunnel-origin", value)
}

const statusRequest = (method, body, headers = auth) => new Request(statusUrl, {
  method,
  headers: body === undefined ? headers : { ...headers, "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
})

test("provisioner parses Wrangler JSONC and TOML namespace output", () => {
  const id = "0123456789abcdef0123456789abcdef"
  assert.equal(extractNamespaceId(`{ "binding": "TARGETS", "id": "${id}" }`), id)
  assert.equal(extractNamespaceId(`binding = "TARGETS"\nid = "${id}"`), id)
})

test("provisioner accepts bounded current Wrangler whoami JSON", () => {
  const id = "0123456789abcdef0123456789abcdef"
  assert.equal(extractWhoamiAccountId(JSON.stringify({
    loggedIn: true,
    authType: "OAuth Token",
    email: "person@example.test",
    accounts: [{ id, name: "Sanitized account" }],
    tokenPermissions: ["workers:write"],
  })), id)
})

test("provisioner rejects malformed or ambiguous Wrangler whoami JSON", () => {
  const first = "0123456789abcdef0123456789abcdef"
  const second = "fedcba9876543210fedcba9876543210"
  const multiple = JSON.stringify({
    loggedIn: true,
    accounts: [{ id: first }, { id: second }],
  })
  assert.equal(extractWhoamiAccountId(multiple), "")
  assert.equal(extractWhoamiAccountId(multiple, second), second)
  assert.equal(extractWhoamiAccountId(multiple, "a".repeat(32)), "")
  assert.equal(extractWhoamiAccountId(JSON.stringify({
    loggedIn: true,
    accounts: [{ id: first }],
  }), second), first)

  for (const value of [
    "not json",
    JSON.stringify({ loggedIn: false }),
    JSON.stringify({ loggedIn: true, accounts: [{ id: "short" }] }),
    JSON.stringify({ loggedIn: true, accounts: [{ account: { id: first } }] }),
    JSON.stringify({
      memberships: [{ account: { id: first } }],
    }),
    JSON.stringify({ loggedIn: true, accounts: Array(101).fill({ id: first }) }),
    " ".repeat(64 * 1024 + 1),
    `{"loggedIn":false,"loggedIn":true,"accounts":[{"id":"${first}"}]}`,
    `{"loggedIn":true,"accounts":[{"id":"${first}"}],"accounts":[{"id":"${second}"}]}`,
    `{"loggedIn":true,"accounts":[{"id":"${first}","\\u0069d":"${first}"}]}`,
  ]) assert.equal(extractWhoamiAccountId(value), "")
})

test("provisioner verifies only Wrangler's structured logged-out identity", () => {
  assert.equal(isWhoamiLoggedOut('{"loggedIn":false}'), true)
  assert.equal(isWhoamiLoggedOut('{"loggedIn":true,"accounts":[]}'), false)
  assert.equal(isWhoamiLoggedOut('{"loggedIn":false,"accounts":[]}'), false)
  assert.equal(isWhoamiLoggedOut('{"loggedIn":false,"reason":"expired"}'), false)
  assert.equal(isWhoamiLoggedOut('{"loggedIn":true,"loggedIn":false}'), false)
  assert.equal(isWhoamiLoggedOut("Wrangler is not logged in"), false)
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

test("restart status management is authenticated and method bounded", async () => {
  const env = environment()
  for (const method of ["GET", "PUT", "DELETE"]) {
    const response = await worker.fetch(statusRequest(
      method,
      method === "GET" ? undefined : (method === "PUT" ? statusRecord() : { generation: "generation_000001" }),
      {},
    ), env)
    assert.equal(response.status, 401, method)
    assert.deepEqual(await response.json(), { ok: false })
  }
  const unsupported = await worker.fetch(new Request(statusUrl, {
    method: "POST",
    headers: auth,
  }), env)
  assert.equal(unsupported.status, 405)
  assert.deepEqual(await unsupported.json(), { ok: false })
})

test("restart status schema accepts only the closed canonical contract", async () => {
  const validAt = statusRecord({
    state: "planned",
    reason: "restart",
    eta: { kind: "at", at: "2030-01-02T03:20:00Z" },
  })
  assert.deepEqual(validatedRestartStatus(validAt), validAt)
  assert.deepEqual(validatedRestartStatus(statusRecord({ eta: null })), statusRecord({ eta: null }))
  for (const state of ["planned", "waiting_for_boundary", "restarting", "verifying", "complete", "postponed", "forced_emergency"]) {
    assert.ok(validatedRestartStatus(statusRecord({ state })), state)
  }
  for (const reason of ["restart", "maintenance", "shutdown", "incident"]) {
    assert.ok(validatedRestartStatus(statusRecord({ reason })), reason)
  }
  assert.ok(validatedRestartStatus(statusRecord({ generation: "a".repeat(16) })))
  assert.ok(validatedRestartStatus(statusRecord({ generation: "a".repeat(64) })))
  assert.ok(validatedRestartStatus(statusRecord({ message: "x".repeat(240) })))

  const invalid = [
    { ...statusRecord(), extra: true },
    statusRecord({ schema_version: 2 }),
    statusRecord({ generation: "too-short" }),
    statusRecord({ generation: "a".repeat(65) }),
    statusRecord({ generation: "generation.invalid" }),
    statusRecord({ state: "unknown" }),
    statusRecord({ reason: "upgrade" }),
    statusRecord({ message: "" }),
    statusRecord({ message: "x".repeat(241) }),
    statusRecord({ message: "line one\nline two" }),
    statusRecord({ issued_at: "2030-01-02T03:00:00.000Z" }),
    statusRecord({ issued_at: "2030-02-30T03:00:00Z" }),
    statusRecord({ expires_at: "2030-01-03T03:00:01Z" }),
    statusRecord({ expires_at: "2030-01-02T03:00:00Z" }),
    statusRecord({ eta: { kind: "at", at: "2030-01-02T04:00:01Z" } }),
    statusRecord({ eta: { kind: "range", earliest: "2030-01-02T03:40:00Z", latest: "2030-01-02T03:30:00Z" } }),
    statusRecord({ eta: { kind: "range", earliest: "2030-01-02T03:15:00Z", latest: "2030-01-02T03:30:00Z", extra: true } }),
  ]
  for (const [index, record] of invalid.entries()) {
    assert.equal(validatedRestartStatus(record), null, `validator case ${index}`)
    const response = await worker.fetch(statusRequest("PUT", record), environment())
    assert.equal(response.status, 400, `endpoint case ${index}`)
    assert.deepEqual(await response.json(), { ok: false })
  }
})

test("restart status bounds request bodies and fails closed on KV errors", async () => {
  const oversized = await worker.fetch(new Request(statusUrl, {
    method: "PUT",
    headers: { ...auth, "Content-Type": "application/json" },
    body: "x".repeat(4097),
  }), environment())
  assert.equal(oversized.status, 413)

  const readFailure = environment()
  readFailure.MAESTRO_TARGETS.get = async () => { throw new Error("synthetic read failure") }
  assert.equal((await worker.fetch(statusRequest("GET"), readFailure)).status, 503)
  assert.equal((await worker.fetch(statusRequest("PUT", statusRecord()), readFailure)).status, 503)

  const putFailure = environment()
  putFailure.MAESTRO_TARGETS.put = async () => { throw new Error("synthetic put failure") }
  assert.equal((await worker.fetch(statusRequest("PUT", statusRecord()), putFailure)).status, 503)

  const deleteFailure = environment()
  deleteFailure.__TEST_VALUES.set("restart-status", JSON.stringify(statusRecord()))
  deleteFailure.MAESTRO_TARGETS.delete = async () => { throw new Error("synthetic delete failure") }
  const clear = await worker.fetch(statusRequest("DELETE", {
    generation: statusRecord().generation,
  }), deleteFailure)
  assert.equal(clear.status, 503)
  assert.ok(deleteFailure.__TEST_VALUES.has("restart-status"))
})

test("restart status PUT is replay-safe and newer generations replace older ones", async () => {
  const env = environment()
  let puts = 0
  const originalPut = env.MAESTRO_TARGETS.put
  env.MAESTRO_TARGETS.put = async (...args) => {
    puts += 1
    return originalPut(...args)
  }

  const empty = await worker.fetch(statusRequest("GET"), env)
  assert.equal(empty.status, 200)
  assert.deepEqual(await empty.json(), { ok: true, status: null })

  const firstRecord = statusRecord()
  const first = await worker.fetch(statusRequest("PUT", firstRecord), env)
  assert.equal(first.status, 200)
  assert.deepEqual(await first.json(), { ok: true, status: firstRecord })
  assert.equal(puts, 1)
  assert.equal(env.__TEST_VALUES.size, 1)
  assert.equal(
    env.__TEST_VALUES.get("restart-status"),
    JSON.stringify(firstRecord),
  )

  const replay = await worker.fetch(statusRequest("PUT", { ...firstRecord }), env)
  assert.equal(replay.status, 200)
  assert.equal(puts, 1)

  const alteredReplay = await worker.fetch(statusRequest("PUT", statusRecord({
    message: "Changed under the same generation.",
  })), env)
  assert.equal(alteredReplay.status, 409)

  const older = await worker.fetch(statusRequest("PUT", statusRecord({
    generation: "generation_000002",
    issued_at: "2030-01-02T02:59:59Z",
  })), env)
  assert.equal(older.status, 409)

  const sameTime = await worker.fetch(statusRequest("PUT", statusRecord({
    generation: "generation_000003",
  })), env)
  assert.equal(sameTime.status, 409)

  const newerRecord = statusRecord({
    generation: "generation_000004",
    state: "verifying",
    issued_at: "2030-01-02T03:05:00Z",
  })
  const newer = await worker.fetch(statusRequest("PUT", newerRecord), env)
  assert.equal(newer.status, 200)
  assert.equal(puts, 2)

  const current = await worker.fetch(statusRequest("GET"), env)
  assert.deepEqual(await current.json(), { ok: true, status: newerRecord })
})

test("restart status DELETE clears only its matching generation", async () => {
  const env = environment()
  const current = statusRecord({ generation: "generation_000010" })
  await worker.fetch(statusRequest("PUT", current), env)

  const malformed = await worker.fetch(statusRequest("DELETE", {
    generation: current.generation,
    extra: true,
  }), env)
  assert.equal(malformed.status, 400)

  const stale = await worker.fetch(statusRequest("DELETE", {
    generation: "generation_000009",
  }), env)
  assert.equal(stale.status, 200)
  assert.deepEqual(await stale.json(), { ok: true, cleared: false })
  assert.ok(env.__TEST_VALUES.has("restart-status"))

  const matching = await worker.fetch(statusRequest("DELETE", {
    generation: current.generation,
  }), env)
  assert.equal(matching.status, 200)
  assert.deepEqual(await matching.json(), { ok: true, cleared: true })
  assert.equal(env.__TEST_VALUES.has("restart-status"), false)

  const repeated = await worker.fetch(statusRequest("DELETE", {
    generation: current.generation,
  }), env)
  assert.deepEqual(await repeated.json(), { ok: true, cleared: false })
})

test("restart status DELETE rejects non-string generation values", async () => {
  const env = environment()
  const current = statusRecord({ generation: "generation_000010" })
  await worker.fetch(statusRequest("PUT", current), env)

  for (const generation of [1234567890123456, [current.generation]]) {
    const response = await worker.fetch(statusRequest("DELETE", { generation }), env)
    assert.equal(response.status, 400)
    assert.deepEqual(await response.json(), { ok: false })
    assert.ok(env.__TEST_VALUES.has("restart-status"))
  }
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

test("current restart status renders escaped accessible HTML before origin health", async () => {
  const env = environment()
  env.__TEST_NOW = now
  const record = statusRecord({
    state: "waiting_for_boundary",
    message: 'Please wait <script>alert("unsafe")</script> & retry.',
  })
  const stored = await worker.fetch(statusRequest("PUT", record), env)
  assert.equal(stored.status, 200)
  await configureTarget(env)
  let healthProbes = 0
  env.__TEST_FETCH = async () => {
    healthProbes += 1
    return new Response(null, { status: 530 })
  }

  const response = await worker.fetch(new Request(`${stable}/project/current`, {
    headers: { "Sec-Fetch-Mode": "navigate" },
  }), env)
  assert.equal(response.status, 503)
  assert.match(response.headers.get("Content-Type"), /^text\/html/)
  assert.equal(response.headers.get("Cache-Control"), "no-store")
  assert.match(response.headers.get("Content-Security-Policy"), /default-src 'none'/)
  const html = await response.text()
  assert.match(html, /aria-labelledby="status-title"/)
  assert.match(html, /Waiting for boundary/)
  assert.match(html, /Please wait &lt;script&gt;alert\(&quot;unsafe&quot;\)&lt;\/script&gt; &amp; retry\./)
  assert.match(html, /2030-01-02T03:15:00Z to 2030-01-02T03:30:00Z/)
  assert.doesNotMatch(html, /<script|alert\("unsafe"\)/)
  assert.equal(healthProbes, 1)
})

test("proxy disconnect also renders the current restart status for navigation", async () => {
  const env = environment()
  env.__TEST_NOW = now
  await configureTarget(env)
  await worker.fetch(statusRequest("PUT", statusRecord({
    state: "verifying",
    eta: { kind: "at", at: "2030-01-02T03:20:00Z" },
  })), env)
  env.__TEST_FETCH = async (input, init) => {
    const request = fetchedRequest(input, init)
    if (new URL(request.url).pathname === "/health") {
      return new Response(null, { status: 204 })
    }
    throw new Error("synthetic proxy disconnect")
  }

  const response = await worker.fetch(new Request(`${stable}/project/current`, {
    headers: { Accept: "text/html" },
  }), env)
  assert.equal(response.status, 503)
  const html = await response.text()
  assert.match(html, /Maestro service update/)
  assert.match(html, /Verifying/)
  assert.match(html, /2030-01-02T03:20:00Z/)
})

test("expired, malformed, and unreadable restart status use the unchanged generic HTML", async () => {
  const baselineEnv = environment()
  baselineEnv.__TEST_NOW = now
  const baseline = await worker.fetch(new Request(`${stable}/`, {
    headers: { Accept: "text/html" },
  }), baselineEnv)
  const genericHtml = await baseline.text()

  const expired = environment()
  expired.__TEST_NOW = now
  expired.__TEST_VALUES.set("restart-status", JSON.stringify(statusRecord({
    issued_at: "2030-01-02T01:00:00Z",
    expires_at: "2030-01-02T02:00:00Z",
    eta: null,
  })))

  const malformed = environment()
  malformed.__TEST_NOW = now
  malformed.__TEST_VALUES.set("restart-status", "{not-json")

  const future = environment()
  future.__TEST_NOW = now
  future.__TEST_VALUES.set("restart-status", JSON.stringify(statusRecord({
    issued_at: "2030-01-02T04:00:00Z",
    expires_at: "2030-01-02T05:00:00Z",
    eta: null,
  })))

  const unreadable = environment()
  unreadable.__TEST_NOW = now
  const unreadableGet = unreadable.MAESTRO_TARGETS.get
  unreadable.MAESTRO_TARGETS.get = async (key) => {
    if (key === "restart-status") throw new Error("synthetic KV read failure")
    return unreadableGet(key)
  }

  for (const [name, env] of [["expired", expired], ["future", future], ["malformed", malformed], ["unreadable", unreadable]]) {
    const response = await worker.fetch(new Request(`${stable}/`, {
      headers: { Accept: "text/html" },
    }), env)
    assert.equal(response.status, 503, name)
    assert.equal(await response.text(), genericHtml, name)
  }
})

test("restart status never changes GET API paths into browser offline HTML", async () => {
  const env = environment()
  env.__TEST_NOW = now
  env.__TEST_VALUES.set("restart-status", JSON.stringify(statusRecord()))
  let statusReads = 0
  const originalGet = env.MAESTRO_TARGETS.get
  env.MAESTRO_TARGETS.get = async (key) => {
    if (key === "restart-status") statusReads += 1
    return originalGet(key)
  }
  for (const pathname of ["/api", "/api/v1/jobs"]) {
    const response = await worker.fetch(new Request(`${stable}${pathname}`, {
      headers: {
        Accept: "text/html,application/xhtml+xml",
        "Sec-Fetch-Mode": "navigate",
      },
    }), env)
    assert.equal(response.status, 503, pathname)
    assert.match(response.headers.get("Content-Type"), /^application\/json/)
    assert.deepEqual(
      await response.json(),
      { ok: false, detail: "Maestro is offline" },
      pathname,
    )
  }
  assert.equal(statusReads, 0)

  const ordinaryNavigation = await worker.fetch(new Request(`${stable}/apiary`, {
    headers: { "Sec-Fetch-Mode": "navigate" },
  }), env)
  assert.equal(ordinaryNavigation.status, 503)
  assert.match(ordinaryNavigation.headers.get("Content-Type"), /^text\/html/)
  assert.match(await ordinaryNavigation.text(), /Maestro service update/)
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
