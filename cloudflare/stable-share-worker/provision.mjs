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
import { createHash, randomBytes } from "node:crypto"
import { spawnSync } from "node:child_process"

import {
  candidateMatches,
  canonicalizeManagedEnvironment,
  encodeCandidateMetadata,
  extractNamespaceId,
  extractVersionUpload,
  extractWhoamiAccountId,
  isWhoamiLoggedOut,
  parseCandidateMetadata,
  parseDeploymentReadback,
  parseProvisionArgs,
} from "./provision_helpers.mjs"

const here = dirname(fileURLToPath(import.meta.url))
const repository = dirname(dirname(here))
const environmentPath = join(repository, "ENVIRONMENT")
const workerPath = join(here, "worker.mjs")
const action = parseProvisionArgs(process.argv.slice(2))
const managedEnvironmentKeys = [
  "CLOUDFLARE_ACCOUNT_ID",
  "CLOUDFLARE_API_TOKEN",
  "CLOUDFLARE_KV_NAMESPACE_ID",
  "CLOUDFLARE_WORKERS_FREE_CONFIRMED",
  "PINOKIO_STABLE_SHARE_CANDIDATE",
  "PINOKIO_STABLE_SHARE_UPDATE_SECRET",
  "PINOKIO_STABLE_SHARE_URL",
  "PINOKIO_STABLE_SHARE_WORKER_NAME",
  "SHARE_MODE",
]

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
const shareMode = (setting("SHARE_MODE") || "proxy").trim().toLowerCase()
let namespaceId = setting("CLOUDFLARE_KV_NAMESPACE_ID")
const encodedCandidate = setting("PINOKIO_STABLE_SHARE_CANDIDATE")

if (!freePlanConfirmed) {
  throw new Error("Confirm Workers Free/no paid subscription, then set CLOUDFLARE_WORKERS_FREE_CONFIRMED=true")
}
if (!updateSecret && action.phase === "stage") updateSecret = randomBytes(32).toString("hex")
if (!updateSecret && action.phase === "promote") {
  throw new Error("PINOKIO_STABLE_SHARE_UPDATE_SECRET must be preserved from staging")
}
if (new TextEncoder().encode(updateSecret).length < 32) {
  throw new Error("PINOKIO_STABLE_SHARE_UPDATE_SECRET must contain at least 32 bytes")
}
if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(workerName)) {
  throw new Error("PINOKIO_STABLE_SHARE_WORKER_NAME must be a lowercase Worker name")
}
if (shareMode !== "proxy" && shareMode !== "redirect") {
  throw new Error("SHARE_MODE must be exactly proxy or redirect")
}

const temporary = mkdtempSync(join(tmpdir(), "maestro-worker-"))
const configPath = join(temporary, "wrangler.jsonc")
const secretsPath = join(temporary, "secrets.json")
const childEnvironment = { ...process.env }
if (apiToken) childEnvironment.CLOUDFLARE_API_TOKEN = apiToken
else delete childEnvironment.CLOUDFLARE_API_TOKEN
delete childEnvironment.PINOKIO_STABLE_SHARE_UPDATE_SECRET
delete childEnvironment.PINOKIO_STABLE_SHARE_CANDIDATE
delete childEnvironment.UPDATE_TOKEN
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
  const effectiveUpdates = { ...updates, SHARE_MODE: shareMode }
  const next = canonicalizeManagedEnvironment(
    currentEnvironment,
    effectiveUpdates,
    managedEnvironmentKeys,
  )
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
  main: workerPath,
  compatibility_date: "2026-08-01",
  workers_dev: true,
  observability: { enabled: false },
  vars: { SHARE_MODE: shareMode },
  ...(namespaceId ? {
    kv_namespaces: [{ binding: "MAESTRO_TARGETS", id: namespaceId }],
  } : {}),
})

