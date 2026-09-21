import assert from 'node:assert/strict'
import test from 'node:test'
import { build } from 'esbuild'

const bundle = build({
  stdin: {
    contents: "export * from './src/components/Sidebar/blendMediaTypes.ts'",
    resolveDir: new URL('..', import.meta.url).pathname,
    loader: 'js',
  },
  bundle: true,
  format: 'esm',
  platform: 'node',
  write: false,
  logLevel: 'silent',
}).then(result => result.outputFiles[0].text)

const contract = import(`data:text/javascript;base64,${Buffer.from(await bundle).toString('base64')}`)

test('Blend accepts exactly the backend-supported image and video extensions', async () => {
  const { BLEND_MEDIA_ACCEPT, isBlendImageFile, isBlendMediaFile, isBlendVideoFile } = await contract

  const accepted = BLEND_MEDIA_ACCEPT.split(',')
  assert.deepEqual(accepted, [
    '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff',
    '.mp4', '.mkv', '.avi', '.mov', '.webm',
  ])

  for (const extension of accepted) {
    const file = { name: `clip${extension.toUpperCase()}` }
    assert.equal(isBlendMediaFile(file), true, extension)
    assert.equal(isBlendVideoFile(file), ['.mp4', '.mkv', '.avi', '.mov', '.webm'].includes(extension), extension)
    assert.equal(isBlendImageFile(file), !isBlendVideoFile(file), extension)
  }
})

test('Blend rejects broad browser media types that its backend cannot decode', async () => {
  const { isBlendMediaFile } = await contract

  for (const name of ['clip.gif', 'clip.avif', 'clip.m4v', 'clip.svg', 'clip', 'clip.mp4.exe']) {
    assert.equal(isBlendMediaFile({ name }), false, name)
  }
})
