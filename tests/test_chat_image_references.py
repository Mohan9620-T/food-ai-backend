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


def test_explicit_generated_reference_does_not_choose_a_newer_upload():
    generated = ChatMessageRecord(sender="bot", content="Draw a portrait")
    uploaded = ChatMessageRecord(sender="user", content="Another photo")
    turns = [(generated, "A generated image"), (uploaded, "An uploaded image")]
    assert _select_referenced_image("Describe the generated image", turns) is generated


def test_compare_current_pair_does_not_drop_the_reference_image():
    from app.models.chat import ChatImageAttachment

    generated = ChatMessageRecord(sender="bot", content="Draw a portrait")
    comparison = ChatMessageRecord(
        sender="user",
        content="Compare both",
        additional_images=[ChatImageAttachment(image_data=b"reference", content_type="image/png")],
    )
    turns = [(generated, "A generated portrait"), (comparison, "Both compared")]
    assert (
        _select_referenced_image("Compare the generated image and reference image", turns)
        is comparison
    )
    assert _select_referenced_image("Compare BMW and Audi prices", turns) is None
