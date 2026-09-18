"""Resolve workbook lookups against saved, session-owned file bytes."""

from dataclasses import dataclass
from typing import cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.chat import ChatDocumentAttachment
from app.repositories.chat_repository import ChatRepository
from app.services.document.exceptions import InvalidDocumentError
from app.services.spreadsheet.row_selection import RowSelection, SelectedRows, WorkbookRowSelector


@dataclass(frozen=True)
class WorkbookLookup:
    source: ChatDocumentAttachment
    selection: RowSelection
    file_data: bytes

    def select(self) -> SelectedRows:
        return WorkbookRowSelector().select(self.file_data, self.selection)

    def answer(self) -> str:
        try:
            return self.select().markdown()
        except InvalidDocumentError as error:
            return str(error)


def prepare_workbook_lookup(
    db: Session, session_id: int, instruction: str
) -> WorkbookLookup | None:
    # Call only after the endpoint has verified session ownership.
    try:
        selection = RowSelection.from_instruction(instruction, workbook_context=True)
    except ValidationError as error:
        raise InvalidDocumentError(
            "Specify at most 200 IDs, each at most 128 characters."
        ) from error
    if selection is None:
        return None
    source = ChatRepository().get_row_selection_source(db, session_id, instruction)
    return (
        WorkbookLookup(source, selection, cast(bytes, source.file_data))
        if source is not None
        else None
    )
