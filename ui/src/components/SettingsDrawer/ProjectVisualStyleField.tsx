import { useEffect, useRef, useState } from 'react'
import { currentDirectorQueueScope, useStore } from '../../stores/useStore'
import { fetchProjectAssets, fetchProjectVisualStyleLibrary, getProjectAssetMediaUrl, projectAssetOutputNeedsInitialBlur, resolveProjectVisualStyleReference, saveProjectVisualStyle } from '../../api/client'
import type { ProjectAssetOutput } from '../../api/client'
import type { CustomVisualStyleReference, ProjectVisualStyle } from '../../types'
import { VisualStyleField } from './VisualStyleField'

type Choice = {
  key: string
  label: string
  output: ProjectAssetOutput
  selector: Omit<CustomVisualStyleReference, 'expected_sha256'>
}
type Picker = { choices: Choice[]; selected: string[]; original: CustomVisualStyleReference[] }
const referenceKey = (reference: Omit<CustomVisualStyleReference, 'expected_sha256'>) => JSON.stringify([reference.asset_id, reference.variant_id, reference.output_index])
const button = 'mobile-control-target rounded border border-border px-3 py-2 text-xs text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50'

export function ProjectVisualStyleField() {
  const project = useStore(state => state.activeWorkspace)
  const account = useStore(state => state.accountContext)
  const epoch = useStore(state => state.directorStyleAuthoringEpoch)
  // Account and project changes retire the entire authoring surface and its callbacks.
  void account
  const scope = currentDirectorQueueScope()
  return <ScopedProjectVisualStyleField key={JSON.stringify([scope, epoch])} project={project} scope={scope} epoch={epoch} />
}

