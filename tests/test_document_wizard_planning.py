import json
from unittest.mock import MagicMock

import pytest

from app.services.document.document_automation_service import (
    DocumentAutomationPlanningError,
    DocumentAutomationService,
)


def test_options_and_plain_question_envelopes():
    for options in [
        None,
        [],
        [{"id": "word", "label": "Word", "recommended": True}, {"id": "pdf", "label": "PDF"}],
    ]:
        model = MagicMock()
        model.chat.return_value = json.dumps(
            {
                "status": "clarification_required",
                "clarifying_question": "Which file format?",
                "clarification_options": options,
                "steps": [],
            }
        )
        result = DocumentAutomationService(chat_service=model).plan(
            "Make me a product brochure", ()
        )
        assert result.clarifying_question == "Which file format?"
        assert len(result.clarification_options) == len(options or [])
        model.chat.assert_called_once()


@pytest.mark.parametrize(
    "options",
    [
        [{"id": "one", "label": "Only"}],
        [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}],
        [
            {"id": "a", "label": "A", "recommended": True},
            {"id": "b", "label": "B", "recommended": True},
        ],
    ],
)
def test_invalid_choices_are_rejected(options):
    with pytest.raises(DocumentAutomationPlanningError):
        DocumentAutomationService._parse_envelope(
            json.dumps(
                {
                    "status": "clarification_required",
                    "clarifying_question": "Choose?",
                    "clarification_options": options,
                    "steps": [],
                }
            )
        )
