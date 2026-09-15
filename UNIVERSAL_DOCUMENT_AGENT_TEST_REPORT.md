# Universal Document AI Agent — End-to-End Verification Report

## Executive verdict

**Overall status: PARTIAL (production-capable deterministic core, documented limits remain).**

The current implementation successfully reads all five supplied files, creates and reopens all seven supported output formats from three different source types, executes the implemented conversion matrix, preserves the supplied originals, persists generated attachments through the existing API path in automated tests, and completes the full backend and frontend regression suites.

It is not accurate to call the system universally complete. Live OCR is unavailable on this machine, external provider availability is intermittent, PDF-to-XLSX correctly refuses PDFs without extractable tables, arbitrary visual PDF editing is unsupported, and wide spreadsheet content loses visual fidelity when rendered as Word/PDF/PowerPoint. The deterministic harness is supplemented below by separate real NVIDIA and live-browser runs.

The current machine-readable real-file evidence is in [`test-results/universal-document-agent/2026-09-15-runtime-fix/results.json`](test-results/universal-document-agent/2026-09-15-runtime-fix/results.json). The exact live-browser evidence is in [`../test-results/runtime-debug/phase1/evidence.json`](../test-results/runtime-debug/phase1/evidence.json).

## Real Runtime Root Cause

### 1. Exact root cause and failing path

The observed upload failure was not caused by PDF extraction or DOCX rendering. It had three related runtime causes:

1. `ChatDocumentService._complete()` used `ChatService.stream_chat()` even though upload analysis and document generation are atomic JSON endpoints. NVIDIA can return an SSE `error` event inside HTTP 200; `_stream_nvidia()` assumed every event had `choices[0]`, so this became an unusable stream and fell through to the slow local provider.
2. The fallback Ollama model (`qwen3:8b`) is installed and reachable but is CPU-only on this machine. A full document prompt can exceed the document deadline, producing `ChatModelUnavailableError`. `upload_document()` caught that error and deliberately returned the saved upload with `analysis_status="unavailable"`.
3. Simple requests such as `can you create the word document on this content` were unnecessarily sent to the free-form AI planner. A malformed/invalid planner envelope produced `steps=[]`, while some plans repeated the DOCX target as the input `document_type`, causing a false PDF/DOCX source mismatch. Clear document Q&A and conversion requests had the same avoidable planner dependency.

Exact code locations:

- `app/services/chat_document_service.py`: `ChatDocumentService._complete`, `summarize`, and `generate_content`.
- `app/services/chat_service.py`: `ChatService.complete_chat`, `_complete_with_nvidia`, `_complete_with_ollama`, `_stream_nvidia`, and `_nvidia_body`.
- `app/services/document/document_automation_service.py`: `DocumentAutomationService.plan`, `_plan_unambiguous_question`, `_plan_unambiguous_creation`, and `_plan_unambiguous_conversion`.
- `app/services/document/document_pipeline_service.py`: `DocumentPipelineService.plan_structured` target/source normalization for `CREATE_DOCUMENT`.
- `app/api/chat_documents.py`: `upload_document` catches `ChatModelUnavailableError` and preserves the uploaded attachment/extracted text.

Upload and extraction succeeded because signature validation, deterministic PDF reading, persistence, and extraction all run before AI analysis. The failure occurred only when the extracted `DocumentRepresentation` was submitted for semantic analysis. The generator was not reached in the earlier failed automation request because planning returned no valid steps.

### 2. Running provider verification

The running process was checked, not merely the repository defaults:

| Setting/check | Runtime result |
|---|---|
| `APP_ENVIRONMENT` | `development` |
| `LLM_PROVIDER` | `nvidia` |
| NVIDIA key | Configured; value not printed |
| NVIDIA model | `nvidia/nemotron-3-super-120b-a12b` |
| NVIDIA read timeout | 30 seconds |
| Ollama URL | `http://localhost:11434/api/chat` |
| Ollama model | `qwen3:8b` |
| Ollama `/api/tags` | Reachable; exact `qwen3:8b` model present |
| Document overall deadline | 90 seconds |
| Document output budget | 2,048 tokens |

