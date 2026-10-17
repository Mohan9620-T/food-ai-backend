"""Named chat-mode profiles: a profile picks a model family plus generation
tuning (temperature/top_p/max_tokens/timeout/thinking) for one turn.

Profiles are built fresh on every call (never cached at import time) so that
settings monkeypatches in tests, and any future dynamic settings reload, take
effect immediately - a module-level constant would bake in whatever
`settings.OLLAMA_CHAT_THINK`/`settings.CHAT_TEMPERATURE` happened to be at
import time.
"""

from dataclasses import dataclass, replace
from typing import Literal

from app.config import settings

Tier = Literal["chat", "reasoning"]

# Fields a caller may override via LLM_PROFILE_OVERRIDES. Anything else in an
# override dict is silently dropped.
_OVERRIDE_FIELDS = {
    "model",
    "thinking",
    "temperature",
    "top_p",
    "max_tokens",
    "timeout_seconds",
    "label",
    "description",
}


@dataclass(frozen=True)
class ChatProfile:
    name: str
    label: str
    description: str
    tier: Tier
    thinking: bool
    temperature: float
    top_p: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    model: str | None = None

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        if self.tier == "reasoning" and settings.NVIDIA_REASONING_MODEL:
            return settings.NVIDIA_REASONING_MODEL
        return settings.NVIDIA_CHAT_MODEL


def thinking_kwarg_for_model(model: str) -> str:
    """The chat_template_kwargs key a model family expects to toggle thinking."""
    return "thinking" if "deepseek" in model.lower() else "enable_thinking"


def _base_profiles() -> dict[str, ChatProfile]:
    return {
        "general": ChatProfile(
            name="general",
            label="General",
            description="Everyday conversation and quick questions.",
            tier="chat",
            thinking=settings.OLLAMA_CHAT_THINK,
            temperature=settings.CHAT_TEMPERATURE,
        ),
        "reasoning": ChatProfile(
            name="reasoning",
            label="Reasoning",
            description="Step-by-step reasoning for complex, open-ended questions.",
            tier="reasoning",
            thinking=True,
            temperature=0.2,
        ),
        "math": ChatProfile(
            name="math",
            label="Mathematics",
            description="Careful, step-by-step quantitative problem solving.",
            tier="reasoning",
            thinking=True,
            temperature=0.1,
        ),
        "coding": ChatProfile(
            name="coding",
            label="Coding",
            description="Programming help with explicit reasoning about code.",
            tier="chat",
            thinking=True,
            temperature=0.2,
        ),
        "planning": ChatProfile(
            name="planning",
            label="Planning",
            description="Multi-step plans and structured agendas.",
            tier="chat",
            thinking=False,
            temperature=0.4,
        ),
        "document": ChatProfile(
            name="document",
            label="Document",
            description="Grounded answers about uploaded or generated documents.",
            tier="chat",
            thinking=False,
            temperature=settings.DOCUMENT_AI_TEMPERATURE,
        ),
    }


def _apply_overrides(profile: ChatProfile, overrides: dict[str, dict]) -> ChatProfile:
    override = overrides.get(profile.name)
    if not override:
        return profile
    patch = {key: value for key, value in override.items() if key in _OVERRIDE_FIELDS}
    if not patch:
        return profile
    try:
        return replace(profile, **patch)
    except TypeError:
        return profile


def resolve_profile(name: str | None) -> ChatProfile:
    """Resolve a requested mode name to a profile. Unknown/empty/None -> general."""
    key = (name or "").strip().lower()
    profiles = _base_profiles()
    base = profiles.get(key, profiles["general"])
    return _apply_overrides(base, settings.LLM_PROFILE_OVERRIDES)


def list_profiles() -> list[ChatProfile]:
    """The public profiles, in a stable order, with overrides applied."""
    overrides = settings.LLM_PROFILE_OVERRIDES
    return [_apply_overrides(profile, overrides) for profile in _base_profiles().values()]
