const ACCOUNT_ID = /^[a-f0-9]{32}$/i
const DIGEST = /^[a-f0-9]{64}$/
const VERSION_ID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const WORKER_NAME = /^[a-z0-9][a-z0-9-]{0,62}$/
const MAX_WHOAMI_JSON_LENGTH = 64 * 1024
const MAX_WHOAMI_ACCOUNTS = 100
const MAX_DEPLOYMENT_JSON_LENGTH = 64 * 1024
const CANDIDATE_SCHEMA_VERSION = 1
const CANDIDATE_KEYS = [
  "accountId",
  "configSha256",
  "namespaceId",
  "previewUrl",
  "schemaVersion",
  "sourceSha256",
  "stableUrl",
  "updateTokenSha256",
  "versionId",
  "workerName",
]

const hasDuplicateObjectKeys = (source) => {
  const stack = []
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index]
    if (character === '"') {
      const start = index
      for (index += 1; index < source.length; index += 1) {
        if (source[index] === "\\") index += 1
        else if (source[index] === '"') break
      }
      const current = stack[stack.length - 1]
      if (current?.type === "object" && current.expectingKey) {
        const key = JSON.parse(source.slice(start, index + 1))
        if (current.keys.has(key)) return true
        current.keys.add(key)
        current.expectingKey = false
      }
    } else if (character === "{") {
      stack.push({ type: "object", keys: new Set(), expectingKey: true })
    } else if (character === "[") {
      stack.push({ type: "array" })
    } else if (character === "}" || character === "]") {
      stack.pop()
    } else if (character === ",") {
      const current = stack[stack.length - 1]
      if (current?.type === "object") current.expectingKey = true
    }
  }
  return false
}

