from unittest.mock import MagicMock

import pytest

from app.schemas.chat import (
    ChatDocumentAutomationResponse,
    ChatDocumentAutomationState,
    ChatHistoryMessage,
)
from app.services.document.document_automation_service import DocumentAutomationService
from app.services.document.document_operation_registry import DocumentOperation, DocumentType


def history():
    return [
        ChatHistoryMessage(role="user", content="Create a new Excel document"),
        ChatHistoryMessage(role="assistant", content="Which headers should the Excel file use?"),
        ChatHistoryMessage(role="user", content="Header - Uninstall\nColumn - Install"),
        ChatHistoryMessage(role="assistant", content='What text goes under the column "Install"?'),
    ]


@pytest.mark.parametrize("answer", ["Header - Uninstall\nColumn - Install", "yes", "okay"])
def test_ambiguous_layout_and_bare_acknowledgement_offer_concrete_choices(answer):
    model = MagicMock()
    service = DocumentAutomationService(chat_service=model)
    plan = service.plan(answer, (), conversation_history=history())
    assert plan.status == "clarification_required"
    assert len(plan.clarification_options) == 3
    assert all(not choice.recommended for choice in plan.clarification_options)
    assert all(
        '"Uninstall"' in choice.label and '"Install"' in choice.label
        for choice in plan.clarification_options
    )
    assert plan.clarifying_question != history()[-1].content
    model.chat.assert_not_called()


@pytest.mark.parametrize("source_mode", ["auto", "description"])
def test_each_choice_advances_to_a_new_excel_plan_without_repeating_the_question(source_mode):
    model = MagicMock()
    service = DocumentAutomationService(chat_service=model)
    choices = service.plan("yes", (), conversation_history=history()).clarification_options
    for choice in choices:
        plan = service.plan(
            choice.label, (), conversation_history=history(), source_mode=source_mode
        )
        assert plan.status == "ready"
        assert len(plan.steps) == 1
        assert plan.steps[0].intent.operation == DocumentOperation.CREATE_DOCUMENT
        assert plan.steps[0].intent.output_type == DocumentType.XLSX
        assert plan.steps[0].source_filenames == ()
        assert choice.label in plan.steps[0].instruction
    model.chat.assert_not_called()


def test_layout_values_are_taken_from_the_current_user_answer():
    service = DocumentAutomationService(chat_service=MagicMock())
    plan = service.plan("Header: Stock\nColumn: Quantity", (), conversation_history=history())
    assert all(
        '"Stock"' in choice.label and '"Quantity"' in choice.label
        for choice in plan.clarification_options
    )
    assert all("Install" not in choice.label for choice in plan.clarification_options)


def test_bare_yes_after_the_layout_question_keeps_the_choices_and_explains_selection():
    service = DocumentAutomationService(chat_service=MagicMock())
    first = service.plan("Header - Uninstall\nColumn - Install", (), conversation_history=history())
    followup_history = history() + [
        ChatHistoryMessage(role="assistant", content=first.clarifying_question)
    ]
    answer = service.plan("yes", (), conversation_history=followup_history)
    assert "does not select a layout" in answer.clarifying_question
    assert answer.clarification_options == first.clarification_options


def test_yes_after_a_review_does_not_restore_an_old_layout_question():
    service = DocumentAutomationService(chat_service=MagicMock())
    review_history = history() + [
        ChatHistoryMessage(
            role="assistant", content="Review: Excel with two columns. No file has been built yet."
        )
    ]
    assert service._clarify_spreadsheet_layout("yes", review_history) is None


def test_plain_questions_and_unrelated_context_do_not_trigger_layout_choices():
    service = DocumentAutomationService(chat_service=MagicMock())
    assert (
        service._clarify_spreadsheet_layout(
            "yes", [ChatHistoryMessage(role="assistant", content="Which country?")]
        )
        is None
    )
    assert service._clarify_spreadsheet_layout("Header: Stock\nColumn: Quantity", []) is None


def test_old_open_question_receives_the_composer_contract():
    response = ChatDocumentAutomationResponse.model_validate(
        {
            "session_id": 103,
            "status": "clarification_required",
            "response": "Which title?",
            "clarification": None,
        }
    )
    assert response.clarification.model_dump() == {
        "question": "Which title?",
        "options": [],
        "allow_other": True,
    }


def test_old_saved_layout_question_restores_choices_without_modifying_history():
    stored = {
        "instruction": "yes",
        "request_instruction": "Header - Uninstall\nColumn - Install",
        "choices": [{"question": "Which Excel columns?", "answer": "yes"}],
        "response": {
            "session_id": 103,
            "status": "clarification_required",
            "response": history()[-1].content,
            "clarification": None,
        },
    }
    state = ChatDocumentAutomationState.model_validate(stored)
    assert len(state.response.clarification.options) == 3
    assert state.response.clarification.allow_other
    assert stored["response"]["clarification"] is None
    assert state.choices[0].answer == "yes"


@pytest.mark.parametrize("status", ["done", "ready_for_review", "failed"])
def test_completed_and_review_turns_never_restore_pending_choices(status):
    state = ChatDocumentAutomationState.model_validate(
        {
            "instruction": "yes",
            "request_instruction": "Header - Uninstall\nColumn - Install",
            "response": {
                "session_id": 103,
                "status": status,
                "response": "Excel file",
                "clarification": None,
            },
        }
    )
    assert state.response.clarification is None
