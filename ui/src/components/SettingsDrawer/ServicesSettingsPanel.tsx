import { useState } from 'react'
import { RefreshCw, ShieldAlert, ShieldCheck, Lock } from 'lucide-react'
import { useStore } from '../../stores/useStore'
import { HOST_TERM_NOTICES } from '../../lib/hostTerms'

function ApiKeyField({ label, maskedValue, isSet, onSave }: {
  label: string
  maskedValue: string
  isSet: boolean
  onSave: (value: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')

  return (
    <div>
      <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
        {label}
      </label>
      {editing ? (
        <div className="flex gap-2">
          <input
            type="password"
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder="Paste API key..."
            className="flex-1 bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
            autoFocus
          />
          <button
            onClick={() => { onSave(value); setEditing(false); setValue('') }}
            className="px-3 py-2 bg-accent-blue text-white text-xs rounded-lg hover:bg-accent-blue-hover"
          >
            Save
          </button>
          <button
            onClick={() => { setEditing(false); setValue('') }}
            className="px-3 py-2 border border-border text-xs rounded-lg text-text-secondary hover:text-text-primary"
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex gap-2 items-center">
          <div className="flex-1 bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-muted font-mono">
            {isSet ? maskedValue : 'Not set'}
          </div>
          <button
            onClick={() => setEditing(true)}
            className="px-3 py-2 border border-border text-xs rounded-lg text-text-secondary hover:text-text-primary hover:border-border-light transition-colors"
          >
            {isSet ? 'Change' : 'Set'}
          </button>
        </div>
      )}
    </div>
  )
}

const PUBLIC_PROVIDERS = new Set(['openai', 'anthropic'])

function NsfwToggleSection() {
  const servicesConfig = useStore(s => s.servicesConfig)
  const servicesConfigLoading = useStore(s => s.servicesConfigLoading)
  const servicesConfigError = useStore(s => s.servicesConfigError)
  const clearServicesConfigError = useStore(s => s.clearServicesConfigError)
  const updateConfig = useStore(s => s.updateServicesConfig)
  const hostTerms = useStore(s => s.hostTerms)
  const hostTermsLoading = useStore(s => s.hostTermsLoading)
  const hostTermsError = useStore(s => s.hostTermsError)
  const acceptHostTerm = useStore(s => s.acceptHostTerm)

  if (!servicesConfig) return null

  const provider = servicesConfig.llm_provider || 'local'
  const isPublicProvider = PUBLIC_PROVIDERS.has(provider)
  const nsfwEnabled = servicesConfig.nsfw_mode
  const lawfulUse = hostTerms?.lawful_use
  const hasAccepted = lawfulUse?.accepted === true

  const handleToggle = async () => {
    if (isPublicProvider || servicesConfigLoading || hostTermsLoading) return

    clearServicesConfigError()

    if (nsfwEnabled) {
      await updateConfig({ nsfw_mode: false })
      return
    }

    if (!hasAccepted && !await acceptHostTerm('lawful_use')) {
      return
    }
    await updateConfig({ nsfw_mode: true })
  }

  return (
    <div className="space-y-3">
      <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Content Guidance</h3>
      <div
        className={`flex items-center justify-between ${isPublicProvider ? '' : 'cursor-pointer'} group`}
        onClick={() => { void handleToggle() }}
      >
        <div className="flex-1 mr-3">
          <div className={`text-sm flex items-center gap-1.5 ${
            isPublicProvider ? 'text-text-muted' : 'text-text-primary group-hover:text-accent-blue transition-colors'
          }`}>
            {nsfwEnabled ? (
              <ShieldAlert size={14} className="text-red-400 shrink-0" />
            ) : (
              <ShieldCheck size={14} className="text-indicator-success shrink-0" />
            )}
            Mature prompt guidance
            {isPublicProvider && <Lock size={11} className="text-text-muted" />}
          </div>
          <div className="text-[10px] text-text-muted mt-0.5">
            {isPublicProvider ? (
              <>Guidance is unavailable with {provider}; provider terms and privacy apply separately. Local generation remains content-neutral.</>
            ) : nsfwEnabled ? (
              <>Maestro can add mature authoring guidance when you mark a job Explicit.</>
            ) : (
              <>Guidance is off. Maestro does not inspect or filter locally processed creative content.</>
            )}
          </div>
        </div>
        <div
          className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
            isPublicProvider || servicesConfigLoading || hostTermsLoading ? 'bg-bg-tertiary border border-border opacity-40 cursor-not-allowed'
              : nsfwEnabled ? 'bg-red-500' : 'bg-bg-tertiary border border-border'
          }`}
        >
          <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white border border-border shadow transition-transform ${
            nsfwEnabled && !isPublicProvider ? 'translate-x-4' : 'translate-x-0.5'
          }`} />
        </div>
      </div>

      {lawfulUse && !hasAccepted && (
        <div className="rounded border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[10px] leading-relaxed text-text-muted">
          {HOST_TERM_NOTICES.lawful_use.text} Enabling guidance accepts notice v{HOST_TERM_NOTICES.lawful_use.version} once for this Maestro installation.
        </div>
      )}
      {lawfulUse?.accepted && (
        <p className="text-[9px] text-text-muted">Notice v{lawfulUse.accepted_version} accepted for this Maestro installation. You still choose Explicit separately for each job.</p>
      )}
      {(servicesConfigError || hostTermsError) && (
        <div className="flex items-start justify-between gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[10px] text-red-300">
          <span>{servicesConfigError || hostTermsError}</span>
          {servicesConfigError && (
            <button type="button" onClick={clearServicesConfigError} className="shrink-0 text-red-200 hover:text-white">Dismiss</button>
          )}
        </div>
      )}
    </div>
  )
}

