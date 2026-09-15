from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.services.document.document_automation_service import (
    AvailableDocument,
    DocumentAutomationService,
)
from app.services.document.document_operation_registry import DocumentType
from app.services.document.extraction_models import OperationPlan


def clarification_payload():
    return {
        "intent_category": "CREATE",
        "confidence": 0.95,
        "needs_clarification": True,
        "clarification_plan": {
            "questions": [
                {
                    "id": "ranking",
                    "prompt": "How should the top 50 South Indian dishes be selected?",
                    "options": [
                        {
                            "id": "1",
                            "label": "Popularity",
                            "description": "Commonly known dishes.",
                            "recommended": True,
                        },
                        {
                            "id": "2",
                            "label": "State balance",
                            "description": "Equal coverage across states.",
                        },
                        {
                            "id": "3",
                            "label": "Variety",
                            "description": "Cover different meal types.",
                        },
                    ],
                    "allow_other": True,
                    "allow_freeform_answer": True,
                }
            ]
        },
    }


def test_underspecified_creation_accepts_ordered_model_questions_without_execution():
    payload = clarification_payload()
    second = deepcopy(payload["clarification_plan"]["questions"][0])
    second.update(id="diet", prompt="Which dietary constraints apply?")
    payload["clarification_plan"]["questions"].append(second)
    plan = OperationPlan.model_validate(payload)
    assert [q.id for q in plan.clarification_plan.questions] == ["ranking", "diet"]
    assert plan.clarification_question == payload["clarification_plan"]["questions"][0]["prompt"]
    assert plan.steps == []
    assert OperationPlan.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize(
    "invalid",
    [
        "empty",
        "too_many",
        "duplicate_question",
        "duplicate_option",
        "multiple_recommended",
        "blank_prompt",
        "blank_label",
        "extra_question_field",
        "too_few_options",
        "too_many_options",
        "other_without_text",
        "coerced_boolean",
        "executable",
        "steps",
    ],
)
def test_rejects_malformed_or_executable_clarification(invalid):
    payload = clarification_payload()
    questions = payload["clarification_plan"]["questions"]
    question = questions[0]
    if invalid == "empty":
        questions.clear()
    elif invalid == "too_many":
        questions.extend({**deepcopy(question), "id": str(i)} for i in range(3))
    elif invalid == "duplicate_question":
        questions.append(deepcopy(question))
    elif invalid == "duplicate_option":
        question["options"][1]["id"] = "1"
    elif invalid == "multiple_recommended":
        question["options"][1]["recommended"] = True
    elif invalid == "blank_prompt":
        question["prompt"] = "  "
    elif invalid == "blank_label":
        question["options"][0]["label"] = "  "
    elif invalid == "extra_question_field":
        question["execute_now"] = True
    elif invalid == "too_few_options":
        question["options"].pop()
    elif invalid == "too_many_options":
        question["options"].extend(deepcopy(question["options"]))
    elif invalid == "other_without_text":
        question["allow_freeform_answer"] = False
    elif invalid == "coerced_boolean":
        question["options"][0]["recommended"] = "true"
    elif invalid == "executable":
        payload["needs_clarification"] = False
    elif invalid == "steps":
        payload["steps"] = [
            {"operation": "create_document", "input_ref": "source.pdf", "output_type": "docx"}
        ]
    with pytest.raises(ValidationError):
        OperationPlan.model_validate(payload)


def test_legacy_single_question_still_valid():
    plan = OperationPlan(
        intent_category="MODIFY",
        confidence=1,
        needs_clarification=True,
        clarification_question="Which section should change?",
    )
    assert plan.clarification_plan is None


def test_fully_specified_uploaded_pdf_creation_does_not_ask_questions():
    model = MagicMock()
    plan = DocumentAutomationService(chat_service=model).plan(
        "create a Word document summarizing this uploaded PDF",
        (AvailableDocument(73, "sample-1.pdf", DocumentType.PDF, is_latest=True),),
        explicit_source=True,
    )
    assert plan.status == "ready"
    assert plan.clarifying_question is None
    assert len(plan.steps) == 1
    model.chat.assert_not_called()
