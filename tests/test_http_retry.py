from unittest.mock import Mock

import pytest
import requests

from app.services.http_retry import post_with_retry


def _response(status_code, headers=None):
    return Mock(status_code=status_code, headers=headers or {})


def test_post_with_retry_returns_immediately_on_success():
    session = Mock()
    session.post.return_value = _response(200)

    response = post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 10))

    assert response.status_code == 200
    session.post.assert_called_once()


def test_post_with_retry_does_not_retry_non_retryable_status(monkeypatch):
    session = Mock()
    session.post.return_value = _response(400)
    sleeps = []
    monkeypatch.setattr(
        "app.services.http_retry.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    response = post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 10))

    assert response.status_code == 400
    session.post.assert_called_once()
    assert sleeps == []


def test_post_with_retry_retries_transient_errors_then_succeeds(monkeypatch):
    session = Mock()
    session.post.side_effect = [_response(503, {"Retry-After": "0"}), _response(200)]
    sleeps = []
    monkeypatch.setattr(
        "app.services.http_retry.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    response = post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 10))

    assert response.status_code == 200
    assert session.post.call_count == 2
    assert len(sleeps) == 1


def test_post_with_retry_honors_retry_after_header(monkeypatch):
    session = Mock()
    session.post.side_effect = [_response(429, {"Retry-After": "3"}), _response(200)]
    sleeps = []
    monkeypatch.setattr(
        "app.services.http_retry.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 30))

    assert sleeps == [3.0]


def test_post_with_retry_gives_up_after_three_attempts_and_returns_last_response(monkeypatch):
    session = Mock()
    session.post.side_effect = [
        _response(503, {"Retry-After": "0"}),
        _response(503, {"Retry-After": "0"}),
        _response(503, {"Retry-After": "0"}),
    ]
    monkeypatch.setattr("app.services.http_retry.time.sleep", lambda seconds: None)

    response = post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 30))

    assert response.status_code == 503
    assert session.post.call_count == 3


def test_post_with_retry_ignores_malformed_retry_after_header(monkeypatch):
    session = Mock()
    session.post.side_effect = [_response(503, {"Retry-After": "not-a-number"}), _response(200)]
    sleeps = []
    monkeypatch.setattr(
        "app.services.http_retry.time.sleep", lambda seconds: sleeps.append(seconds)
    )

    response = post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 30))

    assert response.status_code == 200
    # Falls back to the exponential backoff delay (2**0 = 1.0) instead of raising.
    assert sleeps == [1.0]


def test_post_with_retry_returns_failing_response_instead_of_sleeping_past_the_deadline(
    monkeypatch,
):
    session = Mock()
    session.post.return_value = _response(503, {"Retry-After": "0"})
    monkeypatch.setattr("app.services.http_retry.time.sleep", lambda seconds: None)
    # started=0.0; the post-delay check sees 9s already elapsed against a 10s
    # budget, so adding even the minimal backoff delay would exceed it - the
    # function must return the failing response rather than sleep past the deadline.
    times = iter([0.0, 9.0])
    monkeypatch.setattr("app.services.http_retry.time.monotonic", lambda: next(times))

    response = post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 10))

    assert response.status_code == 503
    session.post.assert_called_once()


def test_post_with_retry_raises_timeout_once_the_read_budget_is_exhausted(monkeypatch):
    session = Mock()
    session.post.return_value = _response(503, {"Retry-After": "0"})
    monkeypatch.setattr("app.services.http_retry.time.sleep", lambda seconds: None)
    # started=0.0; post-delay check after attempt 0 sees no elapsed time (still
    # under budget, so it retries); attempt 1's remaining check then sees 15s
    # elapsed against a 10s read budget, so it raises before a second post.
    times = iter([0.0, 0.0, 15.0])
    monkeypatch.setattr("app.services.http_retry.time.monotonic", lambda: next(times))

    with pytest.raises(requests.Timeout):
        post_with_retry(session, "http://x", headers={}, json={}, timeout=(5, 10))

    session.post.assert_called_once()
