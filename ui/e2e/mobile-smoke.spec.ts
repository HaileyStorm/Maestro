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

const TOOLBAR_VIEWPORTS = [
  { name: '320 portrait', width: 320, height: 568 },
  { name: '390 portrait', width: 390, height: 844 },
  { name: '568 landscape', width: 568, height: 320 },
  { name: '767 boundary', width: 767, height: 1024 },
  { name: '768 boundary', width: 768, height: 1024 },
  { name: '1024 landscape', width: 1024, height: 768 },
  { name: '1080p desktop', width: 1920, height: 1080 },
  { name: '4K desktop', width: 3840, height: 2160 },
] as const

const PRODUCT_ACCEPTANCE_VIEWPORTS = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 900 },
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

type RootFaultTarget = 'sidebar' | 'main' | 'account' | 'portal'

async function routeOneShotRootFault(page: Page, target: RootFaultTarget) {
  const once = `
    function failOnce() {
      const counts = globalThis.__maestroRootFaultCounts ||= {};
      counts.${target} = (counts.${target} || 0) + 1;
      if (counts.${target} === 1) throw new Error('Synthetic synchronous render fault');
    }
  `
  const modules: Record<RootFaultTarget, { path: string; source: string }> = {
    sidebar: {
      path: '**/src/components/Sidebar/Sidebar.tsx',
      source: `${once} export function Sidebar() { failOnce(); return null }`,
    },
    main: {
      path: '**/src/components/MainContent/MainContent.tsx',
      source: `${once} export function MainContent() { failOnce(); return null }`,
    },
    account: {
      path: '**/src/components/AccountSupport/AccountSupportDrawer.tsx',
      source: `${once}
        export function AccountSupportButton() { return null }
        export function AccountSupportDrawer() { failOnce(); return null }
      `,
    },
    portal: {
      path: '**/src/components/WhatsNewDialog.tsx',
      source: `
        import { createElement } from '/node_modules/.vite/deps/react.js';
        import { createPortal } from '/node_modules/.vite/deps/react-dom.js';
        ${once}
        function PortalFault() { failOnce(); return null }
        export function WhatsNewButton() { return null }
        export function WhatsNewDialogHost() {
          return createPortal(createElement(PortalFault), document.body)
        }
      `,
    },
  }
  const selected = modules[target]
  await page.route(selected.path, route => route.fulfill({
    status: 200,
    contentType: 'application/javascript',
    body: selected.source,
  }))
}

async function routeMigratedUserAccess(page: Page, username: string) {
  await page.route('**/api/v1/access-context', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      remote: true,
      project_password_required: false,
      project_names_visible: true,
      machine_controls: false,
      custom_model_sources: false,
      catalog_model_downloads: false,
      classic_ui: false,
      cloudflare_enabled: true,
      share_url: 'https://stable.example.test',
      share_flow: 'account',
      account_project_access_active: true,
      account_project_creation_requires_account: true,
      accounts: {
        enabled: true,
        authenticated: true,
        account: {
          id: 'synthetic-user-account',
          username,
          role: 'user',
          disabled: false,
          created_at: 1_725_000_100,
          has_email: false,
          passkey_credentials: 0,
          passkey_authentication_available: false,
        },
        capabilities: ['account.self'],
        reauthenticated: true,
        passkey_authentication_available: false,
      },
    }),
  }))
  await page.route('**/api/v1/sample-campaign/queue', route => route.fulfill({
    status: 404,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'No sample campaign queue in this fixture' }),
  }))
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

  const menuButton = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
  await menuButton.click()
  const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
  await expect(menu).toBeVisible()

  for (const mode of ['Generate', 'Director', 'References'] as const) {
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
  await expect(menu.getByRole('link', { name: 'Review model terms', exact: true })).toBeVisible()
  findings.menuGenerateVideoInputs = await collectRenderedActionTargetViolations(menu)
  const profileDisclosure = menu.getByRole('button', { name: /technical comparison profiles/ })
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

async function expectPrimaryActionContrast(action: Locator) {
  await expect(action).toBeVisible()
  const result = await action.evaluate(element => {
    const parseRgb = (value: string) => {
      const channels = value.match(/[\d.]+/g)?.map(Number)
      if (!channels || (channels.length !== 3 && channels.length !== 4)) {
        throw new Error(`Unsupported computed color: ${value}`)
      }
      if (channels.length === 4 && channels[3] !== 1) {
        throw new Error(`Translucent computed color cannot be measured directly: ${value}`)
      }
      return channels.slice(0, 3)
    }
    const luminance = (channels: number[]) => channels
      .map(channel => channel / 255)
      .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
      .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0)
    const style = getComputedStyle(element)
    const foreground = luminance(parseRgb(style.color))
    const background = luminance(parseRgb(style.backgroundColor))
    return {
      backgroundColor: style.backgroundColor,
      color: style.color,
      ratio: (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05),
    }
  })
  expect(result.ratio, `${result.color} on ${result.backgroundColor} meets WCAG AA`).toBeGreaterThanOrEqual(4.5)
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

for (const target of ['sidebar', 'main', 'account', 'portal'] as const) {
  test(`root recovery boundary retries one synchronous ${target} render fault without a loop`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await skipWelcome(page)
    await routeOneShotRootFault(page, target)
    await page.goto('/')

    const recovery = page.getByRole('heading', { name: 'Maestro needs to recover this screen' })
    await expect(recovery).toBeVisible()
    await expect(page.getByRole('button', { name: 'Reload Maestro' })).toBeVisible()
    await page.getByRole('button', { name: 'Try again' }).click()
    await expect(recovery).toHaveCount(0)
    await expect(page.locator('#root')).not.toBeEmpty()
    await page.waitForTimeout(100)
    expect(await page.evaluate(fault => (
      (globalThis as typeof globalThis & { __maestroRootFaultCounts?: Record<string, number> })
        .__maestroRootFaultCounts?.[fault]
    ), target)).toBe(2)
  })
}

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

