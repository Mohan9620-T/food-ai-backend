from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    reference_history: list[ChatHistoryMessage] = Field(default_factory=list)
    # Internal optional image payload for future multimodal transports. The existing
    # JSON /chat endpoint remains text-only; uploaded files use /chat/vision.
    image_data: bytes | None = Field(default=None, exclude=True, repr=False)


class ChatResponse(BaseModel):
    response: str
    session_id: int


class ChatDocumentAttachmentOut(BaseModel):
    id: int
    filename: str
    content_type: str
    file_size: int
    kind: Literal["uploaded", "generated"]

    class Config:
        from_attributes = True


class ChatDocumentResponse(ChatResponse):
    attachment: ChatDocumentAttachmentOut


class ChatDocumentGenerateRequest(BaseModel):
    session_id: int | None = Field(default=None, gt=0)
    instruction: str = Field(min_length=1, max_length=2000)
    output_format: Literal["pdf", "docx"] = "pdf"

    @field_validator("instruction")
    @classmethod
    def strip_instruction(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe the document you want to create.")
        return value.strip()
