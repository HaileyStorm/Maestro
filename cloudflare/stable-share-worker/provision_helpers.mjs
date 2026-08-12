const ACCOUNT_ID = /^[a-f0-9]{32}$/i
const MAX_WHOAMI_JSON_LENGTH = 64 * 1024
const MAX_WHOAMI_ACCOUNTS = 100

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