const sha256 = (value) => createHash("sha256").update(value).digest("hex")
const writeConfig = () => {
  const text = JSON.stringify(baseConfig(), null, 2)
  writeFileSync(configPath, text, { encoding: "utf8", mode: 0o600 })
  chmodSync(configPath, 0o600)
  return { text, digest: sha256(text) }
}
const readDeployment = () => wrangler([
  "deployments", "status", "--json", "--config", configPath,
])

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

  if (action.phase === "promote" && !/^[a-f0-9]{32}$/i.test(namespaceId)) {
    throw new Error("Promotion requires the exact KV namespace recorded during staging")
  }
  writeConfig()
  const baselineResult = readDeployment()
  const baseline = baselineResult.status === 0
    ? parseDeploymentReadback(
      baselineResult.stdout,
      action.phase === "promote" ? action.versionId : "",
    )
    : null
  if (!baseline) {
    throw new Error(
      "This provisioner requires an existing Worker deployment with readable production state; use the documented fresh-Worker prerequisite first",
    )
  }
  if (!/^[a-f0-9]{32}$/i.test(namespaceId)) {
    const created = wrangler([
      "kv", "namespace", "create", "MAESTRO_TARGETS", "--config", configPath,
    ])
    if (created.status !== 0) throw new Error("Cloudflare KV namespace creation failed")
    namespaceId = extractNamespaceId(commandOutput(created))
    if (!namespaceId) throw new Error("Cloudflare created KV, but its namespace id could not be parsed")
    replaceEnvironmentValues({
      CLOUDFLARE_ACCOUNT_ID: accountId,
      CLOUDFLARE_KV_NAMESPACE_ID: namespaceId,
      PINOKIO_STABLE_SHARE_UPDATE_SECRET: updateSecret,
      PINOKIO_STABLE_SHARE_WORKER_NAME: workerName,
    })
    writeConfig()
  }
  const config = writeConfig()
  const sourceSha256 = sha256(readFileSync(workerPath))
  const updateTokenSha256 = sha256(Buffer.from(updateSecret, "utf8"))

  if (action.phase === "stage") {
    writeFileSync(
      secretsPath,
      JSON.stringify({ UPDATE_TOKEN: updateSecret }),
      { encoding: "utf8", mode: 0o600, flag: "wx" },
    )
    chmodSync(secretsPath, 0o600)
    const uploaded = wrangler([
      "versions", "upload", "--config", configPath, "--secrets-file", secretsPath,
    ])
    if (uploaded.status !== 0) throw new Error("Cloudflare Worker candidate upload failed")
    const upload = extractVersionUpload(commandOutput(uploaded), workerName)
    if (!upload) {
      throw new Error("Cloudflare uploaded a candidate, but its exact Version ID and preview URL were not found")
    }
    const candidate = {
      schemaVersion: 1,
      accountId,
      workerName,
      namespaceId,
      versionId: upload.versionId,
      previewUrl: upload.previewUrl,
      stableUrl: upload.stableUrl,
      sourceSha256,
      configSha256: config.digest,
      updateTokenSha256,
    }
    replaceEnvironmentValues({
      CLOUDFLARE_ACCOUNT_ID: accountId,
      CLOUDFLARE_KV_NAMESPACE_ID: namespaceId,
      PINOKIO_STABLE_SHARE_UPDATE_SECRET: updateSecret,
      PINOKIO_STABLE_SHARE_WORKER_NAME: workerName,
      PINOKIO_STABLE_SHARE_CANDIDATE: encodeCandidateMetadata(candidate),
    })
    process.stdout.write(`Worker candidate ${upload.versionId} staged with no production traffic.\n`)
    process.stdout.write(`Review ${upload.previewUrl}, then promote that exact Version ID explicitly.\n`)
  } else {
    const candidate = parseCandidateMetadata(encodedCandidate)
    if (!candidate || candidate.versionId !== action.versionId) {
      throw new Error("Promotion requires the exact Version ID recorded by the most recent successful staging run")
    }
    const expected = {
      ...candidate,
      accountId,
      workerName,
      namespaceId,
      sourceSha256,
      configSha256: config.digest,
      updateTokenSha256,
    }
    if (!candidateMatches(candidate, expected)) {
      throw new Error("Staged candidate no longer matches this account, Worker, KV binding, source, config, or update secret")
    }
    let reconciled = baseline.active
    let promotionStatus = 0
    if (!reconciled) {
      const promoted = wrangler([
        "versions", "deploy", `${candidate.versionId}@100%`, "--config", configPath, "-y",
      ])
      promotionStatus = promoted.status
      const readbackResult = readDeployment()
      const readback = readbackResult.status === 0
        ? parseDeploymentReadback(readbackResult.stdout, candidate.versionId)
        : null
      if (!readback) {
        throw new Error(
          "Worker promotion outcome is ambiguous because production readback failed; candidate metadata and stable URL were preserved for reconciliation",
        )
      }
      if (!readback.active) {
        throw new Error(
          "Production readback does not show the candidate at 100%; candidate metadata and stable URL were preserved for reconciliation",
        )
      }
      reconciled = true
    }
    if (!reconciled) throw new Error("Worker candidate promotion was not confirmed")
    replaceEnvironmentValues({
      CLOUDFLARE_ACCOUNT_ID: accountId,
      CLOUDFLARE_API_TOKEN: "",
      CLOUDFLARE_KV_NAMESPACE_ID: namespaceId,
      PINOKIO_STABLE_SHARE_UPDATE_SECRET: updateSecret,
      PINOKIO_STABLE_SHARE_URL: candidate.stableUrl,
      PINOKIO_STABLE_SHARE_WORKER_NAME: workerName,
      PINOKIO_STABLE_SHARE_CANDIDATE: "",
    })
    const detail = baseline.active
      ? "already confirmed active by production readback"
      : (promotionStatus === 0 ? "confirmed by production readback" : "reconciled active after a nonzero deploy result")
    process.stdout.write(`Worker candidate ${candidate.versionId} is at 100% traffic (${detail}) at ${candidate.stableUrl}.\n`)
  }

  if (oauthLane) {
    const loggedOut = wrangler(["logout"])
    if (loggedOut.status !== 0) throw new Error("Worker operation finished, but Wrangler OAuth logout failed")
    const verification = wrangler(["whoami", "--json"])
    if (verification.status === 0 || !isWhoamiLoggedOut(commandOutput(verification))) {
      throw new Error("Worker operation finished, but Wrangler OAuth still appears authenticated")
    }
    oauthLogoutVerified = true
  }

  process.stdout.write("ENVIRONMENT was updated atomically with mode 0600; no secret was printed.\n")
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
