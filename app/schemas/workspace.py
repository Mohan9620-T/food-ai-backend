from typing import Literal

from pydantic import BaseModel, Field, field_validator


class WorkspaceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class ConversationWrite(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=255)


class ScopeWrite(BaseModel):
    mode: Literal["all", "selected"]


class BookmarkWrite(BaseModel):
    target_type: Literal["answer", "document", "conversation", "artifact"]
    target_id: int = Field(gt=0)
    note: str = Field(default="", max_length=500)
