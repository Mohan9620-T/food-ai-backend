import asyncio
import struct

import httpx

from app.services.web_image_preview import is_photograph, vet_preview_images


def _png(width: int, height: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    chunk = struct.pack(">I", len(ihdr) - 4) + ihdr + b"\x00\x00\x00\x00"
    return signature + chunk + b"\x00" * 20


def test_is_photograph_rejects_small_or_extreme_aspect_ratio():
    assert is_photograph(800, 500) is True
    assert is_photograph(50, 50) is False
    assert is_photograph(800, 100) is False
    assert is_photograph(399, 500) is False


def test_vets_candidates_concurrently_and_keeps_only_photographs(monkeypatch):
    good = _png(800, 500)
    small = _png(50, 50)

    def handler(request: httpx.Request) -> httpx.Response:
        if "good" in str(request.url):
            return httpx.Response(206, content=good)
        if "small" in str(request.url):
            return httpx.Response(206, content=small)
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.web_image_preview.httpx.AsyncClient", mocked_client)

    result = asyncio.run(
        vet_preview_images(["http://x/good.png", "http://x/small.png", "http://x/missing.png"])
    )

    assert result == ["http://x/good.png"]


def test_caps_results_at_the_requested_limit(monkeypatch):
    good = _png(800, 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, content=good)

    real_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.web_image_preview.httpx.AsyncClient", mocked_client)

    result = asyncio.run(
        vet_preview_images(["http://x/a.png", "http://x/b.png", "http://x/c.png"], limit=2)
    )

    assert len(result) == 2


def test_empty_candidate_list_short_circuits_without_a_network_call(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("must not create a client for an empty candidate list")

    monkeypatch.setattr("app.services.web_image_preview.httpx.AsyncClient", fail_if_called)

    assert asyncio.run(vet_preview_images([])) == []


def test_a_failed_fetch_is_dropped_not_raised(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow host")

    real_client = httpx.AsyncClient

    def mocked_client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.services.web_image_preview.httpx.AsyncClient", mocked_client)

    assert asyncio.run(vet_preview_images(["http://x/unreachable.png"])) == []
