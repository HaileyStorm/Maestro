import { expect, test, type Page, type Route } from '@playwright/test'
import { installSyntheticApi } from './syntheticApi'

const workspace = 'Synthetic project'
const modelId = 'synthetic-local-chat'
const json = (route: Route, body: unknown, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

async function setup(page: Page, immediate: boolean, vision = false) {
  const api = await installSyntheticApi(page)
  api.setAccountScenario('remote-user')
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
  const bodies: Record<string, unknown>[] = []
  const lookups: string[] = []
  const legacyIds = new Set<string>()
  let response: 'foreign' | 'missing' | 'completed' | 'partial' = immediate ? 'completed' : 'foreign'
  let uploadReconciliations = 0
  let projectInstance = 'synthetic-project-instance'
  let uploadDeletions = 0
  let nextLookupGate: Promise<void> | null = null
  await page.route('**/api/v1/llm/models*', route => json(route, {
    models: [{ id: modelId, label: 'Synthetic local model', size_hint: 'Test fixture',
      provider: 'local', installed: true, current: true, loaded: true, vision_capable: vision }],
    guides: [], project_instance: projectInstance,
  }))
  await page.route('**/api/v1/llm/status', route => json(route, {
    loaded: true, model_id: modelId, device: 'cpu', provider: 'local',
  }))
  await page.route('**/api/v1/llm/prepare', route => json(route, {
    operation_id: 'synthetic-ready', status: 'ready', phase: 'ready', retryable: false,
    model_id: modelId, workspace, purpose: 'chat',
  }))
  await page.route('**/api/v1/llm/chat', async route => {
    expect(route.request().method()).toBe('POST')
    bodies.push(route.request().postDataJSON() as Record<string, unknown>)
    return route.fulfill({ status: 202, contentType: 'application/json', body: '{"request_id":' })
  })
  await page.route('**/api/v1/llm/chat/*', async route => {
    const url = new URL(route.request().url())
    expect(route.request().method()).toBe('GET')
    expect(url.searchParams.get('workspace')).toBe(workspace)
    const id = decodeURIComponent(url.pathname.split('/').at(-1)!)
    lookups.push(id)
    const gate = nextLookupGate
    nextLookupGate = null
    if (gate) await gate
    expect(bodies.some(body => body.request_id === id) || legacyIds.has(id)).toBe(true)
    if (response === 'partial') {
      response = 'missing'
      return json(route, { request_id: id, status: 'running', phase: 'generating', retryable: false, partial_text: 'Preserve this partial response' })
    }
    if (response === 'missing') return json(route, { detail: 'Chat request not found' }, 404)
    return json(route, {
      request_id: response === 'foreign' ? '00000000-0000-4000-8000-000000000001' : id.replaceAll('-', ''),
      status: 'completed', phase: 'completed', retryable: false,
      result: { text: response === 'foreign' ? 'FOREIGN RESULT MUST STAY HIDDEN' : 'Recovered original response',
        model_id: modelId, guide_ids: [] },
    })
  })
  await page.route('**/api/v1/llm/chat-upload?*', route => {
    expect(route.request().method()).toBe('POST')
    return json(route, { filename: 'one-use-image.png', url: '/private-input' })
  })
  await page.route('**/api/v1/llm/chat-upload-request/*', route => {
    uploadReconciliations += 1
    return json(route, { state: 'not_found', deleted: 0 })
  })
  await page.route('**/api/v1/llm/chat-upload/*', route => {
    uploadDeletions += 1
    return json(route, {})
  })
  await page.goto('/')
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  const shell = page.locator('[data-chat-shell]')
  await expect(shell).toBeVisible()
  await expect(shell.getByRole('combobox', { name: 'Language model for Chat', exact: true })).toHaveValue(modelId)
  const composer = shell.locator('[data-chat-composer]')
  const draft = composer.locator('textarea')
  await expect(draft).toBeEnabled()
  return { api, bodies, lookups, shell, composer, draft,
    setResponse: (value: typeof response) => { response = value },
    reconciliations: () => uploadReconciliations, deletions: () => uploadDeletions,
    setProjectInstance: (value: string) => { projectInstance = value },
    addLegacyId: (id: string) => legacyIds.add(id),
    holdNextLookup: () => {
      let release!: () => void
      nextLookupGate = new Promise<void>(resolve => { release = resolve })
      return release
    } }
}

test('a truncated accepted Chat response retrieves one original turn', async ({ page }) => {
  const fixture = await setup(page, true)
  await fixture.draft.fill('Keep this accepted turn')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(1)
  await expect(fixture.shell.getByText('Keep this accepted turn', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.lookups).toHaveLength(1)
  await expect(fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })).toHaveCount(0)
  expect(fixture.reconciliations()).toBe(0)
  await fixture.api.assertClean()
})

