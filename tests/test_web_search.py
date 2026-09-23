import json

import requests

from app.config import settings
from app.services import web_search_provider
from app.services.chat_service import ChatService


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload

    def close(self):
        pass


# --- web_search_provider.search ---------------------------------------------------


def test_url_reading_works_without_tavily_key(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")
    monkeypatch.setattr(
        "app.services.chat_service.read_page",
        lambda url: [{"title": "Example", "url": url, "content": "Verified page text"}],
    )
    context = ChatService()._maybe_web_search("Read https://example.com")
    assert "Verified page text" in context and "Markdown links" in context
    assert "untrusted" in context


def test_explicit_search_reports_missing_configuration_without_llm_call(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")
    monkeypatch.setattr(
        "app.services.chat_service.requests.post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("No LLM call")),
    )
    answer = ChatService().chat("Find the latest news", [], [], web_search=True)
    assert "not configured" in answer
    assert "WEB_ACCESS_UNAVAILABLE" not in answer


def test_explicit_search_uses_provider_even_without_nvidia_primary(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        "app.services.chat_service.web_search_provider.search",
        lambda q: [
            {"title": "Live source", "url": "https://example.com", "content": "Latest data"}
        ],
    )
    assert "Latest data" in ChatService()._maybe_web_search("Find news", force=True)


def test_explicit_search_failure_does_not_claim_live_results(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr("app.services.chat_service.web_search_provider.search", lambda q: None)
    answer = ChatService().chat("Search the web for current weather", [], [])
    assert "not verified" in answer


def test_search_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", False)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")

    assert web_search_provider.search("anything") is None


def test_search_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")

    assert web_search_provider.search("anything") is None


def test_search_returns_parsed_results(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    payload = {
        "results": [
            {"title": "A", "url": "https://a.example", "content": "Content A"},
            {"title": "B", "url": "https://b.example", "content": "Content B"},
            {"title": "No URL", "content": "dropped"},
        ]
    }
    monkeypatch.setattr(
        web_search_provider._session, "post", lambda *a, **k: _Response(200, payload)
    )

    results = web_search_provider.search("food question")

    assert results == [
        {"title": "A", "url": "https://a.example", "content": "Content A"},
        {"title": "B", "url": "https://b.example", "content": "Content B"},
    ]


def test_search_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(web_search_provider._session, "post", lambda *a, **k: _Response(500, {}))

    assert web_search_provider.search("anything") is None


def test_search_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(
        web_search_provider._session,
        "post",
        lambda *a, **k: _Response(200, {"results": "not-a-list"}),
    )

    assert web_search_provider.search("anything") is None


def test_search_returns_none_on_network_error(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")

    def broken_post(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(web_search_provider._session, "post", broken_post)

    assert web_search_provider.search("anything") is None


# --- ChatService._maybe_web_search -------------------------------------------------


def _tool_call_response(query):
    return _Response(
        200,
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "web_search",
                                    "arguments": json.dumps({"query": query}),
                                }
                            }
                        ]
                    }
                }
            ]
        },
    )


def _no_tool_call_response():
    return _Response(200, {"choices": [{"message": {"content": "no search needed"}}]})


def test_maybe_web_search_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", False)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")

    calls = []
    monkeypatch.setattr(
        "app.services.chat_service.requests.post",
        lambda *a, **k: calls.append(1) or _no_tool_call_response(),
    )

    assert ChatService()._maybe_web_search("what's the weather right now?") is None
    assert calls == []


def test_maybe_web_search_returns_none_without_tavily_key(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")

    assert ChatService()._maybe_web_search("anything") is None


def test_maybe_web_search_returns_none_when_not_nvidia_primary(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")

    assert ChatService()._maybe_web_search("anything") is None


def test_maybe_web_search_returns_none_when_model_declines(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(
        "app.services.chat_service.requests.post", lambda *a, **k: _no_tool_call_response()
    )

    assert ChatService()._maybe_web_search("what is 2+2?") is None


def test_maybe_web_search_runs_search_and_formats_context(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(
        "app.services.chat_service.requests.post",
        lambda *a, **k: _tool_call_response("current gold price per gram"),
    )
    monkeypatch.setattr(
        "app.services.chat_service.web_search_provider.search",
        lambda query: [{"title": "Gold Price Today", "url": "https://x.example", "content": "..."}],
    )

    result = ChatService()._maybe_web_search("what's the gold price today?")

    assert result is not None
    assert "current gold price per gram" in result
    assert "Gold Price Today" in result
    assert "https://x.example" in result


def test_maybe_web_search_returns_none_when_search_fails(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(
        "app.services.chat_service.requests.post",
        lambda *a, **k: _tool_call_response("current gold price"),
    )
    monkeypatch.setattr("app.services.chat_service.web_search_provider.search", lambda query: None)

    assert ChatService()._maybe_web_search("what's the gold price today?") is None


def test_maybe_web_search_returns_none_on_decision_call_failure(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")

    def broken_post(*args, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr("app.services.chat_service.requests.post", broken_post)

    assert ChatService()._maybe_web_search("anything") is None


def test_chat_injects_web_search_context_into_the_completion(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(
        ChatService,
        "_maybe_web_search",
        lambda self, message: 'WEB SEARCH RESULTS for "gold price":\n\n1. Gold Price Today',
    )

    seen_messages = []

    def post(url, **kwargs):
        if "tools" in kwargs.get("json", {}):
            raise AssertionError("the decision call should be mocked away by _maybe_web_search")
        seen_messages.extend(kwargs["json"]["messages"])
        return _Response(200, {"choices": [{"message": {"content": "It's $80/gram."}}]})

    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    answer = ChatService().chat("what's the gold price today?", [], [])

    assert answer == "It's $80/gram."
    assert any("Gold Price Today" in message.get("content", "") for message in seen_messages)


def test_chat_web_search_disabled_by_default_makes_one_call(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvidia-test")
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return _Response(200, {"choices": [{"message": {"content": "answer"}}]})

    monkeypatch.setattr("app.services.chat_service.requests.post", post)

    assert ChatService().chat("hello", [], []) == "answer"
    assert len(calls) == 1
