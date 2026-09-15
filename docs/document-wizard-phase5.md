# Interactive document wizard — Phase 5

## STATUS

XLSX preview implementation complete. Backend preview/download consistency verified on a real stored workbook. **Overall acceptance remains blocked by Phase 4 live generation failure. Rendered browser verification is pending.**

## Files and behavior

- `app/services/spreadsheet/spreadsheet_preview_service.py`: opens the actual stored XLSX twice for cached values and formulas, returns sheet names/dimensions, positional rows, formula information, and the file SHA-256.
- `app/api/chat_documents.py`: authenticated `/chat/documents/{id}/preview`, with row/column paging and bounded page sizes. Ownership and XLSX type are checked. Download can use inline disposition for View.
- `food-ai-ui/src/app/components/spreadsheet-preview/`: sheet tabs, keyboard navigation, actual cells, row/column positions, horizontal scrolling, paging, loading and retry states. Null remains blank, zero remains zero, and data is rendered as text.
- `components/document-result/`: XLSX View opens the preview; other supported file types use an authenticated blob in a new tab/native handler.
- Tests: `tests/test_document_wizard_build.py` and `components/document-result/document-result.spec.ts` cover ownership, paging, multiple sheets, blanks, zero, formula fallback, exact cell values, errors and disclosure behavior.

## Tests passed / failed

Shared regression totals and warnings are recorded in [Phase 4](document-wizard-phase4.md). Frontend tests passed after fixing template whitespace so cell text matches response values exactly.

### Track A — real HTTP preview

Stored document 76 was downloaded via `/chat/documents/76/download` (HTTP 200), saved and reopened with openpyxl. Its SHA-256 is `a9abcb663c06cb285a7410952673c3b8784de58d760c976d237e9423ca525fc8`.

Both real sheets were retrieved through the preview endpoint and compared cell-for-cell with the downloaded workbook:

| Worksheet | Rows | Columns | Preview/download equality |
| --- | ---: | ---: | --- |
| Overview | 7 | 1 | PASS |
| Top 50 Vegetarian South Indian (stored name includes a trailing space) | 56 | 3 | PASS |

[Exact preview payloads and comparison evidence](../test-results/document-wizard-final/live-structural-only.json).

This proves preview fidelity, not compliance of the content with the original request: the data sheet has 55 entries, not 50. That earlier generated workbook is a rejected acceptance sample. The final generator correctly rejects an incorrect row count, and no replacement accepted workbook was produced in this run.

### Track B — rendered UI

Each item is **PENDING MANUAL VERIFICATION — no browser tool available in this session**:

1. On a successfully generated XLSX card, click View and confirm the visible grid uses the actual sheet names, rows and columns.
2. Switch sheets by click and arrow keys. Confirm focus follows the selected tab, row/column paging works and wide sheets scroll without breaking the chat layout.
3. Download the same attachment and compare every displayed page with that file. The HTTP comparison above does not prove what a browser rendered.
4. Check empty cells, zero, long strings, formulas, loading/errors, mobile layout, light/dark presentation and browser accessibility/color contrast.

## Known limitations / readiness

Formula cells display saved values when present; otherwise they display the formula. No formula recalculation is performed. DOCX/PDF in-chat rendering and the Library remain out of scope.

Phase 5 cannot receive end-to-end acceptance until the Phase 4 live generation failure is resolved and the browser checks are observed. No visual PASS is claimed.
