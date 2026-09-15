# Stateless document review and confirmation — implementation report

> Follow-up: the upload-routing and stale frontend issues reported afterward are addressed in [PDF upload and actual file generation — fix and verification](document-file-generation-fix.md).

Verified on 15 September 2026. This report supersedes the earlier persisted-wizard design in [document-wizard-final.md](document-wizard-final.md).

The requested backend and Angular changes are implemented. **Real HTTP review/confirmation checks passed, including valid downloaded DOCX files. Rendered browser acceptance remains pending.** Model-generated question wording and recommendations are not perfectly consistent; see the reliability section below.

## API contract

The existing endpoint is `POST /chat/documents/automate`.

| Schema | Field | Contract |
| --- | --- | --- |
| `ChatDocumentAutomationRequest` | `confirm: bool = False` | Omitted/false plans and returns a question or review. True re-plans and executes only if ready. |
| `ChatDocumentAutomationResponse` | `status` | Now accepts `ready_for_review`, alongside `done`, `clarification_required`, `partial`, and `failed`. |
| `ChatDocumentAutomationResponse` | `plan_summary: str \| None = None` | Review paragraph describing operations, source filenames/conversation content, output type, and a supplied output filename when available. |
| `ChatDocumentAutomationResponse` | `clarification: ClarificationQuestionOut \| None = None` | Structured choices when supplied; null for the existing plain-question path. |
| `ClarificationQuestionOut` | `question`, `options`, `allow_other` | Question text; options default to an empty list; Other defaults to true. |
| `ClarificationOption` | `id`, `label`, `description`, `recommended` | Nonempty ID/label; nullable description; recommended defaults to false. |

`session_id`, `instruction`, and optional `source_document_id` remain the request context. `response` remains populated for plain-text clients. Existing attachment, latest-document, and execution-step fields remain available.

**Caller behavior changes intentionally:** requests that previously executed immediately now return review unless they send `confirm: true`. No wizard ID, revision, question ID, state endpoint, table, or migration is required.

Every call plans again from the instruction and ordinary non-internal chat history. Questions and review text are saved as ordinary turns; an identical repeated review does not append duplicate turns. The confirm flag never substitutes for a missing answer. The review branch returns before the existing `DocumentPipelineService.execute_agent_step` / `ChatRepository.save_generated_document` execution path.

Example confirmed request from the final live run:

```json
{"session_id":60,"instruction":"Word document (DOCX)","confirm":true}
```

Without `confirm`, that same instruction returned:

```json
{
  "response": "Review before building: create document using the conversation content, producing DOCX. No file has been built yet.",
  "session_id": 60,
  "status": "ready_for_review",
  "plan_summary": "Review before building: create document using the conversation content, producing DOCX. No file has been built yet.",
  "clarification": null,
  "attachments": [],
  "latest_document_id": null,
  "steps": []
}
```

## Files changed for this request

Paths are relative to the backend repository. Other existing workspace changes are outside this refactor.

| Files | Changes |
| --- | --- |
| `app/schemas/chat.py` | Confirm field, review status/summary, structured clarification schemas; removed old wizard ID/revision request fields. |
| `app/services/document/document_automation_service.py` | Optional model choices, prompt instructions, parsing/validation, summary generation, safe parse diagnostics, stable handling of optional generated names. Uses `ChatService.chat` and ordinary history. |
| `app/api/chat_documents.py` | Review gate before execution, structured/plain clarification response, duplicate-review suppression, endpoint documentation; removed persisted wizard endpoints and orchestration. |
| `app/repositories/chat_repository.py` | Removed obsolete clarification-record read/write helpers. |
| `food-ai-ui/src/app/models/chat.ts` | Response fields/status and local automation-turn/choice metadata. |
| `food-ai-ui/src/app/services/chat.ts` | Sends confirm, accumulates local answers, routes successful responses into messages, retains in-flight processing handling. |
| `food-ai-ui/src/app/components/document-wizard/document-wizard.ts`, `.html`, `.css`, `.spec.ts` | Response-driven question and review cards, radios/recommended badges, Other/free text, running choices, Next/Build, busy/spinner/error handling, focus and DOM tests. |
| `food-ai-ui/src/app/components/chat-window/chat-window.ts`, `.html`, `.spec.ts` | Routes the latest pending automation response into the wizard; retains existing completed attachment cards. |
| `food-ai-ui/src/app/components/chat-input/chat-input.ts`, `.spec.ts` | Routes short wizard answers and source-free creation requests through automation. |
| `food-ai-ui/src/app/services/chat.spec.ts` | Confirm payload and answer accumulation/replay coverage. |
| `tests/test_document_wizard_build.py`, `tests/test_document_wizard_planning.py` | Review/no-generation, confirm, unanswered questions, plain/structured options, replay, file validity, failure and source-lineage coverage. |
| `tests/test_document_automation.py`, `tests/test_phase5_document_automation.py`, `tests/test_phase6_document_hardening.py` | Existing execution tests explicitly send `confirm: true`. |
| `README.md`, this report, `docs/document-wizard-final.md` | Current API behavior and verification record; prior report marked historical. |