test('shared toolbar keeps view hierarchy and panel baseline stable across simulated viewports', async ({ page }) => {
  test.setTimeout(120_000)
  await page.route('**/api/v1/sample-campaign/queue', route => route.fulfill({
    status: 404,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'No sample campaign queue in this fixture' }),
  }))
  await page.setViewportSize(TOOLBAR_VIEWPORTS[0])
  await skipWelcome(page)
  await gotoSyntheticApp(page)

  const toolbar = page.locator('[data-main-toolbar]')
  const primaryRow = page.locator('[data-main-toolbar-primary]')
  const viewRow = page.locator('[data-main-toolbar-view]')
  await expect(toolbar).toBeVisible()
  await expect(primaryRow).toBeVisible()
  await expect(viewRow).toBeVisible()

  for (const viewport of TOOLBAR_VIEWPORTS) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await expect.poll(() => page.evaluate(() => ({ width: innerWidth, height: innerHeight })))
      .toEqual({ width: viewport.width, height: viewport.height })

    const viewGeometry: Array<{
      view: string
      toolbarHeight: number
      panelTop: number
      primaryTop: number
      primaryBottom: number
      viewTop: number
      viewBottom: number
      toolbarBottom: number
    }> = []

    for (const view of ['Gallery', 'Queue', 'Chat'] as const) {
      const tab = page.getByRole('tab', { name: view === 'Queue' ? /^Queue/ : view, exact: view !== 'Queue' })
      await tab.click()
      await expect(tab).toHaveAttribute('aria-selected', 'true')
      await expect(page.locator(`[role="tabpanel"][aria-labelledby="main-${view.toLowerCase()}-tab"]`)).toBeVisible()

      if (view === 'Gallery') {
        await expect(viewRow.getByRole('button', { name: 'Search Gallery' })).toBeVisible()
        await expect(viewRow.locator('button[aria-controls="gallery-filter-popover"]')).toBeVisible()
        if (viewport.width === 768 || viewport.width === 1024) {
          const workspaceTrigger = primaryRow.getByRole('button', { name: /Current project: .*Open project selector/ })
          const supportTrigger = page.getByRole('button', { name: 'Open Support', exact: true })
          await expect(workspaceTrigger).toBeVisible()
          await expect(supportTrigger).toBeVisible()
          const [workspaceBox, supportBox] = await Promise.all([
            workspaceTrigger.boundingBox(),
            supportTrigger.boundingBox(),
          ])
          expect(workspaceBox).not.toBeNull()
          expect(supportBox).not.toBeNull()
          const overlapWidth = Math.min(workspaceBox!.x + workspaceBox!.width, supportBox!.x + supportBox!.width)
            - Math.max(workspaceBox!.x, supportBox!.x)
          const overlapHeight = Math.min(workspaceBox!.y + workspaceBox!.height, supportBox!.y + supportBox!.height)
            - Math.max(workspaceBox!.y, supportBox!.y)
          expect(overlapWidth <= 0 || overlapHeight <= 0, `${viewport.name} Workspace and Support do not overlap`).toBe(true)
        }
        if (viewport.width === 1024) {
          const actionBounds = await viewRow.locator('button:visible').evaluateAll(buttons => {
            const row = document.querySelector<HTMLElement>('[data-main-toolbar-view]')!
            const rowBox = row.getBoundingClientRect()
            return {
              clientWidth: row.clientWidth,
              scrollWidth: row.scrollWidth,
              violations: buttons.flatMap(button => {
                const box = button.getBoundingClientRect()
                if (box.left >= rowBox.left - 1 && box.right <= rowBox.right + 1) return []
                return [{
                  name: button.getAttribute('aria-label') || button.textContent?.trim() || 'button',
                  left: box.left,
                  right: box.right,
                  rowLeft: rowBox.left,
                  rowRight: rowBox.right,
                }]
              }),
            }
          })
          expect(actionBounds.scrollWidth, '1024px Gallery tools fit without a clipped overflow lane')
            .toBeLessThanOrEqual(actionBounds.clientWidth + 1)
          expect(actionBounds.violations, 'Every 1024px Gallery action stays inside the main pane').toEqual([])

          await viewRow.locator('button[aria-controls="gallery-filter-popover"]').click()
          const filterPopover = page.getByRole('dialog', { name: 'Gallery filters' })
          await expect(filterPopover).toBeVisible()
          const filterBounds = await filterPopover.evaluate(element => {
            const box = element.getBoundingClientRect()
            return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: innerWidth, height: innerHeight }
          })
          expect(filterBounds.left).toBeGreaterThanOrEqual(-1)
          expect(filterBounds.right).toBeLessThanOrEqual(filterBounds.width + 1)
          expect(filterBounds.top).toBeGreaterThanOrEqual(-1)
          expect(filterBounds.bottom).toBeLessThanOrEqual(filterBounds.height + 1)
          await filterPopover.getByRole('button', { name: 'Close Gallery filters' }).click()

          const workspaceTrigger = primaryRow.getByRole('button', { name: /Current project: .*Open project selector/ })
          await workspaceTrigger.click()
          const workspaceDialog = page.getByRole('dialog', { name: 'Workspaces' })
          await expect(workspaceDialog).toBeVisible()
          const workspaceBounds = await workspaceDialog.evaluate(element => {
            const box = element.getBoundingClientRect()
            return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: innerWidth, height: innerHeight }
          })
          expect(workspaceBounds.left).toBeGreaterThanOrEqual(-1)
          expect(workspaceBounds.right).toBeLessThanOrEqual(workspaceBounds.width + 1)
          expect(workspaceBounds.top).toBeGreaterThanOrEqual(-1)
          expect(workspaceBounds.bottom).toBeLessThanOrEqual(workspaceBounds.height + 1)
          await page.locator('body').dispatchEvent('mousedown')
          await expect(workspaceDialog).toHaveCount(0)
        }
      } else {
        await expect(viewRow).toContainText(view === 'Queue' ? 'Queue' : 'LLM Chat')
        if (view === 'Chat') {
          const chatShell = page.locator('[data-chat-shell]')
          await expect(chatShell).toBeVisible()
          const chatBounds = await chatShell.evaluate(element => {
            const shell = element.getBoundingClientRect()
            const panel = element.closest<HTMLElement>('[role="tabpanel"]')!.getBoundingClientRect()
            return {
              shellTop: shell.top,
              shellBottom: shell.bottom,
              shellHeight: shell.height,
              panelTop: panel.top,
              panelBottom: panel.bottom,
            }
          })
          expect(chatBounds.shellTop).toBeGreaterThanOrEqual(chatBounds.panelTop - 1)
          expect(chatBounds.shellBottom).toBeLessThanOrEqual(chatBounds.panelBottom + 1)
          expect(chatBounds.shellHeight).toBeGreaterThan(0)
        }
      }

      const geometry = await page.evaluate(currentView => {
        const toolbarElement = document.querySelector<HTMLElement>('[data-main-toolbar]')!
        const primaryElement = document.querySelector<HTMLElement>('[data-main-toolbar-primary]')!
        const viewElement = document.querySelector<HTMLElement>('[data-main-toolbar-view]')!
        const panelElement = document.querySelector<HTMLElement>(
          `[role="tabpanel"][aria-labelledby="main-${currentView.toLowerCase()}-tab"]`,
        )!
        const toolbarBox = toolbarElement.getBoundingClientRect()
        const primaryBox = primaryElement.getBoundingClientRect()
        const viewBox = viewElement.getBoundingClientRect()
        const panelBox = panelElement.getBoundingClientRect()
        return {
          toolbarHeight: toolbarBox.height,
          panelTop: panelBox.top,
          primaryTop: primaryBox.top,
          primaryBottom: primaryBox.bottom,
          viewTop: viewBox.top,
          viewBottom: viewBox.bottom,
          toolbarBottom: toolbarBox.bottom,
        }
      }, view)
      viewGeometry.push({ view, ...geometry })
    }

    const baseline = viewGeometry[0]
    for (const geometry of viewGeometry) {
      expect(Math.abs(geometry.toolbarHeight - baseline.toolbarHeight), `${viewport.name} ${geometry.view} toolbar height`).toBeLessThanOrEqual(1)
      expect(Math.abs(geometry.panelTop - baseline.panelTop), `${viewport.name} ${geometry.view} panel top`).toBeLessThanOrEqual(1)
      expect(Math.abs(geometry.panelTop - geometry.toolbarBottom), `${viewport.name} ${geometry.view} panel follows toolbar`).toBeLessThanOrEqual(1)
      expect(geometry.primaryTop, `${viewport.name} ${geometry.view} primary row begins first`).toBeLessThanOrEqual(geometry.viewTop)
      expect(geometry.primaryBottom, `${viewport.name} ${geometry.view} rows do not overlap`).toBeLessThanOrEqual(geometry.viewTop + 1)
      expect(geometry.viewBottom, `${viewport.name} ${geometry.view} view row stays inside toolbar`).toBeLessThanOrEqual(geometry.toolbarBottom + 1)
    }
    await expectNoHorizontalOverflow(page)
  }

  await page.setViewportSize({ width: 768, height: 1024 })
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  const chatShell = page.locator('[data-chat-shell]')
  await expect(chatShell).toBeVisible()
  await page.setViewportSize({ width: 767, height: 1024 })
  await expect(chatShell).toBeVisible()
  const resizedChatBounds = await chatShell.evaluate(element => {
    const shell = element.getBoundingClientRect()
    const panel = element.closest<HTMLElement>('[role="tabpanel"]')!.getBoundingClientRect()
    return { shellTop: shell.top, shellBottom: shell.bottom, panelTop: panel.top, panelBottom: panel.bottom }
  })
  expect(resizedChatBounds.shellTop).toBeGreaterThanOrEqual(resizedChatBounds.panelTop - 1)
  expect(resizedChatBounds.shellBottom).toBeLessThanOrEqual(resizedChatBounds.panelBottom + 1)

  await page.setViewportSize({ width: 768, height: 1024 })
  await page.getByRole('tab', { name: 'Gallery', exact: true }).click()
  await viewRow.getByRole('button', { name: 'Search Gallery' }).click()
  const searchInput = viewRow.getByRole('textbox', { name: 'Search Gallery' })
  await searchInput.fill('focus survives resize')
  await expect(searchInput).toBeFocused()
  await page.setViewportSize({ width: 767, height: 1024 })
  await expect(searchInput).toBeFocused()
  await expect(searchInput).toHaveValue('focus survives resize')
  await viewRow.getByRole('button', { name: 'Clear search text and close search' }).click()
  await expect(viewRow.getByRole('button', { name: 'Search Gallery' })).toBeFocused()
})

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

  const menuButton = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
  await expectMinimumTarget(menuButton)
  await menuButton.click()
  await expect(page.getByRole('dialog', { name: 'Generate, Director, and References menu' })).toBeVisible()
  const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
  await expect(menu).toHaveAccessibleName('Generate, Director, and References menu')
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
  await expect(page.getByRole('dialog', { name: 'Generate, Director, and References menu' })).toHaveCount(0)
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

    const resizedMenuButton = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
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

    const menuButton = page.getByRole('button', { name: 'Open Generate, Director, and References menu' })
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
      await page.getByRole('button', { name: 'Open Generate, Director, and References menu' }).click()
    }
    await menu.getByRole('button', { name: 'Open Director', exact: true }).click()
    await menu.getByRole('button', { name: 'Open Director production dashboard' }).click()
    const director = page.getByRole('dialog', { name: 'Director Dashboard' })
    await expect(director).toBeVisible()
    await expect(director.getByRole('combobox', { name: 'Select Director pipeline' })).toHaveValue('')
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

    await page.getByRole('button', { name: 'Open Generate, Director, and References menu' }).click()
    const menu = page.locator('#maestro-mobile-sidebar[role="dialog"]')
    await menu.getByRole('button', { name: 'Open Generate', exact: true }).click()
    const generationModes = menu.getByRole('group', { name: 'Generation mode' })
    await generationModes.getByRole('button', { name: 'Video', exact: true }).click()
    const termsLink = menu.getByRole('link', { name: 'Review model terms', exact: true })
    await expect(termsLink).toBeVisible()
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

