import threading
from contextlib import contextmanager

# A single shared slot intentionally serializes meal and chat vision inference.
# On CPU-only hosts, parallel model calls compete for the same cores and make both
# requests substantially slower. Queuing increases wait time under load but keeps
# each inference predictable and prevents CPU starvation.
_vision_semaphore = threading.BoundedSemaphore(value=1)


@contextmanager
def vision_inference_slot(timeout_seconds: float | None = None):
    acquired = _vision_semaphore.acquire(timeout=timeout_seconds)
    if not acquired:
        from app.services.image_parser_service import VisionModelUnavailableError

        raise VisionModelUnavailableError("Image analysis is busy. Please retry shortly.")
    try:
        yield
    finally:
        _vision_semaphore.release()
