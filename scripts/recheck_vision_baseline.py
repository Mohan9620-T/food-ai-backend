import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import requests
from PIL import Image

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import models  # noqa: E402,F401
from app.config import settings  # noqa: E402
from app.database.database import SessionLocal  # noqa: E402
from app.models.chat import ChatMessageRecord  # noqa: E402
from app.services.chat_vision_service import ChatVisionService  # noqa: E402

DEFAULT_RESULTS_PATH = REPO_ROOT / "docs" / "vision-baseline-qwen3-vl-4b-structured.json"
SAMPLE_NAMES = ("persisted-image-1", "persisted-image-2", "generated-red-png")


def persisted_images() -> list[bytes]:
    db = SessionLocal()
    try:
        records = (
            db.query(ChatMessageRecord)
            .filter(ChatMessageRecord.image_data.isnot(None))
            .order_by(ChatMessageRecord.id)
            .all()
        )
        unique: list[bytes] = []
        hashes: set[str] = set()
        for record in records:
            digest = hashlib.sha256(record.image_data).hexdigest()
            if digest not in hashes:
                hashes.add(digest)
                unique.append(record.image_data)
        if len(unique) < 2:
            raise RuntimeError("The two distinct Phase 0 persisted images are unavailable")
        return unique[:2]
    finally:
        db.close()


def red_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def load_results(results_path: Path) -> list[dict]:
    if not results_path.exists():
        return []
    return json.loads(results_path.read_text(encoding="utf-8"))["results"]


def save_results(results_path: Path, results: list[dict]) -> None:
    payload = {
        "model": settings.OLLAMA_CHAT_VISION_MODEL,
        "pipeline": "ChatVisionService structured VisionResult",
        "timeout_seconds": settings.OLLAMA_CHAT_VISION_TIMEOUT_SECONDS,
        "results": results,
    }
    results_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", required=True, choices=SAMPLE_NAMES)
    parser.add_argument("--model", default=settings.OLLAMA_CHAT_VISION_MODEL)
    parser.add_argument("--results-path", type=Path, default=DEFAULT_RESULTS_PATH)
    args = parser.parse_args()
    settings.OLLAMA_CHAT_VISION_MODEL = args.model

    images = persisted_images()
    samples = {
        "persisted-image-1": images[0],
        "persisted-image-2": images[1],
        "generated-red-png": red_png(),
    }
    results = [entry for entry in load_results(args.results_path) if entry["sample"] != args.sample]
    record = {
        "sample": args.sample,
        "elapsed_seconds": None,
        "raw_response": None,
        "raw_thinking": None,
        "response": None,
        "error": None,
    }
    original_post = requests.post

    def capture_post(*post_args, **post_kwargs):
        response = original_post(*post_args, **post_kwargs)
        try:
            message = response.json()["message"]
            record["raw_response"] = message.get("content")
            record["raw_thinking"] = message.get("thinking")
        except (KeyError, TypeError, ValueError):
            pass
        return response

    requests.post = capture_post
    started = time.perf_counter()
    try:
        record["response"] = ChatVisionService().describe(samples[args.sample], None)
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        requests.post = original_post
    record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    results.append(record)
    save_results(args.results_path, results)
    print(json.dumps(record, indent=2), flush=True)


if __name__ == "__main__":
    main()
