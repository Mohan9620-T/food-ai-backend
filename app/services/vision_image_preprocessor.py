from io import BytesIO
import logging

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings

logger = logging.getLogger(__name__)


def prepare_vision_image(image_bytes: bytes) -> bytes:
    """Return a smaller inference copy while leaving the stored original untouched."""
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source)
            if max(image.size) <= settings.OLLAMA_VISION_MAX_DIMENSION:
                return image_bytes

            original_size = image.size
            image.thumbnail(
                (settings.OLLAMA_VISION_MAX_DIMENSION,) * 2,
                Image.Resampling.LANCZOS,
            )
            if image.mode != "RGB":
                image = image.convert("RGB")

            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            logger.info(
                "vision.image_downscaled",
                extra={"original_size": original_size, "inference_size": image.size},
            )
            return output.getvalue()
    except (OSError, UnidentifiedImageError):
        logger.warning("vision.image_preprocessing_skipped")
        return image_bytes
