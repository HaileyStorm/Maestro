import { useState, useRef, useEffect } from 'react'
import { Heart, Film, Search, SlidersHorizontal, X } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { buildOutputSearchQuery, splitOutputSearchQuery } from '../../api/client'
import type { MediaFilter, OutputArtifactScope, OutputSearchFilters } from '../../types'

const artifactScopes: { value: OutputArtifactScope; label: string; shortLabel: string }[] = [
  { value: 'final', label: 'Finals', shortLabel: 'Final' },
  { value: 'all', label: 'All', shortLabel: 'All' },
  { value: 'component', label: 'Components', shortLabel: 'Parts' },
  { value: 'window', label: 'Windows', shortLabel: 'Win' },
  { value: 'temporary', label: 'Temporary', shortLabel: 'Tmp' },
]

const tabs: { value: MediaFilter; label: string; shortLabel: string; icon?: string }[] = [
  { value: 'all', label: 'All', shortLabel: 'All' },
  { value: 'images', label: 'Images', shortLabel: 'Img' },
  { value: 'videos', label: 'Videos', shortLabel: 'Vid' },
  { value: 'audio', label: 'Audio', shortLabel: 'Aud' },
  { value: 'avatars', label: 'Edits', shortLabel: 'Edits' },
  { value: 'multiclip', label: 'Multi-clip', shortLabel: 'MC', icon: 'film' },
  { value: 'favorites', label: 'Favorites', shortLabel: '', icon: 'heart' },
]

