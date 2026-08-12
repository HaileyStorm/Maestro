import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Check,
  HeartHandshake,
  KeyRound,
  Loader2,
  LogIn,
  LogOut,
  RefreshCw,
  ShieldCheck,
  UserCog,
  UserPlus,
  UserRound,
  X,
} from 'lucide-react'
import { AccountApiError } from '../../api/client'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'
import { useStore } from '../../stores/useStore'
import { createAccountDrawerLifecycle } from './accountDrawerLifecycle'
import { SupportPanel } from './SupportPanel'
import { nextAccountSupportTab, type AccountSupportTab } from './supportPresentation'

function resolveAccountSupportTrigger(document: Document, fallback: HTMLElement | null): HTMLElement | null {
  const mobile = document.defaultView?.matchMedia?.('(max-width: 767px)').matches === true
  const expected = document.querySelector<HTMLElement>(
    `[data-responsive-dialog-trigger="account-support:${mobile ? 'mobile' : 'desktop'}"]`,
  )
  if (expected && expected.isConnected !== false) return expected
  if (fallback && fallback.isConnected !== false) return fallback
  const replacement = document.querySelector<HTMLElement>('[data-responsive-dialog-trigger^="account-support:"]')
  return replacement && replacement.isConnected !== false ? replacement : null
}

function accountErrorMessage(error: unknown): string {
  if (error instanceof AccountApiError && error.retryAfter > 0) {
    return `${error.message} Try again in about ${error.retryAfter} seconds.`
  }
  return error instanceof Error ? error.message : 'The account request could not be completed.'
}

function formatTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

function Field({
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
  required = false,
  minLength,
  placeholder,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: 'text' | 'password' | 'email'
  autoComplete?: string
  required?: boolean
  minLength?: number
  placeholder?: string
}) {
  return (
    <label className="block text-[10px] font-medium text-text-secondary">
      <span>{label}</span>
      <input
        type={type}
        value={value}
        onChange={event => onChange(event.target.value)}
        autoComplete={autoComplete}
        required={required}
        minLength={minLength}
        placeholder={placeholder}
        tabIndex={0}
        className="mt-1 w-full rounded-lg border border-border bg-bg-primary px-3 py-2 text-xs text-text-primary outline-none transition-colors placeholder:text-text-muted focus:border-accent-blue focus:ring-1 focus:ring-accent-blue"
      />
    </label>
  )
}

function OneTimeCodes({
  label,
  codes,
  onDismiss,
}: {
  label: string
  codes: string[]
  onDismiss: () => void
}) {
  return (
    <section className="rounded-xl border border-indicator-warning/60 bg-indicator-warning/10 p-3" aria-live="polite">
      <div className="flex items-start gap-2">
        <KeyRound size={15} className="mt-0.5 shrink-0 text-indicator-warning" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-semibold text-text-primary">{label}</h3>
          <p className="mt-1 text-[10px] leading-relaxed text-text-secondary">
            Save these now. Maestro will not show this set again after it is dismissed or this panel closes.
          </p>
          <ol className="mt-2 grid gap-1 rounded-lg bg-bg-primary/70 p-2 font-mono text-[10px] text-text-primary sm:grid-cols-2">
            {codes.map(code => <li key={code}>{code}</li>)}
          </ol>
          <button
            type="button"
            onClick={onDismiss}
            className="mt-2 rounded-lg border border-border px-2.5 py-1.5 text-[10px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary"
          >
            I saved them
          </button>
        </div>
      </div>
    </section>
  )
}

export function AccountSupportButton({ compact = false }: { compact?: boolean }) {
  const context = useStore(state => state.accountContext)
  const open = useStore(state => state.accountDrawerOpen)
  const setOpen = useStore(state => state.setAccountDrawerOpen)

  const accountLabel = context?.authenticated ? context.account?.username || 'Account' : null
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      data-responsive-dialog-trigger={`account-support:${compact ? 'mobile' : 'desktop'}`}
      aria-haspopup="dialog"
      aria-controls="account-support-drawer"
      aria-expanded={open}
      aria-label={accountLabel ? `Open Support and account for ${accountLabel}` : 'Open Support'}
      className={`flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-border bg-bg-secondary text-text-secondary shadow-lg transition-colors hover:border-border-light hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
        compact ? 'h-11 w-11 p-0' : 'px-3 py-2 text-[11px] font-semibold'
      }`}
    >
      <HeartHandshake size={compact ? 18 : 14} aria-hidden="true" />
      {!compact && <span className="max-w-32 truncate">Support</span>}
    </button>
  )
}

