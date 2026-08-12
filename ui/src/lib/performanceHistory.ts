export type PerformanceProvenanceKind =
  | 'measured_local'
  | 'measured_published'
  | 'adapted_published'
  | 'estimated_backfill'

export type PerformanceConfidence = 'high' | 'medium' | 'low'
export type DatePrecision = 'day' | 'month'
export const PERFORMANCE_HISTORY_CHART_MAX = 5

interface ProvenanceCommon {
  readonly kind: PerformanceProvenanceKind
  readonly sourceTitle: string
  readonly sourceUrl: `https://${string}`
  readonly sourceDate: string
  readonly method: string
  readonly confidence: PerformanceConfidence
  readonly hardware: string
  readonly cohort: string
  readonly profile: string
  readonly comparability: string
}

export interface MeasuredLocalProvenance extends ProvenanceCommon {
  readonly kind: 'measured_local'
  readonly protocol: string
}

export interface MeasuredPublishedProvenance extends ProvenanceCommon {
  readonly kind: 'measured_published'
  readonly publishedMetric: string
}

export interface AdaptedPublishedProvenance extends ProvenanceCommon {
  readonly kind: 'adapted_published'
  readonly publishedMetric: string
  readonly adaptation: string
}

export interface EstimatedBackfillProvenance extends ProvenanceCommon {
  readonly kind: 'estimated_backfill'
  readonly basis: string
}

export type PerformanceProvenance =
  | MeasuredLocalProvenance
  | MeasuredPublishedProvenance
  | AdaptedPublishedProvenance
  | EstimatedBackfillProvenance

export interface PerformanceUncertainty {
  readonly low: number
  readonly high: number
  readonly unit: 'inverse_step_work_x'
}

export type PerformanceMarker = 'circle' | 'square' | 'triangle'

export interface PerformanceTrendPoint {
  readonly id: string
  readonly seriesId: 'iteration' | 'quality'
  readonly date: string
  readonly datePrecision: DatePrecision
  readonly release: string
  readonly value: number
  readonly uncertainty: PerformanceUncertainty
  readonly provenance: EstimatedBackfillProvenance
}

export interface PerformanceTrendSeries {
  readonly id: PerformanceTrendPoint['seriesId']
  readonly label: string
  readonly marker: PerformanceMarker
  readonly dash: string
  readonly description: string
  readonly points: readonly PerformanceTrendPoint[]
}

export interface PublishedPerformanceReference {
  readonly id: string
  readonly label: string
  readonly date: string
  readonly datePrecision: DatePrecision
  readonly displayMetric: string
  readonly marker: PerformanceMarker
  readonly chartComparable: false
  readonly provenance: MeasuredPublishedProvenance | AdaptedPublishedProvenance
}

const maestroSource = 'https://github.com/HaileyStorm/Maestro'

function estimatedPoint(
  id: string,
  seriesId: PerformanceTrendPoint['seriesId'],
  date: string,
  release: string,
  value: number,
  low: number,
  high: number,
  sourceCommit: string,
  profile: string,
  basis: string,
): PerformanceTrendPoint {
  return {
    id,
    seriesId,
    date,
    datePrecision: 'day',
    release,
    value,
    uncertainty: { low, high, unit: 'inverse_step_work_x' },
    provenance: {
      kind: 'estimated_backfill',
      sourceTitle: `Maestro ${release} release evidence`,
      sourceUrl: `${maestroSource}/commit/${sourceCommit}`,
      sourceDate: date,
      method: 'Conservative inverse step-count proxy; its ideal ratio is discounted to illustrate sensitivity to fixed setup, encoding, decoding, and offload overhead.',
      confidence: 'low',
      hardware: '4090-class reference host; no machine measurement asserted',
      cohort: 'MiniMax H3 FL2VA, single native shot, 480p-class output',
      profile,
      comparability: 'Comparable only as an inverse documented-step index after the disclosed profile changes. It is not a measured generation rate or an end-to-end latency claim.',
      basis,
    },
  }
}

const nativeCommit = '8c4979f62fc8b60d84af0b8acc06a44db27388b3'
const turboCommit = '8a662b5b2d3a40b95d3d8edefaae44acc54b7fb7'
const managedCommit = 'd500f58e0c2be948800c757fd106c5254c70b605'
const continuumCommit = 'bead30709e0136e6087f1fff71cf871eef6385b6'

