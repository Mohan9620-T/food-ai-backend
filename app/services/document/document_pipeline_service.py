import re
from dataclasses import dataclass
from pathlib import Path

from app.services.chat_document_service import ChatDocumentService
from app.services.conversion.document_conversion_service import DocumentConversionService
from app.services.document.document_generation_service import (
    DocumentGenerationService,
    GeneratedDocument,
)
from app.services.document.document_intent_service import (
    DocumentIntent,
    DocumentIntentService,
    InvalidDocumentParametersError,
    UnsupportedDocumentOperationError,
)
from app.services.document.document_operation_registry import DocumentOperation, DocumentType
from app.services.document.document_validation_service import DocumentValidationService
from app.services.document.exceptions import InvalidDocumentError


@dataclass(frozen=True)
class PipelineDocument:
    file_data: bytes
    filename: str


@dataclass(frozen=True)
class DocumentPipelineStep:
    position: int
    instruction: str
    intent: DocumentIntent
    explicitly_named_input: bool


@dataclass(frozen=True)
class DocumentPipelineResult:
    step: DocumentPipelineStep
    document: GeneratedDocument
    summary: str


@dataclass(frozen=True)
class StructuredPipelineStep:
    document_type: DocumentType | None
    operation: DocumentOperation
    input_file: str | None
    output_type: DocumentType | None
    parameters: dict[str, object]


