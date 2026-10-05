import type { OutputFile } from '../../types'

export const H3_BRIDGE_GENERATED_FRAMES = Array.from(
  { length: 15 },
  (_, index) => 107 + index * 17,
)

export function resolveH3BridgeSelection(
  outputs: readonly OutputFile[],
  selectedKeys: readonly string[],
  activeWorkspace: string,
  canGenerate: boolean,
): readonly [OutputFile, OutputFile] | null {
  if (!canGenerate || !activeWorkspace || selectedKeys.length !== 2) return null
  const uniqueKeys = new Set(selectedKeys)
  if (uniqueKeys.size !== 2) return null

  const outputByKey = new Map(outputs.map(output => [
    `${output.workspace}\0${output.name}`,
    output,
  ]))
  const selected = [...uniqueKeys].map(key => outputByKey.get(key))
  if (selected.some(output => !output)) return null
  const [first, second] = selected as [OutputFile, OutputFile]
  if (
    first.type !== 'video'
    || second.type !== 'video'
    || first.workspace !== activeWorkspace
    || second.workspace !== activeWorkspace
    || !first.revision.trim()
    || !second.revision.trim()
  ) return null

  return [first, second]
}
