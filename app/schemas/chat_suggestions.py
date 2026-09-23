from pydantic import BaseModel, ConfigDict, Field


class SuggestionRequest(BaseModel):
    answer_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class FollowUpSuggestion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    label: str = Field(min_length=1, max_length=70)
    prompt: str = Field(min_length=1, max_length=320)


class SuggestionResponse(BaseModel):
    message_id: int
    suggestions: list[FollowUpSuggestion] = Field(default_factory=list, max_length=2)