test('stale remote sessions and locked boot calls recover without a blank root or early queue polling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await skipWelcome(page)
  api!.setAccountScenario('remote-user')
  await gotoSyntheticApp(page)

  await expect(page.getByRole('button', { name: /Current project: Synthetic project/ })).toBeVisible()
  await page.getByRole('tab', { name: /^Queue/ }).click()
  await expect(page.getByText(/1 waiting/).first()).toBeVisible()
  await expect.poll(() => api!.requestCount('/api/v1/queue')).toBeGreaterThan(0)
  await expect.poll(() => api!.requestCount('/api/v1/jobs')).toBeGreaterThan(0)
  await expect.poll(() => api!.requestCount('/api/v1/h3/estimate')).toBeGreaterThan(0)
  await expect.poll(() => api!.requestCount('/api/v1/loras/check-updates')).toBeGreaterThan(0)

  api!.setBootFailures({ queue: 423 })
  await page.evaluate(() => window.dispatchEvent(new Event('maestro:queue-refresh')))
  await expect(page.getByRole('heading', { name: 'Choose a project to continue' })).toBeVisible()
  await expect(page.locator('#root')).not.toBeEmpty()
  await expect(page.getByText(/1 waiting/)).toHaveCount(0)
  const protectedRequestsAfterLock = {
    queue: api!.requestCount('/api/v1/queue'),
    jobs: api!.requestCount('/api/v1/jobs'),
    estimate: api!.requestCount('/api/v1/h3/estimate'),
    loras: api!.requestCount('/api/v1/loras/check-updates'),
  }

  api!.setBootFailures({ account: 403 })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Sign in to continue' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible()
  await expect(page.locator('#root')).not.toBeEmpty()
  await expect.poll(() => ({
    queue: api!.requestCount('/api/v1/queue'),
    jobs: api!.requestCount('/api/v1/jobs'),
    estimate: api!.requestCount('/api/v1/h3/estimate'),
    loras: api!.requestCount('/api/v1/loras/check-updates'),
  })).toEqual(protectedRequestsAfterLock)

  api!.setBootFailures({ project: 403 })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Choose a project to continue' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open sign-in' })).toBeVisible()
  await expect(page.locator('#root')).not.toBeEmpty()
  expect(api!.requestCount('/api/v1/account/context')).toBeGreaterThan(0)
  expect(api!.requestCount('/api/v1/workspaces')).toBeGreaterThan(0)
  await expect.poll(() => ({
    queue: api!.requestCount('/api/v1/queue'),
    jobs: api!.requestCount('/api/v1/jobs'),
    estimate: api!.requestCount('/api/v1/h3/estimate'),
    loras: api!.requestCount('/api/v1/loras/check-updates'),
  })).toEqual(protectedRequestsAfterLock)
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

