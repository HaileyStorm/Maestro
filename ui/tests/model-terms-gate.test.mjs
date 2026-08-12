import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = async relative => readFile(new URL(relative, import.meta.url), 'utf8')

test('model term notices are server-backed, exact, and never browser authority', async () => {
  const notices = await source('../src/lib/hostTerms.ts')
  assert.match(notices, /black-forest-labs\/FLUX\.1-dev\/blob\/3de623f\/LICENSE\.md/)
  assert.match(notices, /black-forest-labs\/FLUX\.2-dev\/blob\/0cb56aa\/LICENSE\.md/)
  assert.match(notices, /krea\/Krea-2-Turbo\/blob\/98e0fe1\/README\.md/)
  assert.match(notices, /ponpoke\/flux2-klein-4b-uncensored-text-encoder\/blob\/633217e588e4c0bc76619052e05d3ce0e057cd83\/README\.md/)
  assert.match(notices, /ponpoke\/flux2-klein-9b-uncensored-text-encoder\/blob\/fba36e796aac081246708dd30392a401ba44922e\/README\.md/)
  assert.match(notices, /civitai\.com\/models\/2382648\?modelVersionId=2973304/)
  assert.match(notices, /civitai\.com\/models\/2731187\?modelVersionId=3209007/)
  assert.match(notices, /civitai\.com\/models\/2764429\?modelVersionId=3211049/)
  assert.match(notices, /credit is required/)
  assert.match(notices, /derivatives are allowed/)
  assert.match(notices, /commercial scope is RentCivit only/)
  assert.match(notices, /underlying FLUX base remains non-commercial/)
  assert.match(notices, /Optional local fidelity QA/)
  assert.match(notices, /it is not moderation/)
  assert.match(notices, /does not decide permissibility/)
  assert.match(notices, /broad-capability research, evaluation, and fine-tune development/)
  assert.match(notices, /not automatically circumvention/)
  assert.match(notices, /explicitly designed to defeat safety filters remain excluded/)
  assert.match(notices, /Acceptable Use Policy/)
  assert.match(notices, /required human review/)
  assert.match(notices, /derivatives are forbidden/)
  assert.match(notices, /does not permit Moody derivatives or derivative tooling/)
  assert.doesNotMatch(notices, /localStorage/)
})

test('PornMaster visibility is a one-time v9 addition with no auto authority', async () => {
  const store = await source('../src/stores/useStore.ts')
  const recipe = 'flux2_klein_9b_pornmaster_v4_turbo_fp8_ponpoke'
  assert.match(store, /const DEFAULTS_VERSION = 9/)
  assert.match(store, new RegExp(`9: \\[\\s*'${recipe}'\\s*\\]`))
  assert.match(store, /if \(storedVer < DEFAULTS_VERSION\)/)
  const defaults = store.slice(
    store.indexOf('const modeDefaultModel:'),
    store.indexOf('export function getFamilyMode'),
  )
  assert.doesNotMatch(defaults, new RegExp(recipe))
})