The grounded starting hypothesis that this running backend was unintentionally Ollama-primary was **refuted**: this process is explicitly NVIDIA-primary. The required architecture therefore remains one NVIDIA attempt followed by one Ollama fallback, with no parallel inference and no retry loop. No reverse/symmetric fallback was added.

A development-safe call through the existing `ChatService.complete_chat()` client with `Return a JSON object containing intent=DOCUMENT_QA.` passed in 1.768 seconds and returned `{"intent":"DOCUMENT_QA"}`. The live document request also reached NVIDIA and returned HTTP 200 in the successful exact flow. A later repetition recorded one real transient NVIDIA HTTP 503; the single Ollama fallback then exceeded the 90-second document deadline. The upload remained stored and downloadable, and the next factual Q&A and creation requests succeeded when NVIDIA recovered. This upstream intermittency remains a documented limitation.

### 3. Exact code fix

- Added a cancellable, async, non-streaming `ChatService.complete_chat()` for atomic document operations, preserving NVIDIA-primary -> one Ollama fallback.
- Hardened `_stream_nvidia()` to recognize provider `error` events and validate `choices`/`delta` instead of raising an accidental `TypeError`.
- Changed `ChatDocumentService._complete()` to use `complete_chat()` under one overall document deadline.
- Applied the separate 2,048-token document budget only to document completion; normal chat retains `OLLAMA_CHAT_MAX_TOKENS`.
- Added registry-validated deterministic planning for unambiguous Q&A, creation, and conversion. Complex, ambiguous, and multi-step requests still use the existing AI planner.
- Normalized `CREATE_DOCUMENT` so the requested target format is `output_type`, not a conflicting source type.
- Extended the centralized intent grammar with general creation verbs used by the required English/Thanglish requests; no list of exact sentence hacks was added.
- Added live-error, fallback, timeout, intent, context, test-order, and language-correction regressions.

### 4. Concrete reached/not-reached trace

Successful exact flow:

| Stage | Function/path | Result |
|---|---|---|
| Frontend upload | `ChatInput.requestDocumentResponse` -> `ChatService.uploadDocument` | REACHED |
| Upload API | `POST /chat/documents` -> `upload_document` | REACHED, HTTP 200 |
| Extraction | `ChatDocumentService.extract_document` / PDF reader | REACHED, pages 1-2 of 2 |
| Storage | `ChatRepository.save_document_upload` | REACHED, session 41/document 66 |
| Q&A provider | `summarize` -> `_analyze_extracted` -> `_complete` -> `complete_chat` | REACHED, grounded answer |
| Frontend automation | `ChatInput.requestDocumentAutomationResponse` -> `ChatService.automateDocument` | REACHED |
| Automation API | `POST /chat/documents/automate` -> `automate_document` | REACHED, HTTP 200 |
| Intent/planning | `DocumentAutomationService.plan` -> `_plan_unambiguous_creation` | REACHED, `CREATE_DOCUMENT`/DOCX |
| Context resolution | persisted latest document in session | REACHED, source document 66 |
| Executor | `DocumentPipelineService.execute_agent_step` | REACHED |
| Generator | deterministic DOCX generation/validation | REACHED |
| Generated storage | `ChatRepository.save_generated_document` | REACHED, document 67 |
| UI attachment | Angular response acceptance/rendering | REACHED and visually verified |
| Download | `GET /chat/documents/67/download` | REACHED, HTTP 200, 35,957 bytes |

In the original failed automation path, frontend, API, persisted context, intent entry, and AI planner were reached; executor, generator, generated storage, attachment, and download were **not reached** because no valid plan survived validation.

### 5. Real browser, context, Q&A, and attachment proof

The final exact flow ran in real headless Chromium against the active Angular app on `localhost:4200`, live FastAPI on `127.0.0.1:8000`, PostgreSQL, and the real configured provider. No API route was mocked or fulfilled by Playwright.

