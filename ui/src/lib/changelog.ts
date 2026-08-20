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
    title: 'Organized projects with private, controlled access',
    summary: 'Keep outputs and references together by project. Remote visitors can open only the projects you authorize, while computer settings stay with the person running Continuum.',
  },
  {
    id: 'h3-long-form',
    title: 'Longer H3 videos with clear plans and recovery',
    summary: 'Continuum brings the included Maestro 1.6.5 H3 features into Generate and Director, keeping timing consistent, choosing compatible shot lengths, and offering clear recovery options.',
  },
  {
    id: 'reference-studio',
    title: 'Reference',
    summary: 'Build reusable character, setting, item, and style cards, compare generated candidates, and carry approved references into Generate or Director.',
  },
  {
    id: 'local-intelligence',
    title: 'Local creative guidance when you want it',
    summary: 'Use the local AI tools chosen by the person running Continuum for individual requests, with Director guidance kept inside the active project.',
  },
  {
    id: 'queue-recovery',
    title: 'A queue you can follow and resume',
    summary: 'Project owners can see position, progress, saved checkpoints, problems, and resume options on each queue card when the workflow supports them.',
  },
] as const satisfies readonly ReleaseHighlight[]

const continuum030Highlights = [
  {
    id: 'continuum-030-reference-pack-v2',
    title: 'Reference Packs that keep your choices together',
    summary: 'Reference Pack v2 keeps sheet order, generation and editor model choices, and optional LoRAs with the work. LoRAs stay off unless you choose them.',
  },
  {
    id: 'continuum-030-host-model-controls',
    title: 'Clear model choices and compatible recipes',
    summary: 'The person running Continuum can review model licenses and confirm any required checks, while curated recipes appear only for models they support.',
  },
  {
    id: 'continuum-030-private-gallery',
    title: 'Blur/Reveal controls for large galleries',
    summary: 'Blur or reveal private previews in this browser without changing project access. Large galleries stay responsive by loading previews as needed.',
  },
  {
    id: 'continuum-030-durable-queue',
    title: 'Reliable queues with recovery and estimates',
    summary: 'Queued work keeps its plan, while each queue card shows preparation progress, available recovery options, and expected resource use.',
  },
] as const satisfies readonly ReleaseHighlight[]

const continuum020Highlights = [
  {
    id: 'continuum-020-project-access',
    title: 'Projects that work locally and remotely',
    summary: 'Let remote visitors unlock approved projects while keeping computer settings available only to the person running Continuum.',
  },
  {
    id: 'continuum-020-reference-studio',
    title: 'Reference workflow',
    summary: 'Create reusable character, setting, item, and style cards, compare candidates, and send approved references into Generate or Director.',
  },
  {
    id: 'continuum-020-local-intelligence',
    title: 'Local guidance for each request',
    summary: 'Choose local AI guidance when you need it, while Director keeps its planning inside the active project.',
  },
  {
    id: 'continuum-020-queue-recovery',
    title: 'Queue progress and recovery',
    summary: 'Project owners can follow position, progress, saved checkpoints, problems, and resume options where the workflow supports them.',
  },
] as const satisfies readonly ReleaseHighlight[]

export const CHANGELOG_MANIFEST = {
  productName: PRODUCT_NAME,
  currentVersion: PRODUCT_VERSION,
  maestroBaseVersion: MAESTRO_BASE_VERSION,
  whyContinuum: whyContinuumHighlights,
  lineageNote: 'Continuum, Maestro, and WanGP each have their own release history.',
  releases: [
    {
      lineage: 'continuum',
      version: '0.3.0',
      label: 'Current Continuum release',
      summary: 'Continuum 0.3 improves Reference, makes model choices clearer, adds private gallery controls, and makes queued work easier to follow and recover.',
      provenance: { kind: 'continuum-build' },
      highlights: continuum030Highlights,
    },
    {
      lineage: 'continuum',
      version: '0.2.0',
      label: 'More ways to create in projects',
      summary: 'Continuum 0.2 added Reference, optional local AI guidance, and clearer queue progress and recovery.',
      provenance: { kind: 'continuum-build' },
      highlights: continuum020Highlights,
    },
    {
      lineage: 'continuum',
      version: '0.1.0',
      label: 'First Continuum release',
      summary: 'Continuum began as a new creative workspace built on Maestro, with projects at the center and remote access controlled by the person running it.',
      provenance: { kind: 'continuum-build' },
      highlights: [
        {
          id: 'continuum-identity',
          title: 'Distinct product foundation',
          summary: 'Introduced the Continuum name and a project-centered studio while clearly crediting the Maestro base.',
        },
        {
          id: 'continuum-access-foundation',
          title: 'Controlled project access',
          summary: 'Let the person running Continuum choose which projects can be opened locally or remotely.',
        },
      ],
    },
    {
      lineage: 'maestro-base',
      version: '1.6.5',
      label: 'Included Maestro 1.6.5 base',
      summary: 'The included Maestro 1.6.5 base added ready-to-use H3 quality profiles and compatible shot planning shared by Studio and Director.',
      provenance: {
        kind: 'bundled-snapshot',
        note: 'This version is documented in the included Maestro changelog and does not have its own GitHub tag.',
      },
      highlights: [
        {
          id: 'base-h3-profiles',
          title: 'Ready-to-use H3 quality profiles',
          summary: 'Draft and Fast use ready-made four- and eight-step Turbo settings. Quality and High keep the fuller 20-step process.',
        },
        {
          id: 'base-h3-planner',
          title: 'Coherent H3 timelines',
          summary: 'Your timing choices are preserved, while untimed sections are fitted to shot lengths that H3 supports.',
        },
      ],
    },
    {
      lineage: 'maestro-base',
      version: '1.6.1',
      label: 'Earlier Maestro release',
      summary: 'This tagged release let people tune the Full-model H3 Turbo adapter. Maestro 1.6.5 later replaced those controls with ready-to-use profiles.',
      provenance: { kind: 'git-tag', tag: 'v1.6.1', date: '2026-08-06' },
      highlights: [],
    },
    {
      lineage: 'maestro-base',
      version: '1.5.0',
      label: 'Earlier Maestro release',
      summary: 'This tagged release added major SCAIL-2 Recast and Repaint features, LTX-2.3 Outpaint improvements, and broader Studio reliability updates.',
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
  if (!firstBaseRelease) {
    throw new Error('The bundled changelog must include a Maestro base archive.')
  }
  // Leftover 1.9.0 bumped VERSION; Continuum still archives the 1.6.5 snapshot.
  // Do not invent a 1.9.0 maestro-base release just to match the file.
  if (compareVersionsDescending(MAESTRO_BASE_VERSION, firstBaseRelease.version) > 0) {
    throw new Error('The current Maestro base version cannot be older than the leading archive entry.')
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
