import { expect, test, type Page, type Route } from '@playwright/test'
import { createHash } from 'node:crypto'
import { installSyntheticApi } from './syntheticApi'

const original = 'A paper boat drifts across a quiet pond.'
const workspace = 'Synthetic project'
const projectInstance = 'e'.repeat(64)
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') {
    const object = value as Record<string, unknown>
    return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + canonical(object[key])).join(',') + '}'
  }
  return JSON.stringify(value)
}
function completedResult(mode = 't2va') {
  const roles = mode === 'fl2va' ? ['first_frame', 'last_frame'] : mode === 'i2va' ? ['first_frame'] : mode === 'l2va' ? ['last_frame'] : []
  const request = { schema_version: 1, original_prompt: original, mode, image_roles: roles.map(role => ({ role, input_id: role })), literal_anchors: [],
    role_commitments: roles.map(role => ({ role_id: role, commitment: createHash('sha256').update(role).digest('hex') })),
    execution_policy: { explicit_compose_only: true, auto_apply: false, learned_fallback: false, provider_fallback: false, content_classification: false } }
  const commitment = createHash('sha256').update(canonical(request)).digest('hex')
  const preview = { schema_version: 2, request_commitment: commitment, original_prompt: original,
    candidates: [
      { kind: 'deterministic', text: original, produced_by_runtime: false },
      { kind: 'base', text: 'A close camera follows the paper boat across the pond.', produced_by_runtime: true },
      { kind: 'adapted', text: 'A wide shot follows the paper boat. Ripples spread across the pond.', produced_by_runtime: true },
    ], selection: null,
    runtime_evidence: { execution_available: true, base_executed: true, adapter_executed: true, fallback_used: false, execution_receipt_sha256: 'b'.repeat(64) } }
  return { original, enhanced: original, h3_rewrite_request: { ...request, commitment },
    h3_rewrite_preview: { ...preview, commitment: createHash('sha256').update(canonical(preview)).digest('hex') } }
}
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const readPrompt = (page: Page) => page.evaluate(async () => {
  const modulePath = '/src/stores/useStore.ts'
  return (await import(/* @vite-ignore */ modulePath)).useStore.getState().params.prompt
})

async function setup(page: Page, withoutSubtle = false, lostApply = false) {
  const api = await installSyntheticApi(page)
  const submissions: Record<string, unknown>[] = [], applies: Record<string, unknown>[] = [], recoveryGets: string[] = []
  let result = completedResult()
  const uploads: string[] = []
  let prepares = 0
  await page.addInitScript(({ withoutSubtle }) => {
    localStorage.setItem('maestro_welcome_seen_v1', '1')
    if (withoutSubtle) Object.defineProperty(globalThis.crypto, 'subtle', { configurable: true, value: undefined })
  }, { withoutSubtle })
  await page.route('**/api/v1/llm/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/llm/models') return json(route, { models: [], guides: [], project_instance: projectInstance })
    if (path === '/api/v1/llm/prepare') { prepares++; return json(route, { detail: 'Unexpected ordinary preparation' }, 500) }
    if (path === '/api/v1/llm/enhance-prompt') {
      submissions.push(route.request().postDataJSON())
      result = completedResult(String(submissions.at(-1)!.rewrite_mode))
      const requestId = String(submissions.at(-1)!.request_id)
      return json(route, status(requestId), 202)
    }
    if (path.endsWith('/h3-apply')) {
      const body = route.request().postDataJSON(); applies.push(body)
      if (lostApply && applies.length === 1) return json(route, { detail: 'Synthetic lost Apply acknowledgement' }, 503)
      return json(route, { enhanced: result.h3_rewrite_preview.candidates.find(candidate => candidate.kind === body.selected_kind)!.text,
        request_commitment: body.request_commitment, preview_commitment: body.preview_commitment, selected_kind: body.selected_kind })
    }
    if (path.endsWith('/result')) { recoveryGets.push(path); return json(route, result) }
    if (path.startsWith('/api/v1/llm/operations/enhance/')) return json(route, status(path.split('/').at(-1)!))
    return route.fallback()
  })
  await page.route('**/api/v1/upload**', route => {
    const filename = /filename="([^"\r\n]+)"/.exec(route.request().postDataBuffer()?.toString('latin1') || '')?.[1]
    if (!filename) return json(route, { detail: 'Synthetic missing image' }, 400)
    uploads.push(filename)
    return json(route, { path: '/private/' + filename })
  })
  function status(requestId: string) { return { request_id: requestId.replaceAll('-', ''), operation_kind: 'enhance',
    status: 'completed', phase: 'completed', stage: 'completed', pass: 1, pass_limit: 1, attempt: 1, attempt_limit: 1,
    partial_text: '', generated_tokens_approx: 0, elapsed_seconds: 1, live_tps: null, average_tps: null, result_available: true, retryable: false } }
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Gallery', exact: true })).toBeVisible()
  await page.evaluate(async original => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ modulePath)
    useStore.setState((state: { params: Record<string, unknown> }) => ({ sidebarMode: 'studio', sidebarOpen: true, generationMode: 'video', durationSeconds: 6,
      startImage: null, endImage: null, imageRefs: [], params: { ...state.params, prompt: original, model_type: 'minimax_h3', image_prompt_type: 'T', image_start: '', image_end: '' } }))
  }, original)
  const compose = page.getByRole('button', { name: 'Compose with H3', exact: true })
  await page.getByRole('spinbutton', { name: 'Compose duration (seconds)', exact: true }).fill('6')
  await expect(compose).toBeEnabled()
  return { api, submissions, applies, recoveryGets, uploads, get result() { return result }, compose, get prepares() { return prepares } }
}

