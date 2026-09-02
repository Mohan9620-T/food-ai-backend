from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.profile import UserProfileOut, UserProfileUpsert
from app.services.profile_service import ProfileService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/profile", tags=["Profile"])
service = ProfileService()


@router.get(
    "/",
    response_model=UserProfileOut,
    summary="Get the current user's profile",
    description="Return the authenticated user's nutrition and lifestyle profile.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        404: {"description": "The user has not created a profile."},
    },
)
def get_profile(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    profile = service.get(db, int(current_user["sub"]))
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not set")
    return service.serialize(profile)


@router.put(
    "/",
    response_model=UserProfileOut,
    summary="Create or update the current user's profile",
    description="Persist the authenticated user's nutrition and lifestyle profile.",
    responses={
        401: {"description": "Missing, invalid, or expired access token."},
        422: {"description": "The profile payload failed validation."},
    },
)
def upsert_profile(payload: UserProfileUpsert, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.serialize(service.upsert(db, int(current_user["sub"]), payload))
