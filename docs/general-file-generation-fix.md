# File generation, chat routing, and formatting verification

Date: 2026-09-15

## Result

New Excel creation, general-question routing, and Markdown presentation work through the running application. A real browser completed prompt → review → Build → Download → View for an eight-planet workbook. The file contains the correct eight rows and bold column headers.

Two remaining limits must not be mistaken for passing checks:

- **Factual accuracy: FAIL for the presidents example.** The configured model generated a valid workbook but incorrectly called Joe Biden the incumbent in 2026 and claimed 19 people while listing 22. The [White House identifies Donald Trump as the 45th and 47th president](https://www.whitehouse.gov/administration/donald-j-trump/). Source-free generation uses model knowledge; no live research service is configured. The workbook is test evidence, not a verified historical dataset. Date awareness and instructions to reconcile counts do not guarantee model accuracy.
- **Cache cleanup: partial.** Angular and Ruff caches were cleared using their supported commands (about 18.4 MB of old cache contents during the initial cleanup). The follow-up GitHub publication cleanup also cleared pytest cache data using `pytest --cache-clear --collect-only`. Working caches recreate as needed. Automatic approval review rejected direct cache-directory deletion with only "blocked by policy" as its reason; Python bytecode, mypy caches, and empty pytest temporary directories remain.

## Changes

- Recognize `crate`/`creat` as creation verbs, including the exact screenshot prompt.
- Keep new subjects independent of old attachments. Preserve explicit file references, selected uploads, and source choices through clarification/review.
- Ensure execution follows the planned source selection; an old latest-file pointer no longer supplies an implicit source to a source-free creation.
- Omit unrelated file reference text from ordinary chat requests. Retain explicit file references and content-topic matches such as “What is the oats revenue?”, along with general conversation context and standing preferences.
- Return read-only document answers directly. Keep review and Build for file-producing requests.
- Retry temporary provider overload responses twice with short delays. Preserve the existing fallback and document deadline. Limit planning time and remove error guidance for the deleted export button.
- Render distinct heading/subheading sizes and bold weights; retain HTML sanitization. Fix upload download-button contrast.
- Preserve complete filenames derived from dotted titles such as “U.S. Presidents.”
- Remove automatic chat consolidation on first browser load. Fix the legacy consolidation endpoint to move attachments before deleting old sessions and preserve the latest-document pointer. Earlier already-deleted attachments are not restored by this code fix.
- Ignore Ruff and mypy cache directories in Git.

## Track A — real HTTP execution

Backend: `http://127.0.0.1:8000`, configured NVIDIA provider with Ollama fallback; real PostgreSQL storage. No mocks or TestClient were used for the following checks.

Exact request bodies, response payloads, statuses and timings: [evidence.json](../test-results/general-file-fix-live/evidence.json). Authorization credentials are excluded.

| Check | Result | Evidence |
|---|---|---|
| Upload unrelated food workbook | PASS | `POST /chat/documents`, HTTP 200, uploaded ID 95, session 71 |
| General question after upload | PASS for routing/answer delivery | `POST /chat/?session_id=71`, HTTP 200; no restriction to the food file |
| Exact presidents prompt, including “crate” | PASS for file planning | `POST /chat/documents/automate`, HTTP 200; XLSX review, no file before confirmation |
| Confirm and generate | PASS for actual file generation | Same endpoint with `confirm: true`, HTTP 200; document 96; `source_document_ids: []`, `general_knowledge` |
| Stored download and parser | PASS | `GET /chat/documents/96/download`, HTTP 200; 7,221 bytes; reopened by openpyxl |
| Presidents content fact check | FAIL | Outdated incumbent and inconsistent count, described above |
| Structured general explanation | PASS | Real chat response contains headings, subheadings, bold labels, and relevant content |

Provider failures encountered before successful execution remain in `provider-overload-evidence.json` and the backend logs. A provider outage can still prevent AI-written content; failure responses do not claim to attach a file.

Final running-server regression: [final-smoke.json](../test-results/general-file-fix-live/final-smoke.json) records real `sample-1.pdf` upload/AI reading (both pages), Word review/build/download, and downloading the saved planet workbook after restarting the backend. All requests returned HTTP 200. python-docx reopened the Word file and verified that the text extracted from both PDF pages is present. The stored Excel bytes remained identical.

## Track B — actual browser rendering

No connected browser surface was exposed by the computer-use inventory. Headless Chromium was available through the repository’s Playwright installation, so the following checks were performed against the running frontend/backend, without mocked API responses.

| Check | Result |
|---|---|
| Assistant answer rendered in chat | PASS |
| Heading/subheading/bold styles | PASS: H2 22.4 px, H3 19.2 px, bold weight 700 |
| Attachment filename and Excel type | PASS |
| Download button returns matching stored bytes | PASS: SHA-256 matches HTTP download |
| Stale “AI analysis is currently unavailable” message | PASS: absent in verified chat |
| New independent planet prompt → review → Build | PASS: HTTP 200, generated ID 97, no old source IDs |
| New workbook download and View preview | PASS: `Planets-of-the-Solar-System.xlsx`, 6,048 bytes |
| Reopen browser download | PASS: eight planets in correct order 1–8, bold headers |
| WCAG A/AA checks on the chat response area | PASS: zero axe violations after contrast fix |
| Fresh browser silently merges chats | PASS: no consolidation request; saved chats remain separate |

Browser evidence: [validation](../test-results/general-file-fix-live/browser-validation.json), [review](../test-results/general-file-fix-live/browser-excel-review.json), [build](../test-results/general-file-fix-live/browser-excel-build.json), [rendered answer](../test-results/general-file-fix-live/rendered-answer.png), [rendered workbook](../test-results/general-file-fix-live/rendered-new-excel.png).

## Automated checks

- Frontend: 152 tests passed; TypeScript checks, ESLint, and production build passed. Existing component CSS budget warnings remain.
- Backend: **672 passed, 1 skipped** in the full suite. [JUnit results](../test-results/general-file-fix-live/backend-tests.xml). Regression checks cover PDF→Word preservation, independent creation, clarification source retention, direct document answers, planning timeouts, filenames, and attachment-safe consolidation.
- Ruff passed; mypy passed for 99 source files.

## Try it

Refresh `http://localhost:4200` with Ctrl+F5, send a new creation request, review the plan, then click **Build my document file**. Use **Download** for the real file and **View** for an Excel preview. General questions can be asked in the same chat without using an unrelated uploaded file.