class DocumentPipelineService:
    """Plan and execute ordered, deterministic document transformations."""

    _STEP_SEPARATOR = re.compile(r"\s*(?:,?\s+then\s+|;\s*)", re.IGNORECASE)
    _OUTPUT_OPERATIONS = frozenset(
        {
            DocumentOperation.CONVERT_DOCUMENT,
            DocumentOperation.FORMAT_WORKBOOK,
            DocumentOperation.FILTER_COLUMN,
            DocumentOperation.SPLIT_BY_CATEGORY,
            DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY,
            DocumentOperation.EXTRACT_TABLE_TO_EXCEL,
        }
    )

    def __init__(
        self,
        *,
        intent_service: DocumentIntentService | None = None,
        document_service: ChatDocumentService | None = None,
        conversion_service: DocumentConversionService | None = None,
        validator: DocumentValidationService | None = None,
    ) -> None:
        self.document_service = document_service or ChatDocumentService()
        self.intent_service = intent_service or DocumentIntentService(
            document_service=self.document_service
        )
        self.validator = validator or DocumentValidationService()
        self.conversion_service = conversion_service or DocumentConversionService(
            reader=self.document_service.document_reader,
            generator=self.document_service.document_generator,
            validator=self.validator,
        )

    def plan(self, instruction: str, *, input_filename: str) -> tuple[DocumentPipelineStep, ...]:
        clauses = tuple(
            clause.strip(" ,")
            for clause in self._STEP_SEPARATOR.split(instruction.strip())
            if clause.strip(" ,")
        )
        if not clauses:
            raise UnsupportedDocumentOperationError(
                "Describe the ordered document steps you want me to perform."
            )

        current_filename = Path(input_filename).name
        steps: list[DocumentPipelineStep] = []
        for position, clause in enumerate(clauses, start=1):
            explicit_input = self.intent_service.filename_in(clause)
            intent = self.intent_service.resolve(
                clause, input_file=explicit_input or current_filename
            )
            if intent.operation not in self._OUTPUT_OPERATIONS:
                raise UnsupportedDocumentOperationError(
                    f"{intent.operation.value} does not produce a document that can be passed "
                    "to the next pipeline step."
                )
            if intent.document_type is None:
                raise InvalidDocumentParametersError(
                    f"The input type for step {position} could not be determined."
                )
            output_type = intent.output_type or DocumentType.XLSX
            if intent.operation == DocumentOperation.CONVERT_DOCUMENT:
                assert intent.output_type is not None
                self.conversion_service.ensure_supported(intent.document_type, intent.output_type)
            current_filename = self._predicted_filename(
                current_filename, output_type, intent.operation
            )
            steps.append(
                DocumentPipelineStep(
                    position=position,
                    instruction=clause,
                    intent=intent,
                    explicitly_named_input=explicit_input is not None,
                )
            )
        return tuple(steps)

    def plan_structured(
        self,
        requested_steps: tuple[StructuredPipelineStep, ...],
        *,
        input_filename: str,
    ) -> tuple[DocumentPipelineStep, ...]:
        """Validate an AI-authored plan before any document bytes are touched."""
        if not requested_steps:
            raise UnsupportedDocumentOperationError("The document plan contains no steps.")
        if len(requested_steps) > 8:
            raise InvalidDocumentParametersError("A document plan can contain at most 8 steps.")

        current_filename = Path(input_filename).name
        steps: list[DocumentPipelineStep] = []
        for position, requested in enumerate(requested_steps, start=1):
            if requested.operation not in self._OUTPUT_OPERATIONS:
                raise UnsupportedDocumentOperationError(
                    "The requested document plan contains an operation that cannot produce a file."
                )
            source_filename = (
                Path(requested.input_file).name if requested.input_file else current_filename
            )
            source_type = self.intent_service.registry.document_type_from_filename(source_filename)
            if source_type is None:
                raise InvalidDocumentParametersError(
                    f"The input type for step {position} could not be determined."
                )
            if requested.document_type is not None and requested.document_type != source_type:
                raise InvalidDocumentParametersError(
                    f"The document plan expected {requested.document_type.value.upper()}, but "
                    f"{source_filename} is {source_type.value.upper()}."
                )
            definition = self.intent_service.registry.get(requested.operation)
            if definition is None or source_type not in definition.input_types:
                raise InvalidDocumentParametersError(
                    f"{requested.operation.value} cannot use a {source_type.value.upper()} file."
                )

            output_type = self._structured_output_type(requested, definition.output_types)
            parameters = self._structured_parameters(requested)
            if requested.operation == DocumentOperation.CONVERT_DOCUMENT:
                self.conversion_service.ensure_supported(source_type, output_type)
            instruction = self._structured_instruction(
                requested.operation,
                source_filename,
                output_type,
                parameters,
            )
            intent = DocumentIntent(
                document_type=source_type,
                operation=requested.operation,
                input_file=source_filename,
                output_type=output_type,
                parameters=parameters,
            )
            current_filename = self._predicted_filename(
                source_filename, output_type, requested.operation
            )
            steps.append(
                DocumentPipelineStep(
                    position=position,
                    instruction=instruction,
                    intent=intent,
                    explicitly_named_input=requested.input_file is not None,
                )
            )
        return tuple(steps)

    def execute_step(
        self, step: DocumentPipelineStep, source: PipelineDocument
    ) -> DocumentPipelineResult:
        source_type = self.intent_service.registry.document_type_from_filename(source.filename)
        if source_type is None:
            raise InvalidDocumentError("The source document type is not supported.")
        if step.intent.document_type is None:
            raise InvalidDocumentParametersError(
                f"The input type for step {step.position} could not be determined."
            )
        if step.intent.document_type != source_type:
            raise InvalidDocumentParametersError(
                f"Step {step.position} requires {step.intent.document_type.value.upper()}, but "
                f"{Path(source.filename).name} is {source_type.value.upper()}."
            )
        self.validator.validate(source.file_data, source_type)

        if step.intent.operation == DocumentOperation.CONVERT_DOCUMENT:
            assert step.intent.output_type is not None
            generated = self.conversion_service.convert(
                source.file_data, source.filename, step.intent.output_type
            )
            summary = (
                f"Converted {Path(source.filename).name} to "
                f"{step.intent.output_type.value.upper()}."
            )
        elif step.intent.operation == DocumentOperation.EXTRACT_TABLE_TO_EXCEL:
            extracted = self.document_service.extract_document(source.file_data, source.filename)
            data, filename, content_type = self.document_service.extract_tables_to_excel(
                extracted, source.filename
            )
            generated = GeneratedDocument(
                file_data=data,
                filename=filename,
                content_type=content_type,
                document_type=DocumentType.XLSX,
            )
            summary = f"Extracted tables from {Path(source.filename).name} to Excel."
        else:
            data, filename, actions = self.document_service.format_spreadsheet(
                source.file_data, source.filename, step.instruction
            )
            generated = GeneratedDocument(
                file_data=data,
                filename=filename,
                content_type=DocumentGenerationService.MIME_TYPES[DocumentType.XLSX],
                document_type=DocumentType.XLSX,
            )
            summary = f"Updated {Path(source.filename).name}: {', '.join(actions)}."

        expected_type = step.intent.output_type or DocumentType.XLSX
        actual_type = self.intent_service.registry.document_type_from_filename(generated.filename)
        if generated.document_type != expected_type or actual_type != expected_type:
            raise InvalidDocumentError(
                f"Step {step.position} produced the wrong document type; the output was not saved."
            )
        self.validator.validate(generated.file_data, generated.document_type)
        return DocumentPipelineResult(step=step, document=generated, summary=summary)

    @staticmethod
    def _predicted_filename(
        current_filename: str,
        output_type: DocumentType,
        operation: DocumentOperation,
    ) -> str:
        if operation == DocumentOperation.CONVERT_DOCUMENT:
            return DocumentConversionService.output_filename(current_filename, output_type)
        stem = Path(current_filename).stem or "document"
        return DocumentGenerationService.safe_filename(f"{stem}-pipeline.xlsx", output_type)

    @staticmethod
    def _structured_output_type(
        requested: StructuredPipelineStep,
        allowed: frozenset[DocumentType],
    ) -> DocumentType:
        if requested.output_type is not None:
            output_type = requested.output_type
        elif len(allowed) == 1:
            output_type = next(iter(allowed))
        else:
            raise InvalidDocumentParametersError(
                "The document plan must specify the output type for this conversion."
            )
        if output_type not in allowed:
            raise InvalidDocumentParametersError(
                f"{output_type.value.upper()} is not a valid output for "
                f"{requested.operation.value}."
            )
        return output_type

    @staticmethod
    def _structured_parameters(requested: StructuredPipelineStep) -> dict[str, object]:
        allowed_keys = (
            {"column"} if requested.operation == DocumentOperation.FILTER_COLUMN else set()
        )
        unexpected = set(requested.parameters) - allowed_keys
        if unexpected:
            raise InvalidDocumentParametersError(
                "The document plan contains unsupported parameters."
            )
        if requested.operation == DocumentOperation.FILTER_COLUMN:
            column = requested.parameters.get("column")
            if not isinstance(column, str) or not column.strip():
                raise InvalidDocumentParametersError(
                    "The document plan must specify which column should be filtered."
                )
            return {"column": column.strip()}
        return {}

    @staticmethod
    def _structured_instruction(
        operation: DocumentOperation,
        source_filename: str,
        output_type: DocumentType,
        parameters: dict[str, object],
    ) -> str:
        if operation == DocumentOperation.CONVERT_DOCUMENT:
            return f"Convert {source_filename} to {output_type.value.upper()}"
        if operation == DocumentOperation.EXTRACT_TABLE_TO_EXCEL:
            return f"Extract the tables from {source_filename} to Excel"
        if operation == DocumentOperation.FILTER_COLUMN:
            return f"Add a filter to the {parameters['column']} column"
        if operation == DocumentOperation.SPLIT_BY_CATEGORY:
            return "Split the workbook into one sheet per category"
        if operation == DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY:
            return "Create category rows for every x"
        return "Format the workbook professionally"
