# Universal Document Assistant — Phase 4

Phase 4 adds the `POST /chat/documents/pipeline` endpoint. It accepts an ordered instruction
whose steps are separated with `then`. A step reads either an explicitly named attachment in
the same chat or the session's persisted `latest_document_id`. After each successful step, the
new attachment is validated, stored, and made the latest document before the next step runs.

## Conversion support

| Source | Target | Status | Behavior |
| --- | --- | --- | --- |
| CSV | XLSX | Fully supported | Rows and cell text are preserved in a valid workbook. |
| XLSX | CSV | Fully supported for one populated sheet | Multi-sheet workbooks are rejected instead of silently dropping sheets. |
| TXT | Markdown | Fully supported | Extracted UTF-8 text is preserved. |
| Markdown | TXT | Fully supported | Markdown source is treated as plain UTF-8 text. |
| DOCX | PDF | Supported with LibreOffice | Uses headless LibreOffice to preserve layout. |
| PPTX | PDF | Supported with LibreOffice | Uses headless LibreOffice to preserve slide layout. |

DOCX/PPTX to PDF is available only when LibreOffice is installed. The converter discovers
`soffice` on `PATH` and in standard Windows installation folders. Deployments can instead set
`LIBREOFFICE_BINARY` to the full executable path. If LibreOffice is absent, the API returns a
clear 503 response; it never substitutes a lossy pure-Python re-render.

Every other conversion pair is currently unsupported and returns a 422 response without
changing the source or creating an approximate output.

## Supported pipeline operations

- Convert a document using one of the pairs above.
- Format, filter, split, or expand an XLSX workbook using the existing deterministic Excel
  operations.
- Extract structured PDF tables into XLSX.

Reading and summarization do not produce a file and therefore are not pipeline transformation
steps in Phase 4. Natural-language agent planning and clarification are reserved for Phase 5.

The Windows development environment used for the Phase 4 acceptance run has LibreOffice
26.8.0.3 installed, and the real DOCX/PPTX-to-PDF conversion tests pass. Other environments must
install LibreOffice separately or configure `LIBREOFFICE_BINARY`.

Example that is executed end to end by the test suite:

```text
Convert items.csv to XLSX, then format the workbook professionally,
then add a filter to the Category column
```