test('accounts-disabled compatibility, local bootstrap, and remote bootstrap boundaries stay explicit', async ({ page, browserName }) => {
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
  await expect(bootstrap).toContainText(
    'For security, create the first owner account by opening Maestro directly on the computer where it is running.',
  )
  await bootstrap.getByLabel('Username', { exact: true }).fill('Synthetic Owner')
  await bootstrap.getByLabel('Password', { exact: true }).fill('synthetic-bootstrap-password')
  await bootstrap.getByLabel('Device label', { exact: true }).fill('Synthetic browser')
  const createOwnerAction = bootstrap.getByRole('button', { name: 'Create owner account' })
  if (browserName !== 'webkit') {
    await expectPrimaryActionContrast(createOwnerAction)
    await expectPrimaryActionContrast(opened.drawer.getByRole('button', { name: 'Sign in', exact: true }))
  }
  await createOwnerAction.click()
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
  await expect(opened.drawer).toContainText(
    'Existing project access may also depend on this browser or a project password.',
  )
})

test('anonymous login, owner reauthentication, account-session sign-out, and logout preserve project authority', async ({ page, browserName }) => {
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
  const signInAction = login.getByRole('button', { name: 'Sign in', exact: true })
  if (browserName !== 'webkit') await expectPrimaryActionContrast(signInAction)
  await signInAction.click()

  await expect(drawer.getByText('Signed in.', { exact: true })).toBeVisible()
  await expect(drawer.getByText('Confirmation needed for sensitive actions')).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Account sessions' })).toBeVisible()
  await expect(drawer.getByText('Synthetic tablet', { exact: false })).toBeVisible()
  await expect(drawer.getByRole('button', { name: 'Sign out other account sessions' })).toBeDisabled()

  const confirmation = drawer.getByRole('heading', { name: 'Confirm your password' }).locator('xpath=..')
  await confirmation.getByLabel('Current password').fill('synthetic-owner-password')
  const confirmPasswordAction = confirmation.getByRole('button', { name: 'Confirm password' })
  if (browserName !== 'webkit') await expectPrimaryActionContrast(confirmPasswordAction)
  await confirmPasswordAction.click()
  await expect(drawer.getByText('Sensitive account actions are temporarily unlocked.')).toBeVisible()
  await expect(drawer.getByText('Recently confirmed')).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Manage users' })).toBeVisible()
  if (browserName !== 'webkit') {
    await expectPrimaryActionContrast(drawer.getByRole('button', { name: 'Create user' }))
  }

  const otherSession = drawer.getByText('Synthetic tablet', { exact: false }).locator('xpath=../..')
  await otherSession.getByRole('button', { name: 'Sign out' }).click()
  await expect(drawer.getByText('That account session was signed out.')).toBeVisible()
  await expect(drawer.getByText('Synthetic tablet', { exact: false })).toHaveCount(0)
  await expect(drawer.getByText('Synthetic browser', { exact: false })).toBeVisible()

  await drawer.getByRole('button', { name: 'Sign out', exact: true }).last().click()
  await expect(drawer.getByText('Signed out. Any separate browser or project-password access remains unchanged.')).toBeVisible()
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
  await expect(drawer.getByRole('heading', { name: 'Account sessions' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Password and recovery' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Manage users' })).toHaveCount(0)
  await expect(drawer.getByRole('button', { name: 'Create user' })).toHaveCount(0)
  await expect(drawer).toContainText(
    'Signing in identifies your account. Access to existing projects may still depend on this browser or a project password.',
  )
})

