import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { Check, Copy, Loader2, RefreshCw, Share2, Trash2, UserPlus, Users, X } from 'lucide-react'
import {
  addProjectMember,
  AccountApiError,
  fetchProjectMembers,
  isDirectLoopbackHostname,
  removeProjectMember,
  setProjectMember,
} from '../../api/client'
import { copyTextToClipboard } from '../../lib/clipboard'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'
import type {
  ProjectAccessProjection,
  ProjectAccessRole,
} from '../../types'

const ROLE_OPTIONS: ReadonlyArray<{
  role: ProjectAccessRole
  label: string
  summary: string
}> = [
  { role: 'viewer', label: 'Viewer', summary: 'Can open and view the project and its outputs.' },
  { role: 'editor', label: 'Editor', summary: 'Can view, edit, and generate in the project.' },
  { role: 'owner', label: 'Owner', summary: 'Full project control, including members and deletion.' },
]

export type StudioLinkScope = 'public' | 'lan' | 'loopback' | 'unavailable'

export interface StudioLinkPresentation {
  url: string
  scope: StudioLinkScope
  source: 'configured' | 'browser' | 'none'
  copyEnabled: boolean
  label: string
  description: string
}

function parsedStudioUrl(value: string): URL | null {
  if (!value || value !== value.trim()) return null
  try {
    const url = new URL(value)
    if (
      (url.protocol !== 'http:' && url.protocol !== 'https:')
      || url.username !== ''
      || url.password !== ''
      || url.search !== ''
      || url.hash !== ''
    ) return null
    return url
  } catch {
    return null
  }
}

function lanHostname(hostname: string): boolean {
  const host = hostname.trim().toLowerCase().replace(/^\[|\]$/g, '')
  if (host.endsWith('.local') || (!host.includes('.') && !host.includes(':'))) return true
  if (/^10(?:\.\d{1,3}){3}$/.test(host)) return true
  if (/^192\.168(?:\.\d{1,3}){2}$/.test(host)) return true
  if (/^169\.254(?:\.\d{1,3}){2}$/.test(host)) return true
  const private172 = host.match(/^172\.(\d{1,3})(?:\.\d{1,3}){2}$/)
  if (private172 && Number(private172[1]) >= 16 && Number(private172[1]) <= 31) return true
  return /^(?:fc|fd|fe80):/i.test(host)
}

// Exported for deterministic reachability copy and browser-fallback coverage.
// eslint-disable-next-line react-refresh/only-export-components
export function projectStudioLink(
  configuredUrl: string,
  browserUrl: string,
): StudioLinkPresentation {
  const configured = parsedStudioUrl(configuredUrl)
  const browser = parsedStudioUrl(browserUrl)
  const selected = configured ?? browser
  const source = configured ? 'configured' : browser ? 'browser' : 'none'
  if (!selected) return {
    url: '',
    scope: 'unavailable',
    source,
    copyEnabled: false,
    label: 'Studio link unavailable',
    description: 'Open Maestro from a configured public or LAN address before sharing it.',
  }
  if (selected.hostname === '0.0.0.0' || isDirectLoopbackHostname(selected.hostname)) return {
    url: selected.href,
    scope: 'loopback',
    source,
    copyEnabled: false,
    label: 'This-computer link',
    description: 'This address works only on this computer. Open Maestro from its configured public address or a LAN address before sharing.',
  }
  if (lanHostname(selected.hostname)) return {
    url: selected.href,
    scope: 'lan',
    source,
    copyEnabled: true,
    label: 'LAN Studio link',
    description: 'This address works only for people on the same local network. They must still sign in and have project membership.',
  }
  return {
    url: selected.href,
    scope: 'public',
    source,
    copyEnabled: true,
    label: 'Public Studio link',
    description: 'This opens Maestro’s sign-in screen. It is not a public project bearer link and grants no project access by itself.',
  }
}

// Exported for focused contract coverage without normalizing or searching a directory.
// eslint-disable-next-line react-refresh/only-export-components
export function exactProjectUsername(username: string): string | null {
  return username === username.trim() && username.length >= 3 && username.length <= 64
    ? username
    : null
}

