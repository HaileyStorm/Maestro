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
H3_PROFILES = (
    ROOT / "ui/src/components/Sidebar/H3PerformanceProfiles.tsx"
).read_text(encoding="utf-8")
MODEL_SELECTOR = (
    ROOT / "ui/src/components/Sidebar/ModelSelector.tsx"
).read_text(encoding="utf-8")
H3_PLAN_DIALOG = (
    ROOT / "ui/src/components/H3GenerationPlanDialog.tsx"
).read_text(encoding="utf-8")
TYPES = (ROOT / "ui/src/types/index.ts").read_text(encoding="utf-8")
PROJECT_REFS = (
    ROOT / "ui/src/components/Sidebar/ProjectReferenceLibrary.tsx"
).read_text(encoding="utf-8")
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
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
        self.assertIn("Mark this Studio or Director job as explicit", CONTROLS)
        self.assertNotIn("updateServicesConfig", CONTROLS)
        self.assertNotIn("nsfw_accepted_at", CONTROLS)
        self.assertNotIn("PUBLIC_PROVIDERS", CONTROLS)
        self.assertIn("Private controls preview blur only", CONTROLS)
        self.assertIn("Project access rules always apply separately", CONTROLS)
        self.assertNotIn("visible to other authorized project users", CONTROLS)

        self.assertGreaterEqual(SIDEBAR.count("<GenerationPrivacyControls />"), 2)
        self.assertIn("{isDirector ? <DirectorChat /> : studioControls}", SIDEBAR)

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
        self.assertIn("explicit_output: get().explicitOutput", STORE)
        self.assertIn("private_output: get().privateOutput", STORE)
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

    def test_host_consent_stays_local_and_errors_are_visible(self):
        self.assertIn("{machineControls && <SettingsDrawer />}", APP_SOURCE)
        self.assertIn("const servicesConfigError = useStore", SERVICES)
        self.assertIn("{servicesConfigError}", SERVICES)
        self.assertIn("clearServicesConfigError", SERVICES)
        self.assertIn("nsfw_accepted_at: new Date().toISOString()", SERVICES)
        self.assertIn("void updateConfig({", SERVICES)
        self.assertIn("servicesConfigError: message", STORE)

    def test_profiles_and_mature_output_restore_enforce_job_policy(self):
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
        self.assertIn("_modelTypeIsMature(state, target)", profile_helper)
        self.assertIn("explicitOutput: true, privateOutput: true", profile_helper)
        self.assertNotIn("params:", explicit_setter)
        self.assertIn("const restoredExplicitOutput", output_restore)
        self.assertIn("selectedOutputMeta.explicit === true", output_restore)
        self.assertIn("!!model?.nsfw_only", output_restore)
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

        self.assertIn("pinkReconciliationLabel", H3_PLAN_DIALOG)
        self.assertIn("await selectModel(model)", H3_PLAN_DIALOG)
        self.assertIn("setModels(values =>", H3_PLAN_DIALOG)
        self.assertNotIn("disabled={Boolean(pinkReconciliationLabel", H3_PLAN_DIALOG)

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

    def test_mature_model_and_lora_selections_lock_explicit_on(self):
        self.assertIn("disabled={matureSelectionActive}", CONTROLS)
        self.assertIn("A selected mature model or LoRA requires", CONTROLS)
        self.assertIn(
            "if (!enabled && _activeSelectionHasMatureComponent(get())) return",
            STORE,
        )
        self.assertIn("_modelTypeIsMature(s, modelType)", STORE)
        self.assertIn("_matureLoraKey(modelType, filename)", STORE)
        self.assertIn("classifiedLoraNames", STORE)
        self.assertIn("_loraNeedsExplicit", STORE)
        self.assertIn("registerMatureLoraFlags(modelType, r.loras)", LORAS)
        self.assertIn("registerMatureLoraFlags(modelType, r.loras)", DIRECTOR_LORAS)
        self.assertIn(
            "activatedLoras.includes(name) || loraDetailsReady",
            DIRECTOR_LORAS,
        )
        sidebar_mode = STORE[
            STORE.index("setSidebarMode: (mode) => {"):
            STORE.index("directorUploadAndAnalyze:", STORE.index("setSidebarMode: (mode) => {"))
        ]
        self.assertIn("_activeSelectionHasMatureComponent(get())", sidebar_mode)
        self.assertIn("explicitOutput: true, privateOutput: true", sidebar_mode)
        pipeline_restore = STORE[
            STORE.index("loadDirectorFromPipeline: async"):
            STORE.index("loraBrowserOpen:", STORE.index("loadDirectorFromPipeline: async"))
        ]
        self.assertIn("_activeSelectionHasMatureComponent(get())", pipeline_restore)
        self.assertIn("explicitOutput: true, privateOutput: true", pipeline_restore)

    def test_lora_selection_fails_closed_until_mature_metadata_is_known(self):
        for source in (LORAS, DIRECTOR_LORAS):
            self.assertIn("loraDetailsReady", source)
            self.assertIn("New selections are disabled", source)
            self.assertIn("detailsRequest !== loraDetailsRequest.current", source)
        self.assertIn("if (!loraDetailsReady && !isActivated) return false", LORAS)
        self.assertIn("{loraDetailsReady && (", DIRECTOR_LORAS)
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
        self.assertIn("file.private && !privateRevealed ? 'blur-2xl'", MEDIA_ITEM)
        self.assertNotIn("group-hover/private:blur-none", MEDIA_ITEM)
        self.assertIn("Private preview — click to reveal", MEDIA_ITEM)
        self.assertIn("Click, tap, or press Enter", MEDIA_ITEM)
        self.assertIn("privatePreviewIdentity(file.workspace, file.name, file.revision)", MEDIA_ITEM)
        self.assertIn("privatePreviewWasRevealed(privateRevealKey)", MEDIA_ITEM)
        self.assertIn("rememberPrivatePreviewReveal(privateRevealKey)", MEDIA_ITEM)
        self.assertIn("forgetPrivatePreviewReveal(privateRevealKey)", MEDIA_ITEM)
        self.assertIn("`${workspace}\\u0000${name}\\u0000${revision}`", PRIVATE_PREVIEW)
        self.assertIn("sessionStorage.getItem(privateRevealStorageKey(identity))", PRIVATE_PREVIEW)
        self.assertIn("sessionStorage.setItem(privateRevealStorageKey(identity), '1')", PRIVATE_PREVIEW)
        self.assertIn("sessionStorage.removeItem(privateRevealStorageKey(identity))", PRIVATE_PREVIEW)
        self.assertIn("subscribePrivatePreviewReveal(privateRevealKey, syncReveal)", MEDIA_ITEM)
        self.assertIn("return subscribePrivatePreviewChanges", PRIVATE_PREVIEW)
        self.assertIn("new CustomEvent(PRIVATE_REVEAL_CHANGE_EVENT", PRIVATE_PREVIEW)
        self.assertNotIn("updateOutputPrivacy", MEDIA_ITEM)

    def test_private_thumbnail_and_reference_previews_use_same_reveal_contract(self):
        self.assertIn("file.private && !privateRevealed ? 'blur-md'", THUMBNAILS)
        self.assertIn("privatePreviewIdentity(file.workspace, file.name, file.revision)", THUMBNAILS)
        self.assertIn("revealPrivatePreview(privateIdentity)", THUMBNAILS)
        self.assertIn("subscribePrivatePreviewChanges(() =>", THUMBNAILS)
        self.assertIn('type="button"', THUMBNAILS)
        self.assertIn("Reveal private preview and select", THUMBNAILS)
        self.assertIn("Click, tap, or press Enter", THUMBNAILS)
        self.assertNotIn("group-hover/private:blur-none", THUMBNAILS)
        self.assertIn("output.metadata?.private === true", REFERENCE_LIBRARY)
        self.assertIn(
            "privatePreviewIdentity(project, `asset:${assetId}:${output.id}`, output.relative_path)",
            REFERENCE_LIBRARY,
        )
        self.assertIn("isPrivate && !revealed ? 'blur-xl'", REFERENCE_LIBRARY)
        self.assertIn("Reveal private reference preview", REFERENCE_LIBRARY)
        self.assertNotIn("group-hover/private:blur-none", REFERENCE_LIBRARY)
        self.assertIn("Blur previews", MAIN_CONTENT)
        self.assertIn("Show previews", MAIN_CONTENT)
        self.assertNotIn("> Public", MAIN_CONTENT)

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
