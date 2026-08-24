import { useEffect, useState } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from './components/Sidebar/Sidebar'
import { MainContent } from './components/MainContent/MainContent'
import { SettingsDrawer } from './components/SettingsDrawer/SettingsDrawer'
import { LoraBrowser } from './components/LoraBrowser/LoraBrowser'
import { DirectorDashboard } from './components/DirectorDashboard/DirectorDashboard'
import { StorageDashboard } from './components/StorageDashboard/StorageDashboard'
import { RetakeDialog } from './components/RetakeDialog'
import { OomRecoveryBanner } from './components/OomRecoveryBanner'
import { DownloadStatusBanner } from './components/DownloadStatusBanner'
import { PreflightBanner } from './components/PreflightBanner'
import { WelcomeModal } from './components/WelcomeModal'
import { H3GenerationPlanDialog } from './components/H3GenerationPlanDialog'
import { RecipesOverlay } from './components/Recipes/RecipesOverlay'
import { WhatsNewButton, WhatsNewDialogHost } from './components/WhatsNewDialog'
import { AccountSupportButton, AccountSupportDrawer } from './components/AccountSupport/AccountSupportDrawer'
import { GlobalQueuePopover } from './components/GlobalQueuePopover'
import { useStore } from './stores/useStore'
import { useIsMobile } from './lib/useIsMobile'
import { POLL_INTERVAL_MS, useVisibilityPolling } from './lib/useVisibilityPolling'
import { PRODUCT_NAME, PRODUCT_NAME_VISUAL } from './lib/branding'
import * as api from './api/client'

const BOOTSTRAP_TIMEOUT_MS = 15_000

type BootstrapState = 'loading' | 'ready' | 'error' | 'account' | 'project'

function clearProtectedBootState(recovery: 'account' | 'project'): void {
  const current = useStore.getState()
  const account = current.accountContext ?? current.accessContext?.accounts ?? null
  const unauthenticatedAccount = account ? {
    ...account,
    authenticated: false,
    account: null,
    capabilities: [],
    reauthenticated: false,
  } : null
  useStore.setState({
    workspaces: [],
    activeWorkspace: '',
    browsingUploads: false,
    outputs: [],
    outputsTotal: 0,
    outputsLoading: false,
    selectedOutput: 0,
    selectedOutputMeta: null,
    selectedOutputMetaName: null,
    selectedOutputKeys: [],
    gallerySelectionMode: false,
    jobs: [],
    sampleCampaignPairs: [],
    isEnhancing: false,
    enhanceStatus: null,
    enhanceRequestScope: null,
    enhanceQueueCard: null,
    directorPreviewStatus: null,
    directorPreviewRequestScope: null,
    presets: [],
    presetsLoading: false,
    isGenerating: false,
    ...(recovery === 'account' ? {
      accountContext: unauthenticatedAccount,
      accessContext: current.accessContext ? {
        ...current.accessContext,
        accounts: unauthenticatedAccount ?? undefined,
      } : current.accessContext,
    } : {}),
  })
}

function bootstrapRecoveryFor(error: unknown): Exclude<BootstrapState, 'loading' | 'ready'> {
  return api.accessRecoveryKind(error) ?? 'error'
}

function bootstrapWithin<T>(promise: Promise<T>, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error(message)), BOOTSTRAP_TIMEOUT_MS)
    promise.then(
      value => {
        window.clearTimeout(timeout)
        resolve(value)
      },
      error => {
        window.clearTimeout(timeout)
        reject(error)
      },
    )
  })
}

