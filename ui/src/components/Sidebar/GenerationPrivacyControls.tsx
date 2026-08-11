import { useEffect } from 'react'
import { EyeOff, Flame } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { HOST_TERM_NOTICES } from '../../lib/hostTerms'

export function GenerationPrivacyControls() {
  const explicitOutput = useStore(s => s.explicitOutput)
  const setExplicitOutput = useStore(s => s.setExplicitOutput)
  const privateOutput = useStore(s => s.privateOutput)
  const setPrivateOutput = useStore(s => s.setPrivateOutput)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const loadHostTerms = useStore(s => s.loadHostTerms)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)
  const llmProvider = useStore(s => s.servicesConfig?.llm_provider || 'local')
  const baseH3Selected = useStore(s => (
    s.sidebarMode === 'director'
      ? s.selectedModelPerMode.video === 'minimax_h3'
      : s.params.model_type === 'minimax_h3'
  ))

  useEffect(() => {
    if (activeWorkspace && !hostTerms && !hostTermsLoading) void loadHostTerms()
  }, [activeWorkspace, hostTerms, hostTermsLoading, loadHostTerms])

  const lawfulUseAccepted = hostTerms?.lawful_use.accepted === true

  return (
    <div className="px-4 py-2 border-b border-border bg-bg-tertiary/50">
      <div className="grid grid-cols-2 gap-2">
        <label
          className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px] transition-colors ${
            explicitOutput
              ? 'border-red-500/50 bg-red-500/15 text-red-300'
              : 'border-border bg-bg-secondary text-text-secondary hover:text-text-primary'
          }`}
          title="Mark this Generate or Director job as explicit"
        >
          <input
            type="checkbox"
            checked={explicitOutput}
            onChange={event => setExplicitOutput(event.target.checked)}
            className="sr-only"
          />
          <Flame size={12} /> Explicit {explicitOutput ? 'On' : 'Off'}
        </label>
        <label
          className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px] transition-colors ${
            privateOutput
              ? 'border-violet-500/50 bg-violet-500/15 text-violet-200'
              : 'border-border bg-bg-secondary text-text-secondary hover:text-text-primary'
          }`}
          title="Blur this output's gallery preview until deliberately revealed in this browser session"
        >
          <input
            type="checkbox"
            checked={privateOutput}
            onChange={event => setPrivateOutput(event.target.checked)}
            className="sr-only"
          />
          <EyeOff size={12} /> Private {privateOutput ? 'On' : 'Off'}
        </label>
      </div>
      {explicitOutput && privateOutput && (
        <p className="mt-1 text-center text-[9px] text-text-muted">
          Explicit jobs start with a blurred Private preview. You can deliberately change it.
        </p>
      )}
      {explicitOutput && !privateOutput && (
        <p className="mt-1 text-center text-[9px] text-amber-400">
          This explicit output's gallery preview will not be blurred automatically.
        </p>
      )}
      {explicitOutput && baseH3Selected && (
        <p className="mt-1 text-center text-[9px] text-amber-400">
          Base may be less reliable for explicit intent.
        </p>
      )}
      {explicitOutput && hostTerms && !lawfulUseAccepted && (
        <div className="mt-1.5 flex flex-col items-stretch gap-2 rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-[9px] leading-relaxed text-text-muted sm:flex-row sm:items-start">
          <span className="flex-1">
            {HOST_TERM_NOTICES.lawful_use.text} Notice v{HOST_TERM_NOTICES.lawful_use.version}.
          </span>
          <button
            type="button"
            disabled={hostTermsLoading}
            onClick={() => { void acceptHostTerm('lawful_use') }}
            className="w-full shrink-0 rounded border border-amber-400/50 px-2 py-1 text-amber-300 hover:bg-amber-500/10 disabled:opacity-50 sm:w-auto sm:px-1.5 sm:py-0.5"
          >
            Accept for this host
          </button>
        </div>
      )}
      {explicitOutput && hostTermsError && (
        <p className="mt-1 text-center text-[9px] text-red-300">{hostTermsError}</p>
      )}
      {explicitOutput && llmProvider !== 'local' && (
        <p className="mt-1 text-center text-[9px] text-text-muted">
          Authoring guidance may send request context to {llmProvider}; that provider's terms and privacy policy apply separately.
        </p>
      )}
      <p className="mt-1 text-center text-[9px] text-text-muted">
        Private controls preview blur only. Project access rules always apply separately.
      </p>
    </div>
  )
}
