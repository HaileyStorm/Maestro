import type { HostTermId } from '../types'


export const HOST_TERM_NOTICES: Record<HostTermId, {
  version: number
  text: string
  href?: string
  linkLabel?: string
}> = {
  lawful_use: {
    version: 1,
    text: 'Use Maestro only for lawful work you are authorized to create. You are responsible for consent, rights, storage, and distribution. Maestro does not inspect local requests or outputs.',
  },
  minimax_h3_ref2va: {
    version: 1,
    text: 'Confirm you are authorized to use the separately licensed MiniMax H3 Ref2VA weights and accept the applicable model terms for this host. Availability or use may require authorization or a waiver in the US, EU, UK, or South Korea.',
    href: 'https://huggingface.co/MiniMaxAI/MiniMax-H3',
    linkLabel: 'Review model terms',
  },
}
