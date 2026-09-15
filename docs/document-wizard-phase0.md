# Interactive document wizard — Phase 0

## STATUS

Track A: PASS — real HTTP against the existing running backend at http://127.0.0.1:8000 on 2026-09-15. No TestClient, mocked provider, internal generator invocation or replacement server was used.

Track B: PENDING MANUAL VERIFICATION — no browser tool available in this session.

## Track A — real backend execution

| Step | Exact endpoint | HTTP | Result |
| --- | --- | --- | --- |
| Upload | POST /chat/documents | 200 | PASS: session 43, source ID 73, sample-1.pdf, 69,988 bytes |
| Q&A | POST /chat/documents/automate | 200 | PASS: done, answer_document_question, source ID 73 |
| Creation | POST /chat/documents/automate | 200 | PASS: done, create_document, generated ID 74 |
| Download | GET /chat/documents/74/download | 200 | PASS: persisted file retrieved by ID, 35,969 bytes |
| Save/reopen | python-docx parser | n/a | PASS: saved DOCX reopens with 13 paragraphs |

Upload multipart fields: analyze=false; file part named file, filename sample-1.pdf, MIME application/pdf. The upload intentionally separates storage/extraction from the subsequent real AI Q&A call. Its analysis_status=skipped is expected, not an AI success claim.

Exact Q&A payload:

```json
{"session_id":43,"instruction":"can you read this document and tell me what it is about"}
```

Exact creation payload:

```json
{"session_id":43,"instruction":"can you create the word document on this content"}
```

The real answer identified the Your Company brochure dated September 04, 20XX, Product Overview and Details sections, and Lorem ipsum placeholder content. It explicitly did not infer real product facts. The resulting Product-Brochure---Your-Company.docx has the Word MIME type and contains the brochure title, date, headings and source content. Downloaded size matches attachment metadata. SHA-256: 99ce3376f8f84f59f53e7e8007ef5a285042b7ff36151085ac4e7599055e54f6.

### Exact evidence

[Complete HTTP request/response payloads, statuses, IDs and parser results](../test-results/document-wizard-phase0/evidence.json).

The same directory contains exact request and response body bytes for all four steps, including the multipart upload, the [downloaded DOCX](../test-results/document-wizard-phase0/Product-Brochure---Your-Company.docx), and extracted generated text. Authentication reused the prior local test account through POST /users/login (200); credentials and tokens are omitted from new evidence.

## Track B — frontend rendering

Every item below is PENDING MANUAL VERIFICATION — no browser tool available in this session:

1. Upload sample-1.pdf in the active Angular app and submit the exact Q&A instruction above. Verify the assistant answer visibly identifies the brochure and placeholder content.
2. Submit the exact Word creation instruction in the same chat. Verify an attachment card shows the real returned filename and Word/DOCX type. Track A returned Product-Brochure---Your-Company.docx, 35,969 bytes.
3. Click the rendered Download button and verify the downloaded file opens. The successful HTTP download does not verify the button.
4. Verify no stale “AI analysis is currently unavailable” message remains for the successful current response or in the composer.

## Files changed / regression

Phase 0 changed this report and created local HTTP evidence and a downloaded artifact. No application changes for Phase 0; no regression suite needed for evidence collection. Phase 1 work is reported separately.

## Known limitations

This establishes the exact Word flow on this run, not continuous provider availability or Excel generation. The screenshot's ambiguous PDF-edit error is not claimed fixed. No UI rendering PASS is claimed.

## Ready for next phase

Yes: Track A passed, and the latest user instruction explicitly authorized Phase 1 while Track B remains pending. See document-wizard-phase1.md.
# Later verification update — 15 September 2026

The historical success below does not supersede the latest live repeat: upload passed, but the document Q&A timed out, so Word creation was not retried. See the separated backend/manual tracks in [the Phase 4 report](document-wizard-phase4.md) and [final status](document-wizard-final.md). Overall acceptance is not complete.
