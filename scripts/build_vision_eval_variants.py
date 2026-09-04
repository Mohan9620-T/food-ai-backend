from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

DATASET_ROOT = Path(__file__).parents[1] / "docs" / "vision-eval-dataset" / "images"


def save_low_light(source: str, destination: str) -> None:
    with Image.open(DATASET_ROOT / source) as image:
        ImageEnhance.Brightness(image.convert("RGB")).enhance(0.32).save(
            DATASET_ROOT / destination, quality=90
        )


def save_crop(source: str, destination: str) -> None:
    with Image.open(DATASET_ROOT / source) as image:
        width, height = image.size
        cropped = image.crop((width // 5, height // 6, width, height * 5 // 6))
        cropped.convert("RGB").save(DATASET_ROOT / destination, quality=90)


def save_rotated(source: str, destination: str) -> None:
    with Image.open(DATASET_ROOT / source) as image:
        image.convert("RGB").rotate(24, expand=False).save(DATASET_ROOT / destination, quality=90)


def save_occluded(source: str, destination: str) -> None:
    with Image.open(DATASET_ROOT / source) as image:
        output = image.convert("RGB")
        width, height = output.size
        ImageDraw.Draw(output).rectangle(
            (width * 3 // 5, height // 3, width, height * 2 // 3),
            fill=(35, 35, 35),
        )
        output.save(DATASET_ROOT / destination, quality=90)


def save_crop_rotated(source: str, destination: str) -> None:
    with Image.open(DATASET_ROOT / source) as image:
        width, height = image.size
        cropped = image.crop((width // 8, height // 8, width * 7 // 8, height * 7 // 8))
        cropped.convert("RGB").rotate(24, expand=False).save(DATASET_ROOT / destination, quality=90)


save_low_light("indian-001-idli-sambar.jpg", "indian-010-idli-sambar-low-light.jpg")
save_crop("indian-002-idli-sambar.jpg", "indian-011-idli-sambar-crop.jpg")
save_occluded("indian-003-masala-dosa-vada.jpg", "indian-012-masala-dosa-vada-occluded.jpg")
save_rotated("indian-004-masala-dosa.jpg", "indian-013-masala-dosa-rotated.jpg")
save_low_light("indian-005-vegetable-biryani.jpg", "indian-014-vegetable-biryani-low-light.jpg")
save_crop("indian-006-vegetable-biryani.jpg", "indian-015-vegetable-biryani-crop.jpg")
save_low_light("indian-007-thali.jpg", "indian-016-thali-low-light.jpg")
save_occluded("indian-008-thali.jpg", "indian-017-thali-occluded.jpg")
save_crop_rotated("indian-009-samosa.jpg", "indian-018-samosa-crop-rotated.jpg")
