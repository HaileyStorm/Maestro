// Locks leftover 1.9.0 VERSION probes to Continuum identity: current Maestro
// file is 1.9.0, Continuum is 0.3.0, and the maestro-base archive still leads
// with the included 1.6.5 snapshot. Do not invent a 1.9.0 changelog release.
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
  assert.equal(maestroBaseVersion, '1.9.0')
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
    'Organized projects with private, controlled access',
    'Longer H3 videos with clear plans and recovery',
    'Reference',
    'Local creative guidance when you want it',
    'A queue you can follow and resume',
  ])
  assert.deepEqual(CURRENT_RELEASE.highlights.map(item => item.title), [
    'Reference Packs that keep your choices together',
    'Clear model choices and compatible recipes',
    'Blur/Reveal controls for large galleries',
    'Reliable queues with recovery and estimates',
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
  assert.match(allTimeH3.summary, /included Maestro 1\.6\.5 H3 features/)
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

test('release and welcome copy stays clear for people using the app', () => {
  const userFacingManifestCopy = [
    CHANGELOG_MANIFEST.lineageNote,
    ...CHANGELOG_MANIFEST.whyContinuum.flatMap(item => [item.title, item.summary]),
    ...CHANGELOG_MANIFEST.releases.flatMap(release => [
      release.label,
      release.summary,
      ...(release.provenance.kind === 'bundled-snapshot' ? [release.provenance.note] : []),
      ...release.highlights.flatMap(item => [item.title, item.summary]),
    ]),
  ].join(' ')

  assert.doesNotMatch(userFacingManifestCopy, /UI-bundled|separate lineages|server-authored|deterministic|durable plans?|evidence ledger|structural proof|request-scoped|host-authorized|authored timeline|native-shot|bundled snapshot/i)
  assert.match(dialogSource, /These notes are included with the app\. Opening them does not access your projects, running work, or device information\./)
  assert.doesNotMatch(dialogSource, /Public, UI-bundled notes only|release delta|untagged bundled snapshot/)
  assert.doesNotMatch(welcomeSource, /adaptive segment plan|language-model service|shared cache|Cloudflare sessions/)
  assert.match(userFacingManifestCopy, /Remote visitors can open only the projects you authorize/)
  assert.match(userFacingManifestCopy, /resume options[^.]*when the workflow supports them/)
  assert.match(userFacingManifestCopy, /without changing project access/)
  assert.match(welcomeSource, /Project access controls who can open the project; blur controls what appears in this browser/)
  assert.match(welcomeSource, /If needed,[^<]*downloads and prepares model files in a shared storage area/)
  assert.match(welcomeSource, /Approved local and remote users can reuse them; project access and private-preview settings still apply/)
})

test('Continuum, Maestro base, and WanGP provenance stay distinct', () => {
  const continuum = CHANGELOG_MANIFEST.releases.filter(release => release.lineage === 'continuum')
  const maestro = CHANGELOG_MANIFEST.releases.filter(release => release.lineage === 'maestro-base')
  assert.deepEqual(continuum.map(release => release.version), [continuumVersion, '0.2.0', '0.1.0'])
  assert.deepEqual(continuum[1].highlights.map(item => item.title), [
    'Projects that work locally and remotely',
    'Reference workflow',
    'Local guidance for each request',
    'Queue progress and recovery',
  ])
  assert.deepEqual(maestro.map(release => release.version), ['1.6.5', '1.6.1', '1.5.0'])
  assert.notEqual(maestro[0].version, maestroBaseVersion)
  assert.equal(maestro[0].provenance.kind, 'bundled-snapshot')
  assert.deepEqual(maestro.slice(1).map(release => release.provenance.kind), ['git-tag', 'git-tag'])
  assert.deepEqual(maestro.slice(1).map(release => release.provenance.tag), ['v1.6.1', 'v1.5.0'])
  assert.equal(CHANGELOG_MANIFEST.lineageNote, 'Continuum, Maestro, and WanGP each have their own release history.')
  assert.match(dialogSource, /not listed as Continuum releases/)
})

test("what's-new dialog has persistent triggers and modal focus isolation", () => {
  assert.match(sidebarSource, /\{!isMobile && <WhatsNewButton \/>\}/)
  assert.match(dialogSource, /aria-haspopup="dialog"/)
  assert.match(dialogSource, /role="dialog"/)
  assert.match(dialogSource, /aria-modal="true"/)
  assert.match(dialogSource, /aria-labelledby=\{titleId\}/)
  assert.match(dialogSource, /installModalFocus\(\{/)
})

test("mobile what's-new trigger remains reachable in the existing workspace drawer", () => {
  const mobileHeaderStart = appSource.indexOf('{isMobile && (')
  const sidebarMount = appSource.indexOf('<Sidebar />', mobileHeaderStart)
  assert.ok(mobileHeaderStart >= 0)
  assert.ok(sidebarMount > mobileHeaderStart)

  const mobileHeader = appSource.slice(mobileHeaderStart, sidebarMount)
  const mobileSidebarStart = sidebarSource.indexOf('if (isMobile) {')
  const desktopSidebarStart = sidebarSource.indexOf('// Desktop: static sidebar', mobileSidebarStart)
  assert.ok(mobileSidebarStart >= 0)
  assert.ok(desktopSidebarStart > mobileSidebarStart)
  const mobileSidebar = sidebarSource.slice(mobileSidebarStart, desktopSidebarStart)
  const trigger = mobileSidebar.indexOf('<WhatsNewButton compact />')
  const machineSettingsGate = mobileSidebar.indexOf('{machineControls && (')

  assert.match(mobileHeader, /<\/button> : <WhatsNewButton compact \/>}/)
  assert.ok(trigger >= 0, 'trigger remains in the mobile drawer')
  assert.ok(machineSettingsGate > trigger, 'trigger remains outside the machine-controls gate')
  assert.equal(mobileSidebar.match(/<WhatsNewButton compact \/>/g)?.length, 1)
})

test('current notes and collapsed archive remain reachable on mobile', () => {
  assert.match(dialogSource, /Continuum v\{CURRENT_RELEASE\.version\}/)
  assert.match(dialogSource, /<details className=/)
  assert.match(dialogSource, /Earlier releases/)
  assert.match(dialogSource, /max-h-\[calc\(100dvh-1\.5rem\)\]/)
  assert.match(dialogSource, /overflow-y-auto/)
  assert.match(dialogSource, /env\(safe-area-inset-bottom\)/)
  assert.match(dialogSource, /createPortal\(/)
  assert.match(dialogSource, /aria-label=\{`Continuum \$\{CURRENT_RELEASE\.version\} release highlights`\}/)
  assert.match(welcomeSource, /aria-label=\{`Continuum \$\{CURRENT_RELEASE\.version\} release highlights`\}/)
  assert.doesNotMatch(dialogSource, /Continuum 0\.2 release highlights/)
  assert.doesNotMatch(welcomeSource, /Continuum 0\.2 release highlights/)
})

test("what's-new visible controls keep mobile touch targets and compact desktop sizing", () => {
  const closeButton = dialogSource.match(/<button\s+ref=\{closeRef\}[^]*?<\/button>/)?.[0]
  assert.ok(closeButton, "the visible what's-new close button must remain present")
  assert.match(closeButton, /className="[^"]*\bh-11\b[^"]*\bw-11\b[^"]*\bp-0\b[^"]*\bmd:h-auto\b[^"]*\bmd:w-auto\b[^"]*\bmd:p-1\.5\b[^"]*"/)

  const releaseHistorySummary = dialogSource.match(/<summary[^]*?<\/summary>/)?.[0]
  assert.ok(releaseHistorySummary, 'the release-history disclosure must remain present')
  assert.match(releaseHistorySummary, /className="[^"]*\bmin-h-11\b[^"]*\bmd:min-h-0\b[^"]*"/)
  assert.match(releaseHistorySummary, /Earlier releases/)

  const footer = dialogSource.match(/<footer[^]*?<\/footer>/)?.[0]
  const doneButton = footer?.match(/<button[^]*?>\s*Done\s*<\/button>/)?.[0]
  assert.ok(doneButton, "the visible what's-new Done button must remain present")
  assert.match(doneButton, /className="[^"]*\bmin-h-11\b[^"]*\bmd:min-h-0\b[^"]*"/)
  assert.match(doneButton, /className="[^"]*\bbg-bg-active\b[^"]*\btext-text-primary\b[^"]*\bhover:bg-bg-hover\b[^"]*"/)
  assert.doesNotMatch(doneButton, /\bbg-accent-blue\b|\btext-white\b/)
})

test('welcome onboarding uses the shared priority stack and bounded mobile geometry', () => {
  assert.match(appSource, /\{!remoteProjectRequired && <WelcomeModal \/>\}/)
  assert.match(welcomeSource, /maestro_welcome_seen_v1/)
  assert.match(welcomeSource, /createPortal\(/)
  assert.match(welcomeSource, /document\.body/)
  assert.match(welcomeSource, /installModalFocus\(\{/)
  assert.match(welcomeSource, /priority: 120/)
  assert.match(welcomeSource, /restoreFocus,/)
  assert.match(welcomeSource, /closeModalIfTop\(document, dialogRef\.current, dismiss\)/)
  assert.doesNotMatch(welcomeSource, /document\.addEventListener\('keydown'/)
  assert.match(welcomeSource, /h-\[100vh\][^"\n]*supports-\[height:100dvh\]:h-\[100dvh\]/)
  assert.match(welcomeSource, /max-h-full/)
  assert.match(welcomeSource, /max-h-\[55%\][^"\n]*overflow-y-auto/)
  for (const edge of ['top', 'right', 'bottom', 'left']) {
    assert.match(welcomeSource, new RegExp(`safe-area-inset-${edge}`))
  }
  assert.match(welcomeSource, /min-h-11 min-w-11/)
  assert.match(welcomeSource, /flex-1 min-h-0 overflow-y-auto/)
})