Removed obsolete files from the earlier implementation: `app/schemas/document_clarification.py`, `app/services/document/document_clarification_service.py`, `food-ai-ui/src/app/models/document-wizard.ts`, and `tests/test_document_clarification_state.py`.

`tests/test_phase1_document_understanding.py` was not modified for this request. The earlier attachment-provenance migration `0011_document_metadata` remains; this refactor adds no migration. Existing internal state snapshots are ignored, not deleted.

Local HTTP evidence scripts are `test-results/verify_stateless_wizard_live.py` and `test-results/verify_stateless_options_live.py`.

## Track A — real running backend

Requests used `httpx` over the actual network listener at `http://127.0.0.1:8000`, with the configured model/provider. No TestClient, mocked planner, or direct internal generation calls were used in these live runs. Exact document request/response JSON, endpoints, and HTTP statuses are in the linked evidence; login credentials and bearer tokens are excluded.

### Plain question, review and build — session 56

| Check | Result | Evidence |
| --- | --- | --- |
| Ask `make me a product brochure` | PASS | HTTP 200, `clarification_required`, plain question asking which product; clarification null. |
| Confirm before providing product details | PASS | HTTP 200, still `clarification_required`, no attachments. |
| Provide product facts | PASS | HTTP 200, `ready_for_review`, no attachments/latest ID. |
| Repeat the same instruction without confirmation | PASS | HTTP 200, identical review summary; session has no document attachments. |
| Same instruction with `confirm: true` | PASS | HTTP 200, `done`, exactly one generated document, ID 79. |
| Download and reopen | PASS | `GET /chat/documents/79/download`, HTTP 200; 36,008-byte DOCX reopened with `python-docx`. |

[Exact HTTP evidence](../test-results/stateless-wizard-live/evidence.json) · [Downloaded DOCX](../test-results/stateless-wizard-live/Atlas_Lunch_Box_Brochure.docx)

SHA-256: `892c571a67eb87af542b95984acf2b9c1b22bc8fa7765f7bee9e0c7b9dc36c49`.

### Natural choices — session 57

PASS: Asking to choose between editable Word and printable PDF returned HTTP 200 with two actual model-provided options, descriptions, and PDF marked recommended. Sending the Word label returned `ready_for_review`, with no generated attachment. [Exact HTTP evidence](../test-results/stateless-wizard-live/options.json).

### Multiple missing details — session 60, final implementation

| Check | Result | Evidence |
| --- | --- | --- |
| Ask for a brochure with product details and output format unresolved | PASS for clarification gate | HTTP 200, `clarification_required`, no attachments. Initial wording combined both missing details; recommendation inconsistency is recorded below. |
| Confirm with details still missing | PASS | HTTP 200, still `clarification_required`, no attachments. |
| Supply product name, features, audience and tone | PASS | HTTP 200, asks Word/PDF format; two choices, Word recommended. |
| Answer `Word document (DOCX)` | PASS | HTTP 200, `ready_for_review`, zero returned attachments and no attachment messages in `GET /chat/sessions/60`. |
| Repeat `Word document (DOCX)` without confirm | PASS | HTTP 200, identical review text and still no attachment. |
| Repeat with `confirm: true` | PASS | HTTP 200, `done`, exactly one generated document, ID 80. |
| Download and reopen | PASS | `GET /chat/documents/80/download`, HTTP 200; 35,837-byte DOCX saved locally and reopened with `python-docx`; product content extracted successfully. |

[Exact HTTP evidence](../test-results/stateless-wizard-multiple-live/evidence.json) · [Downloaded DOCX](../test-results/stateless-wizard-multiple-live/Atlas-Lunch-Box-Product-Brochure.docx)

