# Interactive document wizard — Phase 2

## STATUS

Backend clarification-state persistence: PASS in the real running application over HTTP. UI: **PENDING MANUAL VERIFICATION — no browser tool available in this session**. No full wizard/browser PASS is claimed.

## Files changed

- `app/schemas/document_clarification.py`: validated start, answer, navigation-update and stored-state models.
- `app/services/document/document_clarification_service.py`: start, retrieve and update session-scoped state without executing document operations.
- `app/repositories/chat_repository.py`: store a single versioned snapshot in an existing internal chat message.
- `app/api/chat_documents.py`: authenticated state endpoints using the existing session lock and PostgreSQL row lock.
- `tests/test_document_clarification_state.py`: state, reload, navigation, isolation, validation and concurrent-update tests.
- This report and local real-HTTP evidence.

No database migration or new state table. No frontend changes. Existing uncommitted work was preserved.

## State and API contract

State retains the original instruction, optional source document ID, validated OperationPlan, wizard ID, revision, current question ID and answers keyed by question ID. It is kept in `chat_messages` with `is_internal=true`; ordinary message history and AI history exclude it. Storage keys contain the original session ID, so session consolidation cannot misidentify another session's wizard as the destination's state.

| Endpoint | Behavior |
| --- | --- |
| `POST /chat/documents/clarification` | Save a structured non-executable question plan for an owned session. An existing wizard returns 409 instead of silently losing its answers. |
| `GET /chat/documents/clarification/{session_id}` | Restore the owned session's persisted state. Missing/foreign sessions return 404. |
| `PATCH /chat/documents/clarification/{session_id}` | Update one answer and/or the navigation target, preserving all other answers. Requires matching wizard ID and expected revision. |

Answers use either `option_id` or `text`, never both. Unknown questions/options, disallowed free text, blank answers and invalid navigation are rejected with 422 before persistence. Stale writes return 409. Question order comes from the plan; answers can arrive in any order. Answering all questions yields `ready_for_review`, not permission to execute. The stored OperationPlan remains non-executable.

Updates serialize through the existing process-local session lock and database session-row lock. Revision checks catch stale tabs even after requests are serialized.

## Tests passed / failed

The 12 new state tests passed, including reloading through a new database session/service instance, editing an earlier answer after completing the questions, no hidden-state leakage into visible or AI history, authentication/ownership isolation, source ownership, invalid update rollback, duplicate start, stale revision and concurrent writes.

Broader regression: **120 passed**, 12 existing dependency deprecation warnings, 64.73 seconds. No failures.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_document_clarification_state.py tests/test_document_wizard_schema.py tests/test_document_automation.py tests/test_chat_documents.py tests/test_chat.py -q
```

Ruff lint and formatting passed for all five changed Python files. MyPy passed on the four changed application files. Git whitespace check passed (existing Windows line-ending notices only).

## Real HTTP verification — 2026-09-15

The running server at `http://127.0.0.1:8000` created session **44**. The verification sent an explicit three-question test plan through the real start endpoint, then performed these real requests:

1. Answer question 2 before question 1 — 200.
2. Answer question 1 and navigate to question 3 — 200.
3. Navigate Back to question 1 without an answer — 200; previous answers unchanged.
4. Replace question 1 with free text, `Tamil Nadu and Kerala` — 200; question 2 preserved.
5. Answer question 3 — 200; `ready_for_review`.
6. Send an obsolete revision — 409; no overwritten state.
7. Send two simultaneous edits sharing one revision — one 200 and one 409; one revision increment, no lost update.
8. Read through a new HTTP client/connection — 200; exact state restored.
9. Fetch normal session history — 200; internal state absent from public messages.

No generation occurred, and no attachment was claimed. [Exact HTTP payloads, responses, statuses and final state](../test-results/document-wizard-phase2/evidence.json).

This exercises real HTTP handlers, validation, repository persistence and PostgreSQL concurrency. The question plan was explicitly supplied as test data, not proposed by an LLM; this run makes no claim about automatic model question generation.

## Frontend verification

**PENDING MANUAL VERIFICATION — no browser tool available in this session**:

- After Phase 3 supplies the cards, answer question 2, click Back and change question 1; confirm question 2's answer remains selected.
- Reload the chat and confirm the saved answers and navigation position return.
- Confirm review lists the revised answer and retained answers correctly.

The Phase 0 manual checks for answer rendering, attachment metadata, Download and stale analysis messages remain pending too.

## Known limitations / phase boundary

The persistence endpoints accept a validated non-executable question plan; they do not treat client-submitted plan data as an authorized execution plan. Automatic LLM question planning/transport and ordinary chat-turn presentation are not yet connected. Phase 3 adds question/review UI; Phase 4 must revalidate answers and use the existing execution pipeline for Build.

An existing wizard cannot yet be replaced or consumed through these endpoints; start returns 409. Completion/cancellation lifecycle belongs with the subsequent wizard orchestration work. No new generation path exists. The Library page remains out of scope.

## Ready for next phase

Ready for Phase 3 after approval. Stopped at the requested phase gate; UI checks are pending, not passed.
