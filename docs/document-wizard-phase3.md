# Interactive document wizard — Phase 3

## STATUS

Question/review implementation: complete for this phase. Real backend planning and answer persistence: PASS. Automated frontend interactions: PASS. **Rendered browser verification: PENDING MANUAL VERIFICATION — no browser tool available in this session.** This is not a full Phase 3 end-to-end/browser PASS.

## What changed

- Added an Angular question card showing one question at a time, progress, native radio options, recommendation badges, Other text input, Back and Next/Review controls.
- Added review rows with the chosen answers and a Change action that returns to the selected question.
- Added 1–4 shortcuts, excluding text inputs and editable content. Focus moves to new questions and the Other input after rendering.
- Connected cards to Phase 2 state retrieval and revision-checked updates. Back saves a valid current answer, preserves all other answers, and navigates to the previous question. Reload restores saved state. Conflicts show an explicit reload action.
- Routed source-free document creation through the existing automation endpoint, creating a chat session first when needed. Retries use that route too.
- Extended the existing automation planner envelope with the Phase 1 clarification schema. Structured questions are persisted through the Phase 2 service. Pending questions block automation execution.
- Source-free creation planning uses the existing non-streaming document completion method and document timeout/token budget. No alternate document generator was added.

The single Build action is visible but disabled, with an explanation that file creation from review is not available yet. Enabling it and executing the existing generator is Phase 4; no file creation is falsely claimed.

## Files changed

- `food-ai-ui/src/app/components/document-wizard/`: component, template, styles and six tests.
- `food-ai-ui/src/app/models/document-wizard.ts`: typed question/state/answer contracts.
- `food-ai-ui/src/app/components/chat-window/chat-window.ts`, `.html`, `.spec.ts`: render the session's wizard and preserve existing chat tests.
- `food-ai-ui/src/app/components/chat-input/chat-input.ts`, `.spec.ts`: source-free request/retry routing.
- `food-ai-ui/src/app/services/chat.ts`, `.spec.ts`: create a session before automation when needed.
- `food-ai-ui/package.json`, `package-lock.json`: axe-core development dependency for accessibility tests.
- `app/services/document/document_automation_service.py`: structured planner questions and bounded document completion.
- `app/api/chat_documents.py`: persist proposed questions and guard pending state.
- `tests/test_document_wizard_planning.py`: planner/state integration regressions.
- This report and local real HTTP evidence.

Existing uncommitted work was preserved. No database migration, new state table, Library page, result-card redesign or preview implementation.

## Tests passed / failed

| Check | Final result |
| --- | --- |
| Angular full unit suite (`npm test -- --watch=false`) | 129 passed across 11 files |
| Wizard interaction tests | Other, Back, earlier-answer change, review, shortcuts, conflict feedback and restore passed |
| axe structural checks | Zero violations for question and review cards |
| Angular typecheck and ESLint | Passed |
| Backend document/planner/state regression subset | 69 passed; 12 existing dependency deprecation warnings |
| Ruff and MyPy on changed application modules | Passed |
| Angular production build | Passed; existing component CSS budget warnings |

The axe test runs against jsdom, which cannot validate rendered color contrast/layout. That rule is explicitly excluded there and remains pending in the browser. This report does not claim all visual/accessibility checks passed.

Tests caught and fixed a keyboard event target that was not an Element and an invalid review definition-list structure. Final frontend failures: zero. The initial live model failure is retained in the evidence below.

Backend regression command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_documents.py tests/test_document_wizard_planning.py tests/test_document_automation.py tests/test_document_wizard_schema.py tests/test_document_clarification_state.py -q
```

## Real HTTP execution

The real running server at `http://127.0.0.1:8000` received:

```json
{"session_id":46,"instruction":"I want to create one excel sheet this could contain top 50 dishes from south india, like dish name, approx price in south indian, which state belongs to, and all"}
```

`POST /chat/documents/automate` returned HTTP 200, `status=clarification_required`, no executable steps and no attachments. `GET /chat/documents/clarification/46` returned HTTP 200 with two real model-proposed questions: dietary preference and price currency, each with three labeled options, descriptions and a recommendation.

This plan was not seeded or mocked. [Exact live request, response and stored model questions](../test-results/document-wizard-phase3/live-model-final.json).

The initial attempt in session 45 returned HTTP 200 with `status=failed` because the planner response was invalid; no file was produced. That attempt used the legacy chat completion path. Moving source-free planning to the existing document completion path resulted in the valid session 46 plan. The precise malformed provider output was not captured, so this report does not assert a proven token-truncation diagnosis. [Initial failure evidence](../test-results/document-wizard-phase3/live-model.json).

Real PATCH requests then saved Other text, answered the second question, navigated Back, revised the first answer to `Vegetarian without eggs; dairy is fine`, preserved the second answer and reached `ready_for_review`. A fresh GET returned the same state. [Exact answer/navigation payloads and responses](../test-results/document-wizard-phase3/live-answers.json).

These HTTP checks establish backend behavior. The Angular interaction tests use a simulated DOM and controlled HTTP responses; they are not substituted for a real browser run.

## Browser/manual verification

**PENDING MANUAL VERIFICATION — no browser tool available in this session**, for each item:

1. Open the active application at localhost:4200 and start a new chat. Submit the exact source-free Excel request above. Verify one question card is visible with N of M progress and one recommendation when supplied.
2. Select Other, type an answer, and continue. Choose an answer to the next question, click Back, and confirm previously entered answers remain selected.
3. Change the earlier answer, finish the questions, and verify the review lists the revised answer and all retained answers.
4. Use each Change control and verify it opens the corresponding question. Reload and verify saved answers return.
5. Verify 1–4 option shortcuts, normal number entry in Other/composer inputs, keyboard focus order, radio labels, narrow-screen layout, light/dark presentation and browser axe/color-contrast checks.
6. Confirm Build is visibly unavailable in this phase and no generated attachment appears from answering/reviewing alone.

Browser inventory returned no available browser. No screenshot or visual success is claimed. Prior Phase 0 manual checks remain pending as well.

## Known limitations / next phase

- Phase 4 must implement confirmed Build, revalidation of all saved answers, completion/cancellation lifecycle, result cards and provenance. Existing wizard replacement still returns 409.
- The model may leave ambiguity after its questions; Phase 4 must follow up or explicitly disclose any assumption before generation, as requested.
- Result cards, file previews and the screenshot's ambiguous PDF-edit behavior are not claimed fixed here.
- npm reported 16 dependency advisories during installation (12 moderate, 4 high). No broad dependency upgrades were performed in this feature phase.

## Ready for next phase

Implementation and backend evidence are ready for review. Real-browser verification remains pending. Stopped before Phase 4 under the requested phase gate; proceeding requires approval with this verification gap visible.
