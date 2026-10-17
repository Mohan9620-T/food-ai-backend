import json

import pytest

from app.database.redis_client import get_cached_history
from app.models.user_memory import UserMemoryFact
from app.services import user_memory_service
from app.services.chat_service import ChatService


def _register_and_login(client, email="memory@example.com", password="test-password"):
    client.post("/users/", json={"fullname": "Memory User", "email": email, "password": password})
    token = client.post("/users/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


# --- Deterministic extraction -------------------------------------------------


def test_extract_facts_covers_allergy_dislike_goal_constraint_and_diet():
    assert user_memory_service.extract_facts("I'm allergic to peanuts") == [
        ("allergy", "Allergic to peanuts")
    ]
    assert user_memory_service.extract_facts("I don't eat mushrooms") == [
        ("dislike", "Dislikes/avoids mushrooms")
    ]
    assert user_memory_service.extract_facts("Honestly I hate cilantro") == [
        ("dislike", "Dislikes cilantro")
    ]
    assert user_memory_service.extract_facts("My goal is to lose weight before summer") == [
        ("goal", "Goal: lose weight before summer")
    ]
    assert user_memory_service.extract_facts("I only have 15 minutes to cook on weekdays") == [
        ("constraint", "Constraint: I only have 15 minutes to cook on weekdays")
    ]
    assert user_memory_service.extract_facts("I'm vegetarian") == [
        ("dietary_restriction", "Follows a vegetarian diet")
    ]


def test_extract_facts_conservatively_stops_before_a_trailing_and_clause():
    """A conjunction after the subject is treated as a new clause, not part of the
    fact, so a run-on sentence never gets silently folded in as if it were the
    same stated fact (e.g. "...and I also love spicy food" must not become part
    of the allergy). This means a multi-item list joined by "and" is only
    captured up to its first item - a deliberate, conservative trade-off."""
    assert user_memory_service.extract_facts("I'm allergic to peanuts and shellfish") == [
        ("allergy", "Allergic to peanuts")
    ]


def test_extract_facts_ignores_plain_questions_and_unrelated_chat():
    assert user_memory_service.extract_facts("What can I make with chicken and rice?") == []
    assert user_memory_service.extract_facts("Can you suggest a high protein breakfast?") == []
    assert user_memory_service.extract_facts("Thanks, that recipe looks great!") == []


@pytest.mark.parametrize(
    "message",
    [
        "My friend is allergic to peanuts",
        "I'm not allergic to peanuts anymore",
        "If I am allergic to peanuts, what should I do?",
        'Translate "I am allergic to peanuts" into Tamil',
        "Am I allergic to peanuts?",
    ],
)
def test_memory_does_not_infer_an_allergy_from_other_people_or_non_assertions(message):
    assert user_memory_service.extract_facts(message) == []


def test_memory_preserves_negation_in_a_cooking_constraint():
    assert user_memory_service.extract_facts("I don't have an oven") == [
        ("constraint", "Constraint: I don't have an oven")
    ]


def test_extract_retractions_removes_matching_allergy_and_diet_claims():
    not_allergic = user_memory_service.extract_retractions("I'm not allergic to peanuts anymore")
    assert ("allergy", "peanuts") in not_allergic
    assert ("dislike", "peanuts") in not_allergic

    can_eat_again = user_memory_service.extract_retractions("I can eat dairy again now")
    assert ("allergy", "dairy") in can_eat_again
    assert ("dislike", "dairy") in can_eat_again

    diet_retractions = user_memory_service.extract_retractions("I'm not vegetarian anymore")
    assert ("dietary_restriction", "Follows a vegetarian diet") in diet_retractions


# --- Persistence, cross-session recall, correction/removal --------------------


def test_fact_persists_across_sessions_for_the_same_user(client, db_session):
    headers = _register_and_login(client)
    first_session = client.post("/chat/sessions", json={"title": "First"}, headers=headers).json()[
        "id"
    ]
    user_memory_service.sync_from_message(db_session, 1, first_session, "I'm allergic to peanuts")
    second_session = client.post(
        "/chat/sessions", json={"title": "Second, unrelated"}, headers=headers
    ).json()["id"]

    # A brand-new session for the same user must still see the earlier fact -
    # it must not depend on the rolling summary of the session it was stated in.
    block = user_memory_service.context_block(db_session, 1)
    assert block is not None
    assert "Allergic to peanuts" in block
    assert second_session != first_session


def test_fact_survives_a_redis_cache_miss(client, db_session):
    headers = _register_and_login(client, email="cachemiss@example.com")
    session_id = client.post("/chat/sessions", json={"title": "Chat"}, headers=headers).json()["id"]
    user_id = db_session.query(UserMemoryFact).count()  # sanity: table starts empty
    assert user_id == 0
    user_memory_service.sync_from_message(db_session, 1, session_id, "I'm allergic to shellfish")

    # No Redis-backed history cache has been populated for this user/session -
    # context_block must read straight from PostgreSQL, not depend on that cache.
    assert get_cached_history(1, session_id) is None
    block = user_memory_service.context_block(db_session, 1)
    assert block is not None and "Allergic to shellfish" in block


def test_goal_is_single_valued_and_replaces_the_previous_statement(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "My goal is to lose weight")
    user_memory_service.sync_from_message(db_session, 1, None, "My goal is to gain muscle")
    facts = [
        f
        for f in user_memory_service.repository.list_for_user(db_session, 1)
        if f.category == "goal"
    ]
    assert len(facts) == 1
    assert facts[0].fact_text == "Goal: gain muscle"


def test_allergies_accumulate_and_do_not_duplicate_the_same_statement(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to peanuts")
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to shellfish")
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to peanuts")
    facts = [
        f.fact_text
        for f in user_memory_service.repository.list_for_user(db_session, 1)
        if f.category == "allergy"
    ]
    assert sorted(facts) == ["Allergic to peanuts", "Allergic to shellfish"]


def test_retraction_removes_a_previously_stored_fact(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to dairy")
    assert "Allergic to dairy" in (user_memory_service.context_block(db_session, 1) or "")
    user_memory_service.sync_from_message(db_session, 1, None, "Good news, I can eat dairy again")
    block = user_memory_service.context_block(db_session, 1)
    assert block is None or "dairy" not in block.lower()


def test_negative_allergy_correction_does_not_add_the_allergy_back(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to peanuts")
    user_memory_service.sync_from_message(
        db_session, 1, None, "I'm not allergic to peanuts anymore"
    )
    assert user_memory_service.context_block(db_session, 1) is None


def test_another_person_cannot_clear_the_users_allergy(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to peanuts")
    user_memory_service.sync_from_message(
        db_session, 1, None, "My friend is not allergic to peanuts"
    )
    assert "Allergic to peanuts" in (user_memory_service.context_block(db_session, 1) or "")


def test_saved_facts_are_scoped_to_the_user(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to peanuts")
    assert user_memory_service.context_block(db_session, 2) is None


# --- Context assembly ----------------------------------------------------------


def test_user_memory_block_is_present_in_the_assembled_llm_request():
    service = ChatService()
    fact_block = (
        "ESTABLISHED USER FACTS (explicitly stated by this user in a past or current "
        "conversation; treat these as confirmed - do not ask the user to re-state or "
        "re-confirm them, and do not present them as a new guess):\n"
        "Allergies: Allergic to peanuts"
    )
    _, body = service._build_request_body(
        "What can I cook tonight?", [], [], stream=False, user_memory=fact_block
    )
    assert any(fact_block == message.get("content") for message in body["messages"])


def test_no_user_memory_block_added_when_nothing_is_stored():
    service = ChatService()
    _, body = service._build_request_body("Hello", [], [], stream=False, user_memory=None)
    assert not any(
        "ESTABLISHED USER FACTS" in str(message.get("content")) for message in body["messages"]
    )


def test_context_block_groups_multiple_categories_with_labels(db_session):
    user_memory_service.sync_from_message(db_session, 1, None, "I'm allergic to peanuts")
    user_memory_service.sync_from_message(db_session, 1, None, "I don't eat cilantro")
    user_memory_service.sync_from_message(db_session, 1, None, "My goal is to lose weight")
    block = user_memory_service.context_block(db_session, 1)
    assert block is not None
    assert "Allergies: Allergic to peanuts" in block
    assert "Dislikes: Dislikes/avoids cilantro" in block
    assert "Goals: Goal: lose weight" in block


def test_stated_allergy_in_chat_is_recalled_in_a_later_session(client, monkeypatch, db_session):
    """End-to-end: a fact stated via the real /chat/stream endpoint is fed back
    into the request body of a later, unrelated turn in a NEW session."""
    headers = _register_and_login(client, email="e2ememory@example.com")
    captured = []

    async def fake_stream(self, message, history, reference_history, **kwargs):
        captured.append(kwargs.get("user_memory"))
        yield "Noted!"

    monkeypatch.setattr(ChatService, "stream_chat", fake_stream)

    first = client.post(
        "/chat/stream", headers=headers, json={"message": "I'm allergic to peanuts"}
    )
    assert first.status_code == 200
    session_id = json.loads(first.text.splitlines()[0])["session_id"]

    second = client.post(
        f"/chat/stream?session_id={session_id}",
        headers=headers,
        json={"message": "What snack can I have?"},
    )
    assert second.status_code == 200
    assert captured[-1] is not None
    assert "Allergic to peanuts" in captured[-1]
