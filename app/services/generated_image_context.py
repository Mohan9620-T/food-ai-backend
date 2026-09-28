"""Answer identity follow-ups from generation provenance, never from a face guess."""

import re
from typing import cast
from urllib.parse import quote

from sqlalchemy.orm import QueryableAttribute, Session, defer

from app.models.chat import ChatDocumentAttachment
from app.repositories.chat_repository import ChatRepository

_IDENTITY_FOLLOW_UP = re.compile(
    r"\s*(?:(?:can|could) you (?:tell me |explain )?)?"
    r"(?:who (?:is|was) (?:he|she|this|that|it|(?:this|that|the) (?:person|man|woman))|"
    r"what is (?:his|her|their) name|who (?:is|was) (?:in|shown in) (?:this|that|the) (?:image|picture|photo))"
    r"\s*[?.!]*\s*",
    re.I,
)


def generated_image_follow_up(db: Session, session_id: int, message: str) -> str | None:
    if not _IDENTITY_FOLLOW_UP.fullmatch(message):
        return None
    # Only the active subject: an unrelated exchange or a newer upload must not
    # silently select an older generated portrait.
    recent = ChatRepository().get_message_history(db, session_id, limit=24)
    if not recent or recent[-1].sender != "bot":
        return None
    attachment = (
        db.query(ChatDocumentAttachment)
        .options(defer(cast(QueryableAttribute, ChatDocumentAttachment.file_data)))
        .filter(
            ChatDocumentAttachment.session_id == session_id,
            ChatDocumentAttachment.kind == "generated",
            ChatDocumentAttachment.content_type.like("image/%"),
        )
        .order_by(ChatDocumentAttachment.message_id.desc())
        .first()
    )
    if attachment is None:
        return None
    if not any(item.id == attachment.message_id for item in recent):
        return None
    for item in recent:
        if item.id > attachment.message_id and item.sender == "user":
            content = cast(str, item.content)
            if item.image_data or not (
                _IDENTITY_FOLLOW_UP.fullmatch(content)
                or re.fullmatch(r"\s*(?:thanks|thank you|ok|okay)\s*[!.]*\s*", content, re.I)
            ):
                return None
    metadata = cast(dict, attachment.generation_metadata or {})
    subject = metadata.get("subject")
    source = metadata.get("subject_source_url")
    if subject and source:
        source_url = quote(source, safe="/:?&=%#@!$+,-._~")
        return (
            f"The image was intended to depict **{subject}**, based on the saved generation "
            f"request and [source used at generation time](<{source_url}>). "
            "It is an AI-generated illustration, not a verified photograph. "
            "I can't confirm that the generated face accurately resembles that person. "
            "If the likeness is wrong, the image should not be used as their portrait."
        )
    prompt = str(metadata.get("prompt") or "the previous image request")
    # Existing images also retain their original prompt; never invent a subject
    # retroactively just because an old request mentioned a public office.
    prompt = re.sub(r"[\[\]<>`*_\\]", "", prompt)[:1000]
    return (
        f'This is an AI-generated illustration created for your request: "{prompt}". '
        "That image has no verified person's name saved with it, so I can't reliably name "
        "the person or claim it depicts the requested official. The earlier generic success "
        "message did not establish an accurate likeness."
    )
