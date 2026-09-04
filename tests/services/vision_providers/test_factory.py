from app.config import settings
from app.services.vision_providers import get_vision_provider
from app.services.vision_providers.nvidia_provider import NvidiaVisionProvider
from app.services.vision_providers.ollama_provider import OllamaVisionProvider


def test_factory_selects_ollama(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")

    assert isinstance(get_vision_provider(), OllamaVisionProvider)


def test_factory_selects_nvidia(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "nvidia")

    assert isinstance(get_vision_provider(), NvidiaVisionProvider)


def test_factory_uses_ollama_default(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")

    assert isinstance(get_vision_provider(), OllamaVisionProvider)


def test_factory_falls_back_to_ollama_for_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "unsupported")

    assert isinstance(get_vision_provider(), OllamaVisionProvider)
