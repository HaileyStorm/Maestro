import { Image, Video, AudioLines, Wand2, Wrench } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import type { GenerationMode } from '../../types'

const modes: { value: GenerationMode; label: string; icon: typeof Image }[] = [
  { value: 'image', label: 'Image', icon: Image },
  { value: 'video', label: 'Video', icon: Video },
  { value: 'audio', label: 'Audio', icon: AudioLines },
  { value: 'avatar', label: 'Edit', icon: Wand2 },
  { value: 'tools', label: 'Tools', icon: Wrench },
]

export function GenerationModeSelector() {
  const generationMode = useStore(s => s.generationMode)
  const setGenerationMode = useStore(s => s.setGenerationMode)

  return (
    <div role="group" aria-label="Generation mode" className="grid grid-cols-3 gap-0.5 rounded-lg border border-border bg-bg-tertiary p-0.5 md:grid-cols-5 md:gap-0">
      {modes.map(m => {
        const Icon = m.icon
        const active = generationMode === m.value
        return (
          <button
            key={m.value}
            type="button"
            onClick={() => setGenerationMode(m.value)}
            aria-pressed={active}
            className={`mobile-control-target flex min-w-0 items-center justify-center gap-1.5 rounded-md py-2 text-xs transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
              active
                ? 'bg-bg-active text-text-primary'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            <Icon aria-hidden="true" className="shrink-0" size={14} />
            <span>{m.label}</span>
          </button>
        )
      })}
    </div>
  )
}
