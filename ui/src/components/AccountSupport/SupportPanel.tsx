import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ExternalLink, HeartHandshake, Loader2, ShieldCheck } from 'lucide-react'
import { AccountApiError, isAccountProjectAccessActive } from '../../api/client'
import { useStore } from '../../stores/useStore'
import type {
  AccountActivationState,
  SupportAccountSummary,
  SupportAdminAudit,
  SupportAdminEventKind,
  SupportFulfillmentMutationInput,
  SupportFulfillmentStatus,
  SupportManualContributionInput,
  SupportManualContributionKind,
  SupportContributionSource,
} from '../../types'
import {
  affectedPriorityNotice,
  responsibleUseIsAccepted,
  visibleSupportProviders,
} from './supportPresentation'

interface AccountActivationReadiness {
  label: string
  detail: string
}

const accountActivationReadinessByState: Record<AccountActivationState, AccountActivationReadiness> = {
  disabled: {
    label: 'Accounts are optional and off',
    detail: 'No account setup or sign-in is required to keep using Maestro.',
  },
  setup_available: {
    label: 'Owner setup is available',
    detail: 'Create the first owner account from the Account tab while using Maestro directly on this computer. Maestro will not create an account automatically.',
  },
  setup_requires_loopback: {
    label: 'Open Maestro on this computer to continue',
    detail: 'For security, create the first owner account by opening Maestro directly on the computer where it is running. Setup details are hidden on this connection.',
  },
  disable_bootstrap: {
    label: 'Owner setup is complete',
    detail: 'Restart Maestro after turning off first-owner setup in its account configuration.',
  },
  ready: {
    label: 'Account access is ready',
    detail: 'Sign-in and account controls are available. Existing project access may still depend on this browser or a project password.',
  },
  unavailable: {
    label: 'Account setup status is unavailable',
    detail: 'Maestro could not determine whether account setup is ready, so setup is unavailable from this connection.',
  },
}

function accountActivationReadiness(
  state: unknown,
  accountProjectAccessActive = false,
): AccountActivationReadiness {
  if (state === 'ready' && accountProjectAccessActive) {
    return {
      label: 'Account access is ready',
      detail: 'Sign-in and account controls are available. Project access follows your account membership.',
    }
  }
  return typeof state === 'string' && Object.hasOwn(accountActivationReadinessByState, state)
    ? accountActivationReadinessByState[state as AccountActivationState]
    : accountActivationReadinessByState.unavailable
}

const supportErrorMessages: Record<string, string> = {
  authentication_required: 'Sign in to refresh support details.',
  owner_required: 'An owner account must confirm this request.',
  reauth_required: 'Confirm the owner password, then try again.',
  rate_limited: 'Too many support requests were made.',
  responsible_use_notice_changed: 'The notice changed. Review the updated notice, then try again.',
  account_store_unavailable: 'Support details are temporarily unavailable.',
  account_store_capacity: 'Support details cannot be updated because account storage is full.',
}

// Exported for deterministic copy-safety regression coverage.
// eslint-disable-next-line react-refresh/only-export-components
export function safeSupportErrorMessage(code: string, retryAfter = 0): string {
  const message = supportErrorMessages[code] || 'Support details could not be refreshed.'
  return retryAfter > 0
    ? `${message} Try again in about ${retryAfter} seconds.`
    : message
}

function supportErrorMessage(error: unknown): string {
  return error instanceof AccountApiError
    ? safeSupportErrorMessage(error.code, error.retryAfter)
    : 'Support details could not be refreshed.'
}

function adminSupportErrorMessage(error: unknown): string {
  if (error instanceof AccountApiError && error.retryAfter > 0) {
    return `Private support history could not be refreshed. Try again in about ${error.retryAfter} seconds.`
  }
  return 'Private support history could not be refreshed. Confirm the owner password and try again.'
}

function fulfillmentErrorMessage(error: unknown): string {
  if (error instanceof AccountApiError) {
    if (error.status === 409) return 'The follow-up changed. Support history was refreshed; review it and choose again.'
    if (error.status === 401 || error.status === 403) return 'Confirm the owner password before changing support follow-up.'
    if (error.status === 404) return 'That follow-up is no longer available. Refresh support history and choose again.'
    if (error.retryAfter > 0) return `The follow-up could not be saved. Try again in about ${error.retryAfter} seconds.`
  }
  return 'The follow-up could not be saved. Review the current support history and try again.'
}

function manualContributionErrorMessage(error: unknown): string {
  if (error instanceof AccountApiError) {
    if (error.status === 409) return 'Support history changed. It was refreshed; review it before saving again.'
    if (error.status === 401 || error.status === 403) return 'Confirm the owner password before adding a contribution record.'
    if (error.status === 400) return 'The contribution record was not accepted. Check its amount, currency, and related contribution.'
    if (error.retryAfter > 0) return `The contribution record could not be saved. Try again in about ${error.retryAfter} seconds.`
  }
  return 'The contribution record could not be saved. You can safely try again without changing the form.'
}

const allowanceSourceLabels = {
  free: 'Free allowance',
  one_time_support: 'One-time support',
  recurring_support: 'Recurring support',
} as const

