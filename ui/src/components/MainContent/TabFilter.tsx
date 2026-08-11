import { useState, useRef, useEffect } from 'react'
import { Heart, Film, Search, SlidersHorizontal, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { buildOutputSearchQuery, splitOutputSearchQuery } from '../../api/client'
import type { MediaFilter, OutputArtifactScope, OutputSearchFilters } from '../../types'

const mediaKinds: { value: MediaFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'images', label: 'Images' },
  { value: 'videos', label: 'Videos' },
  { value: 'audio', label: 'Audio' },
]

const artifactScopes: { value: OutputArtifactScope; label: string }[] = [
  { value: 'final', label: 'Finals' },
  { value: 'all', label: 'All' },
  { value: 'component', label: 'Components' },
  { value: 'window', label: 'Windows' },
  { value: 'temporary', label: 'Temporary' },
]

const savedViews: { value: MediaFilter; label: string; icon: 'heart' | 'film' | null }[] = [
  { value: 'avatars', label: 'Edits', icon: null },
  { value: 'multiclip', label: 'Multi-clip', icon: 'film' },
  { value: 'favorites', label: 'Favorites', icon: 'heart' },
]

function facetButton(active: boolean, emphasized = false): string {
  return `flex items-center justify-center gap-1 rounded-md border px-2 py-1.5 text-[10px] font-medium transition-colors ${
    active
      ? emphasized
        ? 'border-amber-500/50 bg-amber-500/15 text-amber-300'
        : 'border-accent-blue/50 bg-accent-blue/15 text-accent-blue'
      : 'border-border bg-bg-primary text-text-muted hover:bg-bg-hover hover:text-text-primary'
  }`
}

