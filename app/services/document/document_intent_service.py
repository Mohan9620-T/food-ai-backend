import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.document.document_operation_registry import (
    DocumentOperation,
    DocumentOperationDefinition,
    DocumentOperationRegistry,
    DocumentType,
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
    _CREATE_PATTERN = re.compile(r"\b(?:create|generate|make|write|build|draft)\b", re.IGNORECASE)
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
            candidates.append(DocumentOperation.CREATE_DOCUMENT)
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
        targets = self.registry.document_types_in_text(match.group("target") if match else "")
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
