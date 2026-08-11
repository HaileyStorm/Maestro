export const OPEN_QUEUE_VIEW_EVENT = 'maestro:open-queue'

export function requestQueueView(): void {
  window.dispatchEvent(new Event(OPEN_QUEUE_VIEW_EVENT))
}

export function subscribeQueueView(listener: () => void): () => void {
  window.addEventListener(OPEN_QUEUE_VIEW_EVENT, listener)
  return () => window.removeEventListener(OPEN_QUEUE_VIEW_EVENT, listener)
}
