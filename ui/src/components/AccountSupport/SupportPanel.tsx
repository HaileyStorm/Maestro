import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ExternalLink, HeartHandshake, Loader2, ShieldCheck } from 'lucide-react'
import { AccountApiError } from '../../api/client'
import { useStore } from '../../stores/useStore'
import type { SupportAccountSummary, SupportAdminAudit, SupportAdminEventKind } from '../../types'
import {
  affectedPriorityNotice,
  availableSupportProviders,
  responsibleUseIsAccepted,
} from './supportPresentation'

function supportErrorMessage(error: unknown): string {
  if (error instanceof AccountApiError && error.retryAfter > 0) {
    return `${error.message} Try again in about ${error.retryAfter} seconds.`
  }
  return error instanceof Error ? error.message : 'Support details could not be refreshed.'
}

function adminSupportErrorMessage(error: unknown): string {
  if (error instanceof AccountApiError && error.retryAfter > 0) {
    return `Private support audit could not be refreshed. Try again in about ${error.retryAfter} seconds.`
  }
  return 'Private support audit could not be refreshed. Confirm recent owner access and try again.'
}

const allowanceSourceLabels = {
  free: 'Free allowance',
  one_time_support: 'One-time support',
  recurring_support: 'Recurring support',
} as const

const allowanceStateText = {
  active: 'Active',
  inactive: 'Inactive',
  refunded: 'Refunded',
  expired: 'Expired',
  capped: 'Capped',
  canceled: 'Canceled',
} as const

const allowanceRefundLabels = {
  partial: 'Partial refund recorded',
  full: 'Full refund recorded',
  excess: 'Refund exceeds the recorded source',
} as const

const PRIVATE_SUPPORT_AUDIT_DISPLAY_TTL_MS = 4 * 60 * 1000

function allowanceUnits(value: number, unit: string): string {
  const label = unit === 'compute_seconds'
    ? `compute second${value === 1 ? '' : 's'}`
    : unit.replaceAll('_', ' ')
  return `${value.toLocaleString()} ${label}`
}

function allowanceDate(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  })
}

const auditEventLabels: Record<SupportAdminEventKind, string> = {
  one_time_contribution: 'One-time contribution',
  recurring_started: 'Recurring support started',
  recurring_renewed: 'Recurring support renewed',
  refund: 'Refund',
  chargeback: 'Chargeback',
  recurring_canceled: 'Recurring support canceled',
  fulfillment_set: 'Fulfillment updated',
  account_link_verified: 'Account link verified',
  account_link_revoked: 'Account link revoked',
}

const discrepancyLabels = {
  unresolved_or_mismatched_adjustment: 'Adjustment does not match a recorded contribution',
  adjustments_exceed_contribution: 'Recorded adjustments exceed the contribution',
} as const

function auditLabel(value: string): string {
  return value.replaceAll('_', ' ')
}

function minorUnits(value: number, currency: string): string {
  return `${value.toLocaleString()} ${currency} minor unit${value === 1 ? '' : 's'}`
}

