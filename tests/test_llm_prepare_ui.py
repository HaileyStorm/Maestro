"""Focused source contracts for the browser-side cold LLM lifecycle."""
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
CHAT = (ROOT / "ui/src/components/LlmChat.tsx").read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
MUSIC = (
    ROOT / "ui/src/components/Sidebar/MusicControls.tsx"
).read_text(encoding="utf-8")


def source_block(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    return source[start_index:source.index(end, start_index)]


def store_action_block(name: str) -> str:
    start = STORE.index(f"  {name}: async", STORE.index("export const useStore"))
    next_action = re.search(r"\n  [A-Za-z][A-Za-z0-9]*: (?:async|\()", STORE[start + 1:])
    return STORE[start:] if next_action is None else STORE[start:start + 1 + next_action.start()]


class LlmPrepareClientContracts(unittest.TestCase):
    def test_prepare_is_short_polled_and_outlives_a_proxy_request(self):
        prepare = source_block(
            CLIENT,
            "export async function startLlmPreparation",
            "export async function fetchLlmPreparation",
        )
        wait = source_block(
            CLIENT,
            "export async function prepareLlmForRequest",
            "async function withLlmPreparation",
        )

        self.assertIn("/api/v1/llm/prepare", prepare)
        self.assertIn("cache: 'no-store'", prepare)
        self.assertIn("fetchLlmPreparation(", wait)
        self.assertIn("45 * 60 * 1000", CLIENT)
        self.assertGreater(45 * 60 * 1000, 120 * 1000)
        self.assertIn("document.visibilityState === 'hidden'", CLIENT)
        self.assertIn("LLM_PREPARATION_VISIBLE_POLL_MS", CLIENT)
        self.assertNotIn("LLM_PREPARATION_HIDDEN_POLL_MS", CLIENT)
        self.assertIn("document.addEventListener('visibilitychange'", CLIENT)
        self.assertIn("window.clearTimeout(timer)", CLIENT)
        self.assertIn("error.code === 'preparation_not_found'", wait)

    def test_prepare_payload_is_content_free_and_purpose_bounded(self):
        prepare = source_block(
            CLIENT,
            "export async function startLlmPreparation",
            "export async function fetchLlmPreparation",
        )
        payload = source_block(
            prepare,
            "const payload: LlmPreparationRequest",
            "const res = await fetch",
        )

        for allowed in (
            "workspace: request.workspace",
            "purpose: request.purpose",
            "model_id: request.model_id",
            "model_type: request.model_type",
            "vision_required: request.vision_required",
        ):
            self.assertIn(allowed, payload)
        for content_field in (
            "messages", "prompt:", "image_path", "image_paths", "media",
            "description", "lyrics",
        ):
            self.assertNotIn(content_field, payload)

    def test_abort_and_project_switch_stop_browser_wait_only(self):
        self.assertIn("signal?.addEventListener('abort'", CLIENT)
        self.assertIn("signal?.removeEventListener('abort'", CLIENT)
        self.assertNotIn("/api/v1/llm/prepare/cancel", CLIENT)
        self.assertIn("pending.controller.abort()", CHAT)
        self.assertIn(
            "projectInstanceRef.current === pending.projectInstance",
            CHAT,
        )
        self.assertIn("Cancel wait", CHAT)
        self.assertNotIn("Cancel preparation", CHAT)

    def test_prepare_failure_is_actionable_and_retryable(self):
        self.assertIn("export class LlmPreparationError", CLIENT)
        self.assertIn("readonly code: string", CLIENT)
        self.assertIn("readonly retryable: boolean", CLIENT)
        self.assertIn("'preparation_failed'", CLIENT)
        self.assertIn("'preparation_timeout'", CLIENT)
        self.assertIn("Try again to resume waiting.", CLIENT)
        self.assertIn("status >= 500 && status <= 599", CLIENT)

    def test_all_direct_llm_clients_prepare_before_inference(self):
        functions = (
            "writeSong", "directorV2Plan", "llmChat", "llmEnhancePrompt",
            "llmDescribeImage", "planAnglePrompts", "planClipPrompts",
            "planClipStructure", "classifySections",
            "planClipPromptsAndImages", "planDialogueScenes",
            "planShortFilmPrompts", "planShortFilmScript",
        )
        for index, name in enumerate(functions):
            with self.subTest(name=name):
                start = CLIENT.index(f"export async function {name}")
                candidates = [
                    CLIENT.find("\nexport async function ", start + 1),
                    CLIENT.find("\nexport interface ", start + 1),
                    CLIENT.find("\n// ---", start + 1),
                ]
                ends = [value for value in candidates if value >= 0]
                block = CLIENT[start:min(ends) if ends else len(CLIENT)]
                self.assertRegex(
                    block,
                    re.compile(
                        r"prepareLlmForRequest|withLlmPreparation|"
                        r"preparedConfiguredPost"
                    ),
                )

        for action in (
            "enhancePrompt", "directorAnalyzeAndPlan", "directorWriteSong",
            "directorSetEnergyBias", "directorPlanPrompts",
            "directorPlanVideoPrompts", "shortFilmUploadAndAnalyze",
            "shortFilmSetPacingBias", "shortFilmPlanPrompts",
            "shortFilmPlanVideoPrompts", "shortFilmPlanFromStory",
        ):
            with self.subTest(store_action=action):
                block = store_action_block(action)
                self.assertRegex(block, r"_begin(?:Workspace|Enhance|Director)LlmRequest\(")
                self.assertRegex(block, r"\bworkspace(?:\s*:|\s*,)")
                self.assertIn("lifecycle.signal", block)
                self.assertIn("lifecycle.ownsWorkspace()", block)
        self.assertIn("workspace: requestWorkspace", MUSIC)
        self.assertIn("useStore.getState().activeWorkspace !== requestWorkspace", MUSIC)
        self.assertIn("ownsWorkspace: () =>", STORE)

    def test_request_owned_loading_state_survives_project_switches(self):
        enhance = store_action_block("enhancePrompt")
        self.assertIn("_beginEnhanceLlmRequest(activeWorkspace)", enhance)
        self.assertIn("if (lifecycle.ownsWorkspace()) set({ isEnhancing: false })", enhance)
        self.assertIn("_enhanceLlmRequestToken === token", STORE)
        self.assertIn("_directorLlmRequestToken === token", STORE)
        self.assertIn("directorStep: previousStep", STORE)
        for action in (
            "directorAnalyzeAndPlan", "directorSetEnergyBias",
            "directorPlanPrompts", "directorPlanVideoPrompts",
            "shortFilmUploadAndAnalyze", "shortFilmSetPacingBias",
            "shortFilmPlanPrompts", "shortFilmPlanVideoPrompts",
            "shortFilmPlanFromStory",
        ):
            with self.subTest(action=action):
                self.assertIn("_beginDirectorLlmRequest(", store_action_block(action))


class LlmChatRecoveryContracts(unittest.TestCase):
    def test_chat_uses_one_idempotent_request_without_duplicate_turn(self):
        chat_client = source_block(
            CLIENT,
            "export async function llmChat",
            "export async function llmEnhancePrompt",
        )
        submit = source_block(
            CHAT,
            "const submitBranch = async (",
            "\n\n  const send = async () => {",
        )

        self.assertIn("requestId: api.createLlmRequestId()", submit)
        self.assertIn("request_id: pending.requestId", submit)
        self.assertIn("pending.submissionAttempted = true", submit)
        self.assertIn("persistPendingOperation(pending)", submit)
        self.assertIn("recoverLlmChatSubmission(", chat_client)
        self.assertIn("waitForLlmChatOperation(", chat_client)
        self.assertIn("submitLlmChat(request, signal)", chat_client)
        self.assertNotIn("createLlmRequestId()", chat_client)
        self.assertIn("setMessages(nextMessages)", submit)
        self.assertEqual(submit.count("setMessages(nextMessages)"), 1)
        self.assertIn("setMessages(pending.retainedHistory)", submit)
        self.assertIn("setDraft(pending.draft)", submit)
        self.assertIn("suspendedChatRequests.set(", submit)
        self.assertIn("waitForLlmChatOperation(", CHAT)
        self.assertIn("Resume wait", CHAT)
        self.assertIn("while (!operation)", CLIENT)
        self.assertIn("export class LlmChatWaitError", CLIENT)
        self.assertIn("err instanceof api.LlmChatWaitError", submit)
        self.assertGreaterEqual(CHAT.count("instanceof api.LlmChatWaitError"), 3)

    def test_chat_phases_are_truthful_and_uploads_remain_one_use(self):
        for phase in ("'queued'", "'preparing'", "'generating'"):
            self.assertIn(phase, CHAT)
        self.assertIn("operationRequestPhase(status.phase)", CHAT)
        self.assertNotIn("chatSubmitted", CHAT)
        self.assertIn("cleanupUnsubmittedUploads(pending)", CHAT)
        self.assertIn("if (pending.submissionAttempted", CHAT)
        self.assertIn("one-use upload references", CLIENT)
        self.assertNotIn("setError('Chat request failed')", CHAT)
        self.assertNotIn("Queued to prepare the selected LLM", CHAT)

    def test_director_reference_uploads_guard_project_ownership(self):
        helper = source_block(
            STORE,
            "  _uploadDirectorRefs: async (lifecycle)",
            "\n\n  directorPlanPrompts:",
        )
        self.assertGreaterEqual(helper.count("requireOwnership()"), 5)
        for action in (
            "directorPlanPrompts", "shortFilmPlanPrompts",
            "shortFilmPlanFromStory",
        ):
            with self.subTest(action=action):
                self.assertIn("_uploadDirectorRefs(lifecycle)", store_action_block(action))

    def test_hidden_chat_suspends_catalog_refreshes(self):
        refresh = source_block(
            CHAT,
            "  useEffect(() => {\n    if (!sending) return",
            "  useEffect(() => {\n    endRef.current",
        )
        self.assertIn("document.visibilityState === 'hidden'", refresh)
        self.assertIn("document.addEventListener('visibilitychange'", refresh)
        self.assertIn("window.clearTimeout(timer)", refresh)
        self.assertNotIn("setInterval", refresh)

    def test_executable_client_lifecycle_contract(self):
        completed = subprocess.run(
            ["node", "tests/llm_prepare_client_runtime.mjs"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
