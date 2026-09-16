import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas.chat import ChatHistoryMessage, ClarificationOption
from app.services.chat_service import ChatService
from app.services.document.document_operation_registry import (
    DocumentOperation,
    DocumentType,
    InputArity,
)
from app.services.document.document_pipeline_service import (
    DocumentPipelineService,
    DocumentPipelineStep,
    StructuredPipelineStep,
)
from app.services.document.document_references import references_document, references_image

logger = logging.getLogger(__name__)


class DocumentAutomationPlanningError(ValueError):
    """The model did not return a safe executable document plan."""


@dataclass(frozen=True)
class AvailableDocument:
    document_id: int
    filename: str
    document_type: DocumentType
    is_latest: bool = False
    kind: Literal["uploaded", "generated"] = "uploaded"


class _PlannedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: DocumentType | None = None
    operation: DocumentOperation
    input_file: str | list[str] | None = None
    output_type: DocumentType | Literal["text_response"] | None = None
    parameters: dict[str, object] = Field(default_factory=dict)


class _PlanEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "clarification_required"]
    clarifying_question: str | None = None
    clarification_options: list[ClarificationOption] | None = Field(default=None, max_length=5)
    steps: list[_PlannedStep] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_status(self):
        if self.clarification_options:
            if self.status != "clarification_required":
                raise ValueError("Options require a clarification question.")
            if len(self.clarification_options) < 2:
                raise ValueError("Provide at least two choices, or omit options.")
            if len({option.id for option in self.clarification_options}) != len(
                self.clarification_options
            ):
                raise ValueError("Clarification option IDs must be unique.")
            if sum(option.recommended for option in self.clarification_options) > 1:
                raise ValueError("At most one option can be recommended.")
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
    clarification_options: tuple[ClarificationOption, ...] = ()


