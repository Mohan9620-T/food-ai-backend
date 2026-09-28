import asyncio
import json
import time

import pytest

from app.config import settings
from app.schemas.chat import ChatHistoryMessage as Message
from app.services import web_search_provider
from app.services import youtube_lookup as youtube
from app.services.chat_service import ChatService
from app.services.web_lookup import lookup
from app.services.web_page_reader import WebPageError

CHANNEL = "UC" + "A" * 22
OTHER = "UC" + "B" * 22
URL = "https://www.youtube.com/@ExampleClub"


@pytest.fixture(autouse=True)
def settings_and_network(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", True)
    monkeypatch.setattr(web_search_provider, "search", lambda q: [])
    monkeypatch.setattr(youtube, "_fetch", lambda *a, **k: pytest.fail("Unexpected network call"))


def page(
    *, identity=CHANNEL, title="Example Club", handle="ExampleClub", latest=True, modern=False
):
    cards = []
    for index in range(1, 4):
        if modern:
            card = {
                "lockupViewModel": {
                    "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                    "contentId": f"{index:011d}",
                    "metadata": {
                        "lockupMetadataViewModel": {
                            "title": {"content": f"Upload {index}"},
                            "metadata": {
                                "contentMetadataViewModel": {
                                    "metadataRows": [
                                        {
                                            "metadataParts": [
                                                {"text": {"content": f"{index} days ago"}}
                                            ]
                                        }
                                    ]
                                }
                            },
                        }
                    },
                }
            }
        else:
            card = {
                "videoRenderer": {
                    "videoId": f"{index:011d}",
                    "title": {"runs": [{"text": f"Upload {index}"}]},
                    "publishedTimeText": {"simpleText": f"{index} days ago"},
                }
            }
        cards.append({"richItemRenderer": {"content": card}})
    return {
        "metadata": {
            "channelMetadataRenderer": {
                "externalId": identity,
                "title": title,
                "ownerUrls": [f"https://www.youtube.com/@{handle}"],
            }
        },
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [
                    {
                        "tabRenderer": {
                            "title": "Home",
                            "content": {"videoRenderer": {"videoId": "WRONG000001"}},
                        }
                    },
                    {
                        "tabRenderer": {
                            "title": "Videos",
                            "selected": True,
                            "content": {
                                "richGridRenderer": {
                                    "contents": cards
                                    + [
                                        {
                                            "richSectionRenderer": {
                                                "videoRenderer": {
                                                    "videoId": "WRONG000002",
                                                    "title": {
                                                        "simpleText": "Unrelated recommendation"
                                                    },
                                                }
                                            }
                                        }
                                    ],
                                    "header": {
                                        "chipBarViewModel": {
                                            "chips": [
                                                {
                                                    "chipViewModel": {
                                                        "text": "Latest" if latest else "Popular",
                                                        "selected": True,
                                                    }
                                                }
                                            ]
                                        }
                                    },
                                }
                            },
                        }
                    },
                ]
            }
        },
    }


def install_page(monkeypatch, data=None):
    calls = []

    def fetch(url, deadline, **kwargs):
        calls.append(url)
        assert deadline > time.monotonic()
        return (
            url,
            "text/html",
            "<script>var ytInitialData = " + json.dumps(data or page()) + ";</script>",
        )

    monkeypatch.setattr(youtube, "_fetch", fetch)
    return calls


@pytest.mark.parametrize(
    "prompt,target,count",
    [
        ("can you show latest video on the channel", "Example Club", 1),
        ("show latest 3 videos on the channel", "Example Club", 3),
        ("show the latest seven videos on the channel", "Example Club", 7),
        ("List the recent videos", "Example Club", 5),
        ("Show the latest 2 videos from Beta channel", "Beta", 2),
        ("List videos on YouTube channel Beta", "Beta", 5),
        ("Show videos from a different channel", None, 5),
        ("Show latest videos from @Beta", "https://www.youtube.com/@Beta", 5),
        ("Show 99 videos from Example Club channel", "Example Club", 30),
    ],
)
def test_followup_keeps_channel_and_explicit_switch_takes_precedence(prompt, target, count):
    history = [Message(role="user", content="Do you know Example Club YouTube channel?")]
    request = youtube.video_request(prompt, history)
    assert request and request.target == target and request.count == count


@pytest.mark.parametrize(
    "prompt",
    [
        "Hi",
        "How are you?",
        "What are the latest video games?",
        "Create a video",
        "What is a YouTube channel?",
        "What is the latest news?",
        "Show the latest phone prices",
        "Give me video ideas for my YouTube channel",
    ],
)
def test_other_topics_do_not_inherit_old_video_channel(prompt):
    assert youtube.video_request(prompt, [Message(role="user", content=f"Read {URL}")]) is None


def test_missing_channel_asks_for_link_without_searching_or_inventing_one():
    result = lookup("Show the latest video on the channel")
    assert result and "Which YouTube channel" in result.answer
    assert "Sources" not in result.answer


