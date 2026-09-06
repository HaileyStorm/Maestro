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
import {
  AccountApiError,
  isAccountProjectAccessActive,
  registerAccount,
  type AccessContext,
} from '../../api/client'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'
import { useStore } from '../../stores/useStore'
import type { AccountProjectMigrationStatus } from '../../types'
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

const accountErrorMessages: Record<string, string> = {
  account_store_unavailable: 'Account access is temporarily unavailable.',
  account_store_capacity: 'Account storage is full. Free some disk space, then try again.',
  authentication_required: 'Sign in to continue.',
  invalid_credentials: 'The username or password was not accepted.',
  invalid_recovery: 'The recovery information was not accepted.',
  owner_required: 'An owner account must confirm this change.',
  reauth_required: 'Confirm your password before making this change.',
  rate_limited: 'Too many account attempts were made.',
  bootstrap_complete: 'The first owner account already exists. Refresh account status, then sign in.',
  invalid_nonce: 'This account form expired. Try the action again.',
  account_not_found: 'That account is no longer available.',
  session_not_found: 'That account session is no longer active.',
  invalid_username: 'Check the username and try again.',
  invalid_email: 'Check the optional email address and try again.',
  invalid_device_label: 'Check the device name and try again.',
  invalid_password: 'Check the password requirements and try again.',
  username_unavailable: 'That username is unavailable.',
  self_disable_rejected: 'The active owner account cannot disable itself.',
  project_migration_unavailable: 'Project setup is available only to a recently confirmed owner using Maestro directly on this computer.',
}

// Exported for deterministic copy-safety regression coverage.
// eslint-disable-next-line react-refresh/only-export-components
export function safeAccountErrorMessage(code: string, retryAfter = 0): string {
  const message = accountErrorMessages[code] || 'The account request could not be completed.'
  return retryAfter > 0
    ? `${message} Try again in about ${retryAfter} seconds.`
    : message
}

// Exported so status-specific fallback copy is exercised without rendering the drawer.
// eslint-disable-next-line react-refresh/only-export-components
export function safeAccountHttpErrorMessage(
  status: number,
  code = 'account_request_failed',
  retryAfter = 0,
  context: 'account' | 'project-migration' = 'account',
): string {
  if (context === 'project-migration' && code === 'project_migration_needs_attention') {
    const message = 'Some existing project folders need attention. Resolve each listed project on this computer, then retry. Removing a project is a separate action that Maestro will ask you to confirm. Account-based project filtering remains off, and existing project access stays unchanged.'
    return retryAfter > 0 ? `${message} Try again in about ${retryAfter} seconds.` : message
  }
  if (code !== 'account_request_failed') return safeAccountErrorMessage(code, retryAfter)
  if (context !== 'project-migration') return safeAccountErrorMessage(code, retryAfter)
  const message = status === 404
    ? 'Project setup is not available on this Maestro host.'
    : status === 423
      ? 'Project access changed while setup was running. Refresh project access, then try again.'
      : status === 503
        ? 'Project access is temporarily unavailable. Try again after Maestro is ready.'
        : status === 409
          ? 'Wait for current project activity to finish, then try again.'
          : status === 403
            ? 'Confirm the owner password and open Maestro directly on this computer, then try again.'
            : safeAccountErrorMessage(code)
  return retryAfter > 0 ? `${message} Try again in about ${retryAfter} seconds.` : message
}

function accountErrorMessage(error: unknown): string {
  if (!(error instanceof AccountApiError)) return 'The account request could not be completed.'
  return safeAccountHttpErrorMessage(error.status, error.code, error.retryAfter)
}

function projectMigrationErrorMessage(error: unknown): string {
  if (!(error instanceof AccountApiError)) return 'Existing project setup could not be refreshed.'
  return safeAccountHttpErrorMessage(
    error.status,
    error.code,
    error.retryAfter,
    'project-migration',
  )
}

function formatTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

function directLoopbackBrowser(): boolean {
  if (typeof window === 'undefined') return false
  const hostname = window.location.hostname.trim().toLowerCase().replace(/^\[|\]$/g, '')
  return hostname === 'localhost' || hostname === '::1' || /^127(?:\.\d{1,3}){3}$/.test(hostname)
}

