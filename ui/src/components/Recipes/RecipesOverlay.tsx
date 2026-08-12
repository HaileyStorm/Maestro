import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X, BookMarked, Trash2, Upload, Play, Loader2, AlertTriangle, Download, ExternalLink, Layers } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import type { RecipeCard, RecipeLora } from '../../api/client'
import * as api from '../../api/client'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'

/**
 * RecipesOverlay — the one-click preset library. Bundled starters + the
 * user's own saved recipes as a thumbnail grid. Clicking a card applies
 * it (switches model + settings and prepopulates the prompt). Fully ready
 * recipes close into Studio; missing-LoRA recipes remain open with truthful
 * host-install guidance.
 */
export function RecipesOverlay() {
  const open = useStore(s => s.recipesOpen)
  const setOpen = useStore(s => s.setRecipesOpen)
  const recipes = useStore(s => s.recipes)
  const loading = useStore(s => s.recipesLoading)
  const loadError = useStore(s => s.recipesError)
  const applyRecipe = useStore(s => s.applyRecipe)
  const deleteRecipe = useStore(s => s.deleteRecipe)
  const loadRecipes = useStore(s => s.loadRecipes)
  const civitaiKeySet = useStore(s => s.servicesConfig?.civitai_api_key_set ?? false)
  const setSettingsOpen = useStore(s => s.setSettingsOpen)
  const setSettingsTab = useStore(s => s.setSettingsTab)
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const applyingRef = useRef(false)
  const titleId = useId()

  const [applying, setApplying] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [missing, setMissing] = useState<{ modelType: string; loras: RecipeLora[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const closeOverlay = useCallback(() => {
    if (!applyingRef.current) setOpen(false)
  }, [setOpen])

  useEffect(() => {
    if (!open || !dialogRef.current || !closeButtonRef.current) return
    const restoreFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    setMissing(null)
    setError(null)
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeButtonRef.current,
      restoreFocus,
      appRoot: document.getElementById('root'),
      onClose: closeOverlay,
      priority: 100,
    })
  }, [closeOverlay, open])

  if (!open) return null

  const handleApply = async (card: RecipeCard) => {
    if (applyingRef.current) return
    applyingRef.current = true
    closeButtonRef.current?.focus()
    setApplying(card.id); setError(null); setMissing(null)
    let closeAfterApply = false
    try {
      const { missing: missingLoras } = await applyRecipe(card.id)
      if (missingLoras.length > 0) {
        // Applied, but some LoRAs aren't installed — keep the overlay open
        // and surface them so the user can fetch them before generating
        // (rather than hitting a cryptic "Loras missing" failure at gen time).
        setMissing({ modelType: card.model_type, loras: missingLoras })
      } else {
        closeAfterApply = true
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to apply recipe')
    } finally {
      applyingRef.current = false
      setApplying(null)
      if (closeAfterApply) setOpen(false)
    }
  }

  const openCivitaiKeySettings = () => {
    setMissing(null); setOpen(false)
    setSettingsTab('integrations'); setSettingsOpen(true)
  }

  const handleImport = () => {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = '.json'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        await api.importRecipe(JSON.parse(text))
        loadRecipes()
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Import failed')
      }
    }
    input.click()
  }

  const handleDelete = async (card: RecipeCard) => {
    if (applyingRef.current) return
    if (confirmDelete !== card.id) {
      setConfirmDelete(card.id)
      window.setTimeout(() => setConfirmDelete(current => current === card.id ? null : current), 4000)
      return
    }
    setConfirmDelete(null)
    applyingRef.current = true
    closeButtonRef.current?.focus()
    setApplying(`delete:${card.id}`)
    setError(null)
    try {
      await deleteRecipe(card.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete recipe')
    } finally {
      applyingRef.current = false
      setApplying(null)
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex h-[100vh] items-center justify-center overflow-hidden supports-[height:100dvh]:h-[100dvh]"
      style={{
        paddingTop: 'max(0.5rem, env(safe-area-inset-top))',
        paddingRight: 'max(0.5rem, env(safe-area-inset-right))',
        paddingBottom: 'max(0.5rem, env(safe-area-inset-bottom))',
        paddingLeft: 'max(0.5rem, env(safe-area-inset-left))',
      }}
    >
      <button
        type="button"
        tabIndex={-1}
        disabled={applying !== null}
        aria-label="Close recipes"
        className="absolute inset-0 appearance-none border-0 bg-black/60 p-0"
        onClick={() => closeModalIfTop(document, dialogRef.current, closeOverlay)}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative flex h-full min-h-0 w-full max-w-7xl flex-col overflow-hidden rounded-xl border border-border bg-bg-primary shadow-2xl"
      >
      {/* Header */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-3 py-2 sm:px-4">
        <BookMarked size={16} className="text-accent-blue shrink-0" />
        <h1 id={titleId} className="text-sm font-semibold text-text-primary">Recipes</h1>
        <span className="hidden text-[11px] text-text-muted sm:inline">one-click presets — pick a look, tweak the prompt, generate</span>
        <div className="flex-1" />
        {machineControls && <button
          type="button"
          onClick={handleImport}
          disabled={applying !== null}
          className="flex min-h-11 items-center gap-1.5 rounded-lg border border-border bg-bg-tertiary px-2.5 py-1.5 text-[11px] text-text-secondary transition-colors hover:border-border-light hover:text-text-primary md:min-h-0"
          aria-label="Import a recipe file"
        >
          <Upload size={12} /> Import
        </button>}
        <button
          ref={closeButtonRef}
          type="button"
          onClick={() => closeModalIfTop(document, dialogRef.current, closeOverlay)}
          className="flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-border bg-bg-secondary transition-colors hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:min-h-0 md:min-w-0 md:p-1.5"
          aria-label={applying ? 'Recipe action in progress; close is temporarily unavailable' : 'Close recipes'}
          aria-disabled={applying !== null}
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain [-webkit-overflow-scrolling:touch]">
      {/* Missing-LoRA notice */}
      {missing && (
        <div role="status" aria-live="polite" className="px-4 py-2.5 bg-amber-500/10 border-b border-amber-500/30">
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="text-indicator-warning shrink-0 mt-0.5" />
            <div className="flex-1 text-[11px] text-text-primary">
              <div className="font-medium mb-1">
                Applied — but this recipe uses {missing.loras.length} LoRA
                {missing.loras.length > 1 ? 's' : ''} you don't have installed:
              </div>
              <div className="space-y-1">
                {missing.loras.map(l => (
                  <MissingLoraRow
                    key={l.filename}
                    lora={l}
                    modelType={missing.modelType}
                    civitaiKeySet={civitaiKeySet}
                    canInstall={machineControls}
                  />
                ))}
              </div>
              {machineControls && !civitaiKeySet && missing.loras.some(l => l.source_url) && (
                <div className="mt-1.5 text-[10px] text-text-secondary leading-snug">
                  Auto-download needs a free CivitAI API key.{' '}
                  <button type="button" onClick={openCivitaiKeySettings} className="inline-flex min-h-11 items-center underline hover:text-text-primary md:min-h-0">Add one in Settings</button>
                  {' '}— then click Download. Or use each “Open source” link to grab it manually.
                </div>
              )}
              <div className="mt-1.5 text-[10px] text-text-secondary">
                {machineControls
                  ? 'The recipe is applied in Studio. Install the LoRA before you Generate.'
                  : 'The recipe is applied in Studio, but this LoRA can only be installed by the Maestro host owner. Ask them to install it before you Generate.'}
              </div>
            </div>
            <button type="button" onClick={() => { setMissing(null); closeOverlay() }}
              className="flex min-h-11 min-w-11 shrink-0 items-center justify-center text-[10px] text-text-secondary hover:text-text-primary md:min-h-0 md:min-w-0">Dismiss</button>
          </div>
        </div>
      )}
      {error && (
        <div role="alert" className="px-4 py-2 bg-red-500/10 border-b border-red-500/30 text-[11px] text-chip-red">{error}</div>
      )}

      {/* Grid */}
      <div className="p-3 sm:p-4">
        {loading ? (
          <div className="flex items-center justify-center min-h-[300px] text-text-muted">
            <Loader2 size={22} className="animate-spin" />
          </div>
        ) : loadError ? (
          <div className="flex flex-col items-center justify-center min-h-[300px] gap-3 text-text-muted text-center">
            <AlertTriangle size={28} />
            <p role="alert" className="text-sm max-w-xs">{loadError}</p>
            <button
              type="button"
              onClick={() => void loadRecipes()}
              className="min-h-11 rounded-lg border border-border bg-bg-secondary px-3 py-1.5 text-xs text-text-primary hover:bg-bg-hover md:min-h-0"
            >
              Try again
            </button>
          </div>
        ) : recipes.length === 0 ? (
          <div className="flex flex-col items-center justify-center min-h-[300px] gap-3 text-text-muted text-center">
            <BookMarked size={28} />
            <p className="text-sm max-w-xs">
              {machineControls
                ? 'No recipes yet. Generate something you like, then use “Save as Recipe” on it — or import a recipe file.'
                : 'No bundled recipes are available on this Maestro host.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(min(200px,100%),1fr))] gap-3">
            {recipes.map(card => (
              <RecipeGridCard
                key={card.id}
                card={card}
                applying={applying === card.id}
                disabled={applying !== null}
                onApply={() => handleApply(card)}
                onDelete={card.source === 'user' ? () => void handleDelete(card) : undefined}
                deleteConfirming={confirmDelete === card.id}
              />
            ))}
          </div>
        )}
      </div>
      </div>
      </div>
    </div>
    , document.body
  )
}

function RecipeGridCard({ card, applying, disabled, onApply, onDelete, deleteConfirming }: {
  card: RecipeCard; applying: boolean; disabled: boolean; onApply: () => void; onDelete?: () => void; deleteConfirming: boolean
}) {
  return (
    <div className="group relative rounded-xl border border-border bg-bg-secondary overflow-hidden hover:border-accent-blue/60 transition-colors flex flex-col">
      {/* Thumbnail */}
      <button
        type="button"
        onClick={onApply}
        disabled={disabled}
        className="block aspect-video bg-bg-tertiary relative"
        aria-label={`Apply recipe ${card.name} in Studio`}
      >
        {card.thumbnail_url ? (
          <img src={card.thumbnail_url} alt="" className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-text-muted">
            <BookMarked size={28} />
          </div>
        )}
        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center">
          {applying
            ? <Loader2 size={22} className="text-white animate-spin" />
            : <Play size={22} className="text-white opacity-0 group-hover:opacity-100 transition-opacity" />}
        </div>
      </button>

      {/* Body */}
      <div className="p-2.5 flex-1 flex flex-col gap-1">
        <div className="flex items-start justify-between gap-1.5">
          <div className="text-xs font-medium text-text-primary leading-tight">{card.name}</div>
          {onDelete && (
            <button type="button" onClick={onDelete} disabled={disabled}
              title={deleteConfirming ? 'Click again to confirm delete' : 'Delete recipe'}
              aria-label={deleteConfirming ? `Confirm delete recipe ${card.name}` : `Delete recipe ${card.name}`}
              className={`shrink-0 min-h-11 min-w-11 -m-2 px-2 flex items-center justify-center gap-1 rounded transition-colors sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 focus-visible:opacity-100 ${deleteConfirming ? 'text-red-400 bg-red-500/10 sm:opacity-100' : 'text-text-muted hover:text-red-400'}`}>
              <Trash2 size={14} />
              {deleteConfirming && <span className="text-[10px] font-medium">Confirm?</span>}
            </button>
          )}
        </div>
        {card.description && (
          <div className="text-[10px] text-text-muted leading-snug line-clamp-2">{card.description}</div>
        )}
        <div className="mt-auto pt-1 flex items-center gap-2 text-[9px] text-text-muted">
          <span className="capitalize">{card.mode}</span>
          {card.lora_count > 0 && (
            <span className="flex items-center gap-0.5"><Layers size={9} /> {card.lora_count}</span>
          )}
        </div>
      </div>
    </div>
  )
}

function MissingLoraRow({ lora, modelType, civitaiKeySet, canInstall }: {
  lora: RecipeLora
  modelType: string
  civitaiKeySet: boolean
  canInstall: boolean
}) {
  const downloadRecipeLora = useStore(s => s.downloadRecipeLora)
  const [state, setState] = useState<'idle' | 'downloading' | 'done' | 'error'>('idle')

  const handleDownload = async () => {
    setState('downloading')
    try {
      await downloadRecipeLora(lora, modelType)
      setState('done')
    } catch {
      setState('error')
    }
  }

  // The "Open source" link — always a valid fallback (opens the CivitAI page/
  // file directly, no key needed to view it).
  const sourceLink = lora.source_url ? (
    <a href={lora.source_url} target="_blank" rel="noreferrer"
      className="flex min-h-11 shrink-0 items-center gap-0.5 text-accent-blue hover:text-accent-blue-hover md:min-h-0">
      <ExternalLink size={10} /> Open source
    </a>
  ) : (
    <span className="text-text-secondary shrink-0">install manually</span>
  )

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="min-w-0 max-w-full break-all font-mono text-text-secondary">{lora.filename}</span>
      {lora.size_mb ? <span className="text-text-secondary shrink-0">~{Math.round(lora.size_mb)} MB</span> : null}
      {/* In-app auto-download needs a CivitAI key. With a key → offer Download;
          without → skip the button (it would just fail) and show the source
          link so the user can grab it manually. */}
      {!canInstall && <span className="text-text-secondary shrink-0">host installation required</span>}
      {canInstall && lora.source_url && civitaiKeySet && state === 'idle' && (
        <button type="button" onClick={handleDownload}
          className="flex min-h-11 shrink-0 items-center gap-0.5 text-accent-blue hover:text-accent-blue-hover md:min-h-0">
          <Download size={10} /> Download
        </button>
      )}
      {canInstall && lora.source_url && !civitaiKeySet && state === 'idle' && sourceLink}
      {canInstall && state === 'downloading' && <Loader2 size={10} className="animate-spin text-indicator-warning shrink-0" />}
      {canInstall && state === 'done' && <span className="text-indicator-success shrink-0">started ↓ (see download bar)</span>}
      {canInstall && state === 'error' && sourceLink}
      {canInstall && !lora.source_url && state === 'idle' && sourceLink}
    </div>
  )
}
