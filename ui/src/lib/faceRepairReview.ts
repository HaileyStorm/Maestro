import type { ModelDef, OutputFile } from '../types'

export type FaceBox = [number, number, number, number]
export interface FaceRepairSource {
  workspace: string
  name: string
  revision: string
  width: number
  height: number
  frame_count: number
  fps: '24/1'
  audio_streams: Array<{ ordinal: number; label: string }>
}

export type FaceRepairAdmission = 'pending' | 'uncertain' | 'accepted' | null
const admissions = new Map<string, FaceRepairAdmission>()
const admissionEvent = 'maestro:face-repair-admission'
export function faceRepairAdmissionKey(account: string, source: Pick<OutputFile, 'workspace' | 'name' | 'revision'>): string {
  return 'maestro.face-repair-admission.' + encodeURIComponent(JSON.stringify([account, source.workspace, source.name, source.revision]))
}
export function readFaceRepairAdmission(key: string): FaceRepairAdmission {
  if (admissions.has(key)) return admissions.get(key)!
  try {
    const stored = sessionStorage.getItem(key)
    const value = stored === null ? null : stored === 'accepted' ? 'accepted' : 'uncertain'
    admissions.set(key, value)
    return value
  } catch { return 'uncertain' }
}
export function setFaceRepairAdmission(key: string, state: FaceRepairAdmission): void {
  admissions.set(key, state)
  try {
    if (state === null) sessionStorage.removeItem(key)
    else sessionStorage.setItem(key, state === 'pending' ? 'uncertain' : state)
  } catch { /* The pre-dispatch uncertain receipt remains the reload fence. */ }
  window.dispatchEvent(new CustomEvent(admissionEvent, { detail: key }))
}
export function beginFaceRepairAdmission(key: string): boolean {
  if (readFaceRepairAdmission(key) !== null) return false
  // Retain a conservative reload fence before a non-idempotent POST leaves the browser.
  try { sessionStorage.setItem(key, 'uncertain') } catch { return false }
  setFaceRepairAdmission(key, 'pending')
  return true
}
export function subscribeFaceRepairAdmission(key: string, listener: (state: FaceRepairAdmission) => void): () => void {
  const change = (event: Event) => { if ((event as CustomEvent).detail === key) listener(readFaceRepairAdmission(key)) }
  window.addEventListener(admissionEvent, change)
  return () => window.removeEventListener(admissionEvent, change)
}

export function resolveFaceRepairSelection(
  outputs: readonly OutputFile[], selected: readonly string[], workspace: string,
  canGenerate: boolean, models: readonly ModelDef[], enabled: ReadonlySet<string>, loaded: boolean,
): OutputFile | null {
  if (!canGenerate || !loaded || selected.length !== 1 || !enabled.has('minimax_h3')
    || !models.some(model => model.model_type === 'minimax_h3' && model.h3_face_refine === true
      && model.execution_allowed !== false && model.availability_status !== 'legal_blocked'
      && model.availability_status !== 'location_declaration_required')) return null
  return outputs.find(output => `${output.workspace}\0${output.name}` === selected[0]
    && output.workspace === workspace && output.type === 'video'
    && output.artifact_class === 'final' && Boolean(output.revision.trim())) ?? null
}

export function validateFaceRepairSource(value: unknown, selected: OutputFile): FaceRepairSource {
  const facts = value as FaceRepairSource | null
  if (!facts || facts.workspace !== selected.workspace || facts.name !== selected.name
    || facts.revision !== selected.revision || facts.fps !== '24/1'
    || !Number.isSafeInteger(facts.width) || !Number.isSafeInteger(facts.height)
    || facts.width < 1 || facts.height < 1 || !Number.isSafeInteger(facts.frame_count)
    || facts.frame_count < 124 || facts.frame_count > 345 || facts.frame_count % 17 !== 5
    || facts.width * facts.height * facts.frame_count * 3 > 512 * 1024 ** 2
    || !Array.isArray(facts.audio_streams) || facts.audio_streams.length > 16
    || facts.audio_streams.some((stream, index) => !stream || stream.ordinal !== index
      || typeof stream.label !== 'string' || stream.label.length > 255)) {
    throw new Error('This source is unavailable for face repair. Refresh Gallery and select an exact 24 fps Base-H3 clip (124–345 frames).')
  }
  return facts
}

