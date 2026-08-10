const PRIVATE_REVEAL_SESSION_PREFIX = 'maestro.private-preview-revealed.'
const PRIVATE_HIDDEN_SESSION_PREFIX = 'maestro.private-preview-hidden.'
const PRIVATE_PROJECT_REVEAL_SESSION_PREFIX = 'maestro.private-preview-project-revealed.'
const PRIVATE_REVEAL_CHANGE_EVENT = 'maestro:private-preview-reveal-change'

type PrivatePreviewChange = string | { workspace: string; revealed: boolean }

// sessionStorage can be disabled (or fill up) while the page is open. Keep a
// process-local mirror so preview controls still behave coherently for the
// remainder of that browser session without falling back to durable storage.
const memoryRevealed = new Map<string, boolean>()
const memoryHidden = new Map<string, boolean>()
const memoryProjectDefaults = new Map<string, boolean>()

export function privatePreviewIdentity(
  workspace: string,
  name: string,
  revision = '',
): string {
  return `${workspace}\u0000${name}\u0000${revision}`
}

function privateRevealStorageKey(identity: string): string {
  return PRIVATE_REVEAL_SESSION_PREFIX + encodeURIComponent(identity)
}

function privateHiddenStorageKey(identity: string): string {
  return PRIVATE_HIDDEN_SESSION_PREFIX + encodeURIComponent(identity)
}

function privateProjectRevealStorageKey(workspace: string): string {
  return PRIVATE_PROJECT_REVEAL_SESSION_PREFIX + encodeURIComponent(workspace)
}

function storedFlag(key: string, memoryOverride: boolean | undefined): boolean {
  if (memoryOverride !== undefined) return memoryOverride
  try {
    return sessionStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

export function privatePreviewWasRevealed(identity: string): boolean {
  if (storedFlag(privateHiddenStorageKey(identity), memoryHidden.get(identity))) return false
  if (storedFlag(privateRevealStorageKey(identity), memoryRevealed.get(identity))) return true
  const workspace = identityWorkspace(identity)
  return storedFlag(
    privateProjectRevealStorageKey(workspace),
    memoryProjectDefaults.get(workspace),
  )
}

export function privatePreviewWorkspaceHasRevealed(workspace: string): boolean {
  if (storedFlag(
    privateProjectRevealStorageKey(workspace),
    memoryProjectDefaults.get(workspace),
  )) return true
  if ([...memoryRevealed].some(([identity, revealed]) => (
    revealed && identityWorkspace(identity) === workspace
  ))) return true
  try {
    for (let index = 0; index < sessionStorage.length; index++) {
      const key = sessionStorage.key(index)
      if (!key?.startsWith(PRIVATE_REVEAL_SESSION_PREFIX)) continue
      const identity = decodeStoredIdentity(key, PRIVATE_REVEAL_SESSION_PREFIX)
      if (
        identity !== null
        && memoryRevealed.get(identity) !== false
        && identityWorkspace(identity) === workspace
      ) return true
    }
  } catch { /* in-memory state above remains authoritative when storage is unavailable */ }
  return false
}

export function revealPrivatePreview(identity: string): void {
  memoryHidden.set(identity, false)
  memoryRevealed.set(identity, true)
  try {
    sessionStorage.removeItem(privateHiddenStorageKey(identity))
    // Retain the legacy per-item reveal key for compatibility.
    sessionStorage.setItem(privateRevealStorageKey(identity), '1')
  } catch { /* session storage unavailable */ }
  window.dispatchEvent(new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT, { detail: identity }))
}

export function hidePrivatePreview(identity: string): void {
  const workspace = identityWorkspace(identity)
  memoryRevealed.set(identity, false)
  memoryHidden.set(identity, privatePreviewProjectDefaultIsRevealed(workspace))
  try {
    sessionStorage.removeItem(privateRevealStorageKey(identity))
    if (privatePreviewProjectDefaultIsRevealed(workspace)) {
      sessionStorage.setItem(privateHiddenStorageKey(identity), '1')
    } else {
      sessionStorage.removeItem(privateHiddenStorageKey(identity))
    }
  } catch { /* session storage unavailable */ }
  window.dispatchEvent(new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT, { detail: identity }))
}

