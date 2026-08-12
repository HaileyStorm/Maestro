import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { build } from 'esbuild'

const source = relative => readFile(new URL(relative, import.meta.url), 'utf8')

function asDataModule(contents) {
  return `data:text/javascript;base64,${Buffer.from(contents).toString('base64')}`
}

function elements(value, type, result = []) {
  if (Array.isArray(value)) {
    for (const child of value) elements(child, type, result)
    return result
  }
  if (!value || typeof value !== 'object') return result
  if (value.type === type) result.push(value)
  elements(value.props?.children, type, result)
  return result
}

const [{ AspectRatioGrid }, css] = await Promise.all([
  build({
    stdin: {
      contents: "export { AspectRatioGrid } from './src/components/Sidebar/AspectRatioGrid.tsx'",
      resolveDir: new URL('..', import.meta.url).pathname,
      loader: 'js',
    },
    bundle: true,
    format: 'esm',
    jsx: 'automatic',
    logLevel: 'silent',
    platform: 'node',
    write: false,
    plugins: [{
      name: 'aspect-ratio-grid-render-runtime',
      setup(bundle) {
        bundle.onResolve({ filter: /^react\/jsx-runtime$/ }, () => ({ path: 'jsx-runtime', namespace: 'aspect-grid' }))
        bundle.onResolve({ filter: /stores\/useStore$/ }, () => ({ path: 'store', namespace: 'aspect-grid' }))
        bundle.onLoad({ filter: /.*/, namespace: 'aspect-grid' }, args => {
          if (args.path === 'jsx-runtime') return { contents: `
            export const jsx = (type, props, key) => ({ type, key, props: props || {} })
            export const jsxs = jsx
          ` }
          return { contents: 'export const useStore = selector => selector(globalThis.__maestroAspectGridStore)' }
        })
      },
    }],
  }).then(result => import(asDataModule(result.outputFiles[0].text))),
  source('../src/index.css'),
])

test('image aspect buttons keep exact actions and narrow mobile targets', () => {
  const selected = []
  globalThis.__maestroAspectGridStore = {
    aspectRatio: 'auto',
    setAspectRatio: value => selected.push(value),
    generationMode: 'image',
    modelOptions: undefined,
  }

  const buttons = elements(AspectRatioGrid(), 'button')
  const expected = ['auto', '16:9', '9:16', '1:1', '4:3', '3:4']

  assert.equal(buttons.length, 6)
  assert.deepEqual(buttons.map(button => button.props.children[1].props.children), ['Auto', ...expected.slice(1)])
  assert.ok(buttons.every(button => /\bmobile-control-target\b/.test(button.props.className)))
  assert.ok(buttons.every(button => /\bmin-w-0\b/.test(button.props.className)))

  for (const button of buttons) button.props.onClick()
  assert.deepEqual(selected, expected)

  const mobile = css.slice(css.indexOf('@media (max-width: 767px)'))
  assert.match(mobile, /\.mobile-control-target\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/s)
  assert.doesNotMatch(css.slice(0, css.indexOf('@media (max-width: 767px)')), /\.mobile-control-target\s*\{/)
  assert.ok(6 * 44 + 5 * 4 <= 320 - 32, 'six 44px targets plus gaps fit the 320px sidebar content width')
})
