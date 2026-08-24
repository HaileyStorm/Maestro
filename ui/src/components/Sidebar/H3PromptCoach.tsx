import { reviewH3Prompt, type H3PromptReviewInput, type H3PromptReviewStatus } from '../../lib/h3PromptReview'

const STATUS_CUE: Record<H3PromptReviewStatus, string> = {
  noted: '✓',
  info: 'i',
  consider: '→',
}

export function H3PromptCoach(props: H3PromptReviewInput) {
  const review = reviewH3Prompt(props)
  if (!review) return null
  const considerCount = review.checks.filter(check => check.status === 'consider').length

  return (
    <details className="group rounded-lg border border-border bg-bg-tertiary/50 text-[9px] text-text-muted">
      <summary className="mobile-control-target flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-2 py-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className="flex shrink-0 gap-0.5">
          <span className="h-2.5 w-1.5 rounded-sm border border-accent-blue/50 bg-accent-blue/15" />
          <span className="h-2.5 w-1.5 rounded-sm border border-accent-green/50 bg-accent-green/15" />
          <span className="h-2.5 w-1.5 rounded-sm border border-accent-blue/50 bg-accent-blue/15" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] font-medium text-text-secondary">Prompt Coach</span>
          <span className="block leading-tight">Structural review only · your prompt is unchanged</span>
        </span>
        <span className="shrink-0 rounded-full border border-border bg-bg-secondary px-1.5 py-0.5 font-medium text-text-secondary">
          {considerCount ? `${considerCount} to consider` : `${review.checks.length} notes`}
        </span>
        <span role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          Prompt Coach structural review updated: {considerCount} to consider, {review.checks.length} total checks.
        </span>
      </summary>
      <ul className="space-y-1 border-t border-border px-2 py-2" aria-label="H3 structural prompt review">
        {review.checks.map(check => (
          <li key={check.id} data-check-id={check.id} className="flex items-start gap-2 rounded-md bg-bg-secondary/70 px-2 py-1.5">
            <span aria-hidden="true" className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[8px] font-bold ${
              check.status === 'noted' ? 'border-accent-green/50 text-accent-green'
                : check.status === 'consider' ? 'border-accent-blue/50 text-accent-blue'
                  : 'border-border text-text-muted'
            }`}>{STATUS_CUE[check.status]}</span>
            <span className="min-w-0">
              <span className="block font-medium text-text-secondary">{check.label}</span>
              <span className="block leading-relaxed">{check.detail}</span>
              <span className="sr-only">Status: {check.status}.</span>
            </span>
          </li>
        ))}
      </ul>
    </details>
  )
}
