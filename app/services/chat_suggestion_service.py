import asyncio
import json
from collections import OrderedDict
from time import monotonic

from pydantic import ValidationError

from app.schemas.chat_suggestions import FollowUpSuggestion
from app.services.chat_service import ChatModelUnavailableError, ChatService


class ChatSuggestionService:
    """Optional follow-up ideas; their generation never delays the actual answer."""

    def __init__(self):
        self.model = ChatService()
        self.cache: OrderedDict[tuple[int, int, str], tuple[float, list[FollowUpSuggestion]]] = (
            OrderedDict()
        )

    async def suggest(
        self, *, user_id: int, message_id: int, answer_hash: str, question: str, answer: str
    ) -> list[FollowUpSuggestion]:
        key = (user_id, message_id, answer_hash)
        cached = self.cache.get(key)
        if cached and monotonic() - cached[0] < 1800:
            self.cache.move_to_end(key)
            return cached[1]
        suggestions = self.fallback()
        try:
            async with asyncio.timeout(12):
                raw = await self.model.complete_follow_up_suggestions(question, answer)
            parsed = self.parse(raw)
            if parsed:
                suggestions = parsed
        except (TimeoutError, ChatModelUnavailableError, ValueError, TypeError, ValidationError):
            pass  # Optional UI guidance must not replace a successful answer with an error.
        self.cache[key] = (monotonic(), suggestions)
        self.cache.move_to_end(key)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        return suggestions

    @staticmethod
    def fallback() -> list[FollowUpSuggestion]:
        return [
            FollowUpSuggestion(
                label="Explain with an example",
                prompt="You could explore a practical example to see how this works in a real situation.",
            ),
            FollowUpSuggestion(
                label="Explore another approach",
                prompt="Another useful direction is to compare alternative approaches and their trade-offs.",
            ),
        ]

    @staticmethod
    def parse(raw: str) -> list[FollowUpSuggestion]:
        text = raw.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        payload = json.loads(text)
        rows = payload.get("suggestions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        result = []
        labels: set[str] = set()
        prompts: set[str] = set()
        for row in rows[:6]:
            try:
                item = FollowUpSuggestion.model_validate(row)
            except ValidationError:
                continue
            label, prompt = item.label.casefold(), item.prompt.casefold()
            if label in labels or prompt in prompts or label == "other":
                continue
            if any(ord(char) < 32 for char in item.label + item.prompt):
                continue
            labels.add(label)
            prompts.add(prompt)
            result.append(item)
            if len(result) == 2:
                break
        return result