const nativeBasis = 'The v1.5.5 release documents native H3 generation; the 20-step native profile is the 1.0x reference.'
const turboBasis = 'The v1.6.1 release documents an experimental six-step Turbo surface versus the 20-step native reference.'

export const PERFORMANCE_TREND_SERIES = [
  {
    id: 'iteration',
    label: 'Lowest-step documented path',
    marker: 'triangle',
    dash: '2 3',
    description: 'The lowest documented denoising-step path available at each release; profile names and resolutions change.',
    points: [
      estimatedPoint('iteration-155', 'iteration', '2026-08-04', 'v1.5.5', 1, 0.65, 1.2, nativeCommit, 'Native 20-step reference', nativeBasis),
      estimatedPoint('iteration-161', 'iteration', '2026-08-06', 'v1.6.1', 2.2, 1.4, 3.3, turboCommit, 'Experimental six-step Turbo', turboBasis),
      estimatedPoint('iteration-165', 'iteration', '2026-08-08', 'v1.6.5', 2.2, 1.4, 3.3, managedCommit, 'Managed six-step Turbo', 'The exact v1.6.5 release documents a managed six-step preset; no later named profile is backdated into this point.'),
      estimatedPoint('iteration-030', 'iteration', '2026-08-10', 'Continuum v0.3', 3, 1.8, 5, continuumCommit, 'Managed Draft, four-step Turbo at 608×352', 'The Continuum v0.3 commit introduces the named four-step Draft bundle; the upper bound is the undiluted 20/4 step ratio.'),
    ],
  },
  {
    id: 'quality',
    label: 'Native 20-step reference',
    marker: 'circle',
    dash: '1 0',
    description: 'The stable 20-step native reference line.',
    points: [
      estimatedPoint('quality-155', 'quality', '2026-08-04', 'v1.5.5', 1, 0.85, 1.15, nativeCommit, 'Native 20-step path', nativeBasis),
      estimatedPoint('quality-161', 'quality', '2026-08-06', 'v1.6.1', 1, 0.85, 1.15, turboCommit, 'Native 20-step path', 'The optional Turbo surface did not replace the native 20-step path.'),
      estimatedPoint('quality-165', 'quality', '2026-08-08', 'v1.6.5', 1, 0.85, 1.15, managedCommit, 'Native 20-step path', 'The exact v1.6.5 source retains a 20-step native default; the later named Quality bundle is not backdated.'),
      estimatedPoint('quality-030', 'quality', '2026-08-10', 'Continuum v0.3', 1, 0.85, 1.15, continuumCommit, 'Managed Quality, 20 steps at 960×544', 'The Continuum v0.3 commit introduces the named 20-step Quality bundle; this line tracks denoising work only.'),
    ],
  },
] as const satisfies readonly PerformanceTrendSeries[]

