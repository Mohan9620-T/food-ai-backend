import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import ValidationError

from app.config import settings
from app.schemas.chat import ChatHistoryMessage
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
from app.services.document.document_modification_service import DocumentModificationService
from app.services.document.document_operation_registry import (
    DocumentOperation,
    DocumentOperationDefinition,
    DocumentType,
    Fidelity,
    InputArity,
)
from app.services.document.document_validation_service import DocumentValidationService
from app.services.document.exceptions import InvalidDocumentError
from app.services.document.extraction_models import (
    DocumentEdit,
    ExtractedDocument,
    GeneratedSectionContent,
    StructuredDocumentContent,
)
from app.services.document.image_document_reader import ImageDocumentReader
from app.services.spreadsheet.row_append import AppendRowsRequest, WorkbookRowAppender
from app.services.spreadsheet.row_selection import RowSelection, WorkbookRowSelector


@dataclass(frozen=True)
class PipelineDocument:
    file_data: bytes
    filename: str
    document_id: int | None = None


@dataclass(frozen=True)
class DocumentPipelineStep:
    position: int
    instruction: str
    intent: DocumentIntent
    explicitly_named_input: bool
    source_filenames: tuple[str, ...] = ()
    produces_document: bool = True


@dataclass(frozen=True)
class DocumentPipelineResult:
    step: DocumentPipelineStep
    document: GeneratedDocument
    summary: str


@dataclass(frozen=True)
class DocumentAgentStepResult:
    step: DocumentPipelineStep
    document: GeneratedDocument | None
    text: str | None
    summary: str
    extracted_documents: tuple[ExtractedDocument, ...] = ()
    fidelity: Fidelity = Fidelity.HIGH
    fidelity_note: str | None = None
    assumptions: tuple[str, ...] = ()
    structured_content: StructuredDocumentContent | None = None


@dataclass(frozen=True)
class StructuredPipelineStep:
    document_type: DocumentType | None
    operation: DocumentOperation
    input_file: str | list[str] | None
    output_type: DocumentType | None
    parameters: dict[str, object]


