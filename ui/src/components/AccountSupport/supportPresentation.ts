import type {
  SupportAccountSummary,
  SupportPriorityPolicy,
  SupportProvider,
  SupportPublicProjection,
  ResponsibleUseProjection,
} from '../../types'
import {
  developmentCostRecoveryProjection,
  type DevelopmentCostRecoveryProjection,
} from '../../types/index.ts'

export type AccountSupportTab = 'support' | 'account'

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

export function affectedPriorityNotice(
  account: SupportAccountSummary | null,
  policy: SupportPriorityPolicy | null,
): string | null {
  const hasRecordedSchedulingBenefit = account?.benefits.recorded_eligibility.some(
    benefit => benefit === 'one_time_credit_eligibility'
      || benefit === 'periodic_credit_eligibility',
  ) === true
  const hasExactExclusion = policy?.exclusions.some(
    exclusion => exclusion.support_priority_eligible === false
      && exclusion.marker === 'creator_terms_exclude_support_priority',
  ) === true
  return hasRecordedSchedulingBenefit && hasExactExclusion ? policy?.notice || null : null
}
