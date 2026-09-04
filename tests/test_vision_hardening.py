import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from app.config import settings
from app.services.chat_vision_service import ChatVisionService
from app.services.image_parser_service import ImageParserService, VisionModelUnavailableError


class FakeResponse:
    def __init__(self, content: str):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.content}}


def test_meal_and_chat_vision_requests_share_one_inference_slot(monkeypatch):
    active = 0
    maximum_active = 0
    counter_lock = threading.Lock()

    def post(url, **kwargs):
        nonlocal active, maximum_active
        with counter_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with counter_lock:
            active -= 1
        content = json.dumps(
            {
                "image_type": "food",
                "items": [
                    {
                        "name": "apple",
                        "confidence": "high",
                        "visual_evidence": "round red fruit",
                    }
                ],
                "uncertain_items": [],
            }
        )
        return FakeResponse(content)

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr(ChatVisionService, "_extract_ocr_text", lambda self, data: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        meal_future = executor.submit(ImageParserService().parse, b"meal-image")
        chat_future = executor.submit(ChatVisionService().describe, b"chat-image", None)
        assert meal_future.result()[0].food_name == "apple"
        assert chat_future.result() == "I can see apple (round red fruit)."

    assert maximum_active == 1


def test_meal_vision_timeout_is_bounded_and_graceful(monkeypatch):
    observed_timeout = None

    def timeout(url, **kwargs):
        nonlocal observed_timeout
        observed_timeout = kwargs["timeout"]
        raise requests.Timeout("Ollama exceeded its deadline")

    monkeypatch.setattr("requests.post", timeout)

    with pytest.raises(VisionModelUnavailableError, match="analysis timed out after"):
        ImageParserService().parse(b"image")

    assert observed_timeout == settings.OLLAMA_VISION_TIMEOUT_SECONDS
