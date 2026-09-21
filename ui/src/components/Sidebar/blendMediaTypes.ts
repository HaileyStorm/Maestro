const BLEND_IMAGE_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff',
])

const BLEND_VIDEO_EXTENSIONS = new Set([
  '.mp4', '.mkv', '.avi', '.mov', '.webm',
])

export const BLEND_MEDIA_ACCEPT = [
  ...BLEND_IMAGE_EXTENSIONS,
  ...BLEND_VIDEO_EXTENSIONS,
].join(',')

function mediaExtension(name: string): string {
  const separator = name.lastIndexOf('.')
  return separator >= 0 ? name.slice(separator).toLowerCase() : ''
}

export function isBlendImageFile(file: Pick<File, 'name'>): boolean {
  return BLEND_IMAGE_EXTENSIONS.has(mediaExtension(file.name))
}

export function isBlendVideoFile(file: Pick<File, 'name'>): boolean {
  return BLEND_VIDEO_EXTENSIONS.has(mediaExtension(file.name))
}

export function isBlendMediaFile(file: Pick<File, 'name'>): boolean {
  return isBlendImageFile(file) || isBlendVideoFile(file)
}
