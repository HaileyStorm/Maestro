import { useEffect, useState } from 'react'
import { Menu, Settings } from 'lucide-react'
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
import { useStore } from './stores/useStore'
import { useIsMobile } from './lib/useIsMobile'
import * as api from './api/client'

const BOOTSTRAP_TIMEOUT_MS = 15_000

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
  const loadOutputs = useStore(s => s.loadOutputs)
  const reconnectJobs = useStore(s => s.reconnectJobs)
  const loadSystemConfig = useStore(s => s.loadSystemConfig)
  const loadServicesConfig = useStore(s => s.loadServicesConfig)
  const loadLlmStatus = useStore(s => s.loadLlmStatus)
  const loadLlmModels = useStore(s => s.loadLlmModels)
  const loadPipelineList = useStore(s => s.loadPipelineList)
  const toggleSidebar = useStore(s => s.toggleSidebar)
  const setSidebarOpen = useStore(s => s.setSidebarOpen)
  const toggleSettings = useStore(s => s.toggleSettings)
  const appVersion = useStore(s => s.systemConfig?.app_version)
  const isMobile = useIsMobile()
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const remote = useStore(s => s.accessContext?.remote === true)
  const workspaces = useStore(s => s.workspaces)
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0)
  const [bootstrapState, setBootstrapState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [bootstrapError, setBootstrapError] = useState('')

  useEffect(() => {
    let cancelled = false
    void bootstrapWithin(
      loadAccessContext(),
      'Maestro did not respond while checking access.',
    ).then(async context => {
      const workspaceState = await bootstrapWithin(
        api.fetchWorkspaces(),
        'Maestro did not respond while loading projects.',
      )
      if (cancelled) return
      useStore.setState({
        workspaces: workspaceState.workspaces,
        activeWorkspace: workspaceState.active,
        selectedOutputKeys: [],
        gallerySelectionMode: false,
      })
      loadModels()
      loadServicesConfig()
      loadLlmStatus()
      reconnectJobs()
      if (context.machine_controls || (context.remote && workspaceState.active)) {
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
      setBootstrapError(error instanceof Error ? error.message : 'Maestro did not respond')
      setBootstrapState('error')
    })
    return () => { cancelled = true }
  }, [bootstrapAttempt, loadAccessContext, loadModels, loadOutputs, loadSystemConfig, loadServicesConfig, loadLlmStatus, loadLlmModels, loadPipelineList, reconnectJobs])

  // Poll LLM status to stay in sync with backend auto-load/unload
  useEffect(() => {
    if (bootstrapState !== 'ready') return
    const interval = setInterval(loadLlmStatus, 15000)
    return () => clearInterval(interval)
  }, [bootstrapState, loadLlmStatus])

  // Establish the signed Maestro session cookie before mounting any child
  // component. Several children poll immediately; allowing those requests to
  // race the first response can leave a just-uploaded reference owned by a
  // different session than the generation request that follows it.
  if (bootstrapState !== 'ready') {
    return (
      <div className="flex h-full w-full items-center justify-center bg-bg-primary px-6 text-text-primary">
        <div className="w-full max-w-sm rounded-xl border border-border bg-bg-secondary p-6 text-center shadow-xl">
          <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-accent-blue text-lg font-bold text-white">
            M
          </div>
          <h1 className="text-base font-semibold">
            {bootstrapState === 'loading' ? 'Connecting to Maestro…' : 'Maestro is not ready'}
          </h1>
          {bootstrapState === 'error' && (
            <>
              <p className="mt-2 text-sm text-text-secondary">{bootstrapError}</p>
              <button
                type="button"
                onClick={() => {
                  setBootstrapState('loading')
                  setBootstrapError('')
                  setBootstrapAttempt(value => value + 1)
                }}
                className="mt-4 rounded-lg bg-accent-blue px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              >
                Try again
              </button>
            </>
          )}
        </div>
      </div>
    )
  }

  const remoteProjectRequired = remote && (
    !activeWorkspace
    || !workspaces.some(workspace => (
      workspace.name === activeWorkspace && workspace.unlocked !== false
    ))
  )

  return (
    <div className="flex min-w-0 flex-col md:flex-row h-full w-full bg-bg-primary overflow-hidden">
      {/* Mobile header */}
      {isMobile && (
        <header className={`h-12 shrink-0 border-b border-border bg-bg-secondary px-4 flex items-center ${machineControls ? 'justify-between' : 'justify-center'}`}>
          {machineControls && <button
            onClick={toggleSidebar}
            className="p-2 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
          >
            <Menu size={20} />
          </button>}
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-accent-blue flex items-center justify-center text-white font-bold text-sm">
              M
            </div>
            <span className="font-semibold text-sm">Maestro</span>
            {appVersion && <span className="text-[10px] text-text-muted font-normal mt-0.5">v{appVersion}</span>}
          </div>
          {machineControls && <button
            onClick={() => { setSidebarOpen(false); toggleSettings() }}
            className="p-2 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
          >
            <Settings size={20} />
          </button>}
        </header>
      )}

      <Sidebar />
      <MainContent />
      {machineControls && <SettingsDrawer />}
      {machineControls && <LoraBrowser />}
      <DirectorDashboard />
      {machineControls && <StorageDashboard />}
      {machineControls && <RecipesOverlay />}
      <RetakeDialog />
      <H3GenerationPlanDialog />
      {/* OomRecoveryBanner is a fixed-position overlay — renders nothing
          unless the latest job/pipeline failure has oom_info attached.
          Lives at the App root so it floats above whichever screen the
          user is looking at when their generation OOMs. */}
      <OomRecoveryBanner />
      {/* PreflightBanner — fixed top overlay shown once on startup if the
          environment is missing ffmpeg / CUDA or low on disk. Renders
          nothing when everything checks out. */}
      {machineControls && <PreflightBanner />}
      {/* DownloadStatusBanner — fixed bottom-right overlay, polls
          /api/v1/downloads/active every 2s. Renders nothing unless
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
