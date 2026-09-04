import logging
from dataclasses import replace
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.repositories.meal_repository import MealRepository
from app.schemas.meal import DailyTotalsOut, MealCreate, MealLogOut
from app.services.image_parser_service import (
    ImageParseError,
    ImageParserService,
    VisionModelUnavailableError,
)
from app.services.image_validation import InvalidImageError, validate_image_content
from app.services.nutrition_parser_service import (
    NutritionParseError,
    NutritionParserService,
    ParsedFoodItem,
)
from app.services.usda_nutrition_service import UsdaNutritionService
from app.utils.auth_dependency import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/meals", tags=["Meal Log"])
repository = MealRepository()
parser = NutritionParserService()
nutrition = UsdaNutritionService()
image_parser = ImageParserService()
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _user_id(current_user: dict) -> int:
    return int(current_user["sub"])


def _save_parsed_meal(
    db: Session,
    user_id: int,
    raw_description: str,
    logged_at: datetime,
    parsed_items: list[ParsedFoodItem],
    source: str,
):
    resolved_items = [nutrition.lookup(item) for item in parsed_items]
    if source == "image":
        resolved_items = [
            replace(item, food_name=f"Unidentified food ({item.food_name})")
            if item.fdc_id is None
            else item
            for item in resolved_items
        ]
    unmatched_count = sum(item.fdc_id is None for item in resolved_items)
    meal = repository.create(
        db,
        user_id,
        raw_description,
        logged_at,
        resolved_items,
        source=source,
    )
    logger.info(
        "meal.created",
        extra={
            "user_id": user_id,
            "meal_id": meal.id,
            "source": source,
            "item_count": len(resolved_items),
            "unmatched_count": unmatched_count,
        },
    )
    return meal


@router.post(
    "/",
    response_model=MealLogOut,
    summary="Log a meal from text",
    description="Parse a meal description, resolve nutrition data, and save it for the current user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        422: {"description": "The payload is invalid or the description cannot be parsed."},
        429: {"description": "The meal creation rate limit was exceeded."},
    },
)
@limiter.limit(settings.MEAL_CREATE_RATE_LIMIT)
def create_meal(
    request: Request,
    payload: MealCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _user_id(current_user)
    try:
        parsed_items = parser.parse(payload.description)
    except NutritionParseError as error:
        logger.info("meal.parse_failed", extra={"user_id": user_id})
        raise HTTPException(status_code=422, detail=str(error))

    return _save_parsed_meal(
        db,
        user_id,
        payload.description,
        payload.logged_at or datetime.now(timezone.utc),
        parsed_items,
        "text",
    )


@router.post(
    "/from-image",
    response_model=MealLogOut,
    summary="Log a meal from an image",
    description="Identify foods in an uploaded image, resolve nutrition data, and save the meal.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        413: {"description": "The uploaded image exceeds 8 MB."},
        415: {"description": "The uploaded file is not a supported image type."},
        422: {"description": "The image or form data is invalid, empty, or cannot be parsed."},
        429: {"description": "The meal creation rate limit was exceeded."},
        503: {"description": "The configured Ollama vision model is unavailable."},
    },
)
@limiter.limit(settings.MEAL_CREATE_RATE_LIMIT)
async def create_meal_from_image(
    request: Request,
    image: UploadFile = File(...),
    logged_at: datetime | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _user_id(current_user)
    try:
        content_type = (image.content_type or "").lower().split(";", 1)[0]
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Unsupported file type. Upload a JPEG, PNG, WebP, or GIF image.",
            )
        image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image is too large. Maximum size is 8 MB.")
        if not image_bytes:
            raise HTTPException(status_code=422, detail="The uploaded image is empty.")
        try:
            validate_image_content(image_bytes, content_type)
        except InvalidImageError as error:
            raise HTTPException(status_code=422, detail=str(error))

        try:
            parsed_items = image_parser.parse(image_bytes)
        except ImageParseError as error:
            logger.info("meal.image_parse_failed", extra={"user_id": user_id})
            raise HTTPException(status_code=422, detail=str(error))
        except VisionModelUnavailableError as error:
            logger.warning("meal.vision_unavailable", extra={"user_id": user_id})
            raise HTTPException(status_code=503, detail=str(error))

        description = "Photo: " + ", ".join(
            f"{item.quantity:g} {item.unit} {item.food_name}" for item in parsed_items
        )
        return _save_parsed_meal(
            db,
            user_id,
            description,
            logged_at or datetime.now(timezone.utc),
            parsed_items,
            "image",
        )
    finally:
        await image.close()


@router.get(
    "/",
    response_model=list[MealLogOut],
    summary="List meal logs",
    description="Return the authenticated user's meal logs, optionally filtered by date range.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        422: {"description": "A date is invalid or the end date precedes the start date."},
    },
)
def list_meals(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must not be before start_date")
    return repository.list_for_user(db, _user_id(current_user), start_date, end_date)


@router.get(
    "/totals",
    response_model=DailyTotalsOut,
    summary="Get daily nutrition totals",
    description="Aggregate calories and macronutrients for the current user on one date.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        422: {"description": "The required date is missing or invalid."},
    },
)
def daily_totals(
    date: date = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return repository.get_daily_totals(db, _user_id(current_user), date)


@router.delete(
    "/{meal_id}",
    summary="Delete a meal log",
    description="Delete one meal log when it belongs to the authenticated user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The meal log does not exist or belongs to another user."},
        422: {"description": "The meal ID failed validation."},
    },
)
def delete_meal(
    meal_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not repository.delete(db, meal_id, _user_id(current_user)):
        raise HTTPException(status_code=404, detail="Meal log not found")
    return {"detail": "Meal log deleted"}