def test_assistant_channel_link_is_only_a_live_discovery_hint():
    history = [
        Message(
            role="assistant",
            content=f"The channel is [Example]({URL}).\n### Sources\nhttps://www.youtube.com/@Wrong",
        )
    ]
    assert youtube.video_request("show latest video on the channel", history).target == URL


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.example/@ExampleClub",
        "https://user:pass@youtube.com/@ExampleClub",
        "https://youtube.com:8080/@ExampleClub",
        "https://youtu.be/00000000001",
        "https://youtube.com/watch?v=00000000001",
        "http://127.0.0.1/@ExampleClub",
        "file:///channel/test",
    ],
)
def test_only_real_channel_urls_are_candidates(url):
    assert youtube.channel_url(url) is None


@pytest.mark.parametrize("modern", [False, True])
def test_direct_channel_returns_numbered_list_and_only_its_own_uploads(monkeypatch, modern):
    calls = install_page(monkeypatch, page(modern=modern))
    result = lookup(f"Show 2 latest videos from {URL}")
    answer = result.answer
    assert calls == [URL + "/videos?hl=en"]
    assert "1. **[Upload 1]" in answer and "2. **[Upload 2]" in answer
    assert "Upload 3" not in answer and "WRONG" not in answer
    assert "Published: 1 days ago" in answer
    assert answer.count("### Sources") == 1
    assert URL + "/videos?hl=en" in answer and "watch?v=00000000001" in answer


def test_channel_name_search_ignores_unrelated_video_hits(monkeypatch):
    queries = []

    def search(query):
        queries.append(query)
        return [
            {"url": "https://www.youtube.com/watch?v=WRONG000001"},
            {"url": URL + "/videos"},
            {"url": "https://evil.example/@ExampleClub"},
        ]

    monkeypatch.setattr(web_search_provider, "search", search)
    calls = install_page(monkeypatch)
    result = lookup(
        "can you show latest video on the channel",
        history=[Message(role="user", content="Do you know Example Club channel?")],
    )
    assert queries == ['"Example Club" YouTube channel']
    assert calls == [URL + "/videos?hl=en"]
    assert "Upload 1" in result.answer and "WRONG" not in result.answer


def test_channel_identity_mismatch_never_substitutes_other_videos(monkeypatch):
    install_page(monkeypatch, page(identity=OTHER, title="Different", handle="Different"))
    result = lookup(f"Show latest videos from {URL}")
    assert "exact channel link" in result.answer
    assert "Upload" not in result.answer and "### Sources" not in result.answer


def test_same_display_name_on_two_channels_requires_exact_handle(monkeypatch):
    monkeypatch.setattr(
        web_search_provider,
        "search",
        lambda q: [{"url": URL}, {"url": "https://www.youtube.com/@Other"}],
    )

    def fetch(url, deadline, **kwargs):
        data = page(identity=OTHER, handle="Other") if "@Other" in url else page()
        return url, "text/html", "var ytInitialData=" + json.dumps(data)

    monkeypatch.setattr(youtube, "_fetch", fetch)
    result = lookup("Show latest videos from Example Club channel")
    assert "uniquely verify" in result.answer and "Upload 1" not in result.answer


def test_owner_mismatch_and_duplicate_cards_are_excluded(monkeypatch):
    data = page()
    cards = data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][1]["tabRenderer"]["content"][
        "richGridRenderer"
    ]["contents"]
    cards[0]["richItemRenderer"]["content"]["videoRenderer"]["ownerText"] = {
        "runs": [{"navigationEndpoint": {"browseEndpoint": {"browseId": OTHER}}}]
    }
    cards.append(cards[1])
    install_page(monkeypatch, data)
    answer = lookup(f"List videos on {URL}").answer
    assert "Upload 1" not in answer and answer.count("watch?v=00000000002") == 1


@pytest.mark.parametrize(
    "payload",
    [
        "<html>Sign in</html>",
        "var ytInitialData=[]",
        "var ytInitialData=broken",
        'var ytInitialData={"metadata":null}',
    ],
)
def test_blocked_or_changed_pages_report_unverified_instead_of_model_guess(monkeypatch, payload):
    monkeypatch.setattr(youtube, "_fetch", lambda url, *a, **k: (url, "text/html", payload))
    answer = lookup(f"Show latest videos from {URL}").answer
    assert "couldn't" in answer and "### Sources" not in answer


