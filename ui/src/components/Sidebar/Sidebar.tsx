import { useCallback, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { Settings, X, Globe, BookMarked } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { useIsMobile } from '../../lib/useIsMobile'
import { PRODUCT_NAME, PRODUCT_NAME_VISUAL, PRODUCT_PROVENANCE } from '../../lib/branding'
import { WhatsNewButton } from '../WhatsNewDialog'
import { GenerationModeSelector } from './GenerationModeSelector'
import { InputsPanel } from './InputsPanel'
import { PromptInput } from './PromptInput'
import { ImageRefSection } from './ImageRefSection'
import { AudioModeSection } from './AudioModeSection'
import { MusicControls } from './MusicControls'
import { AudioSubModeToggle } from './AudioSubModeToggle'
import { SfxControls } from './SfxControls'
import { MixerControls } from './MixerControls'
import { ModeToggle } from './ModeToggle'
import { DurationSlider } from './DurationSlider'
import { ResolutionPresets } from './ResolutionPresets'
import { AspectRatioGrid } from './AspectRatioGrid'
import { H3PerformanceProfiles } from './H3PerformanceProfiles'
import { AdvancedSettings } from './AdvancedSettings'
import { GenerateButton } from './GenerateButton'
import { ModelSelector } from './ModelSelector'
import { MultiClipEditor } from './MultiClipEditor'
import { DirectorChat } from './DirectorChat'
import { EditSubModeToggle } from './EditSubModeToggle'
import { RestyleControls } from './RestyleControls'
import { InpaintControls } from './InpaintControls'
import { OutpaintControls } from './OutpaintControls'
import { RetakeControls } from './RetakeControls'
import { EditAnythingControls } from './EditAnythingControls'
import { RecastControls } from './RecastControls'
import { BlendControls } from './BlendControls'
import { AnchorReturnBanner } from './AnchorReturnBanner'
import { VoiceRefSection } from './VoiceRefSection'
import { ToolsPanel } from './ToolsPanel'
import { HardwareStatusBar } from './HardwareStatusBar'
import { GenerationPrivacyControls } from './GenerationPrivacyControls'
import { ProjectReferenceLibrary } from './ProjectReferenceLibrary'
import { closeModalIfTop, installModalFocus } from '../../lib/modalFocus'

export function Sidebar() {
  const toggleSettings = useStore(s => s.toggleSettings)
  const generationMode = useStore(s => s.generationMode)
  const imageMode = useStore(s => s.params.image_mode)
  const modelOptions = useStore(s => s.modelOptions)
  const sidebarOpen = useStore(s => s.sidebarOpen)
  const setSidebarOpen = useStore(s => s.setSidebarOpen)
  const sidebarMode = useStore(s => s.sidebarMode)
  const setSidebarMode = useStore(s => s.setSidebarMode)
  const editSubMode = useStore(s => s.editSubMode)
  const modelType = useStore(s => s.params.model_type)
  const openLoraBrowser = useStore(s => s.setLoraBrowserOpen)
  const machineControls = useStore(s => s.accessContext?.machine_controls === true)
  const isMobile = useIsMobile()
  const mobileSidebarRef = useRef<HTMLElement>(null)
  const mobileCloseRef = useRef<HTMLButtonElement>(null)
  const mobileRestoreFocusRef = useRef<HTMLElement | null>(null)

  const closeMobileSidebar = useCallback(() => {
    closeModalIfTop(document, mobileSidebarRef.current, () => setSidebarOpen(false))
  }, [setSidebarOpen])

  const isVideo = generationMode === 'video'
  const isImage = generationMode === 'image'
  const isAudio = generationMode === 'audio'
  const audioSubMode = useStore(s => s.audioSubMode)
  const isEdit = generationMode === 'avatar'
  const isTools = generationMode === 'tools'
  const isRetake = isEdit && editSubMode === 'retake'
  const isRestyle = isEdit && editSubMode === 'restyle'
  const isInpaint = isEdit && editSubMode === 'inpaint'
  const isOutpaint = isEdit && editSubMode === 'outpaint'
  const isEditAnything = isEdit && editSubMode === 'edit_anything'
  const isRecast = isEdit && editSubMode === 'recast'
  const isScailEdit = isRecast || isRestyle
  const isMultiClip = isVideo && imageMode === 2
  const isContinue = isVideo && imageMode === 3
  const isBlend = isVideo && imageMode === 4
  const isDirector = sidebarMode === 'director'
  const isReference = sidebarMode === 'reference'
  const activeWorkspace = useStore(s => s.activeWorkspace)
  const workspaces = useStore(s => s.workspaces)
  const browsingUploads = useStore(s => s.browsingUploads)
  const referenceLocked = workspaces.some(workspace => (
    workspace.name === activeWorkspace && workspace.unlocked === false
  ))
  const isI2vOnly = modelOptions?.i2v_class && !modelOptions?.t2v_class
  const isH3 = isVideo && (
    modelType.startsWith('minimax_h3')
    || String(modelOptions?.architecture || '').startsWith('minimax_h3')
    || String(modelOptions?.model_type || '').startsWith('minimax_h3')
  )

  useEffect(() => {
    if (!isMobile || !sidebarOpen || !mobileSidebarRef.current || !mobileCloseRef.current) return
    mobileRestoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    return installModalFocus({
      document,
      dialog: mobileSidebarRef.current,
      initialFocus: mobileCloseRef.current,
      restoreFocus: mobileRestoreFocusRef.current,
      appRoot: document.getElementById('root'),
      onClose: closeMobileSidebar,
      priority: 60,
    })
  }, [closeMobileSidebar, isMobile, sidebarOpen])

  const openRecipes = () => {
    setSidebarOpen(false)
    useStore.getState().setRecipesOpen(true)
  }

  const productIdentity = (
    <div className="flex min-w-0 items-center gap-2">
      <div aria-hidden="true" className="w-7 h-7 shrink-0 rounded-lg bg-accent-blue flex items-center justify-center text-white font-bold text-sm">
        M
      </div>
      <div className="min-w-0">
        <span className="sr-only">{PRODUCT_NAME}. {PRODUCT_PROVENANCE}</span>
        <span aria-hidden="true" className="block truncate text-[11px] font-semibold leading-tight">{PRODUCT_NAME_VISUAL}</span>
        <span aria-hidden="true" className="block truncate text-[8px] font-normal leading-tight text-text-muted">{PRODUCT_PROVENANCE}</span>
      </div>
      {!isMobile && <WhatsNewButton />}
    </div>
  )

  const modeToggle = (size: 'sm' | 'md') => (
    <div role="group" aria-label="Creative workspace" className={`grid grid-cols-3 bg-bg-tertiary rounded-lg p-0.5 border border-border ${size === 'sm' ? 'w-full' : ''}`}>
      <button
        type="button"
        onClick={() => setSidebarMode('studio')}
        aria-label="Open Generate"
        aria-pressed={sidebarMode === 'studio'}
        className={`mobile-control-target min-w-0 px-1.5 py-1 text-[10px] md:px-2.5 md:text-[11px] rounded-md transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
          sidebarMode === 'studio' ? 'bg-toggle-active shadow-accent-glow text-white' : 'text-text-secondary hover:text-text-primary'
        }`}
      >
        Generate
      </button>
      <button
        type="button"
        onClick={() => setSidebarMode('director')}
        aria-label="Open Director"
        aria-pressed={isDirector}
        className={`mobile-control-target min-w-0 px-1.5 py-1 text-[10px] md:px-2.5 md:text-[11px] rounded-md transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue ${
          // bg-toggle-active is flat accent-blue in the default theme
          // (preserves the original blue pill) and a red→orange sunset
          // gradient in Golden Hour. shadow-accent-glow is empty in
          // default and a warm bloom in Golden Hour.
          isDirector ? 'bg-toggle-active shadow-accent-glow text-white' : 'text-text-secondary hover:text-text-primary'
        }`}
      >
        Director
      </button>
      <button
        type="button"
        onClick={() => setSidebarMode('reference')}
        disabled={!activeWorkspace || browsingUploads || referenceLocked}
        aria-label={referenceLocked ? 'Unlock project to open Reference' : 'Open Reference'}
        aria-pressed={isReference}
        className={`mobile-control-target min-w-0 px-1.5 py-1 text-[10px] md:px-2.5 md:text-[11px] rounded-md transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue disabled:cursor-not-allowed disabled:opacity-40 ${
          isReference ? 'bg-toggle-active shadow-accent-glow text-white' : 'text-text-secondary hover:text-text-primary'
        }`}
      >
        Reference
      </button>
    </div>
  )

  // Edit mode sub-controls based on sub-mode
  const editControls = (
    <>
      {isRetake && (
        <>
          <RetakeControls />
          <PromptInput />
        </>
      )}
      {isInpaint && (
        <>
          <InpaintControls />
          <PromptInput />
        </>
      )}
      {isOutpaint && (
        <>
          <OutpaintControls />
          <PromptInput />
        </>
      )}
      {isRestyle && (
        <>
          <RestyleControls />
          <PromptInput />
        </>
      )}
      {isEditAnything && (
        <>
          <EditAnythingControls />
          <PromptInput />
        </>
      )}
      {isRecast && (
        <>
          <RecastControls />
          <PromptInput />
        </>
      )}
    </>
  )

  const studioControls = (
    <>
      {/* Edit Anything/Recast → Image Mode round-trip banner. Visible while
          a boundary anchor or Recast reference is being edited; null otherwise. */}
      <AnchorReturnBanner />

      {/* [&>*]:shrink-0 — keep every section at its natural height and let
          the column SCROLL when space is tight (e.g. ID-LoRA voice section
          added + hardware bar expanded), instead of letting flex-shrink
          crush sections into each other. */}
      <div className={`${isMobile ? 'flex-none overflow-visible' : 'flex-1 overflow-y-auto min-h-0'} px-4 py-4 flex flex-col gap-4 [&>*]:shrink-0`}>
        <GenerationModeSelector />

        {/* Tools mode: standalone post-processing (upscale / revoice) on any
            existing clip. Renders in place of the generation controls. */}
        {isTools ? <ToolsPanel /> : (
        <>
        {/* Edit mode: sub-mode toggle + sub-controls */}
        {isEdit && <EditSubModeToggle />}
        {isEdit && editControls}

        {/* Video mode */}
        {isVideo && <ModeToggle />}
        {/* Blend mode manages its own duration (overlap_sec) and its own
            start/end anchors — so the generic Duration slider and
            start/end ImageUpload don't apply there. */}
        {isH3 && <H3PerformanceProfiles />}
        {isVideo && !isBlend && <DurationSlider />}
        {/* Resolution is a primary generation choice. Recast/Repaint and
            Outpaint own dedicated output-quality canvases; audio has none. */}
        {!isAudio && !isScailEdit && (
          <>
            {!isOutpaint && !modelOptions?.hide_resolution_presets && <ResolutionPresets />}
            {!isEdit && !isH3 && <AspectRatioGrid />}
          </>
        )}
        {/* Frames (image_mode 0) AND Extend (image_mode 3) both use the unified
            InputsPanel. In Extend mode its first tile is the source video to
            continue from; otherwise it's the start frame. */}
        {isVideo && !isMultiClip && !isBlend && (
          <div>
            {isI2vOnly && !isContinue && (
              <div className="text-[10px] text-indicator-warning bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-1.5 mb-2">
                This model requires a start image to generate video.
              </div>
            )}
            <InputsPanel />
          </div>
        )}
        {isBlend && <BlendControls />}

        {/* Image mode: reference images */}
        {isImage && modelOptions?.image_ref_choices && <ImageRefSection />}

        {/* Video/Image mode: audio controls (soundtrack, control video, etc.).
            In Frames mode (video, image_mode 0) the unified InputsPanel routes
            audio/control-video via tiles instead, so the dropdown is hidden
            there. Other video sub-modes + image mode keep AudioModeSection. */}
        {!isEdit && !isAudio && !(isVideo && (imageMode === 0 || imageMode === 3)) && modelOptions?.audio_prompt_type_sources && <AudioModeSection />}

        {/* Audio mode: sub-mode toggle + mode-specific controls */}
        {isAudio && <AudioSubModeToggle />}
        {isAudio && audioSubMode === 'speech' && modelOptions?.audio_prompt_type_sources && <AudioModeSection />}
        {isAudio && audioSubMode === 'sfx' && <SfxControls />}
        {isAudio && audioSubMode === 'mixer' && <MixerControls />}
        {isAudio && audioSubMode === 'music' && <MusicControls />}

        {/* Prompt area (non-edit modes, skip for SFX/Mixer/Music which have their own UI) */}
        {!isEdit && !(isAudio && (audioSubMode === 'sfx' || audioSubMode === 'mixer' || audioSubMode === 'music')) && (isMultiClip ? <MultiClipEditor /> : <PromptInput />)}

        {/* Video: reference images below prompt. In Frames mode the InputsPanel
            renders them as ordered tiles instead. */}
        {isVideo && imageMode !== 0 && imageMode !== 3 && modelOptions?.image_ref_choices && <ImageRefSection />}

        {/* Voice Reference (ID-LoRA) — gated by Settings → Services
            toggle (`voice_reference_enabled`). VoiceRefSection internally
            no-ops when the toggle is off. We render it for Studio Video
            mode (basic, multi-clip, continue, blend) — it's the same
            generation path that consumes `directorVoiceRef` server-side.
            Director mode renders its own copy in DirectorChat. */}
        {isVideo && !isDirector && imageMode !== 0 && imageMode !== 3 && <VoiceRefSection />}
        </>
        )}
      </div>

      {/* Bottom Bar: Advanced + LoRA Browser + Model + Generate.
          Hidden in Tools mode — ToolsPanel has its own Run button and
          owns no model. */}
      {!isTools && (
      <div className="px-3 py-2.5 border-t border-border">
        <div className="flex items-center gap-2">
          <AdvancedSettings />
          <button
            type="button"
            onClick={openRecipes}
            className="mobile-control-target flex shrink-0 items-center justify-center rounded-lg border border-border bg-bg-tertiary p-2 text-text-secondary transition-colors hover:border-border-light hover:text-accent-blue focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
            title="Recipes — one-click presets"
            aria-label="Browse recipes"
          >
            <BookMarked size={14} />
          </button>
          {machineControls && !isOutpaint && (
            <button
              onClick={() => openLoraBrowser(true, modelType)}
              className="p-2 rounded-lg bg-bg-tertiary border border-border hover:border-border-light text-text-secondary hover:text-accent-blue transition-colors shrink-0"
              title="Browse LoRAs on CivitAI"
            >
              <Globe size={14} />
            </button>
          )}
          <div className="flex-1 min-w-0">
            <ModelSelector />
          </div>
          <div className="shrink-0">
            <GenerateButton />
          </div>
        </div>
      </div>
      )}
    </>
  )

  // Mobile: overlay drawer
  if (isMobile) {
    return createPortal(
      <>
        {sidebarOpen && (
          <button
            type="button"
            aria-label="Close creative workspace menu"
            tabIndex={-1}
            className="fixed inset-0 bg-black/40 z-40"
            onClick={closeMobileSidebar}
          />
        )}
        <aside
          id="maestro-mobile-sidebar"
          ref={mobileSidebarRef}
          role="dialog"
          aria-modal="true"
          aria-label="Generate, Director, and Reference menu"
          aria-hidden={!sidebarOpen}
          inert={!sidebarOpen}
          className={`fixed top-0 left-0 h-[100vh] supports-[height:100dvh]:h-[100dvh] w-[380px] max-w-[85vw] bg-bg-secondary border-r border-border z-[60] flex flex-col overflow-hidden transform transition-transform duration-300 ease-in-out pt-[env(safe-area-inset-top)] pr-[env(safe-area-inset-right)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}>
          {/* Header */}
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-b border-border px-4 py-3">
            {productIdentity}
            <button
              ref={mobileCloseRef}
              type="button"
              onClick={closeMobileSidebar}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-bg-hover hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue"
              aria-label="Close creative workspace menu"
            >
              <X aria-hidden="true" size={16} />
            </button>
            <div className="col-span-2 min-w-0">{modeToggle('sm')}</div>
          </div>
          <div className={`flex flex-1 min-h-0 flex-col overscroll-contain [-webkit-overflow-scrolling:touch] ${
            isDirector || isReference ? 'overflow-hidden' : 'overflow-y-auto'
          }`}>
            {!isReference && <GenerationPrivacyControls />}
            {!isReference && (isDirector ? <DirectorChat /> : studioControls)}
            <ProjectReferenceLibrary active={isReference && sidebarOpen} />
            {machineControls && <HardwareStatusBar />}
          </div>
        </aside>
      </>,
      document.body,
    )
  }

  // Desktop: static sidebar
  return (
    <aside className="w-[clamp(460px,24vw,560px)] h-full bg-bg-secondary border-r border-border flex flex-col shrink-0">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        {productIdentity}
        <div className="flex items-center gap-2">
          {modeToggle('md')}
          {machineControls && <button
            type="button"
            onClick={toggleSettings}
            className="p-1.5 rounded-lg hover:bg-bg-hover text-text-secondary hover:text-text-primary transition-colors"
            title="Settings"
            aria-label="Open machine settings"
          >
            <Settings size={16} />
          </button>}
        </div>
      </div>
      {!isReference && <GenerationPrivacyControls />}
      {!isReference && (isDirector ? <DirectorChat /> : studioControls)}
      <ProjectReferenceLibrary active={isReference} />
      {machineControls && <HardwareStatusBar />}
    </aside>
  )
}
