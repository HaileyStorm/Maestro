import { useCallback, useEffect, useId, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { BookMarked, Loader2, X } from 'lucide-react'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'

/**
 * SaveRecipeDialog — turns the current gallery output into a reusable
 * recipe. The output's sidecar supplies model + LoRAs + settings and the
 * media supplies the thumbnail; the user just names it.
 */
export function SaveRecipeDialog({ onSave, onCancel, restoreFocusRef }: {
  onSave: (name: string, description: string, nsfw: boolean) => Promise<void>
  onCancel: () => void
  restoreFocusRef: RefObject<HTMLButtonElement | null>
}) {
  const titleId = useId()
  const descriptionId = useId()
  const nameId = useId()
  const recipeDescriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const savingRef = useRef(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestClose = useCallback(() => {
    if (!savingRef.current) onCancel()
  }, [onCancel])

  useEffect(() => {
    if (!dialogRef.current || !nameRef.current) return
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: nameRef.current,
      restoreFocus: restoreFocusRef.current,
      appRoot: document.getElementById('root'),
      onClose: requestClose,
      priority: 100,
    })
  }, [requestClose, restoreFocusRef])

  const submit = async () => {
    if (!name.trim() || savingRef.current) return
    savingRef.current = true
    setSaving(true); setError(null)
    try {
      await onSave(name.trim(), description.trim(), false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
      savingRef.current = false
      setSaving(false)
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center"
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
        disabled={saving}
        aria-label="Close Save Recipe dialog"
        className="absolute inset-0 appearance-none border-0 bg-black/60 p-0"
        onClick={() => closeModalIfTop(document, dialogRef.current, requestClose)}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative flex min-h-0 max-h-[calc(100vh-1.5rem)] w-full max-w-[420px] flex-col overflow-hidden rounded-xl border border-border bg-bg-secondary shadow-2xl supports-[height:100dvh]:max-h-[calc(100dvh-1.5rem)] sm:max-h-[92vh]"
        onClick={e => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-start gap-2 border-b border-border px-4 py-3 sm:px-5">
          <BookMarked size={16} aria-hidden="true" className="mt-1 shrink-0 text-accent-blue" />
          <h2 id={titleId} className="min-w-0 flex-1 text-sm font-semibold text-text-primary">Save as Recipe</h2>
          <button
            type="button"
            disabled={saving}
            onClick={() => closeModalIfTop(document, dialogRef.current, requestClose)}
            aria-label="Close Save Recipe dialog"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:p-1.5"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 [-webkit-overflow-scrolling:touch] sm:px-5">
          <p id={descriptionId} className="mb-3 text-[11px] leading-snug text-text-muted">
            Captures this generation's model, LoRAs, and settings as a one-click
            preset. Its thumbnail comes from this output. Applying a recipe later
            prepopulates the prompt so you just edit the subject.
          </p>

          <label htmlFor={nameId} className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Name</label>
          <input
            id={nameId}
            ref={nameRef}
            value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') submit() }}
            placeholder="e.g. Cinematic Film Look"
            className="mb-3 min-h-11 w-full rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-sm text-text-primary focus:border-accent-blue focus:outline-none md:min-h-0"
          />

          <label htmlFor={recipeDescriptionId} className="text-[10px] text-text-muted uppercase tracking-wider mb-1 block">Description (optional)</label>
          <textarea
            id={recipeDescriptionId}
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="When to use it, what it's good for…"
            rows={2}
            className="mb-3 min-h-[5.5rem] w-full resize-none rounded-lg border border-border bg-bg-tertiary px-3 py-2 text-sm text-text-primary focus:border-accent-blue focus:outline-none"
          />

          {error && <div role="alert" className="mb-3 text-[11px] text-red-400">{error}</div>}
        </div>

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3 sm:px-5">
          <button onClick={requestClose} disabled={saving}
            type="button"
            className="min-h-11 rounded-lg border border-border px-4 py-2 text-xs text-text-secondary transition-colors hover:border-border-light hover:text-text-primary disabled:opacity-40 md:min-h-0">
            Cancel
          </button>
          <button onClick={submit} disabled={!name.trim() || saving}
            type="button"
            className="flex min-h-11 items-center gap-1.5 rounded-lg bg-accent-blue px-4 py-2 text-xs text-white transition-colors hover:bg-accent-blue-hover disabled:opacity-40 md:min-h-0">
            {saving ? <><Loader2 size={12} className="animate-spin" /> Saving…</> : 'Save Recipe'}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
