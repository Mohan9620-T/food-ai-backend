from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.profile import UserProfileOut, UserProfileUpsert
from app.services.profile_service import ProfileService
from app.utils.auth_dependency import get_current_user

router = APIRouter(prefix="/profile", tags=["Profile"])
service = ProfileService()


@router.get("/", response_model=UserProfileOut)
def get_profile(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    profile = service.get(db, int(current_user["sub"]))
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not set")
    return service.serialize(profile)


@router.put("/", response_model=UserProfileOut)
def upsert_profile(payload: UserProfileUpsert, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return service.serialize(service.upsert(db, int(current_user["sub"]), payload))