test('foreign status and missing Resume keep Chat locked to its original request', async ({ page }, info) => {
  const fixture = await setup(page, false)
  await fixture.draft.fill('Keep this uncertain turn')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  const resume = fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })
  await expect(resume).toBeEnabled()
  await expect(fixture.shell.getByText('FOREIGN RESULT MUST STAY HIDDEN', { exact: true })).toHaveCount(0)
  fixture.setResponse('missing')
  await resume.click()
  await expect.poll(() => fixture.lookups.length).toBe(2)
  await expect(resume).toBeEnabled()
  await expect(fixture.composer.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  await expect(fixture.draft).toBeDisabled()
  await expect(fixture.shell.getByText('Keep this uncertain turn', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.reconciliations()).toBe(0)
  await page.screenshot({ path: info.outputPath('chat-resume-pending.png'), animations: 'disabled' })
  fixture.setResponse('completed')
  await resume.click()
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(new Set(fixture.lookups).size).toBe(1)
  await fixture.api.assertClean()
})

test('browser reload checks a pending Chat turn without a second submission', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('missing')
  await fixture.draft.fill('Keep this turn across reload')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await expect(fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })).toBeEnabled()
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })).toBeEnabled()
  await expect(fixture.composer.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  expect(fixture.reconciliations()).toBe(0)
  fixture.setResponse('completed')
  await fixture.shell.getByRole('button', { name: 'Resume wait', exact: true }).click()
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(1)
  expect(fixture.bodies).toHaveLength(1)
  expect(new Set(fixture.lookups).size).toBe(1)
  await fixture.api.assertClean()
})


test('confirmed exit preserves partial output and draft across reload without resending or deleting inputs', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('partial')
  await fixture.draft.fill('Draft to preserve after leaving')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  const leave = fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true })
  await expect(leave).toBeEnabled()
  await expect(fixture.shell.getByText('Preserve this partial response', { exact: true })).toBeVisible()
  await leave.click()
  const dialog = fixture.shell.getByRole('dialog', { name: 'Leave this pending response?' })
  await dialog.getByRole('button', { name: 'Keep waiting', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(fixture.draft).toBeDisabled()
  await leave.click()
  await dialog.getByRole('button', { name: 'Leave response', exact: true }).click()
  await expect(fixture.draft).toBeEnabled()
  await expect(fixture.draft).toHaveValue('Draft to preserve after leaving')
  await expect(fixture.shell.getByText(/Partial response \(left unfinished\):/)).toContainText('Preserve this partial response')
  const reads = fixture.lookups.length
  fixture.setResponse('completed')
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.draft).toBeEnabled()
  await expect(fixture.draft).toHaveValue('Draft to preserve after leaving')
  await expect(fixture.shell.getByText(/Partial response \(left unfinished\):/)).toContainText('Preserve this partial response')
  await expect(leave).toHaveCount(0)
  await expect(fixture.shell.getByText('Recovered original response', { exact: true })).toHaveCount(0)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.lookups).toHaveLength(reads)
  expect(fixture.deletions()).toBe(0)
  expect(fixture.reconciliations()).toBe(0)
  await fixture.api.assertClean()
})

test('an old unavailable turn cannot unlock or import text into a recreated project', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('missing')
  await fixture.draft.fill('Original project only')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true }).click()
  fixture.setProjectInstance('replacement-project-instance')
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.draft).toBeEnabled()
  await expect(fixture.draft).toHaveValue('')
  await expect(fixture.shell.getByRole('dialog')).toHaveCount(0)
  await expect(fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true })).toHaveCount(0)
  await expect(fixture.shell.getByText('Original project only', { exact: true })).toHaveCount(0)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.reconciliations()).toBe(0)
  await fixture.api.assertClean()
})


test('leaving a vision request keeps its image and requires reattachment after reload', async ({ page }) => {
  const fixture = await setup(page, false, true)
  fixture.setResponse('missing')
  const image = { name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jg3cAAAAASUVORK5CYII=', 'base64') }
  await fixture.composer.locator('input[type="file"]').setInputFiles(image)
  await fixture.draft.fill('Use the attached reference')
  const send = fixture.composer.getByRole('button', { name: 'Send', exact: true })
  await send.click()
  await fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true }).click()
  await fixture.shell.getByRole('dialog').getByRole('button', { name: 'Leave response', exact: true }).click()
  await expect(fixture.composer.getByRole('button', { name: 'Remove reference.png', exact: true })).toBeVisible()
  await expect(send).toBeEnabled()
  expect(fixture.bodies[0].image_paths).toEqual(['one-use-image.png'])
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.draft).toHaveValue('Use the attached reference')
  await expect(send).toBeDisabled()
  await fixture.draft.press('Enter')
  expect(fixture.bodies).toHaveLength(1)
  await fixture.composer.locator('input[type="file"]').setInputFiles(image)
  await expect(send).toBeEnabled()
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.deletions()).toBe(0)
  expect(fixture.reconciliations()).toBe(0)
  await fixture.api.assertClean()
})

