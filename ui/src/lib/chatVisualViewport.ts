export interface ChatVisualViewportGeometry {
  height: number
  offsetTop: number
}

interface ChatViewportElement {
  getBoundingClientRect: () => Pick<DOMRect, 'top'>
  previousElementSibling?: Element | null
}

interface ChatViewportWindow {
  visualViewport: VisualViewport | null
  requestAnimationFrame: (callback: FrameRequestCallback) => number
  cancelAnimationFrame: (handle: number) => void
  addEventListener: Window['addEventListener']
  removeEventListener: Window['removeEventListener']
  ResizeObserver?: typeof ResizeObserver
}

export function chatVisibleHeight(
  viewport: ChatVisualViewportGeometry,
  elementTop: number,
): number {
  if (
    !Number.isFinite(viewport.height)
    || !Number.isFinite(viewport.offsetTop)
    || !Number.isFinite(elementTop)
  ) return 0
  const viewportTop = Math.max(0, viewport.offsetTop)
  const viewportBottom = viewportTop + Math.max(0, viewport.height)
  return Math.max(0, Math.floor(viewportBottom - Math.max(elementTop, viewportTop)))
}

export function observeChatVisualViewport(
  element: ChatViewportElement,
  onHeight: (height: number | null) => void,
  windowRef: ChatViewportWindow = window,
): () => void {
  let frame: number | null = null
  let stopped = false

  const update = () => {
    frame = null
    if (stopped) return
    const viewport = windowRef.visualViewport
    onHeight(viewport
      ? chatVisibleHeight(viewport, element.getBoundingClientRect().top)
      : null)
  }
  const schedule = () => {
    if (stopped || frame !== null) return
    frame = windowRef.requestAnimationFrame(update)
  }

  const viewport = windowRef.visualViewport
  const layoutObserver = windowRef.ResizeObserver
    ? new windowRef.ResizeObserver(schedule)
    : null
  viewport?.addEventListener('resize', schedule)
  viewport?.addEventListener('scroll', schedule)
  windowRef.addEventListener('resize', schedule)
  windowRef.addEventListener('orientationchange', schedule)
  layoutObserver?.observe(element.previousElementSibling ?? element as Element)
  schedule()

  return () => {
    stopped = true
    viewport?.removeEventListener('resize', schedule)
    viewport?.removeEventListener('scroll', schedule)
    windowRef.removeEventListener('resize', schedule)
    windowRef.removeEventListener('orientationchange', schedule)
    layoutObserver?.disconnect()
    if (frame !== null) windowRef.cancelAnimationFrame(frame)
    frame = null
  }
}
