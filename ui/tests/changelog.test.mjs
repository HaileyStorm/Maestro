import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const rootUrl = new URL('../../', import.meta.url)
const continuumVersion = (await readFile(new URL('CONTINUUM_VERSION', rootUrl), 'utf8')).trim()
const maestroBaseVersion = (await readFile(new URL('VERSION', rootUrl), 'utf8')).trim()

globalThis.__CONTINUUM_VERSION__ = continuumVersion
globalThis.__MAESTRO_BASE_VERSION__ = maestroBaseVersion

const { CHANGELOG_MANIFEST, CURRENT_RELEASE, validateChangelogManifest } = await import('../src/lib/changelog.ts')
const dialogSource = await readFile(new URL('../src/components/WhatsNewDialog.tsx', import.meta.url), 'utf8')
const appSource = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
const welcomeSource = await readFile(new URL('../src/components/WelcomeModal.tsx', import.meta.url), 'utf8')
const sidebarSource = await readFile(new URL('../src/components/Sidebar/Sidebar.tsx', import.meta.url), 'utf8')

test('public release manifest is typed, current, unique, and newest first', () => {
  assert.doesNotThrow(() => validateChangelogManifest(CHANGELOG_MANIFEST))
  assert.equal(CHANGELOG_MANIFEST.currentVersion, continuumVersion)
  assert.equal(CHANGELOG_MANIFEST.maestroBaseVersion, maestroBaseVersion)
  assert.equal(continuumVersion, '0.3.0')
  assert.equal(maestroBaseVersion, '1.6.5')
  assert.equal(CURRENT_RELEASE.version, continuumVersion)
  assert.equal(CURRENT_RELEASE.lineage, 'continuum')

  const keys = CHANGELOG_MANIFEST.releases.map(release => `${release.lineage}:${release.version}`)
  assert.equal(new Set(keys).size, keys.length)
  for (const lineage of ['continuum', 'maestro-base']) {
    const versions = CHANGELOG_MANIFEST.releases
      .filter(release => release.lineage === lineage)
      .map(release => release.version)
    assert.deepEqual(versions, [...versions].sort((left, right) => right.localeCompare(left, undefined, { numeric: true })))
  }
})

test('all-time product themes and the current release delta are exact and separate', () => {
  assert.deepEqual(CHANGELOG_MANIFEST.whyContinuum.map(item => item.title), [
    'Project-scoped creation, privacy, and remote access',
    'Curated H3 long-form planning and recovery',
    'Reference Studio',
    'Request-scoped local LLM and Director tools',
    'Queue and recovery visibility',
  ])
  assert.deepEqual(CURRENT_RELEASE.highlights.map(item => item.title), [
    'Adaptive, sealed Reference Pack v2',
    'Host-authorized models and exact-family recipes',
    'Blur/Reveal controls for large galleries',
    'Durable plans, recovery, and resource visibility',
  ])
  assert.notStrictEqual(CURRENT_RELEASE.highlights, CHANGELOG_MANIFEST.whyContinuum)
  const aliasedManifest = {
    ...CHANGELOG_MANIFEST,
    releases: [
      { ...CURRENT_RELEASE, highlights: CHANGELOG_MANIFEST.whyContinuum },
      ...CHANGELOG_MANIFEST.releases.slice(1),
    ],
  }
  assert.throws(() => validateChangelogManifest(aliasedManifest), /must be separate/)
  assert.doesNotMatch(JSON.stringify(CURRENT_RELEASE), /\bH3\b|Maestro 1\.6\.5|server-authored/i)
  assert.doesNotMatch(JSON.stringify(CURRENT_RELEASE), /restart-resumable|fixed-seed|quality acceptance|default promotion/i)
  const allTimeH3 = CHANGELOG_MANIFEST.whyContinuum.find(item => item.id === 'h3-long-form')
  assert.match(allTimeH3.summary, /bundled Maestro 1\.6\.5 H3 contract/)
  assert.match(welcomeSource, /CURRENT_RELEASE\.highlights\.map/)
  assert.match(welcomeSource, /CHANGELOG_MANIFEST\.whyContinuum\.map/)
  assert.match(dialogSource, /CURRENT_RELEASE\.highlights\.map/)
  assert.match(dialogSource, /CHANGELOG_MANIFEST\.whyContinuum\.map/)
})