test('an account transition cannot import another account draft or pending recovery', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('missing')
  await fixture.draft.fill('Original account draft')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true }).click()
  await fixture.shell.getByRole('dialog').getByRole('button', { name: 'Leave response', exact: true }).click()
  await expect(fixture.draft).toHaveValue('Original account draft')
  fixture.api.setAccountScenario('owner')
  await page.evaluate(async () => {
    const path = '/src/stores/useStore.ts'
    const { useStore } = await import(path)
    await useStore.getState().loadAccountContext()
  })
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.draft).toHaveValue('')
  await expect(fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true })).toHaveCount(0)
  fixture.api.setAccountScenario('remote-user')
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(fixture.draft).toHaveValue('Original account draft')
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.deletions()).toBe(0)
  await fixture.api.assertClean()
})


test('a delayed recovery response cannot acquire the account identity that replaced its sender', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('missing')
  const release = fixture.holdNextLookup()
  await fixture.draft.fill('Old account request')
  await fixture.composer.getByRole('button', { name: 'Send', exact: true }).click()
  await expect.poll(() => fixture.lookups.length).toBe(1)
  fixture.api.setAccountScenario('owner')
  await page.evaluate(async () => {
    const path = '/src/stores/useStore.ts'
    const { useStore } = await import(path)
    await useStore.getState().loadAccountContext()
  })
  release()
  await expect(page.getByRole('tab', { name: 'Chat', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(fixture.shell).toBeVisible()
  await expect(fixture.draft).toBeEnabled()
  await expect(fixture.draft).toHaveValue('')
  await expect(fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true })).toHaveCount(0)
  await expect(fixture.shell.getByRole('dialog')).toHaveCount(0)
  expect(fixture.bodies).toHaveLength(1)
  expect(fixture.deletions()).toBe(0)
  await fixture.api.assertClean()
})


test('legacy unbound pending records keep GET-only Resume and leave without importing composer inputs', async ({ page }) => {
  const fixture = await setup(page, false)
  fixture.setResponse('missing')
  const id = '10000000-0000-4000-8000-000000000001'
  fixture.addLegacyId(id)
  await page.evaluate(({ id, workspace, modelId }) => {
    const projectInstance = 'synthetic-project-instance'
    const submittedMessages = [{ role: 'user', content: 'Legacy uncertain request' }]
    localStorage.setItem(`maestro:llm-chat:${encodeURIComponent(workspace)}:${projectInstance}`, JSON.stringify(submittedMessages))
    localStorage.setItem(`maestro:llm-chat-operation:${encodeURIComponent(workspace)}:${projectInstance}`, JSON.stringify({
      requestId: id, workspace, projectInstance, modelId, effectiveModelId: modelId,
      draft: 'Unbound legacy draft must not import', retainedHistory: [], submittedMessages,
      requires_fresh_image: true,
    }))
  }, { id, workspace, modelId })
  await page.reload()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  const resume = fixture.shell.getByRole('button', { name: 'Resume wait', exact: true })
  await expect(resume).toBeEnabled()
  await expect(fixture.draft).toBeDisabled()
  await resume.click()
  await expect.poll(() => fixture.lookups.length).toBe(2)
  await expect(resume).toBeEnabled()
  expect(fixture.bodies).toHaveLength(0)
  await fixture.shell.getByRole('button', { name: 'Leave pending response', exact: true }).click()
  await fixture.shell.getByRole('dialog').getByRole('button', { name: 'Leave response', exact: true }).click()
  await expect(fixture.draft).toBeEnabled()
  await expect(fixture.draft).toHaveValue('')
  await expect(fixture.shell.getByText('Legacy uncertain request', { exact: true })).toBeVisible()
  expect(fixture.bodies).toHaveLength(0)
  expect(new Set(fixture.lookups)).toEqual(new Set([id]))
  expect(fixture.deletions()).toBe(0)
  expect(fixture.reconciliations()).toBe(0)
  await fixture.api.assertClean()
})