SHA-256: `501dd91d6938cb43da1514017f78d628aa7da3f5858598920fe65ac62441e257`.

The successful download after generation also demonstrates that the attachment bytes were available from backend storage. Live session histories show zero attachments before confirmation and one generated attachment afterward. Separate endpoint tests assert zero generated database records and no generator invocation before confirmation.

## Model reliability and limits

Structured choices worked in real Word/PDF questions, and open product questions exercised the null/plain fallback. **The prompt did not return perfectly consistent question presentation in every call.** Session 60 initially combined two missing details in one question and returned options without a recommended pick; the later format question returned a single recommendation correctly. The UI renders the supplied choices and marks a recommendation only when the model supplies it. It falls back to a text answer when choices are absent, null, or empty. No claim is made that every possible prompt will yield options or exactly one semantic question.

Live failures identified and fixed during this refactor:

- The model sometimes returned `clarification_options: null`; parsing now accepts null as well as omission and empty lists.
- The model invented an optional creation filename on a repeated review. For a single creation step, invented `title`/`filename` parameters are removed unless present in the user's instruction/history. User-supplied names and multi-step file references remain available. The final live repeated review was identical.

Malformed plans still fail safely. Provider fallback/latency remains observable: the final repeated-review request took several minutes before succeeding. Stateless model re-planning is not a mathematical guarantee of identical semantic plans for every conversation; the supplied repeat cases passed, and no cached plan or persisted state was introduced.

Earlier failed attempts are retained in the evidence directories, including filename drift and an incomplete answer from the test driver. Their later successful replacements are explicitly identified above. This report does not retroactively mark the previous 50-row generation or PDF-timeout acceptance cases as passed.

The running choices summary and response-card metadata are local UI state. A full reload restores ordinary chat history, not a persisted wizard card. Repeated confirmed requests are separate execution requests; this change does not add an idempotency key for completed builds. The active Build button prevents duplicate clicks while its request is in flight.

## Automated validation

| Check | Result |
| --- | --- |
| Full backend suite | **646 passed, 1 skipped**; [JUnit](../test-results/stateless-wizard-backend.xml). |
| Focused backend suite after the final parser/name fixes | **59 passed**, including unchanged Phase 1 understanding tests; [JUnit](../test-results/stateless-wizard-focused-final.xml). |
| Full Angular suite | **134 passed across 12 files**. |
| Ruff and targeted MyPy | Passed; MyPy checked the three changed API/planner/schema files. |
| Angular typecheck and ESLint | Passed. |
| Angular production build | Passed; initial bundle 498.16 kB. Existing component CSS budget warnings remain for sidebar, chat window/input and meal log. |
| DOM accessibility checks | Passed structural axe checks; browser color contrast/layout was not measured by jsdom. |
| Diff whitespace check | Passed. |

Tests cover recommendation badges, free-text fallback, selected-label submission, answer accumulation, review routing, identical instruction plus `confirm: true`, busy state, and valid generated/downloaded content. Existing Python dependency deprecation warnings remain. These automated tests are distinct from the real HTTP evidence above and from literal browser rendering below.

## Track B — rendered frontend

**PENDING MANUAL VERIFICATION — no browser tool available in this session.** The browser-control inventory returned no apps or browsers, so there was no usable surface on which to observe the running UI. DOM tests do not substitute for these checks.

Each item below is PENDING:

1. Open the running app, start a chat, and send `make me a product brochure`. Check that the real AI question is visible and its answer input works.
2. Ask for help choosing editable Word versus printable PDF. Check that model-provided options render as radios, the supplied recommended choice has a visible badge, and Other accepts free text. If the provider supplies no choices, check that the text-answer fallback appears.
3. Answer each question with Next. Check that the next question appears and all prior question/answer pairs remain visible in “Your choices so far”.
4. Finish the answers. Check that “Review your answers”, the plan summary, and “Build my document file” appear. Confirm that there is no generated attachment before clicking Build.
5. Click Build once. Check the disabled button/spinner while waiting, then the AI completion answer and attachment card with the correct filename/type.
6. Click the actual Download button. Open the downloaded file in the matching application. HTTP download/parser checks passed separately; this browser click has not been observed.
7. Check that the UI does not show a stale “AI analysis is currently unavailable” message after a successful backend response.
8. Check keyboard focus, radio/Other controls, small-screen layout and text contrast in the rendered application.

No commit, push or deployment was performed. The local backend remains running at `http://127.0.0.1:8000`.
