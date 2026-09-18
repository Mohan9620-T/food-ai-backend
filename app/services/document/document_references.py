"""Distinguish source references from a new request's output format or topic."""

import re
from collections.abc import Iterable


def references_image(message: str) -> bool:
    instruction = re.sub(r'"[^\"]*"|`[^`]*`', " ", message).casefold()
    return bool(re.search(r"\b(?:image|photo|picture|screenshot)\b", instruction))


def requests_new_document(message: str, filenames: Iterable[str] = ()) -> bool:
    """A fresh creation request must not inherit an earlier wizard's source/answers."""
    return bool(
        re.search(
            r"\b(?:create|generate|make|build|prepare|draft|write)\s+(?:(?:a|an|the|brand)\s+)*new\b"
            r"|\bfrom\s+scratch\b",
            message,
            re.I,
        )
    ) and not references_document(message, filenames, creation=True)


def matches_document_topic(message: str, text: str) -> bool:
    """Retain natural follow-ups such as 'What is the oats revenue?'."""
    stop_words = set(
        "what which who when where why how does do did is are was were will would can could "
        "should have has had the this that these those about for from with and but not "
        "you your me my our their tell explain give show know please document file data "
        "content table sheet spreadsheet workbook excel pdf word read create generate "
        "list all any more some its into only answer question".split()
    )
    terms = {
        word for word in re.findall(r"\b[a-z]{3,}\b", message.casefold()) if word not in stop_words
    }
    source_words = set(re.findall(r"\b[a-z]{3,}\b", text.casefold()))
    return bool(terms & source_words)


def references_document(
    message: str, filenames: Iterable[str] = (), *, creation: bool = False
) -> bool:
    # Quoted content is data, not a request to select an older attachment.
    instruction = re.sub(r'"[^"]*"|“[^”]*”|`[^`]*`', " ", message).casefold()
    if any(filename.casefold() in instruction for filename in filenames):
        return True
    modifiers = (
        "this|that|these|those|uploaded|attached|provided|selected|"
        "latest|last|current|first|second|third|previous|prior"
    )
    if not creation:
        modifiers += "|the|my|our"
    nouns = (
        "content|document|file|pdf|word|docx|excel|xlsx|csv|powerpoint|pptx|"
        "spreadsheet|workbook|table|data|image|photo|picture|screenshot"
    )
    return bool(
        re.search(
            rf"\b(?:{modifiers})\s+"
            r"(?:(?:uploaded|attached|source|original|previous|existing)\s+)?"
            rf"(?:{nouns})\b"
            rf"|\b(?:from|using|based on|summary of|version of)\s+(?:the|my|our)\s+(?:{nouns})\b"
            r"|\b(?:from|using|based on)\s+(?:this|that|it|them)\b"
            r"|\b(?:from|using)\s+(?:an?\s+)?(?:image|photo|picture|screenshot)\b"
            r"|\b(?:read|summari[sz]e|analy[sz]e|convert|edit|update|make|turn)\s+(?:this|that|it|them)\b"
            r"|\b(?:itha|indha)\b",
            instruction,
        )
    )
