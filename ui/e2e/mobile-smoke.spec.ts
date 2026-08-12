import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { connect } from 'node:net'
import { installSyntheticApi, type SyntheticApiController } from './syntheticApi'

const E2E_PORT = Number(process.env.MAESTRO_E2E_PORT)
if (!Number.isInteger(E2E_PORT) || E2E_PORT <= 0 || E2E_PORT > 65_535) {
  throw new Error('MAESTRO_E2E_PORT is required for synthetic browser tests')
}
const E2E_ORIGIN = `http://127.0.0.1:${E2E_PORT}`
const ACTION_SELECTOR = [
  'button',
  'a[href]',
  'input',
  'select',
  'textarea',
  'summary',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
  '[class~="cursor-pointer"]',
  '[role="button"]',
  '[role="checkbox"]',
  '[role="combobox"]',
  '[role="link"]',
  '[role="menuitem"]',
  '[role="menuitemcheckbox"]',
  '[role="menuitemradio"]',
  '[role="option"]',
  '[role="radio"]',
  '[role="slider"]',
  '[role="spinbutton"]',
  '[role="switch"]',
  '[role="tab"]',
  '[role="treeitem"]',
].join(',')

const VIEWPORTS = [
  { name: '320 portrait', width: 320, height: 568 },
  { name: '320 landscape', width: 568, height: 320 },
  { name: '390 portrait', width: 390, height: 844 },
  { name: '390 landscape', width: 844, height: 390 },
  { name: '767 portrait', width: 767, height: 1024 },
  { name: '767 landscape', width: 1024, height: 767 },
  { name: '768 portrait', width: 768, height: 1024 },
  { name: '768 landscape', width: 1024, height: 768 },
] as const

async function skipWelcome(page: Page) {
  await page.addInitScript(() => localStorage.setItem('maestro_welcome_seen_v1', '1'))
}

async function gotoSyntheticApp(page: Page) {
  const hostTermsSettled = page.waitForResponse(response => (
    new URL(response.url()).pathname === '/api/v1/host-terms'
  ))
  await page.goto('/')
  await hostTermsSettled
}

async function openAccountSupport(page: Page) {
  const trigger = page.getByRole('button', { name: /Open Support/ }).first()
  if (await page.evaluate(() => innerWidth <= 767)) await expectMinimumTarget(trigger)
  await trigger.click()
  const drawer = page.locator('#account-support-drawer[role="dialog"]')
  await expect(drawer).toBeVisible()
  await expect(page.locator('#root')).toHaveAttribute('inert', '')
  await expectBodyModalLock(page, true)
  const close = drawer.getByRole('button', { name: 'Close Support panel' }).last()
  await expect(close).toBeFocused()
  if (await page.evaluate(() => innerWidth <= 767)) await expectMinimumTarget(close)
  return { trigger, drawer }
}

async function expectNoHorizontalOverflow(page: Page) {
  const geometry = await page.evaluate(() => ({
    viewport: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
  }))
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewport + 1)
  expect(geometry.bodyWidth).toBeLessThanOrEqual(geometry.viewport + 1)
}

async function expectUniqueDialogNames(page: Page) {
  const names = await page.locator('[role="dialog"]:visible').evaluateAll(dialogs => dialogs.map(dialog => {
    const labelledBy = dialog.getAttribute('aria-labelledby')
    const label = dialog.getAttribute('aria-label')?.trim()
    const heading = labelledBy
      ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent?.trim() || '').join(' ').trim()
      : ''
    return label || heading
  }))
  expect(names.every(Boolean), 'Every visible dialog has an accessible name').toBe(true)
  expect(new Set(names).size, 'Visible dialog names are unique').toBe(names.length)
}

function rawLocalRequest(target: string, headers: string[] = []): Promise<string> {
  return new Promise((resolve, reject) => {
    const socket = connect(E2E_PORT, '127.0.0.1')
    let response = ''
    socket.setEncoding('utf8')
    socket.setTimeout(2_000, () => socket.destroy(new Error('Synthetic server probe timed out')))
    socket.on('connect', () => {
      socket.write([
        `GET ${target} HTTP/1.1`,
        `Host: 127.0.0.1:${E2E_PORT}`,
        ...headers,
        'Connection: close',
        '',
        '',
      ].join('\r\n'))
    })
    socket.on('data', chunk => {
      response += chunk
    })
    socket.on('error', reject)
    socket.on('end', () => resolve(response))
  })
}

async function expectMinimumTarget(locator: Locator, minimum = 44) {
  const box = await locator.boundingBox()
  expect(box, 'Visible action has a rendered hit target').not.toBeNull()
  const width = Math.round(box!.width * 1_000) / 1_000
  const height = Math.round(box!.height * 1_000) / 1_000
  expect(width).toBeGreaterThanOrEqual(minimum)
  expect(height).toBeGreaterThanOrEqual(minimum)
}

