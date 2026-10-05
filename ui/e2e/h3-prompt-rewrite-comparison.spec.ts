import { expect, test, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { build } from 'esbuild'
import { fileURLToPath } from 'node:url'
import AxeBuilder from '@axe-core/playwright'

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value !== null && typeof value === 'object') {
    const object = value as Record<string, unknown>
    return '{' + Object.keys(object).sort().map(key => JSON.stringify(key) + ':' + canonical(object[key])).join(',') + '}'
  }
  return JSON.stringify(value)
}

function preview(original = 'A paper boat drifts across a quiet pond.', suffix = '', requestCommitment = 'a'.repeat(64)) {
  const unsigned = {
    schema_version: 2, request_commitment: requestCommitment, original_prompt: original,
    candidates: [
      { kind: 'deterministic', text: original, produced_by_runtime: false },
      { kind: 'base', text: 'A low camera follows a paper boat.\nRipples move across the pond.' + suffix, produced_by_runtime: true },
      { kind: 'adapted', text: 'A wide view of a paper boat crossing the still pond. <b>Plain text</b> ' + 'Longword'.repeat(80) + suffix, produced_by_runtime: true },
    ], selection: null,
    runtime_evidence: { execution_available: true, base_executed: true, adapter_executed: true,
      fallback_used: false, execution_receipt_sha256: 'b'.repeat(64) },
  }
  return { ...unsigned, commitment: createHash('sha256').update(canonical(unsigned)).digest('hex') }
}

interface Fixture {
  calls: { selected_kind: string; request_commitment: string; preview_commitment: string }[]
  applied: string[]
  render: (preview: unknown, binding?: { requestCommitment: string; originalPrompt: string }) => Promise<void>
  remount: () => void
  defer: () => void
  settle: (reject?: boolean) => void
}

const bundle = build({
  stdin: { resolveDir: fileURLToPath(new URL('..', import.meta.url)), loader: 'tsx', contents: `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import {H3PromptRewriteComparison} from './src/components/H3PromptRewriteComparison';
    import {parseH3PromptRewritePreview} from './src/lib/h3PromptRewritePreview';
    const element=document.getElementById('fixture'); let root=createRoot(element), active, binding, deferred=false, resolve, reject;
    const fixture={calls:[],applied:[],
      async render(input,current){active=await parseH3PromptRewritePreview(input,{requestCommitment:input.request_commitment,originalPrompt:input.original_prompt});binding=current||{requestCommitment:input.request_commitment,originalPrompt:input.original_prompt};draw()},
      remount(){root.unmount();root=createRoot(element);draw()},
      defer(){deferred=true},settle(failure){failure?reject(new Error('PRIVATE FAILURE MUST STAY HIDDEN')):resolve()}};
    function draw(){root.render(<H3PromptRewriteComparison preview={active} currentRequest={binding} onApply={async selection=>{
      fixture.calls.push(selection); const captured=active;
      if(deferred){deferred=false;await new Promise((yes,no)=>{resolve=yes;reject=no})}
      fixture.applied.push(captured.candidates.find(candidate=>candidate.kind===selection.selected_kind).text);
    }}/>) }
    window.__h3Fixture=fixture;
  ` },
  bundle: true, write: false, format: 'iife', platform: 'browser', jsx: 'automatic', logLevel: 'silent',
}).then(result => result.outputFiles[0].text)

async function setup(page: Page, withoutSubtle = false) {
  const unexpected: string[] = []
  const origin = new URL('http://127.0.0.1:' + process.env.MAESTRO_E2E_PORT).origin
  if (withoutSubtle) await page.addInitScript(() => Object.defineProperty(globalThis.crypto, 'subtle', { value: undefined, configurable: true }))
  await page.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.origin !== origin || !['GET', 'HEAD'].includes(request.method()) || url.pathname.startsWith('/api/')) {
      unexpected.push(request.method() + ' ' + url.pathname)
      await route.abort()
    } else if (url.pathname === '/') {
      await route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Synthetic H3 component fixture</title><link rel="stylesheet" href="/src/index.css"></head><body><main id="fixture" style="max-width:760px;margin:auto"></main></body></html>' })
    } else await route.continue()
  })
  await page.goto('/')
  await page.addScriptTag({ content: await bundle })
  const input = preview()
  await page.evaluate(input => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.render(input), input)
  await expect(page.getByRole('heading', { name: 'Compare prompts' })).toBeVisible()
  return { input, unexpected }
}

const state = (page: Page) => page.evaluate(() => {
  const fixture = (window as unknown as { __h3Fixture: Fixture }).__h3Fixture
  return { calls: fixture.calls, applied: fixture.applied }
})