test('active account project access opens and creates member projects without project passwords', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 })
  await skipWelcome(page)
  api!.setAccountScenario('user')
  await routeMigratedUserAccess(page, 'Synthetic User')
  const projectPermissions = [
    'project.open', 'project.read', 'project.mutate', 'project.generate', 'project.lifecycle', 'project.delete',
  ]
  let activeProject = 'Member project'
  const workspaces = [{
    name: 'Member project',
    password_protected: true,
    unlocked: false,
    project_role: 'owner',
    project_permissions: projectPermissions,
  }, {
    name: 'Second member project',
    password_protected: true,
    unlocked: false,
    project_role: 'owner',
    project_permissions: projectPermissions,
  }]
  const createdBodies: Array<Record<string, unknown>> = []
  const openedProjects: string[] = []

  await page.route('**/api/v1/workspaces', async route => {
    const request = route.request()
    if (request.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ workspaces, active: activeProject }),
      })
      return
    }
    if (request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      createdBodies.push(body)
      const name = String(body.name || '')
      workspaces.push({
        name,
        password_protected: false,
        unlocked: false,
        project_role: 'owner',
        project_permissions: projectPermissions,
      })
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      return
    }
    await route.abort('blockedbyclient')
  })
  await page.route('**/api/v1/workspaces/active', async route => {
    const body = route.request().postDataJSON() as { name?: unknown }
    activeProject = String(body.name || '')
    openedProjects.push(activeProject)
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await gotoSyntheticApp(page)
  const projectTrigger = page.getByRole('button', { name: /Current project: Member project/ })
  await projectTrigger.click()
  const selector = page.getByRole('dialog', { name: 'Projects' })
  await expect(selector).toBeVisible()
  await expect(selector.getByRole('button', { name: /Unlock|Lock/ })).toHaveCount(0)
  await expect(selector.locator('input[type="password"]')).toHaveCount(0)

  await selector.getByRole('button', { name: 'Second member project' }).click()
  await expect(page.getByRole('button', { name: /Current project: Second member project/ })).toBeVisible()
  expect(openedProjects).toContain('Second member project')

  await page.getByRole('button', { name: /Current project: Second member project/ }).click()
  const reopenedSelector = page.getByRole('dialog', { name: 'Projects' })
  await reopenedSelector.getByRole('button', { name: 'New project' }).click()
  await expect(reopenedSelector.locator('input[type="password"]')).toHaveCount(0)
  const nameInput = reopenedSelector.getByPlaceholder('workspace-name')
  const createButton = reopenedSelector.getByRole('button', { name: 'Create project' })
  await expect(createButton).toBeDisabled()
  await nameInput.fill('threadspan-acceptance')
  await expect(nameInput).toHaveValue('threadspan-acceptance')
  await expect(createButton).toBeEnabled()
  await createButton.click()
  await expect(page.getByRole('button', { name: /Current project: threadspan-acceptance/ })).toBeVisible()
  await expect.poll(() => createdBodies.length).toBe(1)
  expect(createdBodies).toEqual([{ name: 'threadspan-acceptance' }])
})

