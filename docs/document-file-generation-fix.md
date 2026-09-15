# PDF upload and actual file generation — fix and verification

15 September 2026. This follows the stateless wizard implementation and addresses the user's screenshots of a text-only refusal and `format_workbook requires ... XLSX` when a PDF was attached.

## What was broken

1. **The running frontend served stale code.** HTTP inspection of `localhost:4200/main.js` showed the old persisted wizard and an `automateDocument` method without `confirm`. New backend review responses could therefore not reach the current Build flow. The frontend was restarted with `--poll 1000`. The served bundle now includes the file router, confirmation parameter and Build button, and no longer includes `wizard_id`.
2. **Attached instructions bypassed the creation workflow.** The composer always sent an attached file to the upload handler. That handler interpreted edit/formatting words before storing the file, including when `analyze=false`. The PDF could be rejected as an invalid XLSX input, or a creation request could produce only a text summary.
3. **File actions phrased as questions were misclassified.** “Can you add … and update the Excel document” could reach a read-only answer or an operation intended to edit an existing workbook.
4. **AI availability unnecessarily affected plain PDF-to-Word copies.** The real provider returned HTTP 503; its CPU-only Ollama fallback exceeded the document deadline. A straightforward Word copy should not depend on AI generation.

## Changes

- `food-ai-ui/src/app/models/document-requests.ts`: shared classification for file requests, including Word, PDF, Excel, CSV and PowerPoint; quoted content does not supply routing keywords. Capability questions remain ordinary chat.
- `food-ai-ui/src/app/services/chat.ts`: attached file actions first save/extract with `analyze=false`, then call `/automate` with the returned source ID. The source ID persists through clarification, review and Build. Ordinary upload-and-read continues through document analysis.
- `food-ai-ui/src/app/models/chat.ts`: local automation metadata includes `sourceDocumentId`.
- `food-ai-ui/src/app/components/chat-input/chat-input.ts`: file requests reach automation; a successful upload is cleared even if later planning fails, preventing duplicate uploads on retry. Failed planning leaves the instruction available for retry.
- `app/api/chat_documents.py`: `analyze=false` now strictly saves/extracts; it does not run edits, formatting or table conversion.
- `app/services/document/document_automation_service.py`: file actions phrased as questions no longer take the read-only shortcut. A PDF with an explicit Excel creation/edit target creates a new output file. Plain PDF-to-Word requests use the existing deterministic converter; requests for summaries, additions, translations and other content changes still use generation. “Word summary of this PDF” resolves its target correctly.
- `app/services/chat_service.py`: accurate file capabilities and workflow instructions, immediate answers to the screenshot's capability questions, and one bounded retry for transient NVIDIA 502/503/504 responses before the existing fallback. The document AI deadline remains in force.
- Regression coverage: `tests/test_document_file_routing.py`, `tests/test_phase5_document_automation.py`, `tests/test_chat.py`, `food-ai-ui/src/app/models/document-requests.spec.ts`, and `food-ai-ui/src/app/services/chat.spec.ts`.

The review gate remains: no generated file before confirmation; **Build my document file** sends `confirm: true`. No new migration or persisted wizard state was added. Both local development servers were restarted onto the current code. Backend reload watches `app/` so editing test evidence does not restart the application.

## Real HTTP verification

Requests ran against `http://127.0.0.1:8000` using the real `sample-1.pdf` from Downloads (69,988 bytes, two pages), the configured AI provider, PostgreSQL-backed attachment storage, and authenticated downloads. These runs used no mocked planner/generator or TestClient.

| Step | Result |
| --- | --- |
| `POST /chat/documents`, attached PDF + “can you read this document end to end and tell me what it is about” | **PASS**: HTTP 200, `analysis_status=complete`, source ID 82, session 63. Answer identified “Your Company”, the Product Brochure, its placeholder date and Lorem ipsum content. The automatic retry recovered from a real provider 503. |
| `POST /chat/documents/automate`, source 82 + “can you create the word document on this content” | **PASS**: HTTP 200, `ready_for_review`, no attachments. |
| Same request with `confirm: true` | **PASS**: HTTP 200, `done`, document 83, `sample-1-converted.docx`. Build took 0.74 seconds. |
| `GET /chat/documents/83/download` | **PASS**: HTTP 200, 35,981 bytes, saved locally and reopened using `python-docx`. All extracted text from **both PDF pages** appears in the Word file after normalizing whitespace. |
| Save PDF with the screenshot's quoted extra text and Excel request | **PASS**: HTTP 200, source 84, session 64; no workbook-formatting error. |
| Review and confirm Excel creation | **PASS after retry**: review had no generated attachment. The first confirmed request timed out in the provider fallback. Retrying the saved request succeeded: HTTP 200, `done`, document 85. |
| `GET /chat/documents/85/download` | **PASS**: HTTP 200, 6,048 bytes; `openpyxl` reopened the workbook and verified the source company content and the additional quoted text. |
| Capability question through `/chat/` | **PASS**: HTTP 200 and an answer explaining actual downloadable file support. |

After the final capability fix and backend restart, the exact screenshot question returned the correct workflow in **0.127 seconds**, without a model call: [final HTTP evidence](../test-results/document-file-routing-live/capability-final.json).

[Exact requests, responses, timings and file hashes](../test-results/document-file-routing-live/evidence.json) · [Word page-text coverage](../test-results/document-file-routing-live/docx-coverage.json)

Generated files:

- [sample-1-converted.docx](../test-results/document-file-routing-live/sample-1-converted.docx)
- [Updated-Excel-Document-Content.xlsx](../test-results/document-file-routing-live/Updated-Excel-Document-Content.xlsx)

The earlier provider failures are retained as `provider-timeout-before-fix.json` and `excel-provider-timeout.json` in the same evidence directory. The successful runs do not guarantee continuous external-provider availability. Plain PDF-to-Word conversion now works independently of that provider; AI summaries and newly written content still need an available model.

Word reconstruction preserves extracted text and tables, not the PDF's exact visual layout. This sample passed a complete per-page text comparison; no pixel-perfect layout claim is made.

## Automated verification

- Full backend regression run: **652 passed, 1 skipped** ([JUnit](../test-results/document-file-routing-backend.xml)).
- Focused run after conversion and provider-retry changes: **100 passed** ([JUnit](../test-results/document-file-routing-final.xml)).
- Final chat/capability checks: **61 passed** ([JUnit](../test-results/document-file-capabilities-final.xml)).
- Final Angular suite: **149 passed in 13 files**.
- Ruff, MyPy, Angular typecheck, ESLint and production build passed. Existing dependency deprecation and component CSS-budget warnings remain.

## Browser verification

**PENDING MANUAL VERIFICATION — no usable browser surface is exposed in this session.** The browser inventory returned no apps/browsers. HTTP inspection verified the current JavaScript is served; it does not prove rendered layout or a literal Download-button click.

To check the actual UI:

1. Refresh `http://localhost:4200` with **Ctrl+F5** and start a new chat.
2. Attach the PDF and send **Read this document end to end and tell me what it is about**.
3. Send **Create a Word document from this PDF**. It is also supported as the message sent with the initial attachment.
4. Click **Build my document file** on the review card, then **Download** on the generated Word attachment.
5. Check that the answer and correct filename appear, the old XLSX error is absent for this flow, and no stale unavailable-analysis notice appears after a successful response.

No commit, push or deployment was performed.
