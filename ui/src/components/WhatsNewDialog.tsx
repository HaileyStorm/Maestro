import { useCallback, useEffect, useId, useRef, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronRight, History, Megaphone, X } from 'lucide-react'
import { PRODUCT_NAME } from '../lib/branding'
import { CHANGELOG_MANIFEST, CURRENT_RELEASE, type PublicReleaseNote } from '../lib/changelog'
import { closeModalIfTop, installModalFocus } from '../lib/modalFocus'
import { PerformanceHistoryChart } from './PerformanceHistoryChart'

const whatsNewListeners = new Set<() => void>()
let whatsNewOpen = false
let whatsNewRestoreFocus: HTMLElement | null = null

function subscribeWhatsNew(listener: () => void) {
  whatsNewListeners.add(listener)
  return () => whatsNewListeners.delete(listener)
}

function setWhatsNewOpen(open: boolean) {
  if (whatsNewOpen === open) return
  whatsNewOpen = open
  for (const listener of whatsNewListeners) listener()
}

function getWhatsNewOpen() {
  return whatsNewOpen
}

function resolveWhatsNewTrigger(
  document: Document,
  fallback: HTMLElement | null,
): HTMLElement | null {
  const mobile = document.defaultView?.matchMedia?.('(max-width: 767px)').matches === true
  const expected = document.querySelector<HTMLElement>(
    `[data-responsive-dialog-trigger="whats-new:${mobile ? 'mobile' : 'desktop'}"]`,
  )
  if (expected && expected.isConnected !== false) return expected
  if (fallback && fallback.isConnected !== false) return fallback
  const replacement = document.querySelector<HTMLElement>('[data-responsive-dialog-trigger^="whats-new:"]')
  return replacement && replacement.isConnected !== false ? replacement : null
}

export function WhatsNewButton({ compact = false }: { compact?: boolean }) {
  const open = useSyncExternalStore(subscribeWhatsNew, getWhatsNewOpen, getWhatsNewOpen)
  const triggerRef = useRef<HTMLButtonElement>(null)

  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={() => {
        whatsNewRestoreFocus = triggerRef.current
        setWhatsNewOpen(true)
      }}
      data-responsive-dialog-trigger={`whats-new:${compact ? 'mobile' : 'desktop'}`}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={`What's new in ${PRODUCT_NAME} v${CHANGELOG_MANIFEST.currentVersion}`}
      className={`flex shrink-0 items-center justify-center gap-1 rounded-md border border-border/80 text-[9px] font-medium text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
        compact ? 'h-11 w-11 p-0' : 'px-1.5 py-1'
      }`}
    >
      <Megaphone size={compact ? 13 : 11} aria-hidden="true" />
      {!compact && <span>What's new</span>}
    </button>
  )
}

export function WhatsNewDialogHost() {
  const open = useSyncExternalStore(subscribeWhatsNew, getWhatsNewOpen, getWhatsNewOpen)
  const focusReturnRef = useRef<HTMLSpanElement>(null)
  const closeDialog = useCallback(() => setWhatsNewOpen(false), [])

  return (
    <>
      <span
        ref={focusReturnRef}
        tabIndex={-1}
        className="fixed h-px w-px overflow-hidden opacity-0 pointer-events-none"
        data-responsive-dialog-focus-return="whats-new"
        onFocus={() => resolveWhatsNewTrigger(document, whatsNewRestoreFocus)?.focus()}
      />
      {open && <WhatsNewDialog onClose={closeDialog} restoreFocusRef={focusReturnRef} />}
    </>
  )
}