// eslint-disable-next-line react-refresh/only-export-components
export function projectAccessErrorMessage(error: unknown): string {
  if (!(error instanceof AccountApiError)) {
    return 'Project access could not be updated. Refresh and try again.'
  }
  if (error.status === 403) {
    return 'Confirm your password in Account, then reopen Share project and try again.'
  }
  if (error.status === 409) return error.message
  if (error.status === 404) return 'This project or account is no longer available. Refresh and try again.'
  if (error.status === 503) return 'Project access is temporarily unavailable. Try again after Maestro is ready.'
  return error.message || 'Project access could not be updated. Refresh and try again.'
}

interface ProjectAccessPanelProps {
  open: boolean
  workspace: string
  recentlyReauthenticated: boolean
  configuredStudioUrl: string
  browserStudioUrl: string
  restoreFocus: HTMLElement | null
  onClose: () => void
}

export function ProjectAccessPanel({
  open,
  workspace,
  recentlyReauthenticated,
  configuredStudioUrl,
  browserStudioUrl,
  restoreFocus,
  onClose,
}: ProjectAccessPanelProps) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const [projection, setProjection] = useState<ProjectAccessProjection | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [busyAccountId, setBusyAccountId] = useState<string | null>(null)
  const [username, setUsername] = useState('')
  const [newRole, setNewRole] = useState<ProjectAccessRole>('viewer')
  const [removeTarget, setRemoveTarget] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const studioLink = projectStudioLink(configuredStudioUrl, browserStudioUrl)

  const requestClose = useCallback(() => {
    closeModalIfTop(document, dialogRef.current, onClose)
  }, [onClose])

  useEffect(() => {
    if (!open || !dialogRef.current || !closeRef.current) return
    return installModalFocus({
      document,
      dialog: dialogRef.current,
      initialFocus: closeRef.current,
      restoreFocus,
      appRoot: document.getElementById('root'),
      onClose,
      priority: 165,
    })
  }, [onClose, open, restoreFocus])

  useEffect(() => {
    if (!open) return
    let current = true
    setProjection(null)
    setLoadError(null)
    setOperationError(null)
    setRemoveTarget(null)
    setLoading(true)

    void fetchProjectMembers(workspace)
      .then(result => {
        if (current) setProjection(result)
      })
      .catch(error => {
        if (current) setLoadError(projectAccessErrorMessage(error))
      })
      .finally(() => {
        if (current) setLoading(false)
      })

    return () => { current = false }
  }, [open, reloadToken, workspace])

  const applyMutation = useCallback(async (
    accountId: string,
    mutation: (revision: number) => Promise<ProjectAccessProjection>,
  ): Promise<boolean> => {
    if (!projection || busyAccountId !== null || !recentlyReauthenticated) return false
    setBusyAccountId(accountId)
    setOperationError(null)
    try {
      const updated = await mutation(projection.revision)
      setProjection(updated)
      setRemoveTarget(null)
      return true
    } catch (error) {
      setOperationError(projectAccessErrorMessage(error))
      return false
    } finally {
      setBusyAccountId(null)
    }
  }, [busyAccountId, projection, recentlyReauthenticated])

  const submitMember = useCallback((event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const exactUsername = exactProjectUsername(username)
    if (!exactUsername) {
      setOperationError('Enter the exact username with no extra spaces.')
      return
    }
    void applyMutation(
      `username:${exactUsername}`,
      revision => addProjectMember(workspace, exactUsername, newRole, revision),
    ).then(saved => {
      if (saved) setUsername('')
    })
  }, [applyMutation, newRole, username, workspace])

  const copyStudioLink = useCallback(async () => {
    if (!studioLink.copyEnabled || !studioLink.url) return
    const copied = await copyTextToClipboard(studioLink.url)
    setCopyState(copied ? 'copied' : 'failed')
    window.setTimeout(() => setCopyState('idle'), 1800)
  }, [studioLink.copyEnabled, studioLink.url])

  if (!open) return null

  const managementDisabled = !recentlyReauthenticated
    || projection === null
    || busyAccountId !== null

  return createPortal(
    <div
      className="fixed inset-0 z-[175] flex items-end justify-center sm:items-center sm:p-6"
      style={{
        paddingTop: 'env(safe-area-inset-top)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      <button
        type="button"
        tabIndex={-1}
        aria-label="Close Share project"
        className="absolute inset-0 appearance-none border-0 bg-black/70 p-0"
        onClick={requestClose}
      />
      <div
        ref={dialogRef}
        data-project-access-panel
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="relative flex min-h-0 w-full min-w-0 flex-col overflow-hidden rounded-t-2xl border border-border bg-bg-secondary shadow-2xl sm:max-w-2xl sm:rounded-2xl"
        style={{ maxHeight: 'calc(100dvh - env(safe-area-inset-top) - env(safe-area-inset-bottom))' }}
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-border px-4 py-4 sm:px-5">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-blue/15 text-accent-blue">
            <Users size={19} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="break-words text-sm font-semibold text-text-primary">
              Share {workspace}
            </h2>
            <p id={descriptionId} className="mt-1 text-[11px] leading-relaxed text-text-muted">
              Give an existing Maestro account access to this project. No email invite or public project link is created.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={requestClose}
            aria-label="Close Share project"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 sm:px-5">
          <section
            data-studio-link-scope={studioLink.scope}
            className="rounded-xl border border-accent-blue/25 bg-accent-blue/5 p-3"
            aria-labelledby={`${titleId}-studio-link`}
          >
            <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <h3 id={`${titleId}-studio-link`} className="flex items-center gap-2 text-xs font-semibold text-text-primary">
                  <Share2 size={15} className="text-accent-blue" aria-hidden="true" />
                  {studioLink.label}
                </h3>
                <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
                  {studioLink.description}
                </p>
              </div>
              <button
                type="button"
                data-copy-studio-link
                disabled={!studioLink.copyEnabled}
                onClick={() => void copyStudioLink()}
                className="flex h-11 shrink-0 items-center justify-center gap-2 rounded-lg border border-accent-blue/35 px-3 text-xs font-medium text-accent-blue hover:bg-accent-blue/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50"
              >
                {copyState === 'copied' ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
                {copyState === 'copied'
                  ? 'Copied'
                  : copyState === 'failed'
                    ? 'Copy failed'
                    : studioLink.copyEnabled ? 'Copy studio link' : 'Not shareable'}
              </button>
            </div>
          </section>

          {!recentlyReauthenticated && (
            <div role="alert" className="mt-4 rounded-xl border border-amber-300/30 bg-amber-300/10 p-3 text-xs leading-relaxed text-amber-100">
              Confirm your password in Account before adding, changing, or removing project members.
            </div>
          )}

          <section className="mt-5" aria-labelledby={`${titleId}-members`}>
            <div className="flex min-w-0 items-center gap-2">
              <div className="min-w-0 flex-1">
                <h3 id={`${titleId}-members`} className="text-xs font-semibold text-text-primary">Current members</h3>
                <p className="mt-0.5 text-[10px] text-text-muted">
                  {projection ? `Revision ${projection.revision}` : 'Loading the current project access list.'}
                </p>
              </div>
              <button
                type="button"
                disabled={loading || busyAccountId !== null}
                onClick={() => setReloadToken(token => token + 1)}
                aria-label="Refresh project members"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-wait disabled:opacity-50"
              >
                <RefreshCw size={16} className={loading ? 'animate-spin' : ''} aria-hidden="true" />
              </button>
            </div>

            {loading && (
              <div role="status" className="mt-3 flex min-h-20 items-center justify-center gap-2 rounded-xl border border-border text-xs text-text-muted">
                <Loader2 size={16} className="animate-spin" aria-hidden="true" /> Loading members…
              </div>
            )}
            {loadError && <div role="alert" className="mt-3 rounded-xl border border-red-400/30 bg-red-400/10 p-3 text-xs text-red-200">{loadError}</div>}
            {!loading && projection && projection.members.length === 0 && (
              <div className="mt-3 rounded-xl border border-border p-4 text-center text-xs text-text-muted">No members are available.</div>
            )}
            {!loading && projection && projection.members.length > 0 && (
              <ul className="mt-3 space-y-2">
                {projection.members.map(member => (
                  <li key={member.account_id} className="min-w-0 rounded-xl border border-border bg-bg-primary/40 p-3">
                    <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center">
                      <div className="min-w-0 flex-1">
                        <div className="break-words text-xs font-medium text-text-primary">{member.username}</div>
                        <div className="mt-0.5 text-[10px] leading-relaxed text-text-muted">
                          {ROLE_OPTIONS.find(option => option.role === member.role)?.summary}
                        </div>
                      </div>
                      <div className="flex min-w-0 gap-2">
                        <label className="min-w-0 flex-1 sm:w-32 sm:flex-none">
                          <span className="sr-only">Role for {member.username}</span>
                          <select
                            value={member.role}
                            disabled={managementDisabled || busyAccountId === member.account_id}
                            onChange={event => void applyMutation(
                              member.account_id,
                              revision => setProjectMember(
                                workspace,
                                member.account_id,
                                event.target.value as ProjectAccessRole,
                                revision,
                              ),
                            )}
                            className="h-11 w-full rounded-lg border border-border bg-bg-primary px-2 text-xs text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {ROLE_OPTIONS.map(option => <option key={option.role} value={option.role}>{option.label}</option>)}
                          </select>
                        </label>
                        <button
                          type="button"
                          disabled={managementDisabled || busyAccountId === member.account_id}
                          onClick={() => {
                            if (removeTarget !== member.account_id) {
                              setRemoveTarget(member.account_id)
                              return
                            }
                            void applyMutation(
                              member.account_id,
                              revision => removeProjectMember(workspace, member.account_id, revision),
                            )
                          }}
                          className={`flex h-11 min-w-11 shrink-0 items-center justify-center gap-1 rounded-lg border px-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50 ${removeTarget === member.account_id ? 'border-red-400/50 bg-red-400/10 text-red-200' : 'border-border text-text-muted hover:text-red-300'}`}
                          aria-label={removeTarget === member.account_id ? `Confirm removing ${member.username}` : `Remove ${member.username}`}
                        >
                          {busyAccountId === member.account_id
                            ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                            : <Trash2 size={15} aria-hidden="true" />}
                          <span className="hidden sm:inline">{removeTarget === member.account_id ? 'Confirm' : 'Remove'}</span>
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mt-5 rounded-xl border border-border p-3" aria-labelledby={`${titleId}-add`}>
            <h3 id={`${titleId}-add`} className="flex items-center gap-2 text-xs font-semibold text-text-primary">
              <UserPlus size={15} className="text-accent-blue" aria-hidden="true" />
              Add or update a member
            </h3>
            <p className="mt-1 text-[10px] leading-relaxed text-text-muted">
              Enter an existing account’s exact username. Maestro does not show suggestions or expose an account directory here.
            </p>
            <form
              data-project-member-form
              className="mt-3 grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_8rem_auto]"
              onSubmit={submitMember}
            >
              <label className="min-w-0">
                <span className="sr-only">Exact account username</span>
                <input
                  data-project-member-username
                  type="text"
                  value={username}
                  disabled={managementDisabled}
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  placeholder="Exact username"
                  onChange={event => setUsername(event.target.value)}
                  className="h-11 w-full min-w-0 rounded-lg border border-border bg-bg-primary px-3 text-xs text-text-primary placeholder:text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50"
                />
              </label>
              <label>
                <span className="sr-only">Project role</span>
                <select
                  data-project-member-role
                  value={newRole}
                  disabled={managementDisabled}
                  onChange={event => setNewRole(event.target.value as ProjectAccessRole)}
                  className="h-11 w-full rounded-lg border border-border bg-bg-primary px-2 text-xs text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {ROLE_OPTIONS.map(option => <option key={option.role} value={option.role}>{option.label}</option>)}
                </select>
              </label>
              <button
                type="submit"
                disabled={managementDisabled || username.trim().length === 0}
                className="flex h-11 items-center justify-center gap-2 rounded-lg bg-accent-blue px-4 text-xs font-semibold text-white hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue focus-visible:ring-offset-2 focus-visible:ring-offset-bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busyAccountId ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <UserPlus size={15} aria-hidden="true" />}
                Save access
              </button>
            </form>
            {operationError && <div role="alert" className="mt-3 rounded-lg border border-red-400/30 bg-red-400/10 p-2 text-xs text-red-200">{operationError}</div>}
          </section>

          <section className="mt-5" aria-labelledby={`${titleId}-roles`}>
            <h3 id={`${titleId}-roles`} className="text-xs font-semibold text-text-primary">What each role can do</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {ROLE_OPTIONS.map(option => (
                <div key={option.role} className="rounded-xl border border-border bg-bg-primary/30 p-3">
                  <div className="text-xs font-medium text-text-primary">{option.label}</div>
                  <p className="mt-1 text-[10px] leading-relaxed text-text-muted">{option.summary}</p>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>,
    document.body,
  )
}
