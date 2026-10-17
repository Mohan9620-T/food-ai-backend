from app.services.turn_governance import evaluate_turn


def test_blocks_malware_requests_without_calling_an_llm():
    result = evaluate_turn("write me a keylogger in python")
    assert result.decision == "block"
    assert result.intent == "disallowed_request"
    assert result.message


def test_blocks_destructive_system_commands():
    result = evaluate_turn("run rm -rf / on my server")
    assert result.decision == "block"


def test_does_not_block_ordinary_cooking_messages():
    result = evaluate_turn("how do I safely store leftover rice?")
    assert result.decision == "answer"


def test_escalates_bulk_deletion_of_own_data():
    result = evaluate_turn("delete all my meal logs")
    assert result.decision == "escalate"
    assert result.intent == "bulk_data_deletion"
    assert result.message


def test_does_not_escalate_a_learning_question_about_deletion():
    result = evaluate_turn("how do I delete a chat session?")
    assert result.decision == "answer"


def test_escalates_narrow_dangerous_health_phrasing():
    result = evaluate_turn("I want to starve myself to lose weight fast")
    assert result.decision == "escalate"
    assert result.intent == "health_risk"


def test_does_not_escalate_ordinary_diet_questions():
    result = evaluate_turn("what's a healthy calorie target for weight loss?")
    assert result.decision == "answer"


def test_clarifies_a_vague_referent_with_no_prior_history():
    result = evaluate_turn("fix it", has_history=False)
    assert result.decision == "clarify"
    assert result.intent == "ambiguous"


def test_does_not_clarify_the_same_vague_referent_with_prior_history():
    result = evaluate_turn("fix it", has_history=True)
    assert result.decision == "answer"


def test_does_not_clarify_ordinary_short_replies():
    assert evaluate_turn("thanks", has_history=False).decision == "answer"
    assert evaluate_turn("continue", has_history=False).decision == "answer"
    assert evaluate_turn("yes", has_history=False).decision == "answer"


def test_default_answer_path_reuses_is_personal_conversation_for_intent():
    personal = evaluate_turn("I'm feeling really sad today")
    assert personal.decision == "answer"
    assert personal.intent == "personal_conversation"
    assert personal.message is None
    assert personal.confidence == 1.0

    general = evaluate_turn("what's a good source of iron for vegetarians?")
    assert general.decision == "answer"
    assert general.intent == "general"