const allowanceStateText = {
  active: 'Recorded status: active',
  inactive: 'Recorded status: inactive',
  refunded: 'Recorded status: refunded',
  expired: 'Recorded status: expired',
  capped: 'Recorded status: capped',
  canceled: 'Recorded status: canceled',
} as const

const allowanceRefundLabels = {
  partial: 'Partial refund recorded',
  full: 'Full refund recorded',
  excess: 'Refund exceeds the recorded source',
} as const

const PRIVATE_SUPPORT_AUDIT_DISPLAY_TTL_MS = 4 * 60 * 1000
const OPAQUE_SUPPORT_REFERENCE = /^key_[0-9a-f]{64}$/
const FULFILLMENT_ITEM = /^[a-z][a-z0-9_]{1,63}$/
const SUPPORT_CURRENCY = /^[A-Z]{3}$/
const MAX_SUPPORT_AMOUNT_MINOR = 10_000_000_000

const contributionSourceLabels: Record<SupportContributionSource, string> = {
  buy_me_a_coffee: 'Buy Me a Coffee',
  patreon: 'Patreon',
  direct_compute_sponsorship: 'Direct compute sponsorship',
}

const manualContributionKindLabels: Record<SupportManualContributionKind, string> = {
  one_time_contribution: 'One-time contribution',
  recurring_started: 'Recurring support started',
  recurring_renewed: 'Recurring support renewed',
  recurring_canceled: 'Recurring support canceled',
  refund: 'Refund',
  chargeback: 'Chargeback',
}

const manualContributionSources = Object.keys(contributionSourceLabels) as SupportContributionSource[]
// Manual audit provenance is independent of passive public-link marketing modes:
// every source can record every lifecycle kind, subject to target/state rules.
const manualContributionKinds = Object.keys(manualContributionKindLabels) as SupportManualContributionKind[]

const fulfillmentStatusLabels: Record<SupportFulfillmentStatus, string> = {
  pending: 'Pending',
  in_progress: 'In progress',
  fulfilled: 'Fulfilled',
  declined: 'Declined',
  reversed: 'Reversed',
}

const followUpItemLabels: Record<string, string> = {
  one_time_credit_grant: 'One-time credit record',
  retention_follow_up: 'Result-retention follow-up',
  backdated_follow_up: 'Backdated follow-up',
}

const nextFulfillmentStatuses: Record<SupportFulfillmentStatus, SupportFulfillmentStatus[]> = {
  pending: ['in_progress', 'fulfilled', 'declined'],
  in_progress: ['fulfilled', 'declined'],
  fulfilled: ['reversed'],
  declined: [],
  reversed: [],
}

function fulfillmentIdempotencyKey(): string {
  const bytes = new Uint8Array(32)
  globalThis.crypto.getRandomValues(bytes)
  return `key_${Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')}`
}

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
  fulfillment_set: 'Follow-up updated',
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

function followUpItemLabel(value: string): string {
  return followUpItemLabels[value] || auditLabel(value)
}

function minorUnits(value: number, currency: string): string {
  if (currency === 'USD') {
    const dollars = (value / 100).toLocaleString('en-US', {
      style: 'currency',
      currency: 'USD',
    })
    return `${value.toLocaleString('en-US')} cents (${dollars})`
  }
  return `${value.toLocaleString()} in the smallest ${currency} unit`
}

// Exported for deterministic linear-work regression coverage.
// eslint-disable-next-line react-refresh/only-export-components
export function manualContributionTargetState(
  events: SupportAdminAudit['events'],
  source: SupportContributionSource,
  currency: string,
) {
  const normalizedCurrency = currency.trim().toUpperCase()
  const provider = `manual_${source}`
  const matchingFundingEvents: SupportAdminAudit['events'] = []
  const adjustmentBySourceReference = new Map<string, number>()
  const latestRecurringByContract = new Map<string, SupportAdminAudit['events'][number]>()
  for (const event of events) {
    if (
      (event.kind === 'one_time_contribution'
        || event.kind === 'recurring_started'
        || event.kind === 'recurring_renewed')
      && event.provider === provider
      && event.currency === normalizedCurrency
    ) matchingFundingEvents.push(event)
    if (
      (event.kind === 'refund' || event.kind === 'chargeback')
      && event.provider === provider
      && event.currency === normalizedCurrency
      && event.related_reference !== null
    ) {
      adjustmentBySourceReference.set(
        event.related_reference,
        (adjustmentBySourceReference.get(event.related_reference) || 0) + event.amount_minor,
      )
    }
    if (
      event.contract_reference !== null
      && event.provider === provider
      && event.currency === normalizedCurrency
      && (event.kind === 'recurring_started'
        || event.kind === 'recurring_renewed'
        || event.kind === 'recurring_canceled')
    ) {
      const previous = latestRecurringByContract.get(event.contract_reference)
      if (
        !previous
        || event.occurred_at > previous.occurred_at
        || (event.occurred_at === previous.occurred_at && event.sequence > previous.sequence)
      ) {
        latestRecurringByContract.set(event.contract_reference, event)
      }
    }
  }
  const remainingByTarget = new Map(matchingFundingEvents.map(event => [
    event.event_id,
    Math.max(0, event.amount_minor - (adjustmentBySourceReference.get(event.source_reference) || 0)),
  ]))
  const activeRecurringTargets = new Set<string>()
  for (const latest of latestRecurringByContract.values()) {
    if (latest.kind !== 'recurring_canceled') activeRecurringTargets.add(latest.event_id)
  }
  return { matchingFundingEvents, remainingByTarget, activeRecurringTargets }
}

