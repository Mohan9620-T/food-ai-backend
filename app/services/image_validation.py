from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError


class InvalidImageError(ValueError):
    pass


CONTENT_TYPE_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
MAX_IMAGE_PIXELS = 25_000_000


def validate_image_content(image_bytes: bytes, declared_content_type: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            previous_limit = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
            try:
                with Image.open(BytesIO(image_bytes)) as image:
                    detected_content_type = CONTENT_TYPE_BY_FORMAT.get(image.format or "")
                    image.verify()
            finally:
                Image.MAX_IMAGE_PIXELS = previous_limit
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise InvalidImageError("Uploaded file is not a valid supported image.") from error

    if detected_content_type is None or detected_content_type != declared_content_type:
        raise InvalidImageError(
            "Uploaded image content does not match its declared file type."
        )
