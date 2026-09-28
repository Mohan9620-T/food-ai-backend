import base64
from unittest.mock import Mock

import pytest

from app.config import settings
from app.models.chat import ChatImageAttachment
from app.schemas.vision_result import VisionResult
from app.services.chat_vision_service import ChatVisionService
from app.services.vision_providers.nvidia_provider import NvidiaVisionProvider
from app.services.vision_providers.ollama_provider import OllamaVisionProvider
from tests.test_chat import _register_and_login


def test_multiple_images_persist_in_order_and_follow_up_uses_all(
    client, monkeypatch, valid_png_bytes, db_session
):
    headers = {"Authorization": f"Bearer {_register_and_login(client)}"}
    # Distinct valid original bytes, preserved exactly including PNG trailing data.
    second = valid_png_bytes + b"second"
    describe = Mock(return_value="Image 1 and Image 2 inspected.")
    monkeypatch.setattr(ChatVisionService, "describe", describe)
    response = client.post(
        "/chat/vision",
        headers=headers,
        data={"message": "Compare these images"},
        files=[
            ("images", ("one.png", valid_png_bytes, "image/png")),
            ("images", ("two.png", second, "image/png")),
        ],
    )
    assert response.status_code == 200
    describe.assert_called_once_with(
        valid_png_bytes, "Compare these images", additional_images=[second]
    )
    session_id = response.json()["session_id"]
    history = client.get(f"/chat/sessions/{session_id}", headers=headers).json()["messages"]
    assert len(history) == 2
    assert history[0]["image_url"] == history[0]["image_urls"][0]
    assert [base64.b64decode(url.split(",", 1)[1]) for url in history[0]["image_urls"]] == [
        valid_png_bytes,
        second,
    ]
    describe.reset_mock()
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    follow = client.post(
        f"/chat/stream?session_id={session_id}",
        headers=headers,
        json={"message": "Describe the second image", "history": [], "reference_history": []},
    )
    assert follow.status_code == 200
    assert describe.call_args.kwargs["additional_images"] == [second]
    assert db_session.query(ChatImageAttachment).count() == 1
    assert client.delete(f"/chat/sessions/{session_id}", headers=headers).status_code == 200
    assert db_session.query(ChatImageAttachment).count() == 0


@pytest.mark.parametrize(
    "case,status",
    [
        ("count", 422),
        ("type", 415),
        ("empty", 422),
        ("corrupt", 422),
        ("size", 413),
        ("total", 413),
    ],
)
def test_rejects_entire_invalid_batch(client, monkeypatch, valid_png_bytes, case, status):
    headers = {"Authorization": f"Bearer {_register_and_login(client)}"}
    describe = Mock()
    monkeypatch.setattr(ChatVisionService, "describe", describe)
    files = [("images", ("one.png", valid_png_bytes, "image/png"))]
    if case == "count":
        files *= 6
    elif case == "type":
        files.append(("images", ("file.pdf", b"pdf", "application/pdf")))
    elif case == "empty":
        files.append(("images", ("empty.png", b"", "image/png")))
    elif case == "corrupt":
        files.append(("images", ("invalid.png", b"not an image", "image/png")))
    elif case == "size":
        monkeypatch.setattr("app.api.chat.MAX_CHAT_IMAGE_BYTES", len(valid_png_bytes) - 1)
    else:
        monkeypatch.setattr("app.api.chat.MAX_CHAT_IMAGES_TOTAL_BYTES", len(valid_png_bytes))
        files *= 2
    response = client.post("/chat/vision", headers=headers, files=files)
    assert response.status_code == status
    describe.assert_not_called()
    assert client.get("/chat/sessions", headers=headers).json() == []


def test_multi_image_upload_cannot_use_another_users_chat(client, monkeypatch, valid_png_bytes):
    first = {"Authorization": f"Bearer {_register_and_login(client, 'first@example.com')}"}
    second = {"Authorization": f"Bearer {_register_and_login(client, 'second@example.com')}"}
    session_id = client.post("/chat/sessions", headers=first, json={"title": "Private"}).json()[
        "id"
    ]
    describe = Mock()
    monkeypatch.setattr(ChatVisionService, "describe", describe)
    response = client.post(
        "/chat/vision",
        headers=second,
        data={"session_id": session_id},
        files=[("images", ("one.png", valid_png_bytes, "image/png"))] * 2,
    )
    assert response.status_code == 404
    describe.assert_not_called()


@pytest.mark.parametrize("provider", ["nvidia", "ollama"])
def test_provider_sends_every_image_in_one_request(monkeypatch, provider):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test")
    result = VisionResult(image_type="other", answer="Both inspected", items=[], uncertain_items=[])
    response = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": result.model_dump_json()}}],
        "message": {"content": result.model_dump_json()},
    }
    post = Mock(return_value=response)
    if provider == "nvidia":
        monkeypatch.setattr("app.services.vision_providers.nvidia_provider._post_with_retry", post)
        NvidiaVisionProvider().infer(
            "system", "compare", "first", additional_images=("second", "third")
        )
        payload = post.call_args.kwargs["json"]["messages"][1]["content"]
        assert [
            item["image_url"]["url"].split(",")[1]
            for item in payload
            if item["type"] == "image_url"
        ] == ["first", "second", "third"]
    else:
        monkeypatch.setattr("app.services.vision_providers.ollama_provider.requests.post", post)
        OllamaVisionProvider().infer(
            "system", "compare", "first", additional_images=("second", "third")
        )
        assert post.call_args.kwargs["json"]["messages"][1]["images"] == [
            "first",
            "second",
            "third",
        ]


def test_vision_service_prepares_all_images_and_numbers_them(monkeypatch, valid_png_bytes):
    provider = Mock()
    provider.infer.return_value = VisionResult(
        image_type="other", answer="Compared both", items=[], uncertain_items=[]
    )
    monkeypatch.setattr("app.services.chat_vision_service.get_vision_provider", lambda: provider)
    assert (
        ChatVisionService().describe(
            valid_png_bytes, "Compare", additional_images=[valid_png_bytes]
        )
        == "Compared both"
    )
    assert len(provider.infer.call_args.kwargs["additional_images"]) == 1
    assert "2 attached images" in provider.infer.call_args.kwargs["user_prompt"]