function ScopedProjectVisualStyleField({ project, scope, epoch }: { project: string; scope: string; epoch: number }) {
  const selection = useStore(state => state.directorSavedVisualStyle)
  const setSelection = useStore(state => state.setDirectorSavedVisualStyle)
  const legacy = useStore(state => state.servicesConfig?.use_director_v2 === false)
  const planning = useStore(state => state.directorLoading)
  const [styles, setStyles] = useState<ProjectVisualStyle[]>([])
  const [instance, setInstance] = useState('')
  const instanceRef = useRef('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [picker, setPicker] = useState<Picker | null>(null)
  const [pickerError, setPickerError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [revealed, setRevealed] = useState<string[]>([])
  const alive = useRef(true)
  const resolver = useRef<{ resolve: (references: CustomVisualStyleReference[]) => void; reject: (error: Error) => void } | null>(null)
  const current = () => alive.current && currentDirectorQueueScope() === scope && useStore.getState().directorStyleAuthoringEpoch === epoch
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      resolver.current?.reject(new DOMException('The authoring surface changed', 'AbortError'))
      resolver.current = null
    }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setLoading(true); setError('')
    void fetchProjectVisualStyleLibrary(project, controller.signal).then(result => {
      if (typeof result.project_instance !== 'string' || !result.project_instance) throw new Error('Project identity unavailable')
      if (active && current()) {
        if (instanceRef.current && instanceRef.current !== result.project_instance) setSelection(null, scope)
        instanceRef.current = result.project_instance
        setInstance(result.project_instance); setStyles(result.styles)
      }
    }).catch(() => {
      if (active && current()) setError('Could not load saved directions. Reload to try again.')
    }).finally(() => { if (active && current()) setLoading(false) })
    return () => { active = false; controller.abort() }
    // Scope and request lifetime are fixed by the outer component key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, reload])

  async function confirmReferences() {
    if (!picker || !current() || confirming) return
    const choices = picker.selected.map(key => picker.choices.find(choice => choice.key === key))
    if (choices.some(choice => !choice)) { setPickerError('Remove unavailable references before using this selection.'); return }
    setConfirming(true); setPickerError('')
    const expectedInstance = instanceRef.current
    try {
      const references: CustomVisualStyleReference[] = []
      for (const choice of choices) {
        if (!current()) return
        const reference = await resolveProjectVisualStyleReference(project, choice!.selector, expectedInstance)
        if (!current() || instanceRef.current !== expectedInstance) return
        references.push(reference)
      }
      resolver.current?.resolve(references); resolver.current = null; setPicker(null)
    } catch { if (current()) setPickerError('A reference changed or is unavailable. Reload references and try again.') }
    finally { if (current()) setConfirming(false) }
  }

  return <>
    <VisualStyleField scopeKey={JSON.stringify([scope, instance])} authoringKey={String(epoch)} styles={styles} selection={selection} loading={loading} disabled={planning || legacy || !project || !instance}
      error={legacy ? 'Enable Director v2 to use saved visual directions.' : error} referenceDelivery="text_only"
      onSelect={value => { if (current()) setSelection(value, scope) }}
      onSave={async input => {
        if (!current()) throw new DOMException('The authoring surface changed', 'AbortError')
        const expectedInstance = instanceRef.current
        if (!expectedInstance) throw new Error('Project identity unavailable')
        const saved = await saveProjectVisualStyle(project, { name: input.name, definition: input.definition }, input.current, expectedInstance)
        if (!current() || instanceRef.current !== expectedInstance) throw new DOMException('The authoring surface changed', 'AbortError')
        setStyles(previous => [...previous.filter(style => style.asset_id !== saved.asset_id), saved])
        return saved
      }}
      onPickReferences={async original => {
        if (!current()) throw new DOMException('The authoring surface changed', 'AbortError')
        const assets = await fetchProjectAssets(project)
        if (!current()) throw new DOMException('The authoring surface changed', 'AbortError')
        const choices: Choice[] = assets.flatMap(asset => asset.variants.flatMap(variant => variant.status === 'kept'
          ? variant.outputs.flatMap((output, index) => {
            if (!output.media_type.toLowerCase().startsWith('image/')) return []
            const selector = { asset_id: asset.id, variant_id: variant.id, output_index: index }
            return [{ key: referenceKey(selector), selector, output, label: `${asset.name} · ${variant.label || 'Kept reference'} · ${output.label || `Image ${index + 1}`}` }]
          }) : []))
        setPickerError(''); setRevealed([])
        setPicker({ choices, selected: original.map(referenceKey), original })
        return new Promise<CustomVisualStyleReference[]>((resolve, reject) => { resolver.current = { resolve, reject } })
      }} />
    <button type="button" className={`${button} mt-1`} disabled={loading || planning || picker !== null} onClick={() => setReload(value => value + 1)}>Reload saved directions</button>
    {selection && <button type="button" className={`${button} mt-1 ml-1`} disabled={planning} onClick={() => { if (current()) setSelection(null, scope) }}>Clear saved direction</button>}
    {picker && <fieldset className="mt-2 min-w-0 rounded border border-border bg-bg-tertiary p-2 space-y-2">
      <legend className="px-1 text-xs text-text-secondary">Kept image references</legend>
      <p className="text-xs text-text-secondary">Choose up to eight images from this project. Only text guidance is delivered in this mode.</p>
      {picker.choices.length === 0 && <p className="text-xs text-text-secondary">Keep an image in Reference to make it available here.</p>}
      {picker.choices.map(choice => {
        const hidden = projectAssetOutputNeedsInitialBlur(choice.output) && !revealed.includes(choice.key)
        const checked = picker.selected.includes(choice.key)
        return <div key={choice.key} className="min-w-0 rounded border border-border p-2 space-y-1">
          <label className="mobile-control-target flex items-center gap-2 text-xs text-text-primary"><input type="checkbox" checked={checked} disabled={confirming || (!checked && picker.selected.length >= 8)} onChange={() => setPicker(previous => previous && ({ ...previous, selected: checked ? previous.selected.filter(key => key !== choice.key) : [...previous.selected, choice.key] }))} />{choice.label}</label>
          <img alt={hidden ? 'Hidden reference preview' : choice.label} src={getProjectAssetMediaUrl(project, choice.output.relative_path)} className={`h-24 max-w-full object-contain ${hidden ? 'blur-xl' : ''}`} />
          {projectAssetOutputNeedsInitialBlur(choice.output) && <button type="button" className={button} onClick={() => setRevealed(previous => hidden ? [...previous, choice.key] : previous.filter(key => key !== choice.key))}>{hidden ? 'Reveal preview' : 'Hide preview'}</button>}
        </div>
      })}
      {picker.selected.filter(key => !picker.choices.some(choice => choice.key === key)).map(key => <button key={key} type="button" disabled={confirming} className={button} onClick={() => setPicker(previous => previous && ({ ...previous, selected: previous.selected.filter(item => item !== key) }))}>Remove unavailable reference</button>)}
      {pickerError && <p role="alert" className="text-xs text-text-secondary">{pickerError}</p>}
      <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={confirming} onClick={() => void confirmReferences()}>{confirming ? 'Checking references…' : 'Use selected references'}</button><button type="button" className={button} disabled={confirming} onClick={() => { resolver.current?.resolve(picker.original); resolver.current = null; setPicker(null) }}>Cancel</button></div>
    </fieldset>}
  </>
}
