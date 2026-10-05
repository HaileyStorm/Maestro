import { useId, useRef, useState } from 'react'
import {
  isCurrentH3PromptRewritePreview,
  type H3PromptRewriteApplySelection,
  type H3PromptRewriteBinding,
  type H3PromptRewriteCandidateKind,
  type H3PromptRewritePreview,
} from '../lib/h3PromptRewritePreview'

export interface H3PromptRewriteComparisonProps {
  preview: H3PromptRewritePreview
  currentRequest: H3PromptRewriteBinding
  onApply: (selection: H3PromptRewriteApplySelection) => void | Promise<void>
}

const labels = {
  deterministic: { title: 'Original', source: 'Your input' },
  base: { title: 'Base model', source: 'Without the H3 adapter' },
  adapted: { title: 'H3 rewrite', source: 'With the H3 adapter' },
}

export function H3PromptRewriteComparison(props: H3PromptRewriteComparisonProps) {
  if (!isCurrentH3PromptRewritePreview(props.preview, props.currentRequest)) {
    return <p role="alert" className="p-4 text-sm text-text-secondary">This comparison no longer matches your prompt. Create a new comparison to continue.</p>
  }
  return <BoundComparison key={props.preview.commitment + ':' + props.currentRequest.requestCommitment} {...props} />
}

function BoundComparison({ preview, onApply }: H3PromptRewriteComparisonProps) {
  const id = useId()
  const [selected, setSelected] = useState<H3PromptRewriteCandidateKind | null>(null)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState(false)
  const inFlight = useRef(false)

  async function apply() {
    if (!selected || inFlight.current) return
    inFlight.current = true
    setApplying(true)
    setError(false)
    try {
      await onApply(Object.freeze({ selected_kind: selected,
        request_commitment: preview.request_commitment, preview_commitment: preview.commitment }))
    } catch {
      setError(true)
    } finally {
      inFlight.current = false
      setApplying(false)
    }
  }

  return (
    <section aria-labelledby={id + '-title'} aria-busy={applying} className="min-w-0 bg-bg-secondary p-4 text-text-primary sm:p-6">
      <h2 id={id + '-title'} className="text-xl font-semibold">Compare prompts</h2>
      <p id={id + '-help'} className="mt-2 text-sm text-text-secondary">Choose a version, then apply it to your prompt.</p>
      <fieldset disabled={applying} aria-describedby={id + '-help'} className="mt-5 min-w-0 space-y-3">
        <legend className="sr-only">Prompt version</legend>
        {preview.candidates.map(candidate => {
          const label = labels[candidate.kind]
          const titleId = id + '-' + candidate.kind
          return (
            <label key={candidate.kind} className={`block min-w-0 cursor-pointer rounded-xl border p-4 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent-blue ${selected === candidate.kind ? 'border-accent-blue bg-bg-active' : 'border-border bg-bg-tertiary hover:border-border-light'}`}>
              <span className="flex min-h-11 items-center gap-3">
                <input type="radio" name={id + '-version'} value={candidate.kind} checked={selected === candidate.kind}
                  aria-labelledby={titleId} onChange={() => { setSelected(candidate.kind); setError(false) }}
                  className="h-5 w-5 shrink-0 accent-accent-blue" />
                <span>
                  <span id={titleId} className="block font-semibold">{label.title}</span>
                  <span className="block text-sm text-text-secondary">{label.source}</span>
                </span>
              </span>
              <span className="mt-3 block min-w-0 whitespace-pre-wrap rounded-lg border border-border bg-bg-secondary p-3 text-sm leading-relaxed [overflow-wrap:anywhere]">{candidate.text}</span>
            </label>
          )
        })}
      </fieldset>
      <div className="mt-5 flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between">
        <p role="status" className="text-sm text-text-secondary">{selected ? labels[selected].title + ' selected' : 'No version selected'}</p>
        <button type="button" onClick={() => { void apply() }} disabled={!selected || applying}
          className="min-h-11 rounded-lg bg-cta px-5 py-3 text-sm font-semibold text-cta-foreground hover:ring-2 hover:ring-accent-blue/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-blue disabled:cursor-not-allowed disabled:opacity-50">
          {applying ? 'Applying…' : 'Apply selected prompt'}
        </button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-text-secondary">Could not apply this version. Try again.</p>}
    </section>
  )
}