test('manifest is public-only and contains no runtime artifacts or invented links', () => {
  const serialized = JSON.stringify(CHANGELOG_MANIFEST)
  assert.doesNotMatch(serialized, /\b(?:jobs?|prompts?|logs?|secrets?|localhost)\b/i)
  assert.doesNotMatch(serialized, /(?:[A-Z]:\\|\/(?:home|media|Users)\/)/)
  assert.doesNotMatch(serialized, /https?:\/\//i)
  assert.doesNotMatch(serialized, /"(?:href|url|releaseLink)"/i)
})

test('Continuum, Maestro base, and WanGP provenance stay distinct', () => {
  const continuum = CHANGELOG_MANIFEST.releases.filter(release => release.lineage === 'continuum')
  const maestro = CHANGELOG_MANIFEST.releases.filter(release => release.lineage === 'maestro-base')
  assert.deepEqual(continuum.map(release => release.version), [continuumVersion, '0.2.0', '0.1.0'])
  assert.deepEqual(continuum[1].highlights.map(item => item.title), [
    'Project-aware local and remote workspace',
    'Reference Studio workflow',
    'Request-scoped LLM and Director controls',
    'Visible queue and recovery states',
  ])
  assert.deepEqual(maestro.map(release => release.version), [maestroBaseVersion, '1.6.1', '1.5.0'])
  assert.equal(maestro[0].provenance.kind, 'bundled-snapshot')
  assert.deepEqual(maestro.slice(1).map(release => release.provenance.kind), ['git-tag', 'git-tag'])
  assert.deepEqual(maestro.slice(1).map(release => release.provenance.tag), ['v1.6.1', 'v1.5.0'])
  assert.match(CHANGELOG_MANIFEST.lineageNote, /WanGP pipeline history are separate lineages/)
  assert.match(dialogSource, /not presented as Continuum releases/)
})

test("what's-new dialog has persistent triggers and modal focus isolation", () => {
  assert.match(sidebarSource, /\{!isMobile && <WhatsNewButton \/>\}/)
  assert.match(dialogSource, /aria-haspopup="dialog"/)
  assert.match(dialogSource, /role="dialog"/)
  assert.match(dialogSource, /aria-modal="true"/)
  assert.match(dialogSource, /aria-labelledby=\{titleId\}/)
  assert.match(dialogSource, /installModalFocus\(\{/)
})

test("mobile what's-new trigger remains reachable while the sidebar drawer is closed", () => {
  const mobileHeaderStart = appSource.indexOf('{isMobile && (')
  const sidebarMount = appSource.indexOf('<Sidebar />', mobileHeaderStart)
  assert.ok(mobileHeaderStart >= 0)
  assert.ok(sidebarMount > mobileHeaderStart)

  const mobileHeader = appSource.slice(mobileHeaderStart, sidebarMount)
  const remoteMenuGateEnd = mobileHeader.indexOf('</button> : <span className="w-9" aria-hidden="true" />}')
  const trigger = mobileHeader.indexOf('<WhatsNewButton compact />')
  const machineSettingsGate = mobileHeader.indexOf('{machineControls ? (')

  assert.ok(remoteMenuGateEnd >= 0)
  assert.ok(trigger > remoteMenuGateEnd, 'trigger must sit outside the remote project menu gate')
  assert.ok(machineSettingsGate > trigger, 'trigger must sit outside the machine-controls gate')
  assert.equal(mobileHeader.match(/<WhatsNewButton compact \/>/g)?.length, 1)
})

test('current notes and collapsed archive remain reachable on mobile', () => {
  assert.match(dialogSource, /Continuum v\{CURRENT_RELEASE\.version\}/)
  assert.match(dialogSource, /<details className=/)
  assert.match(dialogSource, /All release history/)
  assert.match(dialogSource, /max-h-\[calc\(100dvh-1\.5rem\)\]/)
  assert.match(dialogSource, /overflow-y-auto/)
  assert.match(dialogSource, /env\(safe-area-inset-bottom\)/)
  assert.match(dialogSource, /createPortal\(/)
  assert.match(dialogSource, /aria-label=\{`Continuum \$\{CURRENT_RELEASE\.version\} release highlights`\}/)
  assert.match(welcomeSource, /aria-label=\{`Continuum \$\{CURRENT_RELEASE\.version\} release highlights`\}/)
  assert.doesNotMatch(dialogSource, /Continuum 0\.2 release highlights/)
  assert.doesNotMatch(welcomeSource, /Continuum 0\.2 release highlights/)
})