export function TabFilter() {
  const mediaFilter = useStore(s => s.mediaFilter)
  const setMediaFilter = useStore(s => s.setMediaFilter)
  const artifactScope = useStore(s => s.outputArtifactScope)
  const setArtifactScope = useStore(s => s.setOutputArtifactScope)
  const searchQuery = useStore(s => s.outputSearchQuery)
  const setSearchQuery = useStore(s => s.setOutputSearchQuery)
  const resetGalleryFilters = useStore(s => s.resetGalleryFilters)
  const [searchOpen, setSearchOpen] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const initialSearch = splitOutputSearchQuery(searchQuery)
  const [draftSearch, setDraftSearch] = useState(initialSearch.text)
  const [filters, setFilters] = useState<OutputSearchFilters>(initialSearch.filters)
  const searchRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const filterTriggerRef = useRef<HTMLButtonElement>(null)
  const filterDialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (searchOpen && searchRef.current) searchRef.current.focus()
  }, [searchOpen])

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const parsed = splitOutputSearchQuery(searchQuery)
      setDraftSearch(parsed.text)
      setFilters(parsed.filters)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [searchQuery])

  useEffect(() => {
    if (!filtersOpen) return
    const trigger = filterTriggerRef.current
    const dialog = filterDialogRef.current
    const focusFrame = window.requestAnimationFrame(() => {
      dialog?.querySelector<HTMLElement>('[data-gallery-filter-initial]')?.focus()
    })
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setFiltersOpen(false)
      window.requestAnimationFrame(() => trigger?.focus())
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (target && (dialog?.contains(target) || trigger?.contains(target))) return
      setFiltersOpen(false)
    }
    document.addEventListener('keydown', handleKeyDown, true)
    document.addEventListener('pointerdown', handlePointerDown)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      document.removeEventListener('keydown', handleKeyDown, true)
      document.removeEventListener('pointerdown', handlePointerDown)
    }
  }, [filtersOpen])

  const handleSearchChange = (value: string) => {
    setDraftSearch(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(
      () => setSearchQuery(buildOutputSearchQuery(value, filters)),
      400,
    )
  }

  const updateFilter = (field: keyof OutputSearchFilters, value: string) => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
    }
    const next = { ...filters, [field]: value }
    setFilters(next)
    setSearchQuery(buildOutputSearchQuery(draftSearch, next))
  }

  const clearStructuredFilters = () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
    }
    setFilters({})
    setSearchQuery(buildOutputSearchQuery(draftSearch))
  }

  const resetAllFilters = () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
    }
    setDraftSearch('')
    setFilters({})
    setSearchOpen(false)
    resetGalleryFilters()
  }

  const closeSearch = () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
    }
    setSearchOpen(false)
    setDraftSearch('')
    setSearchQuery(buildOutputSearchQuery('', filters))
  }

  const closeFilterPopover = () => {
    setFiltersOpen(false)
    window.requestAnimationFrame(() => filterTriggerRef.current?.focus())
  }

  const metadataFilterCount = Object.values(filters).filter(Boolean).length
  const facetFilterCount = Number(mediaFilter !== 'all') + Number(artifactScope !== 'final')
  const activeFilterCount = metadataFilterCount + facetFilterCount
  const hasAnyGalleryFilter = activeFilterCount > 0 || Boolean(searchQuery)
  const mediaLabel = [...mediaKinds, ...savedViews].find(option => option.value === mediaFilter)?.label || 'All'
  const artifactLabel = artifactScopes.find(option => option.value === artifactScope)?.label || 'Finals'

  return (
    <div className="relative flex min-w-0 max-w-full flex-1 basis-[24rem] items-center gap-1">
      {searchOpen ? (
        <div className="flex min-w-0 items-center gap-1 rounded-lg border border-border bg-bg-tertiary px-2 py-0.5">
          <Search size={12} className="shrink-0 text-text-muted" />
          <input
            ref={searchRef}
            type="text"
            value={draftSearch}
            onChange={event => handleSearchChange(event.target.value)}
            placeholder="Prompt or filename..."
            className="w-28 min-w-0 bg-transparent text-xs text-text-primary placeholder:text-text-muted focus:outline-none md:w-44"
          />
          <button onClick={closeSearch} className="text-text-muted hover:text-text-secondary" aria-label="Clear search text and close search">
            <X size={12} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setSearchOpen(true)}
          className={`rounded-lg p-1.5 transition-colors ${searchQuery ? 'bg-accent-blue/10 text-accent-blue' : 'text-text-muted hover:bg-bg-hover hover:text-text-secondary'}`}
          title="Search outputs"
          aria-label="Search Gallery"
        >
          <Search size={14} />
        </button>
      )}
      <button
        ref={filterTriggerRef}
        onClick={() => filtersOpen ? closeFilterPopover() : setFiltersOpen(true)}
        className={`relative flex min-w-0 items-center gap-1.5 rounded-lg border px-2 py-1 text-[10px] transition-colors ${activeFilterCount ? 'border-accent-blue/40 bg-accent-blue/10 text-accent-blue' : 'border-border text-text-muted hover:bg-bg-hover hover:text-text-secondary'}`}
        title="Filter Gallery"
        aria-expanded={filtersOpen}
        aria-haspopup="dialog"
        aria-controls="gallery-filter-popover"
      >
        <SlidersHorizontal size={13} className="shrink-0" />
        <span className="max-w-40 truncate">{mediaLabel} · {artifactLabel}</span>
        {activeFilterCount > 0 && (
          <span className="min-w-3.5 rounded-full bg-accent-blue px-1 text-center text-[8px] leading-3.5 text-white">
            {activeFilterCount}
          </span>
        )}
      </button>

      {filtersOpen && (
        <div
          id="gallery-filter-popover"
          ref={filterDialogRef}
          role="dialog"
          aria-labelledby="gallery-filter-title"
          className="fixed left-2 right-2 top-16 z-[80] max-h-[calc(100vh-5rem)] overflow-y-auto rounded-lg border border-border bg-bg-secondary p-3 shadow-2xl sm:absolute sm:left-0 sm:right-auto sm:top-full sm:mt-2 sm:w-[440px]"
        >
          <div className="mb-3 flex items-start justify-between gap-2">
            <div>
              <p id="gallery-filter-title" className="text-xs font-medium text-text-primary">Gallery filters</p>
              <p className="text-[9px] text-text-muted">Media, artifact, and metadata selections all combine.</p>
            </div>
            <div className="flex items-center gap-1">
              {hasAnyGalleryFilter && (
                <button onClick={resetAllFilters} className="rounded px-2 py-1 text-[10px] text-text-muted hover:bg-bg-hover hover:text-text-primary">
                  Reset all
                </button>
              )}
              <button onClick={closeFilterPopover} className="rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-primary" aria-label="Close Gallery filters">
                <X size={13} />
              </button>
            </div>
          </div>

          <fieldset className="space-y-1.5">
            <legend className="text-[9px] font-medium uppercase tracking-wide text-text-muted">Media type</legend>
            <div className="grid grid-cols-4 gap-1">
              {mediaKinds.map(option => (
                <button
                  type="button"
                  key={option.value}
                  data-gallery-filter-initial={option.value === 'all' ? '' : undefined}
                  aria-pressed={mediaFilter === option.value}
                  onClick={() => {
                    if (mediaFilter !== option.value) setMediaFilter(option.value)
                  }}
                  className={facetButton(mediaFilter === option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="mt-3 space-y-1.5">
            <legend className="text-[9px] font-medium uppercase tracking-wide text-text-muted">Artifact</legend>
            <div className="grid grid-cols-2 gap-1 sm:grid-cols-5">
              {artifactScopes.map(option => (
                <button
                  type="button"
                  key={option.value}
                  aria-pressed={artifactScope === option.value}
                  onClick={() => setArtifactScope(option.value)}
                  className={facetButton(
                    artifactScope === option.value,
                    option.value !== 'final' && option.value !== 'all',
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="mt-3 space-y-1.5">
            <legend className="text-[9px] font-medium uppercase tracking-wide text-text-muted">Quick view</legend>
            <div className="grid grid-cols-3 gap-1">
              {savedViews.map(option => (
                <button
                  type="button"
                  key={option.value}
                  aria-pressed={mediaFilter === option.value}
                  onClick={() => {
                    if (mediaFilter !== option.value) setMediaFilter(option.value)
                  }}
                  className={facetButton(mediaFilter === option.value)}
                >
                  {option.icon === 'heart' && <Heart size={11} fill={mediaFilter === option.value ? 'currentColor' : 'none'} />}
                  {option.icon === 'film' && <Film size={11} />}
                  {option.label}
                </button>
              ))}
            </div>
            <p className="text-[9px] text-text-muted">Quick views replace the media-type selection and still combine with the artifact filter.</p>
          </fieldset>

          <div className="mt-3 border-t border-border pt-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <p className="text-[9px] font-medium uppercase tracking-wide text-text-muted">Generation metadata</p>
              {metadataFilterCount > 0 && (
                <button onClick={clearStructuredFilters} className="rounded px-2 py-1 text-[10px] text-text-muted hover:bg-bg-hover hover:text-text-primary">
                  Clear metadata
                </button>
              )}
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <label className="space-y-1 text-[9px] uppercase tracking-wide text-text-muted">
                Model contains
                <input
                  value={filters.model || ''}
                  onChange={event => updateFilter('model', event.target.value)}
                  placeholder="e.g. minimax_h3"
                  className="w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs normal-case tracking-normal text-text-primary focus:border-accent-blue focus:outline-none"
                />
              </label>
              <label className="space-y-1 text-[9px] uppercase tracking-wide text-text-muted">
                LoRA contains
                <input
                  value={filters.lora || ''}
                  onChange={event => updateFilter('lora', event.target.value)}
                  placeholder="filename or style"
                  className="w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs normal-case tracking-normal text-text-primary focus:border-accent-blue focus:outline-none"
                />
              </label>
              <label className="space-y-1 text-[9px] uppercase tracking-wide text-text-muted">
                Seed
                <input
                  inputMode="numeric"
                  value={filters.seed || ''}
                  onChange={event => updateFilter('seed', event.target.value.replace(/[^0-9-]/g, ''))}
                  placeholder="exact seed"
                  className="w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs normal-case tracking-normal text-text-primary focus:border-accent-blue focus:outline-none"
                />
              </label>
              <label className="space-y-1 text-[9px] uppercase tracking-wide text-text-muted">
                References
                <select
                  value={filters.reference || ''}
                  onChange={event => updateFilter('reference', event.target.value)}
                  className="w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs normal-case tracking-normal text-text-primary focus:border-accent-blue focus:outline-none"
                >
                  <option value="">Any</option>
                  <option value="with">With references</option>
                  <option value="without">Without references</option>
                </select>
              </label>
              <label className="space-y-1 text-[9px] uppercase tracking-wide text-text-muted">
                From date
                <input
                  type="date"
                  value={filters.after || ''}
                  onChange={event => updateFilter('after', event.target.value)}
                  className="w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs normal-case tracking-normal text-text-primary focus:border-accent-blue focus:outline-none"
                />
              </label>
              <label className="space-y-1 text-[9px] uppercase tracking-wide text-text-muted">
                Through date
                <input
                  type="date"
                  value={filters.before || ''}
                  onChange={event => updateFilter('before', event.target.value)}
                  className="w-full rounded border border-border bg-bg-primary px-2 py-1.5 text-xs normal-case tracking-normal text-text-primary focus:border-accent-blue focus:outline-none"
                />
              </label>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
