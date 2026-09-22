import time

import requests


def post_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: dict,
    json: dict,
    timeout: tuple,
) -> requests.Response:
    """Retry transient hosted-capacity errors within the original read budget.

    Shared by the embedding providers. Mirrors the retry loop already proven
    in app.services.vision_providers.nvidia_provider, generalized to take an
    explicit session so callers keep their own connection-pooled session.
    """
    started = time.monotonic()
    for attempt in range(3):
        remaining = timeout[1] if attempt == 0 else timeout[1] - (time.monotonic() - started)
        if remaining <= 0:
            raise requests.Timeout("Request deadline exceeded")
        response = session.post(
            url, headers=headers, json=json, timeout=(min(timeout[0], remaining), remaining)
        )
        if response.status_code not in {429, 502, 503, 504} or attempt == 2:
            return response
        delay = float(2**attempt)
        try:
            delay = max(delay, min(5, float(response.headers.get("Retry-After", "0"))))
        except (TypeError, ValueError):
            pass
        if time.monotonic() - started + delay >= timeout[1]:
            return response
        response.close()
        time.sleep(delay)
    raise requests.Timeout("Request deadline exceeded")