// A sealed active cutover is monotonic. Keep a fresh server access projection
// authoritative even if the owner-only migration detail cache is older.
// eslint-disable-next-line react-refresh/only-export-components
export function isAccountProjectAccessActiveForDrawer(
  accessContext: AccessContext | null,
  projectMigration: AccountProjectMigrationStatus | null,
): boolean {
  return isAccountProjectAccessActive(accessContext)
    || isAccountProjectAccessActive(accessContext, projectMigration)
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
  const inputId = useId()
  const [passwordVisible, setPasswordVisible] = useState(false)
  const passwordField = type === 'password'
  return (
    <div className="block text-[10px] font-medium text-text-secondary">
      <label htmlFor={inputId}>{label}</label>
      <span className="relative mt-1 block">
        <input
          id={inputId}
          type={passwordField && passwordVisible ? 'text' : type}
          value={value}
          onChange={event => onChange(event.target.value)}
          autoComplete={autoComplete}
          required={required}
          minLength={minLength}
          placeholder={placeholder}
          tabIndex={0}
          className={`min-h-11 w-full rounded-lg border border-border bg-bg-primary px-3 py-2 text-xs text-text-primary outline-none transition-colors placeholder:text-text-muted focus:border-accent-blue focus:ring-1 focus:ring-accent-blue ${passwordField ? 'pr-12' : ''}`}
        />
        {passwordField && (
          <button
            type="button"
            onClick={() => setPasswordVisible(visible => !visible)}
            className="absolute inset-y-0 right-0 flex min-h-11 min-w-11 items-center justify-center rounded-r-lg text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent-blue"
            aria-label={passwordVisible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
            aria-pressed={passwordVisible}
          >
            <span className="text-[9px] font-semibold">{passwordVisible ? 'Hide' : 'Show'}</span>
          </button>
        )}
      </span>
    </div>
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
  const [savedAcknowledged, setSavedAcknowledged] = useState(false)
  const [saveNotice, setSaveNotice] = useState('')
  const codesText = codes.join('\n')

  useEffect(() => {
    setSavedAcknowledged(false)
    setSaveNotice('')
  }, [codes])

  const copyCodes = async () => {
    try {
      await navigator.clipboard.writeText(codesText)
      setSaveNotice('Copied. Store the codes somewhere private, then confirm below.')
    } catch {
      setSaveNotice('Copy was unavailable in this browser. Select the codes or download the file instead.')
    }
  }

  const downloadCodes = () => {
    let url = ''
    try {
      url = URL.createObjectURL(new Blob([`${codesText}\n`], { type: 'text/plain;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'maestro-recovery-codes.txt'
      link.click()
      setSaveNotice('Downloaded. Move the file to a private place, then confirm below.')
    } catch {
      setSaveNotice('Download was unavailable in this browser. Copy or select the codes instead.')
    } finally {
      if (url) URL.revokeObjectURL(url)
    }
  }

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
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => void copyCodes()}
              className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary"
            >
              Copy all
            </button>
            <button
              type="button"
              onClick={downloadCodes}
              className="flex min-h-11 items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary"
            >
              Download
            </button>
          </div>
          {saveNotice && <p className="mt-2 text-[9px] leading-relaxed text-text-muted" role="status">{saveNotice}</p>}
          <label className="mt-2 flex min-h-11 items-center gap-2 rounded-lg border border-border bg-bg-primary/40 px-3 py-2 text-[10px] leading-relaxed text-text-secondary">
            <input
              type="checkbox"
              checked={savedAcknowledged}
              onChange={event => setSavedAcknowledged(event.target.checked)}
              className="h-5 w-5 shrink-0 accent-accent-blue"
            />
            <span>I stored these recovery codes somewhere private.</span>
          </label>
          <button
            type="button"
            onClick={onDismiss}
            disabled={!savedAcknowledged}
            className="mt-2 min-h-11 w-full rounded-lg bg-accent-blue px-3 py-2 text-[10px] font-semibold text-cta-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Continue with saved codes
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

  const accountsEnabled = context?.enabled === true
  const authenticated = accountsEnabled && context?.authenticated === true
  const visibleLabel = !accountsEnabled
    ? 'Support'
    : authenticated
      ? 'Account & support'
      : 'Sign in'
  const accessibleLabel = !accountsEnabled
    ? 'Open support'
    : authenticated
      ? 'Open account and support'
      : 'Open sign in and account help'
  const TriggerIcon = !accountsEnabled
    ? HeartHandshake
    : authenticated
      ? UserRound
      : LogIn
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      data-responsive-dialog-trigger={`account-support:${compact ? 'mobile' : 'desktop'}`}
      aria-haspopup="dialog"
      aria-controls="account-support-drawer"
      aria-expanded={open}
      aria-label={accessibleLabel}
      className={`flex shrink-0 items-center justify-center gap-1.5 rounded-lg border border-border bg-bg-secondary text-text-secondary shadow-lg transition-colors hover:border-border-light hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
        compact ? 'h-11 w-11 p-0' : 'px-3 py-2 text-[11px] font-semibold'
      }`}
    >
      <TriggerIcon size={compact ? 18 : 14} aria-hidden="true" />
      {!compact && <span className="max-w-32 truncate">{visibleLabel}</span>}
    </button>
  )
}

export function AccountSupportDrawer({
  required = false,
  onAuthenticated,
}: {
  required?: boolean
  onAuthenticated?: () => void
} = {}) {
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
  const accountIdentityRef = useRef<string | null>(null)
  const oneTimeCodesIdentityRef = useRef<string | null>(null)
  const open = useStore(state => state.accountDrawerOpen)
  const setOpen = useStore(state => state.setAccountDrawerOpen)
  const context = useStore(state => state.accountContext)
  const accessContext = useStore(state => state.accessContext)
  const contextLoading = useStore(state => state.accountContextLoading)
  const projectMigration = useStore(state => state.accountProjectMigration)
  const projectMigrationLoading = useStore(state => state.accountProjectMigrationLoading)
  const sessions = useStore(state => state.accountSessions)
  const users = useStore(state => state.accountUsers)
  const detailsLoading = useStore(state => state.accountDetailsLoading)
  const loadContext = useStore(state => state.loadAccountContext)
  const loadProjectMigration = useStore(state => state.loadAccountProjectMigration)
  const migrateProjects = useStore(state => state.migrateAccountProjects)
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
  const [resumeAfterCodes, setResumeAfterCodes] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirmation, setPasswordConfirmation] = useState('')
  const [email, setEmail] = useState('')
  const [deviceLabel, setDeviceLabel] = useState('Browser')
  const [recoveryCode, setRecoveryCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPasswordConfirmation, setNewPasswordConfirmation] = useState('')
  const [reauthPassword, setReauthPassword] = useState('')
  const [managedUsername, setManagedUsername] = useState('')
  const [managedPassword, setManagedPassword] = useState('')
  const [managedEmail, setManagedEmail] = useState('')
  const [entryMode, setEntryMode] = useState<'login' | 'register' | 'recover'>('login')
  const [activeTab, setActiveTab] = useState<AccountSupportTab>(required ? 'account' : 'support')
  const accountsEnabled = context?.enabled === true
  const authenticated = accountsEnabled && context.authenticated && context.account !== null
  const publicRegistrationAvailable = context?.public_registration_available === true
  const accountEntryModes: Array<['login' | 'register' | 'recover', string]> = publicRegistrationAvailable
    ? [['login', 'Sign in'], ['register', 'Create account'], ['recover', 'Recover']]
    : [['login', 'Sign in'], ['recover', 'Recover']]
  const accountIdentity = context?.authenticated === true && context.account
    ? context.account.id
    : ''
  const selfService = authenticated && context.capabilities.includes('account.self')
  const accountAdmin = authenticated
    && context.capabilities.includes('accounts.admin')
    && context.capabilities.includes('services.admin')
  const migrationOwner = authenticated
    && context.account!.role === 'owner'
    && context.capabilities.includes('owner.admin')
  const directLoopback = accessContext?.remote === false
    && directLoopbackBrowser()
  const accountProjectAccessActive = isAccountProjectAccessActiveForDrawer(
    accessContext,
    projectMigration,
  )
  const migrationAvailable = migrationOwner && context.reauthenticated && directLoopback

  const clearSensitive = useCallback(() => {
    setPassword('')
    setPasswordConfirmation('')
    setEmail('')
    setRecoveryCode('')
    setNewPassword('')
    setNewPasswordConfirmation('')
    setReauthPassword('')
    setManagedPassword('')
    setManagedEmail('')
    setOneTimeCodes([])
    setResumeAfterCodes(false)
    oneTimeCodesIdentityRef.current = null
  }, [])

  const dismissOneTimeCodes = useCallback(() => {
    setOneTimeCodes([])
    oneTimeCodesIdentityRef.current = null
    if (!resumeAfterCodes) return
    setResumeAfterCodes(false)
    onAuthenticated?.()
  }, [onAuthenticated, resumeAfterCodes])

  const closeDrawer = useCallback(() => {
    if (required) return
    lifecycleRef.current.closed()
    clearSensitive()
    setBusy('')
    setNotice(null)
    setActiveTab('support')
    setEntryMode('login')
    setOpen(false)
  }, [clearSensitive, required, setOpen])

  const requestCloseDrawer = useCallback(() => {
    closeModalIfTop(document, dialogRef.current, closeDrawer)
  }, [closeDrawer])

  const selectTab = useCallback((tab: AccountSupportTab) => {
    if (required && tab !== 'account') return
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
  }, [activeTab, clearSensitive, required])

  useEffect(() => {
    if (required && open && activeTab !== 'account') setActiveTab('account')
  }, [activeTab, open, required])

  useEffect(() => {
    if (!publicRegistrationAvailable && entryMode === 'register') {
      setEntryMode('login')
    }
  }, [entryMode, publicRegistrationAvailable])

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
    errorMessage: (error: unknown) => string = accountErrorMessage,
  ) => {
    if (busy) return
    const isCurrent = lifecycleRef.current.operationLease()
    setBusy(name)
    setNotice(null)
    try {
      await action(isCurrent)
    } catch (error) {
      if (isCurrent()) setNotice({ kind: 'error', text: errorMessage(error) })
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
    const previousIdentity = accountIdentityRef.current
    accountIdentityRef.current = accountIdentity
    if (previousIdentity === null || previousIdentity === accountIdentity) return
    if (oneTimeCodesIdentityRef.current === accountIdentity) {
      oneTimeCodesIdentityRef.current = null
      setBusy('')
      return
    }
    clearSensitive()
    setBusy('')
    setNotice(null)
  }, [accountIdentity, clearSensitive])

  useEffect(() => {
    if (!open || activeTab !== 'account' || !migrationAvailable || accountProjectAccessActive) return
    const isCurrent = lifecycleRef.current.operationLease()
    void loadProjectMigration().catch(error => {
      if (isCurrent()) setNotice({ kind: 'error', text: projectMigrationErrorMessage(error) })
    })
  }, [accountProjectAccessActive, activeTab, loadProjectMigration, migrationAvailable, open])

  useEffect(() => {
    if (!open || !dialogRef.current) return
    const initialFocus = required ? accountTabRef.current : closeRef.current
    if (!initialFocus) return
    const nativeControls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
    ))
    const annotatedControls = nativeControls.filter(control => !control.hasAttribute('tabindex'))
    for (const control of annotatedControls) control.setAttribute('tabindex', '0')
    const uninstall = installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus,
      restoreFocus: focusReturnRef.current,
      appRoot: document.getElementById('root'),
      onClose: required ? () => {} : closeDrawer,
      priority: required ? 200 : 100,
    })
    return () => {
      for (const control of annotatedControls) control.removeAttribute('tabindex')
      uninstall()
    }
  }, [closeDrawer, open, required])

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

  return <>
    {focusReturnTarget}
    {createPortal(
    <div
      className={`fixed inset-0 ${required ? 'z-[200]' : 'z-[170]'} flex items-stretch justify-end`}
      style={{ paddingTop: 'env(safe-area-inset-top)', paddingBottom: 'env(safe-area-inset-bottom)' }}
    >
      {required ? (
        <div aria-hidden="true" className="absolute inset-0 bg-black/70 pointer-events-none" />
      ) : (
        <button
          type="button"
          tabIndex={-1}
          aria-label="Close Support panel"
          className="absolute inset-0 appearance-none border-0 bg-black/70 p-0"
          onClick={requestCloseDrawer}
        />
      )}
      <div
        id="account-support-drawer"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative z-10 flex h-full min-h-0 w-full flex-col overflow-hidden border-l border-border bg-bg-secondary shadow-2xl sm:max-w-xl"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-border px-4 py-4 sm:px-5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-blue/15 text-accent-blue">
            <HeartHandshake size={18} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-sm font-semibold text-text-primary">
              {required ? 'Sign in to Maestro' : accountsEnabled ? 'Account & support' : 'Support'}
            </h2>
            <p id={descriptionId} className="mt-0.5 text-[10px] leading-relaxed text-text-muted">
              {required
                ? 'Sign in before project names, uploads, or creative tools become available.'
                : accountsEnabled
                ? accountProjectAccessActive
                  ? 'Support Maestro Continuum or manage your account. Project access follows your account membership.'
                  : 'Support Maestro Continuum or manage your account. Existing project access may also depend on this browser or a project password.'
                : 'View optional ways to support Maestro. Support does not change access or available controls.'}
            </p>
          </div>
          {!required && <button
            ref={closeRef}
            type="button"
            onClick={requestCloseDrawer}
            aria-label="Close Support panel"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg p-0 text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue md:h-auto md:w-auto md:p-1.5"
          >
            <X size={17} aria-hidden="true" />
          </button>}
        </header>

        {accountsEnabled && (
          <div className={`grid shrink-0 ${required ? 'grid-cols-1' : 'grid-cols-2'} border-b border-border p-1`} role="tablist" aria-label="Support and account sections">
            {!required && <button
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
            </button>}
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
          tabIndex={activeTab === 'support' ? 0 : -1}
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
              <OneTimeCodes label={codesLabel} codes={oneTimeCodes} onDismiss={dismissOneTimeCodes} />
            </div>
          )}

          {!authenticated ? (
            <div className="space-y-4">
              {context.bootstrap_available === true && (
                <form
                  className="rounded-xl border border-accent-blue/50 bg-accent-blue/5 p-3"
                  onSubmit={event => {
                    event.preventDefault()
                    if (password !== passwordConfirmation) {
                      setNotice({ kind: 'error', text: 'The password confirmation does not match.' })
                      return
                    }
                    void run('bootstrap', async isCurrent => {
                      const result = await bootstrap({ username, password, email, deviceLabel })
                      if (!isCurrent() || !result) return
                      setPassword('')
                      setPasswordConfirmation('')
                      setEmail('')
                      const codes = result.recovery_codes || []
                      oneTimeCodesIdentityRef.current = codes.length > 0 ? result.account.id : null
                      setOneTimeCodes(codes)
                      setResumeAfterCodes(codes.length > 0 && Boolean(onAuthenticated))
                      setCodesLabel('Owner recovery codes')
                      setNotice({ kind: 'success', text: 'Owner account created and signed in.' })
                      if (codes.length === 0) onAuthenticated?.()
                    })
                  }}
                >
                  <div className="flex items-center gap-2">
                    <ShieldCheck size={15} className="text-accent-blue" aria-hidden="true" />
                    <h3 className="text-xs font-semibold text-text-primary">Create the first owner account</h3>
                  </div>
                  <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
                    For security, create the first owner account by opening Maestro directly on the computer where it is running.
                  </p>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <Field label="Username" value={username} onChange={setUsername} autoComplete="username" required />
                    <Field label="Device label" value={deviceLabel} onChange={setDeviceLabel} autoComplete="off" required />
                    <Field label="Password" value={password} onChange={setPassword} type="password" autoComplete="new-password" required minLength={8} />
                    <Field label="Confirm password" value={passwordConfirmation} onChange={setPasswordConfirmation} type="password" autoComplete="new-password" required minLength={8} />
                    <Field label="Email (optional)" value={email} onChange={setEmail} type="email" autoComplete="email" />
                  </div>
                  <button
                    type="submit"
                    disabled={Boolean(busy)}
                    className="mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-bg-active px-3 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-100"
                  >
                    {busy === 'bootstrap' ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
                    Create owner account
                  </button>
                </form>
              )}

              <div
                className={`grid ${publicRegistrationAvailable ? 'grid-cols-3' : 'grid-cols-2'} rounded-xl border border-border bg-bg-primary p-1`}
                role="group"
                aria-label="Account access"
              >
                {accountEntryModes.map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    aria-pressed={entryMode === mode}
                    onClick={() => {
                      clearSensitive()
                      setNotice(null)
                      setEntryMode(mode)
                    }}
                    className={`min-h-11 rounded-lg px-2 py-2 text-[10px] font-semibold ${entryMode === mode ? 'bg-bg-hover text-text-primary' : 'text-text-muted hover:text-text-primary'}`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {entryMode === 'login' && <form
                className="rounded-xl border border-border bg-bg-tertiary/30 p-3"
                onSubmit={event => {
                  event.preventDefault()
                  void run('login', async isCurrent => {
                    await login({ username, password, deviceLabel })
                    if (!isCurrent()) return
                    await loadSessions().catch(() => {})
                    if (!isCurrent()) return
                    setPassword('')
                    setNotice({ kind: 'success', text: 'Signed in.' })
                    onAuthenticated?.()
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
                  className="mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-bg-active px-3 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-100"
                >
                  {busy === 'login' ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}
                  Sign in
                </button>
              </form>}

              {entryMode === 'register' && publicRegistrationAvailable && (
                <form
                  className="rounded-xl border border-accent-blue/50 bg-accent-blue/5 p-3"
                  onSubmit={event => {
                    event.preventDefault()
                    if (password !== passwordConfirmation) {
                      setNotice({ kind: 'error', text: 'The password confirmation does not match.' })
                      return
                    }
                    void run('register', async isCurrent => {
                      const result = await registerAccount({ username, password, email, deviceLabel })
                      if (!isCurrent()) return
                      const codes = result.recovery_codes || []
                      oneTimeCodesIdentityRef.current = codes.length > 0 ? result.account.id : null
                      setOneTimeCodes(codes)
                      setResumeAfterCodes(codes.length > 0 && Boolean(onAuthenticated))
                      setCodesLabel('Your recovery codes')
                      await loadContext()
                      if (!isCurrent()) return
                      await loadSessions().catch(() => {})
                      if (!isCurrent()) return
                      setPassword('')
                      setPasswordConfirmation('')
                      setEmail('')
                      setNotice({ kind: 'success', text: 'Account created and signed in.' })
                      if (codes.length === 0) onAuthenticated?.()
                    })
                  }}
                >
                  <div className="flex items-center gap-2">
                    <UserPlus size={15} className="text-accent-blue" aria-hidden="true" />
                    <h3 className="text-xs font-semibold text-text-primary">Create account</h3>
                  </div>
                  <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
                    Your account starts with no projects. Projects you create are owned by this account.
                  </p>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <Field label="Username" value={username} onChange={setUsername} autoComplete="username" required />
                    <Field label="Device label" value={deviceLabel} onChange={setDeviceLabel} autoComplete="off" required />
                    <Field label="Password" value={password} onChange={setPassword} type="password" autoComplete="new-password" required minLength={8} />
                    <Field label="Confirm password" value={passwordConfirmation} onChange={setPasswordConfirmation} type="password" autoComplete="new-password" required minLength={8} />
                    <Field label="Email (optional)" value={email} onChange={setEmail} type="email" autoComplete="email" />
                  </div>
                  <button
                    type="submit"
                    disabled={Boolean(busy)}
                    className="mt-3 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-bg-active px-3 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-50"
                  >
                    {busy === 'register' ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
                    Create account
                  </button>
                </form>
              )}

              {entryMode === 'recover' && <form
                  className="rounded-xl border border-border bg-bg-tertiary/20 p-3"
                  onSubmit={event => {
                    event.preventDefault()
                    if (newPassword !== newPasswordConfirmation) {
                      setNotice({ kind: 'error', text: 'The new password confirmation does not match.' })
                      return
                    }
                    void run('recover', async isCurrent => {
                      const result = await recover({ username, recoveryCode, newPassword, deviceLabel })
                      if (!isCurrent() || !result) return
                      setRecoveryCode('')
                      setNewPassword('')
                      setNewPasswordConfirmation('')
                      await loadSessions().catch(() => {})
                      if (!isCurrent()) return
                      const codes = result.recovery_codes || []
                      oneTimeCodesIdentityRef.current = codes.length > 0 ? result.account.id : null
                      setOneTimeCodes(codes)
                      setResumeAfterCodes(codes.length > 0 && Boolean(onAuthenticated))
                      setCodesLabel('Replacement recovery codes')
                      setNotice({ kind: 'success', text: 'Account recovered and signed in.' })
                      if (codes.length === 0) onAuthenticated?.()
                    })
                  }}
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Username" value={username} onChange={setUsername} autoComplete="username" required />
                    <Field label="Device label" value={deviceLabel} onChange={setDeviceLabel} autoComplete="off" required />
                    <Field label="Recovery code" value={recoveryCode} onChange={setRecoveryCode} autoComplete="one-time-code" required />
                    <Field label="New password" value={newPassword} onChange={setNewPassword} type="password" autoComplete="new-password" required minLength={8} />
                    <Field label="Confirm new password" value={newPasswordConfirmation} onChange={setNewPasswordConfirmation} type="password" autoComplete="new-password" required minLength={8} />
                  </div>
                  <button
                    type="submit"
                    disabled={Boolean(busy)}
                    className="mt-3 min-h-11 w-full rounded-lg border border-border-light px-3 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-50"
                  >
                    Recover and sign in
                  </button>
                </form>}
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
                  {accountProjectAccessActive
                    ? 'Signing in identifies your account and grants access to projects assigned to it.'
                    : 'Signing in identifies your account. Access to existing projects may still depend on this browser or a project password.'}
                </p>
              </section>

              {migrationOwner && !accountProjectAccessActive && (
                <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3" aria-label="Existing project account setup">
                  <div className="flex items-center gap-2">
                    <ShieldCheck size={14} className="text-accent-blue" aria-hidden="true" />
                    <h3 className="flex-1 text-xs font-semibold text-text-primary">Connect existing projects</h3>
                    {migrationAvailable && (
                      <button
                        type="button"
                        onClick={() => void run(
                          'refresh-project-setup',
                          async () => { await loadProjectMigration() },
                          projectMigrationErrorMessage,
                        )}
                        disabled={Boolean(busy) || projectMigrationLoading}
                        aria-label="Refresh existing project setup"
                        className="rounded-lg p-1.5 text-text-muted hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
                      >
                        <RefreshCw size={13} className={projectMigrationLoading ? 'animate-spin' : ''} />
                      </button>
                    )}
                  </div>
                  {!directLoopback ? (
                    <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
                      To connect projects safely, open Maestro directly on the computer where it is running, then confirm the owner password.
                    </p>
                  ) : !context.reauthenticated ? (
                    <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
                      Confirm the owner password above before connecting existing projects.
                    </p>
                  ) : projectMigrationLoading && !projectMigration ? (
                    <p className="mt-2 text-[10px] text-text-muted">Checking existing project access…</p>
                  ) : projectMigration?.state === 'not_started' ? (
                    <>
                      <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
                        Existing projects are not connected to this owner account yet. Maestro will not make this change automatically.
                      </p>
                      <button
                        type="button"
                        onClick={() => void run('migrate-projects', async isCurrent => {
                          const status = await migrateProjects()
                          if (!isCurrent() || !status) return
                          if (status.needs_attention > 0) return
                          setNotice({
                            kind: 'success',
                            text: `Project access is ready for ${status.project_count} project${status.project_count === 1 ? '' : 's'}.`,
                          })
                        }, projectMigrationErrorMessage)}
                        disabled={Boolean(busy) || projectMigrationLoading}
                        className="mt-3 w-full rounded-lg bg-accent-blue px-3 py-2 text-xs font-semibold text-white hover:bg-accent-blue-hover disabled:opacity-50"
                      >
                        {busy === 'migrate-projects' ? 'Connecting projects…' : 'Connect existing projects to this owner'}
                      </button>
                    </>
                  ) : projectMigration?.state === 'needs_attention' ? (
                    <p className="mt-2 text-[10px] leading-relaxed text-indicator-warning">
                      Account-based project filtering is not enabled yet. {projectMigration.needs_attention} existing project folder{projectMigration.needs_attention === 1 ? '' : 's'} need attention. Resolve each listed project on this computer, then retry. Removing a project is a separate action that Maestro will ask you to confirm. Existing browser and project-password access stays unchanged.
                    </p>
                  ) : (
                    <p className="mt-2 text-[10px] leading-relaxed text-text-muted">
                      Existing project setup is unavailable. Refresh when Maestro is ready.
                    </p>
                  )}
                </section>
              )}

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
                  <p className="mt-1 text-[10px] text-text-muted">Confirm before changing passwords or recovery codes, signing out other account sessions, or managing users.</p>
                  <div className="mt-3">
                    <Field label="Current password" value={reauthPassword} onChange={setReauthPassword} type="password" autoComplete="current-password" required />
                  </div>
                  <button type="submit" disabled={Boolean(busy)} className="mt-3 w-full rounded-lg bg-bg-active px-3 py-2 text-xs font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-100">
                    Confirm password
                  </button>
                </form>
              )}

              {selfService && (
                <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
                  <div className="flex items-center gap-2">
                    <h3 className="flex-1 text-xs font-semibold text-text-primary">Account sessions</h3>
                    <button
                      type="button"
                      onClick={() => void run('refresh-sessions', async () => { await loadSessions() })}
                      disabled={Boolean(busy)}
                      aria-label="Refresh account sessions"
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
                            {session.device_label}{session.current ? ' · Current account session' : ''}
                          </p>
                          <p className="mt-0.5 text-[9px] leading-relaxed text-text-muted">
                            {session.remote_created ? 'Remote sign-in' : 'Direct or local-network sign-in'} · Last used {formatTime(session.last_seen_at)} · Account sign-in expires {formatTime(session.expires_at)}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => void run(`revoke-${session.id}`, async isCurrent => {
                            const current = await revokeSession(session.id)
                            if (!isCurrent()) return
                            setNotice({ kind: 'success', text: current ? 'Signed out of this account session.' : 'That account session was signed out.' })
                          })}
                          disabled={Boolean(busy)}
                          className="shrink-0 rounded-lg border border-border px-2 py-1 text-[9px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-50"
                        >
                          Sign out
                        </button>
                      </div>
                    ))}
                    {sessions.length === 0 && !detailsLoading && <p className="text-[10px] text-text-muted">No active account sessions found.</p>}
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    <button
                      type="button"
                      onClick={() => void run('revoke-others', async isCurrent => {
                        const count = await revokeAllSessions(true)
                        if (!isCurrent()) return
                        setNotice({ kind: 'success', text: `${count} other account session${count === 1 ? '' : 's'} signed out.` })
                      })}
                      disabled={Boolean(busy) || !context.reauthenticated}
                      className="rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-40"
                    >
                      Sign out other account sessions
                    </button>
                    <button
                      type="button"
                      onClick={() => void run('revoke-all', async isCurrent => {
                        await revokeAllSessions(false)
                        if (!isCurrent()) return
                        setNotice({ kind: 'success', text: 'All account sessions were signed out.' })
                      })}
                      disabled={Boolean(busy) || !context.reauthenticated}
                      className="rounded-lg border border-chip-red/50 px-3 py-2 text-[10px] font-semibold text-chip-red hover:bg-chip-red/10 disabled:opacity-40"
                    >
                      Sign out all account sessions
                    </button>
                  </div>
                  <p className="mt-2 text-[9px] leading-relaxed text-text-muted">
                    {accountProjectAccessActive
                      ? 'These controls end account membership access in the affected browsers.'
                      : 'These controls affect account sign-in only. Separate browser or project-password access is unchanged.'}
                  </p>
                </section>
              )}

              {selfService && (
                <section className="rounded-xl border border-border bg-bg-tertiary/20 p-3">
                  <h3 className="text-xs font-semibold text-text-primary">Password and recovery</h3>
                  <form
                    className="mt-3"
                    onSubmit={event => {
                      event.preventDefault()
                      if (newPassword !== newPasswordConfirmation) {
                        setNotice({ kind: 'error', text: 'The new password confirmation does not match.' })
                        return
                      }
                      void run('password', async isCurrent => {
                        await changePassword(newPassword)
                        if (!isCurrent()) return
                        setNewPassword('')
                        setNewPasswordConfirmation('')
                        setNotice({ kind: 'success', text: 'Password changed. Other sessions were revoked.' })
                      })
                    }}
                  >
                    <Field label="New password" value={newPassword} onChange={setNewPassword} type="password" autoComplete="new-password" required minLength={8} />
                    <div className="mt-2">
                      <Field label="Confirm new password" value={newPasswordConfirmation} onChange={setNewPasswordConfirmation} type="password" autoComplete="new-password" required minLength={8} />
                    </div>
                    <button type="submit" disabled={Boolean(busy) || !context.reauthenticated} className="mt-2 w-full rounded-lg border border-border px-3 py-2 text-[10px] font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-40">
                      Change password
                    </button>
                  </form>
                  <button
                    type="button"
                    onClick={() => void run('codes', async isCurrent => {
                      const codes = await rotateRecoveryCodes()
                      if (!isCurrent() || !codes) return
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
                    <h3 className="text-xs font-semibold text-text-primary">Manage users</h3>
                  </div>
                  {!context.reauthenticated ? (
                    <p className="mt-2 text-[10px] text-text-muted">Confirm your password above to view or change other accounts.</p>
                  ) : (
                    <>
                      <div className="mt-3 space-y-2">
                        {users.map(user => (
                          <div key={user.id} className="flex items-center gap-2 rounded-lg border border-border/80 bg-bg-primary/40 p-2.5">
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-[10px] font-semibold text-text-primary">{user.username}</p>
                              <p className="text-[9px] text-text-muted">
                                {user.role}{user.has_email ? ' · email recorded' : ''}
                                {user.id === context.account!.id ? ' · current owner account cannot be disabled' : ''}
                              </p>
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
                            if (!isCurrent() || !result) return
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
                            <Field label="Temporary password" value={managedPassword} onChange={setManagedPassword} type="password" autoComplete="new-password" required minLength={8} />
                          </div>
                        </div>
                        <button type="submit" disabled={Boolean(busy)} className="mt-2 w-full rounded-lg bg-bg-active px-3 py-2 text-[10px] font-semibold text-text-primary hover:bg-bg-hover disabled:opacity-100">
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
                  setNotice({
                    kind: 'success',
                    text: accountProjectAccessActive
                      ? 'Signed out. Project access from this browser now requires account sign-in.'
                      : 'Signed out. Any separate browser or project-password access remains unchanged.',
                  })
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
              ? 'Maestro protects account sign-in in this browser and does not save your password in browser preferences. Email is optional and cannot be used alone to sign in.'
              : 'Support details come from Maestro. External support links appear only when they are securely configured and available.'}
          </p>
        </footer>
      </div>
    </div>,
    document.body,
    )}
  </>
}
