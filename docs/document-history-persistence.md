# Document data and history persistence

Implemented and verified locally on 2026-09-16.

## What is stored

- `chat_sessions`: owner, title, activity time and latest document ID.
- `chat_messages`: prompts, answers, original image bytes and document workflow
  state. Workflow state includes the request, selected source, clarification
  choices, review, build result, step results and generated attachment IDs.
- `chat_document_attachments`: original/generated file bytes, filename, MIME
  type, size, timestamp and text. `extracted_data` retains full parsed text,
  tables, blocks, locations, coverage and extraction warnings.
- Generation metadata retains structured generated content, assumptions, source
  IDs, fidelity, instruction and snapshots of the source extractions used by
  automated builds. Each generated output has its own attachment ID.

The legacy generation endpoint retains its Markdown response in `raw_text`;
the parser's representation of the file is stored separately in `extracted_data`.
Failed image builds retain successfully extracted source data with the failure
response. Pending review/clarification state is restored from the server after
refresh or login. Multi-output turns restore every generated file card once.
New chat activity updates the session's position in the recent-history list.

`GET /chat/documents/{id}/data` returns the saved text, structured extraction and
generation metadata. The data, history, preview and download endpoints enforce
document/session ownership. Full extraction JSON is fetched separately, rather
than including large tables in every chat-history response.

## Migration and existing history

```powershell
.\.venv\Scripts\python.exe -m alembic -c app/alembic.ini upgrade head
```

Revision `0012_document_history` adds two nullable JSON columns. It was applied to
the local PostgreSQL database. Existing message and file records remain intact;
older internal output attachments are included in restored file cards too.
Existing file bytes and saved text remain available. Extra extraction JSON is
populated on subsequent processing; previously unsaved wizard choices cannot be
reconstructed by this migration.

## Real execution verification

Real HTTP requests used the running server on port 8000 and its configured OCR/AI
providers. Chromium used the Angular app on port 4200, with no API interception.
An isolated verification account was used.

| Check | Result |
| --- | --- |
| Upload receipt image | PASS: source attachment 161, session 91 |
| Image to Excel, Word and PDF | PASS: attachments 162, 163, 164 |
| Reopen downloaded outputs with openpyxl, python-docx and pypdf | PASS: receipt items present |
| Read saved data and history using a new HTTP client | PASS: all generated versions and source data retained |
| Fresh browser restores files and pending review | PASS: 3 file cards, no duplicate final attachment |
| Historical Download and Excel View | PASS: download hash matches server bytes; receipt worksheet renders |
| Build from restored review, then refresh | PASS: Word attachment 165 and earlier files retained |
| Direct PostgreSQL verification | PASS: 5 artifacts, 107,069 bytes, 18 messages, 8 workflow records |
| Rendered chat axe WCAG A/AA checks | PASS: zero violations |

Local evidence is under ignored `test-results/document-history-live/`:
`http-evidence.json`, `validation.json`, `database-validation.json`,
`browser-validation.json`, screenshots and downloaded files. Authentication
material is local only and must not be committed.

## Automated checks

- New persistence coverage includes seven output formats, ownership isolation,
  image extraction surviving generation failure, restored review choices,
  multiple generated files, and recent-history ordering.
- Initial full backend run: 707 passed, 1 skipped, one legacy Markdown-preservation
  assertion failed. Backward-compatible Markdown storage was restored; the final
  affected suites passed all 75 tests. The preceding image/wizard/persistence
  suites passed all 67 tests.
- Frontend: 166 tests passed; typecheck, lint and production build passed.
- Ruff, formatting, mypy and Git whitespace checks passed. The frontend retains
  the existing initial-bundle warning (507.68 kB against a 500 kB warning budget).