export function validFaceBox(box: FaceBox | null, facts: FaceRepairSource): box is FaceBox {
  return box !== null && box.every(Number.isSafeInteger) && box[2] - box[0] === box[3] - box[1] && box[0] >= 0 && box[1] >= 0
    && box[2] > box[0] && box[3] > box[1] && box[2] <= facts.width && box[3] <= facts.height
}

/** Range review is explicit and cannot cross a declared shot boundary. */
export function reviewFaceRange(
  boxes: readonly (FaceBox | null)[], box: FaceBox | null, start: number, end: number,
  shots: readonly number[], facts: FaceRepairSource,
): (FaceBox | null)[] {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start
    || end >= facts.frame_count || boxes.length !== facts.frame_count
    || (box !== null && !validFaceBox(box, facts))
    || shots.some(shot => shot > start && shot <= end)) {
    throw new Error('Choose a valid range within one shot. Review each shot separately.')
  }
  return boxes.map((old, index) => index >= start && index <= end ? box?.slice() as FaceBox ?? null : old)
}

function faceRepairMemoryFits(facts: FaceRepairSource, canvas: number, audio: number | null): boolean {
  return facts.frame_count * canvas * canvas * 12 + (audio === null ? 0 : 48_000 * 8 * facts.frame_count / 24) <= 512 * 1024 ** 2
    && (facts.width * facts.height + canvas * canvas) * facts.frame_count * 3 <= 512 * 1024 ** 2
}

/** Keep the usual crop size when possible, including room for source audio guidance. */
export function defaultFaceRepairCanvas(facts: FaceRepairSource): number {
  const audio = facts.audio_streams.length > 0 ? 0 : null
  for (let canvas = 384; canvas >= 64; canvas -= 32) {
    if (faceRepairMemoryFits(facts, canvas, audio)) return canvas
  }
  throw new Error('This clip is too large for face repair on this installation.')
}

export function buildFaceRepairRequest(
  source: OutputFile, facts: FaceRepairSource, boxes: readonly (FaceBox | null)[], shots: readonly number[],
  prompt: string, strength: number, canvas: number, audio: number | null, steps: number, seed: number,
) {
  validateFaceRepairSource(facts, source)
  if (!prompt.trim() || prompt.length > 16384 || boxes.length !== facts.frame_count
    || !boxes.some(box => box !== null) || boxes.some(box => box !== null && !validFaceBox(box, facts))
    || shots[0] !== 0 || shots.some((shot, i) => !Number.isInteger(shot) || shot < 0
      || shot >= facts.frame_count || (i > 0 && shot <= shots[i - 1]!))
    || !Number.isInteger(steps) || steps < 2 || steps > 100
    || !Number.isSafeInteger(seed) || seed < 0 || !Number.isFinite(strength) || strength < steps / 4096 || strength > 1
    || !Number.isInteger(canvas) || canvas < 64 || canvas > 1536 || canvas % 32 !== 0
    || (audio !== null && !facts.audio_streams.some(stream => stream.ordinal === audio))
    || !faceRepairMemoryFits(facts, canvas, audio)) {
    throw new Error('Review at least one region and check the repair settings and crop size.')
  }
  return {
    workspace: source.workspace, name: source.name, revision: source.revision, prompt,
    observations: { shots: [...shots], boxes: boxes.map(box => box?.slice() ?? null), canvas: [canvas, canvas], padding: 1, smoothing: 1 },
    strength, frame_multipliers: boxes.map(box => box === null ? 0 : 1), audio_stream: audio,
    settings: { num_inference_steps: steps, seed, override_profile: 3 },
    private_output: source.private, explicit_output: source.explicit,
  }
}
