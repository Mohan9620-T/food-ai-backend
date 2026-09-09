import httpx

from app.config import settings


class FakeResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"NVIDIA test "}}]}'
        yield 'data: {"choices":[{"delta":{"content":"response"}}]}'
        yield "data: [DONE]"


class FakeAsyncClient:
    last_request: dict | None = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def stream(self, method: str, url: str, **kwargs):
        self.__class__.last_request = {"method": method, "url": url, **kwargs}
        return FakeResponse()


def test_nvidia_chat_sends_text_and_returns_response(client, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr("app.api.nvidia_chat.httpx.AsyncClient", FakeAsyncClient)

    response = client.post("/nvidia-chat", json={"text": "Hello NVIDIA"})

    assert response.status_code == 200
    assert response.json() == {
        "response": "NVIDIA test response",
        "model": settings.NVIDIA_TEST_CHAT_MODEL,
    }
    assert FakeAsyncClient.last_request is not None
    assert FakeAsyncClient.last_request["json"]["messages"] == [
        {"role": "user", "content": "Hello NVIDIA"}
    ]


def test_nvidia_chat_rejects_blank_text(client):
    response = client.post("/nvidia-chat", json={"text": ""})

    assert response.status_code == 422


def test_nvidia_chat_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")

    response = client.post("/nvidia-chat", json={"text": "Hello"})

    assert response.status_code == 503
    assert response.json()["detail"] == "NVIDIA_API_KEY is not configured"


class TimeoutAsyncClient(FakeAsyncClient):
    def stream(self, method: str, url: str, **kwargs):
        request = httpx.Request("POST", url)
        raise httpx.ReadTimeout("timed out", request=request)


def test_nvidia_chat_reports_timeout(client, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr("app.api.nvidia_chat.httpx.AsyncClient", TimeoutAsyncClient)

    response = client.post("/nvidia-chat", json={"text": "Hello"})

    assert response.status_code == 504
    assert response.json()["detail"] == "NVIDIA request timed out"
