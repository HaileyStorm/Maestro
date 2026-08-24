export type H3ShotDeckJson =
  | null
  | boolean
  | number
  | string
  | H3ShotDeckJson[]
  | { [key: string]: H3ShotDeckJson }

export interface H3ShotDeckShot {
  shot_id: string
  index: number
  start_sec: number
  end_sec: number
  duration_sec: number
  scene: string
  subjects: H3ShotDeckJson[]
  spatial: H3ShotDeckJson
  environment: H3ShotDeckJson
  lighting: H3ShotDeckJson
  action: H3ShotDeckJson[]
  camera: { [key: string]: H3ShotDeckJson }
  audio: { [key: string]: H3ShotDeckJson }
  handoff_in: H3ShotDeckJson
  handoff_out: H3ShotDeckJson
  timed_cues: H3ShotDeckJson[]
  reference_anchor_ids?: H3ShotDeckJson
  asset_lineage?: H3ShotDeckJson
  music_metadata?: H3ShotDeckJson
}

export interface H3ShotDeck {
  type: 'minimax_h3_shot_table'
  version: 1
  surface: 'api_persisted_plan'
  authority: 'advisory'
  provenance: {
    source: string
    revision: string
    adaptation: string
  }
  fallback_policy: {
    latest_approved_asset_fallback: 'explicit_only'
    reuse_exact_reference_anchors_first: true
    preserve_authored_dialogue_and_audio: true
    retake_scope: 'shot'
  }
  shots: H3ShotDeckShot[]
  qc_checklist: Array<{ check: string; status: 'pending' }>
}

export interface ScopedH3ShotDeck {
  workspace: string
  deck: H3ShotDeck
}

const REQUIRED_SHOT_KEYS = new Set([
  'shot_id', 'index', 'start_sec', 'end_sec', 'duration_sec', 'scene',
  'subjects', 'spatial', 'environment', 'lighting', 'action', 'camera',
  'audio', 'handoff_in', 'handoff_out', 'timed_cues',
])
const OPTIONAL_SHOT_KEYS = new Set([
  'reference_anchor_ids', 'asset_lineage', 'music_metadata',
])
const MAX_SHOTS = 100
const MAX_QC_CHECKS = 16
const MAX_STRING = 8_192
const MAX_ARRAY = 128
const MAX_OBJECT_KEYS = 64
const MAX_DEPTH = 6

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function boundedJson(value: unknown, depth = 0): value is H3ShotDeckJson {
  if (value === null || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value === 'string') return value.length <= MAX_STRING
  if (depth >= MAX_DEPTH) return false
  if (Array.isArray(value)) {
    return value.length <= MAX_ARRAY && value.every(item => boundedJson(item, depth + 1))
  }
  if (!isObject(value)) return false
  const entries = Object.entries(value)
  return entries.length <= MAX_OBJECT_KEYS
    && entries.every(([key, item]) => key.length > 0 && key.length <= 128 && boundedJson(item, depth + 1))
}

function validShot(value: unknown): value is H3ShotDeckShot {
  if (!isObject(value)) return false
  const keys = Object.keys(value)
  if (keys.some(key => !REQUIRED_SHOT_KEYS.has(key) && !OPTIONAL_SHOT_KEYS.has(key))) return false
  if ([...REQUIRED_SHOT_KEYS].some(key => !(key in value))) return false
  if (typeof value.shot_id !== 'string' || !value.shot_id || value.shot_id.length > 256) return false
  if (!Number.isInteger(value.index) || (value.index as number) < 0) return false
  if (!isFiniteNumber(value.start_sec) || !isFiniteNumber(value.end_sec) || !isFiniteNumber(value.duration_sec)) return false
  if (value.start_sec < 0 || value.end_sec < value.start_sec || value.duration_sec < 0) return false
  if (Math.abs((value.end_sec - value.start_sec) - value.duration_sec) > 0.001) return false
  if (typeof value.scene !== 'string' || value.scene.length > MAX_STRING) return false
  if (!Array.isArray(value.subjects) || !Array.isArray(value.action) || !Array.isArray(value.timed_cues)) return false
  if (!isObject(value.camera) || !isObject(value.audio)) return false
  return boundedJson(value)
}

export function parseH3ShotDeck(value: unknown): H3ShotDeck | null {
  if (!isObject(value)) return null
  if (
    value.type !== 'minimax_h3_shot_table'
    || value.version !== 1
    || value.surface !== 'api_persisted_plan'
    || value.authority !== 'advisory'
  ) return null
  if (!isObject(value.provenance) || !isObject(value.fallback_policy)) return null
  if (
    typeof value.provenance.source !== 'string'
    || typeof value.provenance.revision !== 'string'
    || typeof value.provenance.adaptation !== 'string'
    || value.provenance.source.length > 512
    || value.provenance.revision.length > 256
    || value.provenance.adaptation.length > 256
  ) return null
  if (
    value.fallback_policy.latest_approved_asset_fallback !== 'explicit_only'
    || value.fallback_policy.reuse_exact_reference_anchors_first !== true
    || value.fallback_policy.preserve_authored_dialogue_and_audio !== true
    || value.fallback_policy.retake_scope !== 'shot'
  ) return null
  if (!Array.isArray(value.shots) || value.shots.length === 0 || value.shots.length > MAX_SHOTS) return null
  if (!value.shots.every(validShot)) return null
  if (!Array.isArray(value.qc_checklist) || value.qc_checklist.length === 0 || value.qc_checklist.length > MAX_QC_CHECKS) return null
  if (!value.qc_checklist.every(item => (
    isObject(item)
    && typeof item.check === 'string'
    && item.check.length > 0
    && item.check.length <= 256
    && item.status === 'pending'
    && Object.keys(item).every(key => key === 'check' || key === 'status')
  ))) return null
  if (!boundedJson(value)) return null
  return structuredClone(value) as unknown as H3ShotDeck
}

export function h3ShotDeckFromProductionPlan(
  productionPlan: unknown,
  workspace: string,
): ScopedH3ShotDeck | null {
  if (!workspace || !isObject(productionPlan)) return null
  const deck = parseH3ShotDeck(productionPlan.workflow_template)
  return deck ? { workspace, deck } : null
}