test('a newly signed-up migrated user can create their first passwordless project exactly once', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 })
  await skipWelcome(page)
  api!.setAccountScenario('user')
  await routeMigratedUserAccess(page, 'threadspan-test')

  const projectPermissions = [
    'project.open', 'project.read', 'project.mutate', 'project.generate', 'project.lifecycle', 'project.delete',
  ]
  let activeProject = ''
  const workspaces: Array<Record<string, unknown>> = []
  const createdBodies: Array<Record<string, unknown>> = []
  await page.route('**/api/v1/workspaces', async route => {
    const request = route.request()
    if (request.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ workspaces, active: activeProject }),
      })
      return
    }
    if (request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>
      createdBodies.push(body)
      const name = String(body.name || '')
      workspaces.push({
        name,
        password_protected: false,
        unlocked: false,
        project_role: 'owner',
        project_permissions: projectPermissions,
      })
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      return
    }
    await route.abort('blockedbyclient')
  })
  await page.route('**/api/v1/workspaces/active', async route => {
    const body = route.request().postDataJSON() as { name?: unknown }
    activeProject = String(body.name || '')
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto('/')
  const chooser = page.getByRole('dialog', { name: 'Choose a project to enter Maestro' })
  await expect(chooser).toBeVisible()
  await expect(chooser.locator('input[type="password"]')).toHaveCount(0)
  const nameInput = chooser.getByPlaceholder('workspace-name')
  const createButton = chooser.getByRole('button', { name: 'Create project' })
  await expect(createButton).toBeDisabled()
  await nameInput.fill('threadspan-acceptance')
  await expect(nameInput).toHaveValue('threadspan-acceptance')
  await expect(createButton).toBeEnabled()
  await createButton.click()
  await expect(page.getByRole('button', { name: /Current project: threadspan-acceptance/ })).toBeVisible()
  await expect.poll(() => createdBodies.length).toBe(1)
  expect(createdBodies).toEqual([{ name: 'threadspan-acceptance' }])
})

