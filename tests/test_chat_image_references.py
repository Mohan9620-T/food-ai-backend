import pytest

from app.api.chat import _select_referenced_image
from app.models.chat import ChatMessageRecord
from app.schemas.chat import ChatHistoryMessage


@pytest.mark.parametrize(
    "question",
    [
        "Can you identify it?",
        "Please describe this",
        "Explain this",
        "What is that?",
        "Tell me more",
    ],
)
@pytest.mark.parametrize("intervening_conversation", [False, True])
def test_implicit_visual_follow_up_requires_current_image_exchange(
    question, intervening_conversation
):
    image = ChatMessageRecord(sender="user", content="What is shown?")
    answer = "The picture contains several foods."
    history = [
        ChatHistoryMessage(role="user", content=image.content),
        ChatHistoryMessage(role="assistant", content=answer),
    ]
    if intervening_conversation:
        history.extend(
            [
                ChatHistoryMessage(role="user", content="How do solar panels work?"),
                ChatHistoryMessage(role="assistant", content="They convert sunlight to power."),
            ]
        )

    selected = _select_referenced_image(question, [(image, answer)], history)

    assert selected is (None if intervening_conversation else image)


@pytest.mark.parametrize(
    "question",
    ["Describe the uploaded photo", "What is in this image?", "Explain my diagram"],
)
def test_explicit_image_reference_still_selects_historical_image(question):
    image = ChatMessageRecord(sender="user", content="Earlier upload")

    assert _select_referenced_image(question, [(image, "A scene")]) is image


def test_empty_question_does_not_select_historical_image():
    image = ChatMessageRecord(sender="user", content="Earlier upload")

    assert _select_referenced_image("", [(image, "A scene")]) is None
