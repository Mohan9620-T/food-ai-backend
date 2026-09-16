# Image to document creation

Verified locally on 2026-09-16 using the running FastAPI server, PostgreSQL,
installed Tesseract, configured AI provider, and Angular app. Live checks used
real HTTP requests and Chromium; their responses were not mocked.

## Fix

Image file requests previously went to `/chat/vision`, which only returned text.
Images were not accepted by document upload or available as document sources.
Slow synchronous vision calls also blocked the server's event loop. Local OCR's
Python package was present, but the Tesseract executable was missing on this machine.

Image creation now follows upload → review → Build → saved attachment → View/Download.
The source image is retained, with caption positions used to associate labels and
values. Low-text images use the configured vision provider with bounded queue and
fallback waits. Extraction failures produce an error, never a fake attachment.
Previously sent images can become owned document sources without duplicate attachments.
Generated image-based files are marked BEST_EFFORT because recognition needs review.

The browser check also exposed a history-loading race: completing the initial history
request could replace a new chat during its image upload/build. Initial history now
merges with live turns and preserves their active conversation and source IDs.

## Track A — actual backend execution

Source: the same 968 × 1000 World Cuisine image from the reported conversation.
The verification script uploaded its original bytes through HTTP into a separate test chat.
All requests below returned HTTP **200**.

| Step | Request | Result |
| --- | --- | --- |
| Upload | `POST /chat/documents`, multipart image/jpeg, `analyze=false` | PASS: session 79, source attachment 121; no extraction claimed before Build |
| Original download | `GET /chat/documents/121/download` | PASS: identical original bytes |
| Review | `POST /chat/documents/automate`, payload below, `confirm` omitted | PASS for XLSX, DOCX, PDF: `ready_for_review`, no generated attachment |
| Build XLSX | Same endpoint, `confirm=true` | PASS: generated attachment 122, 6,556 bytes, 12.437 seconds |
| Build DOCX | Same endpoint, `confirm=true` | PASS: generated attachment 123, 36,136 bytes, 5.602 seconds |
| Build PDF | Same endpoint, `confirm=true` | PASS: generated attachment 125, 3,054 bytes, 6.401 seconds |
| Generated download | `GET /chat/documents/{122,123,125}/download` | PASS: reopened using openpyxl, python-docx, and pypdf respectively |
| Responsiveness | `GET /health` during all three builds | PASS: all sampled requests returned 200, longest 0.116 seconds |

Exact Excel review JSON:

```json
{
  "session_id": 79,
  "source_document_id": 121,
  "instruction": "can you read the document and create the excel sheet item name and country name"
}
```

Word instruction: `Create a Word document from this image listing every item name and country name`.
PDF instruction: `Create a PDF document from this image listing every item name and country name`.
Build repeats each payload with `"confirm": true`.

All three outputs contain the **16 matching item/country pairs**, with source spellings
preserved. Excel headers are bold. Source lineage is `[121]` and provenance is
`uploaded_source`. Attachments are stored in PostgreSQL and were downloaded to disk
before validation. These timings are observations, not performance guarantees.

## Track B — rendered browser execution

Real Chromium interacted with the running Angular app at `http://localhost:4200`.

- PASS: upload image with the exact reported prompt; see review and Build action.
- PASS: Build completes with an assistant answer and Excel attachment card.
- PASS: card filename/type match attachment 126, sourced from uploaded image 124 in chat 80.
- PASS: Download saves `Item-Names-and-Country-Names-from-Image.xlsx` (6,377 bytes).
  Browser download bytes equal the authenticated server download.
- PASS: downloaded workbook reopens, contains all 16 correct pairs and bold headers.
- PASS: View renders its worksheet with 17 rows (header plus 16 entries), 2 columns.
- PASS: no stale `AI analysis is currently unavailable` message; the request uses
  document endpoints instead of `/chat/vision`.
- PASS: axe WCAG A/AA checks on the rendered chat window report zero violations.

## Reproducible local evidence

Automated regression checks: **690 backend tests passed, 1 skipped; 156 frontend
tests passed**. Ruff, mypy (101 files), TypeScript checking, ESLint and the production
Angular build passed. The build retains existing component CSS budget warnings.

Ignored directory `test-results/image-document-live/` contains:

- `final/evidence.json`: exact endpoints, HTTP statuses, response JSON, durations,
  and references to exact request/response byte files. Authentication is omitted.
- `final/file-validation.json` and `content-validation.json`: downloaded file hashes,
  matching-parser content, 16-pair assertions and header checks.
- `browser-build.json`, `browser-validation.json`, `browser-review.png`,
  `browser-result.png`: browser API responses, download verification and screenshots.
- `before-ocr/`: initial real failure when both vision providers timed out and
  Tesseract was unavailable. No generated file was falsely reported as successful.

Tesseract 5.4.0.20240606 was installed in the standard Windows location. The reader
also supports `TESSERACT_BINARY` for custom installations. Local OCR defaults to
English. Unclear labels, other languages and photograph interpretation need review;
vision fallback and document composition require their configured providers.

## Follow-up: upload clarification and other image content

The wizard now displays **Upload image or document** when it needs a source,
instead of a text field and repeated "yes"/"Next" loop. Upload resumes the original
creation request and previous choices. The composer follows the same route for an
attached image while a wizard is pending, including when its message is empty.
Beginning a new document upload clears a stale image-cancellation banner.

Visual requests (objects, people, charts, scenes) inspect the image even if text OCR
also finds labels. Valid vision `items` are retained when the provider leaves `answer`
empty. Unreadable cells no longer cause otherwise-readable rows to disappear.

The configured Meta vision model timed out in actual requests. The default and local
`NVIDIA_CHAT_VISION_MODEL` now use `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`,
with thinking disabled for extraction. Transient capacity responses are retried up to
two times within the same read budget. NVIDIA documents this model's image input and
chat endpoint in its [official API reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning-infer).
The selected model was verified by actual inference, not just a model-list response.

### Additional real execution

Evidence is in ignored `test-results/image-upload-followup/`.

| Check | Result |
| --- | --- |
| JPEG receipt → Word | PASS: document 139; readable line items, quantities, amounts and total |
| WebP receipt → Excel | PASS: document 141; readable rows retained; one uncertain quantity blank with a note |
| GIF receipt → PDF | PASS: document 143; parser reopened the downloaded PDF |
| PNG receipt → Excel | PASS: document 145; all three line items and total retained |
| Image with no text → object-count Excel | PASS: source 146 → document 147; 2 squares and 1 circle, 3 objects total; 11.197-second build |
| Wizard upload → Build → browser Download/View | PASS: chat 90, source 148 → document 149; real file chooser opened by the Upload button; downloaded bytes match storage |
| Rendered chat accessibility | PASS: zero axe WCAG A/AA violations |

All upload/review/build/download steps above used the running server at port 8000.
Generated files were reopened with matching parsers. `http-evidence.json` records the
receipt requests; `visual-final.json` records the final successful object-count request.
`browser-validation.json` records the working file chooser, download and preview flow.
The initial visual timeout and retries were failures, not successful file generation.
Visual colors and unclear labels remain interpretations requiring review; arbitrary
photos, low resolution, handwriting and service availability cannot promise perfect extraction.

Regression checks: full backend suite **697 passed, 1 skipped**; after the final provider
and item-response fixes, **65 focused tests passed**. Frontend **164 tests passed**,
including upload continuation and cancellation-message coverage. The unrelated login
render assertion was updated to match the workspace's current "Welcome back" / "Log in"
copy. Existing concurrent UI design changes were preserved.
