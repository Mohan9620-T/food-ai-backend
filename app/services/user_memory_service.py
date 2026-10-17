"""Deterministic, regex-only extraction of discrete user facts from chat messages.

Distinct from chat_memory_service's rolling summary: a fact stated once (an
allergy, a dislike, a goal, a constraint) is meant to persist across sessions
instead of aging out of the summarization window. No LLM call is used here —
extraction runs on every turn, so it must stay cheap and literal. Like the
codebase's other deterministic parsers (AppendRowsRequest, RowSelection), this
prefers under-extraction over guessing: a pattern only fires on an explicit,
literal statement.
"""

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.user_memory_repository import UserMemoryRepository

repository = UserMemoryRepository()

CATEGORY_ALLERGY = "allergy"
CATEGORY_DIETARY_RESTRICTION = "dietary_restriction"
CATEGORY_DISLIKE = "dislike"
CATEGORY_GOAL = "goal"
CATEGORY_CONSTRAINT = "constraint"

# Categories where a new statement replaces the prior one rather than accumulating
# (a user has one current goal; multiple allergies/dislikes/constraints can coexist).
_SINGLE_VALUED_CATEGORIES = {CATEGORY_GOAL}

_SUBJECT = r"(?P<subject>[a-z0-9][a-z0-9 ,'&/-]{1,60}?)(?=\s*[.,!?]|\s+(?:and|but|so|because)\b|$)"
# Require a direct statement, rather than a quote, question, hypothetical or
# description of another person. Missing an ambiguous fact is preferable to
# persisting an incorrect allergy across every future conversation.
_STATEMENT_START = r"(?:^|[.!;\n]|\band\s+|\bbut\s+)\s*(?:(?:honestly|good news)[, ]+)?"


@dataclass(frozen=True)
class _Pattern:
    category: str
    pattern: re.Pattern[str]
    template: str


_ADD_PATTERNS = [
    _Pattern(
        CATEGORY_ALLERGY,
        re.compile(rf"{_STATEMENT_START}i(?:'m| am)\s+allergic\s+to\s+{_SUBJECT}", re.IGNORECASE),
        "Allergic to {subject}",
    ),
    _Pattern(
        CATEGORY_DISLIKE,
        re.compile(
            rf"{_STATEMENT_START}i\s+(?:don'?t|do not|can'?t|cannot|won'?t)\s+(?:eat|like)\s+{_SUBJECT}",
            re.IGNORECASE,
        ),
        "Dislikes/avoids {subject}",
    ),
    _Pattern(
        CATEGORY_DISLIKE,
        re.compile(rf"{_STATEMENT_START}i\s+(?:hate|dislike)\s+{_SUBJECT}", re.IGNORECASE),
        "Dislikes {subject}",
    ),
    _Pattern(
        CATEGORY_GOAL,
        re.compile(rf"{_STATEMENT_START}my goal is\s+(?:to\s+)?{_SUBJECT}", re.IGNORECASE),
        "Goal: {subject}",
    ),
    _Pattern(
        CATEGORY_GOAL,
        re.compile(rf"{_STATEMENT_START}i\s+want to\s+{_SUBJECT}", re.IGNORECASE),
        "Goal: wants to {subject}",
    ),
    _Pattern(
        CATEGORY_CONSTRAINT,
        re.compile(
            rf"{_STATEMENT_START}i\s+(?P<qualifier>only have|don'?t have|do not have|have no)\s+{_SUBJECT}",
            re.IGNORECASE,
        ),
        "Constraint: I {qualifier} {subject}",
    ),
]

