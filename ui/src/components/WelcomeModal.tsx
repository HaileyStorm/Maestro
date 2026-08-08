import { useCallback, useEffect, useId, useRef, useState } from 'react'
import {
  ArrowRight,
  Clapperboard,
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

const SEEN_KEY = 'maestro_welcome_seen_v1'

/**
 * One-time product orientation shown after access and project bootstrap.
 */
export function WelcomeModal() {
  const accessContext = useStore(state => state.accessContext)
  const activeWorkspace = useStore(state => state.activeWorkspace)
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

  useEffect(() => {
    if (!open) return
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    startButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        dismiss()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ))
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
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
      previousFocus?.focus()
    }
  }, [dismiss, open])

  if (!open) return null

  const accessLabel = accessContext?.remote
    ? `Remote access · ${activeWorkspace}`
    : 'Local studio · this machine is home'
  const accessDetail = accessContext?.remote
    ? 'This browser works only inside projects you explicitly unlock. Machine settings stay on the host.'
    : accessContext?.cloudflare_enabled
      ? 'Your local studio keeps working independently of its Cloudflare share address.'
      : 'Open Maestro from Pinokio for the most direct, dependable local connection.'

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 p-3 sm:p-6" onClick={dismiss}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="w-full max-w-3xl max-h-[92vh] overflow-y-auto rounded-2xl border border-border bg-bg-secondary shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
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
              <h2 id={titleId} className="text-xl font-semibold tracking-tight text-text-primary sm:text-2xl">
                Welcome to Maestro
              </h2>
              <p id={descriptionId} className="mt-1 max-w-2xl text-xs leading-relaxed text-text-secondary sm:text-sm">
                Move from a single idea to finished images, video, audio, and connected stories—without losing sight of the work between.
              </p>
            </div>
            <button onClick={dismiss} className="shrink-0 rounded-lg p-1.5 text-text-muted hover:bg-bg-hover hover:text-text-primary" aria-label="Close welcome to Maestro">
              <X size={17} />
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

        <div className="grid gap-2.5 px-5 py-5 sm:grid-cols-2 sm:px-7 sm:py-6">
          <ModeCard icon={<WandSparkles size={17} />} title="Studio" eyebrow="Make and refine">
            Generate images, video, and audio, then restyle, extend, retake, and edit without leaving the workspace.
          </ModeCard>
          <ModeCard icon={<Clapperboard size={17} />} title="Director" eyebrow="Build connected work">
            Turn a brief or song into a planned sequence, then guide clips from first frame through final assembly.
          </ModeCard>
          <ModeCard icon={<MessageSquare size={17} />} title="Chat" eyebrow="Think with your tools">
            {accessContext?.remote
              ? 'Develop concepts and production plans with the language-model service configured on the Maestro host.'
              : 'Develop concepts and production plans with the language-model service you choose in Settings.'}
          </ModeCard>
          <ModeCard icon={<FolderLock size={17} />} title="Projects" eyebrow="Keep context together">
            Isolate outputs, references, Cloudflare sessions, and share links. Password access remains separate from private-preview blur.
          </ModeCard>
          <div className="grid gap-2.5 sm:col-span-2 sm:grid-cols-3">
            <FeaturePoint icon={<Gauge size={15} />} title="H3 control">
              Pick performance and delivery profiles, then review or override the adaptive segment plan before generation.
            </FeaturePoint>
            <FeaturePoint icon={<ListRestart size={15} />} title="Queue + resume">
              Queue multiple generations, hold work, and resume supported queued jobs.
            </FeaturePoint>
            <FeaturePoint icon={<Cuboid size={15} />} title="Blender guidance">
              Use Blender scene context to guide camera, subject, and object motion.
            </FeaturePoint>
          </div>
        </div>

        <div className="border-t border-border px-5 py-4 sm:flex sm:items-center sm:justify-between sm:gap-5 sm:px-7">
          <div className="space-y-1.5 text-[10px] leading-relaxed text-text-muted sm:max-w-lg sm:text-[11px]">
            <div className="flex items-start gap-2">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-accent-green" />
              <span>Private outputs start blurred and reveal only when you choose. Project access controls which remote browsers can enter—not whether a preview is blurred.</span>
            </div>
            <div className="flex items-start gap-2">
              <Download size={14} className="mt-0.5 shrink-0 text-accent-blue" />
              <span>If needed, this Maestro host downloads and prepares model files in a shared cache. Allowed local and remote users reuse that host cache; project access and private-preview rules still apply.</span>
            </div>
          </div>
          <button
            ref={startButtonRef}
            onClick={dismiss}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-gradient-cta-from to-gradient-cta-to px-5 py-2.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue sm:mt-0 sm:w-auto"
          >
            Enter the studio <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
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
    <div className="rounded-xl border border-border bg-bg-tertiary/55 p-3.5">
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