function AdminSupportAudit({ audit }: { audit: SupportAdminAudit }) {
  const totals = Object.entries(audit.currency_totals_minor)
  const visibleEvents = audit.events.slice(-40).reverse()
  const hiddenEventCount = audit.events.length - visibleEvents.length
  const visibleDiscrepancies = audit.discrepancies.slice(0, 20)
  const hiddenDiscrepancyCount = audit.discrepancies.length - visibleDiscrepancies.length
  const visibleFulfillment = audit.fulfillment.slice(0, 20)
  const hiddenFulfillmentCount = audit.fulfillment.length - visibleFulfillment.length
  return (
    <section aria-labelledby="private-support-audit-heading" className="rounded-xl border border-border bg-bg-primary/30 p-3">
      <h4 id="private-support-audit-heading" className="text-[11px] font-semibold text-text-primary">
        Private contribution and fulfillment audit
      </h4>
      <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
        Read-only records shown after recent owner confirmation. State is recorded_not_enforced: this view does not process payments, activate providers, or enforce benefits.
      </p>
      {audit.incomplete && (
        <p className="mt-2 rounded-md border border-indicator-warning/40 bg-indicator-warning/5 px-2 py-1 text-[9px] leading-relaxed text-text-secondary" role="status">
          Some audit data was unavailable or invalid and was not displayed. Empty sections below are not proof that no records exist.
        </p>
      )}

      <section aria-labelledby="audit-totals-heading" className="mt-3">
        <h5 id="audit-totals-heading" className="text-[10px] font-semibold text-text-secondary">Recorded net totals</h5>
        {totals.length > 0 ? (
          <ul className="mt-1 flex flex-wrap gap-2" aria-label="Recorded currency totals">
            {totals.map(([currency, amount]) => (
              <li key={currency} className="rounded-md border border-border/70 bg-bg-tertiary/30 px-2 py-1 text-[9px] text-text-secondary">
                {minorUnits(amount, currency)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Recorded total data is incomplete.' : 'No net contribution total is recorded.'}
          </p>
        )}
      </section>

      <section aria-labelledby="audit-events-heading" className="mt-3">
        <h5 id="audit-events-heading" className="text-[10px] font-semibold text-text-secondary">Source events</h5>
        {visibleEvents.length > 0 ? (
          <ul className="mt-2 space-y-2" aria-label="Private contribution source events">
            {visibleEvents.map(event => (
              <li key={event.event_id} className="min-w-0 rounded-md border border-border/70 bg-bg-tertiary/20 p-2">
                <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                  <span className="break-words text-[10px] font-semibold text-text-primary">{auditEventLabels[event.kind]}</span>
                  <span className="text-[9px] text-text-muted">{auditLabel(event.provider)}</span>
                </div>
                {event.amount_minor > 0 && (
                  <p className="mt-1 text-[9px] font-medium text-text-secondary">{minorUnits(event.amount_minor, event.currency)}</p>
                )}
                <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
                  Occurred <time dateTime={event.occurred_at}>{allowanceDate(event.occurred_at)} UTC</time>
                  {' · '}received <time dateTime={event.received_at}>{allowanceDate(event.received_at)} UTC</time>
                  {' · '}sequence {event.sequence.toLocaleString()}
                </p>
                <details className="mt-1 text-[9px] text-text-muted">
                  <summary className="cursor-pointer select-none">Opaque reconciliation references</summary>
                  <div className="mt-1 space-y-1 break-all font-mono">
                    <p>Event: {event.event_id}</p>
                    <p>Source: {event.source_reference}</p>
                    {event.contract_reference && <p>Contract: {event.contract_reference}</p>}
                    {event.related_reference && <p>Related: {event.related_reference}</p>}
                    {event.actor_reference && <p>Actor: {event.actor_reference}</p>}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Contribution event data is incomplete.' : 'No contribution audit events are recorded for this account.'}
          </p>
        )}
        {hiddenEventCount > 0 && (
          <p className="mt-2 text-[9px] text-text-muted">
            Showing the 40 newest recorded events; {hiddenEventCount.toLocaleString()} older {hiddenEventCount === 1 ? 'event is' : 'events are'} hidden.
          </p>
        )}
      </section>

      <section aria-labelledby="audit-discrepancies-heading" className="mt-3">
        <h5 id="audit-discrepancies-heading" className="text-[10px] font-semibold text-text-secondary">Discrepancies and follow-up</h5>
        {visibleDiscrepancies.length > 0 ? (
          <ul className="mt-2 space-y-1" aria-label="Recorded support discrepancies">
            {visibleDiscrepancies.map(row => (
              <li key={`${row.event_id}-${row.reason}`} className="rounded-md border border-indicator-warning/40 bg-indicator-warning/5 p-2 text-[9px] leading-relaxed text-text-secondary">
                {discrepancyLabels[row.reason]}. <span className="break-all font-mono text-text-muted">{row.event_id}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Discrepancy data is incomplete.' : 'No recorded discrepancies need follow-up.'}
          </p>
        )}
        {hiddenDiscrepancyCount > 0 && (
          <p className="mt-1 text-[9px] text-text-muted">{hiddenDiscrepancyCount.toLocaleString()} additional discrepancy rows are hidden.</p>
        )}
      </section>

      <section aria-labelledby="audit-fulfillment-heading" className="mt-3">
        <h5 id="audit-fulfillment-heading" className="text-[10px] font-semibold text-text-secondary">Fulfillment</h5>
        {visibleFulfillment.length > 0 ? (
          <ul className="mt-2 space-y-1" aria-label="Recorded support fulfillment">
            {visibleFulfillment.map(row => (
              <li key={row.audit_event_id} className="rounded-md border border-border/70 bg-bg-tertiary/20 p-2 text-[9px] leading-relaxed text-text-secondary">
                <span className="font-semibold text-text-primary">{auditLabel(row.item)}</span>
                {' · '}{auditLabel(row.status)}
                {' · '}<time dateTime={row.changed_at}>{allowanceDate(row.changed_at)} UTC</time>
                <details className="mt-1 text-text-muted">
                  <summary className="cursor-pointer select-none">Opaque fulfillment references</summary>
                  <div className="mt-1 space-y-1 break-all font-mono">
                    <p>Audit event: {row.audit_event_id}</p>
                    {row.target_event_id && <p>Target event: {row.target_event_id}</p>}
                    <p>Actor: {row.actor_reference}</p>
                  </div>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Fulfillment data is incomplete.' : 'No fulfillment follow-up is recorded.'}
          </p>
        )}
        {hiddenFulfillmentCount > 0 && (
          <p className="mt-1 text-[9px] text-text-muted">{hiddenFulfillmentCount.toLocaleString()} additional fulfillment rows are hidden.</p>
        )}
      </section>
    </section>
  )
}

function RecordedSupport({ summary }: { summary: SupportAccountSummary }) {
  const recorded = summary.event_count > 0
    || summary.one_time_tier !== null
    || summary.recurring_tier !== null
  const allowance = summary.recorded_allowance
  const visibleAllowanceSources = allowance?.sources.slice(0, 20) || []
  const hiddenAllowanceSourceCount = (allowance?.sources.length || 0) - visibleAllowanceSources.length
  return (
    <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
      <div className="flex items-center gap-2">
        <Check size={14} className={recorded ? 'text-indicator-success' : 'text-text-muted'} aria-hidden="true" />
        <h3 className="text-xs font-semibold text-text-primary">
          {recorded ? 'Support is recorded for this account' : 'No support record is attached to this account'}
        </h3>
      </div>
      {summary.benefits.state === 'recorded_not_enforced' && (
        <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
          Any listed eligibility is recorded only. Support-derived scheduling and retention benefits are not enforced yet.
        </p>
      )}
      {allowance && (
        <section aria-label="Recorded compute allowance" className="mt-3 min-w-0 rounded-lg border border-border bg-bg-primary/40 p-3">
          <div className="min-w-0">
            <h4 className="text-[11px] font-semibold text-text-primary">Current recorded allowance</h4>
            <p className="mt-1 break-words text-sm font-semibold text-accent-blue">
              {allowanceUnits(allowance.effective_allowance, allowance.unit)}
            </p>
            <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
              Recorded only as of{' '}
              <time dateTime={allowance.as_of}>{allowanceDate(allowance.as_of)} UTC</time>.
              {' '}This allowance is not enforced and does not currently change generation, queueing, or retries.
            </p>
          </div>
          <ul aria-label="Recorded allowance sources" className="mt-3 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
            {visibleAllowanceSources.map((source, index) => (
              <li key={`${source.source}-${index}`} className="min-w-0 rounded-md border border-border/70 bg-bg-tertiary/30 p-2">
                <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                  <span className="break-words text-[10px] font-semibold text-text-primary">{allowanceSourceLabels[source.source]}</span>
                  <span className="text-[9px] font-medium text-text-secondary">{allowanceStateText[source.status]}</span>
                </div>
                <p className="mt-1 break-words text-[9px] leading-relaxed text-text-muted">
                  {allowanceUnits(source.effective_allowance, allowance.unit)} recorded in this snapshot
                  {' '}from a {allowanceUnits(source.granted_allowance, allowance.unit)} recorded grant.
                </p>
                {source.expires_at && (
                  <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
                    Recorded expiry: <time dateTime={source.expires_at}>{allowanceDate(source.expires_at)} UTC</time>
                  </p>
                )}
                {source.refund_state in allowanceRefundLabels && (
                  <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
                    {allowanceRefundLabels[source.refund_state as keyof typeof allowanceRefundLabels]}.
                  </p>
                )}
              </li>
            ))}
          </ul>
          {hiddenAllowanceSourceCount > 0 && (
            <p className="mt-2 text-[9px] leading-relaxed text-text-muted">
              {hiddenAllowanceSourceCount.toLocaleString()} additional recorded {hiddenAllowanceSourceCount === 1 ? 'source is' : 'sources are'} not shown in this compact view.
            </p>
          )}
        </section>
      )}
    </section>
  )
}

export function SupportPanel() {
  const context = useStore(state => state.accountContext)
  const users = useStore(state => state.accountUsers)
  const catalog = useStore(state => state.supportCatalog)
  const catalogLoading = useStore(state => state.supportCatalogLoading)
  const catalogUnavailable = useStore(state => state.supportCatalogUnavailable)
  const self = useStore(state => state.supportSelf)
  const responsibleUse = useStore(state => state.responsibleUse)
  const admin = useStore(state => state.supportAdmin)
  const adminAccountId = useStore(state => state.supportAdminAccountId)
  const detailsLoading = useStore(state => state.supportDetailsLoading)
  const loadCatalog = useStore(state => state.loadSupportCatalog)
  const loadSelf = useStore(state => state.loadSupportSelf)
  const loadResponsibleUse = useStore(state => state.loadResponsibleUse)
  const acceptNotice = useStore(state => state.acceptResponsibleUse)
  const loadAdmin = useStore(state => state.loadSupportAdmin)
  const clearAdmin = useStore(state => state.clearSupportAdmin)
  const [selectedUserIndex, setSelectedUserIndex] = useState('')
  const adminSelectionEpochRef = useRef(0)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)

  const authenticated = context?.enabled === true
    && context.authenticated === true
    && context.capabilities.includes('account.self')
  const accountId = authenticated ? context.account?.id || null : null
  const ownerSupport = authenticated
    && context.account?.role === 'owner'
    && context.reauthenticated === true
    && context.capabilities.includes('accounts.admin')
    && context.capabilities.includes('services.admin')
  const selectedAdminAccountId = selectedUserIndex === ''
    ? null
    : users[Number(selectedUserIndex)]?.id ?? null
  const availableProviders = useMemo(() => availableSupportProviders(catalog), [catalog])
  const currentResponsibleUse = responsibleUse || self?.responsible_use || null
  const responsibleUseAccepted = responsibleUseIsAccepted(currentResponsibleUse)
  const selfPriorityNotice = affectedPriorityNotice(
    self?.account || null,
    self?.public.support_priority || null,
  )
  const adminPriorityNotice = affectedPriorityNotice(
    admin?.account || null,
    admin?.support_priority || null,
  )

  useEffect(() => {
    let active = true
    void loadCatalog()
    if (accountId !== null) {
      void Promise.all([loadSelf(), loadResponsibleUse()]).catch(error => {
        if (active) setNotice({ kind: 'error', text: supportErrorMessage(error) })
      })
    }
    return () => { active = false }
  }, [accountId, loadCatalog, loadResponsibleUse, loadSelf])

  useEffect(() => {
    if (!ownerSupport) {
      adminSelectionEpochRef.current += 1
      setSelectedUserIndex('')
      setNotice(null)
      clearAdmin()
    }
  }, [clearAdmin, ownerSupport])

  useEffect(() => () => {
    adminSelectionEpochRef.current += 1
    clearAdmin()
  }, [clearAdmin])

  useEffect(() => {
    if (
      !ownerSupport
      || !admin
      || adminAccountId === null
    ) return
    if (adminAccountId !== selectedAdminAccountId) {
      adminSelectionEpochRef.current += 1
      setSelectedUserIndex('')
      setNotice(null)
      clearAdmin()
      return
    }
    const displayAccountId = adminAccountId
    const displayProjection = admin
    const timeout = window.setTimeout(() => {
      if (
        useStore.getState().supportAdminAccountId === displayAccountId
        && useStore.getState().supportAdmin === displayProjection
      ) {
        adminSelectionEpochRef.current += 1
        setSelectedUserIndex('')
        setNotice(null)
        clearAdmin()
      }
    }, PRIVATE_SUPPORT_AUDIT_DISPLAY_TTL_MS)
    return () => window.clearTimeout(timeout)
  }, [admin, adminAccountId, clearAdmin, ownerSupport, selectedAdminAccountId])

  const acceptResponsibleUse = async () => {
    if (busy || !currentResponsibleUse) return
    setBusy(true)
    setNotice(null)
    try {
      await acceptNotice(
        currentResponsibleUse.notice.version,
        currentResponsibleUse.notice.content_sha256,
      )
      setNotice({ kind: 'success', text: 'Responsible-use acknowledgement recorded.' })
    } catch (error) {
      if (error instanceof AccountApiError && error.code === 'responsible_use_notice_changed') {
        await loadResponsibleUse().catch(() => null)
      }
      setNotice({ kind: 'error', text: supportErrorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  const chooseAdminAccount = async (nextIndex: string) => {
    const selectionEpoch = ++adminSelectionEpochRef.current
    setSelectedUserIndex(nextIndex)
    setNotice(null)
    clearAdmin()
    if (nextIndex === '') return
    const account = users[Number(nextIndex)]
    if (!account) return
    try {
      await loadAdmin(account.id)
    } catch (error) {
      if (selectionEpoch === adminSelectionEpochRef.current) {
        setNotice({ kind: 'error', text: adminSupportErrorMessage(error) })
      }
    }
  }

  return (
    <div className="space-y-4">
      {notice && (
        <div
          role={notice.kind === 'error' ? 'alert' : 'status'}
          aria-live="polite"
          className={`rounded-lg border px-3 py-2 text-[10px] leading-relaxed ${
            notice.kind === 'error'
              ? 'border-chip-red/50 bg-chip-red/10 text-chip-red'
              : 'border-indicator-success/50 bg-indicator-success/10 text-indicator-success'
          }`}
        >
          {notice.text}
        </div>
      )}

      <section className="rounded-xl border border-accent-blue/40 bg-accent-blue/5 p-4">
        <div className="flex items-start gap-3">
          <HeartHandshake size={18} className="mt-0.5 shrink-0 text-accent-blue" aria-hidden="true" />
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Support Maestro</h3>
            <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
              General support funds development, hosting, compute, and ML research. I have already spent hundreds on Codex and intend to recoup those costs before net support funds expand this work. When support is sufficient, I will host Maestro / Continuum with more compute.
            </p>
            <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
              Support is optional. Payment never authorizes prohibited use or changes anyone&apos;s responsibilities.
            </p>
          </div>
        </div>
      </section>

      <section aria-labelledby="support-options-heading" className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
        <div className="flex items-center gap-2">
          <h3 id="support-options-heading" className="flex-1 text-xs font-semibold text-text-primary">Support options</h3>
          {(catalogLoading || detailsLoading) && <Loader2 size={13} className="animate-spin text-text-muted" aria-label="Refreshing Support" />}
        </div>
        {availableProviders.length > 0 ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {availableProviders.map(provider => (
              <a
                key={provider.provider_id}
                href={provider.support_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg border border-border bg-bg-primary/40 p-3 transition-colors hover:border-border-light hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
              >
                <span className="flex items-center gap-2 text-[11px] font-semibold text-text-primary">
                  {provider.display_name}
                  <ExternalLink size={12} aria-hidden="true" />
                </span>
                <span className="mt-1 block text-[9px] leading-relaxed text-text-muted">{provider.description}</span>
              </a>
            ))}
          </div>
        ) : (
          <p className="mt-2 rounded-lg bg-bg-primary/40 px-3 py-2 text-[10px] leading-relaxed text-text-muted">
            {catalogUnavailable
              ? 'No support link is available in this session.'
              : 'No support provider is available right now.'}{' '}
            Support remains optional and does not change access or responsibilities.
          </p>
        )}
      </section>

      {authenticated && self && <RecordedSupport summary={self.account} />}
      {selfPriorityNotice && (
        <p className="rounded-lg border border-indicator-warning/40 bg-indicator-warning/5 px-3 py-2 text-[10px] leading-relaxed text-text-secondary">
          {selfPriorityNotice}
        </p>
      )}

      {authenticated && currentResponsibleUse && (
        <section aria-labelledby="responsible-use-heading" className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck size={14} className="text-accent-blue" aria-hidden="true" />
            <h3 id="responsible-use-heading" className="text-xs font-semibold text-text-primary">
              {currentResponsibleUse.notice.title}
            </h3>
          </div>
          <div className="mt-2 space-y-2 text-[10px] leading-relaxed text-text-muted">
            {currentResponsibleUse.notice.paragraphs.map((paragraph, index) => (
              <p key={`${currentResponsibleUse.notice.version}-${index}`}>{paragraph}</p>
            ))}
          </div>
          <p className="mt-2 text-[9px] leading-relaxed text-text-muted">
            This is an acknowledgement of the server-owned notice, not moderation, classification, or permission to generate content.
          </p>
          {responsibleUseAccepted ? (
            <p className="mt-3 flex items-center gap-2 text-[10px] font-semibold text-indicator-success" role="status">
              <Check size={13} aria-hidden="true" /> Acknowledged
            </p>
          ) : (
            <button
              type="button"
              onClick={() => void acceptResponsibleUse()}
              disabled={busy}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-accent-blue px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <ShieldCheck size={14} aria-hidden="true" />}
              Acknowledge responsible use
            </button>
          )}
        </section>
      )}

      {ownerSupport && users.length > 0 && (
        <section aria-labelledby="owner-support-heading" className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
          <h3 id="owner-support-heading" className="text-xs font-semibold text-text-primary">Owner support records</h3>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
            Choose only from the accounts returned by this server after recent owner confirmation.
          </p>
          <label className="mt-3 block text-[10px] font-medium text-text-secondary">
            <span>Account</span>
            <select
              value={selectedUserIndex}
              onChange={event => void chooseAdminAccount(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-bg-primary px-3 py-2 text-xs text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
            >
              <option value="">Choose an account</option>
              {users.map((user, index) => <option key={user.id} value={String(index)}>{user.username}</option>)}
            </select>
          </label>
          {selectedUserIndex !== ''
            && detailsLoading
            && (
              <p className="mt-3 flex items-center gap-2 rounded-lg bg-bg-primary/40 px-3 py-2 text-[10px] text-text-muted" role="status">
                <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Loading private support audit…
              </p>
            )}
          {selectedUserIndex !== ''
            && !detailsLoading
            && !admin
            && (
              <p className="mt-3 rounded-lg bg-bg-primary/40 px-3 py-2 text-[10px] text-text-muted" role="status">
                Private support audit is unavailable.
              </p>
            )}
          {selectedUserIndex !== ''
            && admin
            && adminAccountId === users[Number(selectedUserIndex)]?.id
            && (
            <div className="mt-3 space-y-2">
              <RecordedSupport summary={admin.account} />
              <AdminSupportAudit audit={admin.audit} />
              {adminPriorityNotice && (
                <p className="rounded-lg border border-indicator-warning/40 bg-indicator-warning/5 px-3 py-2 text-[10px] leading-relaxed text-text-secondary">
                  {adminPriorityNotice}
                </p>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
