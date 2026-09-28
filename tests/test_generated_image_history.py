import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.api import chat_images
from app.config import settings
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord
from app.services import image_subject_service as subjects
from app.services.chat_service import ChatService
from app.services.image_generation_service import (
    GeneratedImage,
    ImageGenerationError,
    ImageGenerationService,
)
from tests.test_image_generation import headers

PROMPT = "can you generate tamil nadu CM image"
SUBJECT = {
    "subject": "Example Minister",
    "subject_source_url": "https://government.example/chief-minister",
    "subject_source_title": "Chief Minister",
    "subject_quote": "Example Minister is the current Chief Minister.",
    "subject_resolved_at": "2026-09-28T00:00:00+00:00",
}


def test_request_is_saved_before_generation_and_name_and_attachment_survive_reload(
    client, db_session, monkeypatch, valid_png_bytes
):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=SUBJECT))

    async def generate(prompt, aspect_ratio, *, subject_name):
        assert subject_name == "Example Minister"
        assert prompt.startswith("Subject: Example Minister.")
        saved = db_session.query(ChatMessageRecord).one()
        assert saved.sender == "user" and saved.content == PROMPT
        return GeneratedImage(valid_png_bytes, prompt, "1:1", 2, 2)

    monkeypatch.setattr(chat_images.service, "generate", generate)
    result = client.post("/chat/images", headers=owner, json={"message": PROMPT}).json()
    session_id = result["session_id"]
    restored = client.get(f"/chat/sessions/{session_id}", headers=owner).json()
    assert len(restored["messages"]) == 2
    assert "not an official photograph" in restored["messages"][-1]["content"]
    attachment_id = restored["messages"][-1]["attachments"][0]["id"]
    metadata = db_session.get(ChatDocumentAttachment, attachment_id).generation_metadata
    assert metadata["subject"] == "Example Minister"
    assert metadata["prompt"] == PROMPT
    assert metadata["subject_source_url"] == SUBJECT["subject_source_url"]
    assert (
        client.get(f"/chat/documents/{attachment_id}/download", headers=owner).content
        == valid_png_bytes
    )

    def no_lookup(*args, **kwargs):
        raise AssertionError("An identity follow-up must use saved image provenance")

    monkeypatch.setattr(ChatService, "_maybe_web_search", no_lookup)
    follow_up = client.post(
        f"/chat/stream?session_id={session_id}", headers=owner, json={"message": "who is he?"}
    )
    events = [json.loads(line) for line in follow_up.text.splitlines()]
    answer = "".join(event.get("content", "") for event in events)
    assert "intended to depict **Example Minister**" in answer
    assert "can't confirm" in answer
    assert SUBJECT["subject_source_url"] in answer
    assert events[-1]["type"] == "done"
    refreshed = client.get(f"/chat/sessions/{session_id}", headers=owner).json()
    assert refreshed["messages"][-1]["content"] == answer
    repeated = client.post(
        f"/chat/?session_id={session_id}", headers=owner, json={"message": "what is his name?"}
    )
    assert "Example Minister" in repeated.json()["response"]
    other_session = client.post(
        "/chat/sessions", headers=owner, json={"title": "Unrelated"}
    ).json()["id"]
    from app.services.generated_image_context import generated_image_follow_up

    assert generated_image_follow_up(db_session, other_session, "who is he?") is None
    db_session.add_all(
        [
            ChatMessageRecord(
                session_id=session_id, sender="user", content="Tell me about my friend"
            ),
            ChatMessageRecord(
                session_id=session_id, sender="bot", content="What would you like to share?"
            ),
        ]
    )
    db_session.commit()
    assert generated_image_follow_up(db_session, session_id, "who is he?") is None


def test_old_portrait_follow_up_does_not_invent_identity(client, monkeypatch, valid_png_bytes):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(return_value=GeneratedImage(valid_png_bytes, "An adult man", "1:1", 2, 2)),
    )
    created = client.post("/chat/images", headers=owner, json={"message": PROMPT}).json()
    response = client.post(
        f"/chat/?session_id={created['session_id']}", headers=owner, json={"message": "who is he?"}
    )
    assert response.status_code == 200
    assert "no verified person's name" in response.json()["response"]
    assert PROMPT in response.json()["response"]


def test_unverified_office_holder_does_not_generate_generic_portrait(
    client, monkeypatch, db_session
):
    owner = headers(client)
    monkeypatch.setattr(
        chat_images,
        "resolve_image_subject",
        AsyncMock(side_effect=ImageGenerationError("Cannot verify subject", 422)),
    )
    generate = AsyncMock()
    monkeypatch.setattr(chat_images.service, "generate", generate)
    response = client.post("/chat/images", headers=owner, json={"message": PROMPT})
    assert response.status_code == 422
    generate.assert_not_awaited()
    assert db_session.query(ChatDocumentAttachment).count() == 0
    assert db_session.query(ChatMessageRecord).count() == 2


@pytest.mark.parametrize(
    "case",
    ["valid", "unknown", "invented_quote", "wrong_url", "name_missing", "refused", "no_sources"],
)
def test_subject_resolution_requires_supported_live_evidence(monkeypatch, case):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(
        subjects.web_search_provider,
        "search",
        lambda query: (
            []
            if case == "no_sources"
            else [
                {
                    "url": SUBJECT["subject_source_url"],
                    "title": "Official",
                    "content": SUBJECT["subject_quote"],
                }
            ]
        ),
    )
    resolved = {
        "subject": SUBJECT["subject"],
        "source_url": SUBJECT["subject_source_url"],
        "quote": SUBJECT["subject_quote"],
    }
    if case == "unknown":
        resolved = {"error": "unverified"}
    if case == "invented_quote":
        resolved["quote"] = "Another unprovided quote about Example Minister."
    if case == "wrong_url":
        resolved["source_url"] = "https://invented.example"
    if case == "name_missing":
        resolved["subject"] = "Different Person"
    result = {
        "choices": [
            {
                "finish_reason": "content_filter" if case == "refused" else "stop",
                "message": {"content": json.dumps(resolved)},
            }
        ]
    }
    monkeypatch.setattr(
        subjects, "_request_preparation", AsyncMock(return_value=json.dumps(result).encode())
    )
    if case == "valid":
        assert asyncio.run(subjects.resolve_image_subject(PROMPT))["subject"] == SUBJECT["subject"]
    else:
        with pytest.raises(ImageGenerationError, match="couldn't verify"):
            asyncio.run(subjects.resolve_image_subject(PROMPT))


def test_ordinary_art_does_not_need_public_person_lookup(monkeypatch):
    def unexpected(*args):
        raise AssertionError("Unnecessary web request")

    monkeypatch.setattr(subjects.web_search_provider, "search", unexpected)
    assert asyncio.run(subjects.resolve_image_subject("A three-headed black dragon")) is None
    assert asyncio.run(subjects.resolve_image_subject("A 20 cm tall dragon at 9 PM")) is None
    assert (
        asyncio.run(subjects.resolve_image_subject("A fictional president in a fantasy kingdom"))
        is None
    )


def test_compaction_cannot_drop_verified_subject(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_IMAGE_GENERATION", True)
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    service = ImageGenerationService()
    monkeypatch.setattr(service, "_prepare_prompt", AsyncMock(return_value="A generic man"))
    with pytest.raises(ImageGenerationError, match="lost the verified"):
        asyncio.run(service.generate("Example Minister", subject_name="Example Minister"))
