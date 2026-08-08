export type LoraDates = { released?: string | null; downloaded?: string | null }

export type LoraPickerSort = 'name' | 'newest'

/** Order picker rows by release date with download date as a fallback. */
export function sortLoraNames(
  names: string[],
  sort: LoraPickerSort,
  dates: Record<string, LoraDates>,
): string[] {
  if (sort !== 'newest') return names
  const dateOf = (name: string) => {
    const iso = dates[name]?.released || dates[name]?.downloaded
    const timestamp = iso ? Date.parse(iso) : NaN
    return Number.isNaN(timestamp) ? 0 : timestamp
  }
  return [...names].sort((a, b) => dateOf(b) - dateOf(a) || a.localeCompare(b))
}