export function setPrivatePreviewsForWorkspaceRevealed(
  workspace: string,
  revealed: boolean,
): void {
  clearWorkspaceOverrides(workspace)
  memoryProjectDefaults.set(workspace, revealed)
  try {
    const key = privateProjectRevealStorageKey(workspace)
    if (revealed) sessionStorage.setItem(key, '1')
    else sessionStorage.removeItem(key)
  } catch { /* session storage unavailable */ }
  window.dispatchEvent(new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT, {
    detail: { workspace, revealed },
  }))
}

export function hidePrivatePreviewsForWorkspace(workspace: string): void {
  setPrivatePreviewsForWorkspaceRevealed(workspace, false)
}

export function subscribePrivatePreviewReveal(
  identity: string,
  listener: (revealed: boolean) => void,
): () => void {
  return subscribePrivatePreviewChanges((changedIdentity, _revealed, changedWorkspace) => {
    if (changedIdentity === identity || (
      changedIdentity === null && changedWorkspace === identityWorkspace(identity)
    )) listener(privatePreviewWasRevealed(identity))
  })
}

export function subscribePrivatePreviewChanges(
  listener: (identity: string | null, revealed: boolean, workspace: string | null) => void,
): () => void {
  const handleChange = (event: Event) => {
    const detail = (event as CustomEvent<PrivatePreviewChange>).detail
    if (typeof detail === 'string') {
      listener(detail, privatePreviewWasRevealed(detail), identityWorkspace(detail))
      return
    }
    listener(null, detail.revealed, detail.workspace)
  }
  window.addEventListener(PRIVATE_REVEAL_CHANGE_EVENT, handleChange)
  return () => window.removeEventListener(PRIVATE_REVEAL_CHANGE_EVENT, handleChange)
}

function privatePreviewProjectDefaultIsRevealed(workspace: string): boolean {
  return storedFlag(
    privateProjectRevealStorageKey(workspace),
    memoryProjectDefaults.get(workspace),
  )
}

function clearWorkspaceOverrides(workspace: string): void {
  for (const identity of memoryRevealed.keys()) {
    if (identityWorkspace(identity) === workspace) memoryRevealed.set(identity, false)
  }
  for (const identity of memoryHidden.keys()) {
    if (identityWorkspace(identity) === workspace) memoryHidden.set(identity, false)
  }
  try {
    const keys: string[] = []
    for (let index = 0; index < sessionStorage.length; index++) {
      const key = sessionStorage.key(index)
      if (!key) continue
      const prefix = key.startsWith(PRIVATE_REVEAL_SESSION_PREFIX)
        ? PRIVATE_REVEAL_SESSION_PREFIX
        : key.startsWith(PRIVATE_HIDDEN_SESSION_PREFIX)
          ? PRIVATE_HIDDEN_SESSION_PREFIX
          : null
      if (prefix === null) continue
      const identity = decodeStoredIdentity(key, prefix)
      if (identity !== null && identityWorkspace(identity) === workspace) {
        if (prefix === PRIVATE_REVEAL_SESSION_PREFIX) memoryRevealed.set(identity, false)
        else memoryHidden.set(identity, false)
        keys.push(key)
      }
    }
    for (const key of keys) sessionStorage.removeItem(key)
  } catch { /* session storage unavailable */ }
}

function decodeStoredIdentity(key: string, prefix: string): string | null {
  try {
    return decodeURIComponent(key.slice(prefix.length))
  } catch {
    return null
  }
}

function identityWorkspace(identity: string): string {
  const delimiter = identity.indexOf('\u0000')
  return delimiter === -1 ? identity : identity.slice(0, delimiter)
}
