import asyncio
import json

import pytest
import requests

from app.config import settings
from app.services import web_search_provider
from app.services.chat_service import ChatService
from app.services.web_lookup import CitationFilter, automatic_web_question, evidence, lookup


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError()

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def search_settings(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(settings, "WIKIPEDIA_SEARCH_ENABLED", False)
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")


def source(content="Verified current data", url="https://source.example/office"):
    return {"title": "Official office", "url": url, "content": content}


@pytest.mark.parametrize(
    "question",
    [
        "can you tell the CM name on tamilnadu in 2026",
        "Tamilnadu CM yaaru?",
        "Who is the president of France?",
        "Explain the Milky Way",
        "What is the latest iPhone price?",
        "Find QA jobs in Chennai",
        "How does gravity work?",
        "I'm not sure who the current Tamil Nadu CM is",
        "I feel anxious. What are today's flight delays?",
        "Today IPL score",
    ],
)
def test_factual_questions_automatically_require_lookup(question):
    assert automatic_web_question(question)


@pytest.mark.parametrize(
    "message",
    [
        "Hi",
        "Thanks!",
        "yes",
        "continue",
        "what is 2+2?",
        "Rewrite this paragraph",
        "Create a poem about the moon",
        "Read my uploaded spreadsheet",
        "What is idli mentioned in the document?",
        "Find rows 24138, 24101, 24102",
        "How many rows are in the spreadsheet?",
        "Can you read the file and give me the row counts?",
        "Create a new Excel file",
        "Please keep the same format as my original Excel sheet — Code, Name, Category, Status, Price — and group the items price-wise.",
        "Sort the workbook by Price from lowest to highest",
        "Group the rows by price and keep the original columns",
        "Please format my 2026 sales spreadsheet",
        "Give me a diet plan and workout sessions from this image",
        "i feel so rejected and stuck right now, what can i do",
        "I'm feeling really low today",
        "i feel like nobody would care anyway",
        "That makes me feel so anxious",
        "I'm not okay today",
        "I'm so sad right now",
        "enakku romba kashtama irukku, enna panrathu?",
        "எனக்கு ரொம்ப கஷ்டமாக இருக்கு",
        "What can I do?",
        "What should I do now?",
        "Can we just talk?",
        "I'm so happy, I passed my driving test today!",
    ],
)
def test_local_work_and_conversation_do_not_trigger_search(message):
    assert not automatic_web_question(message)


def test_emotional_disclosure_containing_now_or_today_does_not_trigger_search():
    # Regression: "now"/"today" used to be unconditional date-sensitivity triggers,
    # so "I feel stuck right now" was misclassified as a current-events question and
    # forced through the web-search pipeline, producing a cited self-help listicle
    # instead of a plain conversational reply.
    assert not automatic_web_question("i feel so rejected and stuck right now, what can i do")
    assert not automatic_web_question("I'm feeling awful today")
    assert automatic_web_question("What is the latest iPhone price today")  # still works


def test_personal_disclosure_does_not_send_text_to_search(monkeypatch):
    def unexpected_search(_message):
        pytest.fail("A personal disclosure must stay in the conversation")

    monkeypatch.setattr(web_search_provider, "search", unexpected_search)
    assert lookup("I feel rejected and stuck right now, what can I do?") is None


@pytest.mark.parametrize("forced", [False, True])
def test_requested_support_search_still_runs(monkeypatch, forced):
    searches = []

    def search(message):
        searches.append(message)
        return [source("Local support services and their contact details")]

    monkeypatch.setattr(web_search_provider, "search", search)
    message = "I'm feeling low today"
    if not forced:
        message += "; search the web for local support services"
    result = lookup(message, force=forced)
    assert searches == [message]
    assert result and result.sources and not result.error


def test_wikipedia_topic_retains_event_year_but_resolves_office_topic():
    assert "2026" in web_search_provider._wikipedia_query("Who won the 2026 world cup?")
    assert (
        web_search_provider._wikipedia_query(
            "Explain the Milky Way galaxy in three paragraphs with source links"
        )
        == "Milky Way galaxy"
    )
    assert (
        web_search_provider._wikipedia_query("can you tell the CM name on tamilnadu in 2026")
        == "chief minister Tamil Nadu"
    )


@pytest.mark.parametrize("size", [1, 3, 11, 1000])
def test_citation_filter_handles_provider_tokens_across_chunk_boundaries(size):
    text = 'A claim【{"id":0,"cursor":0,"loc":0}】. Keep 【Tamil Nadu】 and [source](https://example.org).'
    clean = CitationFilter()
    result = "".join(
        clean.feed(text[i : i + size]) for i in range(0, len(text), size)
    ) + clean.feed("", final=True)
    assert result == "A claim. Keep 【Tamil Nadu】 and [source](https://example.org)."


def test_disabled_search_does_not_call_network(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", False)
    monkeypatch.setattr(web_search_provider, "search", lambda _: pytest.fail("Network"))
    assert lookup("Who is the current CM?") is None
    assert "not enabled" in lookup("Search the web", force=True).error


@pytest.mark.parametrize("suffix", ["", "†L1-L4"])
def test_known_article_title_citation_becomes_an_actual_retrieved_link(suffix):
    sources = evidence([source()], "question")
    clean = CitationFilter(sources.citation_links)
    assert (
        clean.feed("Fact【Official ") + clean.feed("office" + suffix + "】.", final=True)
        == "Fact [Official office](<https://source.example/office>)."
    )


def test_automatic_lookup_does_not_ask_old_model_for_permission(monkeypatch):
    calls = []
    monkeypatch.setattr(web_search_provider, "search", lambda q: calls.append(q) or [source()])
    monkeypatch.setattr(
        "app.services.chat_service.requests.post", lambda *a, **k: pytest.fail("Decision call")
    )
    question = "can you tell the CM name on tamilnadu in 2026"
    result = ChatService()._maybe_web_search(question)
    assert calls == [question]
    assert "Verified current data" in result.context
    assert "source.example/office" in result.sources


def test_failed_automatic_lookup_never_answers_with_old_knowledge(monkeypatch):
    monkeypatch.setattr(web_search_provider, "search", lambda _: None)
    monkeypatch.setattr(
        "app.services.chat_service.requests.post", lambda *a, **k: pytest.fail("LLM answer")
    )
    answer = ChatService().chat("Who is Tamil Nadu CM in 2026?", [], [])
    assert "won't guess" in answer and "couldn't verify" in answer


def test_explicit_url_works_without_search_key(monkeypatch):
    monkeypatch.setattr("app.services.web_lookup.read_page", lambda url: [source(url=url)])
    result = lookup("Read https://source.example/office")
    assert "Verified current data" in result.context
    assert "source.example/office" in result.sources


def test_unreadable_url_does_not_fall_back_to_training(monkeypatch):
    from app.services.web_page_reader import WebPageError

    def fail(_):
        raise WebPageError("Unavailable")

    monkeypatch.setattr("app.services.web_lookup.read_page", fail)
    assert "couldn't read" in lookup("Read https://source.example").error


def test_direct_read_success_does_not_trigger_unnecessary_search(monkeypatch):
    monkeypatch.setattr("app.services.web_lookup.read_page", lambda url: [source(url=url)])
    monkeypatch.setattr(
        web_search_provider, "search", lambda *a, **k: pytest.fail("Unneeded search call")
    )
    result = lookup("Read https://source.example/office")
    assert "Verified current data" in result.context


def test_direct_read_failure_falls_back_to_search(monkeypatch):
    from app.services.web_page_reader import WebPageError

    def fail(_):
        raise WebPageError("Unavailable")

    monkeypatch.setattr("app.services.web_lookup.read_page", fail)
    monkeypatch.setattr(web_search_provider, "search", lambda query: [source()])
    result = lookup("Read https://source.example/office")
    assert "Verified current data" in result.context
    assert "Falling back to web search" in result.context


def test_direct_read_and_search_both_failing_reports_both(monkeypatch):
    from app.services.web_page_reader import WebPageError

    def fail(_):
        raise WebPageError("Unavailable")

    monkeypatch.setattr("app.services.web_lookup.read_page", fail)
    monkeypatch.setattr(web_search_provider, "search", lambda query: None)
    error = lookup("Read https://source.example/office").error
    assert "couldn't read" in error
    assert "web search" in error


# --- Acceptance: GitHub URLs are actually fetched end-to-end, not just searched ----


def test_acceptance_github_tree_url_is_actually_fetched_not_just_searched(monkeypatch):
    import base64

    from app.services import web_page_reader as reader

    listing = [
        {
            "name": "attention_engine.py",
            "type": "file",
            "path": "app/cognitive_kernel/engines/attention_engine.py",
        },
        {
            "name": "memory_engine.py",
            "type": "file",
            "path": "app/cognitive_kernel/engines/memory_engine.py",
        },
    ]
    engine_source = {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(
            b'class AttentionEngine:\n    """Routes salience across the cognitive kernel."""\n'
        ).decode(),
    }

    engine_paths = {
        "app/cognitive_kernel/engines/attention_engine.py",
        "app/cognitive_kernel/engines/memory_engine.py",
    }

    def fetch(url, deadline, **kwargs):
        # The branch "feat/meta-cognitive-architecture" itself contains a "/", so
        # the resolver must probe: split=1 (branch="feat") 404s before split=2
        # (the real branch) succeeds - simulate that real-GitHub behavior here.
        if url.endswith(
            "contents/app/cognitive_kernel/engines?ref=feat/meta-cognitive-architecture"
        ):
            return url, "application/json", json.dumps(listing)
        if any(
            url.endswith(f"contents/{path}?ref=feat/meta-cognitive-architecture")
            for path in engine_paths
        ):
            return url, "application/json", json.dumps(engine_source)
        raise reader.WebPageError(
            "The website returned HTTP 404; it may require sign-in or be unavailable."
        )

    monkeypatch.setattr(reader, "_fetch", fetch)
    monkeypatch.setattr(
        web_search_provider, "search", lambda *a, **k: pytest.fail("Must not fall back to search")
    )

    message = (
        "Explain the cognitive kernel engines in this repository:\n\n"
        "https://github.com/jeyachandran123/Atlas/tree/feat/meta-cognitive-architecture/"
        "app/cognitive_kernel/engines\n\n"
        "Explain the folder structure and each engine like a Chief AI/ML Architect."
    )
    result = lookup(message)

    assert result.error == ""
    assert "AttentionEngine" in result.context
    assert "Routes salience across the cognitive kernel" in result.context
    assert "attention_engine.py" in result.context and "memory_engine.py" in result.context


def test_acceptance_github_blob_url_is_actually_fetched_not_just_searched(monkeypatch):
    import base64

    from app.services import web_page_reader as reader

    file_payload = {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(
            b"NVIDIA_API_KEY=\nJWT_SECRET_KEY=replace_with_a_long_random_jwt_secret\n"
        ).decode(),
    }
    monkeypatch.setattr(
        reader,
        "_fetch",
        lambda url, deadline, **k: (url, "application/json", json.dumps(file_payload)),
    )
    monkeypatch.setattr(
        web_search_provider, "search", lambda *a, **k: pytest.fail("Must not fall back to search")
    )

    message = (
        "Explain this file:\n\n"
        "https://github.com/jeyachandran123/Atlas/blob/feat/meta-cognitive-architecture/.env.example"
    )
    result = lookup(message)

    assert result.error == ""
    assert "JWT_SECRET_KEY" in result.context
    assert "replace_with_a_long_random_jwt_secret" in result.context  # placeholder, safe to show


def test_tavily_primary_result_and_request(monkeypatch):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-only")

    def post(url, **kwargs):
        assert kwargs["json"]["include_answer"] is False
        assert kwargs["json"]["query"] == "Tamil Nadu CM 2026"
        return Response(payload={"results": [source(), {"title": "No URL"}]})

    monkeypatch.setattr(web_search_provider._session, "post", post)
    monkeypatch.setattr(
        web_search_provider, "_search_wikipedia", lambda _: pytest.fail("Unneeded fallback")
    )
    assert web_search_provider.search("Tamil Nadu CM 2026") == [source()]


@pytest.mark.parametrize("payload", [{"results": "bad"}, {}, []])
def test_malformed_tavily_falls_back_to_wikipedia(monkeypatch, payload):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-only")
    monkeypatch.setattr(
        web_search_provider._session, "post", lambda *a, **k: Response(payload=payload)
    )
    monkeypatch.setattr(
        web_search_provider, "_search_wikipedia", lambda _: [source("Live Wikipedia")]
    )
    assert web_search_provider.search("question")[0]["content"] == "Live Wikipedia"


@pytest.mark.parametrize("failure", [401, 429, 500])
def test_tavily_http_error_falls_back(monkeypatch, failure):
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-only")
    monkeypatch.setattr(
        web_search_provider._session, "post", lambda *a, **k: Response(status=failure)
    )
    monkeypatch.setattr(web_search_provider, "_search_wikipedia", lambda _: [source()])
    assert web_search_provider.search("question") == [source()]


def test_wikipedia_search_works_without_key_and_reads_intro(monkeypatch):
    monkeypatch.setattr(settings, "WIKIPEDIA_SEARCH_ENABLED", True)

    def fetch(url, deadline):
        assert "generator=search" in url and "chief+minister+Tamil+Nadu" in url
        assert "exintro=1" in url and "exchars" not in url
        return (
            url,
            "application/json",
            json.dumps(
                {
                    "query": {
                        "pages": [
                            {
                                "title": "Chief Minister of Tamil Nadu",
                                "index": 1,
                                "fullurl": "https://en.wikipedia.org/wiki/Chief_Minister_of_Tamil_Nadu",
                                "extract": "The current office holder changed after the election.",
                                "revisions": [{"timestamp": "2026-09-21T04:41:57Z"}],
                            }
                        ]
                    }
                }
            ),
        )

    monkeypatch.setattr(web_search_provider, "_fetch", fetch)
    results = web_search_provider.search("can you tell the CM name on tamilnadu in 2026")
    assert results[0]["page_updated_at"] == "2026-09-21T04:41:57Z"
    assert "changed after" in results[0]["content"]


@pytest.mark.parametrize("raw", ['{"query":{"pages":[]}}', '{"error":{}}', "not JSON", "[]"])
def test_empty_or_invalid_wikipedia_is_not_treated_as_evidence(monkeypatch, raw):
    monkeypatch.setattr(settings, "WIKIPEDIA_SEARCH_ENABLED", True)
    monkeypatch.setattr(web_search_provider, "_fetch", lambda *a: ("url", "application/json", raw))
    assert web_search_provider.search("CM Tamil Nadu") is None


def test_sources_are_deduplicated_and_unsafe_links_dropped():
    result = evidence(
        [
            source(),
            source(),
            source(url="javascript:alert(1)"),
            source(url="https://user:pass@host.example"),
        ],
        "question",
    )
    assert result.sources.count("source.example/office") == 1
    assert "javascript" not in result.sources and "pass@" not in result.sources
    assert "Retrieved" in result.sources and "UTC" in result.sources
    assert "not proof that a source is up to date" in result.context


def test_plain_chat_appends_real_sources_even_if_model_omits_citations(monkeypatch):
    monkeypatch.setattr(web_search_provider, "search", lambda _: [source("New office holder")])
    captured = []

    def complete(self, body):
        captured.extend(body["messages"])
        return "The current office holder is described in the source."

    monkeypatch.setattr(ChatService, "_chat_with_ollama", complete)
    answer = ChatService().chat("Who is the CM in 2026?", [], [])
    assert "### Sources" in answer and "source.example/office" in answer
    assert any("New office holder" in m["content"] for m in captured)


def test_stream_sources_are_request_scoped_and_appended_once(monkeypatch):
    monkeypatch.setattr(
        web_search_provider,
        "search",
        lambda q: [source(q, "https://source.example/" + ("alpha" if "alpha" in q else "beta"))],
    )

    async def generate(self, body):
        yield "Answer."

    monkeypatch.setattr(ChatService, "_stream_ollama", generate)
    service = ChatService()

    async def run(question):
        return "".join([chunk async for chunk in service.stream_chat(question, [], [])])

    async def both():
        return await asyncio.gather(run("Who is alpha?"), run("Who is beta?"))

    a, b = asyncio.run(both())
    assert a.count("### Sources") == b.count("### Sources") == 1
    assert "source.example/alpha" in a and "source.example/beta" not in a
    assert "source.example/beta" in b and "source.example/alpha" not in b


def test_failure_stream_returns_clear_unverified_message(monkeypatch):
    monkeypatch.setattr(web_search_provider, "search", lambda _: None)

    async def run():
        return "".join(
            [chunk async for chunk in ChatService().stream_chat("Who is current CM?", [], [])]
        )

    answer = asyncio.run(run())
    assert "couldn't verify" in answer and "### Sources" not in answer


def test_search_disabled_keeps_existing_single_model_call(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", False)
    monkeypatch.setattr(settings, "APP_ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "test-only")
    calls = []
    monkeypatch.setattr(
        "app.services.chat_service.requests.post",
        lambda *a, **k: (
            calls.append(1) or Response(payload={"choices": [{"message": {"content": "answer"}}]})
        ),
    )
    assert ChatService().chat("Hello", [], []) == "answer"
    assert calls == [1]
