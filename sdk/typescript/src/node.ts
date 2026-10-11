/** Node-only adapter for the private Netscape jar produced by human login. */
import { constants } from 'node:fs'
import { open } from 'node:fs/promises'
import { Cookie, CookieJar } from 'tough-cookie'
import { MaestroClient } from './index.js'

async function loadSession(baseUrl: string, cookieFile: string): Promise<CookieJar> {
  const origin = new URL(baseUrl)
  const jar = new CookieJar()
  let text: string
  try {
    const file = await open(cookieFile, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
    try {
      const stat = await file.stat()
      if (!stat.isFile() || stat.size > 65536 || (process.platform !== 'win32' &&
          ((stat.mode & 0o077) !== 0 || stat.uid !== process.getuid?.()))) throw new Error()
      const bytes = Buffer.alloc(65537)
      let length = 0
      while (length < bytes.length) {
        const read = await file.read(bytes, length, bytes.length - length, null)
        if (!read.bytesRead) break
        length += read.bytesRead
      }
      if (length > 65536) throw new Error()
      text = bytes.subarray(0, length).toString('utf8')
    } finally { await file.close() }
  } catch { throw new Error('Choose a readable, owner-private regular cookie file; no session was imported') }
  try {
    if (!text.startsWith('# Netscape HTTP Cookie File')) throw new Error()
    let imported = 0
    for (let line of text.split(/\r?\n/)) {
      const httpOnly = line.startsWith('#HttpOnly_')
      if (httpOnly) line = line.slice(10)
      else if (!line || line.startsWith('#')) continue
      const fields = line.split('\t')
      if (fields.length !== 7) throw new Error()
      const [domain, includeSubdomains, path, secure, expiry, key, value] = fields
      if (!['maestro_session', 'maestro_account_session'].includes(key)) continue
      // Python's CookieJar uses localhost.local for the single-label localhost.
      const host = domain.replace(/^\./, '').toLowerCase()
      if (host !== origin.hostname.toLowerCase() &&
          !(origin.hostname === 'localhost' && host === 'localhost.local')) throw new Error()
      if (!['TRUE', 'FALSE'].includes(includeSubdomains) || !['TRUE', 'FALSE'].includes(secure) ||
          !path.startsWith('/') || /[\x00-\x20\x7f;]/.test(value) ||
          (expiry !== '' && !/^\d+$/.test(expiry))) throw new Error()
      const seconds = Number(expiry)
      if (seconds && (!Number.isSafeInteger(seconds) || seconds * 1000 > 8.64e15)) throw new Error()
      const cookie = new Cookie({ key, value, path, secure: secure === 'TRUE', httpOnly,
        // Restrict this explicit client to the configured host, even for a domain jar.
        expires: seconds ? new Date(seconds * 1000) : 'Infinity' })
      await jar.setCookie(cookie, origin.origin)
      if (++imported > 2) throw new Error()
    }
    if (!imported) throw new Error()
  } catch { throw new Error('The file is not a matching Maestro login jar; use human login for this origin') }
  return jar
}

/** Cookies remain in memory; the operator file is never written or exported. */
export async function createNodeClient(baseUrl: string, options: {
  cookieFile: string; origin?: string; timeoutMs?: number
}): Promise<MaestroClient> {
  // Reuse the ordinary client's URL and origin validation before reading a file.
  const client = new MaestroClient(baseUrl, options)
  const jar = await loadSession(client.baseUrl, options.cookieFile)
  const expectedOrigin = new URL(client.baseUrl).origin
  const authenticatedFetch: typeof fetch = async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input))
    if (url.origin !== expectedOrigin) throw new Error('Session requests must stay on the configured Maestro origin')
    const headers = new Headers(init?.headers)
    const cookies = await jar.getCookieString(url.href)
    if (cookies) headers.set('Cookie', cookies)
    const response = await fetch(input, { ...init, headers, redirect: 'error' })
    // RFC cookie updates stay in this process; re-login remains a human action.
    for (const value of response.headers.getSetCookie()) {
      const cookie = Cookie.parse(value)
      if (cookie && ['maestro_session', 'maestro_account_session'].includes(cookie.key)) {
        try { await jar.setCookie(cookie, url.href) }
        catch { throw new Error('The server session update could not be accepted') }
      }
    }
    return response
  }
  return new MaestroClient(client.baseUrl, { ...options, fetch: authenticatedFetch })
}
