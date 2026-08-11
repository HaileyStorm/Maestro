import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ExternalLink, HeartHandshake, Loader2, ShieldCheck } from 'lucide-react'
import { AccountApiError } from '../../api/client'
import { useStore } from '../../stores/useStore'
import type { SupportAccountSummary } from '../../types'
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

function RecordedSupport({ summary }: { summary: SupportAccountSummary }) {
  const recorded = summary.event_count > 0
    || summary.one_time_tier !== null
    || summary.recurring_tier !== null
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
    && context.reauthenticated === true
    && context.capabilities.includes('accounts.admin')
    && context.capabilities.includes('services.admin')
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
      clearAdmin()
    }
  }, [clearAdmin, ownerSupport])

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
        setNotice({ kind: 'error', text: supportErrorMessage(error) })
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
            && admin
            && adminAccountId === users[Number(selectedUserIndex)]?.id
            && (
            <div className="mt-3 space-y-2">
              <RecordedSupport summary={admin.account} />
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
