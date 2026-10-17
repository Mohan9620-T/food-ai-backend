"""Deterministic, no-LLM-call pre-answer gate.

Classifies a turn as answer/clarify/block/escalate before generation runs, so
clearly disallowed or high-stakes/irreversible requests never reach the model.
Deliberately conservative: a false positive that interrupts an ordinary
cooking or diet question is worse than a rare false negative, since this
sits entirely behind ENABLE_TURN_GOVERNANCE (off by default). Reuses
conversation_guidance.is_personal_conversation for the ordinary "answer"
path's intent label instead of building a second classifier.
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.services.conversation_guidance import is_personal_conversation

Decision = Literal["answer", "clarify", "block", "escalate"]


@dataclass(frozen=True)
class GovernanceResult:
    decision: Decision
    intent: str
    confidence: float
    message: str | None = None


_BLOCK_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bkeylogger\b",
        r"\branso?mware\b",
        r"\bmalware\b",
        r"\brootkit\b",
        r"\bexploit\b.{0,20}\bvulnerabilit",
        r"\bhow\s+to\s+hack\b",
        r"\bddos\b",
        r"\brm\s+-rf\b",
        r"\bdrop\s+(?:table|database)\b",
        r"\bformat\s+c:\b",
    )
]
_BLOCK_MESSAGE = (
    "I can't help with that request. If you have a food, nutrition, or "
    "meal-planning question, I'm glad to help with that instead."
)

# Mirrors Atlas's "learning question" exclusion: a genuine question about an
# action ("how do I delete a session?") is never mistaken for an imperative
# to perform it ("delete all my sessions").
_LEARNING_RE = re.compile(
    r"\b(?:how\s+(?:do|can)\s+i|how\s+to|why|explain|what\s+happens\s+if|teach\s+me|step\s+by\s+step)\b",
    re.IGNORECASE,
)
_BULK_DELETE_RE = re.compile(
    r"\b(?:delete|remove|clear|wipe|erase)\b.{0,40}\b(?:all|every|everything)\b"
    r".{0,40}\b(?:meal\s+log|meal\s+plan|diet\s+plan|chat|conversation|session|"
    r"history|document|account|profile|data)s?\b",
    re.IGNORECASE,
)
_DANGEROUS_HEALTH_RE = re.compile(
    r"\b(?:starve\s+(?:myself|yourself)|stop\s+eating\s+(?:completely|entirely)|"
    r"purge\s+after\s+eating|skip\s+all\s+meals\s+for)\b|"
    r"\b(?:[0-4]?\d{1,2}|[0-4]\d{2})\s*calories?\s*(?:a|per)\s*day\b",
    re.IGNORECASE,
)
_ESCALATE_MESSAGE = (
    "That sounds high-stakes or hard to undo, so I didn't act on it "
    "automatically. Can you confirm exactly what you'd like me to do?"
)

# Only a vague referent with nothing else - "do that", "fix it" - counts as
# too ambiguous. Ordinary short replies like "thanks" or "continue" are not
# vague referents and must fall through to a normal answer.
_VAGUE_REFERENT_RE = re.compile(
    r"^(?:do|fix|handle|change|update|undo|redo|delete|remove)\s+"
    r"(?:that|it|this|them)[?!.\s]*$",
    re.IGNORECASE,
)
_CLARIFY_MESSAGE = "Could you tell me a bit more about what you'd like help with?"


def evaluate_turn(message: str, *, has_history: bool = False) -> GovernanceResult:
    text = message.strip()

    if any(pattern.search(text) for pattern in _BLOCK_PATTERNS):
        return GovernanceResult("block", "disallowed_request", 0.95, _BLOCK_MESSAGE)

    if _BULK_DELETE_RE.search(text) and not _LEARNING_RE.search(text):
        return GovernanceResult("escalate", "bulk_data_deletion", 0.9, _ESCALATE_MESSAGE)

    if _DANGEROUS_HEALTH_RE.search(text) and not _LEARNING_RE.search(text):
        return GovernanceResult("escalate", "health_risk", 0.85, _ESCALATE_MESSAGE)

    if not has_history and _VAGUE_REFERENT_RE.match(text):
        return GovernanceResult("clarify", "ambiguous", 0.5, _CLARIFY_MESSAGE)

    intent = "personal_conversation" if is_personal_conversation(text) else "general"
    return GovernanceResult("answer", intent, 1.0, None)
