"""Focused source contracts for the project-scoped Chat branch UI."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "ui" / "src" / "components" / "LlmChat.tsx"
CHAT_CLIENT = ROOT / "ui" / "src" / "api" / "client.ts"
CLIPBOARD = ROOT / "ui" / "src" / "lib" / "clipboard.ts"


class LlmChatUiLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CHAT_UI.read_text(encoding="utf-8")
        cls.client = CHAT_CLIENT.read_text(encoding="utf-8")
        cls.clipboard = CLIPBOARD.read_text(encoding="utf-8")

    def test_retry_and_edit_build_replacement_branches(self):
        self.assertIn("messages.slice(0, assistantIndex)", self.source)
        self.assertIn("messages.slice(0, userIndex)", self.source)
        self.assertIn("return [...prefix, editedUser]", self.source)
        self.assertIn("requestId: api.createLlmRequestId()", self.source)
        self.assertIn("submitBranch(branch, messages, [], draft, false)", self.source)
        self.assertIn(
            "submitBranch(nextMessages, messages, requestImages, content, true)",
            self.source,
        )

    def test_cancel_preserves_branch_and_terminal_failure_rolls_back(self):
        self.assertIn("setMessages(pending.submittedMessages)", self.source)
        self.assertIn("setMessages(pending.retainedHistory)", self.source)
        self.assertIn("const interactionLocked = sending || resumeAvailable", self.source)
        self.assertIn("const branchControlsLocked = interactionLocked || editingTurn !== null", self.source)
        self.assertIn("disabled={branchControlsLocked || retryUnavailable}", self.source)
        self.assertIn("disabled={branchControlsLocked || editUnavailable}", self.source)

    def test_one_use_images_are_never_replayed_as_text_only(self):
        self.assertGreaterEqual(
            self.source.count("some(message => message.attachments?.length)"),
            2,
        )
        self.assertIn("requiresFreshImage: !!message.attachments?.length", self.source)
        self.assertIn("requires_fresh_image: pending.requiresFreshImage", self.source)
        self.assertIn("requiresFreshImage: value.requires_fresh_image === true", self.source)
        self.assertIn(
            "pending.requiresFreshImage && pending.images.length === 0",
            self.source,
        )
        self.assertIn(
            "Attach at least one image again before sending this message.",
            self.source,
        )
        self.assertIn(
            "Retry unavailable: this part of the conversation used temporary images.",
            self.source,
        )

    def test_streaming_status_is_scoped_accessible_and_not_persisted(self):
        self.assertIn("liveChatStatus.workspace === activeWorkspace", self.source)
        self.assertIn("liveChatStatus.projectInstance === projectInstance", self.source)
        self.assertIn('aria-label="Streaming assistant response"', self.source)
        self.assertIn('aria-label="Language model response status"', self.source)
        self.assertIn('role="log"', self.source)
        self.assertIn('aria-atomic="false"', self.source)
        self.assertIn("activeLiveStatus.partial_text", self.source)
        for field in (
            "attempt_limit", "generated_tokens_approx", "live_tps", "average_tps",
        ):
            self.assertIn(field, self.client)

        persist_start = self.source.index("function persistPendingOperation")
        persist_end = self.source.index("function removePendingOperation", persist_start)
        persisted = self.source[persist_start:persist_end]
        self.assertNotIn("latestStatus", persisted)
        self.assertNotIn("partial_text", persisted)
        self.assertNotIn("uploadedRefs", persisted)

    def test_progress_steps_use_plain_language_and_have_a_safe_fallback(self):
        progress_start = self.source.index("function chatProgressStep")
        progress_end = self.source.index("function downloadProgress", progress_start)
        progress_copy = self.source[progress_start:progress_end]
        for mapping in (
            "case 'queued': return 'Waiting to start'",
            "case 'loading': return 'Preparing the model'",
            "case 'inference':\n    case 'generating': return 'Writing the response'",
            "case 'retrying': return 'Trying the response again'",
            "case 'complete':\n    case 'completed': return 'Response complete'",
            "case 'failed': return 'Response could not finish'",
            "case 'cancelled': return 'Response stopped'",
        ):
            self.assertIn(mapping, progress_copy)
        self.assertIn("default: return 'Working on your response'", progress_copy)

    def test_progress_shows_friendly_steps_and_nests_reported_state(self):
        render_start = self.source.index("{sending && (")
        render_end = self.source.index("\n      <div data-chat-composer", render_start)
        progress_render = self.source[render_start:render_end]
        self.assertIn(
            "Step: {chatProgressStep(activeLiveStatus.phase || requestPhase)}",
            progress_render,
        )
        self.assertIn(
            "<span>Step: {chatProgressStep(activeLiveStatus.phase)}",
            progress_render,
        )
        self.assertNotIn("Step: {activeLiveStatus.phase", progress_render)
        self.assertEqual(progress_render.count(">Technical details</summary>"), 2)
        self.assertEqual(progress_render.count("Reported state:"), 2)

        for reported_state in (
            "Reported state: {activeLiveStatus.phase || requestPhase}",
            "Reported state: {activeLiveStatus.phase}",
        ):
            reported_index = progress_render.index(reported_state)
            technical_index = progress_render.rfind(
                ">Technical details</summary>",
                0,
                reported_index,
            )
            self.assertGreater(technical_index, -1)

    def test_model_details_lead_with_friendly_copy_and_nest_diagnostics(self):
        technical_start = self.source.index("function modelTechnicalMeta")
        technical_end = self.source.index("function chatProgressStep", technical_start)
        technical_copy = self.source[technical_start:technical_end]
        for diagnostic in (
            "model.source",
            "model.backend",
            "model.loading_phase",
            "profile.gpu_layers",
            "model.projector_available",
            "shared host cache",
            "speedMeta(model)",
        ):
            self.assertIn(diagnostic, technical_copy)

        details_start = self.source.index("{selectedModel && (")
        details_end = self.source.index("\n      </div>\n\n      <div", details_start)
        model_details = self.source[details_start:details_end]
        status_index = model_details.index("modelStatusCopy(selectedModel)")
        vision_index = model_details.index("modelVisionCopy(selectedModel)")
        technical_index = model_details.index(">Technical details</summary>")
        metadata_index = model_details.index("modelTechnicalMeta(selectedModel)")
        speed_reason_index = model_details.index("selectedModel.speed.reason")
        self.assertLess(status_index, technical_index)
        self.assertLess(vision_index, technical_index)
        self.assertLess(technical_index, metadata_index)
        self.assertLess(technical_index, speed_reason_index)
        self.assertEqual(model_details.count("modelTechnicalMeta(selectedModel)"), 1)

    def test_reload_during_preparation_keeps_committed_history_unchanged(self):
        submit_start = self.source.index("const submitBranch = async (")
        submit_end = self.source.index("\n\n  const send = async () => {", submit_start)
        submit = self.source[submit_start:submit_end]
        optimistic = submit.index("setMessages(nextMessages)")
        submission = submit.index("pending.submissionAttempted = true")
        acknowledgement = submit.index("pending.admissionAcknowledged = true")
        self.assertLess(submission, acknowledgement)
        self.assertIn(
            "admissionAcknowledged: value.admissionAcknowledged === true",
            self.source,
        )
        self.assertIn("reconcileLlmChatUploadRequest(", self.source)
        before_submission = submit[optimistic:submission]
        self.assertNotIn("persistMessages(", before_submission)
        after_submission = submit[submission:]
        self.assertIn("pending.submittedMessages", after_submission)
        self.assertIn("persistMessages(", after_submission)

    def test_loading_phase_and_composition_are_truthful(self):
        self.assertIn("if (phase === 'loading') return 'preparing'", self.source)
        self.assertGreaterEqual(
            self.source.count("operationRequestPhase(status.phase)"),
            2,
        )
        self.assertIn("!event.nativeEvent.isComposing", self.source)

    def test_edit_and_retry_controls_have_names_and_edit_focus(self):
        self.assertIn("aria-label={`Edit your message ${index + 1}`}", self.source)
        self.assertIn("aria-label={`Retry response ${index + 1}`}", self.source)
        self.assertIn("textareaRef.current?.focus()", self.source)

    def test_assistant_copy_uses_exact_content_and_local_http_fallback(self):
        self.assertIn("await clipboard.writeText(content)", self.clipboard)
        self.assertIn("return copyTextWithDocumentCommand(content, documentRef)", self.clipboard)
        self.assertIn("documentRef.createElement('textarea')", self.clipboard)
        self.assertIn("documentRef.execCommand('copy')", self.clipboard)
        copy_turn = self.source[
            self.source.index("const copyAssistantTurn = async"):
            self.source.index("const readRefusalSelection", self.source.index("const copyAssistantTurn = async"))
        ]
        self.assertIn("copyTextToClipboard(message.content)", copy_turn)
        self.assertNotIn("message.attachments", copy_turn)
        self.assertNotIn("message.performance", copy_turn)

    def test_assistant_copy_is_per_turn_accessible_and_not_generation_locked(self):
        self.assertIn("aria-label={`Copy response ${index + 1}`}", self.source)
        self.assertIn("`Response ${index + 1} copied.`", self.source)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', self.source)
        self.assertIn("assistantCopyNotice?.workspace === activeWorkspace", self.source)
        self.assertIn("assistantCopyNotice.projectInstance === projectInstance", self.source)
        self.assertIn("isAssistantCopyScopeCurrent(", self.source)
        copy_button = self.source[
            self.source.index('aria-label={`Copy response ${index + 1}`}'):
            self.source.index("</button>", self.source.index('aria-label={`Copy response ${index + 1}`}'))
        ]
        self.assertNotIn("disabled=", copy_button)
        self.assertIn("onClick={() => void copyAssistantTurn(index)}", copy_button)

    def test_refusal_capture_is_host_only_assistant_selection_only(self):
        self.assertIn(
            "const canManageRefusalLiterals = accessContext?.machine_controls === true",
            self.source,
        )
        self.assertIn("message.role === 'assistant'", self.source)
        self.assertIn("{canManageRefusalLiterals && (", self.source)
        self.assertIn("window.getSelection()", self.source)
        self.assertIn("selection.rangeCount !== 1", self.source)
        self.assertIn("selection.isCollapsed", self.source)
        self.assertIn("content.contains(range.startContainer)", self.source)
        self.assertIn("content.contains(range.endContainer)", self.source)
        self.assertIn("content.contains(range.commonAncestorContainer)", self.source)
        self.assertIn("const literal = selection.toString()", self.source)
        self.assertNotIn("literal = message.content", self.source)

    def test_refusal_literal_is_validated_edited_confirmed_and_not_persisted(self):
        self.assertIn("LLM_REFUSAL_LITERAL_MAX_CODE_POINTS = 256", self.client)
        self.assertIn("const characters = Array.from(literal)", self.client)
        self.assertIn("codePoint <= 0x1f", self.client)
        self.assertIn("codePoint >= 0x7f && codePoint <= 0x9f", self.client)
        self.assertIn("codePoint >= 0xd800 && codePoint <= 0xdfff", self.client)
        self.assertIn('aria-label="Confirm selected refusal wording"', self.source)
        self.assertNotIn("maxLength=", self.source)
        self.assertIn("api.addLlmRefusalLiteral(", self.source)
        self.assertIn("capture.literal,", self.source)
        self.assertIn("window.getSelection()?.removeAllRanges()", self.source)
        self.assertIn("refusalCaptureTriggerRefs.current.get(capture.messageIndex)?.focus()", self.source)

        persist_start = self.source.index("function persistPendingOperation")
        persist_end = self.source.index("function removePendingOperation", persist_start)
        persisted = self.source[persist_start:persist_end]
        for forbidden in ("refusalCapture", "selection", "literal"):
            self.assertNotIn(forbidden, persisted)

    def test_refusal_client_posts_only_literal_and_projects_content_free_status(self):
        self.assertIn("/api/v1/llm/refusal-literals", self.client)
        self.assertIn("body: JSON.stringify({ literal })", self.client)
        self.assertIn("added: body.added", self.client)
        self.assertIn("count: body.count", self.client)
        self.assertIn("revision: body.revision", self.client)

    def test_refusal_save_completion_is_scoped_and_stale_finally_cannot_unlock(self):
        for contract in (
            "token: refusalLiteralSaveTokenRef.current + 1",
            "workspace: activeWorkspace",
            "projectInstance,",
            "controller: new AbortController()",
            "saveRequest.controller.signal",
            "refusalLiteralSaveRef.current === saveRequest",
            "refusalLiteralSaveTokenRef.current === saveRequest.token",
            "useStore.getState().activeWorkspace === saveRequest.workspace",
            "projectInstanceRef.current === saveRequest.projectInstance",
            "useStore.getState().accessContext?.machine_controls === true",
            "if (refusalLiteralSaveRef.current === saveRequest)",
        ):
            self.assertIn(contract, self.source)
        self.assertGreaterEqual(
            self.source.count("cancelActiveRefusalLiteralSave()"),
            3,
        )

    def test_pointer_selection_snapshot_and_adjacent_focused_error(self):
        self.assertIn("onPointerDown={() => snapshotRefusalSelection(index)}", self.source)
        self.assertIn("onPointerCancel={() =>", self.source)
        self.assertIn("event.detail > 0", self.source)
        self.assertNotIn("onMouseDown={event => event.preventDefault()}", self.source)
        self.assertIn("snapshot?.messageIndex === messageIndex", self.source)
        self.assertIn("refusalCaptureErrorRefs.current.get(messageIndex)?.focus()", self.source)
        self.assertIn("refusalCaptureError?.messageIndex === index", self.source)
        self.assertIn("tabIndex={-1}", self.source)

    def test_branch_replacement_clears_indexed_refusal_capture_state(self):
        submit_start = self.source.index("const submitBranch = async (")
        submit_end = self.source.index("const send = async () =>", submit_start)
        submit = self.source[submit_start:submit_end]
        guard = submit.index(
            "if (interactionLocked || refusalLiteralSaveRef.current || !nextMessages.length) return"
        )
        replacement = submit.index("setMessages(nextMessages)")
        before_replacement = submit[guard:replacement]
        self.assertNotIn("cancelActiveRefusalLiteralSave()", before_replacement)
        self.assertIn("refusalSelectionSnapshotRef.current = null", before_replacement)
        self.assertIn("setRefusalCapture(null)", before_replacement)
        self.assertIn("setRefusalCaptureError(null)", before_replacement)

    def test_pending_refusal_save_locks_chat_without_aborting_durable_post(self):
        self.assertIn(
            "const interactionLocked = sending || resumeAvailable || savingRefusalLiteral",
            self.source,
        )
        self.assertIn(
            "interactionLocked || refusalLiteralSaveRef.current || !nextMessages.length",
            self.source,
        )
        self.assertIn(
            "if (interactionLocked || refusalLiteralSaveRef.current) return",
            self.source,
        )
        self.assertIn(
            "if (branchControlsLocked || refusalLiteralSaveRef.current) return",
            self.source,
        )
        submit_start = self.source.index("const submitBranch = async (")
        submit_end = self.source.index("const send = async () =>", submit_start)
        submit = self.source[submit_start:submit_end]
        self.assertNotIn("cancelActiveRefusalLiteralSave()", submit)
        self.assertIn("disabled={!messages.length || interactionLocked}", self.source)
        self.assertIn("disabled={branchControlsLocked || retryUnavailable}", self.source)

    def test_empty_transcript_is_project_catalog_and_history_scoped(self):
        start = self.source.index("{messages.length === 0 && (")
        end = self.source.index("{messages.map((message, index) => {", start)
        empty_state = self.source[start:end]

        self.assertIn("<Bot size={22} />", empty_state)
        self.assertIn("{chatEmptyHeading}", empty_state)
        self.assertIn("{chatEmptyBody}", empty_state)
        self.assertIn("text-text-primary", empty_state)
        self.assertIn("text-text-muted", empty_state)
        self.assertNotIn("<button", empty_state)

        self.assertIn(
            "const [projectInstanceWorkspace, setProjectInstanceWorkspace] = useState('')",
            self.source,
        )
        self.assertIn("setProjectInstanceWorkspace(activeWorkspace)", self.source)
        self.assertIn("setProjectInstanceWorkspace('')", self.source)
        self.assertIn(
            "setCatalogRead({ workspace: activeWorkspace, status: 'loading' })",
            self.source,
        )
        self.assertIn(
            "setCatalogRead({ workspace: activeWorkspace, status: 'ready' })",
            self.source,
        )
        self.assertIn(
            "setCatalogRead({ workspace: activeWorkspace, status: 'failed' })",
            self.source,
        )

        state_start = self.source.index("const chatEmptyState: ChatEmptyState =")
        state_end = self.source.index("const clearConversation", state_start)
        state = self.source[state_start:state_end]
        for contract in (
            "catalogRead.workspace !== activeWorkspace",
            "catalogRead.status === 'loading'",
            "catalogRead.status === 'failed'",
            "projectInstanceWorkspace !== activeWorkspace",
            "!projectInstance",
            "!effectiveModelId",
            "Opening this project’s conversation",
            "Loading the language model and conversation history…",
            "Language models unavailable",
            "Choose a language model above before starting a conversation.",
            "Start a conversation for this project with the selected language model.",
        ):
            self.assertIn(contract, state)

        unavailable_start = self.source.index("const unavailableReason =")
        unavailable_end = self.source.index("const activeLiveStatus", unavailable_start)
        unavailable = self.source[unavailable_start:unavailable_end]
        self.assertIn(": !effectiveModelId", unavailable)
        self.assertIn("'Choose a language model first.'", unavailable)


if __name__ == "__main__":
    unittest.main()