async function collectRenderedActionTargetViolations(root: Locator, minimum = 44) {
  return root.locator(ACTION_SELECTOR).evaluateAll((elements, required) => elements.flatMap(element => {
    const control = element as HTMLElement
    if (
      control.closest('[inert], [hidden], [aria-hidden="true"]')
      || control.getAttribute('aria-disabled') === 'true'
      || (control as HTMLButtonElement).disabled
    ) return []
    const style = getComputedStyle(control)
    const controlBox = control.getBoundingClientRect()
    if (
      style.display === 'none'
      || style.visibility === 'hidden'
      || Number(style.opacity) === 0
      || controlBox.width === 0
      || controlBox.height === 0
    ) return []

    let hitTarget: HTMLElement = control
    if (
      control instanceof HTMLInputElement
      && (control.type === 'checkbox' || control.type === 'radio')
    ) {
      const wrappingLabel = control.closest('label')
      const associatedLabel = control.id
        ? document.querySelector<HTMLLabelElement>(`label[for="${CSS.escape(control.id)}"]`)
        : null
      hitTarget = wrappingLabel || associatedLabel || control
    }
    const box = hitTarget.getBoundingClientRect()
    const width = Math.round(box.width * 1_000) / 1_000
    const height = Math.round(box.height * 1_000) / 1_000
    if (width >= required && height >= required) return []
    const name = control.getAttribute('aria-label')
      || control.getAttribute('title')
      || (control instanceof HTMLInputElement ? control.labels?.[0]?.textContent?.trim() : '')
      || control.parentElement?.textContent?.trim().replace(/\s+/g, ' ').slice(0, 80)
      || control.textContent?.trim().replace(/\s+/g, ' ').slice(0, 80)
      || control.getAttribute('name')
      || control.getAttribute('type')
      || control.id
      || control.tagName.toLowerCase()
    return [{
      element: control.tagName.toLowerCase(),
      name,
      width,
      height,
    }]
  }), minimum)
}

async function collectRenderedActionReachabilityViolations(root: Locator) {
  const controls = root.locator(ACTION_SELECTOR)
  const violations: Array<{ name: string; reason: string }> = []
  const count = await controls.count()
  for (let index = 0; index < count; index += 1) {
    const control = controls.nth(index)
    if (!await control.isVisible()) continue
    await control.scrollIntoViewIfNeeded()
    const finding = await control.evaluate(element => {
      const node = element as HTMLElement
      const box = node.getBoundingClientRect()
      let owner = node.parentElement
      while (owner) {
        const style = getComputedStyle(owner)
        const scrolls = /(auto|scroll)/.test(`${style.overflowX} ${style.overflowY}`)
          && (owner.scrollHeight > owner.clientHeight || owner.scrollWidth > owner.clientWidth)
        if (scrolls) break
        owner = owner.parentElement
      }
      const ownerBox = owner?.getBoundingClientRect() || {
        top: 0,
        left: 0,
        right: innerWidth,
        bottom: innerHeight,
      }
      const visibleTop = Math.max(0, ownerBox.top)
      const visibleLeft = Math.max(0, ownerBox.left)
      const visibleRight = Math.min(innerWidth, ownerBox.right)
      const visibleBottom = Math.min(innerHeight, ownerBox.bottom)
      const reachable = box.bottom >= visibleTop - 1
        && box.top <= visibleBottom + 1
        && box.right >= visibleLeft - 1
        && box.left <= visibleRight + 1
      if (reachable) return null
      return {
        name: node.getAttribute('aria-label')
          || node.getAttribute('title')
          || node.textContent?.trim().replace(/\s+/g, ' ').slice(0, 80)
          || node.tagName.toLowerCase(),
        reason: `outside ${owner ? 'scroll owner' : 'viewport'} after scrollIntoView`,
      }
    })
    if (finding) violations.push(finding)
  }
  return violations
}

async function expectRenderedActionsReachable(root: Locator) {
  expect(
    await collectRenderedActionReachabilityViolations(root),
    'Every rendered action can be brought into its intended scroll owner and viewport',
  ).toEqual([])
}

async function expectEveryRenderedActionMinimumTarget(root: Locator, minimum = 44) {
  const violations = await collectRenderedActionTargetViolations(root, minimum)
  expect(
    violations,
    `Every rendered actionable control in the top dialog has at least a ${minimum}x${minimum} CSS-pixel hit target`,
  ).toEqual([])
}