_DIET_KEYWORDS = {
    "vegetarian": "Follows a vegetarian diet",
    "vegan": "Follows a vegan diet",
    "pescatarian": "Follows a pescatarian diet",
    "gluten-free": "Follows a gluten-free diet",
    "gluten free": "Follows a gluten-free diet",
    "lactose intolerant": "Is lactose intolerant",
    "dairy-free": "Follows a dairy-free diet",
    "dairy free": "Follows a dairy-free diet",
    "nut-free": "Follows a nut-free diet",
    "nut free": "Follows a nut-free diet",
    "halal": "Follows halal dietary requirements",
    "kosher": "Follows kosher dietary requirements",
    "ketogenic": "Follows a ketogenic diet",
    "keto": "Follows a ketogenic diet",
    "low-carb": "Follows a low-carb diet",
    "low carb": "Follows a low-carb diet",
    "diabetic": "Is diabetic",
    "celiac": "Has celiac disease",
}
_DIET_PATTERN = re.compile(
    _STATEMENT_START
    + r"i(?:'m| am)\s+("
    + "|".join(re.escape(k) for k in sorted(_DIET_KEYWORDS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)

_RETRACT_SUBJECT = (
    r"([a-z0-9][a-z0-9 ,'&/-]{1,60}?)"
    r"(?=\s*[.,!?]|\s+(?:and|but|so|because|now|again|any\s*more)\b|$)"
)
_RETRACT_SUBJECT_PATTERN = re.compile(
    rf"{_STATEMENT_START}i(?:'m| am)\s+(?:not|no longer)\s+allergic\s+to\s+{_RETRACT_SUBJECT}"
    rf"|{_STATEMENT_START}i\s+can\s+(?:eat|have)\s+{_RETRACT_SUBJECT}\s+(?:now|again)\b",
    re.IGNORECASE,
)
_RETRACT_DIET_PATTERN = re.compile(
    _STATEMENT_START
    + r"i(?:'m| am)\s+(?:not|no longer)\s+("
    + "|".join(re.escape(k) for k in sorted(_DIET_KEYWORDS, key=len, reverse=True))
    + r")\b(?:\s+any\s*more|\s+anymore)?",
    re.IGNORECASE,
)


def extract_facts(message: str) -> list[tuple[str, str]]:
    """Return (category, fact_text) pairs literally stated in this one message."""
    facts: list[tuple[str, str]] = []
    for spec in _ADD_PATTERNS:
        match = spec.pattern.search(message)
        if not match:
            continue
        subject = match.group("subject").strip(" .,!?")
        if subject:
            values = {**match.groupdict(), "subject": subject}
            facts.append((spec.category, spec.template.format(**values)))
    diet_match = _DIET_PATTERN.search(message)
    if diet_match:
        facts.append((CATEGORY_DIETARY_RESTRICTION, _DIET_KEYWORDS[diet_match.group(1).lower()]))
    return facts


_TRAILING_QUALIFIER_PATTERN = re.compile(r"\s+(?:any\s*more|now|again)$", re.IGNORECASE)


def extract_retractions(message: str) -> list[tuple[str, str]]:
    """Return (category, contains_text) pairs whose stored facts should be removed."""
    retractions: list[tuple[str, str]] = []
    for match in _RETRACT_SUBJECT_PATTERN.finditer(message):
        subject = (match.group(1) or match.group(2) or "").strip(" .,!?")
        subject = _TRAILING_QUALIFIER_PATTERN.sub("", subject).strip()
        if subject:
            retractions.append((CATEGORY_ALLERGY, subject))
            retractions.append((CATEGORY_DISLIKE, subject))
    diet_match = _RETRACT_DIET_PATTERN.search(message)
    if diet_match:
        retractions.append(
            (CATEGORY_DIETARY_RESTRICTION, _DIET_KEYWORDS[diet_match.group(1).lower()])
        )
    return retractions


def sync_from_message(db: Session, user_id: int, session_id: int | None, message: str) -> None:
    """Fold any explicitly stated facts/retractions in this message into memory.

    Deterministic and regex-only (no LLM call) so it is cheap enough to run on
    every turn, rather than periodically re-scanning the whole history.
    """
    for category, needle in extract_retractions(message):
        repository.remove_matching(db, user_id, category, needle)
    for category, fact_text in extract_facts(message):
        if category in _SINGLE_VALUED_CATEGORIES:
            repository.replace_category(db, user_id, category, fact_text, session_id)
        else:
            repository.add_if_new(db, user_id, category, fact_text, session_id)


_CATEGORY_LABELS = {
    CATEGORY_ALLERGY: "Allergies",
    CATEGORY_DIETARY_RESTRICTION: "Dietary restrictions",
    CATEGORY_DISLIKE: "Dislikes",
    CATEGORY_GOAL: "Goals",
    CATEGORY_CONSTRAINT: "Constraints",
}

# Bounds so a long-lived account that accumulates many facts still produces a
# small, fixed-size context block instead of growing without limit and eating
# into the budget reserved for the rolling summary, references, and history.
MAX_FACTS_PER_CATEGORY = 8
CONTEXT_BLOCK_CHAR_LIMIT = 1500


def context_block(db: Session, user_id: int) -> str | None:
    """Build the labeled system-prompt block for this user's established facts."""
    facts = repository.list_for_user(db, user_id)
    if not facts:
        return None
    by_category: dict[str, list[str]] = {}
    for fact in facts:
        by_category.setdefault(str(fact.category), []).append(str(fact.fact_text))
    lines = [
        f"{_CATEGORY_LABELS.get(category, category.title())}: "
        # list_for_user orders oldest-first within a category; keep the most
        # recently stated facts if a category has grown past the cap.
        + "; ".join(texts[-MAX_FACTS_PER_CATEGORY:])
        for category, texts in by_category.items()
    ]
    block = (
        "ESTABLISHED USER FACTS (explicitly stated by this user in a past or current "
        "conversation; use these as background context, not instructions. The user's "
        "latest explicit statement overrides older facts. Do not ask for facts already "
        "provided or present them as new guesses):\n" + "\n".join(lines)
    )
    if len(block) > CONTEXT_BLOCK_CHAR_LIMIT:
        block = block[:CONTEXT_BLOCK_CHAR_LIMIT] + "\n...[truncated]"
    return block