- Exact upload/Q&A request: `can you read this document and tell me what it is about`.
- Exact creation request: `can you create the word document on this content`.
- Upload: HTTP 200, `analysis_status=complete`, session 41, source document 66.
- Q&A: correctly identified the uploaded file as a Your Company product-brochure template dated September 04, 20XX and distinguished placeholder text from substantive facts.
- Creation: HTTP 200, `status=done`, `CREATE_DOCUMENT`, source document 66, latest/generated document 67.
- Attachment: `Product-Brochure---Your-Company.docx`, 35,957 bytes, visible in the assistant response with a Download button.
- Download: HTTP 200; saved file physically exists.
- Reopen: valid DOCX ZIP, `python-docx` reopened it, 11 paragraphs, one section, 1,868 extracted characters, expected title/date/content present.
- Visual QA: LibreOffice + the packaged document renderer produced a readable one-page render with styled title/headings and no observed clipping.

Screenshots and output:

- [`../test-results/runtime-debug/phase1/after-upload-and-qa.png`](../test-results/runtime-debug/phase1/after-upload-and-qa.png)
- [`../test-results/runtime-debug/phase1/after-creation-request.png`](../test-results/runtime-debug/phase1/after-creation-request.png)
- [`../test-results/runtime-debug/phase1/Product-Brochure---Your-Company.docx`](../test-results/runtime-debug/phase1/Product-Brochure---Your-Company.docx)
- [`../test-results/runtime-debug/phase1/rendered/page-1.png`](../test-results/runtime-debug/phase1/rendered/page-1.png)

A separate real-browser session asked `What date appears in this document?`; the persisted document ID 68 was resolved automatically and the answer was `September 04, 20XX`, followed by a successful `create a Word document from this` request and downloadable document 69. Required English/Thanglish variants are covered by the centralized intent tests; a real Thanglish run (`itha Word document ah create panni kudu`) also returned a generated/downloadable DOCX.

### 6. Why earlier tests missed it

Earlier document tests mocked a happy streaming generator and the repository Playwright suite mocks backend routes. They proved UI wiring and deterministic services but did not exercise NVIDIA's HTTP-200 SSE error event, the CPU Ollama deadline, or actual planner output. Some tests also continued patching `stream_chat` after atomic document work moved to `complete_chat`; those stale mocks caused long false failures and were updated. The final evidence combines unit/integration tests, real source files, real provider calls, real live API/database calls, real Chromium UI, physical download, structural reopen, and visual rendering.

### 7. Current real-file and regression result

The 2026-09-15 supplied-file harness result is **45 PASS, 0 FAIL, 0 SKIPPED, 3 explicitly UNSUPPORTED**. All five supplied source hashes remained unchanged. The three unsupported cases are PDF -> XLSX for PDFs with no extractable tables; the system refuses to fabricate empty spreadsheets. The real UAT workbook again produced 3,136 rows and 77 columns from 1,251 source rows.

## 1. Date and environment

| Item | Verified value |
|---|---|
| Test date | 2026-09-15 |
| OS | Windows 11 (`10.0.26200`) |
| Repository branch | `feat/universal-document-phase6` |
| Active frontend | `food-ai-ui` |
| Python | 3.14.7, 64-bit (the project configuration targets Python 3.13) |
| Node / npm | Node 22.23.2 / npm 10.9.8 |
| pypdf / pdfplumber | 6.1.1 / 0.11.10 |
| python-docx | 1.2.0 |
| openpyxl | 3.1.5 |
| python-pptx | 1.0.2 |
| reportlab | 4.4.4 |
| LibreOffice | 26.8.0.3 at `C:\Program Files\LibreOffice\program\soffice.exe` |
| pytesseract | 0.3.13 library installed |
| Tesseract executable | Not installed / not on `PATH`; live OCR not run |

Visual QA used LibreOffice and the packaged document renderer. A small diagnostic adapter supplied PDFium rendering because this venv does not contain `pdf2image`/Poppler; the final downloaded DOCX was rendered and visually inspected.