test('browsing and selecting never applies; explicit Apply carries exact commitments', async ({ page }, info) => {
  const { input, unexpected } = await setup(page)
  const apply = page.getByRole('button', { name: 'Apply selected prompt', exact: true })
  await expect(apply).toBeDisabled()
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await page.getByRole('radio', { name: 'H3 rewrite', exact: true }).check()
  await expect(apply).toBeEnabled()
  expect(await state(page)).toEqual({ calls: [], applied: [] })
  await expect(page.locator('b')).toHaveCount(0)
  await expect(page.getByText(input.original_prompt, { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect((await new AxeBuilder({ page }).include('#fixture').analyze()).violations).toEqual([])
  await page.screenshot({ path: info.outputPath('h3-comparison-selected.png'), fullPage: true, animations: 'disabled' })
  await apply.click()
  await expect.poll(async () => (await state(page)).applied).toEqual([input.candidates[2].text])
  expect((await state(page)).calls).toEqual([{ selected_kind: 'adapted', request_commitment: input.request_commitment, preview_commitment: input.commitment }])
  expect(unexpected).toEqual([])
})

test('keyboard browsing stays unapplied until Apply; remount resets the radio choice', async ({ page }) => {
  const { unexpected } = await setup(page)
  const original = page.getByRole('radio', { name: 'Original', exact: true })
  await original.focus()
  await page.keyboard.press('Space')
  await expect(original).toBeChecked()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('radio', { name: 'Base model', exact: true })).toBeChecked()
  const focusedCard = page.getByRole('radio', { name: 'Base model', exact: true }).locator('..').locator('..')
  expect(await focusedCard.evaluate(element => getComputedStyle(element).outlineStyle)).not.toBe('none')
  expect(await state(page)).toEqual({ calls: [], applied: [] })
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: 'Apply selected prompt', exact: true })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await state(page)).applied.length).toBe(1)
  await page.evaluate(() => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.remount())
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Apply selected prompt', exact: true })).toBeDisabled()
  expect((await state(page)).calls).toHaveLength(1)
  expect((await state(page)).applied).toHaveLength(1)
  expect(unexpected).toEqual([])
})

test('replacement clears selection and a foreign current request disables the whole comparison', async ({ page }) => {
  const { input, unexpected } = await setup(page)
  await page.getByRole('radio', { name: 'Base model', exact: true }).check()
  const refreshed = preview(input.original_prompt, ' Refreshed comparison.')
  await page.evaluate(input => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.render(input), refreshed)
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await page.getByRole('radio', { name: 'Base model', exact: true }).check()
  const next = preview('A new untouched original.', ' New comparison.', 'c'.repeat(64))
  await page.evaluate(input => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.render(input), next)
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Apply selected prompt', exact: true })).toBeDisabled()
  await page.evaluate(input => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.render(input, { requestCommitment: 'd'.repeat(64), originalPrompt: input.original_prompt }), next)
  await expect(page.getByRole('alert')).toContainText('no longer matches your prompt')
  await expect(page.getByRole('radio')).toHaveCount(0)
  expect(await state(page)).toEqual({ calls: [], applied: [] })
  expect(unexpected).toEqual([])
})

test('async Apply prevents duplicate callbacks and a private failure shows only generic copy', async ({ page }) => {
  const { unexpected } = await setup(page)
  await page.getByRole('radio', { name: 'Base model', exact: true }).check()
  await page.evaluate(() => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.defer())
  await page.getByRole('button', { name: 'Apply selected prompt', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Applying…', exact: true })).toBeDisabled()
  await expect(page.getByRole('radio', { name: 'Original', exact: true })).toBeDisabled()
  expect((await state(page)).calls).toHaveLength(1)
  expect((await state(page)).applied).toHaveLength(0)
  await page.evaluate(() => (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.settle(true))
  await expect(page.getByRole('alert')).toHaveText('Could not apply this version. Try again.')
  await expect(page.getByText('PRIVATE FAILURE MUST STAY HIDDEN')).toHaveCount(0)
  await page.getByRole('button', { name: 'Apply selected prompt', exact: true }).click()
  await expect.poll(async () => (await state(page)).applied.length).toBe(1)
  expect((await state(page)).calls).toHaveLength(2)
  expect(unexpected).toEqual([])
})

test('without SubtleCrypto a valid comparison remains usable and a changed digest is rejected', async ({ page }) => {
  const { input, unexpected } = await setup(page, true)
  expect(await page.evaluate(() => typeof globalThis.crypto.subtle)).toBe('undefined')
  const accepted = await page.evaluate(async input => {
    try {
      await (window as unknown as { __h3Fixture: Fixture }).__h3Fixture.render({ ...input, commitment: '0'.repeat(64) })
      return true
    } catch { return false }
  }, input)
  expect(accepted).toBe(false)
  expect(await state(page)).toEqual({ calls: [], applied: [] })
  await page.getByRole('radio', { name: 'Base model', exact: true }).check()
  await page.getByRole('button', { name: 'Apply selected prompt', exact: true }).click()
  await expect.poll(async () => (await state(page)).applied).toEqual([input.candidates[1].text])
  expect((await state(page)).calls).toHaveLength(1)
  expect(unexpected).toEqual([])
})
