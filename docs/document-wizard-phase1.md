# Interactive document wizard — Phase 1

## STATUS

Schema implementation and automated checks: PASS. Fully specified source-based creation over real HTTP: PASS. UI: PENDING MANUAL VERIFICATION — no browser tool available in this session. This is not a full wizard/browser PASS.

## Files changed

- app/services/document/extraction_models.py: extends existing OperationPlan with optional clarification_plan and structured questions/options.
- app/services/document/document_automation_service.py: distinguishes uploaded-source format from output format in clear creation requests.
- tests/test_document_wizard_schema.py: 17 new checks.
- Phase reports and local HTTP evidence.

Existing uncommitted work was preserved. No frontend or session-state changes.

## Schema contract

- One to three ordered questions; three to four options per question.
- Bounded nonblank IDs, prompts, labels and descriptions.
- Unique question IDs and option IDs within each question; unknown fields rejected.
- Strict boolean flags; at most one recommended option (zero permitted when no sensible recommendation exists).
- Other requires free-text support.
- Structured questions require needs_clarification=true and prohibit executable steps. Unanswered structured questions cannot be marked executable.
- Legacy single-string clarification remains supported; structured plans expose their first prompt through that field.

## Tests passed / failed

Final command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_document_wizard_schema.py tests/test_phase1_document_understanding.py tests/test_document_intent_service.py tests/test_document_automation.py -q
```

90 passed, 12 existing dependency deprecation warnings, 5.14 seconds. Ruff lint/format checks passed on the three changed Python files. MyPy passed on both changed application modules.

Tests cover ordered underspecified-creation question payloads, round trips, backward compatibility, duplicates, blanks, limits, unknown fields, recommendations, strict booleans and attempts to execute unresolved questions. The fully specified uploaded-PDF request takes the creation path without clarification or a model-planner call.

Regression testing caught an initially overbroad source/target shortcut that bypassed existing multi-step planning. Restricting it to explicit source-reference wording restored the partial-failure regression. Final failures: zero.

## Real HTTP verification

POST /chat/documents/automate:

```json
{"session_id":43,"source_document_id":73,"instruction":"create a Word document summarizing this uploaded PDF"}
```

HTTP 200, status=done, generated document 75, Product-Brochure.docx, 35,961 bytes. Authenticated download returned HTTP 200 and reopened with 11 paragraphs. No clarification was requested.

[Exact live evidence](../test-results/document-wizard-phase0/phase1-specified-live.json).

## Known limitations

The underspecified-creation test validates a proposed structured question payload. It does not establish that the live model currently generates these questions through the automation endpoint. Phase 1 adds the schema contract; question generation/transport, session answers, rendered cards, review and confirmed execution remain subsequent phases. No alternate generation path or new endpoint was introduced.

UI checks remain pending as listed in Phase 0 Track B. Library remains future work; embedded PDF/DOCX preview is optional.

## Ready for next phase

Ready for Phase 2 backend session-state work after approval. Stopped under the original instruction to report and obtain approval between phases; the latest request authorized Phase 1, not Phase 2.