## 2. Repository state

- The working tree was already dirty with the ongoing Universal Document Agent implementation. Existing user changes were preserved.
- This verification pass made focused fixes and added a persistent real-sample harness/report. It did not discard unrelated changes.
- No commit or push was performed in this pass.
- No Alembic migration file was added or modified. The implemented changes do not require a schema migration.
- Current recent commits at audit time:
  - `4b0e359 feat: add document conversion pipelines and automation`
  - `1bbe365 feat: add universal document reading and extraction`
  - `7353e6c feat: add universal document generation workflows`
  - `1754a0f feat: split uploaded Excel items by category`
  - `f6c7e03 feat: format uploaded Excel workbooks in chat`

Important fixes verified in this pass:

1. Replaced the Excel reader's repeated cached-workbook scanning with lockstep worksheet iteration. The supplied 1,252-row workbook now reads in under one second in the final run instead of taking more than a minute or appearing stuck.
2. Added a deterministic spreadsheet row-count path so simple questions do not wait for NVIDIA/Ollama.
3. Added PDF/DOCX-to-TXT and PDF/DOCX-to-Markdown conversion routes.
4. Corrected CSV validation for multiline cells and fully blank records.
5. Corrected PDF scanned-page detection so vector-only blank pages do not incorrectly require OCR.
6. Added the real-sample acceptance runner and final-report evidence.

## 3. Capability matrix

Legend: **PASS** = real implementation and passing evidence; **PARTIAL** = works within the stated constraints; **N/A** = intentionally not supported.

| Format | Read / extract | Summarize / Q&A | Modify / format | Create | Convert | Real-file result and limitations |
|---|---|---|---|---|---|---|
| PDF | PASS | PARTIAL | PARTIAL | PASS | PARTIAL | All 3 supplied PDFs read. Page remove/extract/reorder and merge are supported. Text/table reflow conversion is not pixel-perfect. PDF→XLSX requires extractable tables. |
| DOCX | PASS | PARTIAL | PASS | PASS | PASS | Supplied DOCX read; generated DOCX reopened; DOCX→PDF/TXT/Markdown passed. Targeted edits and formatting are covered by tests. |
| XLSX | PASS | PASS | PASS | PASS | PASS | Supplied workbook read in 2.508 s. Deterministic row count, filtering, splitting, formatting, category expansion, XLSX→CSV/PDF all passed. XLSX→PDF can be slow and visually unwieldy. |
| CSV | PASS | PASS | PARTIAL | PASS | PASS | Creation and CSV→XLSX passed, including multiline/blank-row regression. Rich formatting is performed after conversion to XLSX rather than retained in CSV. |
| PPTX | PASS | PARTIAL | PASS | PASS | PASS | Generated PPTX reopened and PPTX→PDF passed. Targeted text edits are tested. Very wide spreadsheet-derived text can overflow slides. |
| TXT | PASS | PARTIAL | PARTIAL | PASS | PASS | Deterministic read/create and TXT→Markdown passed. No rich formatting model exists by design. |
| Markdown | PASS | PARTIAL | PARTIAL | PASS | PASS | Deterministic read/create and Markdown→TXT passed. Conversion preserves content rather than Office styling. |
| Image | PARTIAL | PARTIAL | N/A | N/A | N/A | Image/vision and OCR code paths have automated coverage, but the local Tesseract executable is unavailable, so live OCR was not run in this acceptance pass. |

Summarization, analysis, and document Q&A use the existing chat provider path; integration tests replace external responses for deterministic coverage. No separate LLM client was introduced. This report additionally includes real NVIDIA calls and live-browser/API/database proof.

## 4. Creation tests

The final real-file run created **21 documents**: all seven output formats from each of three different source types. Every output passed type-specific validation, was saved, reopened with the matching reader, and contained readable content or tables.

