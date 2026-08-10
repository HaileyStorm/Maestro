import { MAESTRO_BASE_VERSION, PRODUCT_NAME, PRODUCT_VERSION } from './branding.ts'

export type ReleaseLineage = 'continuum' | 'maestro-base'
export type ReleaseProvenance =
  | { readonly kind: 'continuum-build' }
  | { readonly kind: 'bundled-snapshot'; readonly note: string }
  | { readonly kind: 'git-tag'; readonly tag: `v${string}`; readonly date: string }

export interface ReleaseHighlight {
  readonly id: string
  readonly title: string
  readonly summary: string
}

export interface PublicReleaseNote {
  readonly lineage: ReleaseLineage
  readonly version: string
  readonly label: string
  readonly summary: string
  readonly provenance: ReleaseProvenance
  readonly highlights: readonly ReleaseHighlight[]
}

const whyContinuumHighlights = [
  {
    id: 'project-access',
    title: 'Project-scoped creation, privacy, and remote access',
    summary: 'Projects separate outputs and references; remote visitors unlock only an authorized project, while machine controls stay with the host.',
  },
  {
    id: 'h3-long-form',
    title: 'Curated H3 long-form planning and recovery',
    summary: 'Continuum carries the bundled Maestro 1.6.5 H3 contract through Studio and Director: server-authored profiles, one authored timeline, deterministic native-shot plans, and supported recovery controls.',
  },
  {
    id: 'reference-studio',
    title: 'Reference Studio',
    summary: 'Build reusable character, setting, item, and style cards, compare generated candidates, and carry approved references into Studio or Director.',
  },
  {
    id: 'local-intelligence',
    title: 'Request-scoped local LLM and Director tools',
    summary: 'Host-configured local language-model tools apply explicit guidance per request and keep Director planning tied to the active project.',
  },
  {
    id: 'queue-recovery',
    title: 'Queue and recovery visibility',
    summary: 'Owner-only queue cards show position, segment progress, checkpoints, failures, and resumable states when the selected workflow supports them.',
  },
] as const satisfies readonly ReleaseHighlight[]

const continuum030Highlights = [
  {
    id: 'continuum-030-reference-pack-v2',
    title: 'Adaptive, sealed Reference Pack v2',
    summary: 'Reference Studio preserves authored sheet order in sealed v2 plans, carries exact generation and editor model choices, and keeps optional LoRAs explicitly scoped instead of enabling them automatically.',
  },
  {
    id: 'continuum-030-host-model-controls',
    title: 'Host-authorized models and exact-family recipes',
    summary: 'Host controls present exact model-license notices and user-confirmed manual review commitments, while curated recipes are pinned to exact supported model families.',
  },
  {
    id: 'continuum-030-private-gallery',
    title: 'Blur/Reveal controls for large galleries',
    summary: 'Browser-session Blur/Reveal controls protect private previews without changing project access, while virtualized rendering keeps large galleries responsive.',
  },
  {
    id: 'continuum-030-durable-queue',
    title: 'Durable plans, recovery, and resource visibility',
    summary: 'Queued work preserves its authored plan and surfaces bounded recovery actions, preparation status, and resource estimates on its queue card.',
  },
] as const satisfies readonly ReleaseHighlight[]

const continuum020Highlights = [
  {
    id: 'continuum-020-project-access',
    title: 'Project-aware local and remote workspace',
    summary: 'Extends project-scoped creation with explicit remote project unlocks while keeping machine controls host-only.',
  },
  {
    id: 'continuum-020-reference-studio',
    title: 'Reference Studio workflow',
    summary: 'Create reusable character, setting, item, and style cards, compare candidates, and send approved references into Studio or Director.',
  },
  {
    id: 'continuum-020-local-intelligence',
    title: 'Request-scoped LLM and Director controls',
    summary: 'Local language-model guidance is selected per request, while Director planning remains tied to the active project and visible host backend.',
  },
  {
    id: 'continuum-020-queue-recovery',
    title: 'Visible queue and recovery states',
    summary: 'Owner-only queue cards expose position, segment progress, checkpoints, failures, and resumable states where the workflow supports them.',
  },
] as const satisfies readonly ReleaseHighlight[]

