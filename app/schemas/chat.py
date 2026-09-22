from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.utils.document_clarification import spreadsheet_layout_question


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
    provenance: Literal["uploaded_source", "general_knowledge"] | None = None
    source_document_ids: list[int] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ChatDocumentResponse(ChatResponse):
    attachment: ChatDocumentAttachmentOut | None = None
    analysis_status: Literal["complete", "unavailable", "skipped"] | None = None
    fidelity: str | None = None
    fidelity_note: str | None = None


class ChatSpreadsheetOperationRequest(BaseModel):
    session_id: int = Field(gt=0)
    instruction: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def normalize_instruction(self):
        self.instruction = self.instruction.strip()
        if not self.instruction:
            raise ValueError("Describe the spreadsheet update you want me to apply.")
        return self


class ChatDocumentGenerateRequest(BaseModel):
    session_id: int | None = Field(default=None, gt=0)
    mode: Literal["ai", "export"] = Field(
        default="ai",
        description="ai writes content using the model; export saves instruction text without AI.",
    )
    source_document_id: int | None = Field(default=None, gt=0)
    instruction: str = Field(default="", max_length=2000)
    output_format: Literal["pdf", "docx", "xlsx", "csv", "pptx", "txt", "markdown"] = "pdf"
    filename: str | None = Field(
        default=None,
        max_length=255,
        description="Optional output filename. Paths and unsafe characters are removed.",
    )

    @model_validator(mode="after")
    def validate_source(self):
        if self.filename is not None:
            self.filename = self.filename.strip() or None
        if self.source_document_id is not None:
            if self.mode == "ai" and not self.instruction.strip():
                raise ValueError("Describe the document you want to create from the source file.")
            if self.mode == "ai":
                self.instruction = self.instruction.strip()
            return self
        if not self.instruction.strip():
            raise ValueError("Describe the document you want to create.")
        if self.mode == "ai":
            self.instruction = self.instruction.strip()
        return self


class ChatDocumentPipelineRequest(BaseModel):
    session_id: int = Field(gt=0)
    instruction: str = Field(min_length=1, max_length=4000)
    source_document_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def normalize_instruction(self):
        self.instruction = self.instruction.strip()
        if not self.instruction:
            raise ValueError("Describe the document steps you want me to perform.")
        return self


class ChatDocumentPipelineStepOut(BaseModel):
    position: int
    operation: str
    source_document_id: int
    output_document_id: int
    filename: str
    fidelity: str
    fidelity_note: str | None = None


class ChatDocumentPipelineResponse(ChatDocumentResponse):
    latest_document_id: int
    steps: list[ChatDocumentPipelineStepOut]


class ChatDocumentAutomationRequest(BaseModel):
    session_id: int = Field(gt=0)
    instruction: str = Field(min_length=1, max_length=4000)
    source_document_id: int | None = Field(default=None, gt=0)
    source_mode: Literal["auto", "description"] = "auto"
    # Accepted for older clients; ready requests now execute without a confirmation gate.
    confirm: bool = False

    @model_validator(mode="after")
    def normalize_instruction(self):
        self.instruction = self.instruction.strip()
        if not self.instruction:
            raise ValueError("Describe the document result you want.")
        if self.source_mode == "description" and self.source_document_id is not None:
            raise ValueError("Choose written requirements or a source document, not both.")
        return self


class ChatDocumentAutomationStepOut(BaseModel):
    position: int
    operation: str
    source_document_ids: list[int] = Field(default_factory=list)
    output_type: str
    parameters: dict[str, object] = Field(default_factory=dict)
    output_document_id: int | None = None
    filename: str | None = None
    status: Literal["completed", "failed", "not_started"]
    detail: str | None = None
    fidelity: str | None = None
    fidelity_note: str | None = None


class ClarificationOption(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    description: str | None = None
    recommended: bool = False


class ClarificationQuestionOut(BaseModel):
    question: str
    options: list[ClarificationOption] = Field(default_factory=list)
    allow_other: bool = True


class ChatDocumentAutomationResponse(BaseModel):
    response: str
    session_id: int
    status: Literal["done", "clarification_required", "ready_for_review", "partial", "failed"]
    plan_summary: str | None = None
    clarification: ClarificationQuestionOut | None = None
    attachments: list[ChatDocumentAttachmentOut] = Field(default_factory=list)
    latest_document_id: int | None = None
    steps: list[ChatDocumentAutomationStepOut] = Field(default_factory=list)

    @model_validator(mode="after")
    def include_clarification_question(self):
        # Open questions and older saved turns must expose the same composer contract.
        if self.status == "clarification_required" and self.clarification is None:
            self.clarification = ClarificationQuestionOut(question=self.response)
        return self


class DocumentHistoryChoice(BaseModel):
    question: str
    answer: str


class ChatDocumentAutomationState(BaseModel):
    instruction: str
    request_instruction: str
    source_document_id: int | None = None
    source_mode: Literal["auto", "description"] = "auto"
    confirmed: bool = False
    choices: list[DocumentHistoryChoice] = Field(default_factory=list)
    response: ChatDocumentAutomationResponse

    @model_validator(mode="after")
    def restore_layout_choices(self):
        clarification = self.response.clarification
        context = " ".join(
            [
                self.request_instruction,
                self.response.response,
                *(choice.question for choice in self.choices),
            ]
        ).casefold()
        if (
            self.response.status == "clarification_required"
            and clarification is not None
            and not clarification.options
            and any(word in context for word in ("excel", "xlsx", "spreadsheet"))
        ):
            layout = spreadsheet_layout_question(self.request_instruction)
            if layout is not None:
                self.response.clarification = ClarificationQuestionOut(
                    question=layout.question,
                    options=[
                        ClarificationOption(
                            id=option.id, label=option.label, description=option.description
                        )
                        for option in layout.options
                    ],
                )
        return self