test('public signup requires saving one-time recovery codes and recovery replaces them', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await skipWelcome(page)
  api!.setAccountScenario('public-anonymous')
  await gotoSyntheticApp(page)

  const trigger = page.locator('[data-responsive-dialog-trigger^="account-support:"]').first()
  await expectMinimumTarget(trigger)
  await trigger.click()
  const drawer = page.locator('#account-support-drawer[role="dialog"]')
  await expect(drawer).toBeVisible()
  await drawer.getByRole('tab', { name: 'Account' }).click()
  await drawer.getByRole('button', { name: 'Create account', exact: true }).first().click()

  const registration = drawer.getByRole('heading', { name: 'Create account' }).locator('xpath=../..')
  await registration.getByLabel('Username', { exact: true }).fill('Synthetic New User')
  await registration.getByLabel('Device label', { exact: true }).fill('Synthetic phone')
  await registration.getByLabel('Password', { exact: true }).fill('synthetic-new-password')
  await registration.getByLabel('Confirm password', { exact: true }).fill('synthetic-new-password')
  const registrationSubmit = registration.getByRole('button', { name: 'Create account', exact: true })
  await expect(registrationSubmit).toBeEnabled()
  expect(await registration.evaluate(form => (form as HTMLFormElement).checkValidity())).toBe(true)
  const nonceResponse = page.waitForResponse(response => (
    new URL(response.url()).pathname === '/api/v1/account/nonce'
  ))
  await registrationSubmit.click()
  const issuedNonce = await nonceResponse
  expect(issuedNonce.status()).toBe(200)
  await expect(issuedNonce.json()).resolves.toMatchObject({ purpose: 'register' })
  await expect.poll(() => ({
    nonce: api!.requestCount('/api/v1/account/nonce'),
    register: api!.requestCount('/api/v1/account/register'),
  })).toEqual({ nonce: 1, register: 1 })

  const codes = drawer.getByRole('heading', { name: 'Your recovery codes' }).locator('xpath=../..')
  await expect(codes).toBeVisible()
  await expect(codes.getByText('synthetic-recovery-code-one', { exact: true })).toBeVisible()
  const continueWithCodes = codes.getByRole('button', { name: 'Continue with saved codes' })
  await expect(continueWithCodes).toBeDisabled()
  await expectMinimumTarget(codes.getByText('I stored these recovery codes somewhere private.').locator('xpath=..'))
  await codes.getByLabel('I stored these recovery codes somewhere private.').check()
  await expect(continueWithCodes).toBeEnabled()
  await expectNoHorizontalOverflow(page)
  await expectNoBlockingAxeFindings(page)
  await page.screenshot({ path: testInfo.outputPath('mobile-public-signup-recovery-codes.png'), fullPage: true })
  await continueWithCodes.click()
  await expect(drawer.getByText('Synthetic New User', { exact: true }).first()).toBeVisible()

  api!.setAccountScenario('public-anonymous')
  await page.reload()
  const reopenedTrigger = page.locator('[data-responsive-dialog-trigger^="account-support:"]').first()
  await reopenedTrigger.click()
  const reopened = page.locator('#account-support-drawer[role="dialog"]')
  await reopened.getByRole('tab', { name: 'Account' }).click()
  await reopened.getByRole('button', { name: 'Recover', exact: true }).click()
  const recovery = reopened.getByRole('button', { name: 'Recover and sign in' }).locator('xpath=..')
  await recovery.getByLabel('Username', { exact: true }).fill('Synthetic New User')
  await recovery.getByLabel('Device label', { exact: true }).fill('Synthetic replacement phone')
  await recovery.getByLabel('Recovery code', { exact: true }).fill('synthetic-recovery-code-one')
  await recovery.getByLabel('New password', { exact: true }).fill('synthetic-recovered-password')
  await recovery.getByLabel('Confirm new password', { exact: true }).fill('synthetic-recovered-password')
  await recovery.getByRole('button', { name: 'Recover and sign in' }).click()

  const replacementCodes = reopened.getByRole('heading', { name: 'Replacement recovery codes' }).locator('xpath=../..')
  await expect(replacementCodes.getByText('synthetic-replacement-code-one', { exact: true })).toBeVisible()
  const replacementContinue = replacementCodes.getByRole('button', { name: 'Continue with saved codes' })
  await expect(replacementContinue).toBeDisabled()
  await replacementCodes.getByLabel('I stored these recovery codes somewhere private.').check()
  await replacementContinue.click()
  await expect(reopened.getByText('Synthetic New User', { exact: true }).first()).toBeVisible()
})

