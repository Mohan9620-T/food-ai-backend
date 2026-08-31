from sqlalchemy.orm import Session

from app.models.user_profile import UserProfile


class ProfileRepository:
    def get(self, db: Session, user_id: int) -> UserProfile | None:
        return db.query(UserProfile).filter(UserProfile.user_id == user_id).first()

    def upsert(self, db: Session, user_id: int, values: dict) -> UserProfile:
        profile = self.get(db, user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id, **values)
            db.add(profile)
        else:
            for field, value in values.items():
                setattr(profile, field, value)
        db.commit()
        db.refresh(profile)
        return profile
