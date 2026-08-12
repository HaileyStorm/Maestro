"""Explicit-job UI/source contracts plus executable session-policy checks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from services.output_access import output_policy_from_request  # noqa: E402


STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
CONTROLS = (
    ROOT / "ui/src/components/Sidebar/GenerationPrivacyControls.tsx"
).read_text(encoding="utf-8")
SIDEBAR = (ROOT / "ui/src/components/Sidebar/Sidebar.tsx").read_text(
    encoding="utf-8"
)
APP_SOURCE = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")
SERVICES = (
    ROOT / "ui/src/components/SettingsDrawer/ServicesSettingsPanel.tsx"
).read_text(encoding="utf-8")
SETTINGS_DRAWER = (
    ROOT / "ui/src/components/SettingsDrawer/SettingsDrawer.tsx"
).read_text(encoding="utf-8")
H3_PROFILES = (
    ROOT / "ui/src/components/Sidebar/H3PerformanceProfiles.tsx"
).read_text(encoding="utf-8")
MODEL_SELECTOR = (
    ROOT / "ui/src/components/Sidebar/ModelSelector.tsx"
).read_text(encoding="utf-8")
H3_PLAN_DIALOG = (
    ROOT / "ui/src/components/H3GenerationPlanDialog.tsx"
).read_text(encoding="utf-8")
INPUTS_PANEL = (
    ROOT / "ui/src/components/Sidebar/InputsPanel.tsx"
).read_text(encoding="utf-8")
WELCOME = (ROOT / "ui/src/components/WelcomeModal.tsx").read_text(encoding="utf-8")
TYPES = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")
PROJECT_REFS = (
    ROOT / "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx"
).read_text(encoding="utf-8")
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
LLM_CHAT = (ROOT / "ui/src/components/LlmChat.tsx").read_text(encoding="utf-8")
LORAS = (
    ROOT / "ui/src/components/SettingsDrawer/LoraSelector.tsx"
).read_text(encoding="utf-8")
DIRECTOR_LORAS = (
    ROOT / "ui/src/components/SettingsDrawer/DirectorLoraSelector.tsx"
).read_text(encoding="utf-8")
MEDIA_ITEM = (
    ROOT / "ui/src/components/MainContent/MediaFeedItem.tsx"
).read_text(encoding="utf-8")
PRIVATE_PREVIEW = (ROOT / "ui/src/lib/privatePreview.ts").read_text(encoding="utf-8")
THUMBNAILS = (
    ROOT / "ui/src/components/MainContent/ThumbnailGallery.tsx"
).read_text(encoding="utf-8")
REFERENCE_LIBRARY = (
    ROOT / "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx"
).read_text(encoding="utf-8")
MAIN_CONTENT = (
    ROOT / "ui/src/components/MainContent/MainContent.tsx"
).read_text(encoding="utf-8")
HOST_TERMS_UI = (ROOT / "ui/src/lib/hostTerms.ts").read_text(encoding="utf-8")


class ExplicitSessionPolicyTests(unittest.TestCase):
    def test_explicit_intent_is_request_local_and_defaults_private(self):
        first_request = {"explicit_output": True}
        first = output_policy_from_request(
            first_request,
            explicit_enabled=False,
            owner_session_id="a" * 32,
        )
        second_request = {}
        second = output_policy_from_request(
            second_request,
            explicit_enabled=False,
            owner_session_id="b" * 32,
        )

        self.assertEqual(
            first,
            {"private": True, "explicit": True},
        )
        self.assertEqual(
            second,
            {"private": False, "explicit": False},
        )
        self.assertEqual(first_request, {})
        self.assertEqual(second_request, {})

    def test_deliberate_public_override_remains_possible(self):
        policy = output_policy_from_request(
            {"explicit_output": True, "private_output": False},
            explicit_enabled=False,
            owner_session_id="a" * 32,
        )
        self.assertTrue(policy["explicit"])
        self.assertFalse(policy["private"])
        self.assertNotIn("owner_session_id", policy)


class ExplicitJobUiSourceTests(unittest.TestCase):
    def test_visible_control_is_per_job_and_never_mutates_machine_config(self):
        self.assertIn('type="checkbox"', CONTROLS)
        self.assertIn("s.explicitOutput", CONTROLS)
        self.assertIn("s.setExplicitOutput", CONTROLS)
        self.assertIn("Mark this Generate or Director job as explicit", CONTROLS)
        self.assertNotIn("updateServicesConfig", CONTROLS)
        self.assertNotIn("nsfw_accepted_at", CONTROLS)
        self.assertNotIn("PUBLIC_PROVIDERS", CONTROLS)
        self.assertIn("Private controls preview blur only", CONTROLS)
        self.assertIn("Project access rules always apply separately", CONTROLS)
        self.assertNotIn("visible to other authorized project users", CONTROLS)

        self.assertGreaterEqual(SIDEBAR.count("<GenerationPrivacyControls />"), 2)
        self.assertEqual(
            SIDEBAR.count("{!isReference && <GenerationPrivacyControls />}"),
            2,
        )
        self.assertEqual(
            SIDEBAR.count(
                "{!isReference && (isDirector ? <DirectorChat /> : studioControls)}"
            ),
            2,
        )

    def test_store_intent_is_browser_memory_not_host_config_or_local_storage(self):
        services_state = STORE[
            STORE.index("// Services config\n  servicesConfig: null,"):
            STORE.index("// LLM state", STORE.index("// Services config\n  servicesConfig: null,"))
        ]
        load_block = services_state[
            services_state.index("loadServicesConfig: async"):
            services_state.index("updateServicesConfig: async")
        ]
        explicit_setter = services_state[
            services_state.index("explicitOutput: false"):
            services_state.index("privateOutput: false")
        ]

        self.assertIn("explicitOutput: false", explicit_setter)
        self.assertIn("setExplicitOutput: (enabled)", explicit_setter)
        self.assertIn("...(enabled ? { privateOutput: true } : {})", explicit_setter)
        self.assertNotIn("localStorage", explicit_setter)
        self.assertNotIn("explicitOutput", load_block)
        self.assertNotIn("privateOutput", load_block)

    def test_all_store_generation_paths_send_per_job_policy(self):
        self.assertNotIn(
            "explicit_output: state.servicesConfig?.nsfw_mode",
            STORE,
        )
        self.assertGreaterEqual(
            STORE.count("explicit_output: state.explicitOutput"),
            10,
        )
        self.assertGreaterEqual(
            STORE.count("private_output: state.privateOutput"),
            9,
        )
        director_images = STORE[
            STORE.index("directorGenerateStartImages: async"):
            STORE.index(
                "directorApplyToClips:",
                STORE.index("directorGenerateStartImages: async"),
            )
        ]
        self.assertIn(
            "const requestExplicitOutput = initialState.explicitOutput",
            director_images,
        )
        self.assertIn(
            "const requestPrivateOutput = initialState.privateOutput",
            director_images,
        )
        self.assertIn("explicit_output: requestExplicitOutput", director_images)
        self.assertIn("private_output: requestPrivateOutput", director_images)
        self.assertNotIn("explicit_output: get().explicitOutput", director_images)
        self.assertNotIn("private_output: get().privateOutput", director_images)
        self.assertIn("state.explicitOutput", H3_PROFILES)
        self.assertIn("const explicitOutput = useStore(s => s.explicitOutput)", PROJECT_REFS)
        director_music = STORE[
            STORE.index("directorGenerateTrack: async"):
            STORE.index("directorSetEnergyBias: async", STORE.index("directorGenerateTrack: async"))
        ]
        self.assertIn("private_output: s.privateOutput", director_music)
        self.assertIn("explicit_output: s.explicitOutput", director_music)
        music_request = CLIENT[
            CLIENT.index("export interface DirectorMusicRequest"):
            CLIENT.index(
                "export interface DirectorPreparationStatus",
                CLIENT.index("export interface DirectorMusicRequest"),
            )
        ]
        self.assertIn("private_output?: boolean", music_request)
        self.assertIn("explicit_output?: boolean", music_request)
        music_client = CLIENT[
            CLIENT.index("export async function generateMusic"):
            CLIENT.index("// --- Tools:", CLIENT.index("export async function generateMusic"))
        ]
        self.assertIn(
            "params: DirectorMusicRequest & { director_request_id: string }",
            music_client,
        )
        self.assertIn("body: JSON.stringify(params)", music_client)

    def test_llm_prompt_requests_send_request_local_explicit_intent(self):
        enhance_call = STORE[
            STORE.index("const result = await api.llmEnhancePrompt({"):
            STORE.index("set(s => ({", STORE.index("const result = await api.llmEnhancePrompt({"))
        ]
        self.assertIn("explicit_output: state.explicitOutput", enhance_call)

        chat_submit = LLM_CHAT[
            LLM_CHAT.index("const submitBranch = async ("):
            LLM_CHAT.index("\n\n  const send = async () => {", LLM_CHAT.index("const submitBranch = async ("))
        ]
        self.assertIn(
            "const requestExplicitOutput = useStore.getState().explicitOutput",
            chat_submit,
        )
        self.assertIn("explicit_output: requestExplicitOutput", chat_submit)

        for function_name in ("llmChat", "llmEnhancePrompt"):
            declaration = CLIENT[
                CLIENT.index(f"export async function {function_name}"):
                CLIENT.index("): Promise<", CLIENT.index(f"export async function {function_name}"))
            ]
            self.assertIn("explicit_output?: boolean", declaration)

    def test_director_preview_requests_capture_request_local_explicit_intent(self):
        preview_actions = (
            ("directorPlanPrompts: async () => {", "directorPlanVideoPrompts: async () => {", 2),
            ("directorPlanVideoPrompts: async () => {", "directorGenerateStartImages: async", 1),
            ("shortFilmPlanPrompts: async () => {", "shortFilmPlanVideoPrompts: async () => {", 2),
            ("shortFilmPlanVideoPrompts: async () => {", "shortFilmPlanFromStory: async () => {", 1),
            ("shortFilmPlanFromStory: async () => {", "selectModel: async", 2),
        )
        for start, end, expected_calls in preview_actions:
            with self.subTest(action=start):
                action = STORE[STORE.index(start):STORE.index(end, STORE.index(start))]
                capture = "const requestExplicitOutput = get().explicitOutput"
                self.assertIn(capture, action)
                self.assertEqual(action.count(capture), 1)
                self.assertEqual(action.count("get().explicitOutput"), 1)
                self.assertNotIn("set({ explicitOutput", action)
                if "await get()._uploadDirectorRefs()" in action:
                    self.assertLess(
                        action.index(capture),
                        action.index("await get()._uploadDirectorRefs()"),
                    )
                self.assertEqual(
                    action.count("explicit_output: requestExplicitOutput"),
                    expected_calls,
                )

        request_types = (
            ("export interface DirectorV2PlanRequest", "export interface DirectorV2PlanResponse"),
            ("export async function planClipPromptsAndImages", "// --- Short Film Director ---"),
            ("export async function planShortFilmPrompts", "export async function getLlmStreamStatus"),
            ("export async function planShortFilmScript", "// --- CivitAI Browser ---"),
        )
        for start, end in request_types:
            with self.subTest(request=start):
                declaration = CLIENT[CLIENT.index(start):CLIENT.index(end, CLIENT.index(start))]
                self.assertIn("explicit_output?: boolean", declaration)

    def test_host_notice_is_shared_while_per_job_explicit_intent_stays_local(self):
        self.assertIn("{machineControls && <SettingsDrawer />}", APP_SOURCE)
        self.assertIn("Accept for this host", CONTROLS)
        self.assertIn("acceptHostTerm('lawful_use')", CONTROLS)
        self.assertIn("HOST_TERM_NOTICES.lawful_use.text", CONTROLS)
        self.assertIn("version: 1", HOST_TERMS_UI)
        self.assertIn("provider's terms and privacy policy apply separately", CONTROLS)
        self.assertIn("/api/v1/host-terms/accept", CLIENT)
        self.assertIn("state.activeWorkspace", STORE)
        self.assertIn("_queueHostTermsOperation", STORE)
        self.assertIn("document.current_version !== HOST_TERM_NOTICES[term].version", STORE)
        self.assertIn("const servicesConfigError = useStore", SERVICES)
        self.assertIn("clearServicesConfigError", SERVICES)
        self.assertNotIn("nsfw_accepted_at", SERVICES)
        self.assertIn("await updateConfig({ nsfw_mode: true })", SERVICES)
        self.assertIn("Each job's Explicit choice remains separate", SERVICES)
        self.assertIn("servicesConfigError: message", STORE)

    def test_mobile_notice_actions_are_reachable_in_scrollable_safe_area_surfaces(self):
        self.assertIn("h-[100dvh]", SETTINGS_DRAWER)
        self.assertIn("min-h-0 flex-1 overflow-y-auto overscroll-contain", SETTINGS_DRAWER)
        self.assertIn("safe-area-inset-bottom", SETTINGS_DRAWER)
        self.assertNotIn("h-[calc(100%-96px)]", SETTINGS_DRAWER)

        for source in (CONTROLS, INPUTS_PANEL, H3_PLAN_DIALOG):
            with self.subTest(source=source[:40]):
                self.assertIn("flex-col items-stretch", source)
                self.assertIn("w-full shrink-0 rounded", source)
                self.assertIn("sm:w-auto", source)

        self.assertIn("max-h-[calc(100dvh-1.5rem)]", H3_PLAN_DIALOG)
        self.assertIn("safe-area-inset-bottom", H3_PLAN_DIALOG)
        self.assertIn("min-h-0 overflow-y-auto overscroll-contain", H3_PLAN_DIALOG)
        self.assertIn("h-[100vh]", WELCOME)
        self.assertIn("supports-[height:100dvh]:h-[100dvh]", WELCOME)
        self.assertIn("max-h-full", WELCOME)
        self.assertIn(
            "sticky bottom-0 max-h-[55%] overflow-y-auto overscroll-contain shrink-0 border-t border-border",
            WELCOME,
        )
        for property_name, edge in (
            ("paddingTop", "top"),
            ("paddingRight", "right"),
            ("paddingBottom", "bottom"),
            ("paddingLeft", "left"),
        ):
            self.assertIn(
                f"{property_name}: 'max(0.75rem, env(safe-area-inset-{edge}))'",
                WELCOME,
            )

    def test_profiles_and_output_restore_preserve_only_user_policy(self):
        profile_block = STORE[
            STORE.index("applyH3PerformanceProfile: async (id) => {"):
            STORE.index("loadModelOptions: async", STORE.index("applyH3PerformanceProfile: async (id) => {"))
        ]
        explicit_setter = STORE[
            STORE.index("setExplicitOutput: (enabled)"):
            STORE.index("privateOutput: false", STORE.index("setExplicitOutput: (enabled)"))
        ]
        output_restore = STORE[
            STORE.index("loadSettingsFromOutput: async"):
            STORE.index("// Restore image refs as File objects")
        ]
        profile_helper = STORE[
            STORE.index("async function _applyH3ServerProfile("):
            STORE.index("const defaultParams:", STORE.index("async function _applyH3ServerProfile("))
        ]
        self.assertIn("_applyH3ServerProfile(profile, id, seq, get, set)", profile_block)
        self.assertNotIn("_modelTypeIsMature", profile_helper)
        self.assertNotIn("explicitOutput: true, privateOutput: true", profile_helper)
        self.assertNotIn("params:", explicit_setter)
        self.assertIn("const restoredExplicitOutput", output_restore)
        self.assertIn("selectedOutputMeta.explicit === true", output_restore)
        self.assertNotIn("!!model?.nsfw_only", output_restore)
        self.assertNotIn("_loraNeedsExplicit", output_restore)
        self.assertIn("explicitOutput: true, privateOutput: true", output_restore)

    def test_h3_pinkcherry_reconciliation_uses_server_fallback_truth(self):
        self.assertIn("fallback_profile_id: H3PerformanceProfileId | null", TYPES)
        self.assertIn("requested.fallback_profile_id", STORE)
        self.assertIn("profile.id === requested.fallback_profile_id", STORE)
        self.assertNotIn("requested.id === 'draft'", STORE)
        self.assertNotIn("requested.id === 'fast'", STORE)

        selector = STORE[
            STORE.index("selectModel: async (modelType)"):
            STORE.index("// Workspaces", STORE.index("selectModel: async (modelType)"))
        ]
        self.assertIn("const seq = ++_h3ProfileApplySeq", selector)
        self.assertIn("if (seq !== _h3ProfileApplySeq) return false", selector)
        self.assertIn("_applyH3ServerProfile(resolved, resolved.id", selector)
        self.assertNotIn("'quality'", selector)

    def test_all_manual_pinkcherry_surfaces_warn_but_remain_selectable(self):
        self.assertIn("pinkProfileIncompatible", MODEL_SELECTOR)
        self.assertIn("pinkReconciliationLabel", MODEL_SELECTOR)
        self.assertIn("await selectModel(model.model_type)", MODEL_SELECTOR)
        self.assertNotIn("disabled={pinkProfileIncompatible}", MODEL_SELECTOR)

        # A queued plan is frozen to its exact job/project. Editing its
        # overrides must not mutate the current Studio model/profile.
        self.assertIn("serverOptions", H3_PLAN_DIALOG)
        self.assertNotIn("selectModel(model)", H3_PLAN_DIALOG)
        self.assertIn("setModels(values =>", H3_PLAN_DIALOG)
        self.assertIn("approve({", H3_PLAN_DIALOG)

        director_picker = (ROOT / "ui/src/components/Sidebar/DirectorChat.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("pinkReconciliationLabel", director_picker)
        self.assertIn("await get().selectModel(modelType)", STORE)

    def test_explicit_base_is_warning_only_and_restores_normalize_editable_state(self):
        self.assertIn("Base may be less reliable for explicit intent.", CONTROLS)
        self.assertNotIn("setParam", CONTROLS)
        self.assertNotIn("prompt", CONTROLS.lower())

        preset = STORE[
            STORE.index("loadPreset: (preset)"):
            STORE.index("deletePreset:", STORE.index("loadPreset: (preset)"))
        ]
        output_restore = STORE[
            STORE.index("loadSettingsFromOutput: async"):
            STORE.index("// Restore image refs as File objects")
        ]
        self.assertIn("normalizeH3EditableProfile", preset)
        self.assertIn("normalizeH3EditableProfile", output_restore)
        self.assertIn("h3ProfileMatches(profile, state.params", STORE)

    def test_model_and_lora_selections_do_not_lock_explicit_on(self):
        for token in (
            "matureSelectionActive",
            "_activeSelectionHasMatureComponent",
            "_modelTypeIsMature",
            "_matureLoraKey",
            "classifiedLoraNames",
            "_loraNeedsExplicit",
            "registerMatureLoraFlags",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, STORE + CONTROLS + LORAS + DIRECTOR_LORAS)
        sidebar_mode = STORE[
            STORE.index("setSidebarMode: (mode) => {"):
            STORE.index("directorUploadAndAnalyze:", STORE.index("setSidebarMode: (mode) => {"))
        ]
        explicit_private_restore = "explicitOutput: true, privateOutput: true"
        self.assertNotIn(explicit_private_restore, sidebar_mode)
        output_restore = STORE[
            STORE.index("loadSettingsFromOutput: async"):
            STORE.index(
                "// Restore image refs as File objects",
                STORE.index("loadSettingsFromOutput: async"),
            )
        ]
        self.assertEqual(STORE.count(explicit_private_restore), 1)
        self.assertIn(explicit_private_restore, output_restore)

    def test_lora_selection_does_not_wait_for_content_metadata(self):
        for source in (LORAS, DIRECTOR_LORAS):
            self.assertNotIn("loraDetailsReady", source)
            self.assertNotIn("New selections are disabled", source)
            self.assertIn("detailsRequest !== loraDetailsRequest.current", source)
        self.assertIn("<DirectorPresetPicker", DIRECTOR_LORAS)

    def test_fresh_studio_video_defaults_to_h3_high(self):
        defaults = STORE[
            STORE.index("const modeDefaultModel"):
            STORE.index("export function getFamilyMode")
        ]
        self.assertIn("video: 'minimax_h3'", defaults)
        self.assertIn("model_type: 'minimax_h3'", STORE)
        self.assertIn("resolution: '1344x768'", STORE)
        self.assertIn("num_inference_steps: 20", STORE)
        self.assertIn("h3SelectedProfile: 'high' as const", STORE)

    def test_private_preview_requires_deliberate_session_scoped_reveal(self):
        self.assertIn("const privateBlurred = file.private && !privateRevealed", MEDIA_ITEM)
        self.assertIn("privateBlurred ? 'blur-2xl'", MEDIA_ITEM)
        self.assertNotIn("group-hover/private:blur-none", MEDIA_ITEM)
        self.assertIn("Blurred preview — click to Reveal", MEDIA_ITEM)
        self.assertIn("Reveal blurred preview for", MEDIA_ITEM)
        self.assertIn("Blur preview for", MEDIA_ITEM)
        self.assertIn("privatePreviewIdentity(file.workspace, file.name, file.revision)", MEDIA_ITEM)
        self.assertIn("privatePreviewWasRevealed(privateRevealKey)", MEDIA_ITEM)
        self.assertIn("rememberPrivatePreviewReveal(privateRevealKey)", MEDIA_ITEM)
        self.assertIn("forgetPrivatePreviewReveal(privateRevealKey)", MEDIA_ITEM)
        self.assertIn("`${workspace}\\u0000${name}\\u0000${revision}`", PRIVATE_PREVIEW)
        self.assertIn("storedFlag(privateRevealStorageKey(identity), memoryRevealed.get(identity))", PRIVATE_PREVIEW)
        self.assertIn("sessionStorage.setItem(privateRevealStorageKey(identity), '1')", PRIVATE_PREVIEW)
        self.assertIn("sessionStorage.removeItem(privateRevealStorageKey(identity))", PRIVATE_PREVIEW)
        self.assertIn("subscribePrivatePreviewReveal(privateRevealKey, syncReveal)", MEDIA_ITEM)
        withheld_media = MEDIA_ITEM[
            MEDIA_ITEM.index("{privateBlurred ? ("):
            MEDIA_ITEM.index(") : file.type === 'video'")
        ]
        self.assertNotIn("file.url", withheld_media)
        self.assertNotIn("<audio", withheld_media)
        self.assertNotIn("<video", withheld_media)
        self.assertNotIn("RetryImage", withheld_media)
        self.assertIn("src={file.url}", MEDIA_ITEM)
        self.assertIn("url={file.url}", MEDIA_ITEM)
        self.assertIn("video.removeAttribute('src')", MEDIA_ITEM)
        self.assertIn("video.load()", MEDIA_ITEM)
        self.assertNotIn("video.play()", MEDIA_ITEM)
        self.assertIn("return subscribePrivatePreviewChanges", PRIVATE_PREVIEW)
        self.assertIn("new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT", PRIVATE_PREVIEW)
        self.assertIn("hidePrivatePreviewsForWorkspace", PRIVATE_PREVIEW)
        self.assertIn("setPrivatePreviewsForWorkspaceRevealed", PRIVATE_PREVIEW)
        self.assertIn("PRIVATE_PROJECT_REVEAL_SESSION_PREFIX", PRIVATE_PREVIEW)
        self.assertIn("PRIVATE_HIDDEN_SESSION_PREFIX", PRIVATE_PREVIEW)
        self.assertNotIn("updateOutputPrivacy", MEDIA_ITEM)

    def test_private_thumbnail_and_reference_previews_use_same_reveal_contract(self):
        self.assertIn("const privateBlurred = file.private && !privateRevealed", THUMBNAILS)
        self.assertIn("privateBlurred ? 'blur-md'", THUMBNAILS)
        self.assertIn("privatePreviewIdentity(file.workspace, file.name, file.revision)", THUMBNAILS)
        self.assertIn("revealPrivatePreview(privateIdentity)", THUMBNAILS)
        self.assertIn("subscribePrivatePreviewChanges(() =>", THUMBNAILS)
        self.assertIn('type="button"', THUMBNAILS)
        self.assertIn("Reveal blurred preview and select", THUMBNAILS)
        self.assertIn("Reveal this blurred preview", THUMBNAILS)
        withheld_thumbnail = THUMBNAILS[
            THUMBNAILS.index("{privateBlurred ? ("):
            THUMBNAILS.index(") : file.type === 'video'")
        ]
        self.assertNotIn("file.url", withheld_thumbnail)
        self.assertNotIn("<img", withheld_thumbnail)
        self.assertNotIn("<VideoThumbnail", withheld_thumbnail)
        self.assertIn("src={file.url}", THUMBNAILS)
        self.assertIn("requestThumbnail(src, cacheKey, controller.signal)", THUMBNAILS)
        self.assertIn("controller.abort()", THUMBNAILS)
        self.assertNotIn("autoPlay", THUMBNAILS)
        self.assertNotIn("video.play(", THUMBNAILS)
        self.assertNotIn("group-hover/private:blur-none", THUMBNAILS)
        self.assertIn("output.metadata?.private === true", REFERENCE_LIBRARY)
        self.assertIn(
            "privatePreviewIdentity(project, `asset:${assetId}:${output.id}`, output.relative_path)",
            REFERENCE_LIBRARY,
        )
        preview_scope = REFERENCE_LIBRARY[
            REFERENCE_LIBRARY.index("function ProjectAssetPreview"):
            REFERENCE_LIBRARY.index("export function ProjectReferenceLibrary")
        ]
        self.assertIn("const needsInitialBlur = projectAssetOutputNeedsInitialBlur(output)", preview_scope)
        self.assertIn("const privateBlurred = needsInitialBlur && !revealed", preview_scope)
        self.assertIn("privateBlurred ? 'blur-xl'", preview_scope)
        self.assertEqual(
            preview_scope.count("src={privateBlurred ? undefined : getProjectAssetMediaUrl"),
            2,
        )
        self.assertIn(": <img\n              src={privateBlurred ? undefined : getProjectAssetMediaUrl", preview_scope)
        self.assertIn("subscribePrivatePreviewReveal(identity, syncReveal)", preview_scope)
        self.assertIn("Reveal reference preview", preview_scope)
        self.assertNotIn("fetch(", preview_scope)
        self.assertNotIn("setProjectAssetVariantStatus", preview_scope)
        self.assertNotIn("group-hover/private:blur-none", REFERENCE_LIBRARY)
        self.assertIn("Blur previews", MAIN_CONTENT)
        self.assertIn("Show previews", MAIN_CONTENT)
        self.assertIn("privatePreviewWorkspaceHasRevealed(activeWorkspace, 'all')", MAIN_CONTENT)
        self.assertIn("privatePreviewWorkspaceHasRevealed(activeWorkspace) ? 'some' : 'none'", MAIN_CONTENT)
        self.assertIn("'Reveal all remaining'", MAIN_CONTENT)
        self.assertIn("aria-pressed={privatePreviewActionPressed}", MAIN_CONTENT)
        self.assertIn("min-h-11", MAIN_CONTENT)
        self.assertIn("md:min-h-0", MAIN_CONTENT)
        self.assertNotIn("sm:min-h-0", MAIN_CONTENT)
        self.assertIn("Browser-session preview only; project access unchanged.", MAIN_CONTENT)
        self.assertIn("setPrivatePreviewsForWorkspaceRevealed(", MAIN_CONTENT)
        self.assertIn("activeWorkspace && !browsingUploads", MAIN_CONTENT)
        self.assertIn('aria-controls="mobile-thumbnail-panel"', THUMBNAILS)
        self.assertIn("createPortal(", THUMBNAILS)
        self.assertIn("document.body", THUMBNAILS)
        self.assertIn("inert={!mobileOpen}", THUMBNAILS)
        self.assertIn("installModalFocus({", THUMBNAILS)
        self.assertIn("closeModalIfTop(document, mobileDialogRef.current", THUMBNAILS)
        self.assertIn("appRoot: document.getElementById('root')", THUMBNAILS)
        self.assertIn("priority: 70", THUMBNAILS)
        self.assertIn('role="dialog"', THUMBNAILS)
        self.assertIn("aria-modal={mobileOpen ? true : undefined}", THUMBNAILS)
        self.assertNotIn("addEventListener('keydown'", THUMBNAILS)
        self.assertNotIn("requestAnimationFrame(() => mobileOpenerRef", THUMBNAILS)
        thumbnail_activation = THUMBNAILS[
            THUMBNAILS.index("onClick={() => {", THUMBNAILS.index("data-thumb-index={idx}")):
            THUMBNAILS.index("className={`absolute", THUMBNAILS.index("data-thumb-index={idx}"))
        ]
        guard = thumbnail_activation.index("if (onMobileClick && !onMobileClick()) return")
        self.assertLess(guard, thumbnail_activation.index("revealPrivatePreview(privateIdentity)"))
        self.assertLess(guard, thumbnail_activation.index("onThumbnailClick(idx)"))
        self.assertIn("min-h-11 min-w-11", THUMBNAILS)
        self.assertIn("Reveal all remaining", THUMBNAILS)
        self.assertIn("privatePreviewControl.onToggle", THUMBNAILS)
        self.assertNotIn("> Public", MAIN_CONTENT)

    def test_gallery_virtualization_and_card_stacking_follow_output_identity(self):
        self.assertIn("Map<string, { height: number; epoch: number }>", MAIN_CONTENT)
        self.assertIn("measurement?.epoch === measurementEpoch", MAIN_CONTENT)
        self.assertIn("if (epoch !== measurementEpoch) return", MAIN_CONTENT)
        self.assertIn("currentOutputIdentities.current.has(identity)", MAIN_CONTENT)
        self.assertIn("estimatedItemHeight, measurementVersion]", MAIN_CONTENT)
        self.assertNotIn("Map<number, number>", MAIN_CONTENT)
        self.assertIn("key={identity}", MAIN_CONTENT)
        self.assertIn("viewportAnchor", MAIN_CONTENT)
        self.assertIn("intraItemOffset", MAIN_CONTENT)
        self.assertIn("galleryScopeKey", MAIN_CONTENT)
        self.assertIn("scopeFence.current.generation", MAIN_CONTENT)
        self.assertIn("listFence.current.generation", MAIN_CONTENT)
        self.assertIn("requestAnimationFrame(() => requestAnimationFrame(align))", MAIN_CONTENT)
        self.assertIn("focus-within:z-20", MEDIA_ITEM)
        self.assertIn("event.target !== event.currentTarget", MEDIA_ITEM)
        self.assertIn("event.key !== 'Enter' && event.key !== ' '", MEDIA_ITEM)

    def test_preview_reveals_are_scrubbed_on_exact_project_access_transitions(self):
        load_scope = STORE[
            STORE.index("loadWorkspaces: async"):
            STORE.index("switchWorkspace: async")
        ]
        switch_scope = STORE[
            STORE.index("switchWorkspace: async"):
            STORE.index("createWorkspace: async")
        ]
        self.assertIn("revokedWorkspaces", load_scope)
        self.assertIn("hidePrivatePreviewsForWorkspace(workspace)", load_scope)
        self.assertIn("hidePrivatePreviewsForWorkspace(activeWorkspace)", switch_scope)
        self.assertIn("hidePrivatePreviewsForWorkspace(previousWorkspace)", switch_scope)
        for start, end in (
            ("\n  createWorkspace: async", "\n  unlockWorkspace: async"),
            ("\n  unlockWorkspace: async", "\n  lockWorkspace: async"),
            ("\n  lockWorkspace: async", "\n  lockAllWorkspaces: async"),
            ("\n  lockAllWorkspaces: async", "\n  deleteWorkspace: async"),
            ("\n  deleteWorkspace: async", "\n  storageDashboardOpen:"),
        ):
            action = STORE[STORE.index(start):STORE.index(end, STORE.index(start))]
            self.assertIn("hidePrivatePreviewsForWorkspace", action)

    def test_move_and_share_do_not_clear_private_flag(self):
        move_block = MEDIA_ITEM[
            MEDIA_ITEM.index("const handleMove = async"):
            MEDIA_ITEM.index("const handleSendToInput", MEDIA_ITEM.index("const handleMove = async"))
        ]
        share_block = MEDIA_ITEM[
            MEDIA_ITEM.index("const handleShare = async"):
            MEDIA_ITEM.index("const handleRevokeShare", MEDIA_ITEM.index("const handleShare = async"))
        ]
        self.assertIn("moveOutput(file.name, targetWs, file.workspace)", move_block)
        self.assertNotIn("private", move_block)
        self.assertIn("does not change the output's Private preview flag", share_block)
        self.assertNotIn("setOutputPrivacy", share_block)


if __name__ == "__main__":
    unittest.main()
