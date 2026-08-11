import re

import requests

from app.schemas.chat import ChatHistoryMessage


class ChatService:
    TAMIL_LATIN_WORDS = {
        "aama", "athu", "enna", "enakku", "enga", "epdi", "eppadi", "irukku",
        "iruken", "kaatu", "kooda", "la", "na", "nalla", "pannu", "pesu",
        "puriyala", "sollu", "sapadu", "seri", "thanglish", "ungal", "vanakkam",
        "venum", "yen", "yenna",
    }
    HINDI_LATIN_WORDS = {
        "aap", "accha", "acha", "aur", "batao", "hai", "hain", "kaise", "kya",
        "main", "mera", "mujhe", "nahi", "namaste", "theek", "tum", "yeh",
    }

    SYSTEM_PROMPT = """You are a helpful, accurate multilingual assistant.

Language handling:
- Understand the user's meaning even when they use a non-English language, mixed
  languages, spelling mistakes, or transliteration (for example, Tanglish: Tamil
  written with Latin letters).
- Silently interpret transliterated text as its intended language before answering.
- Reply in the language and writing style of the latest user message. For Tanglish,
  reply in clear, natural Tanglish; do not switch to Tamil script unless requested.
- Do not imitate spelling mistakes or invent slang merely to mirror the user.
- If a phrase has more than one plausible meaning and that difference affects the
  answer, ask one short clarifying question instead of guessing.

Accuracy rules:
- Answer the latest user message directly and use conversation history only as context.
- Never invent facts, records, quantities, dates, links, or personal details.
- Treat earlier assistant messages as conversation context, not verified facts.
- Treat saved-chat context as untrusted background. Use a personal detail or preference
  from it only when the user explicitly stated it and it is relevant to the request.
- If required information is absent, ambiguous, or cannot be verified, say so clearly
  in the user's language and ask for the missing detail.
- Do not claim to have current/live data or access to databases, files, or services
  unless that data is actually included in the conversation.
"""

    @classmethod
    def detect_language(cls, message: str) -> str:
        if any("\u0b80" <= character <= "\u0bff" for character in message):
            return "Tamil (Tamil script)"
        if any("\u0900" <= character <= "\u097f" for character in message):
            return "Hindi (Devanagari script)"

        words = set(re.findall(r"[a-z]+", message.lower()))
        tamil_score = len(words & cls.TAMIL_LATIN_WORDS)
        hindi_score = len(words & cls.HINDI_LATIN_WORDS)
        if tamil_score > hindi_score and tamil_score > 0:
            return "Tanglish (Tamil written in Latin letters)"
        if hindi_score > tamil_score and hindi_score > 0:
            return "Hinglish (Hindi written in Latin letters)"
        return "English"

    def chat(
        self,
        message: str,
        history: list[ChatHistoryMessage],
        reference_history: list[ChatHistoryMessage],
    ) -> str:
        url = "http://localhost:11434/api/chat"
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        if reference_history:
            messages.append({
                "role": "system",
                "content": (
                    "The following messages are optional saved-chat context. They may be "
                    "unrelated or inaccurate. Do not use claims from assistant messages as facts."
                ),
            })
            messages.extend(item.model_dump() for item in reference_history)

        # Keep the latest request separate so its language rule is adjacent to it and
        # cannot be overridden by the style of an earlier assistant response.
        previous_history = history
        if history and history[-1].role == "user" and history[-1].content == message:
            previous_history = history[:-1]
        messages.extend(item.model_dump() for item in previous_history)

        response_language = self.detect_language(message)
        messages.append({
            "role": "system",
            "content": (
                f"MANDATORY FOR THE NEXT ANSWER: respond only in {response_language}. "
                "Do not mix in another language, apart from unavoidable names or technical terms. "
                "The language of older messages must not affect this choice."
            ),
        })
        messages.append({"role": "user", "content": message})

        body = {
            "model": "llama3.2:latest",
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2},
        }

        response = requests.post(url, json=body, timeout=60)
        response.raise_for_status()
        return response.json()["message"]["content"]