class DocumentAutomationService:
    """Plan natural-language requests through the existing provider and operation registry."""

    _LATEST_REFERENCE = re.compile(
        r"\b(?:this|that|latest|last|most recent|current)\s+"
        r"(?:document|file|workbook|spreadsheet|pdf|word document)?\b",
        re.IGNORECASE,
    )
    _MULTI_REFERENCE = re.compile(
        r"\b(?:both|all|these|those)\s+(?:(?:pdf|word|excel)\s+)?"
        r"(?:files|documents|pdfs|workbooks)\b",
        re.IGNORECASE,
    )
    _ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}
    _MULTI_STEP_CONNECTOR = re.compile(r"\b(?:then|after that|followed by)\b|;", re.IGNORECASE)
    _DOCUMENT_QUESTION = re.compile(
        r"(?:\?|\b(?:what|which|who|when|where|why|how|does|do|is|are|can)\b)"
        r".*\b(?:document|file|pdf|word|docx|excel|xlsx|csv|powerpoint|pptx|"
        r"spreadsheet|workbook|table|content)\b",
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
        conversation_history: list[ChatHistoryMessage] | None = None,
    ) -> DocumentAutomationPlan:
        source_instruction = instruction
        if len(instruction.split()) <= 8 and not references_document(instruction):
            source_instruction = (
                next(
                    (
                        message.content
                        for message in reversed(conversation_history or [])
                        if message.role == "user"
                        and self.pipeline_service.intent_service.is_creation_request(
                            message.content
                        )
                    ),
                    instruction,
                )
                if not self.pipeline_service.intent_service.is_creation_request(instruction)
                else instruction
            )
        if (
            not explicit_source
            and self.pipeline_service.intent_service.is_creation_request(source_instruction)
            and not references_document(
                source_instruction, (document.filename for document in documents), creation=True
            )
        ):
            # The requested output format does not select an old attachment.
            # A new subject can be written from general knowledge in this chat.
            documents = ()
        if (
            references_image(instruction)
            and references_document(instruction, creation=True)
            and not any(document.document_type == DocumentType.IMAGE for document in documents)
        ):
            return self._clarification("Please upload the image you want me to use.")
        direct_plan = self._plan_unambiguous_creation(
            instruction,
            documents,
            explicit_source=explicit_source,
            conversation_history=conversation_history or [],
        )
        if direct_plan is not None:
            return direct_plan

        direct_conversion = self._plan_unambiguous_conversion(
            instruction,
            documents,
            explicit_source=explicit_source,
        )
        if direct_conversion is not None:
            return direct_conversion

        direct_question = self._plan_unambiguous_question(
            instruction,
            documents,
            explicit_source=explicit_source,
        )
        if direct_question is not None:
            return direct_question

        prompt = self._planning_prompt(instruction, documents, conversation_history or [])
        response = self.chat_service.chat(
            prompt, history=conversation_history or [], reference_history=[]
        )
        envelope = self._parse_envelope(response)
        if envelope.status == "clarification_required":
            return DocumentAutomationPlan(
                status="clarification_required",
                clarifying_question=str(envelope.clarifying_question).strip(),
                steps=(),
                clarification_options=tuple(envelope.clarification_options or []),
            )

        if (
            len(envelope.steps) == 1
            and envelope.steps[0].operation == DocumentOperation.CREATE_DOCUMENT
        ):
            # An optional name invented during re-planning must not change a
            # previously reviewed plan. Keep names the user actually supplied;
            # otherwise let the existing generator name the finished content.
            user_text = "\n".join(
                [
                    instruction,
                    *(
                        message.content
                        for message in conversation_history or []
                        if message.role == "user"
                    ),
                ]
            ).casefold()
            parameters = envelope.steps[0].parameters
            for key in ("filename", "title"):
                value = parameters.get(key)
                if isinstance(value, str) and value.casefold() not in user_text:
                    parameters.pop(key)

        filenames = {document.filename.casefold(): document.filename for document in documents}
        requested_steps: list[StructuredPipelineStep] = []
        for index, item in enumerate(envelope.steps):
            definition = self.pipeline_service.intent_service.registry.get(item.operation)
            if definition is None:
                raise DocumentAutomationPlanningError(
                    "The requested document action is not available."
                )
            resolved = self._resolve_input_reference(
                item.input_file,
                instruction,
                documents,
                filenames,
                index=index,
                explicit_source=explicit_source,
                multi_input=definition.input_arity == InputArity.MULTI,
            )
            if isinstance(resolved, str) and resolved.startswith("clarify:"):
                return self._clarification(resolved.removeprefix("clarify:"))
            output_type = None if item.output_type == "text_response" else item.output_type
            requested_steps.append(
                StructuredPipelineStep(
                    document_type=item.document_type,
                    operation=item.operation,
                    input_file=resolved,
                    output_type=output_type,
                    parameters=item.parameters,
                )
            )

        default_source = next(
            (document for document in documents if document.is_latest),
            documents[-1] if documents else None,
        )
        steps = self.pipeline_service.plan_structured(
            tuple(requested_steps),
            input_filename=default_source.filename if default_source else None,
            request_instruction=instruction,
        )
        return DocumentAutomationPlan(status="ready", clarifying_question=None, steps=steps)

    def _plan_unambiguous_question(
        self,
        instruction: str,
        documents: tuple[AvailableDocument, ...],
        *,
        explicit_source: bool,
    ) -> DocumentAutomationPlan | None:
        if not self._DOCUMENT_QUESTION.search(instruction) or self._MULTI_STEP_CONNECTOR.search(
            instruction
        ):
            return None
        intent_service = self.pipeline_service.intent_service
        if (
            intent_service.is_creation_request(instruction)
            or intent_service.is_conversion_request(instruction)
            or any(
                intent_service.is_modification_request(instruction, document.filename)
                for document in documents
            )
        ):
            # "Can you add ... and create/update the Word document?" is a file
            # action, even though it is phrased as a question.
            return None
        resolved = self._resolve_input_reference(
            None,
            instruction,
            documents,
            {document.filename.casefold(): document.filename for document in documents},
            index=0,
            explicit_source=explicit_source,
            multi_input=False,
        )
        if resolved is None:
            return None
        if isinstance(resolved, str) and resolved.startswith("clarify:"):
            return self._clarification(resolved.removeprefix("clarify:"))

        default_source = next(
            (document for document in documents if document.is_latest),
            documents[-1] if documents else None,
        )
        steps = self.pipeline_service.plan_structured(
            (
                StructuredPipelineStep(
                    document_type=None,
                    operation=DocumentOperation.ANSWER_DOCUMENT_QUESTION,
                    input_file=resolved,
                    output_type=None,
                    parameters={"question": instruction},
                ),
            ),
            input_filename=default_source.filename if default_source else None,
            request_instruction=instruction,
        )
        return DocumentAutomationPlan(status="ready", clarifying_question=None, steps=steps)

    def _plan_unambiguous_creation(
        self,
        instruction: str,
        documents: tuple[AvailableDocument, ...],
        *,
        explicit_source: bool,
        conversation_history: list[ChatHistoryMessage],
    ) -> DocumentAutomationPlan | None:
        """Build a registry-validated plan for a simple, explicit create request.

        This keeps common requests deterministic while leaving edits, conversions,
        ambiguous targets, and multi-step workflows to the model planner.
        """
        if not documents and not re.search(
            r"\b\d{4}\s*(?:to|through|until|-|–)\s*\d{4}\b", instruction, re.IGNORECASE
        ):
            # Let the semantic planner assess missing details. A creation request
            # with an explicit date range and target has enough scope to review.
            return None
        if self._MULTI_STEP_CONNECTOR.search(instruction):
            return None
        intent_service = self.pipeline_service.intent_service
        output_types = self.pipeline_service.intent_service.registry.document_types_in_text(
            instruction
        )
        image_source = any(document.document_type == DocumentType.IMAGE for document in documents)
        if image_source:
            output_types = [kind for kind in output_types if kind != DocumentType.IMAGE]
        selected = next(
            (document for document in documents if document.is_latest),
            documents[-1] if documents else None,
        )
        different_format_edit = (
            selected is not None
            and len(output_types) == 1
            and output_types[0] != selected.document_type
            and intent_service.is_modification_request(instruction, selected.filename)
        )
        image_conversion = image_source and intent_service.is_conversion_request(instruction)
        image_extraction = (
            image_source
            and len(output_types) == 1
            and bool(re.search(r"\bextract\b", instruction, re.IGNORECASE))
        )
        if (
            not intent_service.is_creation_request(instruction)
            and not different_format_edit
            and not image_conversion
            and not image_extraction
        ):
            return None
        if image_source and not output_types:
            return self._clarification(
                "Which file should I create from the image: Excel, Word, or PDF?"
            )
        if len(output_types) > 1 and documents:
            # Source formats in "create Word ... from/summarizing this PDF"
            # are not additional requested outputs.
            clauses = re.split(
                r"\b(?:from|using|based on|summarizing|summarising|(?:summary|copy|version)\s+of)\b",
                instruction,
                maxsplit=1,
                flags=re.IGNORECASE,
            )
            if len(clauses) == 2 and re.search(
                r"\b(?:this|that|uploaded|attached|provided)\b", clauses[1], re.IGNORECASE
            ):
                registry = self.pipeline_service.intent_service.registry
                target_types = registry.document_types_in_text(clauses[0])
                source_types = registry.document_types_in_text(clauses[1])
                available_types = {document.document_type for document in documents}
                if len(target_types) == 1 and source_types and set(source_types) <= available_types:
                    output_types = target_types
        if len(output_types) != 1:
            return None
        output_type = output_types[0]
        create_definition = self.pipeline_service.intent_service.registry.get(
            DocumentOperation.CREATE_DOCUMENT
        )
        if create_definition is None or output_type not in create_definition.output_types:
            return None

        resolved = self._resolve_input_reference(
            None,
            instruction,
            documents,
            {document.filename.casefold(): document.filename for document in documents},
            index=0,
            explicit_source=explicit_source,
            multi_input=False,
        )
        if isinstance(resolved, str) and resolved.startswith("clarify:"):
            return self._clarification(resolved.removeprefix("clarify:"))

        default_source = next(
            (document for document in documents if document.is_latest),
            documents[-1] if documents else None,
        )
        # A plain request for a Word version of an uploaded PDF is a conversion:
        # preserve all extracted content and do not depend on model availability.
        # Summaries, new writing and requested edits still use content generation.
        source_document = next(
            (document for document in documents if document.filename == resolved),
            default_source if resolved is None else None,
        )
        content_changes = re.compile(
            r"\b(?:summari\w*|summary|analy\w*|rewrite|reword|add|append|extra|report|"
            r"brochure|explain|improve|translate|change|remove|delete|only|section|chapter|"
            r"named|titled|called)\b",
            re.IGNORECASE,
        )
        prior_changes = re.compile(
            r"\b(?:add|append|rewrite|reword|translate|change|remove|delete|insert|replace)\b",
            re.IGNORECASE,
        )
        copy_pdf_to_word = (
            source_document is not None
            and source_document.document_type == DocumentType.PDF
            and output_type == DocumentType.DOCX
            and not content_changes.search(instruction)
            and not any(
                message.role == "user" and prior_changes.search(message.content)
                for message in conversation_history
            )
        )
        steps = self.pipeline_service.plan_structured(
            (
                StructuredPipelineStep(
                    document_type=None,
                    operation=(
                        DocumentOperation.CONVERT_DOCUMENT
                        if copy_pdf_to_word
                        else DocumentOperation.CREATE_DOCUMENT
                    ),
                    input_file=resolved,
                    output_type=output_type,
                    parameters={},
                ),
            ),
            input_filename=default_source.filename if default_source else None,
            request_instruction=instruction,
        )
        return DocumentAutomationPlan(status="ready", clarifying_question=None, steps=steps)

    def _plan_unambiguous_conversion(
        self,
        instruction: str,
        documents: tuple[AvailableDocument, ...],
        *,
        explicit_source: bool,
    ) -> DocumentAutomationPlan | None:
        if not self.pipeline_service.intent_service.is_conversion_request(
            instruction
        ) or self._MULTI_STEP_CONNECTOR.search(instruction):
            return None
        resolved = self._resolve_input_reference(
            None,
            instruction,
            documents,
            {document.filename.casefold(): document.filename for document in documents},
            index=0,
            explicit_source=explicit_source,
            multi_input=False,
        )
        if resolved is None:
            return None
        if isinstance(resolved, str) and resolved.startswith("clarify:"):
            return self._clarification(resolved.removeprefix("clarify:"))
        if not isinstance(resolved, str):
            return None

        intent = self.pipeline_service.intent_service.resolve(
            instruction,
            input_file=resolved,
        )
        if intent.operation != DocumentOperation.CONVERT_DOCUMENT or intent.output_type is None:
            return None
        steps = self.pipeline_service.plan_structured(
            (
                StructuredPipelineStep(
                    document_type=intent.document_type,
                    operation=intent.operation,
                    input_file=resolved,
                    output_type=intent.output_type,
                    parameters=intent.parameters,
                ),
            ),
            input_filename=resolved,
            request_instruction=instruction,
        )
        return DocumentAutomationPlan(status="ready", clarifying_question=None, steps=steps)

    def _resolve_input_reference(
        self,
        planned: str | list[str] | None,
        instruction: str,
        documents: tuple[AvailableDocument, ...],
        filenames: dict[str, str],
        *,
        index: int,
        explicit_source: bool,
        multi_input: bool,
    ) -> str | list[str] | None:
        if planned is not None:
            values = planned if isinstance(planned, list) else [planned]
            resolved: list[str] = []
            for value in values:
                filename = filenames.get(Path(value).name.casefold())
                if filename is None:
                    return f"clarify:Which uploaded document should I use? I couldn't find {Path(value).name}."
                resolved.append(filename)
            if index == 0 and len(documents) > 1 and not explicit_source:
                normalized = instruction.casefold()
                named_by_user = any(filename.casefold() in normalized for filename in resolved)
                ordinal_reference = bool(
                    re.search(r"\b(?:first|1st|second|2nd|third|3rd)\b", normalized)
                )
                type_references = (
                    self.pipeline_service.intent_service.registry.document_types_in_text(
                        instruction
                    )
                )
                unique_type_reference = (
                    bool(type_references)
                    and sum(document.document_type == type_references[0] for document in documents)
                    == 1
                )
                if not (
                    named_by_user
                    or ordinal_reference
                    or unique_type_reference
                    or self._LATEST_REFERENCE.search(instruction)
                    or (multi_input and self._MULTI_REFERENCE.search(instruction))
                ):
                    return "clarify:Which uploaded document should I use for this request?"
            if multi_input and len(resolved) < 2:
                return "clarify:Please select at least two files for this request."
            if not multi_input and len(resolved) > 1:
                return "clarify:Please select one document for this part of the request."
            return resolved if multi_input else resolved[0]

        if index > 0:
            return None
        if not documents:
            return None
        if explicit_source:
            return None

        normalized = instruction.casefold()
        matching_names = [
            document.filename
            for document in documents
            if document.filename.casefold() in normalized
        ]
        if matching_names:
            if multi_input:
                if len(matching_names) < 2:
                    return "clarify:Please select at least two files for this request."
                return matching_names
            return matching_names[0]

        if multi_input and self._MULTI_REFERENCE.search(instruction):
            if len(documents) < 2:
                return "clarify:Please upload at least two files for this request."
            return [document.filename for document in documents]

        ordinal_match = re.search(
            r"\b(first|1st|second|2nd|third|3rd)\s+"
            r"(file|document|pdf|workbook|spreadsheet|image|photo|picture|screenshot)\b",
            instruction,
            re.IGNORECASE,
        )
        if ordinal_match:
            selected_index = self._ORDINALS[ordinal_match.group(1).casefold()]
            ordinal_documents = (
                tuple(doc for doc in documents if doc.document_type == DocumentType.IMAGE)
                if ordinal_match.group(2).casefold() in {"image", "photo", "picture", "screenshot"}
                else documents
            )
            if selected_index >= len(ordinal_documents):
                return "clarify:Which uploaded document should I use for this request?"
            return ordinal_documents[selected_index].filename

        mentioned_types = self.pipeline_service.intent_service.registry.document_types_in_text(
            instruction
        )
        if DocumentType.IMAGE in mentioned_types:
            # The image is the source; Excel/Word/PDF in the same request is the target.
            images = [
                document for document in documents if document.document_type == DocumentType.IMAGE
            ]
            if images:
                if re.search(r"\b(?:previous|prior)\s+(?:image|photo|picture)\b", normalized):
                    return images[-2].filename if len(images) > 1 else images[-1].filename
                if len(images) == 1 or self._LATEST_REFERENCE.search(instruction):
                    return images[-1].filename
                return (
                    "clarify:Which uploaded image should I use? Say first, second, or latest image."
                )
            return "clarify:Please upload the image you want me to use."
        if mentioned_types:
            candidates = [
                document for document in documents if document.document_type == mentioned_types[0]
            ]
            uploaded = [document for document in candidates if document.kind == "uploaded"]
            if "uploaded" in normalized and uploaded:
                candidates = uploaded
            if len(candidates) == 1:
                return candidates[0].filename
            if len(candidates) > 1 and not self._LATEST_REFERENCE.search(instruction):
                return f"clarify:Which {mentioned_types[0].value.upper()} file should I use?"

        if self._LATEST_REFERENCE.search(instruction):
            latest = next((document for document in documents if document.is_latest), documents[-1])
            return latest.filename
        if len(documents) == 1:
            return documents[0].filename
        return "clarify:Which uploaded document should I use for this request?"

    @staticmethod
    def _clarification(
        question: str, options: tuple[ClarificationOption, ...] = ()
    ) -> DocumentAutomationPlan:
        return DocumentAutomationPlan(
            status="clarification_required",
            clarifying_question=question,
            steps=(),
            clarification_options=options,
        )

    @staticmethod
    def summarize_plan(plan: DocumentAutomationPlan) -> str:
        descriptions = []
        for step in plan.steps:
            source = ", ".join(step.source_filenames) or "the conversation content"
            operation = step.intent.operation.value.replace("_", " ")
            output = step.intent.output_type.value if step.intent.output_type else "text response"
            filename = step.intent.parameters.get("filename")
            target = f"{filename} ({output.upper()})" if filename else output.upper()
            descriptions.append(f"{operation} using {source}, producing {target}")
        return (
            "Review before building: "
            + "; then ".join(descriptions)
            + ". No file has been built yet."
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
            logger.warning(
                "chat.document_plan_invalid",
                extra={
                    "response_chars": len(response),
                    "error_type": type(error).__name__,
                    "validation_issues": [
                        {"location": item["loc"], "type": item["type"]} for item in error.errors()
                    ]
                    if isinstance(error, ValidationError)
                    else [],
                    "json_prefix": text.startswith("{"),
                    "json_suffix": text.endswith("}"),
                },
            )
            raise DocumentAutomationPlanningError(
                "I couldn't build a safe document plan. Please describe the desired result "
                "more specifically."
            ) from error

    @staticmethod
    def _planning_prompt(
        instruction: str,
        documents: tuple[AvailableDocument, ...],
        conversation_history: list[ChatHistoryMessage],
    ) -> str:
        files = [
            {
                "filename": document.filename,
                "document_type": document.document_type.value,
                "kind": document.kind,
                "is_latest": document.is_latest,
            }
            for document in documents
        ]
        history = [message.model_dump(mode="json") for message in conversation_history[-8:]]
        schema = {
            "status": "ready | clarification_required",
            "clarifying_question": "string or null",
            "clarification_options": [
                {
                    "id": "string",
                    "label": "string",
                    "description": "string or null",
                    "recommended": "boolean",
                }
            ],
            "steps": [
                {
                    "document_type": "xlsx | csv | docx | pdf | pptx | txt | markdown | null",
                    "operation": "registered operation name",
                    "input_file": "exact filename, filename list, or null for prior output",
                    "output_type": (
                        "xlsx | csv | docx | pdf | pptx | txt | markdown | text_response | null"
                    ),
                    "parameters": "only parameters supported by that operation",
                }
            ],
        }
        operations = [operation.value for operation in DocumentOperation]
        return (
            "You are a document automation planner. Return ONLY one JSON object and no Markdown. "
            "Plan the smallest safe ordered workflow. Steps may read, extract, summarize, analyze, "
            "answer questions, create files, modify files, merge PDFs, transform spreadsheets, or "
            "convert documents. A null input_file after step one consumes the preceding file output; "
            "text results remain available as context for later create_document steps. The "
            "document_type field always describes an input/source type; for create_document set "
            "document_type to null and put the requested target only in output_type. Name exact "
            "available filenames for separate source branches. merge_pdf requires a list of at least "
            "two PDF filenames. Text operations use output_type text_response. create_document must "
            "specify the requested file output type. For create_document, parameters MUST be {} "
            "or contain ONLY title and filename; NEVER put content, columns, rows, dietary choices, "
            "currency, assumptions or user answers in parameters. The original instruction and "
            "confirmed answers are already passed to content generation. "
            "A new topic is independent of earlier uploads unless the user refers to a source. "
            "With no available files, use create_document with input_file null to write from "
            "general knowledge and the latest requirements. A request to create a file must "
            "end in a file-producing step, never only answer_document_question. "
            "For creation requests, ask questions ONLY if missing details materially change the "
            "output. For example top 50 dishes without ranking or dietary scope needs questions; "
            "a fully specified request or summarizing an uploaded PDF as Word does not. "
            "Ask only one missing question per call. When a clarifying question has a natural "
            "small set of choices, list 2-5 concise options in clarification_options and mark the "
            "single best one recommended: true. Otherwise omit clarification_options and ask a "
            "plain open-ended question. Return status clarification_required and steps [] while "
            "ANY essential detail remains unanswered. Use conversation history to resolve answers "
            "and retain earlier choices. Do not repeat answered questions. A review summary is "
            "not a new user request. Repeating the same instruction after a review must produce "
            "the same steps unless the user changed the requirements. Never treat confirmation "
            "as an answer to missing details. Once all details are known return status ready. "
            "Never invent a filename, locator, parameter, or operation.\n"
            f"Allowed operations: {json.dumps(operations)}\n"
            f"JSON schema: {json.dumps(schema, ensure_ascii=True)}\n"
            f"Available files: {json.dumps(files, ensure_ascii=True)}\n"
            f"Recent conversation: {json.dumps(history, ensure_ascii=True)}\n"
            f"User request: {json.dumps(instruction, ensure_ascii=True)}"
        )
