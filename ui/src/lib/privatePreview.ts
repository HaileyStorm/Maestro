const PRIVATE_REVEAL_SESSION_PREFIX = 'maestro.private-preview-revealed.'
const PRIVATE_REVEAL_CHANGE_EVENT = 'maestro:private-preview-reveal-change'

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

export function privatePreviewWasRevealed(identity: string): boolean {
  try {
    return sessionStorage.getItem(privateRevealStorageKey(identity)) === '1'
  } catch {
    return false
  }
}

export function revealPrivatePreview(identity: string): void {
  try { sessionStorage.setItem(privateRevealStorageKey(identity), '1') } catch { /* session storage unavailable */ }
  window.dispatchEvent(new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT, { detail: identity }))
}

export function hidePrivatePreview(identity: string): void {
  try { sessionStorage.removeItem(privateRevealStorageKey(identity)) } catch { /* session storage unavailable */ }
  window.dispatchEvent(new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT, { detail: identity }))
}

export function subscribePrivatePreviewReveal(
  identity: string,
  listener: (revealed: boolean) => void,
): () => void {
  return subscribePrivatePreviewChanges((changedIdentity, revealed) => {
    if (changedIdentity !== identity) return
    listener(revealed)
  })
}

export function subscribePrivatePreviewChanges(
  listener: (identity: string, revealed: boolean) => void,
): () => void {
  const handleChange = (event: Event) => {
    const identity = (event as CustomEvent<string>).detail
    listener(identity, privatePreviewWasRevealed(identity))
  }
  window.addEventListener(PRIVATE_REVEAL_CHANGE_EVENT, handleChange)
  return () => window.removeEventListener(PRIVATE_REVEAL_CHANGE_EVENT, handleChange)
}
