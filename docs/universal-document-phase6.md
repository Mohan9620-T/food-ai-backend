# Universal Document Assistant - Phase 6

Phase 6 hardens the Phase 1-5 document workflows and reports fidelity from the
file that was actually produced or extracted. It does not introduce a second
document engine or a second model client.

## Status

- Phase: 6 - production hardening and acceptance validation
- Status: complete
- Date verified: 2026-09-11
- Database migration: none
- Git commit/push: not performed

## Fidelity validation

Generation, conversion, extraction, modification, upload, and automation
responses now use the central document validator to derive fidelity from the
actual result:

- generated files are reopened with their matching parser;
- conversions validate the source and output, with exact row/value/order checks
  for CSV/XLSX paths and visible-text retention checks for reconstructed paths;
- extraction fidelity follows the real extraction mode and warnings;
- targeted modifications retain the existing before/after content and style
  checks before reporting fidelity;
- automation responses expose fidelity for every executed step and include the
  verified overall level in the completion or partial-success message.

The API and Angular response models expose optional `fidelity` and
`fidelity_note` fields without breaking older clients.

## Security and robustness

DOCX, XLSX, and PPTX are inspected before an Office parser opens them. The
preflight rejects:

- invalid ZIP signatures or missing required OOXML parts;
- encrypted, duplicate, absolute, drive-qualified, backslash, or traversal
  entries;
- excessive archive entry counts;
- excessive total decompressed size;
- excessive total or per-entry compression ratios;
- XML containing DTD or entity declarations.

The limits are configurable with safe defaults:

| Setting | Default |
| --- | ---: |
| `DOCUMENT_OOXML_MAX_UNCOMPRESSED_BYTES` | 134217728 (128 MiB) |
| `DOCUMENT_OOXML_MAX_TOTAL_RATIO` | 100 |
| `DOCUMENT_OOXML_MAX_ENTRY_RATIO` | 200 |
| `DOCUMENT_OOXML_MAX_ENTRIES` | 5000 |
| `DOCUMENT_CONVERSION_TIMEOUT_SECONDS` | 90 |

Rejected source files receive one generic malformed/corrupt-file error so
internal thresholds and file-system details are not disclosed. LibreOffice
errors no longer return raw subprocess output, conversion timeouts are bounded,
and temporary conversion directories are cleaned after failures. Exact
duplicate operations in a single pipeline plan are rejected before execution.

## Acceptance scenarios

The following 12 requested scenarios were run end-to-end. Scenario 5 covers
three Office source formats, so pytest reports 14 passing test cases:

1. upload a PDF, read it, summarize it, and return the stored download;
2. extract a PDF table to Excel;
3. convert PDF to Word;
4. update a DOCX title and table;
5. convert DOCX, PPTX, and XLSX to PDF with real headless LibreOffice;
6. clean and format an XLSX workbook;
7. filter active records, sort by `Code`, preserve the other sheet, and keep the
   original immutable;
8. expand dish rows by dietary category;
9. create a professional Word document;
10. read a PDF and create both Excel and Word outputs;
11. update a Word document and return a final PDF;
12. ask for clarification when the source or operation is ambiguous.

Result: `14 passed` in 54.73 seconds. Semantic AI calls were mocked so the run
is repeatable and does not depend on NVIDIA/Ollama availability. Parsing,
generation, modification, persistence, downloads, and validation used real
implementations. Office-to-PDF scenarios used the real LibreOffice 26.8.0.3
installation.

## Scale and concurrency evidence

- A 65,000-character document was processed in three map chunks plus one reduce
  step, including the final source tail: 0.01 seconds with the model stream
  mocked.
- A real generated 1,251-row by 77-column workbook was expanded to 3,136 rows,
  reopened, and checked while preserving blank cells: 15.59 seconds.
- Same-session concurrent automation requests retained a coherent
  `latest_document_id` under the existing application and database locking.
- Prompt-injection text remained delimited as untrusted document data.
- The external UAT workbook was not attached, so its fixture-specific test is
  intentionally skipped; the equivalent full-scale generated fixture passes.

## Verification

| Check | Result |
| --- | --- |
| Backend regression and coverage | 591 passed, 1 skipped, 12 warnings; 90.43% coverage |
| Phase 6 hardening plus Phase 5 automation subset | 26 passed |
| Twelve acceptance scenarios | 14 passed (three cases for scenario 5) |
| Large-document and full-scale workbook timing | 2 passed |
| Ruff lint | passed |
| Ruff format check | passed; 155 files already formatted |
| MyPy | passed; 97 source files |
| Git whitespace check | passed |
| Angular lint | passed |
| Angular typecheck | passed |
| Angular unit tests | 120 passed; 78% line coverage |
| Angular production build | passed |

The backend warning count consists of existing dependency deprecation warnings.
The Angular build reports existing CSS budget warnings but emits the production
bundle successfully.

## Known limitations

- Semantic analysis and AI-authored content still require the configured
  NVIDIA/Ollama provider to be available; deterministic operations do not.
- Reconstructed conversions cannot guarantee pixel-perfect layout and disclose
  HIGH or lower fidelity based on validation.
- Office-to-PDF conversion requires LibreOffice at runtime.
- Scanned-image extraction depends on Tesseract OCR availability and is reported
  as OCR/HIGH rather than FULL.
- Archive preflight reduces decompression and XML-entity risk but is not an
  antivirus scanner.
