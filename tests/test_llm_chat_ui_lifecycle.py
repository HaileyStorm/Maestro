"""Focused source contracts for the project-scoped Chat branch UI."""
from __future__ import annotations

from pathlib import Path
import unittest


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
        self.assertIn("Reattach at least one image", self.source)
        self.assertIn("Retry unavailable: this branch contains one-use images", self.source)

    def test_streaming_status_is_scoped_accessible_and_not_persisted(self):
        self.assertIn("liveChatStatus.workspace === activeWorkspace", self.source)
        self.assertIn("liveChatStatus.projectInstance === projectInstance", self.source)
        self.assertIn('aria-label="Streaming assistant response"', self.source)
        self.assertIn('aria-label="LLM request status"', self.source)
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

    def test_reload_during_preparation_keeps_committed_history_unchanged(self):
        submit_start = self.source.index("const submitBranch = async (")
        submit_end = self.source.index("\n\n  const send = async () => {", submit_start)
        submit = self.source[submit_start:submit_end]
        optimistic = submit.index("setMessages(nextMessages)")
        submission = submit.index("pending.submissionAttempted = true")
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
        self.assertIn("aria-label={`Edit user turn ${index + 1}`}", self.source)
        self.assertIn("aria-label={`Retry assistant turn ${index + 1}`}", self.source)
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
        self.assertIn("aria-label={`Copy assistant turn ${index + 1}`}", self.source)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true"', self.source)
        self.assertIn("assistantCopyNotice?.workspace === activeWorkspace", self.source)
        self.assertIn("assistantCopyNotice.projectInstance === projectInstance", self.source)
        self.assertIn("isAssistantCopyScopeCurrent(", self.source)
        copy_button = self.source[
            self.source.index('aria-label={`Copy assistant turn ${index + 1}`}'):
            self.source.index("</button>", self.source.index('aria-label={`Copy assistant turn ${index + 1}`}'))
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


if __name__ == "__main__":
    unittest.main()
