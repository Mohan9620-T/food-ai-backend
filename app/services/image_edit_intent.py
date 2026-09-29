"""Route editing commands honestly while the hosted generator is text-only."""

import re

_EDIT_COMMAND = re.compile(
    r"^\s*(?:(?:please|can you|could you|would you)\s+)*"
    r"(?:enhance|edit|retouch|upscale|brighten|darken|crop|recolou?r|"
    r"change|replace|remove|add|make|improve|adjust)\b",
    re.I,
)
_VISUAL_TARGET = re.compile(
    r"\b(?:image|picture|photo|portrait|background|foreground|lighting|cinematic|"
    r"atmosphere|colou?rs?|contrast|brightness|shadows?|saturation|resolution|"
    r"brighter|darker|realistic|sharper|blur|shirt|clothes|wings|hair)\b",
    re.I,
)
_OTHER_TASK = re.compile(
    r"\b(?:spreadsheet|workbook|excel|pdf|document|docx|word|code|function|paragraph|"
    r"sentence|prompt|website|css|html)\b",
    re.I,
)


def image_edit_response(message: str) -> str | None:
    """Call only with an attached or currently referenced image."""
    if not _EDIT_COMMAND.search(message) or _OTHER_TASK.search(message):
        return None
    if not _VISUAL_TARGET.search(message):
        return None
    return (
        "The current image tool can create a new image from text, but it cannot edit the existing image. "
        "To make a new version, select **Create image** and include the original subject plus your requested changes. "
        "The result will be a new image; the same face, pose and composition cannot be guaranteed."
    )
