#!/usr/bin/env node
import {
  chmodSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { randomBytes } from "node:crypto"
import { spawnSync } from "node:child_process"

import {
  extractNamespaceId,
  extractWhoamiAccountId,
  isWhoamiLoggedOut,
} from "./provision_helpers.mjs"

const here = dirname(fileURLToPath(import.meta.url))
const repository = dirname(dirname(here))
const environmentPath = join(repository, "ENVIRONMENT")

const readEnvironmentFile = () => {
  try {
    return readFileSync(environmentPath, "utf8").replace(/^\uFEFF/, "")
  } catch (error) {
    if (error?.code === "ENOENT") return ""
    throw error
  }
}

const parseEnvironment = (text) => {
  const values = {}
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/)
    if (!match) continue
    let value = match[2]
    if (value.length >= 2 && (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    )) value = value.slice(1, -1)
    else value = value.replace(/\s+#.*$/, "").trim()
    values[match[1]] = value
  }
  return values
}

const originalEnvironment = readEnvironmentFile()
const fileEnvironment = parseEnvironment(originalEnvironment)
const setting = (key, fallback = "") => (
  Object.prototype.hasOwnProperty.call(fileEnvironment, key)
    ? fileEnvironment[key]
    : (process.env[key] || fallback)
)
let accountId = setting("CLOUDFLARE_ACCOUNT_ID")
const apiToken = setting("CLOUDFLARE_API_TOKEN")
const freePlanConfirmed = setting("CLOUDFLARE_WORKERS_FREE_CONFIRMED").toLowerCase() === "true"
let updateSecret = setting("PINOKIO_STABLE_SHARE_UPDATE_SECRET")
const workerName = setting("PINOKIO_STABLE_SHARE_WORKER_NAME", "maestro-stable-share")
let namespaceId = setting("CLOUDFLARE_KV_NAMESPACE_ID")
let stableUrl = ""

if (!freePlanConfirmed) {
  throw new Error("Confirm Workers Free/no paid subscription, then set CLOUDFLARE_WORKERS_FREE_CONFIRMED=true")
}
if (!updateSecret) updateSecret = randomBytes(32).toString("hex")
if (new TextEncoder().encode(updateSecret).length < 32) {
  throw new Error("PINOKIO_STABLE_SHARE_UPDATE_SECRET must contain at least 32 bytes")
}
if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(workerName)) {
  throw new Error("PINOKIO_STABLE_SHARE_WORKER_NAME must be a lowercase Worker name")
}

const temporary = mkdtempSync(join(tmpdir(), "maestro-worker-"))
const configPath = join(temporary, "wrangler.jsonc")
const childEnvironment = { ...process.env }
if (apiToken) childEnvironment.CLOUDFLARE_API_TOKEN = apiToken
else delete childEnvironment.CLOUDFLARE_API_TOKEN
const wrangler = (args, options = {}) => spawnSync(
  "npx", ["--yes", "wrangler@4", ...args],
  { cwd: here, encoding: "utf8", env: childEnvironment, ...options },
)
const commandOutput = (result) => `${result.stdout || ""}\n${result.stderr || ""}`
const oauthLane = !apiToken
let oauthAuthenticated = false
let oauthLogoutVerified = false

const replaceEnvironmentValues = (updates) => {
  const currentEnvironment = readEnvironmentFile()
  const remaining = new Map(Object.entries(updates))
  const lines = currentEnvironment.split(/\r?\n/)
  const rendered = []
  for (const line of lines) {
    const match = line.match(/^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=).*/)
    if (!match || !remaining.has(match[2])) {
      rendered.push(line)
      continue
    }
    rendered.push(`${match[1]}${match[2]}${match[3]}${remaining.get(match[2])}`)
    remaining.delete(match[2])
  }
  while (rendered.length && rendered[rendered.length - 1] === "") rendered.pop()
  for (const [key, value] of remaining) rendered.push(`${key}=${value}`)
  const next = `${rendered.join("\n")}\n`
  const temporaryEnvironment = join(
    repository,
    `ENVIRONMENT.${process.pid}.${randomBytes(6).toString("hex")}.tmp`,
  )
  try {
    writeFileSync(temporaryEnvironment, next, { encoding: "utf8", mode: 0o600, flag: "wx" })
    chmodSync(temporaryEnvironment, 0o600)
    renameSync(temporaryEnvironment, environmentPath)
    chmodSync(environmentPath, 0o600)
  } finally {
    try { rmSync(temporaryEnvironment, { force: true }) } catch {}
  }
}

const baseConfig = () => ({
  name: workerName,
  main: join(here, "worker.mjs"),
  compatibility_date: "2026-08-01",
  workers_dev: true,
  observability: { enabled: false },
  ...(namespaceId ? {
    kv_namespaces: [{ binding: "MAESTRO_TARGETS", id: namespaceId }],
  } : {}),
})