for (const viewport of PRODUCT_ACCEPTANCE_VIEWPORTS) {
  test(`supporter membership, perks, and live hosted allowance are visible on ${viewport.name}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    api!.setAccountScenario('user')
    api!.setSupportScenario('donor')
    await gotoSyntheticApp(page)

    const welcome = page.getByRole('dialog', { name: /Welcome to Maestro Continuum/ })
    await expect(welcome).toBeVisible()
    const membership = welcome.getByRole('region', { name: 'Account membership and supporter status' })
    await expect(membership).toContainText('Member account')
    await expect(membership).toContainText(/studio supporter/i)
    await expect(membership).toContainText(/continuum supporter/i)
    await expect(membership).toContainText('Supporter recognition')
    await expect(membership).toContainText('Bounded hosted queue priority')
    await expectNoHorizontalOverflow(page)
    await expectNoBlockingAxeFindings(page)
    if (viewport.width <= 767) await expectEveryRenderedActionMinimumTarget(welcome)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-supporter-welcome.png`), fullPage: true })

    await welcome.getByRole('button', { name: /Enter the studio/ }).click()
    const trigger = page.locator('[data-responsive-dialog-trigger^="account-support:"]').first()
    if (viewport.width <= 767) await expectMinimumTarget(trigger)
    await trigger.click()
    const drawer = page.locator('#account-support-drawer[role="dialog"]')
    await drawer.getByRole('tab', { name: 'Support' }).click()
    await expect(drawer.getByRole('heading', { name: 'Supporter tiers and perks' })).toBeVisible()
    const recordedSupport = drawer.getByRole('heading', { name: 'Support is recorded for this account' })
    await expect(recordedSupport).toBeVisible()
    await expect(drawer.getByLabel('Supporter status and benefits')).toContainText(/studio supporter/i)
    await expect(drawer.getByLabel('Supporter status and benefits')).toContainText('Early access updates')
    const activeAllowance = drawer.getByLabel('Active hosted queue allowance')
    await expect(activeAllowance).toContainText('Available amount: 2,400 maestro credits')
    await expect(drawer).toContainText('Maestro neither detects nor automatically refunds it.')
    await expectNoHorizontalOverflow(page)
    await expectNoBlockingAxeFindings(page)
    await activeAllowance.scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-supporter-account.png`), fullPage: true })
  })
}

for (const viewport of PRODUCT_ACCEPTANCE_VIEWPORTS) {
test(`${viewport.name} output share creates, copies, opens anonymously, and revokes one read-only link`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height })
  await skipWelcome(page)
  api!.setAccountScenario('remote-user')
  api!.setOutputScenario('shareable')
  await page.addInitScript(() => {
    const target = globalThis as typeof globalThis & { __maestroCopiedShare?: string }
    Object.defineProperty(navigator, 'share', { configurable: true, value: undefined })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (value: string) => { target.__maestroCopiedShare = value } },
    })
  })
  await gotoSyntheticApp(page)

  await page.getByRole('tab', { name: 'Gallery' }).click()
  const output = page.getByRole('group', { name: /synthetic-share\.png\. Press Enter or Space to select/ })
  await expect(output).toBeVisible()
  const share = output.getByRole('button', { name: /Share synthetic-share\.png — create an output-only link/ })
  const revoke = output.getByRole('button', { name: /Revoke any active output-only link for synthetic-share\.png/ })
  if (viewport.width <= 767) {
    await expectMinimumTarget(share)
    await expectMinimumTarget(revoke)
  }
  await share.click()
  await expect(output.getByRole('status')).toContainText('Local-network output link copied.')
  const copied = await page.evaluate(() => (
    globalThis as typeof globalThis & { __maestroCopiedShare?: string }
  ).__maestroCopiedShare || '')
  expect(copied).toBe(`${E2E_ORIGIN}/share/synthetic-output-share-token`)
  expect(api!.requestCount('/api/v1/output-shares')).toBe(1)
  await expectNoHorizontalOverflow(page)
  await expectNoBlockingAxeFindings(page)
  await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-output-share-active.png`), fullPage: true })

  const sharedPage = await page.context().newPage()
  const activeResponse = await sharedPage.goto(copied)
  expect(activeResponse?.status()).toBe(200)
  await expect(sharedPage.getByRole('heading', { name: 'Shared Maestro output' })).toBeVisible()
  await expect(sharedPage.getByRole('img', { name: 'Shared Maestro output' })).toBeVisible()
  await expect(sharedPage.getByText('synthetic-share.png', { exact: true })).toBeVisible()
  await sharedPage.close()

  page.once('dialog', dialog => dialog.accept())
  await revoke.click()
  await expect(output.getByRole('status')).toContainText('Output link revoked.')
  expect(api!.requestCount('/api/v1/output-shares')).toBe(2)
  const revokedPage = await page.context().newPage()
  const revokedResponse = await revokedPage.goto(copied)
  expect(revokedResponse?.status()).toBe(404)
  await revokedPage.close()
})
}

for (const viewport of PRODUCT_ACCEPTANCE_VIEWPORTS) {
  test(`${viewport.name} project sharing manages exact account membership without creating a bearer project link`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    await skipWelcome(page)
    api!.setAccountScenario('remote-user')
    await page.addInitScript(() => {
      const target = globalThis as typeof globalThis & { __maestroCopiedStudio?: string }
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText: async (value: string) => { target.__maestroCopiedStudio = value } },
      })
    })
    await gotoSyntheticApp(page)

    const trigger = page.locator('[data-project-share-trigger]')
    await expect(trigger).toBeVisible()
    if (viewport.width <= 767) await expectMinimumTarget(trigger)
    await trigger.click()
    const panel = page.locator('[data-project-access-panel]')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('No email invite or public project link is created.')
    await expect(panel.getByText('Synthetic User', { exact: true })).toBeVisible()

    const copyStudio = panel.locator('[data-copy-studio-link]')
    await copyStudio.click()
    await expect(copyStudio).toContainText('Copied')
    expect(await page.evaluate(() => (
      globalThis as typeof globalThis & { __maestroCopiedStudio?: string }
    ).__maestroCopiedStudio || '')).toBe(`${E2E_ORIGIN}/`)

    const memberForm = panel.locator('[data-project-member-form]')
    await memberForm.locator('[data-project-member-username]').fill('Synthetic Collaborator')
    await memberForm.locator('[data-project-member-role]').selectOption('editor')
    await memberForm.getByRole('button', { name: 'Save access' }).click()
    await expect(panel.getByText('Synthetic Collaborator', { exact: true })).toBeVisible()
    const collaboratorRole = panel.getByLabel('Role for Synthetic Collaborator')
    await expect(collaboratorRole).toHaveValue('editor')
    await collaboratorRole.selectOption('viewer')
    await expect(collaboratorRole).toHaveValue('viewer')

    const remove = panel.getByRole('button', { name: 'Remove Synthetic Collaborator' })
    await remove.click()
    await panel.getByRole('button', { name: 'Confirm removing Synthetic Collaborator' }).click()
    await expect(panel.getByText('Synthetic Collaborator', { exact: true })).toHaveCount(0)
    await expectNoHorizontalOverflow(page)
    await expectNoBlockingAxeFindings(page)
    if (viewport.width <= 767) await expectEveryRenderedActionMinimumTarget(panel)
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-project-sharing.png`), fullPage: true })
  })
}

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