export function TabFilter() {
  const mediaFilter = useStore(s => s.mediaFilter)
  const setMediaFilter = useStore(s => s.setMediaFilter)
  const artifactScope = useStore(s => s.outputArtifactScope)
  const setArtifactScope = useStore(s => s.setOutputArtifactScope)
  const searchQuery = useStore(s => s.outputSearchQuery)
  const setSearchQuery = useStore(s => s.setOutputSearchQuery)
  const [searchOpen, setSearchOpen] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const initialSearch = splitOutputSearchQuery(searchQuery)
  const [draftSearch, setDraftSearch] = useState(initialSearch.text)
  const [filters, setFilters] = useState<OutputSearchFilters>(initialSearch.filters)
  const searchRef = useRef<HTMLInputElement>(null)
  const filterBarRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [compactLabels, setCompactLabels] = useState(false)

  useEffect(() => {
    if (searchOpen && searchRef.current) searchRef.current.focus()
  }, [searchOpen])

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
  }, [])

  useEffect(() => {
    const element = filterBarRef.current
    if (!element) return
    const updateLayout = () => setCompactLabels(element.getBoundingClientRect().width < 760)
    updateLayout()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', updateLayout)
      return () => window.removeEventListener('resize', updateLayout)
    }
    const observer = new ResizeObserver(updateLayout)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const parsed = splitOutputSearchQuery(searchQuery)
      setDraftSearch(parsed.text)
      setFilters(parsed.filters)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [searchQuery])

  const handleSearchChange = (val: string) => {
    setDraftSearch(val)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(
      () => setSearchQuery(buildOutputSearchQuery(val, filters)),
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

  const closeSearch = () => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current)
      debounceRef.current = null
    }
    setSearchOpen(false)
    setFiltersOpen(false)
    setDraftSearch('')
    setFilters({})
    setSearchQuery('')
  }

  const activeFilterCount = Object.values(filters).filter(Boolean).length

  return (
    <div ref={filterBarRef} className="relative flex min-w-0 max-w-full flex-1 basis-[42rem] flex-wrap items-center gap-1">
      <div className="flex shrink-0 gap-0.5 rounded-lg border border-border bg-bg-tertiary p-0.5">
        {artifactScopes.map(scope => (
          <button
            key={scope.value}
            onClick={() => setArtifactScope(scope.value)}
            className={`${compactLabels ? 'px-2 py-1 text-[10px]' : 'px-3 py-1.5 text-xs'} shrink-0 whitespace-nowrap rounded-md font-medium transition-all ${
              artifactScope === scope.value
                ? scope.value !== 'final' && scope.value !== 'all' ? 'bg-amber-500/20 text-amber-300' : 'bg-bg-active text-text-primary'
                : 'text-text-muted hover:text-text-secondary'
            }`}
            title={`${scope.label} artifacts`}
          >
            {compactLabels ? scope.shortLabel : scope.label}
          </button>
        ))}
      </div>
      <div className="flex shrink-0 gap-0.5 rounded-lg border border-border bg-bg-tertiary p-0.5">
        {tabs.map(tab => (
          <button
            key={tab.value}
            onClick={() => setMediaFilter(tab.value)}
            className={`${compactLabels ? 'px-2 py-1 text-[10px]' : 'px-3 py-1.5 text-xs'} flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md font-medium transition-all ${
              mediaFilter === tab.value
                ? tab.value === 'favorites' ? 'bg-red-500/20 text-chip-red'
                : tab.value === 'multiclip' ? 'bg-purple-500/20 text-chip-purple'
                : 'bg-bg-active text-text-primary'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {tab.icon === 'heart' && <Heart size={11} fill={mediaFilter === 'favorites' ? 'currentColor' : 'none'} />}
            {tab.icon === 'film' && <Film size={11} />}
            <span>{compactLabels ? tab.shortLabel : tab.label}</span>
          </button>
        ))}
      </div>

      {/* Search */}
      {searchOpen ? (
        <div className="flex items-center gap-1 bg-bg-tertiary border border-border rounded-lg px-2 py-0.5">
          <Search size={12} className="text-text-muted shrink-0" />
          <input
            ref={searchRef}
            type="text"
            value={draftSearch}
            onChange={e => handleSearchChange(e.target.value)}
            placeholder="Prompt or filename..."
            className="bg-transparent text-xs text-text-primary placeholder:text-text-muted focus:outline-none w-24 md:w-36"
          />
          <button onClick={closeSearch}
            className="text-text-muted hover:text-text-secondary">
            <X size={12} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setSearchOpen(true)}
          className={`p-1.5 rounded-lg transition-colors ${searchQuery ? 'text-accent-blue bg-accent-blue/10' : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'}`}
          title="Search outputs"
        >
          <Search size={14} />
        </button>
      )}
      <button
        onClick={() => setFiltersOpen(open => !open)}
        className={`relative p-1.5 rounded-lg transition-colors ${activeFilterCount ? 'text-accent-blue bg-accent-blue/10' : 'text-text-muted hover:text-text-secondary hover:bg-bg-hover'}`}
        title="Filter by generation metadata"
        aria-expanded={filtersOpen}
      >
        <SlidersHorizontal size={14} />
        {activeFilterCount > 0 && (
          <span className="absolute -right-1 -top-1 min-w-3.5 rounded-full bg-accent-blue px-1 text-center text-[8px] leading-3.5 text-white">
            {activeFilterCount}
          </span>
        )}
      </button>
      {filtersOpen && (
        <div className="fixed left-2 right-2 top-16 z-[80] max-h-[calc(100vh-5rem)] overflow-y-auto rounded-lg border border-border bg-bg-secondary p-3 shadow-2xl sm:absolute sm:left-0 sm:right-auto sm:top-full sm:mt-2 sm:w-[420px]">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div>
              <p className="text-xs font-medium text-text-primary">Generation metadata</p>
              <p className="text-[9px] text-text-muted">All selected fields must match.</p>
            </div>
            <div className="flex items-center gap-1">
              {activeFilterCount > 0 && (
                <button onClick={clearStructuredFilters} className="rounded px-2 py-1 text-[10px] text-text-muted hover:bg-bg-hover hover:text-text-primary">
                  Clear
                </button>
              )}
              <button onClick={() => setFiltersOpen(false)} className="rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-primary" aria-label="Close metadata filters">
                <X size={13} />
              </button>
            </div>
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
      )}
    </div>
  )
}
