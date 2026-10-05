from sqlalchemy.orm import Session

from app.models.user_memory import UserMemoryFact


class UserMemoryRepository:
    def list_for_user(self, db: Session, user_id: int) -> list[UserMemoryFact]:
        return (
            db.query(UserMemoryFact)
            .filter(UserMemoryFact.user_id == user_id)
            .order_by(UserMemoryFact.category, UserMemoryFact.created_at)
            .all()
        )

    def add_if_new(
        self,
        db: Session,
        user_id: int,
        category: str,
        fact_text: str,
        source_session_id: int | None = None,
    ) -> UserMemoryFact | None:
        normalized = fact_text.strip().lower()
        existing = (
            db.query(UserMemoryFact)
            .filter(UserMemoryFact.user_id == user_id, UserMemoryFact.category == category)
            .all()
        )
        if any(item.fact_text.strip().lower() == normalized for item in existing):
            return None
        fact = UserMemoryFact(
            user_id=user_id,
            category=category,
            fact_text=fact_text.strip(),
            source_session_id=source_session_id,
        )
        db.add(fact)
        db.commit()
        db.refresh(fact)
        return fact

    def replace_category(
        self,
        db: Session,
        user_id: int,
        category: str,
        fact_text: str,
        source_session_id: int | None = None,
    ) -> UserMemoryFact:
        """Single-valued categories (e.g. the current goal) replace rather than accumulate."""
        db.query(UserMemoryFact).filter(
            UserMemoryFact.user_id == user_id, UserMemoryFact.category == category
        ).delete()
        fact = UserMemoryFact(
            user_id=user_id,
            category=category,
            fact_text=fact_text.strip(),
            source_session_id=source_session_id,
        )
        db.add(fact)
        db.commit()
        db.refresh(fact)
        return fact

    def remove_matching(self, db: Session, user_id: int, category: str, contains_text: str) -> int:
        needle = contains_text.strip().lower()
        if not needle:
            return 0
        matches = (
            db.query(UserMemoryFact)
            .filter(UserMemoryFact.user_id == user_id, UserMemoryFact.category == category)
            .all()
        )
        removed = 0
        for item in matches:
            haystack = item.fact_text.lower()
            if needle in haystack or haystack in needle:
                db.delete(item)
                removed += 1
        if removed:
            db.commit()
        return removed