export const CHANGELOG_MANIFEST = {
  productName: PRODUCT_NAME,
  currentVersion: PRODUCT_VERSION,
  maestroBaseVersion: MAESTRO_BASE_VERSION,
  whyContinuum: whyContinuumHighlights,
  lineageNote: 'Continuum product history, Maestro base history, and the upstream WanGP pipeline history are separate lineages.',
  releases: [
    {
      lineage: 'continuum',
      version: '0.3.0',
      label: 'Current Continuum build',
      summary: 'Continuum 0.3 advances reference production, host-authorized model workflows, private gallery control, and durable queued planning.',
      provenance: { kind: 'continuum-build' },
      highlights: continuum030Highlights,
    },
    {
      lineage: 'continuum',
      version: '0.2.0',
      label: 'Continuum project-authoring expansion',
      summary: 'Continuum 0.2 expands its project-aware authoring layer with Reference Studio, request-scoped local intelligence, and visible queue recovery.',
      provenance: { kind: 'continuum-build' },
      highlights: continuum020Highlights,
    },
    {
      lineage: 'continuum',
      version: '0.1.0',
      label: 'Continuum foundation',
      summary: 'Established Continuum as a distinct product layer over the Maestro base, centered on project-oriented creation and host-authorized remote use.',
      provenance: { kind: 'continuum-build' },
      highlights: [
        {
          id: 'continuum-identity',
          title: 'Distinct product foundation',
          summary: 'Introduced Continuum identity and a project-centered studio experience while preserving attribution to the Maestro base.',
        },
        {
          id: 'continuum-access-foundation',
          title: 'Host-authorized access',
          summary: 'Established local-host ownership and explicit project access as the foundation for local and remote use.',
        },
      ],
    },
    {
      lineage: 'maestro-base',
      version: '1.6.5',
      label: 'Bundled Maestro base snapshot',
      summary: 'The bundled 1.6.5 base established server-authored H3 performance profiles and a deterministic native-shot plan shared by Studio and Director.',
      provenance: {
        kind: 'bundled-snapshot',
        note: 'Documented in the bundled Maestro changelog; this snapshot is not represented as a tagged GitHub release.',
      },
      highlights: [
        {
          id: 'base-h3-profiles',
          title: 'Server-authored H3 profiles',
          summary: 'Draft and Fast use managed four- and eight-step Turbo profiles; Quality and High retain 20-step native generation.',
        },
        {
          id: 'base-h3-planner',
          title: 'Coherent H3 timelines',
          summary: 'Authored timing remains authoritative while untimed work is mapped deterministically onto legal native shots.',
        },
      ],
    },
    {
      lineage: 'maestro-base',
      version: '1.6.1',
      label: 'Historical Maestro base',
      summary: 'This tagged snapshot introduced the earlier adjustable Full-model H3 Turbo adapter surface, later superseded by the managed 1.6.5 profile contract.',
      provenance: { kind: 'git-tag', tag: 'v1.6.1', date: '2026-08-06' },
      highlights: [],
    },
    {
      lineage: 'maestro-base',
      version: '1.5.0',
      label: 'Historical Maestro base',
      summary: 'This tagged snapshot added major SCAIL-2 Recast and Repaint work, LTX-2.3 Outpaint improvements, and broader Studio reliability updates.',
      provenance: { kind: 'git-tag', tag: 'v1.5.0', date: '2026-08-02' },
      highlights: [],
    },
  ],
} as const satisfies {
  readonly productName: string
  readonly currentVersion: string
  readonly maestroBaseVersion: string
  readonly whyContinuum: readonly ReleaseHighlight[]
  readonly lineageNote: string
  readonly releases: readonly PublicReleaseNote[]
}

function compareVersionsDescending(left: string, right: string): number {
  const leftParts = left.split('.').map(Number)
  const rightParts = right.split('.').map(Number)
  for (let index = 0; index < 3; index += 1) {
    const difference = (rightParts[index] ?? 0) - (leftParts[index] ?? 0)
    if (difference !== 0) return difference
  }
  return 0
}

export function validateChangelogManifest(manifest: typeof CHANGELOG_MANIFEST): void {
  if (manifest.currentVersion !== PRODUCT_VERSION || manifest.maestroBaseVersion !== MAESTRO_BASE_VERSION) {
    throw new Error('The bundled changelog versions do not match the product identity.')
  }
  const keys = manifest.releases.map(release => `${release.lineage}:${release.version}`)
  if (new Set(keys).size !== keys.length) {
    throw new Error('The bundled changelog contains duplicate release versions.')
  }
  if (manifest.releases[0]?.lineage !== 'continuum' || manifest.releases[0]?.version !== PRODUCT_VERSION) {
    throw new Error('The current Continuum build must lead the bundled changelog.')
  }
  const firstBaseRelease = manifest.releases.find(release => release.lineage === 'maestro-base')
  if (firstBaseRelease?.version !== MAESTRO_BASE_VERSION) {
    throw new Error('The bundled Maestro base must lead the base archive.')
  }
  if (manifest.whyContinuum.length < 3 || manifest.whyContinuum.length > 5) {
    throw new Error('The all-time Continuum summary must contain three to five highlights.')
  }
  const currentRelease = manifest.releases[0]
  if (currentRelease.highlights.length < 3 || currentRelease.highlights.length > 5) {
    throw new Error('The current Continuum release must contain three to five highlights.')
  }
  if (Object.is(currentRelease.highlights, manifest.whyContinuum)) {
    throw new Error('The current release delta must be separate from the all-time Continuum summary.')
  }
  if (manifest.releases.some(release => !/^\d+\.\d+\.\d+$/.test(release.version))) {
    throw new Error('The bundled changelog contains an invalid version.')
  }
  for (const lineage of ['continuum', 'maestro-base'] as const) {
    const versions = manifest.releases.filter(release => release.lineage === lineage).map(release => release.version)
    if (versions.some((version, index) => index > 0 && compareVersionsDescending(versions[index - 1]!, version) > 0)) {
      throw new Error(`The ${lineage} changelog is not newest first.`)
    }
  }
}

validateChangelogManifest(CHANGELOG_MANIFEST)

export const CURRENT_RELEASE = CHANGELOG_MANIFEST.releases[0]
