import { useId } from 'react'
import { BarChart3, ChevronRight, ExternalLink } from 'lucide-react'
import {
  PERFORMANCE_HISTORY_PRIVACY_NOTE,
  PERFORMANCE_HISTORY_CHART_MAX,
  PERFORMANCE_TREND_SERIES,
  PUBLISHED_PERFORMANCE_REFERENCES,
  type PerformanceMarker,
  type PerformanceTrendPoint,
} from '../lib/performanceHistory'

const chart = { left: 52, right: 680, top: 22, bottom: 220, max: PERFORMANCE_HISTORY_CHART_MAX }
const tickValues = [1, 2, 3, 4, 5] as const

function pointX(index: number, count: number): number {
  return chart.left + (index * (chart.right - chart.left)) / Math.max(1, count - 1)
}

function pointY(value: number): number {
  return chart.bottom - (value / chart.max) * (chart.bottom - chart.top)
}

function Marker({ marker, x, y, filled = true }: { marker: PerformanceMarker; x: number; y: number; filled?: boolean }) {
  const common = {
    fill: filled ? 'currentColor' : 'var(--color-bg-secondary)',
    stroke: 'currentColor',
    strokeWidth: 2,
    vectorEffect: 'non-scaling-stroke' as const,
  }
  if (marker === 'square') return <rect x={x - 4} y={y - 4} width="8" height="8" rx="1" {...common} />
  if (marker === 'triangle') return <path d={`M ${x} ${y - 5} L ${x + 5} ${y + 4} L ${x - 5} ${y + 4} Z`} {...common} />
  return <circle cx={x} cy={y} r="4" {...common} />
}

function seriesClass(seriesId: PerformanceTrendPoint['seriesId']): string {
  if (seriesId === 'iteration') return 'text-accent-blue'
  return 'text-text-secondary'
}

function formatDate(date: string): string {
  const [year, month, day] = date.split('-')
  return day ? `${year}-${month}-${day}` : `${year}-${month}`
}

function sourceKindLabel(kind: 'measured_published' | 'adapted_published'): string {
  return kind === 'measured_published' ? 'Published measurement' : 'Published comparison'
}