async function collectPrimaryTargetStates(page: Page) {
  const findings: Record<string, Awaited<ReturnType<typeof collectRenderedActionTargetViolations>>> = {}
  const root = page.locator('#root')

  await page.getByRole('tab', { name: 'Gallery', exact: true }).click()
  findings.gallery = await collectRenderedActionTargetViolations(root)

  const workspaceTrigger = page.getByRole('button', { name: /Current project: .*Open project selector/ })
  await workspaceTrigger.click()
  const workspaceDialog = page.getByRole('dialog', { name: 'Workspaces' })
  await expect(workspaceDialog).toBeVisible()
  findings.workspace = await collectRenderedActionTargetViolations(workspaceDialog)
  await page.locator('body').dispatchEvent('mousedown')
  await expect(workspaceDialog).toHaveCount(0)

  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(page.locator('[data-chat-shell]')).toBeVisible()
  findings.chat = await collectRenderedActionTargetViolations(root)

  await page.getByRole('tab', { name: /^Queue/ }).click()
  await expect(page.getByText(/1 waiting/).first()).toBeVisible()
  findings.queue = await collectRenderedActionTargetViolations(root)
  const queueHelp = root.locator('summary').filter({ hasText: 'How queue priority works' })
  await queueHelp.click()
  await expect(queueHelp.locator('xpath=..')).toHaveAttribute('open', '')
  findings.queueHelp = await collectRenderedActionTargetViolations(root)
  await queueHelp.click()

  const menuButton = page.getByRole('button', { name: 'Open Generate, Director, and Reference menu' })
  await menuButton.click()
  const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
  await expect(menu).toBeVisible()

  for (const mode of ['Generate', 'Director', 'Reference'] as const) {
    const modeButton = menu.getByRole('button', { name: `Open ${mode}`, exact: true })
    if (await modeButton.isEnabled()) {
      await modeButton.click()
      await expect(modeButton).toHaveAttribute('aria-pressed', 'true')
      findings[`menu${mode}`] = await collectRenderedActionTargetViolations(menu)

      const summaries = menu.locator('summary')
      const summaryCount = await summaries.count()
      for (let index = 0; index < summaryCount; index += 1) {
        const summary = summaries.nth(index)
        if (!await summary.isVisible()) continue
        await summary.click()
        findings[`menu${mode}Summary${index + 1}`] = await collectRenderedActionTargetViolations(menu)
        await summary.click()
      }
    }
  }

  const generateButton = menu.getByRole('button', { name: 'Open Generate', exact: true })
  await generateButton.click()
  await expect(generateButton).toHaveAttribute('aria-pressed', 'true')
  const generationModes = menu.getByRole('group', { name: 'Generation mode' })
  const videoMode = generationModes.getByRole('button', { name: 'Video', exact: true })
  await videoMode.click()
  await expect(videoMode).toHaveAttribute('aria-pressed', 'true')
  await expect(menu.getByText(/MiniMax H3 conditioning/).first()).toBeVisible()
  findings.menuGenerateVideoInputs = await collectRenderedActionTargetViolations(menu)
  const profileDisclosure = menu.getByRole('button', { name: /evaluated H3 engine \/ encoder profiles/ })
  if (await profileDisclosure.isVisible()) {
    await profileDisclosure.click()
    findings.menuGenerateProfiles = await collectRenderedActionTargetViolations(menu)
    await profileDisclosure.click()
  }
  const modelTrigger = menu.getByRole('button', { name: 'Synthetic H3', exact: true }).first()
  if (await modelTrigger.isVisible()) {
    await modelTrigger.click()
    findings.menuGenerateModels = await collectRenderedActionTargetViolations(menu)
    await modelTrigger.click()
  }
  await generationModes.getByRole('button', { name: 'Image', exact: true }).click()

  await menu.getByRole('button', { name: 'Close creative workspace menu' }).click()
  await expect(menu).toHaveAttribute('aria-hidden', 'true')
  await page.getByRole('tab', { name: 'Gallery', exact: true }).click()
  return findings
}

function summarizeTargetViolations(
  findings: Awaited<ReturnType<typeof collectPrimaryTargetStates>>,
) {
  const unique = new Map<string, {
    element: string
    name: string
    width: number
    height: number
    states: string[]
  }>()
  for (const [state, violations] of Object.entries(findings)) {
    for (const violation of violations) {
      const key = JSON.stringify(violation)
      const existing = unique.get(key)
      if (existing) existing.states.push(state)
      else unique.set(key, { ...violation, states: [state] })
    }
  }
  return [...unique.values()]
}

async function expectBodyModalLock(page: Page, locked: boolean) {
  await expect.poll(() => page.locator('body').evaluate(body => body.style.overflow))
    .toBe(locked ? 'hidden' : '')
}

async function expectNoBlockingAxeFindings(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  const blocking = results.violations.filter(violation => (
    violation.impact === 'serious' || violation.impact === 'critical'
  ))
  expect(blocking).toEqual([])
}

let api: SyntheticApiController | undefined
let pageErrors: string[] = []

test.beforeEach(async ({ page }) => {
  pageErrors = []
  const capturePageErrors = (candidate: Page) => {
    candidate.on('pageerror', error => pageErrors.push(error.message))
  }
  page.context().on('page', capturePageErrors)
  capturePageErrors(page)
  api = await installSyntheticApi(page)
})

test.afterEach(async () => {
  const failures: unknown[] = []
  try {
    expect(pageErrors, 'Synthetic UI must not raise uncaught page errors').toEqual([])
  } catch (error) {
    failures.push(error)
  }
  try {
    await api?.assertClean()
  } catch (error) {
    failures.push(error)
  }
  api = undefined
  pageErrors = []
  if (failures.length > 0) {
    throw new AggregateError(failures, 'Synthetic UI cleanup checks failed')
  }
})

