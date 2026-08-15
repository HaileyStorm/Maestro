"""Focused source contracts for the browser-side cold LLM lifecycle."""
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = (ROOT / "ui/src/api/client.ts").read_text(encoding="utf-8")
CHAT = (ROOT / "ui/src/components/LlmChat.tsx").read_text(encoding="utf-8")
STORE = (ROOT / "ui/src/stores/useStore.ts").read_text(encoding="utf-8")
PROMPT_INPUT = (
    ROOT / "ui/src/components/Sidebar/PromptInput.tsx"
).read_text(encoding="utf-8")
MUSIC = (
    ROOT / "ui/src/components/Sidebar/MusicControls.tsx"
).read_text(encoding="utf-8")
LAUNCH = (ROOT / "app/launch.py").read_text(encoding="utf-8")
UI_QUEUE_TEST = (
    ROOT / "ui/tests/enhance-queue-flow.test.mjs"
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
        self.assertIn("if (lifecycle.ownsWorkspace()) {", enhance)
        self.assertIn("enhanceRequestScope: null", enhance)
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

    def test_prompt_enhance_uses_scoped_async_operation_lifecycle(self):
        client = source_block(
            CLIENT,
            "export interface LlmEnhanceOperationScope",
            "export async function llmDescribeImage",
        )
        enhance = store_action_block("enhancePrompt")

        self.assertIn("request_id: scope.requestId", enhance)
        self.assertIn("project_instance: scope.projectInstance", enhance)
        self.assertIn("requestId: api.createLlmRequestId()", enhance)
        self.assertLess(
            enhance.index("requestId: api.createLlmRequestId()"),
            enhance.index("api.llmEnhancePrompt({"),
        )
        self.assertIn("onSubmissionAttempted", enhance)
        self.assertIn("_storeEnhanceOperation({", enhance)
        self.assertIn("project_instance: string", client)
        self.assertIn("options.projectInstance !== params.project_instance", client)
        self.assertIn("res.status !== 202", client)
        self.assertIn("recoverLlmEnhanceSubmission(", client)
        recovery = source_block(
            client,
            "async function recoverLlmEnhanceSubmission",
            "export async function waitForLlmEnhanceOperation",
        )
        self.assertLess(
            recovery.index("fetchLlmEnhanceOperation(scope"),
            recovery.index("submitLlmEnhance(request"),
        )
        self.assertIn("cache: 'no-store'", client)
        self.assertIn("waitForPreparationPoll(signal)", client)
        self.assertIn("assertLlmEnhanceProjectScope(scope", client)
        self.assertIn("assertLlmEnhanceStatusScope(status, scope)", client)
        self.assertIn("fetchLlmEnhanceResult(scope, signal)", client)

    def test_prompt_enhance_post_binds_existing_project_instance_before_worker(self):
        route = source_block(
            LAUNCH,
            '@api.post("/api/v1/llm/enhance-prompt")',
            '@api.post("/api/v1/llm/describe-image")',
        )
        self.assertIn('expected_project_instance = body.get("project_instance")', route)
        self.assertIn('hmac.compare_digest(', route)
        self.assertIn('status_code=409', route)
        self.assertIn('if key not in {"request_id", "project_instance"}', route)
        request_branch = route[route.index('if raw_request_id is not None:'):]
        self.assertLess(
            request_branch.index('hmac.compare_digest('),
            request_branch.index('_prompt_enhancement_runtime_snapshot('),
        )

    def test_prompt_enhance_persists_only_bounded_recovery_metadata(self):
        persistence = source_block(
            STORE,
            "function _writeStoredEnhanceOperations",
            "function _sameEnhanceScope",
        )
        self.assertIn("schemaVersion: 2", persistence)
        self.assertIn("ENHANCE_OPERATION_MAX_RECORDS", STORE)
        for field in (
            "requestId", "workspace", "projectInstance", "storedAt",
            "accountFingerprint", "claimToken", "settingsFingerprint",
        ):
            self.assertIn(field, STORE)
        for private_field in (
            "prompt:", "partial_text", "image_path", "imagePaths",
            "provider:", "api_key", "remote_url",
        ):
            self.assertNotIn(private_field, persistence)
        self.assertIn("ENHANCE_OPERATION_MAX_AGE_MS", STORE)
        self.assertIn("2 * 60 * 60 * 1000", STORE)
        self.assertIn("_findStoredEnhanceOperation", STORE)
        self.assertIn("_removeStoredEnhanceOperation(scope)", STORE)
        self.assertIn("resumeLlmEnhancePrompt", STORE)
        self.assertIn("void get().resumeEnhancePrompt()", STORE)
        self.assertIn(
            "return localStorage.getItem(ENHANCE_OPERATION_STORAGE_KEY) === encoded",
            persistence,
        )
        self.assertIn(
            "remaining.length >= ENHANCE_OPERATION_MAX_RECORDS) return false",
            persistence,
        )
        self.assertIn("durableRecoveryStored", STORE)
        self.assertIn("_hasOwnedStoredEnhanceOperation(scope)", STORE)
        self.assertIn("ENHANCE_LEDGER_LOCK_NAME", STORE)
        self.assertIn("_runEnhanceLedgerMutation", persistence)
        self.assertIn("{ mode: 'exclusive', signal: controller.signal }", persistence)
        self.assertIn("await options.onSubmissionAttempted?.()", CLIENT)
        self.assertLess(
            CLIENT.index("await options.onSubmissionAttempted?.()"),
            CLIENT.index("operation = await submitLlmEnhance(params, options.signal)"),
        )
        self.assertLess(
            CLIENT.index("await options.onSubmissionAttempted?.()"),
            CLIENT.index("throwIfAborted(options.signal)", CLIENT.index("await options.onSubmissionAttempted?.()")),
        )

    def test_prompt_enhance_disconnect_and_cancel_are_distinct(self):
        lifecycle = source_block(
            STORE,
            "function _beginEnhanceLlmRequest",
            "function _beginDirectorLlmRequest",
        )
        cancel = store_action_block("cancelEnhancePrompt")
        self.assertIn("controller.abort()", STORE)
        self.assertNotIn("cancelLlmEnhancePrompt", lifecycle)
        self.assertIn("api.cancelLlmEnhancePrompt(scope,", cancel)
        self.assertIn("_enhanceSubmissionAttemptedRequestId !== scope.requestId", cancel)
        pre_submission_cancel = source_block(
            cancel,
            "_enhanceSubmissionAttemptedRequestId !== scope.requestId",
            "try {",
        )
        self.assertLess(
            pre_submission_cancel.index("_enhanceStopWaiting?.()"),
            pre_submission_cancel.index("await _removeStoredEnhanceOperation(scope)"),
        )
        enhance = store_action_block("enhancePrompt")
        submission_callback = source_block(
            enhance,
            "onSubmissionAttempted: async () => {",
            "onOperationStatus: status => {",
        )
        self.assertLess(
            submission_callback.index("await _storeEnhanceOperation({"),
            submission_callback.index("_enhanceSubmissionAttemptedRequestId = scope.requestId"),
        )
        self.assertIn(
            "cancel during queued pre-POST persistence stays entirely local",
            UI_QUEUE_TEST,
        )
        self.assertIn("res.status === 404", CLIENT)
        self.assertIn("fetchLlmEnhanceOperation(scope, signal)", CLIENT)
        self.assertIn("Prompt Enhance cancellation is still confirming", CLIENT)
        self.assertIn("method: 'DELETE'", CLIENT)
        self.assertIn("partial_text: ''", STORE)
        self.assertIn("live_tps: null", STORE)
        self.assertIn("average_tps: null", STORE)

    def test_prompt_enhance_adoption_and_account_scrub_are_fenced(self):
        enhance = store_action_block("enhancePrompt")
        resume = store_action_block("resumeEnhancePrompt")
        scrub = source_block(
            STORE,
            "function _scrubAccountBoundProjectUi",
            "function _invalidateAccountRequests",
        )
        for block in (enhance, resume):
            self.assertIn("_enhancePromptEditGeneration", block)
            self.assertIn("get().params.prompt !== result.original", block)
            self.assertIn("_enhanceSettingsFingerprint(get())", block)
            self.assertIn("_accountIdentityIsCurrent(accountIdentityEpoch)", block)
        self.assertLess(
            resume.index("await _enhanceFingerprintSalt()"),
            resume.index(
                "if (_enhanceLlmRequestToken !== null "
                "|| get().enhanceRequestScope !== null)"
            ),
        )
        self.assertLess(
            resume.index(
                "if (_enhanceLlmRequestToken !== null "
                "|| get().enhanceRequestScope !== null)"
            ),
            resume.index("_beginEnhanceLlmRequest(scope.workspace)"),
        )
        self.assertIn("_clearStoredEnhanceOperations()", scrub)
        self.assertIn("_enhanceStopWaiting?.()", scrub)
        self.assertIn("enhanceRequestScope: null", scrub)

    def test_prompt_enhance_image_identity_is_digest_fenced_without_raw_storage(self):
        settings = source_block(
            STORE,
            "function _enhanceBytesToHex",
            "function _advanceAccountIdentityEpoch",
        )
        persistence = source_block(
            STORE,
            "function _writeStoredEnhanceOperations",
            "function _sameEnhanceScope",
        )
        for identity in (
            "state.params.image_start",
            "state.params.image_refs",
            "await _enhanceFileIdentity(startImage)",
            "Promise.all(imageRefs.map(file => _enhanceFileIdentity(file)))",
            "file.name",
            "file.size",
            "file.type",
            "file.lastModified",
            "await file.arrayBuffer()",
            "crypto.subtle.digest('SHA-256'",
            "{ name: 'HMAC', hash: 'SHA-256' }",
            "ENHANCE_FINGERPRINT_CLAIM_STORAGE_KEY",
            "globalThis.navigator?.locks",
            "ifAvailable: true",
            "holdForRealmLifetime",
            "ENHANCE_FINGERPRINT_LOCK_TIMEOUT_MS",
            "_enhanceReloadRecoveryAvailable",
            "_enhanceFingerprintClaimRotatedStored",
        ):
            self.assertIn(identity, settings)
        self.assertIn("claimToken", STORE)
        self.assertIn("_realmOwnsStoredEnhanceOperation", STORE)
        loader = source_block(
            STORE,
            "function _loadStoredEnhanceOperations",
            "function _writeStoredEnhanceOperations",
        )
        self.assertNotIn("localStorage.removeItem", loader)
        self.assertIn(
            "could not exclusively reclaim its original private recovery key",
            STORE,
        )
        self.assertIn("/^[0-9a-f]{64}$/i.test(parsed.settingsFingerprint)", STORE)
        self.assertIn("settingsFingerprint", STORE)
        self.assertNotIn("image_start:", persistence)
        self.assertNotIn("image_refs:", persistence)
        self.assertNotIn("file.name", persistence)

        queue_flow = (ROOT / "ui/tests/enhance-queue-flow.test.mjs").read_text(
            encoding="utf-8",
        )
        self.assertIn("waitForCondition", queue_flow)
        self.assertNotIn("attempt < 20", queue_flow)
        self.assertIn("class ExclusiveLocksFake", queue_flow)
        self.assertIn("locks.releaseAll()", queue_flow)
        self.assertIn("delayed reload claim cannot steal a manual successor", queue_flow)
        self.assertIn("full ledger current wait", queue_flow)
        self.assertIn("write failure timeout", queue_flow)
        self.assertIn("global ledger lock preserves concurrent two-owner appends", queue_flow)
        self.assertIn("global ledger lock serializes append", queue_flow)
        self.assertIn("class QueuedEnhanceLocksFake", queue_flow)
        self.assertIn("duplicateClaim.token", queue_flow)
        self.assertIn("missingLocksClaim", queue_flow)
        self.assertIn("erroredLocksClaim", queue_flow)
        self.assertNotIn("getEntriesByType", queue_flow)

    def test_prompt_input_uses_scoped_status_without_legacy_polling(self):
        self.assertNotIn("/api/v1/llm/status", PROMPT_INPUT)
        self.assertNotIn("/api/v1/llm/stream-status", PROMPT_INPUT)
        self.assertNotIn("fetchLlmStatus", PROMPT_INPUT)
        self.assertIn("routeEnhanceStatus?.partial_text", PROMPT_INPUT)
        self.assertIn("routeEnhanceStatus?.live_tps", PROMPT_INPUT)
        self.assertIn("routeEnhanceStatus.stage", PROMPT_INPUT)
        self.assertIn("cancelEnhancePrompt", PROMPT_INPUT)
        self.assertIn('aria-label="Cancel prompt enhancement"', PROMPT_INPUT)
        self.assertIn("mobile-control-target", PROMPT_INPUT)


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
