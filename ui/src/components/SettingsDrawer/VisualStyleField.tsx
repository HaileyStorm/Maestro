import { useEffect, useRef, useState } from 'react'

import type {
  CustomVisualStyleBorrowScope as BorrowScope,
  CustomVisualStyleReference as StyleReference,
  CustomVisualStyleDefinition as StyleDefinition,
  CustomVisualStyleSelection as StyleSelection,
  ProjectVisualStyle as SavedStyle,
} from '../../types'

type Props = {
  scopeKey: string
  authoringKey: string
  styles: SavedStyle[]
  selection: StyleSelection | null
  loading?: boolean
  disabled?: boolean
  error?: string
  referenceDelivery: 'text_only' | 'compatible'
  onSelect: (selection: StyleSelection | null) => void
  onSave: (input: { name: string; definition: StyleDefinition; current: StyleSelection | null }) => Promise<SavedStyle>
  onPickReferences: (current: StyleReference[]) => Promise<StyleReference[]>
}
const scopes: [BorrowScope, string][] = [['palette', 'Palette'], ['materials', 'Materials'], ['lighting', 'Lighting'], ['camera_motion', 'Camera motion']]
const emptyDefinition = (): StyleDefinition => ({ version: 1, direction: '', borrow_scope: ['palette', 'lighting'], references: [] })
const copy = <T,>(value: T): T => structuredClone(value)
const control = 'mobile-control-target w-full rounded border border-border bg-bg-secondary px-2 py-1.5 text-xs text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue'
const action = 'mobile-control-target max-w-full whitespace-normal rounded border border-border px-3 py-2 text-xs text-text-primary hover:bg-bg-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:opacity-50'

/** Bind authoringKey to the current Director request lifetime, including resets. */
export function VisualStyleField(props: Props) {
  const lifetime = JSON.stringify([props.scopeKey, props.authoringKey, props.selection?.asset_id, props.selection?.revision])
  return <ScopedVisualStyleField key={lifetime} {...props} />
}