const parseWhoamiJson = (text) => {
  const raw = String(text)
  if (!raw || raw.length > MAX_WHOAMI_JSON_LENGTH) return null
  const source = raw.trim()
  if (!source) return null
  try {
    const parsed = JSON.parse(source)
    if (hasDuplicateObjectKeys(source)) return null
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

const accountIds = (parsed) => {
  if (parsed.loggedIn !== true || !Array.isArray(parsed.accounts)) return null
  const accounts = parsed.accounts
  if (!accounts.length || accounts.length > MAX_WHOAMI_ACCOUNTS) return null

  const ids = new Set()
  for (const account of accounts) {
    if (!account || typeof account !== "object" || Array.isArray(account)) return null
    if (typeof account.id !== "string" || !ACCOUNT_ID.test(account.id)) return null
    ids.add(account.id.toLowerCase())
  }
  return ids
}

export const extractWhoamiAccountId = (text, expectedAccountId = "") => {
  const parsed = parseWhoamiJson(text)
  const ids = parsed ? accountIds(parsed) : null
  if (!ids) return ""

  if (ids.size === 1) return [...ids][0]
  if (expectedAccountId) {
    if (!ACCOUNT_ID.test(expectedAccountId)) return ""
    const expected = expectedAccountId.toLowerCase()
    return ids.has(expected) ? expected : ""
  }
  return ""
}

export const isWhoamiLoggedOut = (text) => {
  const parsed = parseWhoamiJson(text)
  return parsed?.loggedIn === false && Object.keys(parsed).length === 1
}

export const extractNamespaceId = (text) => (
  String(text).match(/["']?id["']?\s*[:=]\s*["']([a-f0-9]{32})["']/i)?.[1]
  || ""
)

export const parseProvisionArgs = (args) => {
  if (!Array.isArray(args)) throw new Error("Provision arguments must be an array")
  if (args.length === 0 || (args.length === 1 && args[0] === "--stage")) {
    return { phase: "stage", versionId: "" }
  }
  if (args.length === 2 && args[0] === "--promote" && VERSION_ID.test(args[1])) {
    return { phase: "promote", versionId: args[1].toLowerCase() }
  }
  throw new Error("Use `node provision.mjs --stage` or `node provision.mjs --promote <version-id>`")
}

export const canonicalizeManagedEnvironment = (text, updates, managedKeys) => {
  if (!updates || typeof updates !== "object" || Array.isArray(updates)) {
    throw new Error("Environment updates must be an object")
  }
  if (
    !Array.isArray(managedKeys)
    || !managedKeys.length
    || new Set(managedKeys).size !== managedKeys.length
    || managedKeys.some((key) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key))
  ) throw new Error("Managed environment keys must be unique names")
  const managed = new Set(managedKeys)
  if (Object.keys(updates).some((key) => !managed.has(key))) {
    throw new Error("Environment update included an unmanaged key")
  }

  const values = {}
  const rendered = []
  for (const line of String(text).replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/)
    if (!match || !managed.has(match[1])) {
      rendered.push(line)
      continue
    }
    let value = match[2]
    if (value.length >= 2 && (
      (value.startsWith('"') && value.endsWith('"'))
      || (value.startsWith("'") && value.endsWith("'"))
    )) value = value.slice(1, -1)
    else value = value.replace(/\s+#.*$/, "").trim()
    values[match[1]] = value
  }
  while (rendered.length && rendered[rendered.length - 1] === "") rendered.pop()
  for (const key of managedKeys) {
    const value = Object.prototype.hasOwnProperty.call(updates, key)
      ? updates[key]
      : (values[key] || "")
    rendered.push(`${key}=${value}`)
  }
  return `${rendered.join("\n")}\n`
}

export const parseDeploymentReadback = (text, candidateVersionId = "") => {
  const raw = String(text)
  if (!raw || raw.length > MAX_DEPLOYMENT_JSON_LENGTH) return null
  const source = raw.trim()
  if (!source || hasDuplicateObjectKeys(source)) return null
  let parsed
  try {
    parsed = JSON.parse(source)
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null
  if (!Array.isArray(parsed.versions) || !parsed.versions.length || parsed.versions.length > 2) {
    return null
  }
  const versions = []
  let total = 0
  for (const item of parsed.versions) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null
    if (!VERSION_ID.test(item.version_id)) return null
    if (typeof item.percentage !== "number" || !Number.isFinite(item.percentage)) return null
    if (item.percentage < 0 || item.percentage > 100) return null
    total += item.percentage
    versions.push({
      versionId: item.version_id.toLowerCase(),
      percentage: item.percentage,
    })
  }
  if (Math.abs(total - 100) > 0.001) return null
  const requested = candidateVersionId && VERSION_ID.test(candidateVersionId)
    ? candidateVersionId.toLowerCase()
    : ""
  return {
    active: Boolean(
      requested
      && versions.length === 1
      && versions[0].versionId === requested
      && versions[0].percentage === 100
    ),
    versions,
  }
}

const canonicalWorkersUrl = (value) => {
  try {
    const parsed = new URL(String(value))
    if (
      parsed.protocol !== "https:"
      || parsed.username
      || parsed.password
      || parsed.port
      || (parsed.pathname !== "/" && parsed.pathname !== "")
      || parsed.search
      || parsed.hash
      || !parsed.hostname.endsWith(".workers.dev")
    ) return ""
    return `https://${parsed.hostname.toLowerCase()}`
  } catch {
    return ""
  }
}

export const extractVersionUpload = (text, workerName) => {
  if (!WORKER_NAME.test(workerName)) return null
  const raw = String(text)
  const versionIds = [...raw.matchAll(
    /Worker Version ID:\s*([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b/gi,
  )].map((match) => match[1].toLowerCase())
  const previewUrls = [...raw.matchAll(
    /Version Preview URL:\s*(https:\/\/[^\s]+)/gi,
  )].map((match) => canonicalWorkersUrl(match[1])).filter(Boolean)
  if (new Set(versionIds).size !== 1 || new Set(previewUrls).size !== 1) return null

  const versionId = versionIds[0]
  const previewUrl = previewUrls[0]
  const preview = new URL(previewUrl)
  const prefix = `${versionId.slice(0, 8)}-${workerName}.`
  if (!preview.hostname.startsWith(prefix)) return null
  const subdomain = preview.hostname.slice(prefix.length)
  if (!subdomain || subdomain === "workers.dev" || !subdomain.endsWith(".workers.dev")) {
    return null
  }
  return {
    versionId,
    previewUrl,
    stableUrl: `https://${workerName}.${subdomain}`,
  }
}

const validateCandidate = (candidate) => {
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null
  if (Object.keys(candidate).sort().join("\n") !== CANDIDATE_KEYS.join("\n")) return null
  if (candidate.schemaVersion !== CANDIDATE_SCHEMA_VERSION) return null
  if (!ACCOUNT_ID.test(candidate.accountId) || !ACCOUNT_ID.test(candidate.namespaceId)) return null
  if (!WORKER_NAME.test(candidate.workerName) || !VERSION_ID.test(candidate.versionId)) return null
  if (
    !DIGEST.test(candidate.sourceSha256)
    || !DIGEST.test(candidate.configSha256)
    || !DIGEST.test(candidate.updateTokenSha256)
  ) return null
  const upload = extractVersionUpload(
    `Worker Version ID: ${candidate.versionId}\nVersion Preview URL: ${candidate.previewUrl}`,
    candidate.workerName,
  )
  if (!upload || upload.stableUrl !== candidate.stableUrl) return null
  return {
    schemaVersion: CANDIDATE_SCHEMA_VERSION,
    accountId: candidate.accountId.toLowerCase(),
    workerName: candidate.workerName,
    namespaceId: candidate.namespaceId.toLowerCase(),
    versionId: upload.versionId,
    previewUrl: upload.previewUrl,
    stableUrl: upload.stableUrl,
    sourceSha256: candidate.sourceSha256.toLowerCase(),
    configSha256: candidate.configSha256.toLowerCase(),
    updateTokenSha256: candidate.updateTokenSha256.toLowerCase(),
  }
}

export const encodeCandidateMetadata = (candidate) => {
  const validated = validateCandidate(candidate)
  if (!validated) throw new Error("Invalid staged Worker candidate metadata")
  return Buffer.from(JSON.stringify(validated), "utf8").toString("base64url")
}

export const parseCandidateMetadata = (encoded) => {
  const raw = String(encoded)
  if (!raw || raw.length > 8 * 1024 || !/^[A-Za-z0-9_-]+$/.test(raw)) return null
  try {
    const source = Buffer.from(raw, "base64url").toString("utf8")
    if (!source || hasDuplicateObjectKeys(source)) return null
    return validateCandidate(JSON.parse(source))
  } catch {
    return null
  }
}

export const candidateMatches = (candidate, expected) => {
  const validatedCandidate = validateCandidate(candidate)
  const validatedExpected = validateCandidate(expected)
  if (!validatedCandidate || !validatedExpected) return false
  return CANDIDATE_KEYS.every((key) => validatedCandidate[key] === validatedExpected[key])
}
