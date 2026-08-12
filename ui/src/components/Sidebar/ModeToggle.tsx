import { useStore } from '../../stores/useStore'

export function ModeToggle() {
  const imageMode = useStore(s => s.params.image_mode)
  const setParam = useStore(s => s.setParam)

  const modes = [
    { value: 0, label: 'Frames' },
    { value: 2, label: 'Multi-Shot' },
    { value: 3, label: 'Extend' },
    { value: 4, label: 'Blend' },
  ]

  return (
    <div role="group" aria-label="Video input mode" className="grid grid-cols-2 gap-0.5 rounded-lg border border-border bg-bg-tertiary p-0.5 md:grid-cols-4 md:gap-0">
      {modes.map(m => (
        <button
          key={m.value}
          type="button"
          onClick={() => setParam('image_mode', m.value)}
          aria-pressed={imageMode === m.value}
          className={`mobile-control-target min-w-0 rounded-md py-1.5 text-xs transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
            imageMode === m.value
              ? 'bg-bg-active text-text-primary'
              : 'text-text-secondary hover:text-text-primary'
          }`}
        >
          {m.label}
        </button>
      ))}
    </div>
  )
}
