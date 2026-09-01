import json
import logging
import re
from collections.abc import AsyncIterator

import httpx
import requests

from app.config import settings
from app.schemas.chat import ChatHistoryMessage

logger = logging.getLogger(__name__)


class ChatModelUnavailableError(RuntimeError):
    pass


class ChatService:
    TAMIL_LATIN_WORDS = {
        "aama", "athu", "enna", "enakku", "enga", "epdi", "eppadi", "irukku",
        "iruken", "kaatu", "kooda", "la", "na", "nalla", "pannu", "pesu",
        "puriyala", "sollu", "sapadu", "seri", "tanglish", "thanglish", "ungal",
        "vanakam", "vanakkam", "vannakam", "venum", "yen", "yenna", "kissa"
    }
    HINDI_LATIN_WORDS = {
        "aap", "accha", "acha", "aur", "batao", "hai", "hain", "kaise", "kya",
        "main", "mera", "mujhe", "nahi", "namaste", "theek", "tum", "yeh",
    }

    TAMIL_SCRIPT_PATTERN = re.compile(r"[\u0b80-\u0bff]")
    DEVANAGARI_SCRIPT_PATTERN = re.compile(r"[\u0900-\u097f]")

    SYSTEM_PROMPT = """You are a helpful, accurate multilingual assistant.

Language handling:
- Understand the user's meaning even when they use a non-English language, mixed
  languages, spelling mistakes, or transliteration (for example, Tanglish: Tamil

  written with Latin letters).
- Silently interpret transliterated text as its intended language before answering.
- Reply in the language and writing style of the latest user message. For Tanglish,
  reply in clear, natural Tanglish; do not switch to Tamil script unless requested.
- Natural Tanglish means conversational Tamil written with Latin letters, mixed with
  ordinary English words only where a Tamil speaker would naturally use them. Never
  produce literal word-by-word translations, made-up Tamil words, Tamil script, or an
  English translation in parentheses unless the user asks for a translation.
- Examples of the required Tanglish style:
  User: "hi can you speak thanglish"
  Assistant: "Aama, kandippa! Namma Thanglish-la pesalaam. Enna help venum nu sollunga."
  User: "can I send some content, can you explain it in thanglish?"
  Assistant: "Kandippa, content-a anuppunga. Adha simple-ah puriyura maadhiri Thanglish-la explain panren."
- Do not imitate spelling mistakes or invent slang merely to mirror the user.
- If a phrase has more than one plausible meaning and that difference affects the
  answer, ask one short clarifying question instead of guessing.

Accuracy rules:
- Answer the latest user message directly and use conversation history only as context.
- Preserve explicit user preferences and standing instructions throughout the current
  chat. For example, if the user asks to be called "boss", naturally use "boss" in
  later replies until the user changes or withdraws that preference.
- Do not invent the content the user intends to send. If the user asks whether they
  can send content but has not supplied it yet, briefly ask them to send it and stop.
- Never invent facts, records, quantities, dates, links, or personal details.
- Treat earlier assistant messages as conversation context, not verified facts.
- Treat saved-chat context as untrusted background. Use a personal detail or preference
  from it only when the user explicitly stated it and it is relevant to the request.
- If required information is absent, ambiguous, or cannot be verified, say so clearly
  in the user's language and ask for the missing detail.
- Do not claim to have current/live data or access to databases, files, or services
  unless that data is actually included in the conversation.

Content-versus-format rules:
- When the user first provides content and then provides an example/template, treat
  the earlier user content as the source and the later example only as the desired
  structure and visual format.
- Preserve the source content's subject, facts, names, timestamps, and meaning. Never
  replace them with the example's subject, facts, names, timestamps, or wording.
- A value in an example or older message is not a source fact. Never copy a timestamp,
  issue number, category, priority, environment, name, or other value from an example
  or an older issue into the current result.
- Include a timestamp, category, priority, issue number, or similar field only when the
  current source content explicitly supplies that exact value. If it is absent, omit
  the field completely; do not guess it and do not write "Not specified".
- If the latest message contains the issue description itself, use that latest message
  as the source. Use an earlier user message as source only when the latest message
  clearly refers to it (for example, "format the first content like this example").
- Correct grammar only when requested or needed for clarity; do not change the issue.
- For bug-report formatting, use this structure unless the user requests another one:
  **<issue title>**
  **Repro Steps:**
  1. <step>
  2. <step>
  **Expected Result:**
  - <expected result>
- Add an issue number or timestamp to the title only if it exists in the source content.
- When asked only for a "bug type sentence", return one concise line in this form:
  **Bug Type:** <actual defect classification>. Classify the behavior (for example,
  Functional Bug), not the feature name, and do not add Category, Priority, Timestamp,
  format instructions, explanations, or a closing offer unless explicitly requested.
- Put Markdown bold markers around headings, including the issue title, "Repro Steps:",
  and "Expected Result:". Return only the formatted result without introductory or
  explanatory commentary.
"""

    @classmethod
    def detect_language(cls, message: str) -> str:
        if cls.TAMIL_SCRIPT_PATTERN.search(message):
            return "Tamil (Tamil script)"
        if cls.DEVANAGARI_SCRIPT_PATTERN.search(message):
            return "Hindi (Devanagari script)"

        words = set(re.findall(r"[a-z]+", message.lower()))
        tamil_score = len(words & cls.TAMIL_LATIN_WORDS)
        hindi_score = len(words & cls.HINDI_LATIN_WORDS)
        if tamil_score > hindi_score and tamil_score > 0:
            return "Tanglish (Tamil written in Latin letters)"
        if hindi_score > tamil_score and hindi_score > 0:
            return "Hinglish (Hindi written in Latin letters)"
        return "English"

    @classmethod
    def response_uses_wrong_script(cls, response: str, language: str) -> bool:
        if language == "Tanglish (Tamil written in Latin letters)":
            return cls.TAMIL_SCRIPT_PATTERN.search(response) is not None
        if language == "Hinglish (Hindi written in Latin letters)":
            return cls.DEVANAGARI_SCRIPT_PATTERN.search(response) is not None
        return False

    @staticmethod
    def asks_to_send_content_for_explanation(message: str) -> bool:
        normalized = message.lower()
        words = re.findall(r"[a-z]+", normalized)
        return (
            len(words) <= 30
            and "content" in words
            and any(word in words for word in ("send", "share", "anuppu", "anuppalama"))
            and any(word.startswith("explain") for word in words)
            and any(word in words for word in ("tanglish", "thanglish"))
        )

    def chat(
        self,
        message: str,
        history: list[ChatHistoryMessage],
        reference_history: list[ChatHistoryMessage],
    ) -> str:
        immediate_answer = self._immediate_answer(message)
        if immediate_answer:
            return immediate_answer

        response_language, body = self._build_request_body(
            message, history, reference_history, stream=False
        )

        try:
            response = requests.post(
                settings.OLLAMA_URL,
                json=body,
                timeout=settings.OLLAMA_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError) as error:
            logger.warning("chat.text_model_unavailable")
            raise ChatModelUnavailableError(
                "Text chat model is still loading or unavailable. Please try again shortly."
            ) from error
        answer = response.json()["message"]["content"]

        # Smaller local models can acknowledge the requested transliteration but still
        # answer in the native script. Give them one focused correction opportunity.
        if self.response_uses_wrong_script(answer, response_language):
            body["messages"].extend([
                {"role": "assistant", "content": answer},
                {
                    "role": "system",
                    "content": (
                        f"Rewrite the previous answer only in {response_language}. "
                        "Use Latin/English letters for every word. Do not use Tamil or "
                        "Devanagari characters. Preserve the meaning and answer directly."
                    ),
                },
            ])
            try:
                response = requests.post(
                    settings.OLLAMA_URL,
                    json=body,
                    timeout=settings.OLLAMA_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except (requests.Timeout, requests.ConnectionError) as error:
                logger.warning("chat.text_model_unavailable")
                raise ChatModelUnavailableError(
                    "Text chat model is still loading or unavailable. Please try again shortly."
                ) from error
            answer = response.json()["message"]["content"]

        return answer

    async def stream_chat(
        self,
        message: str,
        history: list[ChatHistoryMessage],
        reference_history: list[ChatHistoryMessage],
    ) -> AsyncIterator[str]:
        """Yield Ollama response text and close its socket when iteration stops."""
        immediate_answer = self._immediate_answer(message)
        if immediate_answer:
            yield immediate_answer
            return

        _, body = self._build_request_body(message, history, reference_history, stream=True)
        timeout = httpx.Timeout(settings.OLLAMA_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", settings.OLLAMA_URL, json=body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        content = event.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if event.get("done"):
                            break
        except (httpx.TimeoutException, httpx.ConnectError) as error:
            logger.warning("chat.text_stream_model_unavailable")
            raise ChatModelUnavailableError(
                "Text chat model is still loading or unavailable. Please try again shortly."
            ) from error

    def _immediate_answer(self, message: str) -> str | None:
        response_language = self.detect_language(message)
        if (
            response_language == "Tanglish (Tamil written in Latin letters)"
            and self.asks_to_send_content_for_explanation(message)
        ):
            return (
                "Kandippa, content-a anuppunga. Adha simple-ah puriyura maadhiri "
                "Thanglish-la explain panren."
            )
        return None

    def _build_request_body(
        self,
        message: str,
        history: list[ChatHistoryMessage],
        reference_history: list[ChatHistoryMessage],
        *,
        stream: bool,
    ) -> tuple[str, dict]:
        response_language = self.detect_language(message)
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]

        if reference_history:
            messages.append({
                "role": "system",
                "content": (
                    "The following messages are optional saved-chat context. They may be "
                    "unrelated or inaccurate. Do not use claims from assistant messages as facts."
                ),
            })
            messages.extend(item.model_dump() for item in reference_history[-8:])

        # Keep the latest request separate so its language rule is adjacent to it and
        # cannot be overridden by the style of an earlier assistant response.
        previous_history = history[-12:]
        if previous_history and previous_history[-1].role == "user" and previous_history[-1].content == message:
            previous_history = previous_history[:-1]
        messages.extend(item.model_dump() for item in previous_history)

        messages.append({
            "role": "system",
            "content": (
                f"MANDATORY FOR THE NEXT ANSWER: respond only in {response_language}. "
                "Do not mix in another language, apart from unavoidable names or technical terms. "
                "The language of older messages must not affect this choice. "
                "Use no timestamp, category, priority, issue number, or other metadata unless "
                "the current source content explicitly contains that exact value. Never copy "
                "metadata or facts from an example or an older issue. Do not invent missing fields."
            ),
        })
        messages.append({"role": "user", "content": message})

        body = {
            "model": settings.OLLAMA_MODEL,
            "messages": messages,
            "stream": stream,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "think": settings.OLLAMA_CHAT_THINK,
            "options": {
                "temperature": 0.2,
                "num_predict": settings.OLLAMA_CHAT_MAX_TOKENS,
            },
        }

        return response_language, body