function ScopedVisualStyleField({ styles, selection, loading, disabled, error, referenceDelivery, onSelect, onSave, onPickReferences }: Props) {
  const [editing, setEditing] = useState(false)
  const [current, setCurrent] = useState<StyleSelection | null>(null)
  const [name, setName] = useState('')
  const [definition, setDefinition] = useState<StyleDefinition>(emptyDefinition)
  const [pending, setPending] = useState<'save' | 'references' | null>(null)
  const [message, setMessage] = useState('')
  const alive = useRef(true)
  const operation = useRef(0)
  const editorRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const counter = operation
    alive.current = true
    return () => { alive.current = false; counter.current++ }
  }, [])
  useEffect(() => { if (editing) editorRef.current?.focus() }, [editing])
  const chosen = styles.find(style => style.asset_id === selection?.asset_id)
  const stale = !!selection && (!chosen || chosen.revision !== selection.revision)
  const editingStale = !!current && !styles.some(style => style.asset_id === current.asset_id && style.revision === current.revision)
  const busy = !!disabled || !!loading || pending !== null
  const valid = !!name.trim() && name.length <= 200 && !!definition.direction.trim() && definition.direction.length <= 10000 && definition.borrow_scope.length > 0 && definition.references.length <= 8
  function begin(style?: SavedStyle) {
    operation.current++
    setCurrent(style ? { asset_id: style.asset_id, revision: style.revision } : null)
    setName(style?.name || '')
    setDefinition(style ? copy(style.definition) : emptyDefinition())
    setMessage('')
    setEditing(true)
  }
  async function save() {
    if (busy || !valid || editingStale) return
    const token = ++operation.current
    setPending('save'); setMessage('')
    try {
      const saved = await onSave({ name, definition: copy(definition), current: current && copy(current) })
      if (!alive.current || token !== operation.current) return
      onSelect({ asset_id: saved.asset_id, revision: saved.revision })
      setEditing(false)
    } catch { if (alive.current && token === operation.current) setMessage('Could not save this style. Reload changed styles and try again.') }
    finally { if (alive.current && token === operation.current) setPending(null) }
  }
  async function pick() {
    if (busy) return
    const token = ++operation.current
    setPending('references'); setMessage('')
    try {
      const references = await onPickReferences(copy(definition.references))
      if (!alive.current || token !== operation.current) return
      if (references.length > 8) { setMessage('Choose up to eight references.'); return }
      setDefinition(previous => ({ ...previous, references: copy(references) }))
    } catch { if (alive.current && token === operation.current) setMessage('Could not select references. Try again.') }
    finally { if (alive.current && token === operation.current) setPending(null) }
  }
  return <fieldset className="min-w-0 rounded-lg border border-border bg-bg-tertiary/50 p-2 space-y-2">
    <legend className="px-1 text-[10px] font-medium text-text-secondary">Saved visual direction</legend>
    <select aria-label="Saved visual direction" className={control} value={selection?.asset_id || ''} disabled={busy || editing} onChange={event => {
      const style = styles.find(item => item.asset_id === event.target.value)
      onSelect(style ? { asset_id: style.asset_id, revision: style.revision } : null)
    }}>
      <option value="">None</option>
      {selection && !chosen && <option value={selection.asset_id}>Unavailable style</option>}
      {styles.map(style => <option key={style.asset_id} value={style.asset_id}>{style.name}</option>)}
    </select>
    <div className="flex flex-wrap gap-2">
      <button type="button" className={action} disabled={busy || editing} onClick={() => begin()}>New style</button>
      <button type="button" className={action} disabled={busy || editing || !chosen || stale} onClick={() => begin(chosen)}>Edit style</button>
      {stale && chosen && <button type="button" className={action} disabled={busy || editing} onClick={() => onSelect({ asset_id: chosen.asset_id, revision: chosen.revision })}>Use latest version</button>}
    </div>
    {loading && <p role="status" className="text-xs text-text-secondary">Loading styles…</p>}
    {!loading && styles.length === 0 && <p className="text-xs text-text-secondary">Save a direction to reuse it in this project.</p>}
    {stale && <p role="alert" className="text-xs text-text-secondary">This style changed or is unavailable. Choose its latest version before planning.</p>}
    {editing && <div className="space-y-2 border-t border-border pt-2">
      <label className="block text-xs text-text-secondary">Name<input ref={editorRef} className={`${control} mt-1`} value={name} maxLength={200} disabled={busy} onChange={event => setName(event.target.value)} /></label>
      <label className="block text-xs text-text-secondary">Direction<textarea className={`${control} mt-1 resize-y`} rows={4} value={definition.direction} maxLength={10000} disabled={busy} onChange={event => setDefinition(previous => ({ ...previous, direction: event.target.value }))} placeholder="Amber highlights and cool shadows" /></label>
      <fieldset className="min-w-0"><legend className="text-xs text-text-secondary">Borrow only</legend><div className="grid grid-cols-1 min-[280px]:grid-cols-2 gap-1 mt-1">
        {scopes.map(([scope, label]) => <label key={scope} className="mobile-control-target flex items-center gap-2 text-xs text-text-primary rounded p-1"><input type="checkbox" className="focus-visible:outline-2 focus-visible:outline-accent-blue" disabled={busy} checked={definition.borrow_scope.includes(scope)} onChange={event => setDefinition(previous => ({ ...previous, borrow_scope: scopes.map(([key]) => key).filter(key => key === scope ? event.target.checked : previous.borrow_scope.includes(key)) }))} />{label}</label>)}
      </div></fieldset>
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-text-secondary">References: {definition.references.length}/8</span><button type="button" className={action} disabled={busy} onClick={() => void pick()}>Choose references</button></div>
      {editingStale && <p role="alert" className="text-xs text-text-secondary">This saved style changed while you were editing. Cancel and reopen its latest version.</p>}
      <div className="flex flex-wrap gap-2"><button type="button" className={`${action} bg-accent-blue/20`} disabled={busy || !valid || editingStale} onClick={() => void save()}>{pending === 'save' ? 'Saving…' : 'Save and use'}</button><button type="button" className={action} disabled={pending !== null} onClick={() => { operation.current++; setEditing(false); setMessage('') }}>Cancel</button></div>
    </div>}
    <p className="text-[11px] leading-relaxed text-text-secondary">Your artistic medium and shot direction stay separate.</p>
    <p className="text-[11px] leading-relaxed text-text-secondary">{referenceDelivery === 'text_only' ? 'This mode uses text guidance only. Reference images will not be sent.' : 'Compatible reference images are checked when planning. Camera motion remains text guidance.'}</p>
    {(error || message) && <p role="alert" className="text-xs text-text-secondary">{error || message}</p>}
  </fieldset>
}
