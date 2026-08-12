import { useId } from 'react'

export type H3DurationPlanBarProps = {
  /** Original target, in frames included in the finished video. */
  targetPublishedFrames: number
  /** Current planned total, in frames included in the finished video. */
  currentPublishedFrames: number
  /** Current generated total before extra frames are trimmed. */
  currentGeneratedFrames: number
  /** Current total minus the original target. */
  currentMinusTargetFrames: number
} & (
  | { outcome: 'exact'; reason?: string }
  | { outcome: 'acceptable'; reason: string }
  | { outcome: 'insufficient_capacity'; reason: string }
)

const TRACK_START_X = 28
const TRACK_END_X = 612
const TARGET_MARKER_X = 466

function currentMarkerX(currentPublishedFrames: number, targetPublishedFrames: number): number {
  const target = Math.max(1, targetPublishedFrames)
  const targetTrackWidth = TARGET_MARKER_X - TRACK_START_X
  const maximumRatio = (TRACK_END_X - TRACK_START_X) / targetTrackWidth
  const ratio = Math.min(maximumRatio, Math.max(0, currentPublishedFrames / target))
  return TRACK_START_X + ratio * targetTrackWidth
}

function signedFrames(frames: number): string {
  if (frames > 0) return `+${frames}`
  return `${frames}`
}

function mismatchDirection(frames: number): string {
  if (frames > 0) return 'longer than target'
  if (frames < 0) return 'shorter than target'
  return 'on target'
}

function outcomeLabel(outcome: H3DurationPlanBarProps['outcome']): string {
  if (outcome === 'acceptable') return '≈ Close to target'
  if (outcome === 'insufficient_capacity') return '! Cannot reach target'
  return '✓ Matches target'
}

