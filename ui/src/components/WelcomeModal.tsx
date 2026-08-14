import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  ArrowRight,
  Clapperboard,
  Check,
  Cuboid,
  Download,
  FolderLock,
  Gauge,
  HardDrive,
  ListRestart,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  WandSparkles,
  X,
} from 'lucide-react'
import { useStore } from '../stores/useStore'
import { PRODUCT_NAME, PRODUCT_NAME_VISUAL, PRODUCT_PROVENANCE } from '../lib/branding'
import { CHANGELOG_MANIFEST, CURRENT_RELEASE } from '../lib/changelog'
import { closeModalIfTop, installModalFocus } from '../lib/modalFocus'

const SEEN_KEY = 'maestro_welcome_seen_v1'

/**
 * One-time product orientation shown after access and project bootstrap.
 */
export function WelcomeModal() {
  const accessContext = useStore(state => state.accessContext)
  const activeWorkspace = useStore(state => state.activeWorkspace)
  const setSidebarMode = useStore(state => state.setSidebarMode)
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const startButtonRef = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(SEEN_KEY) !== '1'
    } catch {
      return true
    }
  })

  const dismiss = useCallback(() => {
    try { localStorage.setItem(SEEN_KEY, '1') } catch { /* storage may be blocked */ }
    setOpen(false)
  }, [])

  const enterStudio = useCallback(() => {
    setSidebarMode('studio')
    dismiss()
  }, [dismiss, setSidebarMode])

  useEffect(() => {
    if (!open || !dialogRef.current || !startButtonRef.current) return
    const restoreFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: startButtonRef.current,
      restoreFocus,
      appRoot: document.getElementById('root'),
      onClose: dismiss,
      priority: 120,
    })
  }, [dismiss, open])

  if (!open) return null

  const accessLabel = accessContext?.remote
    ? `Remote project · ${activeWorkspace}`
    : 'Local access · on this computer'
  const accessDetail = accessContext?.remote
    ? 'This browser can open only the projects you unlock. Settings for this computer stay on the computer.'
    : accessContext?.cloudflare_enabled
      ? 'Your local studio keeps working even if its Cloudflare share link is unavailable.'
      : `Open ${PRODUCT_NAME} from Pinokio for the most direct, dependable local connection.`

  return createPortal(
    <div
      className="fixed inset-0 z-[120] flex h-[100vh] items-center justify-center overflow-hidden supports-[height:100dvh]:h-[100dvh]"
      style={{
        paddingTop: 'max(0.75rem, env(safe-area-inset-top))',
        paddingRight: 'max(0.75rem, env(safe-area-inset-right))',
        paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))',
        paddingLeft: 'max(0.75rem, env(safe-area-inset-left))',
      }}
    >
      <button
        type="button"
        tabIndex={-1}
        aria-label={`Close welcome to ${PRODUCT_NAME}`}
        className="absolute inset-0 appearance-none border-0 bg-black/70 p-0"
        onClick={() => closeModalIfTop(document, dialogRef.current, dismiss)}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative flex min-h-0 max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-bg-secondary shadow-2xl"
      >
        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain [-webkit-overflow-scrolling:touch]">
        <div className="relative overflow-hidden border-b border-border px-5 pb-5 pt-6 sm:px-7 sm:pb-6 sm:pt-7">
          <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-accent-blue/15 blur-3xl" />
          <div className="relative flex items-start gap-3 sm:gap-4">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-gradient-toggle-from to-gradient-toggle-to text-xl font-bold text-white shadow-lg sm:h-14 sm:w-14">
              M
            </div>
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-accent-blue">
                <Sparkles size={12} /> Your creative workspace
              </div>
              <h2
                id={titleId}
                aria-label={`Welcome to ${PRODUCT_NAME}`}
                className="text-xl font-semibold tracking-tight text-text-primary sm:text-2xl"
              >
                <span aria-hidden="true">Welcome to {PRODUCT_NAME_VISUAL}</span>
              </h2>
              <p className="mt-0.5 text-[9px] font-medium text-text-muted sm:text-[10px]">{PRODUCT_PROVENANCE}</p>
              <p id={descriptionId} className="mt-1 max-w-2xl text-xs leading-relaxed text-text-secondary sm:text-sm">
                Turn an idea into images, video, audio, and connected scenes while keeping every step in one place.
              </p>
            </div>
            <button
              type="button"
              onClick={() => closeModalIfTop(document, dialogRef.current, dismiss)}
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:p-1.5"
              aria-label={`Close welcome to ${PRODUCT_NAME}`}
            >
              <X size={17} aria-hidden="true" />
            </button>
          </div>
          <div className="relative mt-4 rounded-xl border border-accent-blue/25 bg-accent-blue/10 px-3 py-2.5 sm:flex sm:items-center sm:gap-3">
            <div className="flex shrink-0 items-center gap-2 text-xs font-medium text-text-primary">
              <HardDrive size={14} className="text-accent-blue" /> {accessLabel}
            </div>
            <p className="mt-1 text-[10px] leading-relaxed text-text-muted sm:mt-0 sm:border-l sm:border-accent-blue/25 sm:pl-3 sm:text-[11px]">
              {accessDetail}
            </p>
          </div>
        </div>

        <div className="grid gap-2.5 px-5 pt-5 sm:grid-cols-2 sm:px-7 sm:pt-6">
          <ModeCard icon={<WandSparkles size={17} />} title="Studio" eyebrow="Make and refine">
            Generate images, video, and audio, then restyle, extend, retake, and edit without leaving the workspace.
          </ModeCard>
          <ModeCard icon={<Clapperboard size={17} />} title="Director" eyebrow="Build connected work">
            Turn a brief or song into a planned sequence, then guide clips from first frame through final assembly.
          </ModeCard>
          <ModeCard icon={<MessageSquare size={17} />} title="Chat" eyebrow="Develop ideas">
            {accessContext?.remote
              ? `Develop concepts and production plans with the writing assistant chosen on the computer running ${PRODUCT_NAME}.`
              : 'Develop concepts and production plans with the writing assistant you choose in Settings.'}
          </ModeCard>
          <ModeCard icon={<FolderLock size={17} />} title="Projects" eyebrow="Keep context together">
            Keep outputs, references, remote access, and share links organized by project. Preview blur is a separate privacy choice.
          </ModeCard>
          <div className="grid gap-2.5 sm:col-span-2 sm:grid-cols-3">
            <FeaturePoint icon={<Gauge size={15} />} title="Supported generation controls">
              For supported models, choose a speed or quality profile, review the suggested shot plan, and adjust it before generation.
            </FeaturePoint>
            <FeaturePoint icon={<ListRestart size={15} />} title="Queue and resume">
              Queue multiple generations, hold work, and resume supported queued jobs.
            </FeaturePoint>
            <FeaturePoint icon={<Cuboid size={15} />} title="Use Blender scenes">
              Use Blender scene context to guide camera, subject, and object motion.
            </FeaturePoint>
          </div>
        </div>

        <div className="px-5 py-5 sm:px-7 sm:py-6">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-xs font-semibold text-text-primary">What's new in Continuum</h3>
            <span className="rounded-full bg-accent-blue/15 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-accent-blue">
              v{CURRENT_RELEASE.version}
            </span>
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">{CURRENT_RELEASE.summary}</p>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2" aria-label={`Continuum ${CURRENT_RELEASE.version} release highlights`}>
            {CURRENT_RELEASE.highlights.map(highlight => (
              <li key={highlight.id} className="flex gap-2 rounded-lg border border-border/80 bg-bg-primary/45 px-3 py-2.5">
                <Check size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-accent-green" />
                <div>
                  <h4 className="text-[10px] font-semibold text-text-primary">{highlight.title}</h4>
                  <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">{highlight.summary}</p>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-3 border-t border-border/80 pt-3">
            <h3 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">Why Continuum</h3>
            <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Why Continuum">
              {CHANGELOG_MANIFEST.whyContinuum.map(highlight => (
                <li key={highlight.id} className="rounded-full border border-border bg-bg-tertiary/55 px-2 py-1 text-[9px] font-medium text-text-secondary">
                  {highlight.title}
                </li>
              ))}
            </ul>
          </div>
          <p className="mt-2 text-[9px] leading-relaxed text-text-muted">You can reopen this guide and browse earlier releases from What's new in the header.</p>
        </div>
        </div>

        <div className="sticky bottom-0 max-h-[55%] overflow-y-auto overscroll-contain shrink-0 border-t border-border bg-bg-secondary px-5 py-4 pb-[max(1rem,env(safe-area-inset-bottom))] [-webkit-overflow-scrolling:touch] sm:flex sm:max-h-none sm:items-center sm:justify-between sm:gap-5 sm:overflow-visible sm:px-7">
          <div className="space-y-1.5 text-[10px] leading-relaxed text-text-muted sm:max-w-lg sm:text-[11px]">
            <div className="flex items-start gap-2">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-accent-green" />
              <span>Private previews start blurred until you reveal them. Project access controls who can open the project; blur controls what appears in this browser.</span>
            </div>
            <div className="flex items-start gap-2">
              <Download size={14} className="mt-0.5 shrink-0 text-accent-blue" />
              <span>If needed, the computer running {PRODUCT_NAME} downloads and prepares model files in a shared storage area. Approved local and remote users can reuse them; project access and private-preview settings still apply.</span>
            </div>
          </div>
          <button
            ref={startButtonRef}
            type="button"
            onClick={enterStudio}
            className="mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-gradient-cta-from to-gradient-cta-to px-5 py-2.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue sm:mt-0 sm:w-auto"
          >
            Enter the studio <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function FeaturePoint({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 rounded-lg border border-border/80 bg-bg-primary/45 px-3 py-2.5">
      <div className="mt-0.5 shrink-0 text-accent-blue">{icon}</div>
      <div>
        <h3 className="text-[10px] font-semibold text-text-primary">{title}</h3>
        <p className="mt-0.5 text-[10px] leading-relaxed text-text-muted">{children}</p>
      </div>
    </div>
  )
}

function ModeCard({ icon, title, eyebrow, children }: { icon: React.ReactNode; title: string; eyebrow: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-bg-tertiary/55 p-3.5" title={title}>
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-blue/12 text-accent-blue">
          {icon}
        </div>
        <div>
          <p className="text-[9px] font-medium uppercase tracking-wider text-text-muted">{eyebrow}</p>
          <h3 className="text-xs font-semibold text-text-primary">{title}</h3>
        </div>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
        {children}
      </p>
    </div>
  )
}
