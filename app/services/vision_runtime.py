import threading
from contextlib import contextmanager

# A single shared slot intentionally serializes meal and chat vision inference.
# On CPU-only hosts, parallel model calls compete for the same cores and make both
# requests substantially slower. Queuing increases wait time under load but keeps
# each inference predictable and prevents CPU starvation.
_vision_semaphore = threading.BoundedSemaphore(value=1)


@contextmanager
def vision_inference_slot():
    _vision_semaphore.acquire()
    try:
        yield
    finally:
        _vision_semaphore.release()