function App() {
  const loadModels = useStore(s => s.loadModels)
  const loadAccessContext = useStore(s => s.loadAccessContext)
  const loadAccountContext = useStore(s => s.loadAccountContext)
  const loadWorkspaces = useStore(s => s.loadWorkspaces)
  const loadOutputs = useStore(s => s.loadOutputs)
  const reconnectJobs = useStore(s => s.reconnectJobs)
  const loadSystemConfig = useStore(s => s.loadSystemConfig)
  const loadServicesConfig = useStore(s => s.loadServicesConfig)
  const loadLlmStatus = useStore(s => s.loadLlmStatus)
  const loadLlmModels = useStore(s => s.loadLlmModels)
  const loadPipelineList = useStore(s => s.loadPipelineList)
  const toggleSidebar = useStore(s => s.toggleSidebar)
  const sidebarOpen = useStore(s => s.sidebarOpen)
  const llmLoading = useStore(s => s.llmLoading)
  const llmEnhancing = useStore(s => s.isEnhancing)
  const llmStatusLoading = useStore(s => s.llmStatus?.loading === true)
  const isMobile = useIsMobile()
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const remote = useStore(s => s.accessContext?.remote === true)
  const accessContext = useStore(s => s.accessContext)
  const accountContext = useStore(s => s.accountContext)
  const setAccountDrawerOpen = useStore(s => s.setAccountDrawerOpen)
  const workspaces = useStore(s => s.workspaces)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0)
  const [bootstrapState, setBootstrapState] = useState<BootstrapState>('loading')
  const [bootstrapError, setBootstrapError] = useState('')
  const [accountGateKind, setAccountGateKind] = useState<'needed' | 'expired'>('needed')
  const accountAuthenticationRequired = accessContext?.accounts?.enabled === true
    && accessContext.account_project_access_active === true
    && accountContext?.authenticated !== true
  const retryBootstrap = () => {
    setBootstrapState('loading')
    setBootstrapError('')
    setBootstrapAttempt(value => value + 1)
  }
  const finishAccountRecovery = () => {
    setAccountDrawerOpen(false)
    retryBootstrap()
  }

  useEffect(() => {
    const recoverAccess = (event: Event) => {
      const detail = (event as CustomEvent<{
        status?: api.AccessRecoveryStatus
        recovery?: api.AccessRecoveryKind
      }>).detail
      const status = detail?.status
      if (status !== 401 && status !== 403 && status !== 423) return
      const recovery = detail?.recovery === 'project' ? 'project' : 'account'
      clearProtectedBootState(recovery)
      setBootstrapError('')
      setBootstrapState(recovery)
      if (recovery === 'account') {
        setAccountGateKind('expired')
        setAccountDrawerOpen(true)
      }
    }
    window.addEventListener(api.ACCESS_RECOVERY_EVENT, recoverAccess)
    return () => window.removeEventListener(api.ACCESS_RECOVERY_EVENT, recoverAccess)
  }, [setAccountDrawerOpen])

  useEffect(() => {
    let cancelled = false
    void bootstrapWithin(
      loadAccessContext(false),
      `${PRODUCT_NAME} is taking too long to connect.`,
    ).then(async context => {
      if (context.accounts?.enabled === true) {
        await bootstrapWithin(
          loadAccountContext(false),
          `${PRODUCT_NAME} is taking too long to load your account.`,
        )
      }
      const workspacesLoaded = await bootstrapWithin(
        loadWorkspaces(),
        `${PRODUCT_NAME} is taking too long to load your projects.`,
      )
      if (!workspacesLoaded) {
        if (context.remote) {
          clearProtectedBootState('project')
          setBootstrapError('')
          setBootstrapState('project')
          return
        }
        throw new Error(`${PRODUCT_NAME} couldn't load your projects.`)
      }
      if (cancelled) return
      const workspaceState = useStore.getState()
      const protectedReadsReady = api.protectedProjectReadsReady(
        context,
        workspaceState.accountContext,
        workspaceState.workspaces,
        workspaceState.activeWorkspace,
        workspaceState.accountProjectMigration,
      )
      if (!protectedReadsReady) {
        const awaitingProjectSelection = Boolean(
          context.remote
          && api.isAccountProjectAccessActive(
            context,
            workspaceState.accountProjectMigration,
          )
          && workspaceState.accountContext?.authenticated === true
          && !workspaceState.activeWorkspace,
        )
        if (awaitingProjectSelection) {
          // A signed-in remote account with no selected project is a normal
          // post-restart and first-use state. Mount the gated shell so its
          // project picker can create/select an authorized project; do not
          // start project-scoped polling until that selection exists.
          loadModels()
          loadServicesConfig()
          loadLlmStatus()
          setBootstrapState('ready')
          return
        }
        const recovery = context.account_project_access_active === true
          && workspaceState.accountContext?.authenticated !== true
          ? 'account'
          : 'project'
        clearProtectedBootState(recovery)
        setBootstrapError('')
        setBootstrapState(recovery)
        if (recovery === 'account') {
          setAccountGateKind('needed')
          setAccountDrawerOpen(true)
        }
        return
      }
      loadModels()
      loadServicesConfig()
      loadLlmStatus()
      reconnectJobs()
      if (context.machine_controls || (context.remote && workspaceState.activeWorkspace)) {
        loadOutputs()
      }
      if (context.machine_controls) {
        loadSystemConfig()
        loadLlmModels()
        loadPipelineList()
      }
      setBootstrapState('ready')
    }).catch(error => {
      if (cancelled) return
      const recovery = bootstrapRecoveryFor(error)
      if (recovery === 'account' || recovery === 'project') {
        clearProtectedBootState(recovery)
        setBootstrapError('')
        setBootstrapState(recovery)
        if (recovery === 'account') {
          setAccountGateKind('expired')
          setAccountDrawerOpen(true)
        }
        return
      }
      setBootstrapError(`${PRODUCT_NAME} couldn't connect. Check that it is running, then try again.`)
      setBootstrapState('error')
    })
    return () => { cancelled = true }
  }, [bootstrapAttempt, loadAccessContext, loadAccountContext, loadModels, loadOutputs, loadSystemConfig, loadServicesConfig, loadLlmStatus, loadLlmModels, loadPipelineList, loadWorkspaces, reconnectJobs, setAccountDrawerOpen])

  useEffect(() => {
    if (bootstrapState === 'ready' && accountAuthenticationRequired) {
      setAccountDrawerOpen(true)
    }
  }, [accountAuthenticationRequired, bootstrapState, setAccountDrawerOpen])

  // Backend-driven load/enhance transitions stay responsive; steady state is
  // a low-rate safety refresh. Hidden tabs make no baseline LLM requests.
  const llmTransitionActive = llmLoading || llmEnhancing || llmStatusLoading
  useVisibilityPolling(
    () => loadLlmStatus(),
    llmTransitionActive
      ? POLL_INTERVAL_MS.llmActiveVisible
      : POLL_INTERVAL_MS.llmIdleVisible,
    { enabled: bootstrapState === 'ready', immediate: false },
  )

  // Establish the signed Maestro session cookie before mounting any child
  // component. Several children poll immediately; allowing those requests to
  // race the first response can leave a just-uploaded reference owned by a
  // different session than the generation request that follows it.
  if (bootstrapState !== 'ready') {
    const accountRecovery = bootstrapState === 'account'
    const projectRecovery = bootstrapState === 'project'
    return (
      <main className="flex h-full w-full items-center justify-center bg-bg-primary px-6 text-text-primary">
        <div className="w-full max-w-sm rounded-xl border border-border bg-bg-secondary p-6 text-center shadow-xl">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-accent-blue text-lg font-bold text-white">
            M
          </div>
          <h1 className="text-base font-semibold" aria-label={bootstrapState === 'loading'
            ? `Connecting to ${PRODUCT_NAME}`
            : accountRecovery
              ? 'Sign in to continue'
              : projectRecovery
                ? 'Choose a project to continue'
                : `${PRODUCT_NAME} couldn't open`}>
            <span aria-hidden="true">
              {bootstrapState === 'loading'
                ? `Connecting to ${PRODUCT_NAME_VISUAL}…`
                : accountRecovery
                  ? 'Sign in to continue'
                  : projectRecovery
                    ? 'Choose a project to continue'
                    : `${PRODUCT_NAME_VISUAL} couldn't open`}
            </span>
          </h1>
          {bootstrapState === 'loading' && (
            <p className="mt-2 text-sm text-text-secondary">Checking your connection and projects.</p>
          )}
          {accountRecovery && (
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              {accountGateKind === 'expired'
                ? 'Your session is no longer valid. Sign in again to restore the projects available to your account.'
                : 'Sign in to open your projects and creative tools.'}
            </p>
          )}
          {projectRecovery && (
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              Project access changed. Try again, or sign in again if your access was updated.
            </p>
          )}
          {bootstrapState === 'error' && (
            <p className="mt-2 text-sm text-text-secondary">{bootstrapError}</p>
          )}
          {bootstrapState !== 'loading' && (
            <div className="mt-4 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={retryBootstrap}
                className="min-h-11 rounded-lg bg-accent-blue px-4 py-2 text-sm font-medium text-white hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
              >
                Try again
              </button>
              {projectRecovery && (
                <button
                  type="button"
                  onClick={() => setAccountDrawerOpen(true)}
                  className="min-h-11 rounded-lg border border-border px-4 py-2 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
                >
                  Open sign-in
                </button>
              )}
            </div>
          )}
        </div>
        {(accountRecovery || projectRecovery) && (
          <AccountSupportDrawer
            required={accountRecovery}
            onAuthenticated={accountRecovery ? finishAccountRecovery : undefined}
          />
        )}
      </main>
    )
  }

  const remoteProjectRequired = remote && !api.protectedProjectReadsReady(
    accessContext,
    accountContext,
    workspaces,
    activeWorkspace,
  )

  return (
    <div className="flex min-w-0 flex-col md:flex-row h-full w-full bg-bg-primary overflow-hidden">
      {/* Mobile header */}
      {isMobile && (
        <header className="grid h-12 shrink-0 grid-cols-[2.75rem_minmax(0,1fr)_5.5rem] items-center border-b border-border bg-bg-secondary px-1 sm:px-2">
          {!remoteProjectRequired ? <button
            type="button"
            onClick={toggleSidebar}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            aria-label={sidebarOpen ? 'Close creative workspace menu' : 'Open Generate, Director, and References menu'}
            aria-expanded={sidebarOpen}
            aria-controls="maestro-mobile-sidebar"
          >
            <Menu aria-hidden="true" size={20} />
          </button> : <WhatsNewButton compact />}
          <div className="flex min-w-0 items-center gap-2 px-2">
            <img aria-hidden="true" src="/maestro.svg" alt="" className="h-6 w-6 shrink-0" />
            <div className="min-w-0">
              <span className="sr-only">{PRODUCT_NAME}</span>
              <span aria-hidden="true" className="block truncate text-[11px] font-semibold leading-tight tracking-tight">{PRODUCT_NAME_VISUAL}</span>
            </div>
          </div>
          <div className="flex items-center justify-self-end">
            <GlobalQueuePopover iconSize={20} panelAlign="header-edge" />
            <AccountSupportButton compact />
          </div>
        </header>
      )}

      <Sidebar />
      <MainContent />
      {machineControls && <SettingsDrawer />}
      {machineControls && <LoraBrowser />}
      <DirectorDashboard />
      {machineControls && <StorageDashboard />}
      <RecipesOverlay />
      <RetakeDialog />
      <H3GenerationPlanDialog />
      <WhatsNewDialogHost />
      {!isMobile && (
        <div className="fixed right-3 top-3 z-40 flex items-center gap-1">
          <GlobalQueuePopover iconSize={16} />
          <AccountSupportButton />
        </div>
      )}
      <AccountSupportDrawer required={accountAuthenticationRequired} />
      {/* OomRecoveryBanner is a fixed-position overlay — renders nothing
          unless the latest job/pipeline failure has oom_info attached.
          Lives at the App root so it floats above whichever screen the
          user is looking at when their generation OOMs. */}
      <OomRecoveryBanner />
      {/* PreflightBanner — fixed top overlay shown once on startup if the
          environment is missing ffmpeg / CUDA or low on disk. Renders
          nothing when everything checks out. */}
      {machineControls && <PreflightBanner />}
      {/* DownloadStatusBanner — fixed bottom-right overlay, polls quickly
          only during an active transfer. Renders nothing unless
          a model file is being downloaded. Highlights stalled
          downloads in amber so users know the system is recovering
          rather than frozen. */}
      {machineControls && <DownloadStatusBanner />}
      {/* A remote browser must choose/unlock a project before optional
          orientation can cover the required project dialog. */}
      {!remoteProjectRequired && <WelcomeModal />}
    </div>
  )
}

export default App
