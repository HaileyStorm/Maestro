import * as api from '../../api/client'
import type { LoraParameterDefinition, LoraParameterValue } from '../../types'

export function LoraParameterField({
  loraId, parameter, values, onChange, onClear, namespace = 'director',
}: {
  loraId: string
  namespace?: string
  parameter: LoraParameterDefinition
  values: Record<string, LoraParameterValue>
  onChange: (value: LoraParameterValue) => void
  onClear: () => void
}) {
  const value = api.getLoraParameterValue(parameter, values)
  const id = `${namespace}-${loraId}-${parameter.id}`.replace(/[^a-z0-9_-]/gi, '-')
  const label = `${parameter.label}${parameter.required ? ' (required)' : ''}`
  if (parameter.type === 'boolean') {
    return (
      <label htmlFor={id} className="block text-[9px] text-text-secondary">
        {label}
        <select id={id} value={value === true ? 'true' : value === false ? 'false' : ''} onChange={event => {
          if (event.target.value === '') onClear()
          else onChange(event.target.value === 'true')
        }} className="mobile-control-target mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
          <option value="">Choose…</option>
          <option value="false">No</option>
          <option value="true">Yes</option>
        </select>
      </label>
    )
  }
  if (parameter.type === 'enum') {
    const token = value === undefined ? '' : api.getLoraParameterOptionToken(value)
    return (
      <label htmlFor={id} className="block text-[9px] text-text-secondary">
        {label}
        <select id={id} value={token} onChange={event => {
          const option = parameter.options?.find(candidate => (
            api.getLoraParameterOptionToken(candidate.value) === event.target.value
          ))
          if (event.target.value === '') onClear()
          else if (option) onChange(option.value)
        }} className="mobile-control-target mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
          <option value="">Choose…</option>
          {(parameter.options ?? []).map(option => (
            <option key={api.getLoraParameterOptionToken(option.value)} value={api.getLoraParameterOptionToken(option.value)}>{option.label}</option>
          ))}
        </select>
      </label>
    )
  }
  if (parameter.type === 'number' || parameter.type === 'integer') {
    return (
      <label htmlFor={id} className="block text-[9px] text-text-secondary">
        {label}
        <input id={id} type="number" value={typeof value === 'number' ? value : ''} min={parameter.minimum} max={parameter.maximum} step={parameter.step ?? (parameter.type === 'integer' ? 1 : 'any')} onChange={event => {
          if (event.target.value === '') onClear()
          else if (Number.isFinite(event.target.valueAsNumber)) onChange(event.target.valueAsNumber)
        }} className="mobile-control-target mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />
      </label>
    )
  }
  return (
    <label htmlFor={id} className="block text-[9px] text-text-secondary">
      {label}
      <input id={id} type="text" value={typeof value === 'string' ? value : ''} minLength={parameter.min_length} maxLength={parameter.max_length} onChange={event => onChange(event.target.value)} className="mobile-control-target mt-0.5 w-full rounded border border-border bg-bg-primary px-1.5 py-1 text-[9px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue" />
    </label>
  )
}
