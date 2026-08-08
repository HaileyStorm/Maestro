import { EyeOff, Flame } from 'lucide-react'
import { useStore } from '../../stores/useStore'

export function GenerationPrivacyControls() {
  const matureModelsAvailable = useStore(s => s.servicesConfig?.nsfw_mode ?? false)
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const explicitOutput = useStore(s => s.explicitOutput)
  const setExplicitOutput = useStore(s => s.setExplicitOutput)
  const matureSelectionActive = useStore(s => s.matureSelectionActive())
  const privateOutput = useStore(s => s.privateOutput)
  const setPrivateOutput = useStore(s => s.setPrivateOutput)
  const baseH3Selected = useStore(s => (
    s.sidebarMode === 'director'
      ? s.selectedModelPerMode.video === 'minimax_h3'
      : s.params.model_type === 'minimax_h3'
  ))

  return (
    <div className="px-4 py-2 border-b border-border bg-bg-tertiary/50">
      <div className="grid grid-cols-2 gap-2">
        <label
          className={`flex items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-[11px] transition-colors ${
            matureSelectionActive ? 'cursor-not-allowed' : 'cursor-pointer'
          } ${
            explicitOutput
              ? 'border-red-500/50 bg-red-500/15 text-red-300'
              : 'border-border bg-bg-secondary text-text-secondary hover:text-text-primary'
          }`}
          title={matureSelectionActive
            ? 'A selected mature model or LoRA requires this job to remain explicit'
            : 'Mark this Studio or Director job as explicit'}
        >
          <input
            type="checkbox"
            checked={explicitOutput}
            disabled={matureSelectionActive}
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
      {matureSelectionActive && (
        <p className="mt-1 text-center text-[9px] text-red-300">
          Explicit is required by the selected mature model or LoRA.
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
      <p className="mt-1 text-center text-[9px] text-text-muted">
        Private controls preview blur only. Project access rules always apply separately.
      </p>
      {!matureModelsAvailable && (
        <p className="mt-1 text-center text-[9px] text-text-muted">
          {machineControls
            ? 'Mature models are disabled in Settings; the job label remains available.'
            : 'The host has not enabled mature models; the job label remains available.'}
        </p>
      )}
    </div>
  )
}