def feed(identity=CHANNEL, entry_identity=CHANNEL):
    return f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
    <yt:channelId>{identity}</yt:channelId>
    <entry><yt:channelId>{entry_identity}</yt:channelId><yt:videoId>00000000001</yt:videoId><title>Older upload</title><published>2026-09-20T10:00:00Z</published></entry>
    <entry><yt:channelId>{entry_identity}</yt:channelId><yt:videoId>00000000002</yt:videoId><title>Newer upload</title><published>2026-09-27T10:00:00Z</published></entry></feed>"""


def test_non_latest_page_uses_verified_feed_sorted_by_publish_date(monkeypatch):
    def fetch(url, *args, **kwargs):
        if "/feeds/" in url:
            assert kwargs["allow_xml"] is True
            return url, "application/atom+xml", feed()
        return url, "text/html", "var ytInitialData=" + json.dumps(page(latest=False))

    monkeypatch.setattr(youtube, "_fetch", fetch)
    answer = lookup(f"Show latest video from {URL}").answer
    assert "Newer upload" in answer and "Older upload" not in answer
    assert "27 Sep 2026" in answer and "may include Shorts" in answer


@pytest.mark.parametrize(
    "raw", [feed(OTHER), feed(entry_identity=OTHER), "<!DOCTYPE feed><feed />", "broken XML"]
)
def test_feed_mismatch_or_invalid_xml_never_leaks_other_channel_uploads(monkeypatch, raw):
    def fetch(url, *a, **k):
        return (
            (url, "application/atom+xml", raw)
            if "/feeds/" in url
            else (url, "text/html", "var ytInitialData=" + json.dumps(page(latest=False)))
        )

    monkeypatch.setattr(youtube, "_fetch", fetch)
    answer = lookup(f"Show latest video from {URL}").answer
    assert "couldn't verify" in answer
    assert "Newer upload" not in answer and "Upload 1" not in answer


def test_failed_feed_does_not_call_popular_results_latest(monkeypatch):
    def fetch(url, *a, **k):
        if "/feeds/" in url:
            raise WebPageError("HTTP 404")
        return url, "text/html", "var ytInitialData=" + json.dumps(page(latest=False))

    monkeypatch.setattr(youtube, "_fetch", fetch)
    assert "couldn't verify" in lookup(f"Show latest videos from {URL}").answer


@pytest.mark.parametrize("stream", [False, True])
def test_chat_uses_verified_list_without_an_llm_refusal_or_extra_sources(monkeypatch, stream):
    install_page(monkeypatch)
    monkeypatch.setattr(
        ChatService,
        "_build_request_body",
        lambda *a, **k: pytest.fail("No model rewrite for a verified upload list"),
    )
    history = [Message(role="user", content=f"Read {URL}")]
    service = ChatService()

    async def collect():
        return "".join(
            [
                part
                async for part in service.stream_chat(
                    "show latest video on the channel", history, []
                )
            ]
        )

    answer = (
        asyncio.run(collect())
        if stream
        else service.chat("show latest video on the channel", history, [])
    )
    assert "Upload 1" in answer and answer.count("### Sources") == 1
    assert "cannot access" not in answer
    assert service.requests_web("show recent videos", history=history)


def test_disabled_web_does_not_fetch_a_channel(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_WEB_SEARCH", False)
    assert (
        lookup(
            "show latest video on the channel",
            history=[Message(role="user", content=f"Read {URL}")],
        )
        is None
    )
    assert "not enabled" in lookup(f"Show videos from {URL}").error


def test_tanglish_channel_request_and_private_history_are_resolved_safely():
    request = youtube.video_request("Example Club channel la latest video list pannu", [])
    assert request and request.target == "Example Club"
    history = [
        Message(role="user", content=f"Read {URL}"),
        Message(role="user", content="Read my private document from patient John about my results"),
    ]
    assert youtube.video_request("show latest video on the channel", history).target == URL


@pytest.mark.parametrize("stream", [False, True])
def test_api_uses_persisted_channel_context_and_saves_verified_list(
    client, db_session, monkeypatch, stream
):
    from app.models.chat import ChatMessageRecord
    from tests.test_chat import _register_and_login

    calls = install_page(monkeypatch)
    token = _register_and_login(client, email="video-list@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/chat/sessions", json={"title": "Video lookup"}, headers=headers).json()[
        "id"
    ]
    db_session.add(ChatMessageRecord(session_id=session, sender="user", content=f"Read {URL}"))
    db_session.commit()
    monkeypatch.setattr(
        ChatService,
        "_build_request_body",
        lambda *a, **k: pytest.fail("Video lists must not be rewritten by an LLM"),
    )
    response = client.post(
        "/chat/stream" if stream else "/chat/",
        params={"session_id": session},
        headers=headers,
        json={
            "message": "show latest video on the channel",
            "history": [{"role": "user", "content": "Read https://www.youtube.com/@Wrong"}],
        },
    )
    assert response.status_code == 200
    if stream:
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1] == {"type": "done"}
        answer = "".join(event["content"] for event in events if event["type"] == "token")
    else:
        answer = response.json()["response"]
    assert "Upload 1" in answer and "@Wrong" not in answer
    assert calls == [URL + "/videos?hl=en"]
    saved = client.get(f"/chat/sessions/{session}", headers=headers).json()["messages"]
    assert saved[-1]["content"] == answer