export const PUBLISHED_PERFORMANCE_REFERENCES = [
  {
    id: 'ltx-video-h100',
    label: 'LTX-Video paper',
    date: '2024-12-30',
    datePrecision: 'day',
    displayMetric: '5s at 768×512 / 24 fps in 2s on one H100',
    marker: 'circle',
    chartComparable: false,
    provenance: {
      kind: 'measured_published',
      sourceTitle: 'LTX-Video: Realtime Video Latent Diffusion',
      sourceUrl: 'https://arxiv.org/abs/2501.00103',
      sourceDate: '2024-12-30',
      method: 'Official paper result retained without normalization as a context-only absolute latency.',
      confidence: 'high',
      hardware: 'NVIDIA H100',
      cohort: 'LTX-Video paper configuration, approximately 2B parameters before distillation',
      profile: '121 frames, 20 diffusion steps, 768×512, 24 fps, five-second output',
      comparability: 'Different model, accelerator class, runtime, and output contract; not plotted on the Maestro axis.',
      publishedMetric: 'Two seconds wall time for five seconds of output.',
    },
  },
  {
    id: 'wan21-4090',
    label: 'Wan2.1 1.3B',
    date: '2025-02',
    datePrecision: 'month',
    displayMetric: 'about 4 min for 5s / 480p on RTX 4090',
    marker: 'square',
    chartComparable: false,
    provenance: {
      kind: 'measured_published',
      sourceTitle: 'Official Wan2.1 repository',
      sourceUrl: 'https://github.com/Wan-Video/Wan2.1/tree/9737cba9c1c3c4d04b33fcad41c111989865d315',
      sourceDate: '2025-02',
      method: 'Official repository headline result; retained without applying quantization or another optimization.',
      confidence: 'medium',
      hardware: 'NVIDIA RTX 4090',
      cohort: 'Wan2.1 T2V 1.3B',
      profile: 'Five-second 480p output, without optimization techniques such as quantization',
      comparability: 'Different model and runtime; retained as a hollow context marker, not a connected line.',
      publishedMetric: 'Approximately four minutes wall time.',
    },
  },
  {
    id: 'hailuo02-efficiency',
    label: 'MiniMax Hailuo 02',
    date: '2025-06-18',
    datePrecision: 'day',
    displayMetric: '2.5× architecture efficiency claim; no absolute latency',
    marker: 'triangle',
    chartComparable: false,
    provenance: {
      kind: 'adapted_published',
      sourceTitle: 'MiniMax Hailuo 02 launch note',
      sourceUrl: 'https://www.minimax.io/news/minimax-hailuo-02',
      sourceDate: '2025-06-18',
      method: 'The source\'s relative architecture claim about training and inference efficiency is retained as context only; no aggregate metric is inferred.',
      confidence: 'low',
      hardware: 'Not disclosed for the claim',
      cohort: 'Hailuo 02 architecture versus a prior comparable parameter scale',
      profile: 'Relative architecture claim; product modes are 768p/6s, 768p/10s, and 1080p/6s',
      comparability: 'No absolute time or matching hardware; excluded from both axes and all Maestro calculations.',
      publishedMetric: '2.5× training and inference efficiency at comparable parameter scale.',
      adaptation: 'Shown as a relative-only annotation; it is not converted into wall time or a local generation rate.',
    },
  },
  {
    id: 'hunyuan15-4090',
    label: 'HunyuanVideo-1.5',
    date: '2025-12-05',
    datePrecision: 'day',
    displayMetric: '480p I2V within 75s on RTX 4090, 8/12 steps',
    marker: 'circle',
    chartComparable: false,
    provenance: {
      kind: 'measured_published',
      sourceTitle: 'Official HunyuanVideo-1.5 repository',
      sourceUrl: 'https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5/tree/60783e704160023913bee78f0b47036d393d4dfa',
      sourceDate: '2025-12-05',
      method: 'Official step-distilled headline retained without inferring an unstated output duration.',
      confidence: 'medium',
      hardware: 'NVIDIA RTX 4090',
      cohort: 'HunyuanVideo-1.5 480p I2V step-distilled',
      profile: 'Eight or twelve inference steps; output duration not stated in the headline',
      comparability: 'Duration and runtime differ or are incomplete; not plotted on the Maestro axis.',
      publishedMetric: 'Within 75 seconds; end-to-end generation time reduced by 75% versus the original model.',
    },
  },
] as const satisfies readonly PublishedPerformanceReference[]

export const PERFORMANCE_HISTORY_PRIVACY_NOTE =
  'This bundled view contains release metadata and public claims only. It does not read prompts, projects, jobs, paths, seeds, devices, sessions, accounts, or runtime records.'

function validateProvenance(provenance: PerformanceProvenance): void {
  const common = [
    provenance.sourceTitle,
    provenance.sourceUrl,
    provenance.sourceDate,
    provenance.method,
    provenance.confidence,
    provenance.hardware,
    provenance.cohort,
    provenance.profile,
    provenance.comparability,
  ]
  if (common.some(value => !String(value).trim())) throw new Error('Performance provenance fields must not be empty.')
  if (!provenance.sourceUrl.startsWith('https://')) throw new Error('Performance provenance sources must use HTTPS.')
  const sourceDatePrecision: DatePrecision = /^\d{4}-\d{2}$/.test(provenance.sourceDate) ? 'month' : 'day'
  if (!validEvidenceDate(provenance.sourceDate, sourceDatePrecision)) {
    throw new Error('Performance provenance source dates must be valid.')
  }

  switch (provenance.kind) {
    case 'measured_local':
      if (!provenance.protocol.trim()) throw new Error('Local measurements require a protocol.')
      return
    case 'measured_published':
      if (!provenance.publishedMetric.trim()) throw new Error('Published measurements require the original metric.')
      return
    case 'adapted_published':
      if (!provenance.publishedMetric.trim() || !provenance.adaptation.trim()) {
        throw new Error('Adapted published evidence requires the original metric and adaptation.')
      }
      return
    case 'estimated_backfill':
      if (!provenance.basis.trim()) throw new Error('Estimated backfill requires a basis.')
      return
  }
}