| Source | Output | Bytes | Reopen parser | Result |
|---|---:|---:|---|---|
| UAT XLSX | DOCX | 36,868 | python-docx | PASS — 3,506 chars, 1 table |
| UAT XLSX | PDF | 5,359 | pypdf/pdfplumber | PASS — 3,539 chars, 2 tables, 2 pages |
| UAT XLSX | XLSX | 7,216 | openpyxl | PASS — 2 populated sheets |
| UAT XLSX | CSV | 3,561 | csv | PASS — 21 rows |
| UAT XLSX | PPTX | 32,617 | python-pptx | PASS — 4 slides, 1 table |
| UAT XLSX | TXT | 3,518 | UTF-8 decoder | PASS — 33 lines |
| UAT XLSX | Markdown | 3,736 | Markdown reader | PASS — 34 lines, 1 table |
| sample-1 PDF | DOCX | 35,821 | python-docx | PASS — 735 chars |
| sample-1 PDF | PDF | 2,271 | pypdf/pdfplumber | PASS — 735 chars, 1 page |
| sample-1 PDF | XLSX | 5,588 | openpyxl | PASS — 1 populated sheet |
| sample-1 PDF | CSV | 756 | csv | PASS — 11 rows |
| sample-1 PDF | PPTX | 30,542 | python-pptx | PASS — 3 slides |
| sample-1 PDF | TXT | 745 | UTF-8 decoder | PASS — 21 lines |
| sample-1 PDF | Markdown | 747 | Markdown reader | PASS — 21 lines |
| nutrition DOCX | DOCX | 35,629 | python-docx | PASS — 396 chars |
| nutrition DOCX | PDF | 2,018 | pypdf/pdfplumber | PASS — 396 chars, 1 page |
| nutrition DOCX | XLSX | 5,399 | openpyxl | PASS — 1 populated sheet |
| nutrition DOCX | CSV | 411 | csv | PASS — 11 rows |
| nutrition DOCX | PPTX | 30,367 | python-pptx | PASS — 3 slides |
| nutrition DOCX | TXT | 406 | UTF-8 decoder | PASS — 21 lines |
| nutrition DOCX | Markdown | 408 | Markdown reader | PASS — 21 lines |

The harness itself writes isolated artifacts under `test-results`; it does not claim to be a database test. Storage, attachment metadata, session history, authorization, and download behavior are separately verified through FastAPI/TestClient API tests in the full backend suite.

Visual QA result:

- Normal text-derived DOCX/PDF/PPTX samples were readable with no observed clipping.
- The wide UAT spreadsheet produces readable PDF/DOCX content, but columns wrap heavily.
- The UAT-derived PPTX overview overflows its content region. This is a known presentation-fidelity gap, although the file is valid and reopens.

Rendered visual evidence is under [`test-results/universal-document-agent/2026-09-11-run3/visual-qa`](test-results/universal-document-agent/2026-09-11-run3/visual-qa).

## 5. Conversion tests

| Source → target | Real input | Result | Time | Fidelity / limitation |
|---|---|---:|---:|---|
| PDF→DOCX | sample-1.pdf | PASS | 0.382 s | Reconstructed from extracted content; not pixel-perfect |
| PDF→TXT | sample-1.pdf | PASS | 0.318 s | Reading order retained; visual layout omitted |
| PDF→Markdown | sample-1.pdf | PASS | 0.268 s | Text structure reconstructed |
| PDF→XLSX | sample-1.pdf | UNSUPPORTED | 0.251 s | No extractable tables; no empty workbook created |
| PDF→DOCX | nutrition-document.pdf | PASS | 0.159 s | Reconstructed content |
| PDF→TXT | nutrition-document.pdf | PASS | 0.051 s | Text-only fidelity |
| PDF→Markdown | nutrition-document.pdf | PASS | 0.046 s | Text structure reconstructed |
| PDF→XLSX | nutrition-document.pdf | UNSUPPORTED | 0.037 s | No extractable tables |
| PDF→DOCX | sample-1mb.pdf | PASS | 0.497 s | Reconstructed content |
| PDF→TXT | sample-1mb.pdf | PASS | 0.409 s | Text-only fidelity |
| PDF→Markdown | sample-1mb.pdf | PASS | 0.388 s | Text structure reconstructed |
| PDF→XLSX | sample-1mb.pdf | UNSUPPORTED | 0.232 s | No extractable tables |
| DOCX→PDF | nutrition-document.docx | PASS | 11.685 s | LibreOffice-rendered; minor layout differences possible |
| DOCX→TXT | nutrition-document.docx | PASS | 0.106 s | Styling omitted |
| DOCX→Markdown | nutrition-document.docx | PASS | 0.104 s | Core headings/paragraphs/tables retained |
| XLSX→CSV | UAT workbook | PASS | 4.363 s | Values only; workbook styling omitted |
| XLSX→PDF | UAT workbook | PASS | 116.820 s | 18,786,485 bytes, 300 pages; workbook print settings create a large output |
| PPTX→PDF | generated sample PPTX | PASS | 12.944 s | 3 pages; LibreOffice-rendered |
| CSV→XLSX | generated UAT CSV | PASS | 0.122 s | Multiline values and nonempty row count validated |
| TXT→Markdown | generated sample TXT | PASS | 0.007 s | Content preserved |
| Markdown→TXT | generated sample Markdown | PASS | 0.006 s | Markdown syntax remains plain text |