export function ServicesSettingsPanel() {
  const servicesConfig = useStore(s => s.servicesConfig)
  const servicesConfigLoading = useStore(s => s.servicesConfigLoading)
  const servicesConfigError = useStore(s => s.servicesConfigError)
  const updateConfig = useStore(s => s.updateServicesConfig)
  const systemConfig = useStore(s => s.systemConfig)
  const updateSystemConfig = useStore(s => s.updateSystemConfig)
  const llmStatus = useStore(s => s.llmStatus)
  const llmModels = useStore(s => s.llmModels)
  const loadLlmModels = useStore(s => s.loadLlmModels)
  const [refreshing, setRefreshing] = useState(false)

  if (servicesConfigLoading && !servicesConfig) {
    return <div className="text-xs text-text-muted py-4 text-center">Loading...</div>
  }
  if (!servicesConfig) {
    return (
      <div className="py-4 text-center text-xs text-red-300">
        {servicesConfigError || 'Failed to load services settings'}
      </div>
    )
  }

  const provider = servicesConfig.llm_provider || 'local'
  const isRemote = provider === 'remote'
  const isOpenAI = provider === 'openai'
  const isLocal = provider === 'local'
  const providerLabel = isRemote ? 'your configured server' : isOpenAI ? 'OpenAI' : 'Anthropic'

  const handleRefreshModels = async () => {
    setRefreshing(true)
    await loadLlmModels()
    setRefreshing(false)
  }

  // Filter models by current provider (show local + remote of current provider)
  const filteredModels = llmModels.filter(m => {
    const mp = (m as { provider?: string }).provider || 'local'
    if (isLocal) return mp === 'local'
    return mp === 'local' || mp === provider
  })

  return (
    <div className="space-y-5">
      {/* Beta-features toggle moved to the bottom of this panel. See
          the "BETA FEATURES" section near the end of the return for
          rationale on the demotion + restyle. */}

      {/* LLM Provider */}
      <div className="space-y-4">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Writing Assistant</h3>

        <div className="flex items-center justify-between">
          <div className="min-w-0 flex-1 mr-3">
            <div className="text-sm text-text-primary truncate">
              {llmStatus?.loaded ? llmStatus.model_id : 'Standby'}
            </div>
            <div className="text-[10px] text-text-muted">
              {llmStatus?.loaded
                ? `Active on ${llmStatus.device} (${llmStatus.provider || 'local'})`
                : 'Auto-loads when needed'}
            </div>
          </div>
          <div className={`w-2 h-2 rounded-full shrink-0 ${llmStatus?.loaded ? 'bg-indicator-success' : 'bg-text-muted/30'}`} />
        </div>

        {/* Provider selector */}
        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
            Where the assistant runs
          </label>
          <select
            value={provider}
            onChange={e => {
              const newProvider = e.target.value
              const updates: Record<string, unknown> = { llm_provider: newProvider }
              // Provider compatibility is separate from host notice acceptance.
              if (PUBLIC_PROVIDERS.has(newProvider) && servicesConfig.nsfw_mode) {
                updates.nsfw_mode = false
              }
              updateConfig(updates)
              // Refresh model list for new provider
              setTimeout(() => loadLlmModels(), 500)
            }}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="local">On the computer running Maestro (llama-server)</option>
            <option value="remote">Another compatible server (LM Studio, etc.)</option>
            <option value="openai">OpenAI API</option>
            <option value="anthropic">Anthropic API</option>
          </select>
          {!isLocal && (
            <p className="mt-1.5 rounded border border-amber-500/25 bg-amber-500/5 px-2 py-1.5 text-[9px] leading-relaxed text-text-muted">
              When Maestro uses {providerLabel}, your prompt text and any attached context may be sent there. Its terms and privacy policy apply separately from Maestro's notice.
            </p>
          )}
        </div>

        {/* Remote URL (for remote/openai providers) */}
        {(isRemote || isOpenAI) && (
          <div>
            <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
              {isRemote ? 'Server URL' : 'API Base URL'}
            </label>
            <input
              type="text"
              value={servicesConfig.llm_remote_url}
              onChange={e => updateConfig({ llm_remote_url: e.target.value })}
              placeholder={isRemote ? 'http://192.168.1.100:1234' : 'https://api.openai.com'}
              className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
            />
            <p className="text-[10px] text-text-muted mt-1">
              {isRemote
                ? 'URL of your LM Studio, Ollama, or other OpenAI-compatible server'
                : 'Leave blank for default OpenAI endpoint'}
            </p>
          </div>
        )}

        {/* Model selector with refresh button */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[11px] text-text-muted uppercase tracking-wider">
              Assistant model
            </label>
            {!isLocal && (
              <button
                onClick={handleRefreshModels}
                disabled={refreshing}
                className="text-[10px] text-accent-blue hover:text-accent-blue-hover flex items-center gap-0.5 disabled:opacity-50"
              >
                <RefreshCw size={10} className={refreshing ? 'animate-spin' : ''} />
                Refresh
              </button>
            )}
          </div>
          <select
            value={servicesConfig.llm_model_id}
            onChange={e => updateConfig({ llm_model_id: e.target.value })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            {filteredModels.map(m => (
              <option key={m.id} value={m.id}>
                {m.label} ({m.size_hint})
              </option>
            ))}
          </select>
          {isLocal && (
            <p className="text-[10px] text-text-muted mt-1">
              Larger models can write richer scene descriptions, but use more memory.
            </p>
          )}
        </div>

        {/* Device selector (local only) */}
        {isLocal && (
          <div>
            <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
              Run the assistant on
            </label>
            <select
              value={servicesConfig.llm_device}
              onChange={e => updateConfig({ llm_device: e.target.value })}
              className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
            >
              <option value="cpu">CPU (recommended)</option>
              <option value="cuda">CUDA (uses VRAM)</option>
            </select>
            <p className="text-[10px] text-text-muted mt-1">
              CPU is recommended so the video model can keep the graphics memory it needs.
            </p>
          </div>
        )}
      </div>

      {/* Studio Prompt Enhancer — experimental gate. Default UI uses
          the Director LLM for the sparkle button without exposing the
          full enhancer/Wan2GP-alternative config; advanced users opt
          in via the Experimental toggle to reach this. */}
      {servicesConfig.show_experimental && <>
      <hr className="border-border" />

      {/* Prompt Enhancer */}
      <div className="space-y-4">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Studio Prompt Helper</h3>
        <p className="text-[10px] text-text-muted">
          Controls the sparkle button in Studio. It adapts your prompt for the selected model.
          Choose a separate writing model here, or leave it blank to use Director's model.
        </p>

        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
            Prompt helper model
          </label>
          <select
            value={servicesConfig.enhance_llm_model_id || ''}
            onChange={e => updateConfig({ enhance_llm_model_id: e.target.value })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="">Same as Director</option>
            {llmModels.map(m => (
              <option key={m.id} value={m.id}>
                {m.label} ({m.size_hint})
              </option>
            ))}
          </select>
          <p className="text-[10px] text-text-muted mt-1">
            {servicesConfig.enhance_llm_model_id
              ? 'Uses a separate model for Studio prompts, which can be smaller and faster.'
              : 'Uses Director\'s writing model, which may be slower but more capable.'
            }
          </p>
        </div>

        {servicesConfig.enhance_llm_model_id && (
          <div>
            <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
              Run the prompt helper on
            </label>
            <select
              value={servicesConfig.enhance_llm_device || 'cuda'}
              onChange={e => updateConfig({ enhance_llm_device: e.target.value })}
              className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
            >
              <option value="cpu">CPU</option>
              <option value="cuda">CUDA</option>
            </select>
          </div>
        )}

        <hr className="border-border/50" />

        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
            Wan2GP Enhancer (Alternative)
          </label>
          <select
            value={systemConfig?.enhancer_enabled ?? 0}
            onChange={e => updateSystemConfig({ enhancer_enabled: Number(e.target.value) })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value={0}>Disabled (use LLM above)</option>
            <option value={4}>Qwen3.5 9B Abliterated</option>
            <option value={3}>Qwen3.5 4B Abliterated</option>
            <option value={1}>Llama 3.2 + Florence2</option>
            <option value={2}>LlamaJoy + Florence2</option>
          </select>
          <p className="text-[10px] text-text-muted mt-1">
            When enabled, this alternate enhancer takes over and uses its own prompt rules.
          </p>
        </div>
      </div>
      </>}

      <hr className="border-border" />

      {/* Content Mode */}
      <NsfwToggleSection />

      <hr className="border-border" />

      {/* Director Architecture */}
      <div className="space-y-3">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">Director Planning</h3>
        {/* Director v2 Engine toggle. v2 became the default 2026-05-03
            after weeks of real-world validation showed it's more
            reliable than v1 (v1 had a polish-pass failure mode where
            smaller LLMs would hallucinate dialogue into image_prompts).
            No longer behind the experimental gate — toggle is always
            visible so users who hit issues with v2 can revert to v1
            without first enabling experimental mode. */}
        <label className="flex items-center justify-between cursor-pointer group">
          <div className="flex-1 mr-3">
            <div className="text-sm text-text-primary group-hover:text-accent-blue transition-colors">
              Director Planner <span className="text-[10px] text-text-muted font-normal">(recommended)</span>
            </div>
            <div className="text-[10px] text-text-muted mt-0.5">
              Builds and checks your shot plan, including Podcast and Viral Video projects. Turn it off if you need the previous planner.
            </div>
          </div>
          <div
            onClick={() => updateConfig({ use_director_v2: !servicesConfig.use_director_v2 })}
            className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
              servicesConfig.use_director_v2 ? 'bg-accent-blue' : 'bg-bg-tertiary border border-border'
            }`}
          >
            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white border border-border shadow transition-transform ${
              servicesConfig.use_director_v2 ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </div>
        </label>

        {/* Prompt Polish Mode */}
        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">
            Adapt prompts for each model
          </label>
          <select
            value={servicesConfig.director_prompt_polish || 'third_pass'}
            onChange={e => updateConfig({ director_prompt_polish: e.target.value as 'off' | 'full_guide' | 'light_guide' | 'third_pass' })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="third_pass">Automatic — recommended</option>
            <option value="light_guide">Light guidance</option>
            <option value="full_guide">Full guidance</option>
            <option value="off">Off</option>
          </select>
          <p className="text-[10px] text-text-muted mt-1">
            {servicesConfig.director_prompt_polish === 'full_guide'
              ? 'Uses the complete guide for the selected model while planning.'
              : servicesConfig.director_prompt_polish === 'light_guide'
              ? 'Uses a shorter guide for the selected model while planning.'
              : servicesConfig.director_prompt_polish === 'off'
              ? 'Uses Director\'s general prompt rules without adapting them to the selected model.'
              : 'Automatically prepares prompts in the form each selected model works best with. H3 video prompts are kept in their original form.'}
          </p>
        </div>

        {/* Multi-Shot LoRA Mode toggle (Beta) — Phase 1 of LoRA
            capabilities catalog. When on, Pass 2 emits storyboard-format
            video_prompts for 20s shots so a compatible IC-LoRA (e.g.
            Maque AI LTX-2.3 IC-LoRA) can cut between camera angles
            inside a single generation. Short reaction (≤15s) and long
            sustained (≥40s) shots keep the regular flowing format.
            User must also add the matching LoRA to their video_loras
            selection for cuts to actually render — the toggle only
            changes the prompt format. */}
        <label className="flex items-center justify-between cursor-pointer group">
          <div className="flex-1 mr-3">
            <div className="text-sm text-text-primary group-hover:text-accent-blue transition-colors">
              Multi-Shot LoRA Mode <span className="text-[10px] text-accent-blue/80 font-normal">Beta</span>
            </div>
            <div className="text-[10px] text-text-muted mt-0.5">
              Emits storyboard-format prompts for 20s shots so an IC-LoRA
              (e.g. Maque AI LTX-2.3) can cut between camera angles inside
              one generation. Add the matching LoRA to your video LoRA
              selection for cuts to render. Short and long-sustained shots
              stay single-camera.
            </div>
          </div>
          <div
            onClick={() => updateConfig({ director_multishot_lora_mode: !servicesConfig.director_multishot_lora_mode })}
            className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
              servicesConfig.director_multishot_lora_mode ? 'bg-accent-blue' : 'bg-bg-tertiary border border-border'
            }`}
          >
            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white border border-border shadow transition-transform ${
              servicesConfig.director_multishot_lora_mode ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </div>
        </label>

        {/* Voice Reference (ID-LoRA) is a standard setting, independent of
            the in-development feature gate and enabled by default. */}
        <label className="flex items-center justify-between cursor-pointer group">
          <div className="flex-1 mr-3">
            <div className="text-sm text-text-primary group-hover:text-accent-blue transition-colors">
              Keep a voice consistent
            </div>
            <div className="text-[10px] text-text-muted mt-0.5">
              Lets you add a short voice sample in Studio Video or Director so the speaker sounds consistent across clips. Enabled by default.
            </div>
          </div>
          <div
            onClick={() => updateConfig({ voice_reference_enabled: !servicesConfig.voice_reference_enabled })}
            className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
              servicesConfig.voice_reference_enabled ? 'bg-accent-blue' : 'bg-bg-tertiary border border-border'
            }`}
          >
            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white border border-border shadow transition-transform ${
              servicesConfig.voice_reference_enabled ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </div>
        </label>
      </div>

      <hr className="border-border" />

      {/* FlashVSR Upscaling — DiT super-resolution spatial upsampling.
          Selected per-generation in Post Processing → Spatial Upsampling
          ("FlashVSR 2x", "FlashVSR Two Pass 2x", ...). These control the
          model variant, sparse-attention density, and backend. */}
      <div className="space-y-3">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">FlashVSR Upscaling</h3>
        <p className="text-[10px] text-text-muted -mt-1">
          Makes finished video larger and sharper. Choose it for a generation under Post Processing → Spatial Upsampling. The first use may download about 4 GB of model files, which Maestro saves for later.
        </p>

        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Quality and memory use</label>
          <select
            value={servicesConfig.flashvsr_mode ?? 1}
            onChange={e => updateConfig({ flashvsr_mode: Number(e.target.value) })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value={1}>Tiny — fast, low VRAM (default)</option>
            <option value={2}>Full — best quality, more VRAM</option>
            <option value={3}>Tiny-Long — for long videos</option>
          </select>
          <p className="text-[10px] text-text-muted mt-1">
            {servicesConfig.flashvsr_mode === 2
              ? 'Produces the sharpest detail and most consistent motion, but uses the most graphics memory.'
              : servicesConfig.flashvsr_mode === 3
              ? 'Uses less graphics memory and is tuned for long clips.'
              : 'Fastest and lightest. A good default when the main video model is also using the graphics card.'}
          </p>
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[11px] text-text-muted uppercase tracking-wider">Motion detail</label>
            <span className="text-xs text-text-secondary">{(servicesConfig.flashvsr_topk_ratio ?? 0).toFixed(2)}</span>
          </div>
          <input
            type="range"
            min={0}
            max={4}
            step={0.25}
            value={servicesConfig.flashvsr_topk_ratio ?? 0}
            onChange={e => updateConfig({ flashvsr_topk_ratio: parseFloat(e.target.value) })}
          />
          <p className="text-[10px] text-text-muted mt-1">
            Higher values preserve motion more carefully but take longer. 0 is fastest.
          </p>
        </div>

        <div>
          <label className="text-[11px] text-text-muted uppercase tracking-wider mb-1.5 block">Processing method</label>
          <select
            value={servicesConfig.flashvsr_backend || 'auto'}
            onChange={e => updateConfig({ flashvsr_backend: e.target.value })}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent-blue"
          >
            <option value="auto">Automatic (recommended)</option>
            <option value="triton_sparse">Triton Sparse (included)</option>
            <option value="sparge">SpargeAttn (best motion; separate install)</option>
          </select>
          <p className="text-[10px] text-text-muted mt-1">
            SpargeAttn can preserve motion best but must be installed separately. Automatic uses it when available, or the included Triton method otherwise.
          </p>
        </div>
      </div>

      <hr className="border-border" />

      {/* API Keys.
          The three external-AI provider keys (Google / OpenAI /
          Anthropic) are gated by the experimental toggle — non-power
          users running the local LLM exclusively never need them, and
          surfacing them in the default UI invites confused calls about
          "do I need these to use Maestro?"
          The CivitAI key stays visible always since LoRA download
          rate-limit relief is broadly useful, not a power-user feature. */}
      <div className="space-y-4">
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium">API Keys</h3>

        {servicesConfig.show_experimental && (
          <>
            <p className="text-[10px] text-text-muted">
              Add a key only for an external service you choose to use. Director may also use these services when configured to do so.
            </p>

            <ApiKeyField
              label="Google AI API Key"
              maskedValue={servicesConfig.google_api_key}
              isSet={servicesConfig.google_api_key_set}
              onSave={val => updateConfig({ google_api_key: val })}
            />

            <ApiKeyField
              label="OpenAI API Key"
              maskedValue={servicesConfig.openai_api_key}
              isSet={servicesConfig.openai_api_key_set}
              onSave={val => updateConfig({ openai_api_key: val })}
            />

            <ApiKeyField
              label="Anthropic API Key"
              maskedValue={servicesConfig.anthropic_api_key}
              isSet={servicesConfig.anthropic_api_key_set}
              onSave={val => updateConfig({ anthropic_api_key: val })}
            />
          </>
        )}

        <ApiKeyField
          label="CivitAI API Key"
          maskedValue={servicesConfig.civitai_api_key}
          isSet={servicesConfig.civitai_api_key_set}
          onSave={val => updateConfig({ civitai_api_key: val })}
        />
        <p className="text-[10px] text-text-muted -mt-2">
          Optional. Increases rate limits and enables access to restricted models.
        </p>
      </div>

      {/* ───────────────────────────── BETA FEATURES ─────────────────────────
          Originally lived at the top of this panel with amber styling and a
          "Power Users" badge — visually framed as a featured upgrade. In
          practice the toggle hides in-progress / unstable work, and turning
          it on gave new users a more cluttered UI plus features explicitly
          warned to be unstable. The framing was inverted from intent.

          Moved to the BOTTOM of the panel, neutral styling (no amber, no
          badge), descriptive copy that leads with the warning. Power users
          who want it can find it; new users don't get nudged toward it. */}
      <hr className="border-border" />
      <div>
        <h3 className="text-[11px] text-text-secondary uppercase tracking-wider font-medium mb-3">
          Beta Features
        </h3>
        <label className="flex items-center justify-between cursor-pointer group">
          <div className="flex-1 mr-3">
            <div className="text-sm text-text-primary">
              Show in-development features
            </div>
            <div className="text-[10px] text-text-muted mt-0.5 leading-relaxed">
              Reveals features still under development. Some are incomplete,
              unstable, or require additional setup. Default off keeps the UI
              focused on features known to work well.
            </div>
            <div className="text-[10px] text-text-muted mt-1 leading-relaxed">
              Currently gates: external LLM APIs (Google / OpenAI / Anthropic),
              Studio Prompt Helper settings, and the Inpaint edit mode.
            </div>
          </div>
          <div
            onClick={() => updateConfig({ show_experimental: !servicesConfig.show_experimental })}
            className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${
              servicesConfig.show_experimental ? 'bg-accent-blue' : 'bg-bg-tertiary border border-border'
            }`}
          >
            <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white border border-border shadow transition-transform ${
              servicesConfig.show_experimental ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </div>
        </label>
      </div>
    </div>
  )
}
