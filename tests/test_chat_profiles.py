"""Unit tests for chat-mode profile resolution and LLM_PROFILE_OVERRIDES parsing."""

import json

import pytest

from app.config import settings
from app.services.chat_profiles import (
    list_profiles,
    resolve_profile,
    thinking_kwarg_for_model,
)


@pytest.mark.parametrize("name", [None, "", "  ", "not-a-real-mode", "GENERAL"])
def test_resolve_profile_falls_back_to_general_for_unknown_or_empty_names(name):
    profile = resolve_profile(name)
    assert profile.name == "general"


@pytest.mark.parametrize("name", ["reasoning", "math", "coding", "planning", "document"])
def test_resolve_profile_resolves_each_public_profile_by_name(name):
    assert resolve_profile(name).name == name


def test_resolve_profile_is_case_insensitive_and_trims_whitespace():
    assert resolve_profile(" Reasoning ").name == "reasoning"


def test_list_profiles_returns_exactly_the_six_public_profiles():
    names = {profile.name for profile in list_profiles()}
    assert names == {"general", "reasoning", "math", "coding", "planning", "document"}


def test_reasoning_and_math_and_coding_default_to_thinking_enabled():
    assert resolve_profile("reasoning").thinking is True
    assert resolve_profile("math").thinking is True
    assert resolve_profile("coding").thinking is True


def test_planning_and_document_default_to_thinking_disabled():
    assert resolve_profile("planning").thinking is False
    assert resolve_profile("document").thinking is False


def test_general_profile_tracks_the_live_ollama_chat_think_setting(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_CHAT_THINK", True)
    assert resolve_profile("general").thinking is True
    assert resolve_profile(None).thinking is True


def test_reasoning_tier_falls_back_to_the_chat_model_when_no_reasoning_model_is_set(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_REASONING_MODEL", "")
    monkeypatch.setattr(settings, "NVIDIA_CHAT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    assert resolve_profile("reasoning").resolved_model() == "nvidia/nemotron-3-super-120b-a12b"


def test_reasoning_tier_uses_the_dedicated_reasoning_model_when_set(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_REASONING_MODEL", "some-vendor/reasoning-model")
    assert resolve_profile("reasoning").resolved_model() == "some-vendor/reasoning-model"


def test_general_profile_always_uses_the_chat_model(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_REASONING_MODEL", "some-vendor/reasoning-model")
    monkeypatch.setattr(settings, "NVIDIA_CHAT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    assert resolve_profile("general").resolved_model() == "nvidia/nemotron-3-super-120b-a12b"


@pytest.mark.parametrize(
    ("model", "expected_key"),
    [
        ("deepseek-ai/deepseek-r1", "thinking"),
        ("DeepSeek-V3", "thinking"),
        ("nvidia/nemotron-3-super-120b-a12b", "enable_thinking"),
        ("qwen/qwen3-8b", "enable_thinking"),
    ],
)
def test_thinking_kwarg_for_model_picks_the_right_key(model, expected_key):
    assert thinking_kwarg_for_model(model) == expected_key


def test_profile_overrides_whitelist_drops_unknown_fields(monkeypatch):
    monkeypatch.setattr(
        settings,
        "LLM_PROFILE_OVERRIDES",
        {"coding": {"temperature": 0.9, "not_a_real_field": "ignored"}},
    )
    profile = resolve_profile("coding")
    assert profile.temperature == 0.9
    assert not hasattr(profile, "not_a_real_field")


def test_profile_overrides_only_apply_to_the_named_profile(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROFILE_OVERRIDES", {"coding": {"temperature": 0.9}})
    assert resolve_profile("general").temperature != 0.9


def test_profile_overrides_can_replace_the_label_and_description(monkeypatch):
    monkeypatch.setattr(
        settings,
        "LLM_PROFILE_OVERRIDES",
        {"math": {"label": "Numbers", "description": "Custom description."}},
    )
    profile = resolve_profile("math")
    assert profile.label == "Numbers"
    assert profile.description == "Custom description."


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[1, 2, 3]",
        '"just a string"',
        '{"coding": "not-a-dict"}',
        '{"coding": [1, 2]}',
    ],
)
def test_malformed_llm_profile_overrides_env_value_is_ignored_not_crashed(monkeypatch, raw):
    monkeypatch.setenv("LLM_PROFILE_OVERRIDES", raw)
    # Re-run the same parser the settings module uses at import time, rather than
    # reloading the whole settings module (which would also replay load_dotenv()
    # and every other env read in the file).
    from app.config.settings import _parse_llm_profile_overrides

    assert _parse_llm_profile_overrides() == {}


def test_empty_llm_profile_overrides_env_value_parses_to_empty_dict(monkeypatch):
    monkeypatch.delenv("LLM_PROFILE_OVERRIDES", raising=False)
    from app.config.settings import _parse_llm_profile_overrides

    assert _parse_llm_profile_overrides() == {}


def test_valid_llm_profile_overrides_env_value_parses_and_lowercases_names(monkeypatch):
    monkeypatch.setenv("LLM_PROFILE_OVERRIDES", json.dumps({"CODING": {"temperature": 0.1}}))
    from app.config.settings import _parse_llm_profile_overrides

    assert _parse_llm_profile_overrides() == {"coding": {"temperature": 0.1}}