test('Studio Compose completes as an unselected comparison; explicit Apply never generates', async ({ page }, info) => {
  const f = await setup(page, true)
  await f.compose.click()
  const closeMenu = page.locator('#maestro-mobile-sidebar[role="dialog"]').getByRole('button', { name: 'Close creative workspace menu', exact: true })
  if (await closeMenu.isVisible()) await closeMenu.click()
  await expect(page.getByRole('heading', { name: 'Compare prompts', exact: true })).toBeVisible()
  expect(await readPrompt(page)).toBe(original)
  const apply = page.getByRole('button', { name: 'Apply selected prompt', exact: true })
  await expect(apply).toBeDisabled()
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Use & Generate', exact: true })).toHaveCount(0)
  expect(f.submissions).toHaveLength(1)
  expect(f.submissions[0]).toMatchObject({ engine: 'h3_rewriter', rewrite_mode: 't2va', image_paths: [], duration_seconds: 6,
    prompt: original, workspace, project_instance: projectInstance, model_type: 'minimax_h3' })
  expect(f.prepares).toBe(0)
  await page.getByRole('radio', { name: 'H3 rewrite', exact: true }).check()
  expect(f.applies).toHaveLength(0)
  expect(await readPrompt(page)).toBe(original)
  await page.screenshot({ path: info.outputPath('studio-h3-comparison.png'), fullPage: true, animations: 'disabled' })
  await apply.click()
  await expect(page.getByText('Applied to your prompt. No generation was started.', { exact: true })).toBeVisible()
  expect(await readPrompt(page)).toBe(f.result.h3_rewrite_preview.candidates[2].text)
  expect(f.applies).toEqual([{ workspace, project_instance: projectInstance, request_commitment: f.result.h3_rewrite_request.commitment,
    preview_commitment: f.result.h3_rewrite_preview.commitment, selected_kind: 'adapted' }])
  expect(f.api.generationRequests()).toHaveLength(0)
  await f.api.assertClean()
})

test('unacknowledged Apply stays unchanged until the user explicitly retries the same selection', async ({ page }) => {
  const f = await setup(page, false, true)
  await f.compose.click()
  const closeMenu = page.locator('#maestro-mobile-sidebar[role="dialog"]').getByRole('button', { name: 'Close creative workspace menu', exact: true })
  if (await closeMenu.isVisible()) await closeMenu.click()
  await expect(page.getByRole('heading', { name: 'Compare prompts' })).toBeVisible()
  await page.getByRole('radio', { name: 'Base model', exact: true }).check()
  const apply = page.getByRole('button', { name: 'Apply selected prompt', exact: true })
  await apply.click()
  await expect(page.getByRole('alert')).toContainText('Could not apply this version. Try again.')
  expect(await readPrompt(page)).toBe(original)
  expect(f.applies).toHaveLength(1)
  expect(f.submissions).toHaveLength(1)
  await apply.click()
  await expect(page.getByText('Applied to your prompt. No generation was started.', { exact: true })).toBeVisible()
  expect(f.applies).toHaveLength(2)
  expect(f.applies[1]).toEqual(f.applies[0])
  expect(await readPrompt(page)).toBe(f.result.h3_rewrite_preview.candidates[1].text)
  expect(f.api.generationRequests()).toHaveLength(0)
  await f.api.assertClean()
})

