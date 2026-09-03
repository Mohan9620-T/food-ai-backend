import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.schemas.vision_result import VisionResult
from app.services.image_parser_service import ImageParserService
from app.services.vision_image_preprocessor import prepare_vision_image


DATASET_ROOT = REPO_ROOT / "docs" / "vision-eval-dataset"
RESULTS_PATH = DATASET_ROOT / "raw-results-qwen3-vl-4b.json"
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3-vl:4b"
REQUEST_TIMEOUT_SECONDS = 180
DIFFICULT_CASE_IDS = {
    "indian-007",  # overlapping multi-item scene
    "indian-010",  # low light
    "indian-012",  # occlusion
    "indian-013",  # unusual angle
    "indian-015",  # tight crop
}


def load_results() -> list[dict]:
    if not RESULTS_PATH.exists():
        return []
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))["results"]


def save_results(results: list[dict]) -> None:
    payload = {"model": MODEL, "temperature": 0, "num_ctx": 8192, "results": results}
    RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(DIFFICULT_CASE_IDS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    manifest = json.loads((DATASET_ROOT / "labels.json").read_text(encoding="utf-8"))
    cases = [entry for entry in manifest["images"] if entry["id"] in DIFFICULT_CASE_IDS]
    if args.case:
        cases = [entry for entry in cases if entry["id"] == args.case]
    results = load_results()
    if args.force and args.case:
        results = [result for result in results if result["id"] != args.case]
        save_results(results)
    completed = {result["id"] for result in results}

    for index, case in enumerate(cases, start=1):
        if case["id"] in completed:
            print(f"SKIP {index}/{len(cases)} {case['id']}", flush=True)
            continue

        image_bytes = prepare_vision_image((DATASET_ROOT / case["file"]).read_bytes())
        body = {
            "model": MODEL,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "format": VisionResult.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 1024},
            "messages": [
                {"role": "system", "content": ImageParserService.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Identify the food items in this image.",
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                },
            ],
        }
        print(f"RUN {index}/{len(cases)} {case['id']} {case['challenge']}", flush=True)
        started = time.perf_counter()
        record = {
            "id": case["id"],
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "elapsed_seconds": None,
            "response": None,
            "error": None,
        }
        try:
            response = requests.post(OLLAMA_URL, json=body, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            message = response.json()["message"]
            record["response"] = message.get("content") or message.get("thinking")
            VisionResult.model_validate_json(record["response"])
            print(f"OK {case['id']} {record['elapsed_seconds']}s", flush=True)
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            record["error"] = f"{type(error).__name__}: {error}"
            if isinstance(error, requests.HTTPError) and error.response is not None:
                record["error"] += f"; response={error.response.text[:1000]}"
            print(f"ERROR {case['id']} {record['error']}", flush=True)
        results.append(record)
        save_results(results)


if __name__ == "__main__":
    main()
