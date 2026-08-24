import type {
  SupportAccountSummary,
  SupportContributionSource,
  SupportManualContributionKind,
  SupportPriorityPolicy,
  SupportProvider,
  SupportPublicProjection,
  SupporterBenefit,
  ResponsibleUseProjection,
} from '../../types'
import {
  developmentCostRecoveryProjection,
  type DevelopmentCostRecoveryProjection,
} from '../../types/index.ts'

export type AccountSupportTab = 'support' | 'account'

export const supporterBenefitLabels: Record<SupporterBenefit, string> = {
  supporter_recognition: 'Supporter recognition',
  bounded_queue_priority: 'Bounded hosted queue priority',
  early_access_updates: 'Early access updates',
  supporter_convenience: 'Supporter convenience features',
}

function humanizeSupporterTier(value: string): string {
  const words = value.replace(/[_-]+/g, ' ').trim().replace(/\s+/g, ' ')
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : value
}

export function supporterTierLabels(account: SupportAccountSummary | null): string[] {
  if (!account) return []
  const labels = []
  if (account.one_time_tier) labels.push(`One-time ${humanizeSupporterTier(account.one_time_tier)}`)
  if (account.recurring_tier) labels.push(`Recurring ${humanizeSupporterTier(account.recurring_tier)}`)
  return labels
}

function safeSupportUrl(value: string | null): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:'
      && url.username === ''
      && url.password === ''
      && (url.port === '' || url.port === '443')
      && url.search === ''
      && url.hash === ''
      ? value
      : null
  } catch {
    return null
  }
}

export function nextAccountSupportTab(
  current: AccountSupportTab,
  key: string,
): AccountSupportTab | null {
  if (key === 'Home') return 'support'
  if (key === 'End') return 'account'
  if (key === 'ArrowLeft' || key === 'ArrowRight') {
    return current === 'support' ? 'account' : 'support'
  }
  return null
}

export function responsibleUseIsAccepted(
  projection: ResponsibleUseProjection | null,
): boolean {
  if (!projection?.status.accepted) return false
  return projection.status.document_id === projection.notice.document_id
    && projection.status.document_version === projection.notice.version
    && projection.status.content_sha256 === projection.notice.content_sha256
}

export function verifiedDevelopmentCostRecovery(
  projection: { development_cost_recovery?: unknown } | null,
): DevelopmentCostRecoveryProjection | null {
  return developmentCostRecoveryProjection(projection?.development_cost_recovery)
}

export function visibleSupportProviders(
  catalog: SupportPublicProjection | null,
): SupportProvider[] {
  const directComputeUnlocked = verifiedDevelopmentCostRecovery(catalog)?.state === 'recovered'
  return (catalog?.provider_catalog.providers || []).map(provider => {
    const supportUrl = provider.state === 'available'
      && (provider.provider_id !== 'direct_compute_sponsorship' || directComputeUnlocked)
      ? safeSupportUrl(provider.support_url)
      : null
    return {
      ...provider,
      support_url: supportUrl,
    }
  })
}

const ORDINARY_MANUAL_SUPPORT_SOURCES: SupportContributionSource[] = [
  'buy_me_a_coffee',
  'patreon',
]
const ALL_MANUAL_SUPPORT_KINDS: SupportManualContributionKind[] = [
  'one_time_contribution',
  'recurring_started',
  'recurring_renewed',
  'recurring_canceled',
  'refund',
  'chargeback',
]
const DIRECT_COMPUTE_SUPPORT_KINDS: SupportManualContributionKind[] = [
  'one_time_contribution',
  'refund',
  'chargeback',
]

export function allowedManualSupportSources(
  _directComputeUnlocked: boolean,
): SupportContributionSource[] {
  // Sponsorship stays visible before activation; the caller separately gates
  // which lifecycle mutations are available.
  void _directComputeUnlocked
  return [...ORDINARY_MANUAL_SUPPORT_SOURCES, 'direct_compute_sponsorship']
}

export function allowedManualSupportKinds(
  source: SupportContributionSource,
  _directComputeUnlocked = true,
): SupportManualContributionKind[] {
  void _directComputeUnlocked
  return source === 'direct_compute_sponsorship'
    ? [...DIRECT_COMPUTE_SUPPORT_KINDS]
    : [...ALL_MANUAL_SUPPORT_KINDS]
}

export function affectedPriorityNotice(
  account: SupportAccountSummary | null,
  policy: SupportPriorityPolicy | null,
): string | null {
  const hasRecordedSchedulingBenefit = account?.benefits.recorded_eligibility.some(
    benefit => benefit === 'bounded_queue_priority',
  ) === true
  const hasExactExclusion = policy?.exclusions.some(
    exclusion => exclusion.support_priority_eligible === false
      && exclusion.marker === 'creator_terms_exclude_support_priority',
  ) === true
  return hasRecordedSchedulingBenefit && hasExactExclusion ? policy?.notice || null : null
}