test('editing the current prompt invalidates the comparison without sending Apply or Generate', async ({ page }) => {
  const f = await setup(page)
  await f.compose.click()
  const closeMenu = page.locator('#maestro-mobile-sidebar[role="dialog"]').getByRole('button', { name: 'Close creative workspace menu', exact: true })
  if (await closeMenu.isVisible()) await closeMenu.click()
  await expect(page.getByRole('heading', { name: 'Compare prompts' })).toBeVisible()
  await page.getByRole('radio', { name: 'Original', exact: true }).check()
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ modulePath)
    useStore.getState().setParam('prompt', 'An edited original prompt.')
  })
  await expect(page.getByRole('button', { name: 'Recheck comparison', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply selected prompt', exact: true })).toBeDisabled()
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Compare prompts', exact: true })).toBeVisible()
  expect(f.applies).toHaveLength(0)
  expect(f.api.generationRequests()).toHaveLength(0)
  await f.api.assertClean()
})


test('reload keeps a completed comparison with an empty draft; only restored inputs and explicit GET recheck unlock Apply', async ({ page }) => {
  const f = await setup(page)
  await f.compose.click()
  const closeMenu = page.locator('#maestro-mobile-sidebar[role="dialog"]').getByRole('button', { name: 'Close creative workspace menu', exact: true })
  if (await closeMenu.isVisible()) await closeMenu.click()
  await expect(page.getByRole('heading', { name: 'Compare prompts', exact: true })).toBeVisible()
  const inputSnapshot = await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ modulePath)
    const s = useStore.getState()
    return { params: s.params, durationSeconds: s.durationSeconds, generationMode: s.generationMode,
      slidingWindowSeconds: s.slidingWindowSeconds, slidingWindowOverlap: s.slidingWindowOverlap,
      guideVideoFps: s.guideVideoFps, guideVideoFrameCount: s.guideVideoFrameCount, ttsVoiceCount: s.ttsVoiceCount, explicitOutput: s.explicitOutput }
  })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Compare prompts', exact: true })).toBeVisible()
  expect(await readPrompt(page)).toBe('')
  const recheck = page.getByRole('button', { name: 'Recheck comparison', exact: true })
  await expect(recheck).toBeVisible()
  const apply = page.getByRole('button', { name: 'Apply selected prompt', exact: true })
  await expect(apply).toBeDisabled()
  await page.evaluate(async snapshot => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ modulePath)
    // Disposable fixture restores the exact user's inputs, never product recovery code.
    useStore.setState(snapshot)
    useStore.getState().setH3ComposeDurationSeconds(6)
  }, inputSnapshot)
  expect(await readPrompt(page)).toBe(original)
  await expect(apply).toBeDisabled()
  expect(f.applies).toHaveLength(0)
  const getsBefore = f.recoveryGets.length
  await recheck.click()
  await expect(recheck).toHaveCount(0)
  await expect(page.getByRole('radio', { name: 'H3 rewrite', exact: true })).toBeEnabled()
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await expect(apply).toBeDisabled()
  expect(f.recoveryGets.length).toBe(getsBefore + 1)
  expect(new Set(f.recoveryGets).size).toBe(1)
  expect(f.submissions).toHaveLength(1)
  expect(f.api.generationRequests()).toHaveLength(0)
  await page.getByRole('radio', { name: 'H3 rewrite', exact: true }).check()
  await apply.click()
  await expect(page.getByText('Applied to your prompt. No generation was started.', { exact: true })).toBeVisible()
  expect(f.applies).toHaveLength(1)
  expect(f.submissions).toHaveLength(1)
  expect(f.api.generationRequests()).toHaveLength(0)
  await f.api.assertClean()
})