export function AccountSupportDrawer() {
  const titleId = useId()
  const descriptionId = useId()
  const supportTabId = useId()
  const accountTabId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const supportTabRef = useRef<HTMLButtonElement>(null)
  const accountTabRef = useRef<HTMLButtonElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)
  const focusReturnRef = useRef<HTMLSpanElement>(null)
  const lifecycleRef = useRef(createAccountDrawerLifecycle())
  const open = useStore(state => state.accountDrawerOpen)
  const setOpen = useStore(state => state.setAccountDrawerOpen)
  const context = useStore(state => state.accountContext)
  const contextLoading = useStore(state => state.accountContextLoading)
  const sessions = useStore(state => state.accountSessions)
  const users = useStore(state => state.accountUsers)
  const detailsLoading = useStore(state => state.accountDetailsLoading)
  const loadContext = useStore(state => state.loadAccountContext)
  const bootstrap = useStore(state => state.bootstrapAccount)
  const login = useStore(state => state.loginAccount)
  const logout = useStore(state => state.logoutAccount)
  const reauthenticate = useStore(state => state.reauthenticateAccount)
  const recover = useStore(state => state.recoverAccount)
  const changePassword = useStore(state => state.changeAccountPassword)
  const rotateRecoveryCodes = useStore(state => state.rotateAccountRecoveryCodes)
  const loadSessions = useStore(state => state.loadAccountSessions)
  const revokeSession = useStore(state => state.revokeAccountSession)
  const revokeAllSessions = useStore(state => state.revokeAllAccountSessions)
  const loadUsers = useStore(state => state.loadAccountUsers)
  const createUser = useStore(state => state.createServerAccount)
  const setUserDisabled = useStore(state => state.setServerAccountDisabled)

  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null)
  const [oneTimeCodes, setOneTimeCodes] = useState<string[]>([])
  const [codesLabel, setCodesLabel] = useState('Recovery codes')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [deviceLabel, setDeviceLabel] = useState('Browser')
  const [recoveryCode, setRecoveryCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [reauthPassword, setReauthPassword] = useState('')
  const [managedUsername, setManagedUsername] = useState('')
  const [managedPassword, setManagedPassword] = useState('')
  const [managedEmail, setManagedEmail] = useState('')
  const [activeTab, setActiveTab] = useState<AccountSupportTab>('support')

  const clearSensitive = useCallback(() => {
    setPassword('')
    setEmail('')
    setRecoveryCode('')
    setNewPassword('')
    setReauthPassword('')
    setManagedPassword('')
    setManagedEmail('')
    setOneTimeCodes([])
  }, [])

  const closeDrawer = useCallback(() => {
    lifecycleRef.current.closed()
    clearSensitive()
    setBusy('')
    setNotice(null)
    setActiveTab('support')
    setOpen(false)
  }, [clearSensitive, setOpen])

  const requestCloseDrawer = useCallback(() => {
    closeModalIfTop(document, dialogRef.current, closeDrawer)
  }, [closeDrawer])

  const selectTab = useCallback((tab: AccountSupportTab) => {
    if (tab === activeTab) return
    if (tab === 'support') {
      lifecycleRef.current.closed()
      clearSensitive()
      setBusy('')
      setNotice(null)
    } else {
      lifecycleRef.current.opened()
    }
    setActiveTab(tab)
  }, [activeTab, clearSensitive])

  const handleTabKeyDown = useCallback((
    event: KeyboardEvent<HTMLButtonElement>,
    current: AccountSupportTab,
  ) => {
    const next = nextAccountSupportTab(current, event.key)
    if (next === null) return
    event.preventDefault()
    selectTab(next)
    const nextTabRef = next === 'support' ? supportTabRef : accountTabRef
    nextTabRef.current?.focus()
  }, [selectTab])

  const run = useCallback(async (
    name: string,
    action: (isCurrent: () => boolean) => Promise<void>,
  ) => {
    if (busy) return
    const isCurrent = lifecycleRef.current.operationLease()
    setBusy(name)
    setNotice(null)
    try {
      await action(isCurrent)
    } catch (error) {
      if (isCurrent()) setNotice({ kind: 'error', text: accountErrorMessage(error) })
    } finally {
      if (isCurrent()) setBusy('')
    }
  }, [busy])

  useEffect(() => {
    if (!open) return
    const lifecycle = lifecycleRef.current
    lifecycle.opened()
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    let active = true
    void loadContext().then(async next => {
      if (!active || next?.authenticated !== true) return
      await loadSessions().catch(error => {
        if (active) setNotice({ kind: 'error', text: accountErrorMessage(error) })
      })
      if (next.reauthenticated && next.capabilities.includes('accounts.admin')) {
        await loadUsers().catch(error => {
          if (active) setNotice({ kind: 'error', text: accountErrorMessage(error) })
        })
      }
    }).catch(error => {
      if (active) setNotice({ kind: 'error', text: accountErrorMessage(error) })
    })
    return () => {
      active = false
      lifecycle.closed()
    }
  }, [loadContext, loadSessions, loadUsers, open])

  useEffect(() => {
    if (open) return
    lifecycleRef.current.closed()
    clearSensitive()
    setBusy('')
    setNotice(null)
  }, [clearSensitive, open])

  useEffect(() => {
    if (context?.enabled === true || activeTab !== 'account') return
    lifecycleRef.current.closed()
    clearSensitive()
    setBusy('')
    setNotice(null)
    setActiveTab('support')
  }, [activeTab, clearSensitive, context?.enabled])

  useEffect(() => {
    if (!open || !dialogRef.current || !closeRef.current) return
    const nativeControls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
    ))
    const annotatedControls = nativeControls.filter(control => !control.hasAttribute('tabindex'))
    for (const control of annotatedControls) control.setAttribute('tabindex', '0')
    const uninstall = installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeRef.current,
      restoreFocus: focusReturnRef.current,
      appRoot: document.getElementById('root'),
      onClose: closeDrawer,
    })
    return () => {
      for (const control of annotatedControls) control.removeAttribute('tabindex')
      uninstall()
    }
  }, [closeDrawer, open])

  const focusReturnTarget = (
    <span
      ref={focusReturnRef}
      tabIndex={-1}
      className="fixed h-px w-px overflow-hidden opacity-0 pointer-events-none"
      data-responsive-dialog-focus-return="account-support"
      onFocus={() => resolveAccountSupportTrigger(document, restoreFocusRef.current)?.focus()}
    />
  )

  if (!open) return focusReturnTarget
  const accountsEnabled = context?.enabled === true
  const authenticated = accountsEnabled && context.authenticated && context.account !== null
  const selfService = authenticated && context.capabilities.includes('account.self')
  const accountAdmin = authenticated
    && context.capabilities.includes('accounts.admin')
    && context.capabilities.includes('services.admin')

  return <>
    {focusReturnTarget}
    {createPortal(
    <div
      className="fixed inset-0 z-[170] flex items-stretch justify-end"
      style={{ paddingTop: 'env(safe-area-inset-top)', paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close Support panel"
        className="absolute inset-0 appearance-none border-0 bg-black/70 p-0"
        onClick={requestCloseDrawer}
      />
      <div
        id="account-support-drawer"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-border bg-bg-secondary shadow-2xl sm:max-w-xl"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-border px-4 py-4 sm:px-5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-blue/15 text-accent-blue">
            <HeartHandshake size={18} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-sm font-semibold text-text-primary">
              {accountsEnabled ? 'Support & account' : 'Support'}
            </h2>
            <p id={descriptionId} className="mt-0.5 text-[10px] leading-relaxed text-text-muted">
              Optional support and account controls. Project access stays separate from this account.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={requestCloseDrawer}
            aria-label="Close Support panel"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg p-0 text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:h-auto md:w-auto md:p-1.5"
          >
            <X size={17} aria-hidden="true" />
          </button>
        </header>

        {accountsEnabled && (
          <div className="grid shrink-0 grid-cols-2 border-b border-border p-1" role="tablist" aria-label="Support and account sections">
            <button
              id={supportTabId}
              ref={supportTabRef}
              type="button"
              role="tab"
              aria-selected={activeTab === 'support'}
              aria-controls="support-panel"
              tabIndex={activeTab === 'support' ? 0 : -1}
              onClick={() => selectTab('support')}
              onKeyDown={event => handleTabKeyDown(event, 'support')}
              className={`rounded-lg px-3 py-2 text-[11px] font-semibold ${activeTab === 'support' ? 'bg-bg-hover text-text-primary' : 'text-text-muted hover:text-text-primary'}`}
            >
              Support
            </button>
            <button
              id={accountTabId}
              ref={accountTabRef}
              type="button"
              role="tab"
              aria-selected={activeTab === 'account'}
              aria-controls="account-panel"
              tabIndex={activeTab === 'account' ? 0 : -1}
              onClick={() => selectTab('account')}
              onKeyDown={event => handleTabKeyDown(event, 'account')}
              className={`rounded-lg px-3 py-2 text-[11px] font-semibold ${activeTab === 'account' ? 'bg-bg-hover text-text-primary' : 'text-text-muted hover:text-text-primary'}`}
            >
              Account
            </button>
          </div>
        )}

        <div
          role="region"
          aria-label={accountsEnabled ? 'Support and account content' : 'Support content'}
          tabIndex={0}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 [-webkit-overflow-scrolling:touch] sm:px-5"
        >
          {activeTab === 'support' || !accountsEnabled ? (
            <div
              id="support-panel"
              role="tabpanel"
              aria-labelledby={accountsEnabled ? supportTabId : undefined}
              aria-label={accountsEnabled ? undefined : 'Support'}
            >
              <SupportPanel />
            </div>
          ) : (
          <div id="account-panel" role="tabpanel" aria-labelledby={accountTabId}>
          {(contextLoading || detailsLoading) && (
            <div className="mb-3 flex items-center gap-2 text-[10px] text-text-muted" role="status">
              <Loader2 size={13} className="animate-spin" aria-hidden="true" />
              Refreshing account state…
            </div>
          )}
          {notice && (
            <div
              role={notice.kind === 'error' ? 'alert' : 'status'}
              className={`mb-3 rounded-lg border px-3 py-2 text-[10px] leading-relaxed ${
                notice.kind === 'error'
                  ? 'border-chip-red/50 bg-chip-red/10 text-chip-red'
                  : 'border-indicator-success/50 bg-indicator-success/10 text-indicator-success'
              }`}
            >
              {notice.text}
            </div>
          )}
          {oneTimeCodes.length > 0 && (
            <div className="mb-4">
              <OneTimeCodes label={codesLabel} codes={oneTimeCodes} onDismiss={() => setOneTimeCodes([])} />
            </div>
          )}

          {!authenticated ? (
            <div className="space-y-4">
              {context.bootstrap_available === true && (
                <form
                  className="rounded-xl border border-accent-blue/50 bg-accent-blue/5 p-3"
                  onSubmit={event => {
                    event.preventDefault()
                    void run('bootstrap', async isCurrent => {
                      const result = await bootstrap({ username, password, email, deviceLabel })
                      if (!isCurrent()) return
                      setPassword('')
                      setEmail('')
                      setOneTimeCodes(result.recovery_codes || [])
                      setCodesLabel('Owner recovery codes')
                      setNotice({ kind: 'success', text: 'Owner account created and signed in.' })
                    })
                  }}
                >
                  <div className="flex items-center gap-2">
                    <ShieldCheck size={15} className="text-accent-blue" aria-hidden="true" />
                    <h3 className="text-xs font-semibold text-text-primary">Create the first owner account</h3>
                  </div>
                  <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
                    This setup is available only because the server explicitly offered local bootstrap.
                  </p>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <Field label="Username" value={username} onChange={setUsername} autoComplete="username" required />
                    <Field label="Device label" value={deviceLabel} onChange={setDeviceLabel} autoComplete="off" required />
                    <Field label="Password" value={password} onChange={setPassword} type="password" autoComplete="new-password" required minLength={12} />
                    <Field label="Email (optional)" value={email} onChange={setEmail} type="email" autoComplete="email" />
                  </div>
                  <button
                    type="submit"
                    disabled={Boolean(busy)}
                    className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-accent-blue px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
                  >
                    {busy === 'bootstrap' ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
                    Create owner account
                  </button>
                </form>
              )}

              <form
                className="rounded-xl border border-border bg-bg-tertiary/30 p-3"
                onSubmit={event => {
                  event.preventDefault()
                  void run('login', async isCurrent => {
                    await login({ username, password, deviceLabel })
                    if (!isCurrent()) return
                    setPassword('')
                    setNotice({ kind: 'success', text: 'Signed in.' })
                  })
                }}
              >
                <div className="flex items-center gap-2">
                  <LogIn size={15} className="text-accent-blue" aria-hidden="true" />
                  <h3 className="text-xs font-semibold text-text-primary">Sign in</h3>
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <Field label="Username" value={username} onChange={setUsername} autoComplete="username" required />
                  <Field label="Device label" value={deviceLabel} onChange={setDeviceLabel} autoComplete="off" required />
                  <div className="sm:col-span-2">
                    <Field label="Password" value={password} onChange={setPassword} type="password" autoComplete="current-password" required />
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={Boolean(busy)}
                  className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-accent-blue px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
                >
                  {busy === 'login' ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}
                  Sign in
                </button>
              </form>

              <details className="rounded-xl border border-border bg-bg-tertiary/20">
                <summary className="cursor-pointer px-3 py-3 text-xs font-semibold text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue">
                  Recover an account
                </summary>
                <form
                  className="border-t border-border p-3"
                  onSubmit={event => {
                    event.preventDefault()
                    void run('recover', async isCurrent => {
                      const result = await recover({ username, recoveryCode, newPassword, deviceLabel })
                      if (!isCurrent()) return
                      setRecoveryCode('')
                      setNewPassword('')
                      setOneTimeCodes(result.recovery_codes || [])
                      setCodesLabel('Replacement recovery codes')
                      setNotice({ kind: 'success', text: 'Account recovered and signed in.' })
                    })
                  }}
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Username" value={username} onChange={setUsername} autoComplete="username" required />
                    <Field label="Device label" value={deviceLabel} onChange={setDeviceLabel} autoComplete="off" required />
                    <Field label="Recovery code" value={recoveryCode} onChange={setRecoveryCode} autoComplete="one-time-code" required />
                    <Field label="New password" value={newPassword} onChange={setNewPassword} type="password" autoComplete="new-password" required minLength={12} />
                  </div>
                  <button
                    type="submit"
                    disabled={Boolean(busy)}
                    className="mt-3 w-full rounded-lg border border-border-light px-3 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-50"
                  >
                    Recover and sign in
                  </button>
                </form>
              </details>
            </div>
          ) : (
            <div className="space-y-4">
              <section className="rounded-xl border border-border bg-bg-tertiary/30 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-blue/15 text-accent-blue">
                    <UserRound size={16} aria-hidden="true" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-semibold text-text-primary">{context.account!.username}</p>
                    <p className="text-[9px] uppercase tracking-wider text-text-muted">{context.account!.role}</p>
                  </div>
                  <span className={`rounded-full px-2 py-1 text-[9px] font-semibold ${
                    context.reauthenticated
                      ? 'bg-indicator-success/15 text-indicator-success'
                      : 'bg-bg-primary text-text-muted'
                  }`}>
                    {context.reauthenticated ? 'Recently confirmed' : 'Confirmation needed for sensitive actions'}
                  </span>
                </div>
                <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
                  This account cookie does not replace or mutate the browser session that owns projects, uploads, jobs, or outputs.
                </p>
              </section>

              {selfService && !context.reauthenticated && (
                <form
                  className="rounded-xl border border-indicator-warning/50 bg-indicator-warning/5 p-3"
                  onSubmit={event => {
                    event.preventDefault()
                    void run('reauth', async isCurrent => {
                      await reauthenticate(reauthPassword)
                      if (!isCurrent()) return
                      setReauthPassword('')
                      await Promise.all([loadSessions(), loadUsers()])
                      if (!isCurrent()) return
                      setNotice({ kind: 'success', text: 'Sensitive account actions are temporarily unlocked.' })
                    })
                  }}
                >
                  <h3 className="text-xs font-semibold text-text-primary">Confirm your password</h3>
                  <p className="mt-1 text-[10px] text-text-muted">Required before password, recovery-code, all-session, and owner-administration changes.</p>
                  <div className="mt-3">
                    <Field label="Current password" value={reauthPassword} onChange={setReauthPassword} type="password" autoComplete="current-password" required />
                  </div>
                  <button type="submit" disabled={Boolean(busy)} className="mt-3 w-full rounded-lg bg-accent-blue px-3 py-2 text-xs font-semibold text-white disabled:opacity-50">
                    Confirm password
                  </button>
                </form>
              )}

              {selfService && (
                <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
                  <div className="flex items-center gap-2">
                    <h3 className="flex-1 text-xs font-semibold text-text-primary">Active sessions</h3>
                    <button
                      type="button"
                      onClick={() => void run('refresh-sessions', async () => { await loadSessions() })}
                      disabled={Boolean(busy)}
                      aria-label="Refresh active sessions"
                      className="rounded-lg p-1.5 text-text-muted hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
                    >
                      <RefreshCw size={13} className={busy === 'refresh-sessions' ? 'animate-spin' : ''} />
                    </button>
                  </div>
                  <div className="mt-2 space-y-2">
                    {sessions.map(session => (
                      <div key={session.id} className="flex items-start gap-2 rounded-lg border border-border/80 bg-bg-primary/40 p-2.5">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[10px] font-semibold text-text-primary">
                            {session.device_label}{session.current ? ' · Current' : ''}
                          </p>
                          <p className="mt-0.5 text-[9px] leading-relaxed text-text-muted">
                            {session.remote_created ? 'Remote' : 'Local/LAN'} · Last seen {formatTime(session.last_seen_at)} · Expires {formatTime(session.expires_at)}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => void run(`revoke-${session.id}`, async isCurrent => {
                            const current = await revokeSession(session.id)
                            if (!isCurrent()) return
                            setNotice({ kind: 'success', text: current ? 'Signed out of this session.' : 'Session revoked.' })
                          })}
                          disabled={Boolean(busy)}
                          className="shrink-0 rounded-lg border border-border px-2 py-1 text-[9px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
                        >
                          {session.current ? 'Sign out' : 'Revoke'}
                        </button>
                      </div>
                    ))}
                    {sessions.length === 0 && !detailsLoading && <p className="text-[10px] text-text-muted">No active sessions were returned.</p>}
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => void run('revoke-others', async isCurrent => {
                        const count = await revokeAllSessions(true)
                        if (!isCurrent()) return
                        setNotice({ kind: 'success', text: `${count} other session${count === 1 ? '' : 's'} revoked.` })
                      })}
                      disabled={Boolean(busy) || !context.reauthenticated}
                      className="rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-40"
                    >
                      Revoke other sessions
                    </button>
                    <button
                      type="button"
                      onClick={() => void run('revoke-all', async isCurrent => {
                        await revokeAllSessions(false)
                        if (!isCurrent()) return
                        setNotice({ kind: 'success', text: 'All sessions revoked.' })
                      })}
                      disabled={Boolean(busy) || !context.reauthenticated}
                      className="rounded-lg border border-chip-red/50 px-3 py-2 text-[10px] font-semibold text-chip-red hover:bg-chip-red/10 disabled:opacity-40"
                    >
                      Revoke all and sign out
                    </button>
                  </div>
                </section>
              )}

              {selfService && (
                <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
                  <h3 className="text-xs font-semibold text-text-primary">Password and recovery</h3>
                  <form
                    className="mt-3"
                    onSubmit={event => {
                      event.preventDefault()
                      void run('password', async isCurrent => {
                        await changePassword(newPassword)
                        if (!isCurrent()) return
                        setNewPassword('')
                        setNotice({ kind: 'success', text: 'Password changed. Other sessions were revoked.' })
                      })
                    }}
                  >
                    <Field label="New password" value={newPassword} onChange={setNewPassword} type="password" autoComplete="new-password" required minLength={12} />
                    <button type="submit" disabled={Boolean(busy) || !context.reauthenticated} className="mt-2 w-full rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-40">
                      Change password
                    </button>
                  </form>
                  <button
                    type="button"
                    onClick={() => void run('codes', async isCurrent => {
                      const codes = await rotateRecoveryCodes()
                      if (!isCurrent()) return
                      setOneTimeCodes(codes)
                      setCodesLabel('New recovery codes')
                      setNotice({ kind: 'success', text: 'Previous recovery codes were replaced.' })
                    })}
                    disabled={Boolean(busy) || !context.reauthenticated}
                    className="mt-2 w-full rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-40"
                  >
                    Replace recovery codes
                  </button>
                </section>
              )}

              {accountAdmin && (
                <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
                  <div className="flex items-center gap-2">
                    <UserCog size={14} className="text-accent-blue" aria-hidden="true" />
                    <h3 className="text-xs font-semibold text-text-primary">User administration</h3>
                  </div>
                  {!context.reauthenticated ? (
                    <p className="mt-2 text-[10px] text-text-muted">Confirm your password above to view or change server accounts.</p>
                  ) : (
                    <>
                      <div className="mt-3 space-y-2">
                        {users.map(user => (
                          <div key={user.id} className="flex items-center gap-2 rounded-lg border border-border/80 bg-bg-primary/40 p-2.5">
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-[10px] font-semibold text-text-primary">{user.username}</p>
                              <p className="text-[9px] text-text-muted">{user.role}{user.has_email ? ' · recovery email recorded' : ''}</p>
                            </div>
                            <button
                              type="button"
                              onClick={() => void run(`user-${user.id}`, async isCurrent => {
                                await setUserDisabled(user.id, !user.disabled)
                                if (!isCurrent()) return
                                setNotice({ kind: 'success', text: `${user.username} ${user.disabled ? 'enabled' : 'disabled'}.` })
                              })}
                              disabled={Boolean(busy) || user.id === context.account!.id}
                              className="rounded-lg border border-border px-2 py-1 text-[9px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-40"
                            >
                              {user.disabled ? 'Enable' : 'Disable'}
                            </button>
                          </div>
                        ))}
                      </div>
                      <form
                        className="mt-3 border-t border-border pt-3"
                        onSubmit={event => {
                          event.preventDefault()
                          void run('create-user', async isCurrent => {
                            const result = await createUser({ username: managedUsername, password: managedPassword, email: managedEmail })
                            if (!isCurrent()) return
                            setManagedPassword('')
                            setManagedEmail('')
                            setManagedUsername('')
                            setOneTimeCodes(result.recovery_codes || [])
                            setCodesLabel(`Recovery codes for ${result.account.username}`)
                            setNotice({ kind: 'success', text: 'User account created.' })
                          })
                        }}
                      >
                        <div className="flex items-center gap-2">
                          <UserPlus size={13} className="text-accent-blue" aria-hidden="true" />
                          <h4 className="text-[10px] font-semibold text-text-primary">Create a user</h4>
                        </div>
                        <div className="mt-2 grid gap-3 sm:grid-cols-2">
                          <Field label="Username" value={managedUsername} onChange={setManagedUsername} autoComplete="off" required />
                          <Field label="Email (optional)" value={managedEmail} onChange={setManagedEmail} type="email" autoComplete="off" />
                          <div className="sm:col-span-2">
                            <Field label="Temporary password" value={managedPassword} onChange={setManagedPassword} type="password" autoComplete="new-password" required minLength={12} />
                          </div>
                        </div>
                        <button type="submit" disabled={Boolean(busy)} className="mt-2 w-full rounded-lg bg-accent-blue px-3 py-2 text-[10px] font-semibold text-white disabled:opacity-50">
                          Create user
                        </button>
                      </form>
                    </>
                  )}
                </section>
              )}

              <button
                type="button"
                onClick={() => void run('logout', async isCurrent => {
                  await logout()
                  if (!isCurrent()) return
                  clearSensitive()
                  setNotice({ kind: 'success', text: 'Signed out. Project and output access were not changed.' })
                })}
                disabled={Boolean(busy)}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-xs font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
              >
                {busy === 'logout' ? <Loader2 size={14} className="animate-spin" /> : <LogOut size={14} />}
                Sign out
              </button>
            </div>
          )}
          </div>
          )}
        </div>

        <footer className="shrink-0 border-t border-border px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-5">
          <p className="flex items-start gap-2 text-[9px] leading-relaxed text-text-muted">
            <Check size={12} className="mt-0.5 shrink-0 text-indicator-success" aria-hidden="true" />
            {activeTab === 'account' && accountsEnabled
              ? 'Account requests use same-origin secure cookies and are not saved in browser preferences. Email is optional and is never used as authentication by itself.'
              : 'Support details are read from this server. External links are offered only when the server marks an HTTPS provider as available.'}
          </p>
        </footer>
      </div>
    </div>,
    document.body,
    )}
  </>
}
