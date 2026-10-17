"""One NVIDIA client/event loop, shared by synchronous and async callers."""

import asyncio
import queue
import random
import threading
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.config import settings


def retryable(status: int) -> bool:
    return status in {408, 409, 425, 429} or 500 <= status <= 599


class NvidiaGateway:
    def __init__(self):
        self._lock = threading.Lock()
        self._loop = None
        self._thread = None
        self._client = None
        self._semaphore = None

    async def _initialize(self):
        self._client = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(
                max_connections=settings.LLM_MAX_CONCURRENCY,
                max_keepalive_connections=settings.LLM_MAX_CONCURRENCY,
            ),
        )
        self._semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)

    def submit(self, coroutine):
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, daemon=True, name="nvidia-gateway"
                )
                self._thread.start()
                asyncio.run_coroutine_threadsafe(self._initialize(), self._loop).result()
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    async def _delay(self, attempt):
        await asyncio.sleep(min(8, 0.5 * 2**attempt) * random.uniform(0.5, 1.5))

    async def _post(self, body, timeout):
        assert self._semaphore is not None and self._client is not None
        async with self._semaphore:
            for attempt in range(settings.LLM_MAX_RETRIES + 1):
                try:
                    response = await self._client.post(
                        settings.NVIDIA_API_BASE_URL + "/chat/completions",
                        headers={"Authorization": "Bearer " + settings.NVIDIA_API_KEY},
                        json=body,
                        timeout=timeout,
                    )
                    if not retryable(response.status_code) or attempt == settings.LLM_MAX_RETRIES:
                        return response
                except httpx.TransportError:
                    if attempt == settings.LLM_MAX_RETRIES:
                        raise
                await self._delay(attempt)

    async def post(self, body, timeout):
        return await asyncio.wrap_future(self.submit(self._post(body, timeout)))

    def post_sync(self, body, timeout):
        return self.submit(self._post(body, timeout)).result()

    async def _produce(self, output, body, timeout):
        async def put(value):
            while True:
                try:
                    output.put_nowait(value)
                    return
                except queue.Full:
                    await asyncio.sleep(0.01)

        try:
            assert self._semaphore is not None and self._client is not None
            async with self._semaphore:
                for attempt in range(settings.LLM_MAX_RETRIES + 1):
                    emitted = False
                    try:
                        async with self._client.stream(
                            "POST",
                            settings.NVIDIA_API_BASE_URL + "/chat/completions",
                            headers={"Authorization": "Bearer " + settings.NVIDIA_API_KEY},
                            json=body,
                            timeout=timeout,
                        ) as response:
                            if (
                                retryable(response.status_code)
                                and attempt < settings.LLM_MAX_RETRIES
                            ):
                                await self._delay(attempt)
                                continue
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if line:
                                    emitted = True
                                    await put(("line", line))
                            break
                    except httpx.TransportError:
                        if emitted or attempt == settings.LLM_MAX_RETRIES:
                            raise
                        await self._delay(attempt)
            await put(("done", None))
        except Exception as error:
            await put(("error", error))

    @asynccontextmanager
    async def stream(self, body, timeout):
        output: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=32)
        future = self.submit(self._produce(output, body, timeout))

        class StreamResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                while True:
                    try:
                        kind, value = output.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.01)
                        continue
                    if kind == "error":
                        raise value
                    if kind == "done":
                        break
                    yield value

        try:
            yield StreamResponse()
        finally:
            future.cancel()

    async def close(self):
        if self._loop is None:
            return

        async def shutdown():
            assert self._client is not None
            await self._client.aclose()

        try:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(shutdown(), self._loop))
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            assert self._thread is not None
            await asyncio.to_thread(self._thread.join, 5)
            self._loop.close()
            self._loop = self._thread = self._client = self._semaphore = None


gateway = NvidiaGateway()


@asynccontextmanager
async def client_context(timeout):
    if not settings.ENABLE_LLM_GATEWAY:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            yield client
    else:

        class Client:
            async def post(self, url, **kwargs):
                return await gateway.post(kwargs["json"], timeout)

            def stream(self, method, url, **kwargs):
                return gateway.stream(kwargs["json"], timeout)

        yield Client()
