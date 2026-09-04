import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import get_db
from app.rate_limit import limiter
from app.repositories.diet_plan_repository import DietPlanRepository
from app.schemas.diet_plan import DietPlanOut
from app.services.diet_plan_service import DietPlanGenerationError, DietPlanService
from app.services.profile_service import ProfileService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/diet-plans", tags=["Diet Plans"])
repository = DietPlanRepository()
profile_service = ProfileService()
generator = DietPlanService()
logger = logging.getLogger(__name__)


def _serialize(plan) -> dict:
    totals = []
    for day in range(7):
        items = [item for meal in plan.meals if meal.day_of_week == day for item in meal.items]
        matched = [item for item in items if item.fdc_id is not None]
        totals.append(
            {
                "day_of_week": day,
                "calories": round(sum(item.calories or 0 for item in matched), 2),
                "protein_g": round(sum(item.protein_g or 0 for item in matched), 2),
                "carbs_g": round(sum(item.carbs_g or 0 for item in matched), 2),
                "fat_g": round(sum(item.fat_g or 0 for item in matched), 2),
                "matched_items": len(matched),
                "unmatched_items": len(items) - len(matched),
            }
        )
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "created_at": plan.created_at,
        "target_calories": plan.target_calories,
        "target_protein_g": plan.target_protein_g,
        "target_carbs_g": plan.target_carbs_g,
        "target_fat_g": plan.target_fat_g,
        "meals": plan.meals,
        "daily_totals": totals,
    }


@router.post(
    "/generate",
    response_model=DietPlanOut,
    summary="Generate a diet plan",
    description="Generate and save a seven-day diet plan using the current user's profile.",
    responses={
        400: {"description": "The user must create a profile before generating a plan."},
        401: {"description": "Missing, invalid, or expired access token."},
        422: {"description": "The model could not generate a valid diet plan."},
        429: {"description": "The diet-plan generation rate limit was exceeded."},
    },
)
@limiter.limit(settings.MEAL_CREATE_RATE_LIMIT)
def generate_plan(
    request: Request, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    user_id = int(current_user["sub"])
    profile = profile_service.get(db, user_id)
    if profile is None:
        raise HTTPException(
            status_code=400, detail="Set up your profile before generating a diet plan."
        )
    try:
        days = generator.generate(profile)
    except DietPlanGenerationError as error:
        logger.info("diet_plan.generation_failed", extra={"user_id": user_id})
        raise HTTPException(status_code=422, detail=str(error))
    plan = repository.create(db, user_id, profile, days)
    logger.info("diet_plan.created", extra={"user_id": user_id, "plan_id": plan.id})
    return _serialize(plan)


@router.get(
    "/",
    response_model=list[DietPlanOut],
    summary="List diet plans",
    description="Return all saved diet plans belonging to the authenticated user.",
    responses={401: {"description": "Missing, invalid, or expired access token."}},
)
def list_plans(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return [_serialize(plan) for plan in repository.list_for_user(db, int(current_user["sub"]))]


@router.get(
    "/{plan_id}",
    response_model=DietPlanOut,
    summary="Get a diet plan",
    description="Return one diet plan when it belongs to the authenticated user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The diet plan does not exist or belongs to another user."},
        422: {"description": "The plan ID failed validation."},
    },
)
def get_plan(
    plan_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    plan = repository.get(db, plan_id, int(current_user["sub"]))
    if plan is None:
        raise HTTPException(status_code=404, detail="Diet plan not found")
    return _serialize(plan)


@router.delete(
    "/{plan_id}",
    summary="Delete a diet plan",
    description="Delete one diet plan when it belongs to the authenticated user.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The diet plan does not exist or belongs to another user."},
        422: {"description": "The plan ID failed validation."},
    },
)
def delete_plan(
    plan_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    if not repository.delete(db, plan_id, int(current_user["sub"])):
        raise HTTPException(status_code=404, detail="Diet plan not found")
    return {"detail": "Diet plan deleted"}
