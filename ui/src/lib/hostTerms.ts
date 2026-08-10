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
  bfl_flux1_self_review: {
    version: 1,
    text: 'Confirm the FLUX.1 dev non-commercial terms apply and remain responsible for license compliance and lawful use. Optional local fidelity QA may evaluate visual quality, artifacts, and consistency only; it is not moderation, does not decide permissibility, and never accepts terms.',
    href: 'https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/3de623f/LICENSE.md',
    linkLabel: 'Review FLUX.1 license',
  },
  bfl_flux2_self_review: {
    version: 1,
    text: 'Confirm the FLUX non-commercial terms apply and remain responsible for license compliance and lawful use. Optional local fidelity QA may evaluate visual quality, artifacts, and consistency only; it is not moderation, does not decide permissibility, and never accepts terms.',
    href: 'https://huggingface.co/black-forest-labs/FLUX.2-dev/blob/0cb56aa/LICENSE.md',
    linkLabel: 'Review FLUX license',
  },
  krea2_self_review: {
    version: 2,
    text: "Confirm the Krea 2 Community License and Acceptable Use Policy apply, and complete the required human review. Legitimate intended or potential broad-capability research, evaluation, and fine-tune development are not automatically circumvention; artifacts explicitly designed to defeat safety filters remain excluded from Maestro's curated routing. Optional local fidelity QA evaluates quality only; it is not moderation and does not decide permissibility.",
    href: 'https://huggingface.co/krea/Krea-2-Turbo/blob/98e0fe1/README.md',
    linkLabel: 'Review Krea 2 terms',
  },
  ponpoke_flux2_klein_4b_self_review: {
    version: 1,
    text: 'Confirm the separately gated Ponpoke FLUX.2 Klein 4B encoder access conditions and FLUX non-commercial v2.1 terms apply, and remain responsible for compliance and lawful use. Optional local fidelity QA evaluates quality only; it is not moderation, does not decide permissibility, and never accepts terms.',
    href: 'https://huggingface.co/ponpoke/flux2-klein-4b-uncensored-text-encoder/blob/633217e588e4c0bc76619052e05d3ce0e057cd83/README.md',
    linkLabel: 'Review encoder terms',
  },
  ponpoke_flux2_klein_9b_self_review: {
    version: 1,
    text: 'Confirm the separately gated Ponpoke FLUX.2 Klein 9B encoder access conditions and FLUX non-commercial v2.1 terms apply, and remain responsible for compliance and lawful use. Optional local fidelity QA evaluates quality only; it is not moderation, does not decide permissibility, and never accepts terms.',
    href: 'https://huggingface.co/ponpoke/flux2-klein-9b-uncensored-text-encoder/blob/fba36e796aac081246708dd30392a401ba44922e/README.md',
    linkLabel: 'Review encoder terms',
  },
  civitai_2382648_2973304_creator_terms: {
    version: 1,
    text: "Confirm iamddtla's PornMaster V4 creator terms: credit is required, derivatives are allowed, and the published commercial scope is RentCivit only. The underlying FLUX base remains non-commercial. You remain responsible for compliance and lawful use. Optional local fidelity QA evaluates quality only; it is not moderation, does not decide permissibility, and never accepts terms.",
    href: 'https://civitai.com/models/2382648?modelVersionId=2973304',
    linkLabel: 'Review creator terms',
  },
  civitai_2731187_3209007_creator_terms: {
    version: 1,
    text: "Confirm catlover1937's exact Moody Krea 2 Mix V7 creator terms: credit is required, derivatives are forbidden, and commercial use is limited to RentCivit. The Krea 2 Community License and Acceptable Use Policy also apply. Evaluation may inform separate Krea-base work, but it does not permit Moody derivatives or derivative tooling. You remain responsible for compliance and lawful use; optional local fidelity QA evaluates quality only, is not moderation, does not decide permissibility, and never accepts terms.",
    href: 'https://civitai.com/models/2731187?modelVersionId=3209007',
    linkLabel: 'Review creator terms',
  },
  civitai_2764429_3211049_creator_terms: {
    version: 1,
    text: "Confirm catlover1937's exact Moody Cutie V4 creator terms: credit is required, derivatives are forbidden, and commercial use is limited to RentCivit. The Krea 2 Community License and Acceptable Use Policy also apply. Evaluation may inform separate Krea-base work, but it does not permit Moody derivatives or derivative tooling. You remain responsible for compliance and lawful use; optional local fidelity QA evaluates quality only, is not moderation, does not decide permissibility, and never accepts terms.",
    href: 'https://civitai.com/models/2764429?modelVersionId=3211049',
    linkLabel: 'Review creator terms',
  },
}
