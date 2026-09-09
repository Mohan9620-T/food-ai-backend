import json

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter(tags=["NVIDIA Test"])


class NvidiaChatRequest(BaseModel):
    text: str = Field(min_length=1, description="Text prompt sent to NVIDIA.")


class NvidiaChatResponse(BaseModel):
    response: str
    model: str


@router.post(
    "/nvidia-chat",
    response_model=NvidiaChatResponse,
    summary="Test NVIDIA text chat",
    description=(
        "Send a text prompt directly to the configured NVIDIA chat model. "
        "This endpoint is intended for provider testing only."
    ),
    responses={
        502: {"description": "NVIDIA returned an invalid or unsuccessful response."},
        503: {"description": "NVIDIA_API_KEY is not configured or NVIDIA is unreachable."},
        504: {"description": "The NVIDIA request timed out."},
    },
)
async def nvidia_chat(payload: NvidiaChatRequest) -> NvidiaChatResponse:
    if not settings.NVIDIA_API_KEY:
        raise HTTPException(status_code=503, detail="NVIDIA_API_KEY is not configured")

    timeout = httpx.Timeout(
        connect=settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS,
        read=settings.NVIDIA_TEST_CHAT_TIMEOUT_SECONDS,
        write=settings.NVIDIA_TEST_CHAT_TIMEOUT_SECONDS,
        pool=settings.NVIDIA_CHAT_CONNECT_TIMEOUT_SECONDS,
    )
    request_body = {
        "model": settings.NVIDIA_TEST_CHAT_MODEL,
        "messages": [{"role": "user", "content": payload.text}],
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 16384,
        "stream": True,
        "temperature": 1.0,
        "top_p": 0.95,
    }

    try:
        # This diagnostic endpoint connects directly because stale machine-level
        # proxy variables can otherwise prevent access to NVIDIA's hosted API.
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{settings.NVIDIA_API_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.NVIDIA_API_KEY}",
                    "Accept": "text/event-stream",
                },
                json=request_body,
            ) as response:
                response.raise_for_status()
                chunks: list[str] = []
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    content = event["choices"][0]["delta"].get("content")
                    if content:
                        chunks.append(content)
    except httpx.TimeoutException as error:
        raise HTTPException(status_code=504, detail="NVIDIA request timed out") from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"NVIDIA returned HTTP {error.response.status_code}",
        ) from error
    except httpx.RequestError as error:
        raise HTTPException(status_code=503, detail="NVIDIA API is unavailable") from error

    try:
        content = "".join(chunks).strip()
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Invalid NVIDIA response") from error

    if not content:
        raise HTTPException(status_code=502, detail="NVIDIA returned an empty response")

    return NvidiaChatResponse(response=content, model=settings.NVIDIA_TEST_CHAT_MODEL)
