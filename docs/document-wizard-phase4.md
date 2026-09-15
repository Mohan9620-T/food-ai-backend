# Interactive document wizard — Phase 4

## STATUS

Implementation complete. **Live acceptance FAIL; overall phase not accepted.** Browser verification remains pending. The user's request to complete all phases authorized the remaining implementation without another approval gate.

## Changes

- Build submits the saved wizard ID and revision to the existing automation endpoint. All answers must be present. The server supplies the persisted instruction and source, validates the operation through the existing registry, and uses the existing deterministic generator.
- Confirmed single-file requests with a known format use the existing CREATE planner. Complex or unresolved requests still use semantic planning. Reviewed answers take precedence over old chat turns.
- Completion stores the response in the same transaction as the generated attachments. Repeating the same Build returns the same response/file IDs. Stale, incomplete, or cancelled confirmations cannot execute. Total failure retains answers; Cancel permits a new wizard.
- File cards show the actual MIME badge, filename, byte count, View, Download, and disclosed assumptions. The exact general-knowledge disclaimer appears only for known general-knowledge provenance. Actual uploaded-source lineage is tracked through pipeline steps; unknown historical provenance is not guessed.
- Migration `0011_document_metadata` adds nullable attachment metadata. Applied and verified at PostgreSQL head.
- Generated content has an 8,192-token default budget and at most one schema-guided correction attempt. Each attempt retains the configured document deadline. Explicit “top N” spreadsheet requests validate the main table row count before storage.

## Files changed

- Backend: `app/api/chat_documents.py`, `app/schemas/{chat,chat_session,document_clarification}.py`, `app/models/chat.py`, `app/alembic/versions/0011_document_generation_metadata.py`.
- Services: `document_clarification_service.py`, `document_automation_service.py`, `document_pipeline_service.py`, `extraction_models.py`, `chat_document_service.py`, `chat_service.py`.
- Frontend: `components/document-wizard/`, new `components/document-result/`, `components/chat-window/`, and `models/chat.ts`.
- Configuration: `app/config/settings.py`, both `.env.example` files, README.
- Tests: wizard Build/planning, content correction, generation mock signatures and isolation, and result-card DOM tests.

## Tests / regression

- Full backend run: **657 passed, 1 skipped**, with 12 dependency deprecation warnings. [JUnit evidence](../test-results/document-wizard-final/backend-tests.xml).
- After the final planner/history/error-detail changes: **13 focused tests passed** covering Build, planning, and content correction, including one newly added planner test.
- Frontend: **132 passed in 12 files**. Typecheck, ESLint, Ruff, MyPy and production build passed.
- Build warnings: initial bundle 501.27 kB exceeds the 500 kB warning threshold by 1.27 kB; sidebar, meal-log, chat-input and chat-window CSS also exceed warning budgets.
- DOM axe checks reported no structural violations. Color contrast was excluded in jsdom and is not verified.

## Track A — real backend execution

All requests below used HTTP against the running server at `127.0.0.1:8000`, authenticated as the existing test account. These are separate from mocked unit tests.

| Check | Result | Evidence |
| --- | --- | --- |
| Real model questions and persisted answers | PASS | Session 48 received two questions, and both answers were saved. [Request/response history](../test-results/document-wizard-final/live-build-planning-timeout.json). |
| Generation, storage, download and replay mechanics | PASS for mechanics only | Earlier session 46 produced document 76, 7,538 bytes. Download reopened with openpyxl; replay returned the same attachment. [Evidence](../test-results/document-wizard-final/live-structural-only.json). |
| Requested 50-row workbook content | FAIL | Document 76 contained 55 entries, so it is not an accepted example. The new count check prevents this from being saved again. |
| Final Build after the count check | FAIL | Session 48, HTTP 200, `status=failed`, no attachments. Exact detail: `The AI did not provide exactly 50 table rows. Please retry.` [Final evidence](../test-results/document-wizard-final/live-final.json). |
| Repeat original PDF upload | PASS | `POST /chat/documents`, actual `sample-1.pdf`, HTTP 200, document 77/session 49. [Payloads, source hash and response](../test-results/document-wizard-final/live-source.json). |
| Repeat original PDF question | FAIL | `POST /chat/documents/automate`, exact original question, HTTP 200 with application `status=failed`: document AI timeout. Same evidence file. |
| Repeat original Word creation | NOT RUN | Stopped after the PDF question failed, as requested. Historical Phase 0 success is not current reliability evidence. |

Earlier attempts also encountered planning timeouts and invalid model structures. No timeout or invalid output is counted as successful generation. The final proven failure is incorrect model row count after one correction attempt; the PDF repeat separately timed out. Provider-side causes of those timeouts were not conclusively captured.

## Track B — frontend rendering

Each item is **PENDING MANUAL VERIFICATION — no browser tool available in this session**. Browser inventory returned `apps: [], browsers: []`.

1. Ask the underspecified Excel request, use Other, go Back, change an earlier answer, reload, and confirm review retains all chosen answers.
2. Click Build once; confirm a real successful answer appears in chat and the file card shows the returned filename/type/size. Confirm a failed Build shows its error and keeps the answers.
3. Confirm general-knowledge output shows `Built from general knowledge — check key facts before relying on it.` Confirm an output actually built from an upload omits it.
4. Click Download and reopen the saved file. Use View for PDF/Word and confirm the new tab/native handler opens the actual file.
5. Confirm no stale `AI analysis is currently unavailable` message is shown as the result of a successful new request.
6. Check keyboard focus, radio shortcuts, tab order, narrow-screen layout, light/dark presentation and browser axe/color contrast.

## Known limitations / readiness

Phase 4 is **not accepted** while the real model fails the requested output and the latest PDF question times out. Session 48 retains its reviewed answers for diagnosis/retry. No additional feature work proceeds on that failure. The Library remains out of scope. No repository commit or deployment was made.
