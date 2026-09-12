import { useEffect, useMemo, useRef, useState } from 'react'
import { FolderOpen, Save, Trash2 } from 'lucide-react'
import type { GenerationPreset } from '../../api/client'
import { useStore } from '../../stores/useStore'
import { GenerationProfileSettingsError } from '../../lib/generationProfiles'


type ProfileNotice = {
  kind: 'success' | 'error'
  text: string
}

interface GenerationProfilesProps {
  loadOnMount?: boolean
  placement?: 'sidebar' | 'advanced'
}

export function GenerationProfiles({
  loadOnMount = true,
  placement = 'sidebar',
}: GenerationProfilesProps = {}) {
  const presets = useStore(state => state.presets)
  const presetsLoading = useStore(state => state.presetsLoading)
  const presetsError = useStore(state => state.presetsError)
  const loadPresets = useStore(state => state.loadPresets)
  const savePreset = useStore(state => state.savePreset)
  const loadPreset = useStore(state => state.loadPreset)
  const deletePreset = useStore(state => state.deletePreset)
  const generationMode = useStore(state => state.generationMode)
  const models = useStore(state => state.models) || []
  const modelsLoaded = useStore(state => state.modelsLoaded)
  const selectedId = useStore(state => state.selectedGenerationProfileId)
  const setSelectedId = useStore(state => state.setSelectedGenerationProfileId)
  const [saveName, setSaveName] = useState('')
  const [showSave, setShowSave] = useState(false)
  const [activeAction, setActiveAction] = useState<'save' | 'load' | 'delete' | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [notice, setNotice] = useState<ProfileNotice | null>(null)
  const actionInFlight = useRef(false)
  const selectId = `generation-profile-${placement}`
  const saveFormId = `generation-profile-save-${placement}`

  useEffect(() => {
    if (loadOnMount) void loadPresets()
  }, [loadOnMount, loadPresets])

  const modeProfiles = useMemo(
    () => presets.filter(profile => profile.mode === generationMode),
    [generationMode, presets],
  )
  const selected = modeProfiles.find(profile => profile.id === selectedId) || null

  useEffect(() => {
    if (selectedId && !selected) setSelectedId('')
  }, [selected, selectedId, setSelectedId])

  useEffect(() => {
    setConfirmDelete(false)
    setNotice(null)
  }, [generationMode])

  const beginAction = (action: 'save' | 'load' | 'delete'): boolean => {
    if (actionInFlight.current) return false
    actionInFlight.current = true
    setActiveAction(action)
    setNotice(null)
    return true
  }

  const finishAction = () => {
    actionInFlight.current = false
    setActiveAction(null)
  }

  const handleSave = async () => {
    const name = saveName.trim()
    if (!name || !beginAction('save')) return
    try {
      await savePreset(name)
      setSaveName('')
      setShowSave(false)
      setNotice({ kind: 'success', text: 'Profile saved.' })
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof GenerationProfileSettingsError
        ? 'Keep this setup open. Its settings could not all be saved.'
        : 'Profile could not be saved. Try again.' })
    } finally {
      finishAction()
    }
  }

  const handleLoad = async () => {
    if (!selected || !beginAction('load')) return
    try {
      const loaded = await loadPreset(selected)
      if (loaded === false) throw new Error('Profile load was not confirmed')
      setNotice({ kind: 'success', text: `${selected.name} loaded.` })
    } catch {
      setNotice({ kind: 'error', text: 'Profile could not be loaded. Try again.' })
    } finally {
      finishAction()
    }
  }

  const handleDelete = async () => {
    if (!selected) return
    if (!confirmDelete) {
      setConfirmDelete(true)
      setNotice({ kind: 'error', text: `Select Delete again to remove ${selected.name}.` })
      setTimeout(() => setConfirmDelete(false), 3000)
      return
    }
    if (!beginAction('delete')) return
    try {
      await deletePreset(selected.id)
      setSelectedId('')
      setConfirmDelete(false)
      setNotice({ kind: 'success', text: 'Profile deleted.' })
    } catch {
      setNotice({ kind: 'error', text: 'Profile could not be deleted. Try again.' })
    } finally {
      finishAction()
    }
  }

  const selectedModel = selected ? models.find(model => model.model_type === selected.model_type) : undefined
  const modelUnavailable = !!selected && modelsLoaded && (!selectedModel || selectedModel.execution_allowed === false)
  const busy = presetsLoading || activeAction !== null
  const profileOption = (profile: GenerationPreset) => {
    const model = models.find(model => model.model_type === profile.model_type)
    return <option key={profile.id} value={profile.id}>
      {profile.name}{model?.name ? ` · ${model.name}` : ''}
    </option>
  }

  return (
    <section
      aria-label="Generation profiles"
      data-generation-profiles={placement}
      className={`rounded-lg border border-border bg-bg-tertiary/45 ${
        placement === 'advanced' ? 'p-3' : 'p-2.5'
      }`}
    >
      <div className="flex items-end gap-2">
        <label htmlFor={selectId} className="min-w-0 flex-1">
          <span className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-text-muted">
            Saved profile
          </span>
          <select
            id={selectId}
            value={selectedId}
            onChange={event => {
              setSelectedId(event.target.value)
              setConfirmDelete(false)
              setNotice(null)
            }}
            disabled={busy}
            className="mobile-control-target w-full min-w-0 rounded-md border border-border bg-bg-primary px-2.5 py-2 text-xs text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-60"
          >
            <option value="">
              {presetsLoading
                ? 'Loading profiles…'
                : modeProfiles.length > 0 ? 'Choose a profile' : presetsError ? 'Profiles unavailable' : 'No saved profiles'}
            </option>
            {modeProfiles.map(profileOption)}
          </select>
        </label>
        <button
          type="button"
          onClick={() => { void handleLoad() }}
          disabled={!selected || busy || modelUnavailable}
          title={modelUnavailable ? 'This profile’s model is unavailable' : undefined}
          aria-busy={activeAction === 'load'}
          className="mobile-control-target flex items-center gap-1 rounded-md border border-border px-2.5 py-2 text-xs text-text-secondary transition-colors hover:border-border-light hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50"
        >
          <FolderOpen aria-hidden="true" size={13} />
          {activeAction === 'load' ? 'Loading…' : modelUnavailable ? 'Model unavailable' : 'Load'}
        </button>
        <button
          type="button"
          onClick={() => {
            setShowSave(current => !current)
            setNotice(null)
          }}
          disabled={busy}
          aria-expanded={showSave}
          aria-controls={saveFormId}
          className="mobile-control-target flex items-center gap-1 rounded-md border border-accent-blue/60 px-2.5 py-2 text-xs text-accent-blue transition-colors hover:bg-accent-blue/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-60"
        >
          <Save aria-hidden="true" size={13} /> Save
        </button>
      </div>

      {showSave && (
        <div id={saveFormId} className="mt-2 flex gap-2">
          <input
            type="text"
            aria-label="New profile name"
            value={saveName}
            onChange={event => setSaveName(event.target.value)}
            onKeyDown={event => {
              if (event.key !== 'Enter') return
              event.preventDefault()
              void handleSave()
            }}
            disabled={busy}
            placeholder="Profile name"
            autoFocus
            className="mobile-control-target min-w-0 flex-1 rounded-md border border-border bg-bg-primary px-2.5 py-2 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
          />
          <button
            type="button"
            onClick={() => { void handleSave() }}
            disabled={!saveName.trim() || busy}
            aria-busy={activeAction === 'save'}
            className="mobile-control-target rounded-md bg-accent-blue px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-accent-blue-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50"
          >
            {activeAction === 'save' ? 'Saving…' : 'Save as new'}
          </button>
        </div>
      )}

      {presetsError && (
        <div role="status" className="mt-2 flex items-center justify-between gap-2 text-[10px] text-red-400">
          <span>{presetsError}</span>
          <button
            type="button"
            onClick={() => { void loadPresets() }}
            disabled={busy}
            className="mobile-control-target rounded border border-border px-2 py-1 text-text-secondary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50"
          >
            Retry
          </button>
        </div>
      )}

      {selected && (
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-border/70 pt-2">
          <span className="min-w-0 truncate text-[10px] text-text-muted">
            {selected.model_type} · {selected.activated_loras.length} LoRA{selected.activated_loras.length === 1 ? '' : 's'}
          </span>
          <button
            type="button"
            onClick={() => { void handleDelete() }}
            disabled={busy}
            aria-label={`${confirmDelete ? 'Confirm delete' : 'Delete'} profile ${selected.name}`}
            className={`mobile-control-target flex items-center gap-1 rounded px-2 py-1 text-[10px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-50 ${
              confirmDelete
                ? 'bg-red-500/15 text-red-400'
                : 'text-text-muted hover:bg-red-500/10 hover:text-red-400'
            }`}
          >
            <Trash2 aria-hidden="true" size={11} />
            {confirmDelete ? 'Confirm' : 'Delete'}
          </button>
        </div>
      )}

      <p
        role="status"
        aria-live="polite"
        className={`mt-1.5 min-h-4 text-[10px] ${
          notice?.kind === 'error' ? 'text-red-400' : 'text-text-muted'
        }`}
      >
        {notice?.text || ''}
      </p>
    </section>
  )
}
