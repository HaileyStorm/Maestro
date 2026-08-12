import { useCallback, useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'
import { SystemSettingsPanel } from './SystemSettingsPanel'
import { ServicesSettingsPanel } from './ServicesSettingsPanel'

/**
 * Settings drawer — global panel for hardware/perf and external-service
 * configuration. Two tabs in both Studio and Director modes:
 *
 *   Performance    — VRAM coefficient, profile, hardware tier, etc.
 *                    (mounts <SystemSettingsPanel />)
 *   Integrations   — LLM provider, API keys, NSFW master gate, etc.
 *                    (mounts <ServicesSettingsPanel />)
 *
 * Director-mode-specific controls used to live in a third "Parameters"
 * tab here, but everything in that tab was either:
 *   - a duplicate of Studio's selection (image/video model, LoRAs)
 *   - a duplicate of Integrations (LLM model + device)
 *   - or a post-processing knob that's now in the Director chat sidebar
 *     under the "Advanced" accordion.
 *
 * Removing the tab makes Settings mode-agnostic — same layout in Studio
 * and Director — which matches the user's mental model of Settings as
 * "global preferences" vs Director's per-shoot setup which lives in
 * the chat sidebar where the work is happening.
 */
export function SettingsDrawer() {
  const settingsOpen = useStore(s => s.settingsOpen)
  const setSettingsOpen = useStore(s => s.setSettingsOpen)
  const settingsTab = useStore(s => s.settingsTab)
  const setSettingsTab = useStore(s => s.setSettingsTab)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()

  const closeSettings = useCallback(() => {
    setSettingsOpen(false)
  }, [setSettingsOpen])

  useEffect(() => {
    if (!settingsOpen || !dialogRef.current || !closeButtonRef.current) return
    const restoreFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeButtonRef.current,
      restoreFocus,
      appRoot: document.getElementById('root'),
      onClose: closeSettings,
      priority: 50,
    })
  }, [closeSettings, settingsOpen])

  const tabs = [
    { id: 'performance' as const, label: 'Performance' },
    { id: 'integrations' as const, label: 'Integrations' },
  ]

  return createPortal(
    <>
      {/* Backdrop */}
      {settingsOpen && (
        <button
          type="button"
          tabIndex={-1}
          aria-label="Close settings"
          className="fixed inset-0 z-40 appearance-none border-0 bg-black/40 p-0"
          onClick={() => closeModalIfTop(document, dialogRef.current, closeSettings)}
        />
      )}

      {/* Drawer */}
      <div
        id="machine-settings-drawer"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-hidden={!settingsOpen}
        inert={!settingsOpen}
        className={`fixed left-0 top-0 z-50 flex h-[100vh] w-full flex-col overflow-hidden border-r border-border bg-bg-secondary pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] shadow-2xl transform transition-transform duration-300 ease-in-out supports-[height:100dvh]:h-[100dvh] motion-reduce:transition-none md:w-[clamp(460px,24vw,560px)] ${
        settingsOpen ? 'translate-x-0' : '-translate-x-full'
      }`}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2 sm:px-5 sm:py-3">
          <h2 id={titleId} className="font-semibold text-sm">Settings</h2>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={() => closeModalIfTop(document, dialogRef.current, closeSettings)}
            className="flex min-h-11 min-w-11 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            aria-label="Close settings"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {/* Tab Bar */}
        <div className="shrink-0 px-5 pt-3">
          <div className="flex bg-bg-tertiary rounded-lg p-0.5 border border-border">
            {tabs.map(tab => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setSettingsTab(tab.id)}
                className={`min-h-11 flex-1 rounded-md py-1.5 text-xs transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
                  settingsTab === tab.id
                    ? 'bg-bg-active text-text-primary'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
                aria-pressed={settingsTab === tab.id}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4 space-y-5 [-webkit-overflow-scrolling:touch]">
          {settingsTab === 'performance' && (
            <SystemSettingsPanel />
          )}

          {settingsTab === 'integrations' && (
            <ServicesSettingsPanel />
          )}
        </div>
      </div>
    </>,
    document.body,
  )
}
