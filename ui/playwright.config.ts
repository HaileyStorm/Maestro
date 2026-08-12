import { devices, defineConfig } from '@playwright/test'
import { existsSync, mkdirSync, realpathSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, parse, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const externalRoot = process.platform === 'linux' && existsSync('/var/tmp')
  ? '/var/tmp'
  : tmpdir()
const browserPath = process.env.PLAYWRIGHT_BROWSERS_PATH
const runPort = process.env.MAESTRO_E2E_PORT
const runToken = process.env.MAESTRO_E2E_RUN_TOKEN
const uiRoot = resolve(dirname(fileURLToPath(import.meta.url)))
const repositoryRoot = resolve(uiRoot, '..')
const outputDir = resolve(
  process.env.MAESTRO_PLAYWRIGHT_OUTPUT_DIR
    || join(externalRoot, 'maestro-playwright-results'),
)
const viteCacheDir = resolve(
  process.env.MAESTRO_VITE_CACHE_DIR
    || join(externalRoot, 'maestro-vite-cache'),
)

if (!browserPath) {
  throw new Error(
    'PLAYWRIGHT_BROWSERS_PATH is required. Use npm run test:e2e:install and npm run test:e2e so browser binaries stay outside the mounted checkout.',
  )
}
const portPattern = /^(?:[1-9]|[1-9]\d{1,3}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])$/
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
if (!runPort || !portPattern.test(runPort)) {
  throw new Error('MAESTRO_E2E_PORT must be a valid port supplied by the synthetic test runner.')
}
if (!runToken || !uuidPattern.test(runToken)) {
  throw new Error('MAESTRO_E2E_RUN_TOKEN must be supplied by the synthetic test runner.')
}

const baseURL = `http://127.0.0.1:${runPort}`
const healthURL = `${baseURL}/__maestro_e2e_health/${runToken}`

function isWithin(root: string, candidate: string): boolean {
  const child = relative(realpathSync.native(root), realpathSync.native(candidate))
  return child === '' || (
    child !== '..'
    && !child.startsWith(`..${sep}`)
    && !isAbsolute(child)
  )
}

for (const [label, path] of [
  ['browser', resolve(browserPath)],
  ['output', outputDir],
  ['Vite cache', viteCacheDir],
]) {
  mkdirSync(path, { recursive: true, mode: 0o700 })
  const canonicalPath = realpathSync.native(path)
  if (isWithin(repositoryRoot, canonicalPath)) {
    throw new Error(`Playwright ${label} path must be outside the repository: ${canonicalPath}`)
  }
  const sameFilesystem = process.platform === 'win32'
    ? parse(realpathSync.native(repositoryRoot)).root.toLowerCase() === parse(canonicalPath).root.toLowerCase()
    : statSync(realpathSync.native(repositoryRoot)).dev === statSync(canonicalPath).dev
  if (sameFilesystem) {
    throw new Error(`Playwright ${label} path must be on a different mounted filesystem or volume from the repository: ${canonicalPath}`)
  }
}

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  globalSetup: './e2e/globalSetup.ts',
  outputDir,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 7_500 },
  reporter: [['list']],
  forbidOnly: true,
  use: {
    baseURL,
    extraHTTPHeaders: { 'X-Maestro-E2E-Run-Token': runToken },
    acceptDownloads: false,
    serviceWorkers: 'block',
    screenshot: 'off',
    trace: 'off',
    video: 'off',
    locale: 'en-US',
    timezoneId: 'UTC',
  },
  webServer: {
    command: `npm run dev -- --config e2e/vite.config.ts --configLoader runner --host 127.0.0.1 --port ${runPort} --strictPort`,
    url: healthURL,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
  projects: [
    {
      name: 'desktop-firefox',
      use: {
        ...devices['Desktop Firefox'],
        browserName: 'firefox',
      },
    },
    {
      name: 'android-like-chromium',
      use: {
        ...devices['Pixel 7'],
        browserName: 'chromium',
      },
    },
    {
      name: 'ios-like-webkit',
      use: {
        ...devices['iPhone 13'],
        browserName: 'webkit',
      },
    },
  ],
})
