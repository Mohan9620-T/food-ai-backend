import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.chat_service import ChatService
from app.services.document.document_operation_registry import DocumentOperation, DocumentType
from app.services.document.document_pipeline_service import (
    DocumentPipelineService,
    DocumentPipelineStep,
    StructuredPipelineStep,
)


class DocumentAutomationPlanningError(ValueError):
    """The model did not return a safe executable document plan."""


@dataclass(frozen=True)
class AvailableDocument:
    document_id: int
    filename: str
    document_type: DocumentType
    is_latest: bool = False


class _PlannedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType | None = None
    operation: DocumentOperation
    input_file: str | None = None
    output_type: DocumentType | None = None
    parameters: dict[str, object] = Field(default_factory=dict)


class _PlanEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "clarification_required"]
    clarifying_question: str | None = None
    steps: list[_PlannedStep] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "ready" and not self.steps:
            raise ValueError("A ready plan must contain at least one step.")
        if self.status == "clarification_required":
            if not (self.clarifying_question or "").strip():
                raise ValueError("A clarification plan must include one question.")
            if self.steps:
                raise ValueError("A clarification plan cannot contain executable steps.")
        return self


@dataclass(frozen=True)
class DocumentAutomationPlan:
    status: Literal["ready", "clarification_required"]
    clarifying_question: str | None
    steps: tuple[DocumentPipelineStep, ...]


class DocumentAutomationService:
    """Use the existing text-provider chain to plan, never to manipulate file bytes."""

    _LATEST_REFERENCE = re.compile(
        r"\b(?:latest|last|most recent|current)\s+(?:document|file|workbook|spreadsheet)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        chat_service: ChatService | None = None,
        pipeline_service: DocumentPipelineService | None = None,
    ) -> None:
        self.chat_service = chat_service or ChatService()
        self.pipeline_service = pipeline_service or DocumentPipelineService()

    def plan(
        self,
        instruction: str,
        documents: tuple[AvailableDocument, ...],
        *,
        explicit_source: bool = False,
    ) -> DocumentAutomationPlan:
        if not documents:
            return self._clarification(
                "Please upload or select a document before asking me to automate it."
            )

        response = self.chat_service.chat(
            self._planning_prompt(instruction, documents),
            history=[],
            reference_history=[],
        )
        envelope = self._parse_envelope(response)
        if envelope.status == "clarification_required":
            return self._clarification(str(envelope.clarifying_question).strip())

        filenames = {document.filename.casefold(): document.filename for document in documents}
        requested_steps: list[StructuredPipelineStep] = []
        for index, item in enumerate(envelope.steps):
            input_file = Path(item.input_file).name if item.input_file else None
            if input_file is not None:
                actual_filename = filenames.get(input_file.casefold())
                if actual_filename is None:
                    return self._clarification(
                        f"Which uploaded document should I use? I couldn't find {input_file}."
                    )
                input_file = actual_filename
            elif (
                index == 0
                and len(documents) > 1
                and not explicit_source
                and not self._LATEST_REFERENCE.search(instruction)
            ):
                return self._clarification("Which uploaded document should I use for this request?")
            requested_steps.append(
                StructuredPipelineStep(
                    document_type=item.document_type,
                    operation=item.operation,
                    input_file=input_file,
                    output_type=item.output_type,
                    parameters=item.parameters,
                )
            )

        default_source = next(
            (document for document in documents if document.is_latest), documents[-1]
        )
        steps = self.pipeline_service.plan_structured(
            tuple(requested_steps), input_filename=default_source.filename
        )
        return DocumentAutomationPlan(status="ready", clarifying_question=None, steps=steps)

    @staticmethod
    def _clarification(question: str) -> DocumentAutomationPlan:
        return DocumentAutomationPlan(
            status="clarification_required",
            clarifying_question=question,
            steps=(),
        )

    @staticmethod
    def _parse_envelope(response: str) -> _PlanEnvelope:
        text = response.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            return _PlanEnvelope.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError, TypeError) as error:
            raise DocumentAutomationPlanningError(
                "I couldn't build a safe document plan. Please describe the desired result "
                "more specifically."
            ) from error

    @staticmethod
    def _planning_prompt(instruction: str, documents: tuple[AvailableDocument, ...]) -> str:
        files = [
            {
                "filename": document.filename,
                "document_type": document.document_type.value,
                "is_latest": document.is_latest,
            }
            for document in documents
        ]
        schema = {
            "status": "ready | clarification_required",
            "clarifying_question": "string or null",
            "steps": [
                {
                    "document_type": "xlsx | csv | docx | pdf | pptx | txt | markdown",
                    "operation": (
                        "convert_document | format_workbook | filter_column | "
                        "split_by_category | expand_dish_by_dietary_category | "
                        "extract_table_to_excel"
                    ),
                    "input_file": "exact available filename for step 1; null means prior output",
                    "output_type": "xlsx | csv | pdf | txt | markdown",
                    "parameters": {"column": "required only for filter_column"},
                }
            ],
        }
        return (
            "You are a document automation planner. Return ONLY one JSON object and no Markdown. "
            "Infer the user's desired result and translate it into the smallest ordered list of "
            "registered operations. Use only the allowed operation values in the schema. The first "
            "step must name an exact available filename unless there is only one file or the user "
            "explicitly says latest/current. Later steps should use null input_file to consume the "
            "previous output. Use clarification_required with one short question when the source, "
            "category column, filter column, target format, or desired result is ambiguous. Never "
            "invent a filename, conversion, parameter, or operation. Do not plan read, summarize, "
            "or create operations because they do not produce a pipeline file.\n"
            f"JSON schema: {json.dumps(schema, ensure_ascii=True)}\n"
            f"Available files: {json.dumps(files, ensure_ascii=True)}\n"
            f"User request: {json.dumps(instruction, ensure_ascii=True)}"
        )