Unsupported conversions fail explicitly before an empty or misleading file is returned. Every successful conversion was reopened and validated, and source hashes were checked again afterward.

## 6. Modification and preservation tests

### Real supplied workbook

- Input: `UAT-EMOS-Dish Template 10092026.xlsx`
- Operation: `EXPAND_DISH_BY_DIETARY_CATEGORY`
- Input data rows: 1,251
- Output data rows: **3,136**
- Output columns: **77**
- Runtime: **11.182 seconds**
- Output: `UAT-EMOS-Dish_Template_10092026_category_expanded.xlsx` (796,122 bytes)
- Original SHA-256 remained unchanged.

The expansion created exactly one row for each active `x` marker across Regular, Easy to Chew, Soft & Bite, Minced & Moist, and Pureed, while preserving rows without an active marker.

Automated modification coverage also verifies:

- DOCX exact text replacement and formatting with 0-match, 1-match, and multi-match handling.
- XLSX value/style edits, filtering, sorting, formulas, tables, unrelated sheets, merged-range safety, and explicit worksheet disambiguation.
- PPTX text edits and presentation structure preservation.
- PDF page removal, extraction, reordering, and multi-file merge.
- Source bytes remain unchanged when a new output is generated.

Combined modification/spreadsheet regression: **119 passed, 1 skipped**. The skipped repository-fixture test only searches repository fixture paths; the same attached file from Downloads passed the persistent harness above.

## 7. Natural-language routing evidence

The centralized registry exposes 16 canonical operations, including `EXPAND_DISH_BY_DIETARY_CATEGORY`, creation, reading, extraction, summarization, analysis, Q&A, conversions, page operations, merge, and workbook filtering/sorting.

Examples verified by tests:

| Request | Resolved operation(s) | Result |
|---|---|---|
| `Create separate rows based on Regular, Easy to Chew, Soft & Bite, Minced & Moist and Pureed.` | `expand_dish_by_dietary_category` | PASS on the real UAT file |
| `Add a filter only to the Category* column.` | `filter_column` | PASS; target column extracted |
| `Convert items.csv to XLSX, then format the workbook professionally` | `convert_document` → `format_workbook` | PASS |
| `Convert items.csv to XLSX, then format the workbook professionally, then add a filter to the Category column` | convert → format → filter | PASS; output IDs chained |
| `Turn inventory.csv into a polished workbook and make Category easy to search.` | convert → format → filter | PASS; downloadable final workbook |
| `Organize products.xlsx into a tab for each category and make it presentation-ready.` | split → format | PASS; Fruit/Vegetable sheets and frozen headers |
| `Make it a workbook.` with two candidate CSVs | clarification required | PASS; no guessed input |

Provider-produced automation plans are schema-validated against the central operation registry; deterministic spreadsheet shortcuts do not invoke the LLM for row-count requests.

