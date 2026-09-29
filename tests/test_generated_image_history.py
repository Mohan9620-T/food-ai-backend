import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import chat_images
from app.config import settings
from app.models.chat import ChatDocumentAttachment, ChatMessageRecord
from app.services import image_subject_service as subjects
from app.services.chat_service import ChatService
from app.services.chat_vision_service import ChatVisionService
from app.services.image_generation_service import (
    GeneratedImage,
    ImageGenerationError,
    ImageGenerationService,
)
from tests.test_image_generation import headers

PROMPT = "can you generate an artistic illustration of the tamil nadu CM"
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


@pytest.mark.parametrize(
    "prompt",
    [
        "can you create tamil nadu CM image",
        "Create an accurate CM portrait, not an illustration",
    ],
)
def test_office_portrait_returns_source_without_an_unreliable_face(client, monkeypatch, prompt):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=SUBJECT))
    generate = AsyncMock()
    monkeypatch.setattr(chat_images.service, "generate", generate)
    result = client.post("/chat/images", headers=owner, json={"message": prompt})
    assert result.status_code == 200
    assert result.json()["attachment"] is None
    assert "Example Minister" in result.json()["response"]
    assert "haven't generated a portrait" in result.json()["response"]
    assert SUBJECT["subject_source_url"] in result.json()["response"]
    generate.assert_not_awaited()
    history = client.get(f"/chat/sessions/{result.json()['session_id']}", headers=owner).json()
    assert history["messages"][-1]["content"] == result.json()["response"]


@pytest.mark.parametrize("endpoint", ["/chat/", "/chat/stream"])
def test_generated_comparison_and_identity_followups_use_the_owned_saved_context(
    client, monkeypatch, valid_png_bytes, db_session, endpoint
):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=SUBJECT))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(return_value=GeneratedImage(valid_png_bytes, PROMPT, "1:1", 2, 2)),
    )
    created = client.post("/chat/images", headers=owner, json={"message": PROMPT}).json()
    session_id = created["session_id"]
    describe = Mock(
        return_value="Image 1 has glasses. Image 2 has a dark jacket. The generated likeness is unverified."
    )
    monkeypatch.setattr(ChatVisionService, "describe", describe)
    second = valid_png_bytes + b"reference"
    comparison = client.post(
        "/chat/vision",
        headers=owner,
        data={"session_id": session_id, "message": "can you identify the differences both images"},
        files=[
            ("images", ("generated.png", valid_png_bytes, "image/png")),
            ("images", ("reference.png", second, "image/png")),
        ],
    )
    assert comparison.status_code == 200
    context = "\n".join(item.content for item in describe.call_args.kwargs["conversation_history"])
    assert PROMPT in context
    assert "Example Minister" in context
    assert "Image(s) [1] are exact copies" in context
    assert '"likeness_verified": false' in context
    assert describe.call_args.kwargs["additional_images"] == [second]

    monkeypatch.setattr(
        ChatService, "_maybe_web_search", lambda *a, **k: pytest.fail("No identity web lookup")
    )

    def follow(message):
        response = client.post(
            f"{endpoint}?session_id={session_id}", headers=owner, json={"message": message}
        )
        assert response.status_code == 200
        if endpoint.endswith("stream"):
            return "".join(
                json.loads(line).get("content", "") for line in response.text.splitlines()
            )
        return response.json()["response"]

    assert "Which image do you mean?" in follow("who is he?")
    assert "intended to depict **Example Minister**" in follow("who is in the first image?")
    assert "intended to depict **Example Minister**" in follow("who is he in the generated image?")
    complaint = follow("The generated image doesn't look like the CM")
    assert "cannot use your reference photo" in complaint
    assert "Example Minister" in complaint
    refreshed = client.get(f"/chat/sessions/{session_id}", headers=owner).json()
    assert refreshed["messages"][-1]["content"] == complaint
    # Synthetic visual evidence must not get persisted onto bot messages.
    assert not any(
        record.image_data
        for record in db_session.query(ChatMessageRecord).filter(
            ChatMessageRecord.session_id == session_id, ChatMessageRecord.sender == "bot"
        )
    )


def test_generated_image_can_be_described_again_without_reupload(
    client, monkeypatch, valid_png_bytes
):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(return_value=GeneratedImage(valid_png_bytes, "A robot chef", "1:1", 2, 2)),
    )
    created = client.post(
        "/chat/images", headers=owner, json={"message": "Draw a robot chef"}
    ).json()
    describe = Mock(return_value="The robot has a white apron.")
    monkeypatch.setattr(ChatVisionService, "describe", describe)
    response = client.post(
        f"/chat/stream?session_id={created['session_id']}",
        headers=owner,
        json={"message": "Please describe this"},
    )
    assert response.status_code == 200
    assert "white apron" in response.text
    assert describe.call_args.args[0] == valid_png_bytes


def test_legacy_image_without_metadata_uses_original_saved_request(
    client, monkeypatch, valid_png_bytes, db_session
):
    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(return_value=GeneratedImage(valid_png_bytes, "A portrait", "1:1", 2, 2)),
    )
    created = client.post("/chat/images", headers=owner, json={"message": PROMPT}).json()
    attachment = db_session.get(ChatDocumentAttachment, created["attachment"]["id"])
    attachment.generation_metadata = None
    db_session.commit()
    response = client.post(
        f"/chat/?session_id={created['session_id']}",
        headers=owner,
        json={"message": "ivaru yaaru?"},
    )
    assert "no verified person's name" in response.json()["response"]
    assert PROMPT in response.json()["response"]


def test_visual_provenance_does_not_leak_between_sessions(
    client, monkeypatch, valid_png_bytes, db_session
):
    from app.services.generated_image_context import generated_image_analysis_context

    owner = headers(client)
    monkeypatch.setattr(chat_images, "resolve_image_subject", AsyncMock(return_value=SUBJECT))
    monkeypatch.setattr(
        chat_images.service,
        "generate",
        AsyncMock(return_value=GeneratedImage(valid_png_bytes, PROMPT, "1:1", 2, 2)),
    )
    client.post("/chat/images", headers=owner, json={"message": PROMPT})
    other = client.post("/chat/sessions", headers=owner, json={"title": "Another chat"}).json()[
        "id"
    ]
    assert generated_image_analysis_context(db_session, other, [valid_png_bytes]) == []
