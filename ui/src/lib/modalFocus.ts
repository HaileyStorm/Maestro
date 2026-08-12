export const MODAL_FOCUSABLE_SELECTOR = 'button:not([disabled]), summary, [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

interface ModalFocusOptions {
  readonly document: Document
  readonly dialog: HTMLElement
  readonly initialFocus: HTMLElement
  readonly restoreFocus: HTMLElement | null
  readonly appRoot: HTMLElement | null
  readonly onClose: () => void
  readonly priority?: number
}

interface ModalEntry {
  readonly dialog: HTMLElement
  readonly onClose: () => void
  readonly priority: number
  readonly sequence: number
  restoreFocus: HTMLElement | null
  parent: ModalEntry | null
  covered: boolean
}

interface InertLock {
  count: number
  readonly wasInert: boolean
}

interface ModalState {
  readonly document: Document
  readonly entries: ModalEntry[]
  readonly inertLocks: Map<HTMLElement, InertLock>
  sequence: number
  listening: boolean
  previousOverflow: string
  readonly handleKeyDown: (event: KeyboardEvent) => void
}

const modalStates = new WeakMap<Document, ModalState>()

function topEntry(state: ModalState): ModalEntry | undefined {
  return state.entries[state.entries.length - 1]
}

function acquireInert(state: ModalState, element: HTMLElement | null) {
  if (!element) return
  const existing = state.inertLocks.get(element)
  if (existing) {
    existing.count += 1
    return
  }
  state.inertLocks.set(element, {
    count: 1,
    wasInert: element.hasAttribute('inert'),
  })
  element.setAttribute('inert', '')
}

function releaseInert(state: ModalState, element: HTMLElement | null) {
  if (!element) return
  const lock = state.inertLocks.get(element)
  if (!lock) return
  lock.count -= 1
  if (lock.count > 0) return
  state.inertLocks.delete(element)
  const independentlyHidden = element.hasAttribute('hidden')
    || element.getAttribute?.('aria-hidden') === 'true'
  if (!lock.wasInert && !independentlyHidden) element.removeAttribute('inert')
}

function isFocusableNow(document: Document, element: HTMLElement): boolean {
  if (element.hasAttribute('hidden') || element.getAttribute?.('aria-hidden') === 'true') return false
  if ((element as HTMLButtonElement).disabled) return false
  if (element.closest?.('[inert], [hidden], [aria-hidden="true"], fieldset[disabled]')) return false
  const view = document.defaultView
  if (!view?.getComputedStyle) return true
  const style = view.getComputedStyle(element)
  return style.display !== 'none' && style.visibility !== 'hidden'
}

function syncCoveredDialogs(state: ModalState) {
  const top = topEntry(state)
  for (const entry of state.entries) {
    const shouldBeCovered = entry !== top
    if (entry.covered === shouldBeCovered) continue
    entry.covered = shouldBeCovered
    if (shouldBeCovered) acquireInert(state, entry.dialog)
    else releaseInert(state, entry.dialog)
  }
}

function syncModalParents(state: ModalState) {
  for (let index = 0; index < state.entries.length; index += 1) {
    state.entries[index]!.parent = state.entries[index - 1] ?? null
  }
}

function getModalState(document: Document): ModalState {
  const existing = modalStates.get(document)
  if (existing) return existing

  const state = {} as ModalState
  Object.assign(state, {
    document,
    entries: [],
    inertLocks: new Map<HTMLElement, InertLock>(),
    sequence: 0,
    listening: false,
    previousOverflow: '',
    handleKeyDown: (event: KeyboardEvent) => {
      const entry = topEntry(state)
      if (!entry) return
      if (event.key === 'Escape') {
        event.preventDefault()
        entry.onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(entry.dialog.querySelectorAll<HTMLElement>(MODAL_FOCUSABLE_SELECTOR))
        .filter(element => isFocusableNow(document, element))
      if (focusable.length === 0) return
      const first = focusable[0]!
      const last = focusable[focusable.length - 1]!
      if (!entry.dialog.contains(document.activeElement)) {
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
    },
  })
  modalStates.set(document, state)
  return state
}

/** Request a backdrop/control dismissal without allowing a covered modal to close. */
export function closeModalIfTop(
  document: Document,
  dialog: HTMLElement | null,
  onClose: () => void,
): boolean {
  const state = modalStates.get(document)
  if (!state || state.entries.length === 0) {
    onClose()
    return true
  }
  if (!dialog || topEntry(state)?.dialog !== dialog) return false
  onClose()
  return true
}

/** Install the shared DOM behavior required while a portalled modal is open. */
export function installModalFocus({
  document,
  dialog,
  initialFocus,
  restoreFocus,
  appRoot,
  onClose,
  priority = 100,
}: ModalFocusOptions): () => void {
  const state = getModalState(document)
  const entry: ModalEntry = {
    dialog,
    onClose,
    priority,
    sequence: state.sequence++,
    restoreFocus,
    parent: null,
    covered: false,
  }
  if (state.entries.length === 0) {
    state.previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  acquireInert(state, appRoot)
  state.entries.push(entry)
  state.entries.sort((left, right) => (
    left.priority - right.priority || left.sequence - right.sequence
  ))
  syncModalParents(state)
  syncCoveredDialogs(state)
  if (!state.listening) {
    document.addEventListener('keydown', state.handleKeyDown)
    state.listening = true
  }
  if (topEntry(state) === entry) initialFocus.focus()

  let installed = true
  return () => {
    if (!installed) return
    installed = false
    const wasTop = topEntry(state) === entry
    const entryIndex = state.entries.indexOf(entry)
    const childEntry = entryIndex >= 0 ? state.entries[entryIndex + 1] : undefined
    if (
      childEntry?.parent === entry
      && (
        childEntry.restoreFocus === null
        || entry.dialog.contains(childEntry.restoreFocus)
        || childEntry.restoreFocus.isConnected === false
      )
    ) {
      childEntry.restoreFocus = entry.restoreFocus
    }
    if (entryIndex >= 0) state.entries.splice(entryIndex, 1)
    syncModalParents(state)
    if (entry.covered) releaseInert(state, entry.dialog)
    releaseInert(state, appRoot)
    syncCoveredDialogs(state)
    if (state.entries.length === 0) {
      document.removeEventListener('keydown', state.handleKeyDown)
      state.listening = false
      document.body.style.overflow = state.previousOverflow
    }
    if (wasTop) entry.restoreFocus?.focus()
  }
}