function validEvidenceDate(date: string, precision: DatePrecision): boolean {
  if (precision === 'month') return /^\d{4}-(0[1-9]|1[0-2])$/.test(date)
  if (!/^\d{4}-(0[1-9]|1[0-2])-([0-2]\d|3[01])$/.test(date)) return false
  const [year, month, day] = date.split('-').map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day
}

export function validatePerformanceHistory(
  series: readonly PerformanceTrendSeries[] = PERFORMANCE_TREND_SERIES,
  references: readonly PublishedPerformanceReference[] = PUBLISHED_PERFORMANCE_REFERENCES,
): void {
  if (series.length < 2 || series.some(item => item.points.length < 2)) {
    throw new Error('Performance history requires at least two complete trend series.')
  }
  if (new Set(series.map(item => item.id)).size !== series.length) {
    throw new Error('Performance history series IDs must be unique.')
  }
  const ids = [...series.flatMap(item => item.points.map(point => point.id)), ...references.map(item => item.id)]
  if (new Set(ids).size !== ids.length) throw new Error('Performance history IDs must be unique.')
  const timeline = series[0]?.points.map(point => `${point.date}|${point.release}`) ?? []
  const timelineDates = series[0]?.points.map(point => point.date) ?? []
  if (new Set(timelineDates).size !== timelineDates.length || timelineDates.some((date, index) => index > 0 && date <= timelineDates[index - 1]!)) {
    throw new Error('Performance history trend dates must be unique and chronological.')
  }

  for (const trend of series) {
    if (trend.points.length !== timeline.length || trend.points.some((point, index) => `${point.date}|${point.release}` !== timeline[index])) {
      throw new Error('Performance history trend timelines must align.')
    }
    for (const point of trend.points) {
      validateProvenance(point.provenance)
      if (!validEvidenceDate(point.date, point.datePrecision)) throw new Error('Performance history dates must be valid.')
      if (point.seriesId !== trend.id || !Number.isFinite(point.value) || point.value <= 0 || point.value > PERFORMANCE_HISTORY_CHART_MAX) {
        throw new Error('Performance trend points must be positive and belong to their series.')
      }
      if (point.provenance.kind !== 'estimated_backfill') {
        throw new Error('Connected trend lines may contain estimated backfill only.')
      }
      if (
        point.uncertainty.unit !== 'inverse_step_work_x'
        ||
        !Number.isFinite(point.uncertainty.low)
        || !Number.isFinite(point.uncertainty.high)
        || point.uncertainty.low <= 0
        || point.uncertainty.low > point.value
        || point.uncertainty.high < point.value
        || point.uncertainty.high > PERFORMANCE_HISTORY_CHART_MAX
      ) {
        throw new Error('Performance uncertainty must contain the displayed estimate.')
      }
    }
  }
  for (const reference of references) {
    validateProvenance(reference.provenance)
    if (!validEvidenceDate(reference.date, reference.datePrecision)) throw new Error('Performance history dates must be valid.')
    if (reference.chartComparable !== false) throw new Error('Published references must remain disconnected context.')
    if (!['measured_published', 'adapted_published'].includes(reference.provenance.kind)) {
      throw new Error('Published references require published provenance.')
    }
  }

  const payload = JSON.stringify({ series, references, privacy: PERFORMANCE_HISTORY_PRIVACY_NOTE })
  if (/(?:[A-Z]:\\|\/(?:home|media|Users)\/)/.test(payload)) {
    throw new Error('Performance history must not contain local filesystem paths.')
  }
  if (/"(?:prompt|project|job|seed|user|session|account)(?:s|Id|_id)?"\s*:/i.test(payload)) {
    throw new Error('Performance history must not contain private runtime fields.')
  }
}

validatePerformanceHistory()