test('failed durable visibility reads defer migration and preserve server hides', async () => {
  const store = await source('../src/stores/useStore.ts')
  const loadModels = store.slice(
    store.indexOf('loadModels: async () =>'),
    store.indexOf('// Hydrate persisted per-mode settings'),
  )
  assert.match(loadModels, /fetchModelVisibility\(\)\.catch/)
  assert.match(loadModels, /return null/)
  assert.match(
    loadModels,
    /if \(!shouldHydrateVisibility \|\| visibility !== null\) \{\s*try \{/,
  )
  const guard = loadModels.indexOf(
    'if (!shouldHydrateVisibility || visibility !== null)',
  )
  assert.ok(guard >= 0)
  assert.ok(guard < loadModels.indexOf('if (storedVer < DEFAULTS_VERSION)'))
  assert.ok(guard < loadModels.indexOf('_saveEnabledModels(get().enabledModels)'))
})

test('generic model selector requires explicit host acceptance before generation', async () => {
  const store = await source('../src/stores/useStore.ts')
  const selector = await source('../src/components/Sidebar/ModelSelector.tsx')
  const button = await source('../src/components/Sidebar/GenerateButton.tsx')
  assert.match(store, /required_host_terms:\s*m\.required_host_terms \?\? \[\]/)
  assert.match(selector, /currentModel\?\.required_host_terms/)
  assert.match(selector, /acceptHostTerm\(requirement\.term\)/)
  assert.match(selector, /Accept for this Maestro installation/)
  assert.match(selector, /hostTerms\?\.\[requirement\.term\]\?\.accepted !== true/)
  assert.match(button, /required_host_terms/)
  assert.match(button, /needsModelTerms/)
  assert.match(button, /Review terms/)
  assert.match(selector, /manual_checkpoint_verification_required/)
  assert.match(selector, /Check model file/)
  assert.match(selector, /Maestro will check its size and SHA-256 fingerprint on the computer where it runs/)
  assert.match(button, /needsManualCheckpointVerification/)
  assert.match(button, /Model file needed/)
  assert.match(button, /use the model selector to check it on the computer running Maestro/)
})

test('loading server term status never accepts a legacy browser flag', async () => {
  const store = await source('../src/stores/useStore.ts')
  const loadHostTerms = store.slice(
    store.indexOf('loadHostTerms: async () =>'),
    store.indexOf('acceptHostTerm: async (term) =>'),
  )
  assert.match(loadHostTerms, /api\.fetchHostTerms\(workspace\)/)
  assert.doesNotMatch(loadHostTerms, /api\.acceptHostTerm/)
  assert.doesNotMatch(loadHostTerms, /localStorage\.getItem/)
})

test('Reference Studio gates the selected generation and editor recipe pair', async () => {
  const component = await source('../src/components/Sidebar/ProjectReferenceLibrary.tsx')
  assert.match(component, /selectedRecipeTermRequirements/)
  assert.match(component, /referenceModelType/)
  assert.match(component, /sheetMode !== 'draft' \? \[editorModelType\]/)
  assert.match(component, /pendingRecipeTermRequirements\.length > 0/)
  assert.match(component, /acceptHostTerm\(requirement\.term\)/)
  assert.match(component, /Accept for this host/)
  assert.match(component, /pendingManualModels/)
  assert.match(component, /getProjectReferenceModelAvailabilityCopy/)
  assert.match(component, /Verify model file/)
  assert.match(component, /Maestro will not download it/)
})

test('download submission carries project authority and renders fixed errors only', async () => {
  const client = await source('../src/api/client.ts')
  const download = client.slice(
    client.indexOf('export async function downloadModel'),
    client.indexOf('export async function fetchModelDownloads'),
  )
  assert.match(download, /new URLSearchParams\(\{ workspace \}\)/)
  assert.match(download, /res\.status === 409/)
  assert.doesNotMatch(download, /error\.detail/)
  assert.doesNotMatch(download, /await res\.json/)

  const hostTerms = client.slice(
    client.indexOf('export async function fetchHostTerms'),
    client.indexOf('// --- LLM Service ---'),
  )
  assert.match(hostTerms, /new URLSearchParams\(\{ workspace \}\)/)
  assert.doesNotMatch(hostTerms, /err\.detail/)
  assert.doesNotMatch(hostTerms, /await res\.json\(\)\.catch/)
})

test('manual-only catalog metadata suppresses generic download affordance', async () => {
  const store = await source('../src/stores/useStore.ts')
  const settings = await source(
    '../src/components/SettingsDrawer/SystemSettingsPanel.tsx',
  )
  assert.match(store, /downloadable:\s*m\.downloadable \?\? true/)
  assert.match(
    store,
    /manual_installation_ready:\s*m\.manual_installation_ready \?\? false/,
  )
  assert.match(store, /availability_status:\s*m\.availability_status/)
  assert.match(
    store,
    /manual_checkpoint_verification_required:\s*m\.manual_checkpoint_verification_required \?\? false/,
  )
  assert.match(
    store,
    /manual_checkpoint_verified:\s*m\.manual_checkpoint_verified \?\? false/,
  )
  assert.match(store, /supported_operations:\s*m\.supported_operations \?\? \[\]/)
  assert.match(store, /automatic_routing:\s*m\.automatic_routing \?\? false/)
  assert.match(store, /default_for_operations:\s*m\.default_for_operations \?\? \[\]/)
  assert.match(store, /revenue_eligible:\s*m\.revenue_eligible/)
  assert.match(store, /fine_tuning_eligible:\s*m\.fine_tuning_eligible/)
  assert.match(store, /derivative_tooling:\s*m\.derivative_tooling/)

  const row = settings.slice(
    settings.indexOf('{m.is_downloaded ? ('),
    settings.indexOf("m.is_downloaded\n                              ? 'text-text-primary'"),
  )
  assert.match(row, /m\.downloadable === false/)
  assert.match(row, /Manual model setup required/)
  assert.ok(
    row.indexOf('m.downloadable === false')
      < row.indexOf('handleDownload(m.model_type)'),
  )
})

test('Moody Krea stays outside global defaults while a scoped Reference preference remains separate', async () => {
  const store = await source('../src/stores/useStore.ts')
  const notices = await source('../src/lib/hostTerms.ts')
  const recipes = [
    'krea2_moody_mix_v7_fp8',
    'krea2_moody_cutie_v4_fp8',
  ]
  const defaultEnabled = store.slice(
    store.indexOf('const DEFAULT_ENABLED_MODELS = new Set(['),
    store.indexOf('const DEFAULTS_VERSION'),
  )
  const migrations = store.slice(
    store.indexOf('const DEFAULTS_ADDED_IN:'),
    store.indexOf('function _loadEnabledModels'),
  )
  const modeDefaults = store.slice(
    store.indexOf('const modeDefaultModel:'),
    store.indexOf('export function getFamilyMode'),
  )
  for (const recipe of recipes) {
    assert.doesNotMatch(defaultEnabled, new RegExp(recipe))
    assert.doesNotMatch(migrations, new RegExp(recipe))
    assert.doesNotMatch(modeDefaults, new RegExp(recipe))
  }
  assert.match(notices, /creator-described|catlover1937/)
  assert.doesNotMatch(notices, /effectiveness (?:is|has been) verified/i)
})

test('manual verification is explicit, local-only in copy, and never routine polling', async () => {
  const client = await source('../src/api/client.ts')
  const selector = await source('../src/components/Sidebar/ModelSelector.tsx')
  const reference = await source('../src/components/Sidebar/ProjectReferenceLibrary.tsx')
  const verification = client.slice(
    client.indexOf('export async function verifyManualCheckpoint'),
    client.indexOf('export async function fetchModelDownloads'),
  )
  assert.match(verification, /verify-manual-checkpoint/)
  assert.match(verification, /method: 'POST'/)
  assert.match(verification, /available only on the local host/)
  assert.doesNotMatch(verification, /await res\.json\(\).*detail/)
  assert.match(selector, /await verifyManualCheckpoint\(currentModel\.model_type\)/)
  assert.match(reference, /verifyManualCheckpoint\(model\.model_type\)/)

  const loadModels = (await source('../src/stores/useStore.ts')).slice(
    (await source('../src/stores/useStore.ts')).indexOf('loadModels: async () =>'),
    (await source('../src/stores/useStore.ts')).indexOf('// Hydrate persisted per-mode settings'),
  )
  assert.doesNotMatch(loadModels, /verifyManualCheckpoint/)
})
