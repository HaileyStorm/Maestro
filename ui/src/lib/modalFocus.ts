export const MODAL_FOCUSABLE_SELECTOR = 'button:not([disabled]), summary, [href], [tabindex]:not([tabindex="-1"])'

interface ModalFocusOptions {
  readonly document: Document
  readonly dialog: HTMLElement
  readonly initialFocus: HTMLElement
  readonly restoreFocus: HTMLElement | null
  readonly appRoot: HTMLElement | null
  readonly onClose: () => void
}

/** Install the shared DOM behavior required while a portalled modal is open. */
export function installModalFocus({
  document,
  dialog,
  initialFocus,
  restoreFocus,
  appRoot,
  onClose,
}: ModalFocusOptions): () => void {
  const rootWasInert = appRoot?.hasAttribute('inert') ?? false
  const previousOverflow = document.body.style.overflow
  appRoot?.setAttribute('inert', '')
  document.body.style.overflow = 'hidden'
  initialFocus.focus()

  const handleKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(MODAL_FOCUSABLE_SELECTOR))
    if (focusable.length === 0) return
    const first = focusable[0]!
    const last = focusable[focusable.length - 1]!
    if (!dialog.contains(document.activeElement)) {
      event.preventDefault()
      const wrapTarget = event.shiftKey ? last : first
      wrapTarget.focus()
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  document.addEventListener('keydown', handleKeyDown)
  return () => {
    document.removeEventListener('keydown', handleKeyDown)
    document.body.style.overflow = previousOverflow
    if (!rootWasInert) appRoot?.removeAttribute('inert')
    restoreFocus?.focus()
  }
}