export function H3DurationPlanBar(props: H3DurationPlanBarProps) {
  const titleId = useId()
  const descriptionId = useId()
  const currentX = currentMarkerX(props.currentPublishedFrames, props.targetPublishedFrames)
  const outcomeText = outcomeLabel(props.outcome)
  const reason = props.reason || 'The current video length matches the original target.'
  const hasGeneratedTail = props.currentGeneratedFrames > props.currentPublishedFrames
  const generatedTailText = hasGeneratedTail
    ? `${props.currentGeneratedFrames} frames will be generated, and ${props.currentPublishedFrames} will appear in the finished video. The extra ending frames will be trimmed.`
    : `All ${props.currentGeneratedFrames} generated frames will appear in the finished video.`

  return (
    <section
      aria-labelledby={titleId}
      className="min-w-0 rounded-lg border border-border/80 bg-bg-primary/35 p-3"
    >
      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <h3 id={titleId} className="text-[11px] font-semibold text-text-primary">
            How the video length compares
          </h3>
          <p id={descriptionId} className="mt-0.5 break-words text-[9px] leading-relaxed text-text-muted">
            T marks your original target. C shows the current plan. This chart is for comparison only.
          </p>
        </div>
        <span
          className={`w-fit shrink-0 rounded border px-2 py-1 text-[9px] font-semibold ${
            props.outcome === 'insufficient_capacity'
              ? 'border-amber-400/50 bg-amber-500/10 text-amber-200'
              : props.outcome === 'acceptable'
                ? 'border-blue-400/40 bg-blue-500/10 text-blue-200'
                : 'border-emerald-400/40 bg-emerald-500/10 text-emerald-200'
          }`}
        >
          {outcomeText}
        </span>
      </div>

      <figure className="mt-3 min-w-0" aria-labelledby={titleId} aria-describedby={descriptionId}>
        <svg
          role="img"
          aria-labelledby={`${titleId} ${descriptionId}`}
          viewBox="0 0 640 82"
          preserveAspectRatio="xMidYMid meet"
          className="block h-auto w-full min-w-0"
        >
          <title>Original target and current planned video length</title>
          <desc>
            The dashed T marker shows the original target of {props.targetPublishedFrames} frames.
            The solid diamond C marker shows the current plan of {props.currentPublishedFrames} frames,
            {` ${mismatchDirection(props.currentMinusTargetFrames)} by ${signedFrames(props.currentMinusTargetFrames)} frames.`}
          </desc>
          <line
            x1={TRACK_START_X}
            x2={TRACK_END_X}
            y1="42"
            y2="42"
            className="stroke-border"
            strokeWidth="8"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1={TRACK_START_X}
            x2={currentX}
            y1="42"
            y2="42"
            className="stroke-accent-blue"
            strokeWidth="8"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          <g className="fill-amber-300 stroke-amber-300">
            <line
              x1={TARGET_MARKER_X}
              x2={TARGET_MARKER_X}
              y1="14"
              y2="68"
              strokeWidth="2"
              strokeDasharray="4 3"
              vectorEffect="non-scaling-stroke"
            />
            <path d={`M ${TARGET_MARKER_X} 14 h 18 l -5 7 5 7 h -18 Z`} stroke="none" />
            <text x={TARGET_MARKER_X + 7} y="24" textAnchor="middle" className="fill-bg-primary text-[9px] font-bold">T</text>
          </g>
          <g className="fill-accent-blue stroke-bg-primary">
            <path d={`M ${currentX} 33 l 9 9 -9 9 -9 -9 Z`} strokeWidth="2" vectorEffect="non-scaling-stroke" />
            <text x={currentX} y="45" textAnchor="middle" className="fill-white text-[8px] font-bold" stroke="none">C</text>
          </g>
          <text x={TRACK_START_X} y="78" textAnchor="start" className="fill-text-muted text-[9px]">0f</text>
          <text x={TARGET_MARKER_X} y="78" textAnchor="middle" className="fill-text-secondary text-[9px] font-semibold">
            Target {props.targetPublishedFrames}f
          </text>
        </svg>
        <figcaption className="sr-only">
          The current plan is {props.currentPublishedFrames} frames; the original target is {props.targetPublishedFrames} frames;
          the difference is {signedFrames(props.currentMinusTargetFrames)} frames, {mismatchDirection(props.currentMinusTargetFrames)}.
        </figcaption>
      </figure>

      <dl className="mt-2 grid min-w-0 grid-cols-1 gap-2 text-[9px] sm:grid-cols-3">
        <div className="min-w-0 rounded border border-border/70 px-2 py-1.5">
          <dt className="text-text-muted">Original target · T</dt>
          <dd className="mt-0.5 break-words font-semibold text-text-primary">{props.targetPublishedFrames} frames</dd>
        </div>
        <div className="min-w-0 rounded border border-border/70 px-2 py-1.5">
          <dt className="text-text-muted">Current plan · C</dt>
          <dd className="mt-0.5 break-words font-semibold text-text-primary">{props.currentPublishedFrames} frames</dd>
        </div>
        <div className="min-w-0 rounded border border-border/70 px-2 py-1.5">
          <dt className="text-text-muted">Difference · C − T</dt>
          <dd className="mt-0.5 break-words font-semibold text-text-primary">
            {signedFrames(props.currentMinusTargetFrames)} frames · {mismatchDirection(props.currentMinusTargetFrames)}
          </dd>
        </div>
      </dl>

      <div
        className={`mt-2 rounded border px-2.5 py-2 text-[9px] leading-relaxed ${
          props.outcome === 'insufficient_capacity'
            ? 'border-amber-400/40 bg-amber-500/10 text-amber-100'
            : 'border-border/70 bg-bg-tertiary/30 text-text-secondary'
        }`}
      >
        <strong className="font-semibold">{outcomeText}:</strong> <span className="break-words">{reason}</span>
      </div>

      <p className="mt-2 break-words text-[9px] leading-relaxed text-text-muted">
        <strong className="text-text-secondary">Extra generated frames:</strong> {generatedTailText}
      </p>

      <table className="sr-only">
        <caption>H3 video length comparison</caption>
        <thead>
          <tr><th scope="col">Measure</th><th scope="col">Frames</th><th scope="col">Meaning</th></tr>
        </thead>
        <tbody>
          <tr><th scope="row">Original target</th><td>{props.targetPublishedFrames}</td><td>Target marker T</td></tr>
          <tr><th scope="row">Current plan</th><td>{props.currentPublishedFrames}</td><td>Current marker C</td></tr>
          <tr><th scope="row">Difference</th><td>{signedFrames(props.currentMinusTargetFrames)}</td><td>{mismatchDirection(props.currentMinusTargetFrames)}</td></tr>
          <tr><th scope="row">Frames generated</th><td>{props.currentGeneratedFrames}</td><td>{hasGeneratedTail ? 'Extra ending frames will be trimmed' : 'All generated frames will be used'}</td></tr>
          <tr><th scope="row">Result</th><td colSpan={2}>{outcomeText}: {reason}</td></tr>
        </tbody>
      </table>
    </section>
  )
}
