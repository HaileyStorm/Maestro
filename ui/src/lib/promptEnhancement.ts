/** Whether this workflow accepts durable prompt preparation before generation. */
export function supportsPromptPreparation(
  generationMode: string,
  imageMode: unknown,
  editSubMode: unknown,
  audioOnly: boolean | undefined,
): boolean {
  return !audioOnly
    && !(generationMode === 'video' && imageMode === 4)
    && !(generationMode === 'avatar' && Boolean(editSubMode))
}
