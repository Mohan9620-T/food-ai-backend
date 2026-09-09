import json
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import aclosing

import httpx
import requests

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
from app.services.conversation_guidance import CONVERSATION_GUIDANCE

logger = logging.getLogger(__name__)


class ChatModelUnavailableError(RuntimeError):
    pass


class _NvidiaFallbackError(RuntimeError):
    """Internal signal that NVIDIA could not produce a usable response."""


class ChatService:
    HISTORY_MESSAGE_LIMIT = 24
    REFERENCE_MESSAGE_LIMIT = 4
    CONTEXT_MESSAGE_CHAR_LIMIT = 2000
    STANDING_PREFERENCE_PATTERN = re.compile(
        r"\b(?:call|address|refer to)\s+(?:me\s+)?(?:as\s+)?[a-z0-9_-]+"
        r"|\bmy name is\b|\bi (?:prefer|always (?:prefer|like|want))\b"
        r"|\bremember (?:that|to)\b",
        re.IGNORECASE,
    )
    TAMIL_LATIN_WORDS = {
        "aama",
        "athu",
        "enna",
        "enakku",
        "enga",
        "epdi",
        "eppadi",
        "irukku",
        "iruken",
        "kaatu",
        "kooda",
        "la",
        "na",
        "nalla",
        "pannu",
        "pesu",
        "kandippa",
        "pathi",
        "pesalaam",
        "puriyala",
        "sapadu",
        "sari",
        "seri",
        "sollu",
        "sollunga",
        "solren",
        "tanglish",
        "thanglish",
        "ungal",
        "vanakam",
        "vanakkam",
        "vannakam",
        "venum",
        "yen",
        "yenna",
        "kissa",
    }
    HINDI_LATIN_WORDS = {
        "aap",
        "accha",
        "acha",
        "aur",
        "batao",
        "hai",
        "hain",
        "kaise",
        "kya",
        "main",
        "mera",
        "mujhe",
        "nahi",
        "namaste",
        "theek",
        "tum",
        "yeh",
    }

    TAMIL_SCRIPT_PATTERN = re.compile(r"[\u0b80-\u0bff]")
    DEVANAGARI_SCRIPT_PATTERN = re.compile(r"[\u0900-\u097f]")

    SYSTEM_PROMPT = (
        """You are a helpful, accurate multilingual assistant.

Language handling:
- Understand the user's meaning even when they use a non-English language, mixed
  languages, spelling mistakes, or transliteration (for example, Tanglish: Tamil

  written with Latin letters).
- Silently interpret transliterated text as its intended language before answering.
- Start every new conversation in English. Do not change the response language merely
  because the user writes a greeting, non-English text, mixed language, or transliteration.
- Change the response language only when the user explicitly asks you to speak, reply,
  answer, continue, or switch to that language. Keep that choice until the user explicitly
  requests another language.
- Do not imitate spelling mistakes or invent slang merely to mirror the user.
- If a phrase has more than one plausible meaning and that difference affects the
  answer, ask one short clarifying question instead of guessing.

Accuracy rules:
- Answer the latest user message directly and use conversation history only as context.
- Keep ordinary answers concise (normally under 150 words). Give a longer response only
  when the user explicitly requests detail, steps, a list, code, or a full explanation.
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

Response presentation:
- Use clean Markdown when it improves readability. Use short headings, bullet points,
  and **bold text** for important labels or conclusions in structured answers.
- Add one or two relevant emojis to friendly, motivational, comparison, status, or
  celebratory answers. Do not add emojis to every sentence, and avoid them when the
  user requests plain text, code, JSON, or another strict format.
- End ordinary conversational answers with one short, relevant next-step suggestion or
  question that helps the user continue (for example, offer more detail, steps, or a
  different format). Do not use the same generic suggestion every time. Omit this closing
  suggestion when it would be repetitive, intrusive, or insensitive, or when the user requests code, JSON, plain text, a specific format, or asks for
  only the answer with no additional commentary.

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
  explanatory commentary for this bug-report formatting task only.
"""
        + "\n"
        + CONVERSATION_GUIDANCE
    )

    TANGLISH_STYLE_PROMPT = """The user explicitly selected Tanglish for this chat.
Reply in natural conversational Tamil written with Latin letters, mixing ordinary English
words only where a Tamil speaker naturally would. Never produce a literal word-by-word
translation, made-up Tamil words, Tamil script, or an English translation in parentheses
unless requested. Example style: "Kandippa, content-a anuppunga. Adha simple-ah puriyura
maadhiri Thanglish-la explain panren."""

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
    def requested_language(cls, message: str) -> str | None:
        normalized = " ".join(re.findall(r"[a-z]+", message.lower()))
        request_words = (
            r"(?:speak|reply|respond|answer|continue|change|switch|use|explain|talk|write)"
        )
        language_patterns = (
            ("English", r"english"),
            ("Tanglish (Tamil written in Latin letters)", r"t(?:h)?anglish"),
            ("Hinglish (Hindi written in Latin letters)", r"hinglish"),
            ("Tamil (Tamil script)", r"tamil"),
            ("Hindi (Devanagari script)", r"hindi"),
        )
        matches: list[tuple[int, str]] = []
        for language, pattern in language_patterns:
            expressions = (
                rf"{request_words}\b[^.?!]{{0,40}}\b(?:only\s+)?{pattern}\b",
                rf"\b(?:only\s+|in\s+){pattern}\b",
                rf"\b{pattern}\s+la\b",
                rf"\b{pattern}\b[^.?!]{{0,30}}\b{request_words}\b",
            )
            positions = [
                match.start()
                for expression in expressions
                for match in re.finditer(expression, normalized)
            ]
            if positions:
                matches.append((max(positions), language))
        return max(matches)[1] if matches else None

    @classmethod
    def response_language(
        cls,
        message: str,
        history: list[ChatHistoryMessage],
    ) -> str:
        language = "English"
        previous_history = history
        if history and history[-1].role == "user" and history[-1].content == message:
            previous_history = history[:-1]
        for item in previous_history:
            if item.role != "user":
                continue
            requested = cls.requested_language(item.content)
            if requested:
                language = requested
        return cls.requested_language(message) or language

    @classmethod
    def response_uses_wrong_language(cls, response: str, language: str) -> bool:
        if language == "English":
            return cls.detect_language(response) != "English"
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

        provider = "ollama"
        if self._use_nvidia_primary():
            try:
                answer = self._chat_with_nvidia(body)
                provider = "nvidia"
            except _NvidiaFallbackError:
                logger.warning("chat.text_nvidia_fallback_to_ollama")
                answer = self._chat_with_ollama(body)
        else:
            answer = self._chat_with_ollama(body)

        # Smaller local models can acknowledge the requested transliteration but still
        # answer in the native script. Give them one focused correction opportunity.
        if self.response_uses_wrong_language(answer, response_language):
            rewrite_instruction = (
                "Rewrite the previous answer only in English. Do not use Tamil, Hindi, "
                "Tanglish, Hinglish, or transliterated non-English words. Preserve the "
                "meaning and answer directly."
                if response_language == "English"
                else (
                    f"Rewrite the previous answer only in {response_language}. "
                    "Use Latin/English letters for every word. Do not use Tamil or "
                    "Devanagari characters. Preserve the meaning and answer directly."
                )
            )
            body["messages"].extend(
                [
                    {"role": "assistant", "content": answer},
                    {"role": "system", "content": rewrite_instruction},
                ]
            )
            # Wrong language is not provider failure: correct with the provider that
            # produced the original answer and never cross over to the fallback.
            answer = (
                self._chat_with_nvidia(body, allow_fallback=False)
                if provider == "nvidia"
                else self._chat_with_ollama(body)
            )

        return answer

    async def stream_chat(
        self,
        message: str,
        history: list[ChatHistoryMessage],
        reference_history: list[ChatHistoryMessage],
    ) -> AsyncGenerator[str, None]:
        """Yield one provider stream without mixing partial answers."""
        immediate_answer = self._immediate_answer(message)
        if immediate_answer:
            yield immediate_answer
            return

        _, body = self._build_request_body(message, history, reference_history, stream=True)
        if not self._use_nvidia_primary():
            async with aclosing(self._stream_ollama(body)) as stream:
                async for chunk in stream:
                    yield chunk
            return

        async with aclosing(self._stream_nvidia(body)) as nvidia_stream:
            try:
                first_chunk = await anext(nvidia_stream)
            except (StopAsyncIteration, _NvidiaFallbackError):
                logger.warning("chat.text_stream_nvidia_fallback_to_ollama")
                async with aclosing(self._stream_ollama(body)) as stream:
                    async for chunk in stream:
                        yield chunk
                return

            # Only after the first usable chunk is buffered is NVIDIA content exposed.
            yield first_chunk
            try:
                async for chunk in nvidia_stream:
                    yield chunk
            except _NvidiaFallbackError as error:
                raise ChatModelUnavailableError(
                    "The response stream was interrupted. Please try again."
                ) from error

    @staticmethod
    def _use_nvidia_primary() -> bool:
        return settings.APP_ENVIRONMENT == "production" or settings.LLM_PROVIDER == "nvidia"

    def _chat_with_ollama(self, body: dict) -> str:
        try:
            response = requests.post(
                settings.OLLAMA_URL,
                json=body,
                timeout=(
                    settings.OLLAMA_CONNECT_TIMEOUT_SECONDS,
                    settings.OLLAMA_TIMEOUT_SECONDS,
                ),
            )
            response.raise_for_status()
            answer = response.json()["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("missing Ollama response content")
            return answer
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            logger.warning("chat.text_model_unavailable", extra={"provider": "ollama"})
            raise ChatModelUnavailableError(
                "Text chat model is still loading or unavailable. Please try again shortly."
            ) from error

    def _chat_with_nvidia(self, body: dict, *, allow_fallback: bool = True) -> str:
        try:
            if not settings.NVIDIA_API_KEY:
                raise _NvidiaFallbackError("NVIDIA is not configured")
            response = requests.post(
                f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=self._nvidia_body(body, stream=False),
                timeout=(
                    settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS,
                    settings.NVIDIA_CHAT_TIMEOUT_SECONDS,
                ),
                proxies={"http": "", "https": ""},
            )
            if response.status_code >= 400:
                if response.status_code in {401, 403, 408, 429, 500, 502, 503, 504}:
                    raise _NvidiaFallbackError("NVIDIA provider request failed")
                response.raise_for_status()
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ChatModelUnavailableError(
                    "The response reached its output limit before it finished. "
                    "Please retry with a shorter request."
                )
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise _NvidiaFallbackError("missing NVIDIA response content")
            return answer
        except _NvidiaFallbackError as error:
            if allow_fallback:
                raise
            raise ChatModelUnavailableError(
                "The NVIDIA text provider could not correct the response."
            ) from error
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
            if allow_fallback:
                raise _NvidiaFallbackError("unusable NVIDIA response") from error
            raise ChatModelUnavailableError(
                "The NVIDIA text provider could not correct the response."
            ) from error

    @staticmethod
    def _nvidia_body(body: dict, *, stream: bool) -> dict:
        request_body = {
            "model": settings.NVIDIA_CHAT_MODEL,
            "messages": body["messages"],
            "stream": stream,
            "temperature": body["options"]["temperature"],
            "max_tokens": settings.NVIDIA_CHAT_MAX_TOKENS,
        }
        if settings.NVIDIA_CHAT_MODEL == "google/gemma-4-31b-it":
            request_body.update(
                {
                    "chat_template_kwargs": {"enable_thinking": False},
                    "temperature": 1.0,
                    "top_p": 0.95,
                    "top_k": 64,
                }
            )
        elif settings.NVIDIA_CHAT_MODEL.startswith("nvidia/nemotron-3-"):
            request_body.update(
                {
                    "chat_template_kwargs": {"enable_thinking": False},
                    "temperature": 1.0,
                    "top_p": 0.95,
                }
            )
        return request_body

    async def _stream_nvidia(self, body: dict) -> AsyncGenerator[str, None]:
        if not settings.NVIDIA_API_KEY:
            logger.warning(
                "chat.text_stream_model_unavailable",
                extra={"provider": "nvidia", "reason": "missing_api_key"},
            )
            raise _NvidiaFallbackError("NVIDIA is not configured")
        timeout = httpx.Timeout(
            connect=settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS,
            read=settings.NVIDIA_CHAT_TIMEOUT_SECONDS,
            write=30,
            pool=settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                async with client.stream(
                    "POST",
                    f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    json=self._nvidia_body(body, stream=True),
                ) as response:
                    if response.status_code >= 400:
                        response.raise_for_status()
                    finished = False
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            finished = True
                            break
                        event = json.loads(data)
                        # Usage-only events have no choices; reasoning is deliberately
                        # excluded from the visible answer and saved history.
                        choices = event.get("choices")
                        if choices == []:
                            continue
                        choice = choices[0]
                        content = choice["delta"].get("content")
                        if content is not None and not isinstance(content, str):
                            raise ValueError("Invalid NVIDIA content")
                        if content:
                            yield content
                        if choice.get("finish_reason") == "length":
                            raise ChatModelUnavailableError(
                                "The response reached its output limit before it finished. "
                                "Please retry with a shorter request."
                            )
                        if choice.get("finish_reason") == "stop":
                            finished = True
                    if not finished:
                        raise _NvidiaFallbackError("NVIDIA stream ended before completion")
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            _NvidiaFallbackError,
        ) as error:
            logger.warning(
                "chat.text_stream_model_unavailable",
                extra=self._stream_error_details("nvidia", error),
            )
            raise _NvidiaFallbackError("NVIDIA stream unavailable") from error

    async def _stream_ollama(self, body: dict) -> AsyncGenerator[str, None]:
        timeout = httpx.Timeout(
            connect=settings.OLLAMA_CONNECT_TIMEOUT_SECONDS,
            read=settings.OLLAMA_TIMEOUT_SECONDS,
            write=30,
            pool=settings.OLLAMA_CONNECT_TIMEOUT_SECONDS,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", settings.OLLAMA_URL, json=body) as response:
                    response.raise_for_status()
                    finished = False
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event.get("error"):
                            raise ValueError("Ollama reported an error")
                        content = event.get("message", {}).get("content", "")
                        if not isinstance(content, str):
                            raise ValueError("Invalid Ollama content")
                        if content:
                            yield content
                        if event.get("done_reason") == "length":
                            raise ChatModelUnavailableError(
                                "The response reached its output limit before it finished. "
                                "Please retry with a shorter request."
                            )
                        if event.get("done"):
                            finished = True
                            break
                    if not finished:
                        raise ValueError("Ollama stream ended before completion")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            logger.warning(
                "chat.text_stream_model_unavailable",
                extra=self._stream_error_details("ollama", error),
            )
            raise ChatModelUnavailableError(
                "Text chat model is still loading or unavailable. Please try again shortly."
            ) from error

    @staticmethod
    def _stream_error_details(provider: str, error: Exception) -> dict:
        # Never log headers, provider bodies, prompts, or exception text (which
        # can include credentials or source documents). Status/type are enough
        # to distinguish rate limits/authentication from transport timeouts.
        details: dict = {"provider": provider, "error_type": type(error).__name__}
        if isinstance(error, httpx.HTTPStatusError):
            details["status_code"] = error.response.status_code
        return details

    def _immediate_answer(self, message: str) -> str | None:
        response_language = self.requested_language(message)
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
        response_language = self.response_language(message, history)
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]
        if response_language == "Tanglish (Tamil written in Latin letters)":
            messages.append({"role": "system", "content": self.TANGLISH_STYLE_PROMPT})

        if reference_history:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "The following messages are optional saved-chat context. They may be "
                        "unrelated or inaccurate. Do not use claims from assistant messages as facts."
                    ),
                }
            )
            messages.extend(
                self._context_message(item)
                for item in reference_history[-self.REFERENCE_MESSAGE_LIMIT :]
                if item.role == "user"
                or not self.response_uses_wrong_language(item.content, response_language)
            )

        # Keep the latest request separate so its language rule is adjacent to it and
        # cannot be overridden by the style of an earlier assistant response.
        previous_history = history
        if (
            previous_history
            and previous_history[-1].role == "user"
            and previous_history[-1].content == message
        ):
            previous_history = previous_history[:-1]
        previous_history = previous_history[-self.HISTORY_MESSAGE_LIMIT :]

        # Recalled preferences precede recent turns so a newer correction always
        # wins (for example, "don't call me master anymore").
        recent_contents = {item.content for item in previous_history}
        standing_preferences = [
            item
            for item in history
            if item.role == "user"
            and item.content not in recent_contents
            and self.is_standing_preference(item.content)
        ][-2:]
        if standing_preferences:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Earlier preferences explicitly stated by this user follow. "
                        "More recent corrections override them. Omit playful titles in distress."
                    ),
                }
            )
            messages.extend(self._context_message(item) for item in standing_preferences)
        messages.extend(
            self._context_message(item)
            for item in previous_history
            if item.role == "user"
            or not self.response_uses_wrong_language(item.content, response_language)
        )

        next_answer_instruction = (
            f"MANDATORY FOR THE NEXT ANSWER: respond only in {response_language}. "
            "Do not mix in another language, apart from unavoidable names or technical terms. "
            "The language of older messages must not affect this choice. Never imitate the "
            "language of an older assistant response. "
            "Respond to what changed in this turn, including a correction or declined suggestion. "
            "If someone is distressed, acknowledge their specific concern before advice. "
            "If danger remains unresolved, pair a focused safety question with one practical "
            "immediate action; when a helpline was declined, offer a manageable alternative "
            "such as asking someone nearby to sit with them. Do not replace listening with a script."
        )
        if re.search(r"\b(?:format|formatting|bug|issue|template|repro)\b", message, re.IGNORECASE):
            next_answer_instruction += (
                " For formatting tasks: Use no timestamp, category, priority, issue number, or other "
                "metadata unless the current source content explicitly contains that exact value. "
                "Never copy metadata or facts from an example or an older issue. "
                "Do not invent missing fields."
            )
        messages.append({"role": "system", "content": next_answer_instruction})
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

    @classmethod
    def is_standing_preference(cls, content: str) -> bool:
        """Return non-language preferences that may safely carry across chats."""
        if re.search(
            r"\b(?:english|t(?:h)?anglish|tamil|hinglish|hindi)\b", content, re.IGNORECASE
        ):
            return False
        return cls.STANDING_PREFERENCE_PATTERN.search(content) is not None

    @classmethod
    def _context_message(cls, item: ChatHistoryMessage) -> dict[str, str]:
        content = item.content
        limit = cls.CONTEXT_MESSAGE_CHAR_LIMIT
        if len(content) > limit:
            half = (limit - len("\n...[truncated]...\n")) // 2
            content = f"{content[:half]}\n...[truncated]...\n{content[-half:]}"
        return {"role": item.role, "content": content}
