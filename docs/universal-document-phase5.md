# Universal Document Assistant — Phase 5

Phase 5 adds natural-language document automation through
`POST /chat/documents/automate`. The endpoint converts a request into a validated ordered plan,
then executes every step through the Phase 4 pipeline engine. It does not use a separate file
processing path.

## Supported workflow

1. Upload one or more supported documents in the normal chat composer.
2. Ask for a document task in ordinary language. References such as `this file`, `the latest
   document`, an exact filename, `the first PDF`, and `both PDFs` are resolved against documents
   saved in the current chat.
3. When the intended source is ambiguous, the assistant asks one clarification question. The
   answer is resolved with persisted recent chat history, so the original instruction does not
   need to be repeated.
4. The complete plan is validated before document bytes are processed. Operation registration,
   source arity, input/output compatibility, required parameters, conversion support, and the
   configured step limit are checked up front.
5. Each generated intermediate is reopened with the matching format parser, stored in the chat,
   and becomes the latest document for the next step. All useful generated files are returned as
   download attachments.

`DOCUMENT_PIPELINE_MAX_STEPS` controls the maximum number of operations in one request. The
default is 8 and the accepted range is 1–20.

## Concurrency and failure behavior

Automation and explicit pipeline requests for the same chat are serialized in-process and also
lock the chat-session row in PostgreSQL. This keeps `latest_document_id` coherent when requests
arrive concurrently. Separate chat sessions may still run in parallel.

If a later step fails, execution stops. Earlier validated outputs remain stored and are reported
as partial results; unexecuted steps are marked `not_started`. A failed pipeline never reports a
successful final result.

## End-to-end examples

The automated tests execute these requests against real files:

```text
Read this PDF, extract the customer data, create an Excel and prepare a Word report.
```

This produces a validated XLSX extraction and a validated DOCX report.

```text
Update this Word document, improve the formatting and give me the final PDF.
```

This produces an updated DOCX intermediate and converts it to a final PDF through headless
LibreOffice. PDF merging also accepts two or more explicitly named or collectively referenced
PDF files.

## Runtime dependencies and limitations

- DOCX/PPTX to PDF requires LibreOffice. Set `LIBREOFFICE_BINARY` when `soffice` is not on `PATH`
  or installed in a standard location.
- Semantic planning and AI-authored content use the existing NVIDIA-primary/Ollama-fallback text
  provider. Deterministic reading, validation, conversion, spreadsheet operations, and PDF merge
  do not create a separate model client.
- Provider availability still determines whether an AI writing or analysis step can complete.
  Files generated before a provider failure are retained and the response is marked partial.