export function PerformanceHistoryChart() {
  const titleId = useId()
  const descriptionId = useId()
  const pointCount = PERFORMANCE_TREND_SERIES[0]?.points.length ?? 0
  const datePoints = PERFORMANCE_TREND_SERIES[0]?.points ?? []

  return (
    <details className="group mt-5 rounded-xl border border-border bg-bg-tertiary/35">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3.5 py-3 text-xs font-semibold text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue [&::-webkit-details-marker]:hidden">
        <BarChart3 size={14} aria-hidden="true" className="text-accent-blue" />
        How generation settings have changed
        <span className="text-[9px] font-normal text-text-muted">step-count estimates</span>
        <ChevronRight size={14} aria-hidden="true" className="ml-auto transition-transform motion-reduce:transition-none group-open:rotate-90" />
      </summary>

      <div className="border-t border-border px-3 py-4 sm:px-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          <div>
            <h3 className="text-xs font-semibold text-text-primary">Estimated relative denoising step-count reduction</h3>
            <p className="mt-1 max-w-xl text-[10px] leading-relaxed text-text-muted">
              These values estimate relative reductions in documented H3 denoising steps. The native 20-step preset is 1×; higher values mean a
              larger estimated step-count reduction factor—and therefore fewer documented steps. This does not measure wall-clock generation speed
              or elapsed time. Hardware, model, resolution, setup, and other processing all affect timing.
            </p>
          </div>
          <span className="w-fit shrink-0 rounded-full border border-border px-2 py-1 text-[9px] font-medium text-text-muted">
            Estimate · low confidence
          </span>
        </div>

        <figure className="mt-3" aria-labelledby={titleId} aria-describedby={descriptionId}>
          <div
            role="region"
            aria-label="Scrollable estimated relative H3 step-count-reduction chart"
            tabIndex={0}
            className="overflow-x-auto overscroll-x-contain rounded-lg border border-border/80 bg-bg-primary/45 p-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
          >
            <svg
              role="img"
              aria-labelledby={titleId}
              aria-describedby={descriptionId}
              viewBox="0 0 720 274"
              className="block h-auto min-w-[34rem] w-full"
              preserveAspectRatio="xMidYMid meet"
            >
              <title id={titleId}>Estimated relative H3 denoising step-count reduction by Maestro release; higher values mean fewer documented steps</title>
              <desc id={descriptionId}>
                Two estimate lines show relative denoising step-count reduction against the native 20-step preset at 1×, from Maestro 1.5.5 through
                Continuum 0.3. Higher values indicate fewer documented steps. These estimates do not measure wall-clock generation speed or elapsed time.
              </desc>

              {tickValues.map(tick => {
                const y = pointY(tick)
                return (
                  <g key={tick}>
                    <line x1={chart.left} x2={chart.right} y1={y} y2={y} className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
                    <text x={chart.left - 10} y={y + 3} textAnchor="end" className="fill-text-muted text-[9px]">{tick}×</text>
                  </g>
                )
              })}
              <line x1={chart.left} x2={chart.left} y1={chart.top} y2={chart.bottom} className="stroke-text-muted" strokeWidth="1" vectorEffect="non-scaling-stroke" />
              <line x1={chart.left} x2={chart.right} y1={chart.bottom} y2={chart.bottom} className="stroke-text-muted" strokeWidth="1" vectorEffect="non-scaling-stroke" />

              {PERFORMANCE_TREND_SERIES.map(series => {
                const coordinates = series.points.map((point, index) => `${pointX(index, pointCount)},${pointY(point.value)}`).join(' ')
                return (
                  <g key={series.id} className={seriesClass(series.id)}>
                    <polyline
                      points={coordinates}
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeDasharray={series.dash}
                      vectorEffect="non-scaling-stroke"
                    />
                    {series.points.map((point, index) => {
                      const x = pointX(index, pointCount)
                      const y = pointY(point.value)
                      const lowY = pointY(point.uncertainty.low)
                      const highY = pointY(point.uncertainty.high)
                      return (
                        <g key={point.id}>
                          <line x1={x} x2={x} y1={highY} y2={lowY} stroke="currentColor" strokeWidth="1" opacity="0.55" vectorEffect="non-scaling-stroke" />
                          <line x1={x - 3} x2={x + 3} y1={highY} y2={highY} stroke="currentColor" strokeWidth="1" opacity="0.55" vectorEffect="non-scaling-stroke" />
                          <line x1={x - 3} x2={x + 3} y1={lowY} y2={lowY} stroke="currentColor" strokeWidth="1" opacity="0.55" vectorEffect="non-scaling-stroke" />
                          <Marker marker={series.marker} x={x} y={y} />
                        </g>
                      )
                    })}
                  </g>
                )
              })}

              {datePoints.map((point, index) => (
                <g key={point.id}>
                  <text x={pointX(index, pointCount)} y="240" textAnchor="middle" className="fill-text-secondary text-[9px] font-medium">
                    {point.release.replace('Continuum ', '')}
                  </text>
                  <text x={pointX(index, pointCount)} y="253" textAnchor="middle" className="fill-text-muted text-[8px]">
                    {point.date.slice(5)}
                  </text>
                </g>
              ))}
              <text x="16" y="124" transform="rotate(-90 16 124)" textAnchor="middle" className="fill-text-muted text-[9px]">
                relative reduction (higher = fewer steps)
              </text>
            </svg>
          </div>
          <figcaption className="mt-2 text-[9px] leading-relaxed text-text-muted">
            The vertical ranges account for uncertainty from fixed setup and other processing; they are not measured results or
            statistical confidence ranges. Continuum v0.3 rises because its Draft preset documents four steps instead of six. This chart does not
            claim that it generates video faster.
          </figcaption>
        </figure>

        <ul className="mt-3 grid gap-2 sm:grid-cols-2" aria-label="Relative denoising step-count reduction trend legend">
          {PERFORMANCE_TREND_SERIES.map(series => (
            <li key={series.id} className="flex items-start gap-2 rounded-md border border-border/70 bg-bg-primary/30 px-2.5 py-2">
              <svg aria-hidden="true" viewBox="0 0 20 20" className={`mt-0.5 h-4 w-4 shrink-0 ${seriesClass(series.id)}`}>
                <Marker marker={series.marker} x={10} y={10} />
              </svg>
              <span className="text-[9px] leading-relaxed text-text-muted"><strong className="block text-[10px] text-text-primary">{series.label}</strong>{series.description}</span>
            </li>
          ))}
        </ul>

        <section className="mt-4" aria-labelledby={`${titleId}-published`}>
          <h4 id={`${titleId}-published`} className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-muted">
            Published results from other models
          </h4>
          <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
            These results are shown for context only. Different models, hardware, durations, resolutions, and software make a direct comparison or ranking misleading.
          </p>
          <ul className="mt-2 grid gap-2 sm:grid-cols-2">
            {PUBLISHED_PERFORMANCE_REFERENCES.map(reference => (
              <li key={reference.id} className="flex gap-2 rounded-md border border-dashed border-border bg-bg-primary/25 px-2.5 py-2">
                <svg aria-hidden="true" viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 text-text-secondary">
                  <Marker marker={reference.marker} x={10} y={10} filled={false} />
                </svg>
                <div className="min-w-0">
                  <p className="text-[10px] font-semibold text-text-primary">{reference.label}</p>
                  <p className="mt-0.5 text-[9px] leading-relaxed text-text-muted">{reference.displayMetric}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <details className="group/data mt-4 rounded-lg border border-border/80 bg-bg-primary/30">
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 py-2 text-[10px] font-semibold text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue [&::-webkit-details-marker]:hidden">
            Details and sources
            <ChevronRight size={13} aria-hidden="true" className="ml-auto transition-transform motion-reduce:transition-none group-open/data:rotate-90" />
          </summary>
          <div className="border-t border-border p-3">
            <div
              role="region"
              aria-label="Scrollable generation estimate details table"
              tabIndex={0}
              className="overflow-x-auto overscroll-x-contain focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            >
              <table className="w-full min-w-[35rem] border-collapse text-left text-[9px] leading-relaxed text-text-muted">
                <caption className="mb-2 text-left text-[9px] text-text-muted">
                  The connected lines are relative denoising step-count reduction estimates. Published results are listed separately because their setups differ.
                </caption>
                <thead>
                  <tr className="border-b border-border text-text-secondary">
                    <th scope="col" className="px-2 py-1.5 font-semibold">Date / release</th>
                    <th scope="col" className="px-2 py-1.5 font-semibold">Profile / estimate</th>
                    <th scope="col" className="px-2 py-1.5 font-semibold">Data type</th>
                    <th scope="col" className="px-2 py-1.5 font-semibold">How to read it</th>
                    <th scope="col" className="px-2 py-1.5 font-semibold">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {PERFORMANCE_TREND_SERIES.flatMap(series => series.points.map(point => (
                    <tr key={point.id} className="border-b border-border/60 align-top">
                      <th scope="row" className="px-2 py-2 font-normal">{formatDate(point.date)}<span className="block">{point.release}</span></th>
                      <td className="px-2 py-2"><strong className="text-text-secondary">{series.label}</strong><span className="block">{point.value}× ({point.uncertainty.low}–{point.uncertainty.high}×)</span><span className="block">{point.provenance.profile}</span><span className="block">{point.provenance.cohort}</span></td>
                      <td className="px-2 py-2">Estimate from release notes<span className="block">{point.provenance.confidence} confidence</span><span className="block">{point.provenance.hardware}</span></td>
                      <td className="max-w-64 px-2 py-2">{point.provenance.method}<span className="mt-1 block"><strong className="text-text-secondary">Why this value:</strong> {point.provenance.basis}</span><span className="mt-1 block">{point.provenance.comparability}</span></td>
                      <td className="px-2 py-2">
                        <a href={point.provenance.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-1 text-accent-blue underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
                          {point.provenance.sourceTitle}<ExternalLink size={10} aria-hidden="true" />
                        </a>
                      </td>
                    </tr>
                  )))}
                  {PUBLISHED_PERFORMANCE_REFERENCES.map(reference => (
                    <tr key={reference.id} className="border-b border-border/60 align-top">
                      <th scope="row" className="px-2 py-2 font-normal">{formatDate(reference.date)}<span className="block">published result</span></th>
                      <td className="px-2 py-2"><strong className="text-text-secondary">{reference.label}</strong><span className="block">{reference.displayMetric}</span><span className="block">{reference.provenance.profile}</span><span className="block">{reference.provenance.cohort}</span></td>
                      <td className="px-2 py-2">{sourceKindLabel(reference.provenance.kind)}<span className="block">{reference.provenance.confidence} confidence</span><span className="block">{reference.provenance.hardware}</span></td>
                      <td className="max-w-64 px-2 py-2">{reference.provenance.method}<span className="mt-1 block">{reference.provenance.comparability}</span></td>
                      <td className="px-2 py-2">
                        <a href={reference.provenance.sourceUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-1 text-accent-blue underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue">
                          {reference.provenance.sourceTitle}<ExternalLink size={10} aria-hidden="true" />
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </details>

        <p className="mt-3 rounded-md border border-border/70 bg-bg-primary/25 px-2.5 py-2 text-[9px] leading-relaxed text-text-muted">
          {PERFORMANCE_HISTORY_PRIVACY_NOTE}
        </p>
      </div>
    </details>
  )
}
