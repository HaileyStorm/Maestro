import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import test from 'node:test'

const cssUrl = new URL('../src/index.css', import.meta.url)
const sourceRootUrl = new URL('../src/', import.meta.url)
const css = await readFile(cssUrl, 'utf8')

const themes = [
  ['default', '@theme'],
  ['golden-hour', '[data-theme="golden-hour"]'],
  ['ivory', '[data-theme="ivory"]'],
  ['daylight', '[data-theme="daylight"]'],
  ['pearl', '[data-theme="pearl"]'],
  ['onyx', '[data-theme="onyx"]'],
]

const surfaces = [
  ['cta', '--color-gradient-cta-from', '--color-gradient-cta-to', '--color-cta-foreground'],
  ['toggle-active', '--color-gradient-toggle-from', '--color-gradient-toggle-to', '--color-toggle-active-foreground'],
]

function cssBlock(selector) {
  const start = css.indexOf(`${selector} {`)
  assert.notEqual(start, -1, `found ${selector} theme block`)
  const bodyStart = start + selector.length + 2
  const end = css.indexOf('\n}', bodyStart)
  assert.notEqual(end, -1, `found end of ${selector} theme block`)
  return css.slice(bodyStart, end)
}

function tokenValue(block, token) {
  const match = block.match(new RegExp(`${token}:\\s*(#[0-9a-fA-F]{6});`))
  assert.ok(match, `found concrete ${token}`)
  return match[1]
}

function relativeLuminance(hex) {
  const channels = hex.slice(1).match(/.{2}/g).map(channel => {
    const srgb = Number.parseInt(channel, 16) / 255
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrastRatio(first, second) {
  const values = [relativeLuminance(first), relativeLuminance(second)].sort((a, b) => b - a)
  return (values[0] + 0.05) / (values[1] + 0.05)
}

async function sourceFiles(directoryUrl) {
  const entries = await readdir(directoryUrl, { withFileTypes: true })
  const nested = await Promise.all(entries.map(entry => {
    const entryUrl = new URL(`${entry.name}${entry.isDirectory() ? '/' : ''}`, directoryUrl)
    if (entry.isDirectory()) return sourceFiles(entryUrl)
    return /\.(?:ts|tsx)$/.test(entry.name) ? [entryUrl] : []
  }))
  return nested.flat()
}

test('CTA and active-toggle foreground tokens pass WCAG AA at every theme gradient stop', () => {
  for (const [theme, selector] of themes) {
    const block = cssBlock(selector)
    for (const [surface, fromToken, toToken, foregroundToken] of surfaces) {
      const foreground = tokenValue(block, foregroundToken)
      for (const stopToken of [fromToken, toToken]) {
        const stop = tokenValue(block, stopToken)
        const ratio = contrastRatio(foreground, stop)
        assert.ok(
          ratio >= 4.5,
          `${theme} ${surface} ${foreground} on ${stopToken} ${stop} is ${ratio.toFixed(3)}:1`,
        )
      }
    }
  }
})

test('every production token-gradient call site uses its semantic foreground class', async () => {
  const files = await sourceFiles(sourceRootUrl)
  const callSites = []

  for (const fileUrl of files) {
    const source = await readFile(fileUrl, 'utf8')
    const relativePath = path.relative(sourceRootUrl.pathname, fileUrl.pathname)
    const tokenGradientLiteral = /(['"`])([^'"`]*(?:\bbg-(?:cta|toggle-active)\b|\b(?:from|to)-gradient-(?:cta|toggle)-(?:from|to)\b)[^'"`]*)\1/g
    for (const match of source.matchAll(tokenGradientLiteral)) {
      const classes = match[2]
      const cta = /\bbg-cta\b|\b(?:from|to)-gradient-cta-(?:from|to)\b/.test(classes)
      const toggle = /\bbg-toggle-active\b|\b(?:from|to)-gradient-toggle-(?:from|to)\b/.test(classes)
      assert.notEqual(cta, toggle, `${relativePath} token gradient resolves to exactly one semantic surface`)
      const followingSource = source.slice((match.index || 0) + match[0].length, (match.index || 0) + match[0].length + 250)
      callSites.push({
        relativePath,
        classes,
        surface: cta ? 'cta' : 'toggle-active',
        decorativeImage: /^\s*>\s*<img\s+aria-hidden="true"/.test(followingSource),
      })
    }
  }

  assert.equal(callSites.length, 9, 'expected all nine production token-gradient call sites')
  const decorativeImageSites = callSites.filter(site => site.decorativeImage)
  assert.deepEqual(
    decorativeImageSites.map(site => site.relativePath),
    ['components/WelcomeModal.tsx'],
    'the only token-gradient surface without text is the decorative welcome brand image',
  )
  for (const { relativePath, classes, surface, decorativeImage } of callSites) {
    if (decorativeImage) {
      assert.doesNotMatch(classes, /\btext-white\b/, `${relativePath} decorative gradient has no hardcoded text foreground`)
      continue
    }
    const foreground = surface === 'cta' ? 'text-cta-foreground' : 'text-toggle-active-foreground'
    assert.match(classes, new RegExp(`\\b${foreground}\\b`), `${relativePath} ${surface} uses ${foreground}`)
    assert.doesNotMatch(classes, /\btext-white\b/, `${relativePath} ${surface} has no hardcoded white foreground`)

    if (surface === 'cta') {
      assert.match(classes, /\bhover:ring-2\b/, `${relativePath} CTA retains color-neutral hover feedback`)
      assert.match(classes, /\bhover:ring-accent-blue\/40\b/, `${relativePath} CTA hover ring remains theme-aware`)
      assert.doesNotMatch(classes, /\bhover:(?:brightness|opacity)-/, `${relativePath} CTA hover cannot reduce foreground contrast`)
    }
  }
})
