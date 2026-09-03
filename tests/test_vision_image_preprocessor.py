from io import BytesIO

from PIL import Image

from app.services.vision_image_preprocessor import prepare_vision_image


def image_bytes(size: tuple[int, int], image_format: str = "JPEG") -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format=image_format)
    return output.getvalue()


def test_large_image_is_downscaled_for_inference(monkeypatch):
    monkeypatch.setattr("app.config.settings.OLLAMA_VISION_MAX_DIMENSION", 1024)

    prepared = prepare_vision_image(image_bytes((6000, 4000)))

    with Image.open(BytesIO(prepared)) as image:
        assert image.size == (1024, 683)
        assert image.format == "JPEG"


def test_small_image_is_not_reencoded(monkeypatch):
    monkeypatch.setattr("app.config.settings.OLLAMA_VISION_MAX_DIMENSION", 1024)
    original = image_bytes((391, 249), "PNG")

    assert prepare_vision_image(original) is original


def test_invalid_image_preserves_existing_service_behavior():
    original = b"not-an-image"

    assert prepare_vision_image(original) == original
