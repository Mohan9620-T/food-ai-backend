from app.services.web_search_safety import (
    asks_for_explicit,
    clean,
    explicit_domains,
    is_explicit_source,
)


def test_blocks_explicit_queries():
    assert asks_for_explicit("best nude pics site") is True
    assert asks_for_explicit("xxx videos free") is True
    assert asks_for_explicit("onlyfans subscription price") is True
    assert asks_for_explicit("send me nudes") is True
    assert asks_for_explicit("nudity in renaissance art history") is True


def test_does_not_block_ordinary_queries_that_contain_lookalike_words():
    assert asks_for_explicit("nude lipstick shades for fall") is False
    assert asks_for_explicit("naked eye visible planets tonight") is False
    assert asks_for_explicit("naked mole rat facts") is False
    assert asks_for_explicit("nude color heels") is False
    assert asks_for_explicit("healthy recipes for dinner") is False


def test_filters_explicit_results_by_domain():
    results = [
        {"title": "ok", "url": "https://example.com/a", "content": "healthy recipes"},
        {"title": "bad", "url": "https://pornhub.com/video", "content": "x"},
    ]
    assert clean(results) == [results[0]]


def test_filters_explicit_results_by_title_or_content():
    results = [
        {"title": "ok", "url": "https://example.com/a", "content": "healthy recipes"},
        {"title": "best nude pics", "url": "https://example.com/b", "content": "x"},
    ]
    assert clean(results) == [results[0]]


def test_is_explicit_source_checked_individually():
    assert (
        is_explicit_source({"title": "x", "url": "https://xvideos.com/v", "content": "x"}) is True
    )
    assert (
        is_explicit_source(
            {"title": "nude lipstick review", "url": "https://example.com", "content": ""}
        )
        is False
    )


def test_explicit_domains_returns_a_nonempty_list_for_tavily_exclusion():
    domains = explicit_domains()
    assert isinstance(domains, list)
    assert "pornhub.com" in domains
