import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from pydantic import ValidationError

from app.config import settings
from app.services.document.document_operation_registry import (
    DocumentOperation,
    DocumentOperationDefinition,
    DocumentOperationRegistry,
    DocumentType,
)
from app.services.document.extraction_models import (
    DocumentEdit,
    DocumentEditAction,
    DocumentLocator,
    IntentCategory,
    OperationPlan,
    OperationPlanStep,
)

if TYPE_CHECKING:
    from app.services.chat_document_service import ChatDocumentService


class DocumentIntentError(ValueError):
    pass


class AmbiguousDocumentIntentError(DocumentIntentError):
    pass


class UnsupportedDocumentOperationError(DocumentIntentError):
    pass


class MissingDocumentInputError(DocumentIntentError):
    pass


class InvalidDocumentParametersError(DocumentIntentError):
    pass


@dataclass(frozen=True)
class DocumentIntent:
    document_type: DocumentType | None
    operation: DocumentOperation
    input_file: str | None
    output_type: DocumentType | None
    parameters: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "document_type": self.document_type.value if self.document_type else None,
            "operation": self.operation.value,
            "input_file": self.input_file,
            "output_type": self.output_type.value if self.output_type else None,
            "parameters": self.parameters,
        }


class DocumentIntentService:
    """Resolve document requests without reading or manipulating document bytes."""

    _FILENAME_PATTERN = re.compile(
        r"(?P<filename>[^\s<>:\"/\\|?*]+\.(?:xlsx|csv|docx|pdf|pptx|txt|md|markdown))\b",
        re.IGNORECASE,
    )
    _CONVERT_PATTERN = re.compile(
        r"\b(?:convert|export|save|change)\b.*?\b(?:to|as|into)\b", re.IGNORECASE
    )
    _CREATE_PATTERN = re.compile(
        r"\b(?:create|crate|creat|generate|make|write|build|draft|prepare|ready|maathi)\b",
        re.IGNORECASE,
    )
    _READ_PATTERN = re.compile(
        r"\b(?:read|open|inspect|extract\s+(?:the\s+)?text|show\s+(?:me\s+)?(?:the\s+)?contents?)\b",
        re.IGNORECASE,
    )
    _SUMMARIZE_PATTERN = re.compile(
        r"\b(?:summari[sz]e|analy[sz]e|review|give\s+(?:me\s+)?(?:a\s+)?summary)\b",
        re.IGNORECASE,
    )
    _TABLE_TO_EXCEL_PATTERN = re.compile(
        r"(?:\b(?:extract|copy|export|create|make)\b.*\b(?:table|rows?|data)\b.*"
        r"\b(?:excel|xlsx|workbook)\b|\b(?:excel|xlsx|workbook)\b.*"
        r"\b(?:from|using)\b.*\bpdf\b.*\b(?:table|rows?|data)\b)",
        re.IGNORECASE,
    )
    _DOCUMENT_WORD_PATTERN = re.compile(
        r"\b(?:document|file|pdf|excel|spreadsheet|workbook|csv|word|docx|pptx|"
        r"powerpoint|presentation|slides?|txt|text|markdown)\b",
        re.IGNORECASE,
    )
    _QUESTION_PATTERN = re.compile(
        r"(?:\?|\b(?:what|which|who|when|where|why|how|does|do|is|are|can)\b)",
        re.IGNORECASE,
    )
    _AMBIGUOUS_CHANGE_PATTERN = re.compile(
        r"^\s*(?:change|update|modify|edit|format)\s+(?:this|the)\s+(?:document|file)\s*[.!?]*$",
        re.IGNORECASE,
    )
    _MODIFY_PATTERN = re.compile(
        r"\b(?:change|update|modify|edit|replace|remove|delete|insert|add|align|format|"
        r"freeze|sort|filter|extract|reorder|merge|combine)\b",
        re.IGNORECASE,
    )
    _REPLACE_PATTERN = re.compile(
        r"\b(?:replace|change)\s+[`\"'](?P<old>.+?)[`\"']\s+(?:with|to)\s+"
        r"[`\"'](?P<new>.*?)[`\"']",
        re.IGNORECASE,
    )
    _TITLE_PATTERN = re.compile(
        r"\b(?:change|update|replace)\s+(?:the\s+)?title\s+from\s+"
        r"[`\"'](?P<old>.+?)[`\"']\s+(?:to|with)\s+[`\"'](?P<new>.*?)[`\"']",
        re.IGNORECASE,
    )

    def __init__(
        self,
        registry: DocumentOperationRegistry | None = None,
        document_service: "ChatDocumentService | None" = None,
    ) -> None:
        if document_service is None:
            from app.services.chat_document_service import ChatDocumentService

            document_service = ChatDocumentService()
        self.registry = registry or DocumentOperationRegistry()
        self.document_service = document_service

    def resolve_plan(self, request: str, *, input_file: str | None = None) -> OperationPlan:
        """Create and validate a safe plan without exposing document bytes to the LLM."""
        instruction = request.strip()
        safe_input = self._safe_input_filename(input_file or self._filename_in(instruction))
        if self._AMBIGUOUS_CHANGE_PATTERN.fullmatch(instruction):
            return OperationPlan(
                intent_category=IntentCategory.MODIFY,
                confidence=1.0,
                needs_clarification=True,
                clarification_question="What would you like me to change in the document?",
                steps=[],
            )

        deterministic_modification = self._deterministic_modification_plan(instruction, safe_input)
        if deterministic_modification is not None:
            self.registry.validate_plan(deterministic_modification)
            return deterministic_modification

        normalized = self.registry.normalize(instruction)
        phase_one_categories = {
            DocumentOperation.READ_DOCUMENT: IntentCategory.READ,
            DocumentOperation.EXTRACT_DOCUMENT: IntentCategory.EXTRACT,
            DocumentOperation.SUMMARIZE_DOCUMENT: IntentCategory.SUMMARIZE,
            DocumentOperation.ANALYZE_DOCUMENT: IntentCategory.ANALYZE,
            DocumentOperation.ANSWER_DOCUMENT_QUESTION: IntentCategory.ANALYZE,
        }
        alias_matches = [
            (len(self.registry.normalize(alias)), definition.operation)
            for definition in self.registry.operations()
            if definition.operation in phase_one_categories
            for alias in (definition.operation.value, *definition.aliases)
            if re.search(rf"\b{re.escape(self.registry.normalize(alias))}\b", normalized)
        ]
        # "Read ... and summarize" describes a read prerequisite and a summarization goal,
        # not two competing user-visible operations (acceptance request #1).
        if self._READ_PATTERN.search(instruction) and self._SUMMARIZE_PATTERN.search(instruction):
            alias_matches.append((10_000, DocumentOperation.SUMMARIZE_DOCUMENT))
        if alias_matches:
            operation = max(alias_matches)[1]
            if safe_input is None:
                raise MissingDocumentInputError(
                    "Upload or select the document you want me to use, then try this request again."
                )
            parameters: dict[str, object] = (
                {"question": instruction}
                if operation == DocumentOperation.ANSWER_DOCUMENT_QUESTION
                else {}
            )
            plan = OperationPlan(
                intent_category=phase_one_categories[operation],
                confidence=1.0,
                needs_clarification=False,
                steps=[
                    OperationPlanStep(
                        operation=operation,
                        input_ref=safe_input,
                        output_type="text_response",
                        parameters=parameters,
                    )
                ],
            )
            self.registry.validate_plan(plan)
            return plan

        try:
            intent = self.resolve(instruction, input_file=safe_input)
        except UnsupportedDocumentOperationError:
            if safe_input and self._QUESTION_PATTERN.search(instruction):
                plan = OperationPlan(
                    intent_category=IntentCategory.ANALYZE,
                    confidence=1.0,
                    needs_clarification=False,
                    steps=[
                        OperationPlanStep(
                            operation=DocumentOperation.ANSWER_DOCUMENT_QUESTION,
                            input_ref=safe_input,
                            output_type="text_response",
                            parameters={"question": instruction},
                        )
                    ],
                )
                self.registry.validate_plan(plan)
                return plan
            return self._semantic_plan(instruction, safe_input)

        operation = intent.operation
        spreadsheet_categories = {
            DocumentOperation.FORMAT_WORKBOOK: IntentCategory.FORMAT,
            DocumentOperation.FILTER_COLUMN: IntentCategory.FORMAT,
            DocumentOperation.SPLIT_BY_CATEGORY: IntentCategory.TRANSFORM,
            DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY: IntentCategory.TRANSFORM,
        }
        if operation in spreadsheet_categories:
            assert intent.input_file is not None
            assert intent.output_type is not None
            plan = OperationPlan(
                intent_category=spreadsheet_categories[operation],
                confidence=1.0,
                needs_clarification=False,
                steps=[
                    OperationPlanStep(
                        operation=operation,
                        input_ref=intent.input_file,
                        output_type=cast(
                            Literal["docx", "pdf", "xlsx", "csv", "pptx", "txt", "md"],
                            intent.output_type.value,
                        ),
                        parameters=intent.parameters,
                    )
                ],
            )
            self.registry.validate_plan(plan)
            return plan
        if operation not in {
            DocumentOperation.READ_DOCUMENT,
            DocumentOperation.SUMMARIZE_DOCUMENT,
        }:
            raise UnsupportedDocumentOperationError(
                "That document operation is recognized but is not supported in the current "
                "document-understanding phase."
            )
        normalized = self.registry.normalize(instruction)
        if operation == DocumentOperation.READ_DOCUMENT and "extract" in normalized:
            category = IntentCategory.EXTRACT
            operation = DocumentOperation.EXTRACT_DOCUMENT
        elif operation == DocumentOperation.SUMMARIZE_DOCUMENT and re.search(
            r"\banaly[sz]e\b", normalized
        ):
            category = IntentCategory.ANALYZE
            operation = DocumentOperation.ANALYZE_DOCUMENT
        elif operation == DocumentOperation.SUMMARIZE_DOCUMENT:
            category = IntentCategory.SUMMARIZE
        else:
            category = IntentCategory.READ
        assert intent.input_file is not None
        plan = OperationPlan(
            intent_category=category,
            confidence=1.0,
            needs_clarification=False,
            steps=[
                OperationPlanStep(
                    operation=operation,
                    input_ref=intent.input_file,
                    output_type="text_response",
                    parameters={},
                )
            ],
        )
        self.registry.validate_plan(plan)
        return plan

    @classmethod
    def is_modification_request(cls, instruction: str | None, filename: str | None) -> bool:
        if not instruction or not filename or not cls._MODIFY_PATTERN.search(instruction):
            return False
        normalized = instruction.casefold()
        if re.search(r"\b(?:create|generate|make|write|build)\b", instruction, re.IGNORECASE):
            return False
        if (
            cls._CONVERT_PATTERN.search(instruction)
            and cls.registry_document_type_target(instruction) is not None
        ):
            return False
        document_type = DocumentOperationRegistry.document_type_from_filename(filename)
        if document_type == DocumentType.PDF and "extract" in normalized:
            return bool(re.search(r"\bextract\b.*\bpages?\b", instruction, re.IGNORECASE))
        return document_type in {
            DocumentType.DOCX,
            DocumentType.XLSX,
            DocumentType.PPTX,
            DocumentType.PDF,
        }

    @staticmethod
    def registry_document_type_target(instruction: str) -> DocumentType | None:
        match = re.search(r"\b(?:to|as|into)\b(?P<target>.+)$", instruction, re.IGNORECASE)
        types = DocumentOperationRegistry.document_types_in_text(
            match.group("target") if match else ""
        )
        return types[-1] if types else None

    def _semantic_plan(self, instruction: str, input_file: str | None) -> OperationPlan:
        chat_service = self.document_service.chat_service
        response = chat_service.chat(
            self._planning_prompt(instruction, input_file),
            history=[],
            reference_history=[],
            temperature=settings.DOCUMENT_AI_TEMPERATURE,
        )
        text = response.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
        try:
            plan = OperationPlan.model_validate(json.loads(text))
            self.registry.validate_plan(plan)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
            raise UnsupportedDocumentOperationError(
                "I couldn't build a safe document-understanding plan. Please ask to read, "
                "extract, summarize, analyze, or answer a question about one document."
            ) from error
        allowed_references = {input_file} if input_file else set()
        planned_references = {
            reference
            for step in plan.steps
            for reference in (
                step.input_ref if isinstance(step.input_ref, list) else [step.input_ref]
            )
        }
        if planned_references - allowed_references:
            raise UnsupportedDocumentOperationError(
                "The document plan referenced a file that is not available in this request."
            )
        threshold = settings.DOCUMENT_PLAN_CONFIDENCE_THRESHOLD
        if not plan.needs_clarification and plan.confidence < threshold:
            return OperationPlan(
                intent_category=plan.intent_category,
                confidence=plan.confidence,
                needs_clarification=True,
                clarification_question="Could you clarify what information you need from the document?",
                steps=[],
            )
        if plan.intent_category not in {
            IntentCategory.READ,
            IntentCategory.EXTRACT,
            IntentCategory.SUMMARIZE,
            IntentCategory.ANALYZE,
            IntentCategory.MODIFY,
            IntentCategory.FORMAT,
        }:
            raise UnsupportedDocumentOperationError(
                "That document operation is recognized but is not supported in the current "
                "document phase."
            )
        for step in plan.steps:
            if step.operation in {
                DocumentOperation.MODIFY_DOCUMENT,
                DocumentOperation.MODIFY_PDF_PAGES,
                DocumentOperation.FILTER_AND_SORT_WORKBOOK,
            }:
                edits = step.parameters.get("edits")
                if not isinstance(edits, list) or not edits:
                    raise UnsupportedDocumentOperationError(
                        "A document modification plan must contain targeted edits."
                    )
                try:
                    step.parameters["edits"] = [
                        DocumentEdit.model_validate(edit).model_dump(mode="json") for edit in edits
                    ]
                except ValidationError as error:
                    raise UnsupportedDocumentOperationError(
                        "The document modification plan contained an invalid locator or edit."
                    ) from error
        return plan

    @staticmethod
    def _planning_prompt(instruction: str, input_file: str | None) -> str:
        return (
            "Return ONLY strict JSON for a safe document plan. Categories allowed here are READ, "
            "EXTRACT, SUMMARIZE, ANALYZE, MODIFY, and FORMAT. Reading operations use "
            "read_document, extract_document, summarize_document, analyze_document, or "
            "answer_document_question with output_type text_response. Modifications use only "
            "modify_document, modify_pdf_pages, or filter_and_sort_workbook and must include a "
            "non-empty parameters.edits array. Every edit has action, locator, value, and options; "
            "locators identify heading text, exact text, paragraph index, table+cell, sheet+cell "
            "range, slide+shape, or PDF pages. Never invent a locator. Ask one clarification "
            "question when the desired value or target is ambiguous. Never propose full-document "
            "rewriting, creation, conversion, or an unregistered operation.\n"
            f"Available input: {json.dumps(input_file or 'none')}\n"
            f"User request: {json.dumps(instruction)}"
        )

    def _deterministic_modification_plan(
        self, instruction: str, input_file: str | None
    ) -> OperationPlan | None:
        if input_file is None or not self._MODIFY_PATTERN.search(instruction):
            return None
        document_type = self.registry.document_type_from_filename(input_file)
        if document_type not in {
            DocumentType.DOCX,
            DocumentType.XLSX,
            DocumentType.PPTX,
            DocumentType.PDF,
        }:
            return None

        edits: list[DocumentEdit] = []
        title_match = self._TITLE_PATTERN.search(instruction)
        replace_match = self._REPLACE_PATTERN.search(instruction)
        if title_match:
            edits.append(
                DocumentEdit(
                    action=DocumentEditAction.REPLACE_TEXT,
                    locator=DocumentLocator(heading=title_match.group("old")),
                    value=title_match.group("new"),
                )
            )
        elif replace_match:
            edits.append(
                DocumentEdit(
                    action=DocumentEditAction.REPLACE_TEXT,
                    locator=DocumentLocator(text=replace_match.group("old")),
                    value=replace_match.group("new"),
                )
            )

        if document_type == DocumentType.DOCX and re.search(
            r"\b(?:align|alignment|center|centre)\b.*\btable\b|\btable\b.*\b(?:align|alignment|center|centre)\b",
            instruction,
            re.IGNORECASE,
        ):
            table_match = re.search(r"\btable\s+(?P<number>\d+)\b", instruction, re.IGNORECASE)
            alignment = (
                "right"
                if "right" in instruction.casefold()
                else ("left" if "left" in instruction.casefold() else "center")
            )
            edits.append(
                DocumentEdit(
                    action=DocumentEditAction.FORMAT_TABLE,
                    locator=DocumentLocator(
                        table_index=int(table_match.group("number")) if table_match else None
                    ),
                    options={"alignment": alignment},
                )
            )

        if document_type == DocumentType.XLSX and re.search(
            r"\bfilter\b.*\bsort\b|\bsort\b.*\bfilter\b", instruction, re.IGNORECASE
        ):
            sort_match = re.search(
                r"\bsort(?:ed|ing)?\s+(?:them\s+)?(?:by|on)\s+[`\"']?(?P<column>[a-z0-9 _*-]+?)"
                r"[`\"']?(?=[.!?,]|\s+(?:ascending|descending|asc|desc)\b|$)",
                instruction,
                re.IGNORECASE,
            )
            value_match = re.search(
                r"\bfilter\s+(?:the\s+)?(?P<value>[a-z0-9_-]+)\s+(?:records?|rows?)",
                instruction,
                re.IGNORECASE,
            )
            if not sort_match:
                return self._clarification_plan(
                    "Which column should I use to sort the filtered records?",
                    IntentCategory.FORMAT,
                )
            value = value_match.group("value") if value_match else "active"
            sheet_match = re.search(
                r"\b(?:worksheet|sheet)\s+[`\"']?(?P<sheet>[a-z0-9 _-]+?)"
                r"[`\"']?(?=[.!?,]|$)",
                instruction,
                re.IGNORECASE,
            )
            sheet = sheet_match.group("sheet").strip() if sheet_match else None
            edits.extend(
                [
                    DocumentEdit(
                        action=DocumentEditAction.FILTER_ROWS,
                        locator=DocumentLocator(sheet=sheet),
                        options={"equals": value, "header": True},
                    ),
                    DocumentEdit(
                        action=DocumentEditAction.SORT_RANGE,
                        locator=DocumentLocator(sheet=sheet),
                        options={
                            "column": sort_match.group("column").strip(),
                            "header": True,
                            "descending": bool(
                                re.search(r"\b(?:descending|desc)\b", instruction, re.IGNORECASE)
                            ),
                        },
                    ),
                ]
            )

        if document_type == DocumentType.PDF:
            page_match = re.search(
                r"\b(?P<action>remove|delete|extract|keep|reorder)\b.*?\bpages?\s+"
                r"(?P<pages>\d+(?:\s*[, -]\s*\d+)*)",
                instruction,
                re.IGNORECASE,
            )
            if page_match:
                action_word = page_match.group("action").casefold()
                numbers = [int(value) for value in re.findall(r"\d+", page_match.group("pages"))]
                action = {
                    "remove": DocumentEditAction.REMOVE_PDF_PAGES,
                    "delete": DocumentEditAction.REMOVE_PDF_PAGES,
                    "extract": DocumentEditAction.EXTRACT_PDF_PAGES,
                    "keep": DocumentEditAction.EXTRACT_PDF_PAGES,
                    "reorder": DocumentEditAction.REORDER_PDF_PAGES,
                }[action_word]
                edits.append(DocumentEdit(action=action, locator=DocumentLocator(pages=numbers)))

        if not edits:
            if re.search(r"\b(?:change|update|modify|edit)\b", instruction, re.IGNORECASE):
                return None
            return None
        operation = (
            DocumentOperation.MODIFY_PDF_PAGES
            if document_type == DocumentType.PDF
            else (
                DocumentOperation.FILTER_AND_SORT_WORKBOOK
                if any(edit.action == DocumentEditAction.FILTER_ROWS for edit in edits)
                else DocumentOperation.MODIFY_DOCUMENT
            )
        )
        category = (
            IntentCategory.FORMAT
            if all(
                edit.action
                in {
                    DocumentEditAction.FORMAT_PARAGRAPH,
                    DocumentEditAction.FORMAT_TABLE,
                    DocumentEditAction.FORMAT_RANGE,
                    DocumentEditAction.FREEZE_PANES,
                    DocumentEditAction.SORT_RANGE,
                    DocumentEditAction.FILTER_ROWS,
                }
                for edit in edits
            )
            else IntentCategory.MODIFY
        )
        return OperationPlan(
            intent_category=category,
            confidence=1.0,
            needs_clarification=False,
            steps=[
                OperationPlanStep(
                    operation=operation,
                    input_ref=input_file,
                    output_type=cast(
                        Literal["docx", "pdf", "xlsx", "csv", "pptx", "txt", "md"],
                        "md" if document_type == DocumentType.MARKDOWN else document_type.value,
                    ),
                    parameters={"edits": [edit.model_dump(mode="json") for edit in edits]},
                )
            ],
        )

    @staticmethod
    def _clarification_plan(question: str, category: IntentCategory) -> OperationPlan:
        return OperationPlan(
            intent_category=category,
            confidence=1.0,
            needs_clarification=True,
            clarification_question=question,
            steps=[],
        )

    def resolve(self, request: str, *, input_file: str | None = None) -> DocumentIntent:
        instruction = request.strip()
        if not instruction:
            raise UnsupportedDocumentOperationError(
                "Describe the document operation you want me to perform."
            )
        resolved_input = self._safe_input_filename(input_file or self._filename_in(instruction))
        input_type = self.registry.document_type_from_filename(resolved_input)
        mentioned_types = self.registry.document_types_in_text(instruction)

        if self.is_table_to_excel_request(instruction):
            definition = self.registry.get(DocumentOperation.EXTRACT_TABLE_TO_EXCEL)
            if definition is None:
                raise UnsupportedDocumentOperationError("PDF table extraction is not registered.")
            return self._build_intent(
                instruction,
                definition,
                resolved_input,
                input_type or DocumentType.PDF,
                DocumentType.XLSX,
            )

        spreadsheet_operation = self.document_service.spreadsheet_operation(instruction)
        if spreadsheet_operation is not None:
            definition = self.registry.get(spreadsheet_operation.value)
            if definition is None:
                raise UnsupportedDocumentOperationError(
                    "That spreadsheet operation is not registered."
                )
            return self._build_intent(
                instruction,
                definition,
                resolved_input,
                input_type or self._single_type(mentioned_types),
                DocumentType.XLSX,
            )

        candidates: list[DocumentOperation] = []
        if self._CONVERT_PATTERN.search(instruction):
            candidates.append(DocumentOperation.CONVERT_DOCUMENT)
        if self._SUMMARIZE_PATTERN.search(instruction):
            candidates.append(DocumentOperation.SUMMARIZE_DOCUMENT)
        if self._READ_PATTERN.search(instruction):
            candidates.append(DocumentOperation.READ_DOCUMENT)
        if self._CREATE_PATTERN.search(instruction) and not candidates:
            explicitly_converts_selected_type = (
                input_type is not None
                and input_type in mentioned_types
                and any(document_type != input_type for document_type in mentioned_types)
            )
            candidates.append(
                DocumentOperation.CONVERT_DOCUMENT
                if explicitly_converts_selected_type
                else DocumentOperation.CREATE_DOCUMENT
            )
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) > 1:
            readable = ", ".join(operation.value for operation in candidates)
            raise AmbiguousDocumentIntentError(
                f"This request contains more than one document operation ({readable}). "
                "Please request one operation at a time."
            )
        if not candidates:
            detail = (
                "That document operation is not supported. Ask me to create, read, summarize, "
                "convert, format, filter, split, or expand a supported document."
                if self._DOCUMENT_WORD_PATTERN.search(instruction)
                else "I couldn't identify a document operation. Describe the file task you need."
            )
            raise UnsupportedDocumentOperationError(detail)

        definition = self.registry.get(candidates[0])
        if definition is None:
            raise UnsupportedDocumentOperationError("That document operation is not registered.")
        output_type = self._output_type(instruction, definition.operation, mentioned_types)
        document_type = (
            output_type
            if definition.operation == DocumentOperation.CREATE_DOCUMENT
            else input_type or self._input_type(mentioned_types, output_type)
        )
        return self._build_intent(
            instruction,
            definition,
            resolved_input,
            document_type,
            output_type,
        )

    @classmethod
    def is_table_to_excel_request(cls, instruction: str | None) -> bool:
        return bool(instruction and cls._TABLE_TO_EXCEL_PATTERN.search(instruction))

    @classmethod
    def is_creation_request(cls, instruction: str | None) -> bool:
        return bool(instruction and cls._CREATE_PATTERN.search(instruction))

    @classmethod
    def is_conversion_request(cls, instruction: str | None) -> bool:
        return bool(instruction and cls._CONVERT_PATTERN.search(instruction))

    @classmethod
    def filename_in(cls, instruction: str) -> str | None:
        """Return a safe basename explicitly named in an instruction, if present."""
        return cls._safe_input_filename(cls._filename_in(instruction))

    def _build_intent(
        self,
        instruction: str,
        definition: DocumentOperationDefinition,
        input_file: str | None,
        document_type: DocumentType | None,
        output_type: DocumentType | None,
    ) -> DocumentIntent:
        if definition.requires_input and input_file is None:
            raise MissingDocumentInputError(
                "Upload or select the document you want me to use, then try this request again."
            )
        if input_file is not None and document_type is None:
            raise UnsupportedDocumentOperationError(
                "The selected file type is not supported. Use XLSX, CSV, DOCX, PDF, PPTX, TXT, "
                "or Markdown."
            )
        if (
            document_type is not None
            and definition.input_types
            and document_type not in definition.input_types
        ):
            allowed = ", ".join(sorted(item.value.upper() for item in definition.input_types))
            raise InvalidDocumentParametersError(
                f"{definition.operation.value} requires one of these input types: {allowed}."
            )
        if definition.output_types and output_type is None:
            raise InvalidDocumentParametersError(
                "Specify the output document type: XLSX, CSV, DOCX, PDF, PPTX, TXT, or Markdown."
            )
        if output_type is not None and output_type not in definition.output_types:
            raise InvalidDocumentParametersError(
                f"{output_type.value.upper()} is not a valid output for this operation."
            )
        if (
            definition.operation == DocumentOperation.CONVERT_DOCUMENT
            and document_type == output_type
        ):
            raise InvalidDocumentParametersError(
                "Choose a different output type; the input is already in that format."
            )
        return DocumentIntent(
            document_type=document_type,
            operation=definition.operation,
            input_file=input_file,
            output_type=output_type,
            parameters=self._parameters(instruction, definition.operation),
        )

    def _output_type(
        self,
        instruction: str,
        operation: DocumentOperation,
        mentioned_types: list[DocumentType],
    ) -> DocumentType | None:
        if operation == DocumentOperation.CREATE_DOCUMENT:
            return mentioned_types[-1] if mentioned_types else None
        if operation != DocumentOperation.CONVERT_DOCUMENT:
            return None
        match = re.search(r"\b(?:to|as|into)\b(?P<target>.+)$", instruction, re.IGNORECASE)
        targets = self.registry.document_types_in_text(
            match.group("target") if match else instruction
        )
        return targets[-1] if targets else None

    @staticmethod
    def _input_type(
        mentioned_types: list[DocumentType], output_type: DocumentType | None
    ) -> DocumentType | None:
        for document_type in mentioned_types:
            if document_type != output_type:
                return document_type
        return (
            None if output_type is not None else DocumentIntentService._single_type(mentioned_types)
        )

    @staticmethod
    def _single_type(document_types: list[DocumentType]) -> DocumentType | None:
        return document_types[0] if len(set(document_types)) == 1 else None

    @classmethod
    def _filename_in(cls, instruction: str) -> str | None:
        match = cls._FILENAME_PATTERN.search(instruction)
        return match.group("filename") if match else None

    @staticmethod
    def _safe_input_filename(filename: str | None) -> str | None:
        if filename is None:
            return None
        return Path(filename).name

    def _parameters(self, instruction: str, operation: DocumentOperation) -> dict[str, object]:
        if operation == DocumentOperation.FILTER_COLUMN:
            column = self.document_service.filter_column_reference(instruction)
            return {"column": column} if column else {}
        if operation == DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY:
            return {
                "categories": [
                    "Regular",
                    "Easy to chew",
                    "Soft and bite-sized",
                    "Minced and Moist",
                    "Pureed",
                ],
                "active_marker": "x",
            }
        sheet_match = re.search(
            r"\b(?:worksheet|sheet)\s+[`\"']?(?P<sheet>[a-z0-9 _-]+)",
            instruction,
            re.IGNORECASE,
        )
        return {"sheet_name": sheet_match.group("sheet").strip()} if sheet_match else {}