for (const viewport of VIEWPORTS) {
  test(`content-free responsive shell: ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await gotoSyntheticApp(page)
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)

    const welcome = page.getByRole('dialog', { name: /Welcome to Maestro Continuum/ })
    await expect(welcome).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await expectUniqueDialogNames(page)
    await expectBodyModalLock(page, true)
    await expect(page.locator('#root')).toHaveAttribute('inert', '')

    if (viewport.width <= 767) {
      await expectEveryRenderedActionMinimumTarget(welcome)
    }
  })
}

test('200% layout zoom keeps the narrow synthetic shell usable', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 900 })
  await gotoSyntheticApp(page)
  await page.evaluate(() => {
    document.documentElement.style.zoom = '2'
  })

  await expect(page.getByRole('dialog', { name: /Welcome to Maestro Continuum/ })).toBeVisible()
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).zoom)).toBe('2')
  await expectNoHorizontalOverflow(page)
  await expectEveryRenderedActionMinimumTarget(
    page.getByRole('dialog', { name: /Welcome to Maestro Continuum/ }),
  )
})

test('mobile nested modal stack keeps only the top dialog interactive', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await skipWelcome(page)
  await gotoSyntheticApp(page)

  const menuButton = page.getByRole('button', { name: 'Open Generate, Director, and Reference menu' })
  await expectMinimumTarget(menuButton)
  await menuButton.click()
  await expect(page.getByRole('dialog', { name: 'Generate, Director, and Reference menu' })).toBeVisible()
  const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
  await expect(menu).toHaveAccessibleName('Generate, Director, and Reference menu')
  await expect(menu).toBeVisible()
  await expect(page.locator('#root')).toHaveAttribute('inert', '')
  await expectBodyModalLock(page, true)
  const menuClose = menu.getByRole('button', { name: 'Close creative workspace menu' })
  await expect(menuClose).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  expect(await menu.evaluate(element => element.contains(document.activeElement))).toBe(true)
  await page.keyboard.press('Tab')
  await expect(menuClose).toBeFocused()

  const advancedTrigger = menu.locator('button[aria-controls="advanced-settings-drawer"]')
  await expect(advancedTrigger).toHaveAccessibleName('Open Advanced Settings')
  await advancedTrigger.click()
  await expect(advancedTrigger).toHaveAttribute('aria-expanded', 'true')
  await expect(advancedTrigger).toHaveAttribute('aria-label', 'Close Advanced Settings')
  await expect(page.getByRole('dialog', { name: 'Advanced Settings' })).toBeVisible()
  const advanced = page.locator('#advanced-settings-drawer[role="dialog"]')
  const advancedClose = advanced.locator('button[aria-label="Close Advanced Settings"]')
  await expect(advanced).toBeVisible()
  await expect(advanced).not.toHaveAttribute('aria-hidden', 'true')
  await expect(advanced).not.toHaveAttribute('inert', '')
  await expect(menu).toHaveAttribute('inert', '')
  await expect(advancedClose).toBeFocused()
  await expectBodyModalLock(page, true)
  await expectMinimumTarget(advancedClose)
  await expectEveryRenderedActionMinimumTarget(advanced)
  await page.keyboard.press('Shift+Tab')
  expect(await advanced.evaluate(element => element.contains(document.activeElement))).toBe(true)
  await expect(advancedClose).not.toBeFocused()
  await page.keyboard.press('Tab')
  await expect(advancedClose).toBeFocused()

  const disclosureIds = await advanced
    .locator('button[aria-controls][aria-expanded="false"]:not([disabled])')
    .evaluateAll(buttons => buttons.flatMap(button => {
      const control = button as HTMLButtonElement
      const box = control.getBoundingClientRect()
      const style = getComputedStyle(control)
      const controls = control.getAttribute('aria-controls')
      return controls
        && style.display !== 'none'
        && style.visibility !== 'hidden'
        && box.width > 0
        && box.height > 0
        ? [controls]
        : []
    }))
  expect(disclosureIds.sort()).toEqual(['advanced-preset-save-form', 'post-processing-settings'])

  let expandedViolations: Awaited<ReturnType<typeof collectRenderedActionTargetViolations>> = []
  let voiceClone: Locator | undefined
  try {
    for (const id of disclosureIds) {
      const disclosure = advanced.locator(`button[aria-controls="${id}"]`)
      await disclosure.click()
      await expect(disclosure).toHaveAttribute('aria-expanded', 'true')
    }

    voiceClone = advanced.getByRole('switch', { name: 'Voice Clone' })
    await expect(voiceClone).toBeVisible()
    await expect(voiceClone).toHaveAttribute('aria-checked', 'false')
    await voiceClone.click()
    await expect(voiceClone).toHaveAttribute('aria-checked', 'true')
    expandedViolations = await collectRenderedActionTargetViolations(advanced)
  } finally {
    if (voiceClone && await voiceClone.getAttribute('aria-checked') === 'true') {
      await voiceClone.click()
      await expect(voiceClone).toHaveAttribute('aria-checked', 'false')
    }
    for (const id of disclosureIds.toReversed()) {
      const disclosure = advanced.locator(`button[aria-controls="${id}"]`)
      if (await disclosure.getAttribute('aria-expanded') === 'true') {
        await disclosure.click()
        await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
      }
    }
  }
  expect(
    expandedViolations,
    'Every rendered action in expanded Advanced sections, including Voice Clone controls, has a 44x44 CSS-pixel hit target',
  ).toEqual([])
  await expectUniqueDialogNames(page)

  await page.keyboard.press('Escape')
  await expect(advanced).toHaveAttribute('aria-hidden', 'true')
  await expect(advanced).toHaveAttribute('inert', '')
  await expect(page.getByRole('dialog', { name: 'Advanced Settings' })).toHaveCount(0)
  await expect(menu).toBeVisible()
  await expect(advancedTrigger).toBeFocused()
  await expect(page.locator('#root')).toHaveAttribute('inert', '')
  await expectBodyModalLock(page, true)

  await page.keyboard.press('Escape')
  await expect(menu).toHaveAttribute('aria-hidden', 'true')
  await expect(menu).toHaveAttribute('inert', '')
  await expect(page.getByRole('dialog', { name: 'Generate, Director, and Reference menu' })).toHaveCount(0)
  await expect(menuButton).toBeFocused()
  await expect(page.locator('#root')).not.toHaveAttribute('inert', '')
  await expectBodyModalLock(page, false)
})

for (const viewport of [
  { name: '320 narrow', width: 320, height: 568 },
  { name: '390 mobile', width: 390, height: 844 },
] as const) {
  test(`all primary mobile action states meet target geometry: ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await skipWelcome(page)
    await gotoSyntheticApp(page)

    const findings = await collectPrimaryTargetStates(page)
    expect(
      summarizeTargetViolations(findings),
      `Every rendered enabled primary action at ${viewport.width}px has a 44x44 CSS-pixel hit target`,
    ).toEqual([])
  })
}