## 8. Multi-step pipeline evidence

1. **CSV → formatted XLSX → filtered XLSX**
   - Each intermediate file was validated and persisted.
   - Step 2 consumed step 1's output document ID; step 3 consumed step 2's output ID.
   - `latest_document_id` matched the downloadable final attachment.
   - A subsequent request converted that latest workbook back to CSV.

2. **PDF read/extract → Excel + Word report**
   - Four steps completed.
   - Both `extracted-data.xlsx` and `document-report.docx` were attached and reopened.
   - Generated Excel contained the expected customer value and Word contained the expected report heading.

3. **Word read/update → final PDF**
   - The generated DOCX was passed to the converter by its latest output reference.
   - Final PDF downloaded and reopened with at least one page.

4. **Two named PDFs → merged PDF**
   - Both source document IDs were preserved in the step record.
   - Downloaded output contained both pages.

5. **Failure/clarification behavior**
   - Invalid intermediate files stop the pipeline before the next step.
   - Partial failures do not claim `done`.
   - Ambiguous references return one clarifying question rather than selecting a file arbitrarily.

These API tests use real parsers/generators, test storage, session history, attachment/download endpoints, and a test database. External LLM planner responses are mocked for determinism.

## 9. Security and hardening

Verified automated protections include:

- Authentication and per-user document authorization.
- Cross-user session/document access rejection.
- Filename sanitization and generated-name control.
- File signature/type validation rather than trusting only extension or MIME type.
- Corrupt/truncated/oversized document rejection.
- ZIP entry-count, decompression-ratio, and uncompressed-size limits for Office files.
- Formula preservation with safe parsing behavior.
- Output re-open validation before persistence.
- Pipeline step-count/arity/type validation.
- No destructive overwrite of uploaded originals.

Security/hardening category run: **55 passed**.

## 10. Existing-functionality regression

| Check | Result |
|---|---|
| Full backend suite | **616 passed, 1 skipped**, 12 warnings, 280.63 s |
| Backend coverage | **90.03%** (required minimum 90%) |
| Ruff lint | PASS — all checks passed |
| Ruff format check | PASS — 158 files formatted |
| MyPy | PASS — no issues in 97 source files |
| Frontend ESLint | PASS |
| Frontend TypeScript checks | PASS for app, unit-test, and E2E configs |
| Angular unit tests | **121/121 passed** |
| Angular coverage | 70.15% statements, 64.05% branches, 63.44% functions, 77.88% lines |
| Angular production build | PASS |
| Build warnings | Existing component CSS budgets exceeded: sidebar, chat-window, chat-input, meal-log |
| Playwright browser suite | **24 passed, 12 skipped** |
| Separate live-stack Chromium flow | PASS — real Angular, FastAPI, PostgreSQL, NVIDIA, attachment/download/reopen; no API mocking |

The standard Playwright suite launches real Chromium but mocks `http://127.0.0.1:8000/**`; it proves picker/drop/composer UI integration. The separate final runtime flow used the live backend/database/provider without route mocking and captured its network/API/UI evidence.

Existing chat, vision, authentication, sessions, spreadsheet formatting/splitting/filtering, dish expansion, PDF/Word generation, and document persistence tests all passed in the current full backend run.

## 11. Final capability summary

| Phase | Status | Evidence | Remaining gap |
|---|---|---|---|
| 1 — Intent/understanding | DONE | 121 focused reading/intent tests; registry contains every enum operation; real UAT row-count read | None for documented deterministic intents |
| 2 — Modification | DONE for supported operations | 119 passed, 1 repository-fixture skip; real attached UAT expansion passed separately | Arbitrary visual PDF edits and native rich CSV edits are intentionally outside current support |
| 3 — Creation | DONE for 7 formats | 51 focused tests plus 21/21 real generated/reopened outputs | Wide source layouts need format-aware design improvements |
| 4 — Conversion/pipelines | PARTIAL | 27 focused tests plus all implemented real conversion routes passed | Matrix is intentionally finite; PDF→XLSX needs real tables; Office→PDF depends on LibreOffice |
| 5 — Natural-language automation | DONE for supported operations | Focused tests, real provider calls, and live UI/API/database creation/download pass | Complex ambiguous requests still depend on provider availability |
| 6 — Acceptance/hardening | PARTIAL | Focused hardening tests, full regression, real-file harness, and true live-backend Chromium E2E pass | Live OCR remains unverified |