try {
  if (oauthLane) {
    const identity = wrangler(["whoami", "--json"])
    if (identity.status !== 0) {
      throw new Error("Wrangler OAuth is not authenticated; run `npx wrangler@4 login` first")
    }
    oauthAuthenticated = true
    const authenticatedAccount = extractWhoamiAccountId(commandOutput(identity), accountId)
    if (!authenticatedAccount) {
      throw new Error("Wrangler OAuth did not report exactly one matching account id")
    }
    if (accountId && accountId.toLowerCase() !== authenticatedAccount.toLowerCase()) {
      throw new Error("CLOUDFLARE_ACCOUNT_ID does not match the Wrangler OAuth account")
    }
    accountId = authenticatedAccount
  }
  if (!/^[a-f0-9]{32}$/i.test(accountId)) {
    throw new Error("Set CLOUDFLARE_ACCOUNT_ID in ENVIRONMENT")
  }
  childEnvironment.CLOUDFLARE_ACCOUNT_ID = accountId

  writeFileSync(configPath, JSON.stringify(baseConfig(), null, 2), { mode: 0o600 })
  if (!/^[a-f0-9]{32}$/i.test(namespaceId)) {
    const created = wrangler([
      "kv", "namespace", "create", "MAESTRO_TARGETS", "--config", configPath,
    ])
    if (created.status !== 0) throw new Error("Cloudflare KV namespace creation failed")
    namespaceId = extractNamespaceId(commandOutput(created))
    if (!namespaceId) throw new Error("Cloudflare created KV, but its namespace id could not be parsed")
    writeFileSync(configPath, JSON.stringify(baseConfig(), null, 2), { mode: 0o600 })
  }
  replaceEnvironmentValues({
    CLOUDFLARE_ACCOUNT_ID: accountId,
    CLOUDFLARE_API_TOKEN: "",
    CLOUDFLARE_KV_NAMESPACE_ID: namespaceId,
    PINOKIO_STABLE_SHARE_UPDATE_SECRET: updateSecret,
    PINOKIO_STABLE_SHARE_WORKER_NAME: workerName,
  })

  const deployed = wrangler(["deploy", "--config", configPath])
  if (deployed.status !== 0) throw new Error("Cloudflare Worker deployment failed")
  stableUrl = commandOutput(deployed).match(
    /https:\/\/[a-z0-9-]+(?:\.[a-z0-9-]+)+\.workers\.dev\b/i,
  )?.[0]?.toLowerCase() || ""
  if (!stableUrl) throw new Error("Cloudflare deployed the Worker, but its workers.dev URL was not found")

  const secretResult = wrangler(
    ["secret", "put", "UPDATE_TOKEN", "--config", configPath],
    { input: `${updateSecret}\n`, stdio: ["pipe", "ignore", "pipe"] },
  )
  if (secretResult.status !== 0) throw new Error("Cloudflare Worker secret upload failed")

  replaceEnvironmentValues({
    CLOUDFLARE_ACCOUNT_ID: accountId,
    CLOUDFLARE_KV_NAMESPACE_ID: namespaceId,
    PINOKIO_STABLE_SHARE_UPDATE_SECRET: updateSecret,
    PINOKIO_STABLE_SHARE_URL: stableUrl,
    PINOKIO_STABLE_SHARE_WORKER_NAME: workerName,
  })

  if (oauthLane) {
    const loggedOut = wrangler(["logout"])
    if (loggedOut.status !== 0) throw new Error("Worker deployed, but Wrangler OAuth logout failed")
    const verification = wrangler(["whoami", "--json"])
    if (verification.status === 0 || !isWhoamiLoggedOut(commandOutput(verification))) {
      throw new Error("Worker deployed, but Wrangler OAuth still appears authenticated")
    }
    oauthLogoutVerified = true
  }

  process.stdout.write(`Worker deployed at ${stableUrl}; logging is disabled.\n`)
  process.stdout.write("ENVIRONMENT was updated atomically with mode 0600; no secret was printed and the API token was removed.\n")
  if (oauthLane) process.stdout.write("Wrangler OAuth logout was verified.\n")
} finally {
  if (oauthAuthenticated && !oauthLogoutVerified) {
    const logoutRetry = wrangler(["logout"])
    const verification = wrangler(["whoami", "--json"])
    if (
      logoutRetry.status !== 0
      || verification.status === 0
      || !isWhoamiLoggedOut(commandOutput(verification))
    ) {
      process.stderr.write("Wrangler OAuth cleanup could not be verified; run `npx wrangler@4 logout` now.\n")
      process.exitCode = 1
    }
  }
  rmSync(temporary, { recursive: true, force: true })
}