for (const viewport of [
  { name: '320 portrait', width: 320, height: 568 },
  { name: '390 portrait', width: 390, height: 844 },
  { name: '568 landscape', width: 568, height: 320 },
  { name: '767 boundary', width: 767, height: 1024 },
] as const) {
  test(`representative mobile controls and overlays remain reachable: ${viewport.name}`, async ({ page }, testInfo) => {
    test.setTimeout(90_000)
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await skipWelcome(page)
    await gotoSyntheticApp(page)

    const resizedViewport = viewport.width > viewport.height
      ? { width: 390, height: 568 }
      : { width: 568, height: 320 }
    await page.setViewportSize(resizedViewport)
    await expect.poll(() => page.evaluate(() => ({ width: innerWidth, height: innerHeight })))
      .toEqual(resizedViewport)
    await expectNoHorizontalOverflow(page)

    const resizedMenuButton = page.getByRole('button', { name: 'Open Generate, Director, and Reference menu' })
    await expectRenderedActionsReachable(resizedMenuButton)
    if (testInfo.project.name === 'android-like-chromium') {
      const box = await resizedMenuButton.boundingBox()
      expect(box, 'Creative workspace button remains rendered after the post-load resize').not.toBeNull()
      await page.touchscreen.tap(box!.x + box!.width / 2, box!.y + box!.height / 2)
    } else {
      await resizedMenuButton.dispatchEvent('pointerdown', { pointerType: 'touch', isPrimary: true })
      await resizedMenuButton.dispatchEvent('pointerup', { pointerType: 'touch', isPrimary: true })
      await resizedMenuButton.dispatchEvent('click', { detail: 1 })
    }
    const resizedMenu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
    await expect(resizedMenu).toBeVisible()
    const resizedMenuClose = resizedMenu.getByRole('button', { name: 'Close creative workspace menu' })
    await expectRenderedActionsReachable(resizedMenuClose)
    await resizedMenuClose.click()
    await expect(resizedMenu).toHaveAttribute('aria-hidden', 'true')

    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await expect.poll(() => page.evaluate(() => ({ width: innerWidth, height: innerHeight })))
      .toEqual({ width: viewport.width, height: viewport.height })

    await page.getByRole('tab', { name: 'Gallery', exact: true }).click()
    await page.getByRole('button', { name: 'Search Gallery', exact: true }).click()
    const gallerySearch = page.getByRole('search', { name: 'Search Gallery' })
    await gallerySearch.getByRole('textbox', { name: 'Search Gallery' }).fill('synthetic')
    await expect(gallerySearch.getByRole('textbox', { name: 'Search Gallery' })).toHaveValue('synthetic')
    await expectEveryRenderedActionMinimumTarget(gallerySearch)
    await expectRenderedActionsReachable(gallerySearch)
    await gallerySearch.getByRole('button', { name: 'Clear search text and close search' }).click()

    await page.locator('button[aria-controls="gallery-filter-popover"]').click()
    const filters = page.getByRole('dialog', { name: 'Gallery filters' })
    await expect(filters).toBeVisible()
    const references = filters.getByRole('combobox', { name: 'References' })
    await references.selectOption('with')
    await expect(references).toHaveValue('with')
    await expectEveryRenderedActionMinimumTarget(filters)
    await expectRenderedActionsReachable(filters)
    await expectNoBlockingAxeFindings(page)
    await filters.getByRole('button', { name: 'Close Gallery filters' }).click()

    const menuButton = page.getByRole('button', { name: 'Open Generate, Director, and Reference menu' })
    await menuButton.click()
    const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
    await menu.getByRole('button', { name: 'Open Generate', exact: true }).click()
    const generationModes = menu.getByRole('group', { name: 'Generation mode' })
    const imageMode = generationModes.getByRole('button', { name: 'Image', exact: true })
    await imageMode.click()
    await expect(imageMode).toHaveAttribute('aria-pressed', 'true')
    await expectEveryRenderedActionMinimumTarget(menu)
    await expectRenderedActionsReachable(menu)

    await menu.getByRole('button', { name: 'Browse recipes' }).click()
    const recipes = page.getByRole('dialog', { name: 'Recipes' })
    await expect(recipes).toBeVisible()
    await expectEveryRenderedActionMinimumTarget(recipes)
    await expectRenderedActionsReachable(recipes)
    await expectNoBlockingAxeFindings(page)
    await recipes.getByRole('button', { name: 'Close recipes' }).last().click()

    if (await menu.getAttribute('aria-hidden') === 'true') {
      await page.getByRole('button', { name: 'Open Generate, Director, and Reference menu' }).click()
    }
    await menu.getByRole('button', { name: 'Open Director', exact: true }).click()
    await menu.getByRole('button', { name: 'Open Director pipeline dashboard' }).click()
    const director = page.getByRole('dialog', { name: 'Director Dashboard' })
    await expect(director).toBeVisible()
    await expect(director.getByRole('combobox')).toHaveValue('')
    await expectEveryRenderedActionMinimumTarget(director)
    await expectRenderedActionsReachable(director)
    await expectNoBlockingAxeFindings(page)
    await director.getByRole('button', { name: 'Close Director dashboard' }).last().click()
    await menu.getByRole('button', { name: 'Close creative workspace menu' }).click()

    await page.getByRole('button', { name: 'Open Support' }).click()
    const support = page.getByRole('dialog', { name: /Support/ })
    await expect(support).toBeVisible()
    await expectEveryRenderedActionMinimumTarget(support)
    await expectRenderedActionsReachable(support)
    await expectNoBlockingAxeFindings(page)
    await support.getByRole('button', { name: 'Close Support panel' }).last().click()

    await page.getByRole('button', { name: /What's new in/ }).click()
    const whatsNew = page.getByRole('dialog', { name: /What's new in/ })
    await expect(whatsNew).toBeVisible()
    await expect(whatsNew.locator('[class~="cursor-pointer"]')).not.toHaveCount(0)
    await expectEveryRenderedActionMinimumTarget(whatsNew)
    await expectRenderedActionsReachable(whatsNew)
    await expectNoBlockingAxeFindings(page)
    await whatsNew.getByRole('button', { name: "Close what's new" }).click()
    await expectNoHorizontalOverflow(page)
  })
}

for (const viewport of [
  { width: 320, height: 568 },
  { width: 390, height: 844 },
] as const) {
  test(`H3 video conditioning terms remain reachable at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await skipWelcome(page)
    await gotoSyntheticApp(page)

    await page.getByRole('button', { name: 'Open Generate, Director, and Reference menu' }).click()
    const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
    await menu.getByRole('button', { name: 'Open Generate', exact: true }).click()
    const generationModes = menu.getByRole('group', { name: 'Generation mode' })
    await generationModes.getByRole('button', { name: 'Video', exact: true }).click()
    await expect(menu.getByText(/MiniMax H3 conditioning/).first()).toBeVisible()

    const termsLink = menu.getByRole('link', { name: 'Review model terms', exact: true })
    const geometry = await termsLink.evaluate(element => {
      const box = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return {
        width: Math.round(box.width * 1_000) / 1_000,
        height: Math.round(box.height * 1_000) / 1_000,
        minWidth: style.minWidth,
        minHeight: style.minHeight,
        display: style.display,
        mobileTarget: element.classList.contains('mobile-control-target'),
      }
    })
    if (process.env.MAESTRO_E2E_REPORT_TARGETS === '1') {
      console.log(`H3 terms geometry ${viewport.width}px: ${JSON.stringify(geometry)}`)
    }
    expect(geometry.mobileTarget).toBe(true)
    expect(geometry.minWidth).toBe('44px')
    expect(geometry.minHeight).toBe('44px')
    expect(geometry.display).toBe('inline-flex')
    await expectMinimumTarget(termsLink)
    await expect(termsLink).toHaveAttribute('href', 'https://huggingface.co/MiniMaxAI/MiniMax-H3')
  })
}

test('chat geometry and queue last-good retention remain usable on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await skipWelcome(page)
  await gotoSyntheticApp(page)

  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  const shell = page.locator('[data-chat-shell]')
  const transcript = page.getByRole('region', { name: 'Chat transcript' })
  await expect(shell).toBeVisible()
  await expect(transcript).toBeVisible()
  const chatGeometry = await page.evaluate(() => {
    const shellElement = document.querySelector<HTMLElement>('[data-chat-shell]')!
    const transcriptElement = document.querySelector<HTMLElement>('[data-chat-transcript]')!
    const composerElement = document.querySelector<HTMLElement>('[data-chat-composer]')!
    return {
      shellHeight: shellElement.getBoundingClientRect().height,
      transcriptHeight: transcriptElement.getBoundingClientRect().height,
      transcriptOverflow: getComputedStyle(transcriptElement).overflowY,
      composerOverflow: getComputedStyle(composerElement).overflowY,
    }
  })
  expect(chatGeometry.transcriptHeight).toBeGreaterThanOrEqual(chatGeometry.shellHeight * 0.30 - 1)
  expect(chatGeometry.transcriptOverflow).toBe('auto')
  expect(chatGeometry.composerOverflow).toBe('auto')
  await expectNoHorizontalOverflow(page)

  await page.getByRole('tab', { name: /^Queue/ }).click()
  await expect(page.getByText(/1 waiting/).first()).toBeVisible()
  api!.setQueueFailure(true)
  await page.evaluate(() => window.dispatchEvent(new Event('maestro:queue-refresh')))
  await expect(page.getByRole('status')).toContainText('Showing the last successful update')
  await expect(page.getByText(/1 waiting/).first()).toBeVisible()

  api!.setQueueFailure(false)
  api!.setQueueHeld(true)
  api!.setQueueDelay(250)
  await page.evaluate(() => window.dispatchEvent(new Event('maestro:queue-refresh')))
  await page.waitForTimeout(50)
  await expect(page.getByText(/1 waiting/).first()).toBeVisible()
  await expect(page.getByText(/1 held/).first()).toBeVisible()
  await expect(page.getByRole('status')).toHaveCount(0)
  api!.setQueueDelay(0)

  api!.setQueueFailure(true)
  api!.setQueueHeld(false)
  await page.context().setOffline(true)
  try {
    expect(await page.evaluate(() => navigator.onLine)).toBe(false)
    await page.evaluate(() => window.dispatchEvent(new Event('maestro:queue-refresh')))
    await expect(page.getByRole('status')).toContainText('Showing the last successful update')
    await expect(page.getByText(/1 held/).first()).toBeVisible()
  } finally {
    await page.context().setOffline(false)
  }
  expect(await page.evaluate(() => navigator.onLine)).toBe(true)
  api!.setQueueFailure(false)
  await page.evaluate(() => window.dispatchEvent(new Event('maestro:queue-refresh')))
  await expect(page.getByText(/1 waiting/).first()).toBeVisible()
  await expect(page.getByRole('status')).toHaveCount(0)
})

test('representative shell has no serious or critical axe findings', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await gotoSyntheticApp(page)
  await expect(page.getByRole('dialog', { name: /Welcome to Maestro Continuum/ })).toBeVisible()

  await expectNoBlockingAxeFindings(page)

  await page.getByRole('button', { name: /Enter the studio/ }).click()
  await expect(page.locator('#root')).not.toHaveAttribute('inert', '')
  await expectNoBlockingAxeFindings(page)
})

test('accounts-disabled compatibility, local bootstrap, and remote bootstrap boundaries stay explicit', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await skipWelcome(page)
  api!.setAccountScenario('disabled')
  await gotoSyntheticApp(page)

  let opened = await openAccountSupport(page)
  await expect(opened.drawer).toHaveAccessibleName('Support')
  await expect(opened.drawer.getByRole('tab', { name: 'Account' })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(opened.drawer).toHaveCount(0)
  await expect(opened.trigger).toBeFocused()

  api!.setAccountScenario('local-pristine')
  await gotoSyntheticApp(page)
  opened = await openAccountSupport(page)
  await expect(opened.drawer).toHaveAccessibleName('Support & account')
  await opened.drawer.getByRole('tab', { name: 'Account' }).click()
  const bootstrap = opened.drawer.getByRole('heading', { name: 'Create the first owner account' })
    .locator('xpath=../..')
  await expect(bootstrap).toContainText('server explicitly offered local bootstrap')
  await bootstrap.getByLabel('Username', { exact: true }).fill('Synthetic Owner')
  await bootstrap.getByLabel('Password', { exact: true }).fill('synthetic-bootstrap-password')
  await bootstrap.getByLabel('Device label', { exact: true }).fill('Synthetic browser')
  await bootstrap.getByRole('button', { name: 'Create owner account' }).click()
  await expect(opened.drawer.getByText('Owner account created and signed in.')).toBeVisible()
  await expect(opened.drawer.getByText('Synthetic Owner', { exact: true }).first()).toBeVisible()
  await expect(opened.drawer.getByRole('heading', { name: 'Owner recovery codes' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Current project: Synthetic project/ })).toBeVisible()
  await page.keyboard.press('Escape')

  api!.setAccountScenario('remote-anonymous')
  await gotoSyntheticApp(page)
  const remoteBootstrapStatus = await page.evaluate(async () => {
    const response = await fetch('/api/v1/account/nonce', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ purpose: 'bootstrap' }),
    })
    return response.status
  })
  expect(remoteBootstrapStatus).toBe(403)
  opened = await openAccountSupport(page)
  await opened.drawer.getByRole('tab', { name: 'Account' }).click()
  await expect(opened.drawer.getByRole('heading', { name: 'Create the first owner account' })).toHaveCount(0)
  await expect(opened.drawer.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(opened.drawer).toContainText('Project access stays separate from this account')
})

test('anonymous login, owner reauthentication, session revocation, and logout preserve project authority', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await skipWelcome(page)
  api!.setAccountScenario('local-anonymous')
  await gotoSyntheticApp(page)

  const projectTrigger = page.getByRole('button', { name: /Current project: Synthetic project/ })
  await expect(projectTrigger).toBeVisible()
  const { drawer } = await openAccountSupport(page)
  await drawer.getByRole('tab', { name: 'Account' }).click()
  const login = drawer.getByRole('heading', { name: 'Sign in' }).locator('xpath=../..')
  await login.getByLabel('Username', { exact: true }).fill('Synthetic Owner')
  await login.getByLabel('Password', { exact: true }).fill('synthetic-owner-password')
  await login.getByLabel('Device label', { exact: true }).fill('Synthetic browser')
  await login.getByRole('button', { name: 'Sign in', exact: true }).click()

  await expect(drawer.getByText('Signed in.', { exact: true })).toBeVisible()
  await expect(drawer.getByText('Confirmation needed for sensitive actions')).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Active sessions' })).toBeVisible()
  await expect(drawer.getByText('Synthetic tablet', { exact: false })).toBeVisible()
  await expect(drawer.getByRole('button', { name: 'Revoke other sessions' })).toBeDisabled()

  const confirmation = drawer.getByRole('heading', { name: 'Confirm your password' }).locator('xpath=..')
  await confirmation.getByLabel('Current password').fill('synthetic-owner-password')
  await confirmation.getByRole('button', { name: 'Confirm password' }).click()
  await expect(drawer.getByText('Sensitive account actions are temporarily unlocked.')).toBeVisible()
  await expect(drawer.getByText('Recently confirmed')).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'User administration' })).toBeVisible()

  const otherSession = drawer.getByText('Synthetic tablet', { exact: false }).locator('xpath=../..')
  await otherSession.getByRole('button', { name: 'Revoke' }).click()
  await expect(drawer.getByText('Session revoked.')).toBeVisible()
  await expect(drawer.getByText('Synthetic tablet', { exact: false })).toHaveCount(0)
  await expect(drawer.getByText('Synthetic browser', { exact: false })).toBeVisible()

  await drawer.getByRole('button', { name: 'Sign out', exact: true }).last().click()
  await expect(drawer.getByText('Signed out. Project and output access were not changed.')).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(projectTrigger).toBeVisible()
})

test('a normal account gets self-service without owner administration', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 })
  await skipWelcome(page)
  api!.setAccountScenario('user')
  await gotoSyntheticApp(page)

  const { drawer } = await openAccountSupport(page)
  await drawer.getByRole('tab', { name: 'Account' }).click()
  await expect(drawer.getByText('Synthetic User', { exact: true }).first()).toBeVisible()
  await expect(drawer.getByText('user', { exact: true }).first()).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Active sessions' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Password and recovery' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'User administration' })).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: 'Create user' })).toHaveCount(0)
  await expect(drawer).toContainText('account cookie does not replace or mutate the browser session')
})

test('unknown and external requests are blocked before leaving the fixture', async ({ page }) => {
  await skipWelcome(page)
  await gotoSyntheticApp(page)

  const rejected = await page.evaluate(async () => {
    const outcomes = await Promise.allSettled([
      fetch('/api/v1/not-a-fixture'),
      fetch('/classic/not-a-fixture'),
      fetch('https://example.invalid/maestro-e2e'),
    ])
    return outcomes.map(outcome => outcome.status)
  })
  const websocketClosed = await page.evaluate(() => new Promise<boolean>(resolve => {
    const socket = new WebSocket(`${location.origin.replace(/^http/, 'ws')}/not-a-fixture`)
    socket.addEventListener('open', () => resolve(false))
    socket.addEventListener('error', () => resolve(true))
    socket.addEventListener('close', () => resolve(true))
  }))
  const popupPromise = page.waitForEvent('popup')
  await page.evaluate(() => {
    const link = document.createElement('a')
    link.href = 'https://example.invalid/maestro-e2e-popup'
    link.target = '_blank'
    document.body.append(link)
    link.click()
    link.remove()
  })
  const popup = await popupPromise
  await page.waitForTimeout(50)
  await popup.close()
  expect(rejected).toEqual(['rejected', 'rejected', 'rejected'])
  expect(websocketClosed).toBe(true)
  expect(api!.takeUnexpected().sort()).toEqual([
    'external GET https://example.invalid/maestro-e2e',
    'external GET https://example.invalid/maestro-e2e-popup',
    'unknown GET /api/v1/not-a-fixture',
    'unknown GET /classic/not-a-fixture',
    `websocket ${E2E_ORIGIN.replace(/^http/, 'ws')}/not-a-fixture`,
  ])
})

test('synthetic server refuses direct backend proxy bypasses', async ({ request }) => {
  const [apiResponse, classicResponse, fsResponse, encodedFsResponse, doubleEncodedFsResponse, externalResponse, websocketResponse, wrongHealthResponse] = await Promise.all([
    request.get('/api/v1/not-a-fixture'),
    request.get('/classic/not-a-fixture'),
    request.get('/@fs/C:/Users/example/.ssh/id_rsa'),
    request.get('/%40fs%2FC:%2FUsers%2Fexample%2F.aws%2Fcredentials'),
    request.get('/%2540fs%252FC:%252FUsers%252Fexample%252F.aws%252Fcredentials'),
    rawLocalRequest('https://example.invalid/maestro-e2e'),
    rawLocalRequest('/not-a-fixture', [
      'Upgrade: websocket',
      'Connection: Upgrade',
      'Sec-WebSocket-Key: c3ludGhldGljLW1hZXN0cm8=',
      'Sec-WebSocket-Version: 13',
    ]),
    rawLocalRequest('/__maestro_e2e_health/not-this-run'),
  ])

  expect(apiResponse.status()).toBe(403)
  expect(classicResponse.status()).toBe(403)
  expect(fsResponse.status()).toBe(403)
  expect(encodedFsResponse.status()).toBe(403)
  expect(doubleEncodedFsResponse.status()).toBe(403)
  expect(externalResponse).toMatch(/^HTTP\/1\.1 403 Forbidden/m)
  expect(websocketResponse).toMatch(/^HTTP\/1\.1 403 Forbidden/m)
  expect(wrongHealthResponse).toMatch(/^HTTP\/1\.1 403 Forbidden/m)
  await expect(apiResponse.json()).resolves.toEqual({
    detail: 'Synthetic browser tests do not proxy backend requests.',
  })
  await expect(classicResponse.json()).resolves.toEqual({
    detail: 'Synthetic browser tests do not proxy backend requests.',
  })
  await expect(fsResponse.json()).resolves.toEqual({
    detail: 'Synthetic browser tests restrict Vite filesystem requests to the isolated dependency cache.',
  })
  await expect(encodedFsResponse.json()).resolves.toEqual({
    detail: 'Synthetic browser tests restrict Vite filesystem requests to the isolated dependency cache.',
  })
  await expect(doubleEncodedFsResponse.json()).resolves.toEqual({
    detail: 'Synthetic browser tests restrict Vite filesystem requests to the isolated dependency cache.',
  })
})