class DocumentPipelineService:
    """Plan and execute validated document operations as one ordered pipeline."""

    _STEP_SEPARATOR = re.compile(r"\s*(?:,?\s+then\s+|;\s*)", re.IGNORECASE)
    _FILE_OPERATIONS = frozenset(
        {
            DocumentOperation.CONVERT_DOCUMENT,
            DocumentOperation.FORMAT_WORKBOOK,
            DocumentOperation.FILTER_COLUMN,
            DocumentOperation.SPLIT_BY_CATEGORY,
            DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY,
            DocumentOperation.EXTRACT_TABLE_TO_EXCEL,
            DocumentOperation.MODIFY_DOCUMENT,
            DocumentOperation.MODIFY_PDF_PAGES,
            DocumentOperation.MERGE_PDF,
            DocumentOperation.FILTER_AND_SORT_WORKBOOK,
            DocumentOperation.EXTRACT_MATCHING_ROWS,
            DocumentOperation.APPEND_WORKBOOK_ROWS,
        }
    )
    _TEXT_OPERATIONS = frozenset(
        {
            DocumentOperation.READ_DOCUMENT,
            DocumentOperation.EXTRACT_DOCUMENT,
            DocumentOperation.SUMMARIZE_DOCUMENT,
            DocumentOperation.ANALYZE_DOCUMENT,
            DocumentOperation.ANSWER_DOCUMENT_QUESTION,
        }
    )

    def __init__(
        self,
        *,
        intent_service: DocumentIntentService | None = None,
        document_service: ChatDocumentService | None = None,
        conversion_service: DocumentConversionService | None = None,
        validator: DocumentValidationService | None = None,
        modification_service: DocumentModificationService | None = None,
        max_steps: int | None = None,
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
        self.modification_service = modification_service or DocumentModificationService(
            reader=self.document_service.document_reader,
            validator=self.validator,
        )
        configured_max = max_steps or settings.DOCUMENT_PIPELINE_MAX_STEPS
        self.max_steps = max(1, min(configured_max, 20))

    def plan(self, instruction: str, *, input_filename: str) -> tuple[DocumentPipelineStep, ...]:
        """Keep the explicit `then` pipeline API backward compatible."""
        clauses = tuple(
            clause.strip(" ,")
            for clause in self._STEP_SEPARATOR.split(instruction.strip())
            if clause.strip(" ,")
        )
        if not clauses:
            raise UnsupportedDocumentOperationError(
                "Describe the ordered document steps you want me to perform."
            )
        if len(clauses) > self.max_steps:
            raise InvalidDocumentParametersError(
                f"A document plan can contain at most {self.max_steps} steps."
            )

        current_filename = Path(input_filename).name
        steps: list[DocumentPipelineStep] = []
        for position, clause in enumerate(clauses, start=1):
            explicit_input = self.intent_service.filename_in(clause)
            source_filename = Path(explicit_input or current_filename).name
            intent = self.intent_service.resolve(clause, input_file=source_filename)
            if intent.operation not in self._FILE_OPERATIONS:
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
            steps.append(
                DocumentPipelineStep(
                    position=position,
                    instruction=clause,
                    intent=intent,
                    explicitly_named_input=explicit_input is not None,
                    source_filenames=(source_filename,),
                    produces_document=True,
                )
            )
            current_filename = self._predicted_filename(
                source_filename, output_type, intent.operation, intent.parameters
            )
        return tuple(steps)

    def plan_structured(
        self,
        requested_steps: tuple[StructuredPipelineStep, ...],
        *,
        input_filename: str | None,
        request_instruction: str | None = None,
    ) -> tuple[DocumentPipelineStep, ...]:
        """Validate a complete AI-authored plan before touching document bytes."""
        if not requested_steps:
            raise UnsupportedDocumentOperationError("The document plan contains no steps.")
        # A simple file plan must not swallow an explicit request for chat text too.
        first = requested_steps[0]
        if (
            len(requested_steps) == 1
            and first.output_type is not None
            and isinstance(first.input_file or input_filename, str)
            and re.search(r"\b(?:extract|transcribe|read)\b", request_instruction or "", re.I)
            and re.search(
                r"\b(?:in(?:to)? (?:the )?chat|chatla|chatlayum|text (?:output|response))\b",
                request_instruction or "",
                re.I,
            )
        ):
            requested_steps = (
                StructuredPipelineStep(
                    document_type=None,
                    operation=DocumentOperation.EXTRACT_DOCUMENT,
                    input_file=first.input_file or input_filename,
                    output_type=None,
                    parameters={},
                ),
                *requested_steps,
            )
        if len(requested_steps) > self.max_steps:
            raise InvalidDocumentParametersError(
                f"A document plan can contain at most {self.max_steps} steps."
            )

        current_filenames = (Path(input_filename).name,) if input_filename else ()
        steps: list[DocumentPipelineStep] = []
        seen_steps: set[tuple[str, tuple[str, ...], str | None, str]] = set()
        for position, requested in enumerate(requested_steps, start=1):
            definition = self.intent_service.registry.get(requested.operation)
            if definition is None:
                raise UnsupportedDocumentOperationError(
                    "The requested document plan contains an unregistered operation."
                )

            explicit_sources = self._normalize_input_files(requested.input_file)
            source_filenames = explicit_sources or current_filenames
            self._validate_arity(definition, source_filenames, position)
            source_types = tuple(
                self.intent_service.registry.document_type_from_filename(filename)
                for filename in source_filenames
            )
            if definition.requires_input and any(
                source_type is None for source_type in source_types
            ):
                raise InvalidDocumentParametersError(
                    f"The input type for step {position} could not be determined."
                )
            typed_sources = tuple(source for source in source_types if source is not None)
            # The planner occasionally repeats a CREATE_DOCUMENT target in
            # `document_type`. That field describes the source type everywhere
            # else; CREATE_DOCUMENT's target is already carried by `output_type`.
            # Normalize the ambiguous model output to the actual selected source
            # so PDF -> DOCX creation is validated as a PDF input and DOCX output.
            declared_source_type = (
                None
                if requested.operation == DocumentOperation.CREATE_DOCUMENT
                else requested.document_type
            )
            if definition.requires_input and not set(typed_sources).issubset(
                definition.input_types
            ):
                raise InvalidDocumentParametersError(
                    f"{requested.operation.value} cannot use one or more selected file types."
                )
            if (
                declared_source_type is not None
                and typed_sources
                and any(source != declared_source_type for source in typed_sources)
            ):
                raise InvalidDocumentParametersError(
                    f"The document plan expected {declared_source_type.value.upper()}, but "
                    "one or more selected files have a different type."
                )

            parameters = self._structured_parameters(requested, definition)
            output_type = self._resolve_output_type(requested, definition, typed_sources)
            signature = (
                requested.operation.value,
                source_filenames,
                output_type.value if output_type is not None else None,
                json.dumps(parameters, sort_keys=True, default=str),
            )
            if signature in seen_steps:
                raise InvalidDocumentParametersError(
                    f"Step {position} duplicates an earlier operation in the same plan."
                )
            seen_steps.add(signature)
            if requested.operation == DocumentOperation.CONVERT_DOCUMENT:
                assert typed_sources and output_type is not None
                self.conversion_service.ensure_supported(typed_sources[0], output_type)

            instruction = self._structured_instruction(
                requested.operation,
                source_filenames,
                output_type,
                parameters,
                request_instruction=request_instruction,
            )
            intent = DocumentIntent(
                document_type=declared_source_type or (typed_sources[0] if typed_sources else None),
                operation=requested.operation,
                input_file=source_filenames[0] if source_filenames else None,
                output_type=output_type,
                parameters=parameters,
            )
            produces_document = output_type is not None
            steps.append(
                DocumentPipelineStep(
                    position=position,
                    instruction=instruction,
                    intent=intent,
                    explicitly_named_input=requested.input_file is not None,
                    source_filenames=source_filenames,
                    produces_document=produces_document,
                )
            )
            if produces_document:
                assert output_type is not None
                current_filenames = (
                    self._predicted_filename(
                        source_filenames[0] if source_filenames else "document",
                        output_type,
                        requested.operation,
                        parameters,
                    ),
                )
        return tuple(steps)

    def execute_step(
        self,
        step: DocumentPipelineStep,
        source: PipelineDocument | tuple[PipelineDocument, ...],
    ) -> DocumentPipelineResult:
        """Execute a file-producing deterministic step and validate its result."""
        sources = source if isinstance(source, tuple) else (source,)
        if not sources:
            raise InvalidDocumentError("The pipeline step has no source document.")
        if step.intent.operation == DocumentOperation.MERGE_PDF:
            modified = self.modification_service.merge_pdfs(
                tuple((item.file_data, item.filename) for item in sources)
            )
            generated = GeneratedDocument(
                file_data=modified.file_data,
                filename=modified.filename,
                content_type=modified.content_type,
                document_type=modified.document_type,
                fidelity=modified.fidelity,
                fidelity_note=modified.fidelity_note,
            )
            return self._validated_result(step, generated, modified.summary)
        if len(sources) != 1:
            raise InvalidDocumentParametersError(
                f"Step {step.position} accepts exactly one source document."
            )

        selected = sources[0]
        source_type = self.intent_service.registry.document_type_from_filename(selected.filename)
        if source_type is None:
            raise InvalidDocumentError("The source document type is not supported.")
        if step.intent.document_type is not None and step.intent.document_type != source_type:
            raise InvalidDocumentParametersError(
                f"Step {step.position} requires {step.intent.document_type.value.upper()}, but "
                f"{Path(selected.filename).name} is {source_type.value.upper()}."
            )
        self.validator.validate(selected.file_data, source_type)

        if step.intent.operation == DocumentOperation.EXTRACT_MATCHING_ROWS:
            selection = RowSelection.model_validate(step.intent.parameters)
            result = WorkbookRowSelector().select(selected.file_data, selection)
            generated = GeneratedDocument(
                file_data=result.file_data,
                filename=DocumentGenerationService.safe_filename(
                    f"{Path(selected.filename).stem}-selected-rows", DocumentType.XLSX
                ),
                content_type=DocumentGenerationService.MIME_TYPES[DocumentType.XLSX],
                document_type=DocumentType.XLSX,
                fidelity=Fidelity.HIGH,
                fidelity_note="Selected row values and column order preserved in a new workbook.",
            )
            summary = result.summary
        elif step.intent.operation == DocumentOperation.APPEND_WORKBOOK_ROWS:
            data, appended = WorkbookRowAppender().append(
                selected.file_data, AppendRowsRequest.model_validate(step.intent.parameters)
            )
            generated = GeneratedDocument(
                file_data=data,
                filename=DocumentGenerationService.safe_filename(
                    f"{Path(selected.filename).stem}-updated", DocumentType.XLSX
                ),
                content_type=DocumentGenerationService.MIME_TYPES[DocumentType.XLSX],
                document_type=DocumentType.XLSX,
                fidelity=Fidelity.HIGH,
                fidelity_note="New rows added to the original workbook; existing records and formulas preserved.",
            )
            summary = appended.summary
        elif step.intent.operation == DocumentOperation.CONVERT_DOCUMENT:
            assert step.intent.output_type is not None
            generated = self.conversion_service.convert(
                selected.file_data, selected.filename, step.intent.output_type
            )
            summary = (
                f"Converted {Path(selected.filename).name} to "
                f"{step.intent.output_type.value.upper()}. Actual fidelity: "
                f"{generated.fidelity.value}."
            )
            if generated.fidelity_note:
                summary = f"{summary} {generated.fidelity_note}"
        elif step.intent.operation == DocumentOperation.EXTRACT_TABLE_TO_EXCEL:
            extracted = self.document_service.extract_document(
                selected.file_data, selected.filename
            )
            data, filename, content_type = self.document_service.extract_tables_to_excel(
                extracted, selected.filename
            )
            generated = GeneratedDocument(
                file_data=data,
                filename=filename,
                content_type=content_type,
                document_type=DocumentType.XLSX,
                fidelity=Fidelity.HIGH,
                fidelity_note=(
                    "Extracted table values and order were validated; the source's visual "
                    "layout is not retained in Excel."
                ),
            )
            summary = f"Extracted tables from {Path(selected.filename).name} to Excel."
        elif step.intent.operation in {
            DocumentOperation.MODIFY_DOCUMENT,
            DocumentOperation.MODIFY_PDF_PAGES,
            DocumentOperation.FILTER_AND_SORT_WORKBOOK,
        }:
            edits_value = step.intent.parameters.get("edits")
            if not isinstance(edits_value, list) or not edits_value:
                raise InvalidDocumentParametersError(
                    "A modification step requires at least one targeted edit."
                )
            edits = tuple(DocumentEdit.model_validate(item) for item in edits_value)
            modified = self.modification_service.modify(
                selected.file_data, selected.filename, edits
            )
            generated = GeneratedDocument(
                file_data=modified.file_data,
                filename=modified.filename,
                content_type=modified.content_type,
                document_type=modified.document_type,
                fidelity=modified.fidelity,
                fidelity_note=modified.fidelity_note,
            )
            summary = modified.summary
        elif step.intent.operation in self._FILE_OPERATIONS:
            data, filename, actions = self.document_service.format_spreadsheet(
                selected.file_data, selected.filename, step.instruction
            )
            generated = GeneratedDocument(
                file_data=data,
                filename=filename,
                content_type=DocumentGenerationService.MIME_TYPES[DocumentType.XLSX],
                document_type=DocumentType.XLSX,
                fidelity=(
                    Fidelity.HIGH
                    if step.intent.operation == DocumentOperation.SPLIT_BY_CATEGORY
                    else Fidelity.FULL
                ),
                fidelity_note=(
                    "Rows were reorganized into category sheets; cell values were preserved."
                    if step.intent.operation == DocumentOperation.SPLIT_BY_CATEGORY
                    else None
                ),
            )
            summary = f"Updated {Path(selected.filename).name}: {', '.join(actions)}."
        else:
            raise UnsupportedDocumentOperationError(
                f"{step.intent.operation.value} is not a file-producing operation."
            )
        return self._validated_result(step, generated, summary)

    async def execute_agent_step(
        self,
        step: DocumentPipelineStep,
        sources: tuple[PipelineDocument, ...],
        *,
        request_instruction: str,
        history: list[ChatHistoryMessage] | None = None,
        context_documents: tuple[ExtractedDocument, ...] = (),
        context_texts: tuple[str, ...] = (),
        on_extracted: Callable[[PipelineDocument, ExtractedDocument], None] | None = None,
    ) -> DocumentAgentStepResult:
        """Execute reading, analysis, creation, conversion, or modification uniformly."""
        if step.intent.operation in self._FILE_OPERATIONS:
            result = await asyncio.to_thread(
                self.execute_step,
                step,
                sources if len(sources) != 1 else sources[0],
            )
            return DocumentAgentStepResult(
                step=step,
                document=result.document,
                text=None,
                summary=result.summary,
                fidelity=result.document.fidelity,
                fidelity_note=result.document.fidelity_note,
            )

        if step.intent.operation == DocumentOperation.CREATE_DOCUMENT:
            if step.intent.output_type is None:
                raise InvalidDocumentParametersError(
                    "A document creation step must specify an output type."
                )
            extracted_sources = list(context_documents)
            known_names = {
                item.source.filename
                for item in extracted_sources
                if item.source and item.source.filename
            }
            for source in sources:
                if source.filename not in known_names:
                    extracted_source = await asyncio.to_thread(
                        self.document_service.extract_document,
                        source.file_data,
                        source.filename,
                        **(
                            {"image_instruction": step.instruction or request_instruction}
                            if self.intent_service.registry.document_type_from_filename(
                                source.filename
                            )
                            == DocumentType.IMAGE
                            else {}
                        ),
                    )
                    extracted_sources.append(extracted_source)
                    if on_extracted is not None:
                        on_extracted(source, extracted_source)
            if (
                context_texts
                and step.intent.output_type
                in {DocumentType.PDF, DocumentType.DOCX, DocumentType.TXT, DocumentType.MARKDOWN}
                and re.search(
                    r"\bsame (?:extracted )?(?:information|content|data|text)\b"
                    r"|\b(?:do not|don't) summari[sz]e\b|\bverbatim\b",
                    request_instruction,
                    re.I,
                )
            ):
                # Export the actual text results without a second model rewriting them.
                requested_filename = step.intent.parameters.get("filename")
                generated = await asyncio.to_thread(
                    self.document_service.document_generator.generate,
                    "\n\n".join(context_texts),
                    step.intent.output_type.value,
                    requested_filename=(str(requested_filename) if requested_filename else None),
                )
                if any(item.document_type == DocumentType.IMAGE for item in extracted_sources):
                    generated = replace(
                        generated,
                        fidelity=Fidelity.BEST_EFFORT,
                        fidelity_note="Exported the image transcription; recognition errors may remain.",
                    )
                return replace(
                    self._agent_document_result(step, generated, f"Created {generated.filename}."),
                    extracted_documents=tuple(extracted_sources),
                )
            generated_content = await self.document_service.generate_content(
                step.instruction or request_instruction,
                list(context_texts),
                history or [],
                documents=extracted_sources,
            )
            requested_filename = step.intent.parameters.get("filename")
            if generated_content.assumptions:
                # Keep assumptions out of the main body copy — surface them as a
                # clearly labeled closing section instead of interleaving
                # "Assumed: ..." lines between real content paragraphs.
                generated_content.sections.append(
                    GeneratedSectionContent(
                        heading="Assumptions",
                        bullet_lists=[list(generated_content.assumptions)],
                    )
                )
            data, filename, content_type = await asyncio.to_thread(
                self.document_service.render,
                generated_content,
                step.intent.output_type.value,
                requested_filename=(
                    str(requested_filename) if requested_filename is not None else None
                ),
            )
            generated = GeneratedDocument(
                file_data=data,
                filename=filename,
                content_type=content_type,
                document_type=step.intent.output_type,
                fidelity=(
                    Fidelity.BEST_EFFORT
                    if any(item.document_type == DocumentType.IMAGE for item in extracted_sources)
                    else Fidelity.HIGH
                ),
                fidelity_note=(
                    "Created from image transcription. Review extracted labels and numbers."
                    if any(item.document_type == DocumentType.IMAGE for item in extracted_sources)
                    else None
                ),
            )
            return replace(
                self._agent_document_result(step, generated, f"Created {filename}."),
                assumptions=tuple(generated_content.assumptions),
                extracted_documents=tuple(extracted_sources),
                structured_content=generated_content,
            )

        if step.intent.operation not in self._TEXT_OPERATIONS:
            raise UnsupportedDocumentOperationError(
                f"{step.intent.operation.value} is not executable in a document pipeline."
            )
        if len(sources) != 1:
            raise InvalidDocumentParametersError(
                f"Step {step.position} requires exactly one source document."
            )
        selected = sources[0]
        extracted = await asyncio.to_thread(
            self.document_service.extract_document,
            selected.file_data,
            selected.filename,
            **(
                {"image_instruction": request_instruction}
                if self.intent_service.registry.document_type_from_filename(selected.filename)
                == DocumentType.IMAGE
                else {}
            ),
        )
        if on_extracted is not None:
            on_extracted(selected, extracted)
        if step.intent.operation in {
            DocumentOperation.READ_DOCUMENT,
            DocumentOperation.EXTRACT_DOCUMENT,
        }:
            text = (
                ImageDocumentReader.display_text(extracted.text)
                if extracted.document_type == DocumentType.IMAGE
                else extracted.text
            )
        elif step.intent.operation == DocumentOperation.SUMMARIZE_DOCUMENT:
            text = await self.document_service.summarize(extracted, step.instruction)
        else:
            text = await self.document_service.analyze(extracted, step.instruction)
        if not text.strip():
            raise InvalidDocumentError(
                f"{Path(selected.filename).name} did not contain readable content."
            )
        assessment = self.validator.fidelity_for_extraction(extracted)
        if (
            step.intent.operation
            not in {
                DocumentOperation.READ_DOCUMENT,
                DocumentOperation.EXTRACT_DOCUMENT,
            }
            and assessment.fidelity == Fidelity.FULL
        ):
            assessment = type(assessment)(
                Fidelity.HIGH,
                "The response is an AI interpretation of fully extracted document content.",
            )
        return DocumentAgentStepResult(
            step=step,
            document=None,
            text=text,
            summary=f"Processed {Path(selected.filename).name} for the requested result.",
            extracted_documents=(extracted,),
            fidelity=assessment.fidelity,
            fidelity_note=assessment.note,
        )

    def _agent_document_result(
        self, step: DocumentPipelineStep, generated: GeneratedDocument, summary: str
    ) -> DocumentAgentStepResult:
        validated = self._validated_result(step, generated, summary)
        return DocumentAgentStepResult(
            step=step,
            document=validated.document,
            text=None,
            summary=validated.summary,
            fidelity=validated.document.fidelity,
            fidelity_note=validated.document.fidelity_note,
        )

    def _validated_result(
        self, step: DocumentPipelineStep, generated: GeneratedDocument, summary: str
    ) -> DocumentPipelineResult:
        expected_type = step.intent.output_type
        actual_type = self.intent_service.registry.document_type_from_filename(generated.filename)
        if (
            expected_type is None
            or generated.document_type != expected_type
            or actual_type != expected_type
        ):
            raise InvalidDocumentError(
                f"Step {step.position} produced the wrong document type; the output was not saved."
            )
        self.validator.validate(generated.file_data, generated.document_type)
        return DocumentPipelineResult(step=step, document=generated, summary=summary)

    @staticmethod
    def _normalize_input_files(value: str | list[str] | None) -> tuple[str, ...]:
        if value is None:
            return ()
        values = value if isinstance(value, list) else [value]
        return tuple(Path(item).name for item in values if Path(item).name)

    @staticmethod
    def _validate_arity(
        definition: DocumentOperationDefinition,
        source_filenames: tuple[str, ...],
        position: int,
    ) -> None:
        if definition.requires_input and not source_filenames:
            raise InvalidDocumentParametersError(f"Step {position} requires a source document.")
        if definition.input_arity == InputArity.SINGLE and definition.requires_input:
            if len(source_filenames) != 1:
                raise InvalidDocumentParametersError(
                    f"Step {position} accepts exactly one source document."
                )
        if definition.input_arity == InputArity.MULTI and len(source_filenames) < 2:
            raise InvalidDocumentParametersError(
                f"Step {position} requires at least two source documents."
            )

    def _structured_parameters(
        self,
        requested: StructuredPipelineStep,
        definition: DocumentOperationDefinition,
    ) -> dict[str, object]:
        keys = set(requested.parameters)
        missing = definition.required_parameters - keys
        unexpected = keys - definition.required_parameters - definition.optional_parameters
        if missing:
            raise InvalidDocumentParametersError(
                "The document plan is missing required parameters: " + ", ".join(sorted(missing))
            )
        if unexpected:
            raise InvalidDocumentParametersError(
                "The document plan contains unsupported parameters."
            )
        parameters = dict(requested.parameters)
        if requested.operation == DocumentOperation.APPEND_WORKBOOK_ROWS:
            try:
                return AppendRowsRequest.model_validate(parameters).model_dump(exclude_none=True)
            except ValidationError as error:
                raise InvalidDocumentParametersError(
                    "Paste the new rows to add and optionally name the worksheet."
                ) from error
        if requested.operation == DocumentOperation.EXTRACT_MATCHING_ROWS:
            try:
                parameters = RowSelection.model_validate(parameters).model_dump(exclude_none=True)
            except ValidationError as error:
                raise InvalidDocumentParametersError(
                    "Row selection requires 1 to 200 exact ID strings and optional column/sheet names."
                ) from error
        if requested.operation == DocumentOperation.FILTER_COLUMN:
            column = parameters.get("column")
            if not isinstance(column, str) or not column.strip():
                raise InvalidDocumentParametersError(
                    "The document plan must specify which column should be filtered."
                )
            parameters["column"] = column.strip()
        if requested.operation in {
            DocumentOperation.MODIFY_DOCUMENT,
            DocumentOperation.MODIFY_PDF_PAGES,
            DocumentOperation.FILTER_AND_SORT_WORKBOOK,
        }:
            edits = parameters.get("edits")
            if not isinstance(edits, list) or not edits:
                raise InvalidDocumentParametersError(
                    "A modification step requires at least one targeted edit."
                )
            parameters["edits"] = [
                DocumentEdit.model_validate(edit).model_dump(mode="json") for edit in edits
            ]
        for key in ("title", "filename", "focus", "question"):
            if key in parameters and (
                not isinstance(parameters[key], str) or not str(parameters[key]).strip()
            ):
                raise InvalidDocumentParametersError(
                    f"The document plan contains an invalid {key} parameter."
                )
        return parameters

    def _resolve_output_type(
        self,
        requested: StructuredPipelineStep,
        definition: DocumentOperationDefinition,
        source_types: tuple[DocumentType, ...],
    ) -> DocumentType | None:
        if not definition.output_types:
            if requested.output_type is not None:
                raise InvalidDocumentParametersError(
                    f"{requested.operation.value} returns text, not a document file."
                )
            return None
        if requested.output_type is not None:
            output_type = requested.output_type
        elif (
            requested.operation
            in {
                DocumentOperation.MODIFY_DOCUMENT,
                DocumentOperation.MODIFY_PDF_PAGES,
                DocumentOperation.FILTER_AND_SORT_WORKBOOK,
            }
            and source_types
        ):
            output_type = source_types[0]
        elif len(definition.output_types) == 1:
            output_type = next(iter(definition.output_types))
        else:
            raise InvalidDocumentParametersError(
                "The document plan must specify the output file type."
            )
        if output_type not in definition.output_types:
            raise InvalidDocumentParametersError(
                f"{output_type.value.upper()} is not a valid output for "
                f"{requested.operation.value}."
            )
        if (
            requested.operation
            in {
                DocumentOperation.MODIFY_DOCUMENT,
                DocumentOperation.MODIFY_PDF_PAGES,
                DocumentOperation.FILTER_AND_SORT_WORKBOOK,
            }
            and source_types
            and any(source != output_type for source in source_types)
        ):
            raise InvalidDocumentParametersError(
                f"{requested.operation.value} must preserve the source document type."
            )
        return output_type

    @staticmethod
    def _predicted_filename(
        current_filename: str,
        output_type: DocumentType,
        operation: DocumentOperation,
        parameters: dict[str, object] | None = None,
    ) -> str:
        if operation == DocumentOperation.CONVERT_DOCUMENT:
            return DocumentConversionService.output_filename(current_filename, output_type)
        if operation == DocumentOperation.CREATE_DOCUMENT:
            requested = (parameters or {}).get("filename") or (parameters or {}).get("title")
            return DocumentGenerationService.safe_filename(
                str(requested) if requested else "document", output_type
            )
        if operation == DocumentOperation.MERGE_PDF:
            return "merged_updated.pdf"
        if operation == DocumentOperation.EXTRACT_MATCHING_ROWS:
            return DocumentGenerationService.safe_filename(
                f"{Path(current_filename).stem}-selected-rows", output_type
            )
        if operation == DocumentOperation.APPEND_WORKBOOK_ROWS:
            return DocumentGenerationService.safe_filename(
                f"{Path(current_filename).stem}-updated", output_type
            )
        stem = Path(current_filename).stem or "document"
        return DocumentGenerationService.safe_filename(f"{stem}-pipeline", output_type)

    @staticmethod
    def _structured_instruction(
        operation: DocumentOperation,
        source_filenames: tuple[str, ...],
        output_type: DocumentType | None,
        parameters: dict[str, object],
        *,
        request_instruction: str | None,
    ) -> str:
        source = source_filenames[0] if source_filenames else "the supplied context"
        if operation in {
            DocumentOperation.CREATE_DOCUMENT,
            DocumentOperation.READ_DOCUMENT,
            DocumentOperation.EXTRACT_DOCUMENT,
            DocumentOperation.SUMMARIZE_DOCUMENT,
            DocumentOperation.ANALYZE_DOCUMENT,
            DocumentOperation.ANSWER_DOCUMENT_QUESTION,
        }:
            return request_instruction or str(parameters.get("question") or operation.value)
        if operation == DocumentOperation.CONVERT_DOCUMENT and output_type is not None:
            return f"Convert {source} to {output_type.value.upper()}"
        if operation == DocumentOperation.EXTRACT_TABLE_TO_EXCEL:
            return f"Extract the tables from {source} to Excel"
        if operation == DocumentOperation.FILTER_COLUMN:
            return f"Add a filter to the {parameters['column']} column"
        if operation == DocumentOperation.EXTRACT_MATCHING_ROWS:
            return request_instruction or "Extract complete rows matching the requested IDs"
        if operation == DocumentOperation.APPEND_WORKBOOK_ROWS:
            return request_instruction or "Append the supplied rows to the workbook"
        if operation == DocumentOperation.SPLIT_BY_CATEGORY:
            return "Split the workbook into one sheet per category"
        if operation == DocumentOperation.EXPAND_DISH_BY_DIETARY_CATEGORY:
            return "Create category rows for every x"
        if operation == DocumentOperation.MERGE_PDF:
            return "Merge the selected PDF files"
        if operation in {
            DocumentOperation.MODIFY_DOCUMENT,
            DocumentOperation.MODIFY_PDF_PAGES,
            DocumentOperation.FILTER_AND_SORT_WORKBOOK,
        }:
            return request_instruction or "Apply the requested targeted edits"
        return "Format the workbook professionally"