test('automatic Frames uses two actual chooser attachments in first/end order, and reattachment can GET recheck identical bytes', async ({ page }) => {
  const f = await setup(page)
  const automatic = page.getByRole('checkbox', { name: /^Match each shot automatically/ })
  await expect(automatic).toBeChecked()
  const firstLast = page.getByRole('button', { name: 'First / last frame', exact: true })
  await expect(firstLast).toBeEnabled()
  const bytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j8K0AAAAASUVORK5CYII=', 'base64')
  for (const name of ['first_frame.png', 'last_frame.png']) {
    const chooser = page.waitForEvent('filechooser')
    await firstLast.click()
    await (await chooser).setFiles({ name, mimeType: 'image/png', buffer: bytes })
  }
  await expect(page.getByAltText('Frame Start', { exact: true })).toBeVisible()
  await expect(page.getByAltText('Frame End', { exact: true })).toBeVisible()
  const before = await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const s = (await import(/* @vite-ignore */ modulePath)).useStore.getState()
    return { mode: s.params.image_prompt_type, files: [s.startImage?.name, s.endImage?.name] }
  })
  expect(before).toEqual({ mode: 'T', files: ['first_frame.png', 'last_frame.png'] })
  await f.compose.click()
  const closeMenu = page.locator('#maestro-mobile-sidebar[role="dialog"]').getByRole('button', { name: 'Close creative workspace menu', exact: true })
  if (await closeMenu.isVisible()) await closeMenu.click()
  await expect(page.getByRole('heading', { name: 'Compare prompts', exact: true })).toBeVisible()
  expect(f.submissions).toHaveLength(1)
  expect(f.submissions[0]).toMatchObject({ rewrite_mode: 'fl2va', image_paths: ['/private/first_frame.png', '/private/last_frame.png'] })
  expect(f.uploads).toEqual(['first_frame.png', 'last_frame.png'])
  expect(f.result.h3_rewrite_request.image_roles.map(role => role.role)).toEqual(['first_frame', 'last_frame'])
  const metadata = await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const s = (await import(/* @vite-ignore */ modulePath)).useStore.getState()
    return [s.startImage!.lastModified, s.endImage!.lastModified]
  })
  await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const { useStore } = await import(/* @vite-ignore */ modulePath)
    const s = useStore.getState()
    // Browser chooser-equivalent File rematerialization preserves bytes but changes timestamps.
    useStore.getState().setStartImage(new File([await s.startImage!.arrayBuffer()], s.startImage!.name, { type: s.startImage!.type, lastModified: s.startImage!.lastModified + 1000 }))
    useStore.getState().setEndImage(new File([await s.endImage!.arrayBuffer()], s.endImage!.name, { type: s.endImage!.type, lastModified: s.endImage!.lastModified + 1000 }))
  })
  const apply = page.getByRole('button', { name: 'Apply selected prompt', exact: true })
  await expect(apply).toBeDisabled()
  const gets = f.recoveryGets.length
  await page.getByRole('button', { name: 'Recheck comparison', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Recheck comparison', exact: true })).toHaveCount(0)
  await expect(page.getByRole('radio', { name: 'H3 rewrite', exact: true })).toBeEnabled()
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await expect(apply).toBeDisabled()
  expect(f.recoveryGets.length).toBe(gets + 1)
  expect(new Set(f.recoveryGets).size).toBe(1)
  expect(f.submissions).toHaveLength(1)
  expect(f.applies).toHaveLength(0)
  expect(f.uploads).toEqual(['first_frame.png', 'last_frame.png'])
  expect(await readPrompt(page)).toBe(original)
  expect(await page.evaluate(async () => {
    const modulePath = '/src/stores/useStore.ts'
    const s = (await import(/* @vite-ignore */ modulePath)).useStore.getState()
    return [s.startImage!.lastModified, s.endImage!.lastModified]
  })).toEqual(metadata.map(value => value + 1000))
  expect(f.api.generationRequests()).toHaveLength(0)
  await f.api.assertClean()
})
