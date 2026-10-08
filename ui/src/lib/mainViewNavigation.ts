export const OPEN_QUEUE_VIEW_EVENT = 'maestro:open-queue'
let queueViewPending = false

export function requestQueueView(): void {
  queueViewPending = true
  window.dispatchEvent(new Event(OPEN_QUEUE_VIEW_EVENT))
}

export function subscribeQueueView(listener: () => void): () => void {
  const open = () => {
    queueViewPending = false
    listener()
  }
  window.addEventListener(OPEN_QUEUE_VIEW_EVENT, open)
  if (queueViewPending) open()
  return () => window.removeEventListener(OPEN_QUEUE_VIEW_EVENT, open)
}