## 12. Known limitations

1. Tesseract is not installed, so scanned-image OCR cannot run locally even though the OCR integration has automated tests.
2. The external NVIDIA/Ollama service can be slow, unavailable, rate-limited, or time out. Deterministic operations such as spreadsheet row count no longer depend on it.
3. PDF→XLSX only succeeds when the PDF contains extractable tables. None of the three supplied PDFs did.
4. PDF→DOCX/TXT/Markdown reconstructs content and does not preserve pixel-perfect PDF layout.
5. Native PDF modification is page-level; arbitrary in-page text/layout editing is not implemented.
6. XLSX→PDF follows workbook print settings. The supplied wide workbook produced a valid but impractical 300-page, 18.8 MB PDF in 60.909 seconds.
7. Wide spreadsheet-derived DOCX/PDF tables wrap heavily, and the UAT-derived PPTX overview overflows. The files remain valid, but visual fidelity is partial.
8. Python 3.14.7 is newer than the project's configured Python 3.13 target.
9. The standard Playwright regression suite mocks its API routes by design, but a separate final Chromium run used the live Angular/FastAPI/PostgreSQL/provider stack without route mocking.

## 13. Exact failed tests

**Final state: no backend, frontend unit, or active Playwright test failed.**

During the first real-sample discovery run, CSV→XLSX validation exposed inconsistent handling of multiline/trailing blank rows, and XLSX→PDF reading exposed a false OCR requirement for vector-only pages. Both defects were fixed, regression-tested, and passed in repeated runs 2 and 3.

MyPy was rerun successfully after the runtime fix and reported no issues in 97 source files.

## 14. Exact skipped tests

Backend:

- `tests/test_dish_category_expansion.py::test_real_uat_fixture_expands_1251_rows_to_3136` — the test searches repository fixture paths and does not search Downloads. The exact supplied Downloads file was run separately and passed: 1,251→3,136 rows, 77 columns.

Playwright (12 obsolete document-creator scenarios):

- Create/download PDF and DOCX from the removed creator panel (2).
- Export literal text to PDF and DOCX from the removed creator panel (2).
- Export an attached file to PDF and DOCX from the removed creator panel (2).
- Unavailable-AI creator recovery (1).
- Creator draft retention after AI failure (1).
- Creator attached-file removal warning (1).
- Creator staged-document import flow (1).
- Creator accessibility on desktop and mobile (2).

The active normal-composer document upload/drop workflows passed.

## 15. Categories not reached with real external dependencies

- Live OCR of a scanned document/image: not reached because the Tesseract executable is missing.
- A successful Ollama fallback for a full document prompt was not reached during the transient NVIDIA-503 repetition because the CPU-only local model exceeded the 90-second document deadline.
- Successful PDF→XLSX table extraction: not reached with the supplied PDFs because they contain no extractable tables.
- Live OCR remains not reached because the Tesseract executable is missing.
- Pixel-perfect reconstruction across unrelated formats: not promised by the conversion design and not achievable for all inputs.

## 16. Final verdict

The implementation is **ready for supported deterministic document operations and controlled API use**, including the user's immediate fast Excel row-count requirement and the real UAT dietary-category expansion. All five supplied originals were read without modification; 21 new documents were created and reopened; supported conversions passed; the complete backend and frontend suites are green.

The exact live PDF -> grounded Q&A -> DOCX -> visible attachment -> download -> reopen flow is verified. The honest universal status remains **PARTIAL** because live OCR, transient external-provider availability, and wide-document layout quality remain outside a guaranteed pass. No commit or push was made; the working tree remains available for review.
