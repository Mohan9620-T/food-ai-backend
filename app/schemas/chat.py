from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    analysis_status: Literal["complete", "unavailable", "skipped"] | None = None


class ChatDocumentGenerateRequest(BaseModel):
    session_id: int | None = Field(default=None, gt=0)
    mode: Literal["ai", "export"] = Field(
        default="ai",
        description="ai writes content using the model; export saves instruction text without AI.",
    )
    source_document_id: int | None = Field(default=None, gt=0)
    instruction: str = Field(default="", max_length=2000)
    output_format: Literal["pdf", "docx"] = "pdf"

    @model_validator(mode="after")
    def validate_source(self):
        if self.source_document_id is not None:
            if self.mode != "export":
                raise ValueError("source_document_id is only supported for direct file export.")
            return self
        if not self.instruction.strip():
            raise ValueError("Describe the document you want to create.")
        if self.mode == "ai":
            self.instruction = self.instruction.strip()
        return self
