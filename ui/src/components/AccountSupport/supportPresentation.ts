import type {
  SupportAccountSummary,
  SupportPriorityPolicy,
  SupportProvider,
  SupportPublicProjection,
  ResponsibleUseProjection,
} from '../../types'

export type AccountSupportTab = 'support' | 'account'

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

export function availableSupportProviders(
  catalog: SupportPublicProjection | null,
): Array<SupportProvider & { support_url: string }> {
  return (catalog?.provider_catalog.providers || []).filter(
    (provider): provider is SupportProvider & { support_url: string } => {
      if (provider.state !== 'available' || typeof provider.support_url !== 'string') return false
      try {
        const url = new URL(provider.support_url)
        return url.protocol === 'https:'
          && url.username === ''
          && url.password === ''
          && (url.port === '' || url.port === '443')
          && url.search === ''
          && url.hash === ''
      } catch {
        return false
      }
    },
  )
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
