# Follow-up suggestions

After a completed chat answer, the UI shows one or two different, relevant follow-up ideas as plain paragraphs in the assistant's reply.

- Ideas have no buttons, selection, Other control, composer filling, or automatic sending. Users type their own next prompt in the normal composer and send it.
- The ideas suggest new applications, comparisons, or next steps instead of repeating the answer.
- Only the latest completed answer offers suggestions. Streaming, interrupted answers and pending document clarification do not show additional generic ideas.
- New chats and account changes remove previous suggestions. Refresh can restore suggestions for the latest saved answer.

## API

`POST /chat/sessions/{session_id}/suggestions` uses the normal authenticated session owner checks. Send `{"answer_hash":"<SHA-256 of trimmed displayed answer>"}`. It returns `message_id` and up to two `suggestions` containing `label` and `prompt`. The `prompt` field is the full suggestion sentence displayed as text; it is never submitted automatically.

The endpoint uses the saved conversation, rejects stale answers with 409, and returns 404 for another user's session. It never sends a chat message or builds a document itself.

Suggestion generation uses the configured chat model with a 512-token budget and a 12-second timeout. Results are cached in memory for 30 minutes (maximum 256 answers per process, scoped by user/message/answer). Provider failures fall back to two basic follow-up prompts and do not interrupt the completed answer. No migration or new provider credentials are required.

## Verification

Backend coverage is in `tests/test_chat_suggestions.py`. UI component tests verify plain text with no interactive controls, inaccessible or stale answers, provider fallback, and cleanup on conversation changes. The normal composer remains responsible for sending the next message.

The plain-text flow was verified on 2026-09-23: 74 targeted UI tests and 14 backend suggestion tests passed, along with type checks, lint, and the production build (existing bundle/style size warnings remain). Real NVIDIA responses through Uvicorn and the built UI in headless Edge produced two natural suggestion paragraphs. Clicking the text preserved the unsent draft and made no chat request; manually sending the next prompt received an answer. Mobile light/dark axe checks passed. Evidence is in the UI's ignored `test-results/suggestions/plain-ideas-verification.json`.
