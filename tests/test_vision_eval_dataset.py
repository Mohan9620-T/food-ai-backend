import json
from pathlib import Path

from PIL import Image

DATASET_ROOT = Path(__file__).parents[1] / "docs" / "vision-eval-dataset"


def test_vision_evaluation_dataset_is_complete_and_decodable():
    manifest = json.loads((DATASET_ROOT / "labels.json").read_text(encoding="utf-8"))
    entries = manifest["images"]

    assert 15 <= manifest["image_count"] <= 25
    assert manifest["image_count"] == len(entries)
    assert len({entry["id"] for entry in entries}) == len(entries)
    assert len({entry["file"] for entry in entries}) == len(entries)

    for entry in entries:
        assert entry["expected_items"]
        assert entry["difficulty"] in {"easy", "medium", "hard"}
        assert entry["creator"]
        assert entry["license"]
        assert entry["source_page"].startswith("https://commons.wikimedia.org/")

        image_path = DATASET_ROOT / entry["file"]
        assert image_path.is_file(), f"Missing evaluation image: {entry['file']}"
        with Image.open(image_path) as image:
            image.verify()