function WhatsNewDialog({
  onClose,
  restoreFocusRef,
}: {
  onClose: () => void
  restoreFocusRef: React.RefObject<HTMLElement | null>
}) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const requestClose = useCallback(() => {
    closeModalIfTop(document, dialogRef.current, onClose)
  }, [onClose])

  useEffect(() => {
    if (!dialogRef.current || !closeRef.current) return
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeRef.current,
      restoreFocus: restoreFocusRef.current,
      appRoot: document.getElementById('root'),
      onClose,
    })
  }, [onClose, restoreFocusRef])

  const continuumArchive = CHANGELOG_MANIFEST.releases.filter(
    release => release.lineage === 'continuum' && release.version !== CHANGELOG_MANIFEST.currentVersion,
  )
  const maestroArchive = CHANGELOG_MANIFEST.releases.filter(release => release.lineage === 'maestro-base')

  return createPortal(
    <div
      className="fixed inset-0 z-[160] flex items-center justify-center p-3 sm:p-6"
      style={{
        paddingTop: 'max(0.75rem, env(safe-area-inset-top))',
        paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))',
      }}
    >
      <div aria-hidden="true" className="absolute inset-0 bg-black/75" onClick={requestClose} />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative flex max-h-[calc(100dvh-1.5rem)] w-full max-w-2xl min-h-0 flex-col overflow-hidden rounded-2xl border border-border bg-bg-secondary shadow-2xl sm:max-h-[92vh]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-border px-5 py-4 sm:px-6">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-blue/15 text-accent-blue">
            <Megaphone size={18} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-semibold text-text-primary">What's new in {PRODUCT_NAME}</h2>
            <p id={descriptionId} className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
              Public, UI-bundled notes only. No project, runtime, or machine data is read.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={requestClose}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg p-0 text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:h-auto md:w-auto md:p-1.5"
            aria-label="Close what's new"
          >
            <X size={17} />
          </button>
        </header>

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-5 py-5 [-webkit-overflow-scrolling:touch] sm:px-6">
          <section aria-labelledby={`${titleId}-current`}>
            <div className="flex flex-wrap items-center gap-2">
              <h3 id={`${titleId}-current`} className="text-sm font-semibold text-text-primary">Continuum v{CURRENT_RELEASE.version}</h3>
              <span className="rounded-full bg-accent-blue/15 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-accent-blue">Current build</span>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-text-secondary">{CURRENT_RELEASE.summary}</p>
            <h4 className="mt-4 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">Shipped in this Continuum build</h4>
            <ul className="mt-2 space-y-2" aria-label={`Continuum ${CURRENT_RELEASE.version} release highlights`}>
              {CURRENT_RELEASE.highlights.map(highlight => (
                <li key={highlight.id} className="flex gap-2 rounded-lg border border-border/80 bg-bg-primary/40 px-3 py-2.5">
                  <Check size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-accent-green" />
                  <div>
                    <p className="text-[11px] font-semibold text-text-primary">{highlight.title}</p>
                    <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">{highlight.summary}</p>
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <section className="mt-5 border-t border-border pt-5" aria-labelledby={`${titleId}-why`}>
            <h3 id={`${titleId}-why`} className="text-sm font-semibold text-text-primary">Why Continuum</h3>
            <p className="mt-1 text-[10px] leading-relaxed text-text-muted">The all-time product view, kept separate from this build's release delta.</p>
            <ul className="mt-2 space-y-2" aria-label="Why Continuum">
              {CHANGELOG_MANIFEST.whyContinuum.map(highlight => (
                <li key={highlight.id} className="rounded-lg border border-border/70 bg-bg-tertiary/25 px-3 py-2">
                  <p className="text-[10px] font-semibold text-text-primary">{highlight.title}</p>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">{highlight.summary}</p>
                </li>
              ))}
            </ul>
          </section>

          <PerformanceHistoryChart />

          <details className="group mt-5 rounded-xl border border-border bg-bg-tertiary/35">
            <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3.5 py-3 text-xs font-semibold text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue md:min-h-0 [&::-webkit-details-marker]:hidden">
              <History size={14} aria-hidden="true" className="text-accent-blue" />
              All release history
              <ChevronRight size={14} aria-hidden="true" className="ml-auto transition-transform group-open:rotate-90" />
            </summary>
            <div className="space-y-5 border-t border-border px-3.5 py-4">
              <ReleaseGroup title="Continuum history" releases={continuumArchive} />
              <ReleaseGroup title="Maestro base archive" releases={maestroArchive} />
              <p className="rounded-lg border border-border/70 bg-bg-primary/40 px-3 py-2 text-[10px] leading-relaxed text-text-muted">
                {CHANGELOG_MANIFEST.lineageNote} WanGP details remain in its upstream history and are not presented as Continuum releases.
              </p>
            </div>
          </details>
        </div>

        <footer className="shrink-0 border-t border-border bg-bg-secondary px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
          <button
            type="button"
            onClick={requestClose}
            className="min-h-11 w-full rounded-lg bg-bg-active px-4 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary md:min-h-0"
          >
            Done
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  )
}

function ReleaseGroup({ title, releases }: { title: string; releases: readonly PublicReleaseNote[] }) {
  return (
    <section>
      <h4 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">{title}</h4>
      <div className="mt-2 space-y-3">
        {releases.map(release => (
          <article key={`${release.lineage}-${release.version}`} className="rounded-lg border border-border/80 bg-bg-primary/35 p-3">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <h5 className="text-[11px] font-semibold text-text-primary">v{release.version} · {release.label}</h5>
              <ReleaseProvenance release={release} />
            </div>
            <p className="mt-1.5 text-[10px] leading-relaxed text-text-muted">{release.summary}</p>
            {release.highlights.length > 0 && (
              <ul className="mt-2 space-y-1 text-[10px] leading-relaxed text-text-muted">
                {release.highlights.map(highlight => <li key={highlight.id}>• <span className="font-medium text-text-secondary">{highlight.title}:</span> {highlight.summary}</li>)}
              </ul>
            )}
          </article>
        ))}
      </div>
    </section>
  )
}

function ReleaseProvenance({ release }: { release: PublicReleaseNote }) {
  if (release.provenance.kind === 'git-tag') {
    return <span className="text-[9px] text-text-muted">tag {release.provenance.tag} · {release.provenance.date}</span>
  }
  if (release.provenance.kind === 'bundled-snapshot') {
    return <span title={release.provenance.note} className="text-[9px] text-text-muted">untagged bundled snapshot</span>
  }
  return <span className="text-[9px] text-text-muted">Continuum build · no release link</span>
}