// Exported for exact ambiguous-response retry regression coverage.
// eslint-disable-next-line react-refresh/only-export-components
export function manualContributionRetryIdentity(
  identities: Map<string, string>,
  fingerprint: string,
): string {
  const retained = identities.get(fingerprint)
  if (retained) return retained
  const created = fulfillmentIdempotencyKey()
  identities.set(fingerprint, created)
  return created
}

function AdminSupportAudit({
  audit,
  onTransition,
  onRecordContribution,
}: {
  audit: SupportAdminAudit
  onTransition: (input: SupportFulfillmentMutationInput) => Promise<void>
  onRecordContribution: (input: SupportManualContributionInput) => Promise<void>
}) {
  const [fulfillmentLimit, setFulfillmentLimit] = useState(20)
  const [fundingEventLimit, setFundingEventLimit] = useState(40)
  const [proofByTask, setProofByTask] = useState<Record<string, string>>({})
  const [newTarget, setNewTarget] = useState('')
  const [newItem, setNewItem] = useState('')
  const [newProof, setNewProof] = useState('')
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [transitionNotice, setTransitionNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const retryRef = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null)
  const [manualSource, setManualSource] = useState<SupportContributionSource>('buy_me_a_coffee')
  const [manualKind, setManualKind] = useState<SupportManualContributionKind>('one_time_contribution')
  const [manualAmount, setManualAmount] = useState('1')
  const [manualCurrency, setManualCurrency] = useState('USD')
  const [manualTarget, setManualTarget] = useState('')
  const [manualBusy, setManualBusy] = useState(false)
  const [manualNotice, setManualNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const manualRetryRef = useRef(new Map<string, string>())
  const manualInFlightRef = useRef(false)
  const totals = Object.entries(audit.currency_totals_minor)
  const visibleEvents = audit.events.slice(-40).reverse()
  const hiddenEventCount = audit.events.length - visibleEvents.length
  const visibleDiscrepancies = audit.discrepancies.slice(0, 20)
  const hiddenDiscrepancyCount = audit.discrepancies.length - visibleDiscrepancies.length
  const fulfillmentByActionability = [...audit.fulfillment].sort((left, right) => {
    const leftActionable = nextFulfillmentStatuses[left.status].length > 0 ? 0 : 1
    const rightActionable = nextFulfillmentStatuses[right.status].length > 0 ? 0 : 1
    return leftActionable - rightActionable || right.changed_at.localeCompare(left.changed_at)
  })
  const visibleFulfillment = fulfillmentByActionability.slice(0, fulfillmentLimit)
  const hiddenFulfillmentCount = audit.fulfillment.length - visibleFulfillment.length
  const fundingEvents = audit.events.filter(event => (
    event.kind === 'one_time_contribution'
    || event.kind === 'recurring_started'
    || event.kind === 'recurring_renewed'
  )).reverse()
  const visibleFundingEvents = fundingEvents.slice(0, fundingEventLimit)
  const manualTargetRequired = manualKind !== 'one_time_contribution'
    && manualKind !== 'recurring_started'
  const { matchingFundingEvents, remainingByTarget, activeRecurringTargets } = manualContributionTargetState(
    audit.events,
    manualSource,
    manualCurrency,
  )
  const manualTargetEvents = matchingFundingEvents.filter(event => {
    if (manualKind === 'recurring_renewed' || manualKind === 'recurring_canceled') {
      return activeRecurringTargets.has(event.event_id)
    }
    return (remainingByTarget.get(event.event_id) || 0) > 0
  }).reverse()

  const changeManualSemantic = (change: () => void) => {
    setManualNotice(null)
    change()
  }

  const recordManualContribution = async () => {
    if (manualBusy || manualInFlightRef.current) return
    const currency = manualCurrency.trim().toUpperCase()
    const amount = Number(manualAmount)
    const target = manualTargetEvents.some(event => event.event_id === manualTarget)
      ? manualTarget
      : manualTargetEvents[0]?.event_id || ''
    const canceled = manualKind === 'recurring_canceled'
    if (!SUPPORT_CURRENCY.test(currency)) {
      setManualNotice({ kind: 'error', text: 'Use a three-letter uppercase currency code.' })
      return
    }
    if (
      !Number.isSafeInteger(amount)
      || amount < 0
      || amount > MAX_SUPPORT_AMOUNT_MINOR
      || (canceled ? amount !== 0 : amount <= 0)
    ) {
      setManualNotice({
        kind: 'error',
        text: canceled
          ? 'A recurring cancellation must have an amount of 0.'
          : 'Enter a positive whole number in the currency’s smallest unit.',
      })
      return
    }
    if (manualTargetRequired && target === '') {
      setManualNotice({
        kind: 'error',
        text: 'Choose a related contribution from the current support history.',
      })
      return
    }
    if (
      (manualKind === 'refund' || manualKind === 'chargeback')
      && amount > (remainingByTarget.get(target) || 0)
    ) {
      setManualNotice({
        kind: 'error',
        text: 'The adjustment cannot exceed the selected contribution’s remaining recorded amount.',
      })
      return
    }
    const normalizedTarget = manualTargetRequired ? target : null
    const fingerprint = JSON.stringify([
      manualSource, manualKind, amount, currency, normalizedTarget,
    ])
    const idempotencyKey = manualContributionRetryIdentity(manualRetryRef.current, fingerprint)
    manualInFlightRef.current = true
    setManualBusy(true)
    setManualNotice(null)
    try {
      await onRecordContribution({
        source: manualSource,
        kind: manualKind,
        amount_minor: amount,
        currency,
        target_event_id: normalizedTarget,
        idempotency_key: idempotencyKey,
      })
      manualRetryRef.current.delete(fingerprint)
      setManualAmount(canceled ? '0' : '1')
      setManualTarget('')
      setManualNotice({
        kind: 'success',
        text: 'Contribution record saved. No payment was processed. Any resulting compute allowance is shown below and is used only when hosted credit priority is enabled.',
      })
    } catch (error) {
      if (error instanceof AccountApiError && [400, 401, 403, 404, 409].includes(error.status)) {
        manualRetryRef.current.delete(fingerprint)
      }
      setManualNotice({ kind: 'error', text: manualContributionErrorMessage(error) })
    } finally {
      manualInFlightRef.current = false
      setManualBusy(false)
    }
  }

  const recordTransition = async (
    targetEventId: string,
    item: string,
    status: SupportFulfillmentStatus,
    proofReference: string,
    taskKey: string,
  ) => {
    if (busyKey !== null) return
    const normalizedItem = item.trim()
    const normalizedProof = proofReference.trim()
    if (!FULFILLMENT_ITEM.test(normalizedItem)) {
      setTransitionNotice({ kind: 'error', text: 'Use a 2–64 character lowercase follow-up type.' })
      return
    }
    if (normalizedProof !== '' && !OPAQUE_SUPPORT_REFERENCE.test(normalizedProof)) {
      setTransitionNotice({ kind: 'error', text: 'The optional reference ID must start with key_ and use the required format, or be left blank.' })
      return
    }
    const fingerprint = JSON.stringify([targetEventId, normalizedItem, status, normalizedProof])
    const idempotencyKey = retryRef.current?.fingerprint === fingerprint
      ? retryRef.current.idempotencyKey
      : fulfillmentIdempotencyKey()
    retryRef.current = { fingerprint, idempotencyKey }
    setBusyKey(taskKey)
    setTransitionNotice(null)
    try {
      await onTransition({
        target_event_id: targetEventId,
        item: normalizedItem,
        status,
        idempotency_key: idempotencyKey,
        proof_reference: normalizedProof || null,
      })
      retryRef.current = null
      setTransitionNotice({
        kind: 'success',
        text: `${fulfillmentStatusLabels[status]} was saved. This changes the record only; it does not apply credits or benefits.`,
      })
      if (taskKey === 'new') {
        setNewItem('')
        setNewProof('')
      }
    } catch (error) {
      if (
        error instanceof AccountApiError
        && [400, 401, 403, 404, 409].includes(error.status)
      ) retryRef.current = null
      setTransitionNotice({ kind: 'error', text: fulfillmentErrorMessage(error) })
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <section aria-labelledby="private-support-audit-heading" className="rounded-xl border border-border bg-bg-primary/30 p-3">
      <h4 id="private-support-audit-heading" className="text-[11px] font-semibold text-text-primary">
        Private support history and follow-up
      </h4>
      <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
        Available only after the owner recently confirmed their password. Maestro never processes a payment here. Contribution records can update the compute allowance used by optional hosted queue priority; follow-up records do not change benefits.
      </p>
      {transitionNotice && (
        <p
          className={`mt-2 rounded-md border px-2 py-2 text-[9px] leading-relaxed ${
            transitionNotice.kind === 'error'
              ? 'border-chip-red/50 bg-chip-red/10 text-chip-red'
              : 'border-indicator-success/50 bg-indicator-success/10 text-indicator-success'
          }`}
          role={transitionNotice.kind === 'error' ? 'alert' : 'status'}
        >
          {transitionNotice.text}
        </p>
      )}
      {audit.incomplete && (
        <p className="mt-2 rounded-md border border-indicator-warning/40 bg-indicator-warning/5 px-2 py-1 text-[9px] leading-relaxed text-text-secondary" role="status">
          Some support data could not be loaded and is not shown. A blank section does not necessarily mean there are no records.
        </p>
      )}

      <details className="mt-3 rounded-md border border-border/70 bg-bg-tertiary/20 px-2 text-[9px] text-text-muted">
        <summary className="flex min-h-11 cursor-pointer select-none items-center font-medium text-text-secondary">
          Add a contribution record
        </summary>
        <div className="space-y-2 pb-3">
          <p className="leading-relaxed">
            This does not process a payment. It records an owner-verified contribution and may update the account's compute allowance for optional hosted queue priority.
          </p>
          {manualNotice && (
            <p
              className={`rounded-md border px-2 py-2 leading-relaxed ${
                manualNotice.kind === 'error'
                  ? 'border-chip-red/50 bg-chip-red/10 text-chip-red'
                  : 'border-indicator-success/50 bg-indicator-success/10 text-indicator-success'
              }`}
              role={manualNotice.kind === 'error' ? 'alert' : 'status'}
            >
              {manualNotice.text}
            </p>
          )}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="block">
              <span>Support source</span>
              <select
                value={manualSource}
                onChange={event => changeManualSemantic(() => {
                  setManualSource(event.target.value as SupportContributionSource)
                  setManualTarget('')
                })}
                className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
              >
                {manualContributionSources.map(source => (
                  <option key={source} value={source}>{contributionSourceLabels[source]}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span>Type</span>
              <select
                value={manualKind}
                onChange={event => changeManualSemantic(() => {
                  const kind = event.target.value as SupportManualContributionKind
                  setManualKind(kind)
                  setManualAmount(kind === 'recurring_canceled' ? '0' : '1')
                  setManualTarget('')
                })}
                className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
              >
                {manualContributionKinds.map(kind => (
                  <option key={kind} value={kind}>{manualContributionKindLabels[kind]}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span>
                {manualCurrency.trim().toUpperCase() === 'USD'
                  ? 'Amount (USD cents)'
                  : 'Amount (whole number)'}
              </span>
              <input
                type="number"
                min={manualKind === 'recurring_canceled' ? 0 : 1}
                max={MAX_SUPPORT_AMOUNT_MINOR}
                step={1}
                inputMode="numeric"
                value={manualAmount}
                disabled={manualKind === 'recurring_canceled'}
                onChange={event => changeManualSemantic(() => setManualAmount(event.target.value))}
                className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue disabled:opacity-70"
              />
            </label>
            <label className="block">
              <span>Currency</span>
              <input
                type="text"
                value={manualCurrency}
                maxLength={3}
                autoComplete="off"
                spellCheck={false}
                onChange={event => changeManualSemantic(() => {
                  setManualCurrency(event.target.value.toUpperCase())
                  setManualTarget('')
                })}
                className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 font-mono text-[10px] uppercase text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
              />
            </label>
          </div>
          <p className="leading-relaxed">
            {manualCurrency.trim().toUpperCase() === 'USD'
              ? 'Enter cents for USD: 2500 = $25.00.'
              : 'Enter a whole number in the currency’s smallest unit.'}
          </p>
          {manualTargetRequired && (
            <label className="block">
              <span>Related contribution</span>
              <select
                value={manualTarget || manualTargetEvents[0]?.event_id || ''}
                onChange={event => changeManualSemantic(() => setManualTarget(event.target.value))}
                disabled={manualTargetEvents.length === 0}
                className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue disabled:opacity-70"
              >
                {manualTargetEvents.length === 0 && <option value="">No matching contribution in this history</option>}
                {manualTargetEvents.map(event => (
                  <option key={event.event_id} value={event.event_id}>
                    {auditEventLabels[event.kind]} · Record {event.sequence.toLocaleString()} · {allowanceDate(event.occurred_at)} UTC · {minorUnits(event.amount_minor, event.currency)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            disabled={manualBusy || (manualTargetRequired && manualTargetEvents.length === 0)}
            onClick={() => void recordManualContribution()}
            className="min-h-11 w-full rounded-md bg-accent-blue px-3 py-2 text-[10px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            {manualBusy ? 'Saving…' : 'Save contribution record'}
          </button>
        </div>
      </details>

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
        <h5 id="audit-events-heading" className="text-[10px] font-semibold text-text-secondary">Contribution history</h5>
        {visibleEvents.length > 0 ? (
          <ul className="mt-2 space-y-2" aria-label="Private contribution history">
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
                  <time dateTime={event.occurred_at}>{allowanceDate(event.occurred_at)} UTC</time>
                </p>
                <details className="mt-1 text-[9px] text-text-muted">
                  <summary className="cursor-pointer select-none">Technical details</summary>
                  <div className="mt-1 space-y-1 break-all font-mono">
                    <p>Record ID: {event.event_id}</p>
                    <p>Source ID: {event.source_reference}</p>
                    <p>Received: {allowanceDate(event.received_at)} UTC</p>
                    <p>Order: {event.sequence.toLocaleString()}</p>
                    {event.contract_reference && <p>Recurring-support ID: {event.contract_reference}</p>}
                    {event.related_reference && <p>Related record ID: {event.related_reference}</p>}
                    {event.actor_reference && <p>Changed by: {event.actor_reference}</p>}
                  </div>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Contribution history is incomplete.' : 'No contribution activity is recorded for this account.'}
          </p>
        )}
        {hiddenEventCount > 0 && (
          <p className="mt-2 text-[9px] text-text-muted">
            Showing the 40 newest history items; {hiddenEventCount.toLocaleString()} older {hiddenEventCount === 1 ? 'item is' : 'items are'} not shown.
          </p>
        )}
      </section>

      <section aria-labelledby="audit-discrepancies-heading" className="mt-3">
        <h5 id="audit-discrepancies-heading" className="text-[10px] font-semibold text-text-secondary">Items to review</h5>
        {visibleDiscrepancies.length > 0 ? (
          <ul className="mt-2 space-y-1" aria-label="Recorded support discrepancies">
            {visibleDiscrepancies.map(row => (
              <li key={`${row.event_id}-${row.reason}`} className="rounded-md border border-indicator-warning/40 bg-indicator-warning/5 p-2 text-[9px] leading-relaxed text-text-secondary">
                {discrepancyLabels[row.reason]}.
                <details className="mt-1 text-text-muted">
                  <summary className="cursor-pointer select-none">Technical details</summary>
                  <p className="mt-1 break-all font-mono">Record ID: {row.event_id}</p>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Some review items could not be loaded.' : 'No items need review.'}
          </p>
        )}
        {hiddenDiscrepancyCount > 0 && (
          <p className="mt-1 text-[9px] text-text-muted">{hiddenDiscrepancyCount.toLocaleString()} additional items are not shown.</p>
        )}
      </section>

      <section aria-labelledby="audit-fulfillment-heading" className="mt-3">
        <h5 id="audit-fulfillment-heading" className="text-[10px] font-semibold text-text-secondary">Support follow-up</h5>
        {visibleFulfillment.length > 0 ? (
          <ul className="mt-2 space-y-1" aria-label="Recorded support follow-up">
            {visibleFulfillment.map(row => (
              <li key={row.audit_event_id} className="rounded-md border border-border/70 bg-bg-tertiary/20 p-2 text-[9px] leading-relaxed text-text-secondary">
                <span className="font-semibold text-text-primary">{followUpItemLabel(row.item)}</span>
                {' · '}{fulfillmentStatusLabels[row.status]}
                {' · '}<time dateTime={row.changed_at}>{allowanceDate(row.changed_at)} UTC</time>
                <details className="mt-1 text-text-muted">
                  <summary className="cursor-pointer select-none">Technical details</summary>
                  <div className="mt-1 space-y-1 break-all font-mono">
                    <p>Follow-up type key: {row.item}</p>
                    <p>Follow-up record ID: {row.audit_event_id}</p>
                    {row.target_event_id && <p>Contribution record ID: {row.target_event_id}</p>}
                    <p>Changed by: {row.actor_reference}</p>
                    {row.proof_reference && <p>Reference ID: {row.proof_reference}</p>}
                  </div>
                </details>
                {row.target_event_id && nextFulfillmentStatuses[row.status].length > 0 && (
                  <details className="mt-2 rounded-md border border-border/70 bg-bg-primary/30 px-2 text-text-muted">
                    <summary className="flex min-h-11 cursor-pointer select-none items-center font-medium text-text-secondary">
                      Record next status
                    </summary>
                    <label className="block pb-2">
                      <span>Optional reference ID</span>
                      <input
                        type="text"
                        value={proofByTask[`${row.target_event_id}:${row.item}`] || ''}
                        onChange={event => setProofByTask(previous => ({
                          ...previous,
                          [`${row.target_event_id}:${row.item}`]: event.target.value,
                        }))}
                        placeholder="key_…"
                        autoComplete="off"
                        spellCheck={false}
                        className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 font-mono text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
                      />
                    </label>
                    <div className="grid grid-cols-1 gap-2 pb-2 sm:grid-cols-2">
                      {nextFulfillmentStatuses[row.status].map(status => {
                        const taskKey = `${row.target_event_id}:${row.item}`
                        return (
                          <button
                            key={status}
                            type="button"
                            disabled={busyKey !== null}
                            onClick={() => void recordTransition(
                              row.target_event_id || '',
                              row.item,
                              status,
                              proofByTask[taskKey] || '',
                              taskKey,
                            )}
                            className="min-h-11 rounded-md border border-border bg-bg-tertiary px-3 py-2 text-[10px] font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-50"
                          >
                            {busyKey === taskKey ? 'Saving…' : `Mark ${fulfillmentStatusLabels[status].toLowerCase()}`}
                          </button>
                        )
                      })}
                    </div>
                  </details>
                )}
                {nextFulfillmentStatuses[row.status].length === 0 && (
                  <p className="mt-1 text-text-muted">No further status changes are available.</p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[9px] text-text-muted">
            {audit.incomplete ? 'Follow-up history is incomplete.' : 'No support follow-up is recorded.'}
          </p>
        )}
        {hiddenFulfillmentCount > 0 && (
          <button
            type="button"
            onClick={() => setFulfillmentLimit(limit => limit + 20)}
            className="mt-2 min-h-11 w-full rounded-md border border-border px-3 py-2 text-[9px] font-medium text-text-secondary hover:bg-bg-hover"
          >
            Show 20 more follow-up items ({hiddenFulfillmentCount.toLocaleString()} remaining)
          </button>
        )}
        {fundingEvents.length > 0 && (
          <details className="mt-2 rounded-md border border-border/70 bg-bg-primary/30 px-2 text-[9px] text-text-muted">
            <summary className="flex min-h-11 cursor-pointer select-none items-center font-medium text-text-secondary">
              Start a pending follow-up
            </summary>
            <div className="space-y-2 pb-2">
              <label className="block">
                <span>Contribution event</span>
                <select
                  value={newTarget || fundingEvents[0]?.event_id || ''}
                  onChange={event => setNewTarget(event.target.value)}
                  className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
                >
                  {visibleFundingEvents.map(event => (
                    <option key={event.event_id} value={event.event_id}>
                      {auditEventLabels[event.kind]} · Record {event.sequence.toLocaleString()} · {auditLabel(event.provider)} · {allowanceDate(event.occurred_at)} UTC
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span>Custom follow-up type</span>
                <input
                  type="text"
                  value={newItem}
                  onChange={event => setNewItem(event.target.value)}
                  placeholder="Enter a lowercase technical key"
                  autoComplete="off"
                  spellCheck={false}
                  className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 font-mono text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
                />
              </label>
              <label className="block">
                <span>Optional reference ID</span>
                <input
                  type="text"
                  value={newProof}
                  onChange={event => setNewProof(event.target.value)}
                  placeholder="key_…"
                  autoComplete="off"
                  spellCheck={false}
                  className="mt-1 min-h-11 w-full rounded-md border border-border bg-bg-primary px-3 font-mono text-[10px] text-text-primary outline-none focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
                />
              </label>
              <button
                type="button"
                disabled={busyKey !== null}
                onClick={() => void recordTransition(
                  newTarget || fundingEvents[0]?.event_id || '',
                  newItem,
                  'pending',
                  newProof,
                  'new',
                )}
                className="min-h-11 w-full rounded-md bg-accent-blue px-3 py-2 text-[10px] font-semibold text-white hover:opacity-90 disabled:opacity-50"
              >
                {busyKey === 'new' ? 'Recording…' : 'Record pending follow-up'}
              </button>
              <p className="leading-relaxed">
                This saves a follow-up status only. It does not apply credits or benefits, contact a support service, or process a payment.
              </p>
              {visibleFundingEvents.length < fundingEvents.length && (
                <button
                  type="button"
                  onClick={() => setFundingEventLimit(limit => limit + 40)}
                  className="min-h-11 w-full rounded-md border border-border px-3 py-2 font-medium text-text-secondary hover:bg-bg-hover"
                >
                  Show 40 more contribution events ({(fundingEvents.length - visibleFundingEvents.length).toLocaleString()} remaining)
                </button>
              )}
            </div>
          </details>
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
  const allowanceActive = allowance?.enforcement_enabled === true
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
          Support benefits are not active. Any eligibility shown here is informational and does not change scheduling or how long results are kept.
        </p>
      )}
      {summary.benefits.state === 'hosted_priority_available' && (
        <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
          Hosted credit priority is enabled, but this account has no current allowance. Jobs still remain eligible in the ordinary queue.
        </p>
      )}
      {summary.benefits.state === 'owner_exempt' && (
        <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
          Owner jobs stay outside hosted credit accounting and use the ordinary owner scheduling path.
        </p>
      )}
      {summary.benefits.state === 'active' && (
        <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
          This account has active hosted compute allowance. Eligible jobs can receive bounded queue priority; jobs without enough allowance still remain eligible.
        </p>
      )}
      {allowance && (
        <section aria-label={allowanceActive ? 'Active hosted compute allowance' : 'Recorded informational compute allowance'} className="mt-3 min-w-0 rounded-lg border border-border bg-bg-primary/40 p-3">
          <div className="min-w-0">
            <h4 className="text-[11px] font-semibold text-text-primary">
              {allowanceActive ? 'Active hosted compute allowance' : 'Recorded informational compute allowance'}
            </h4>
            <p className="mt-1 break-words text-sm font-semibold text-accent-blue">
              {allowanceActive ? 'Available amount' : 'Recorded amount'}: {allowanceUnits(allowance.effective_allowance, allowance.unit)}
            </p>
            <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
              Recorded as of{' '}
              <time dateTime={allowance.as_of}>{allowanceDate(allowance.as_of)} UTC</time>.
              {allowanceActive
                ? ' Eligible hosted jobs can reserve this allowance for bounded queue priority.'
                : ' This is not active on the current host and does not change generation, queueing, or retries.'}
            </p>
          </div>
          <ul aria-label="Allowance breakdown" className="mt-3 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
            {visibleAllowanceSources.map((source, index) => (
              <li key={`${source.source}-${index}`} className="min-w-0 rounded-md border border-border/70 bg-bg-tertiary/30 p-2">
                <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                  <span className="break-words text-[10px] font-semibold text-text-primary">{allowanceSourceLabels[source.source]}</span>
                  <span className="text-[9px] font-medium text-text-secondary">{allowanceStateText[source.status]}</span>
                </div>
                <p className="mt-1 break-words text-[9px] leading-relaxed text-text-muted">
                  Recorded informational amount: {allowanceUnits(source.effective_allowance, allowance.unit)}
                  {' '}from an original recorded allowance of {allowanceUnits(source.granted_allowance, allowance.unit)}.
                </p>
                {source.expires_at && (
                  <p className="mt-1 text-[9px] leading-relaxed text-text-muted">
                    Listed expiration: <time dateTime={source.expires_at}>{allowanceDate(source.expires_at)} UTC</time>
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
              {hiddenAllowanceSourceCount.toLocaleString()} additional {hiddenAllowanceSourceCount === 1 ? 'item is' : 'items are'} not shown.
            </p>
          )}
        </section>
      )}
    </section>
  )
}

export function SupportPanel() {
  const context = useStore(state => state.accountContext)
  const accessContext = useStore(state => state.accessContext)
  const projectMigration = useStore(state => state.accountProjectMigration)
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
  const transitionFulfillment = useStore(state => state.transitionSupportFulfillment)
  const recordContribution = useStore(state => state.recordSupportContribution)
  const clearAdmin = useStore(state => state.clearSupportAdmin)
  const [selectedUserIndex, setSelectedUserIndex] = useState('')
  const adminSelectionEpochRef = useRef(0)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const accountProjectAccessActive = isAccountProjectAccessActive(
    accessContext,
    projectMigration,
  )
  const activationReadiness = accountActivationReadiness(
    context?.activation_state,
    accountProjectAccessActive,
  )

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
  const visibleProviders = useMemo(() => visibleSupportProviders(catalog), [catalog])
  const currentResponsibleUse = responsibleUse || self?.responsible_use || null
  const responsibleUseAccepted = responsibleUseIsAccepted(currentResponsibleUse)
  const selfPriorityNotice = affectedPriorityNotice(
    self?.account || null,
    catalog?.support_priority || null,
  )
  const adminPriorityNotice = affectedPriorityNotice(
    admin?.account || null,
    catalog?.support_priority || null,
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

  const recordFulfillment = async (input: SupportFulfillmentMutationInput) => {
    if (!ownerSupport || adminAccountId === null || adminAccountId !== selectedAdminAccountId) {
      throw new Error('Owner access or Support selection changed.')
    }
    await transitionFulfillment(adminAccountId, input)
  }

  const recordManualContribution = async (input: SupportManualContributionInput) => {
    if (!ownerSupport || adminAccountId === null || adminAccountId !== selectedAdminAccountId) {
      throw new Error('Owner access or Support selection changed.')
    }
    await recordContribution(adminAccountId, input)
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

      <section aria-label="Account setup status" className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
        <div className="flex items-center gap-2">
          <Check size={14} className="shrink-0 text-accent-blue" aria-hidden="true" />
          <h3 className="text-xs font-semibold text-text-primary">Account setup</h3>
        </div>
        <p className="mt-2 text-[10px] font-semibold leading-relaxed text-text-secondary" role="status">
          {activationReadiness.label}
        </p>
        <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
          {activationReadiness.detail}
        </p>
      </section>

      <section className="rounded-xl border border-accent-blue/40 bg-accent-blue/5 p-4">
        <div className="flex items-start gap-3">
          <HeartHandshake size={18} className="mt-0.5 shrink-0 text-accent-blue" aria-hidden="true" />
          <div>
            <h3 className="text-sm font-semibold text-text-primary">Support Maestro</h3>
            <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
              Support first helps recoup the hundreds already spent on Codex while building Maestro. After support becomes sustainable, it will fund hosting Maestro Continuum with more compute.
            </p>
            <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
              Support is optional and offers no guarantees or perks. It does not change access or anyone&apos;s responsibilities.
            </p>
          </div>
        </div>
      </section>

      <section aria-labelledby="support-options-heading" className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
        <div className="flex items-center gap-2">
          <h3 id="support-options-heading" className="flex-1 text-xs font-semibold text-text-primary">Support options</h3>
          {(catalogLoading || detailsLoading) && <Loader2 size={13} className="animate-spin text-text-muted" aria-label="Refreshing Support" />}
        </div>
        {visibleProviders.length > 0 ? (
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {visibleProviders.map(provider => {
              const content = (
                <>
                  <span className="flex items-center gap-2 text-[11px] font-semibold text-text-primary">
                    {provider.display_name}
                    {provider.support_url && <ExternalLink size={12} aria-hidden="true" />}
                  </span>
                  <span className="mt-1 block text-[9px] leading-relaxed text-text-muted">{provider.description}</span>
                  {!provider.support_url && (
                    <span className="mt-1 block text-[9px] font-medium text-text-muted">Not available in this session</span>
                  )}
                </>
              )
              return provider.support_url ? (
                <a
                  key={provider.provider_id}
                  href={provider.support_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="min-h-11 rounded-lg border border-border bg-bg-primary/40 p-3 transition-colors hover:border-border-light hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                >
                  {content}
                </a>
              ) : (
                <div
                  key={provider.provider_id}
                  aria-disabled="true"
                  className="min-h-11 rounded-lg border border-border bg-bg-primary/20 p-3 opacity-70"
                >
                  {content}
                </div>
              )
            })}
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
            Acknowledging this notice does not review, restrict, or approve what you create.
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
          <h3 id="owner-support-heading" className="text-xs font-semibold text-text-primary">Manage support records</h3>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
            Choose an account after confirming the owner password.
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
                <Loader2 size={12} className="animate-spin" aria-hidden="true" /> Loading private support history…
              </p>
            )}
          {selectedUserIndex !== ''
            && !detailsLoading
            && !admin
            && (
              <p className="mt-3 rounded-lg bg-bg-primary/40 px-3 py-2 text-[10px] text-text-muted" role="status">
                Private support history is unavailable.
              </p>
            )}
          {selectedUserIndex !== ''
            && admin
            && adminAccountId === users[Number(selectedUserIndex)]?.id
            && (
            <div className="mt-3 space-y-2">
              <RecordedSupport summary={admin.account} />
              <AdminSupportAudit
                audit={admin.audit}
                onTransition={recordFulfillment}
                onRecordContribution={recordManualContribution}
              />
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
