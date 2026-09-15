import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services.chat_document_service import ChatDocumentService
from app.services.chat_service import ChatModelUnavailableError


def content(rows):
    return json.dumps(
        {
            "title": "Dishes",
            "tables": [{"headers": ["Dish"], "rows": [[str(i)] for i in range(rows)]}],
        }
    )


def test_wrong_top_n_row_count_gets_one_validated_correction(monkeypatch):
    service = ChatDocumentService()
    completion = AsyncMock(side_effect=[content(3), content(2)])
    monkeypatch.setattr(service.chat_service, "complete_chat", completion)
    result = asyncio.run(service.generate_content("Create Excel with top 2 dishes", [], []))
    assert len(result.tables[0].rows) == 2
    assert completion.await_count == 2
    assert "EXACTLY 2" in completion.call_args.args[0]


def test_invalid_correction_cannot_be_rendered(monkeypatch):
    service = ChatDocumentService()
    completion = AsyncMock(return_value=content(3))
    monkeypatch.setattr(service.chat_service, "complete_chat", completion)
    with pytest.raises(ChatModelUnavailableError):
        asyncio.run(service.generate_content("Create Excel with top 2 dishes", [], []))
    assert completion.await_count == 2


def test_valid_structure_needs_no_correction(monkeypatch):
    service = ChatDocumentService()
    completion = AsyncMock(return_value=content(2))
    monkeypatch.setattr(service.chat_service, "complete_chat", completion)
    asyncio.run(service.generate_content("Create Excel with top 2 dishes", [], []))
    completion.assert_awaited_once()
