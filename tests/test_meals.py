import json
import requests

from app.config import settings
from app.services.nutrition_parser_service import NutritionParserService
from app.services.image_parser_service import ImageParserService


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _register_and_login(client, email="meals@example.com"):
    client.post("/users/", json={
        "fullname": "Meal User",
        "email": email,
        "password": "meal-password",
    })
    response = client.post("/users/login", json={
        "email": email,
        "password": "meal-password",
    })
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _mock_grounded_calls(monkeypatch):
    monkeypatch.setattr(settings, "USDA_API_KEY", "test-key")

    def post(url, **kwargs):
        if url == settings.OLLAMA_URL:
            if kwargs["json"].get("format") == "json":
                return FakeResponse({"message": {"content": json.dumps([{
                    "food_name": "oatmeal",
                    "quantity": 100,
                    "unit": "g",
                }])}})
            return FakeResponse({"message": {"content": json.dumps({
                "image_type": "food",
                "items": [{
                    "name": "oatmeal",
                    "confidence": "high",
                    "visual_evidence": "a bowl of cooked oats",
                }],
                "uncertain_items": [],
            })}})
        assert url.endswith("/foods/search")
        return FakeResponse({"foods": [
            {"fdcId": 12345, "description": "Oatmeal, cooked", "dataType": "Foundation"}
        ]})

    def get(url, **kwargs):
        assert url.endswith("/food/12345")
        return FakeResponse({
            "fdcId": 12345,
            "description": "Oatmeal, cooked",
            "foodPortions": [{
                "modifier": "serving",
                "gramWeight": 100,
                "amount": 1,
            }],
            "foodNutrients": [
                {"nutrient": {"id": 1008}, "amount": 71},
                {"nutrient": {"id": 1003}, "amount": 2.54},
                {"nutrient": {"id": 1005}, "amount": 12.0},
                {"nutrient": {"id": 1004}, "amount": 1.52},
            ],
        })

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr("requests.get", get)


def test_create_list_totals_and_delete_meal(client, monkeypatch):
    headers = _register_and_login(client)
    _mock_grounded_calls(monkeypatch)

    create_response = client.post("/meals/", headers=headers, json={
        "description": "100 g oatmeal",
        "logged_at": "2026-08-31T08:00:00Z",
    })
    assert create_response.status_code == 200
    meal = create_response.json()
    assert meal["raw_description"] == "100 g oatmeal"
    assert meal["source"] == "text"
    assert meal["items"] == [{
        "id": meal["items"][0]["id"],
        "food_name": "oatmeal",
        "quantity": 100.0,
        "unit": "g",
        "fdc_id": 12345,
        "calories": 71.0,
        "protein_g": 2.54,
        "carbs_g": 12.0,
        "fat_g": 1.52,
    }]

    list_response = client.get(
        "/meals/?start_date=2026-08-31&end_date=2026-08-31",
        headers=headers,
    )
    assert list_response.status_code == 200
    assert [entry["id"] for entry in list_response.json()] == [meal["id"]]

    totals_response = client.get("/meals/totals?date=2026-08-31", headers=headers)
    assert totals_response.status_code == 200
    assert totals_response.json() == {
        "date": "2026-08-31",
        "calories": 71.0,
        "protein_g": 2.54,
        "carbs_g": 12.0,
        "fat_g": 1.52,
        "matched_items": 1,
        "unmatched_items": 0,
    }

    delete_response = client.delete(f"/meals/{meal['id']}", headers=headers)
    assert delete_response.status_code == 200
    assert client.get("/meals/", headers=headers).json() == []


def test_parse_failure_retries_once_and_does_not_save(client, monkeypatch):
    headers = _register_and_login(client, "parse-failure@example.com")
    attempts = 0

    def invalid_parse(url, **kwargs):
        nonlocal attempts
        attempts += 1
        return FakeResponse({"message": {"content": "not json"}})

    monkeypatch.setattr("requests.post", invalid_parse)
    response = client.post("/meals/", headers=headers, json={"description": "something"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Could not parse the meal description. Please be more specific."
    assert attempts == 2
    assert client.get("/meals/", headers=headers).json() == []


def test_unmatched_item_has_no_nutrition_numbers(client, monkeypatch):
    headers = _register_and_login(client, "unmatched@example.com")
    monkeypatch.setattr(settings, "USDA_API_KEY", "test-key")

    def post(url, **kwargs):
        if url == settings.OLLAMA_URL:
            return FakeResponse({"message": {"content": json.dumps([
                {"food_name": "mystery food", "quantity": 1, "unit": "serving"}
            ])}})
        return FakeResponse({"foods": []})

    monkeypatch.setattr("requests.post", post)
    response = client.post("/meals/", headers=headers, json={"description": "mystery food"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["fdc_id"] is None
    assert item["calories"] is None
    assert item["protein_g"] is None
    assert item["carbs_g"] is None
    assert item["fat_g"] is None

    totals = client.get(
        f"/meals/totals?date={response.json()['logged_at'][:10]}",
        headers=headers,
    ).json()
    assert totals["matched_items"] == 0
    assert totals["unmatched_items"] == 1
    assert totals["calories"] == 0


def test_meal_creation_is_rate_limited(client, monkeypatch):
    headers = _register_and_login(client, "meal-rate@example.com")
    _mock_grounded_calls(monkeypatch)

    responses = [
        client.post("/meals/", headers=headers, json={"description": "100 g oatmeal"})
        for _ in range(11)
    ]

    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[10].status_code == 429


def test_parser_rejects_llm_invented_nutrition_fields(monkeypatch):
    responses = iter([
        FakeResponse({"message": {"content": json.dumps([{
            "food_name": "egg",
            "quantity": 2,
            "unit": "large egg",
            "calories": 999,
        }])}}),
        FakeResponse({"message": {"content": json.dumps([{
            "food_name": "egg",
            "quantity": 2,
            "unit": "large egg",
        }])}}),
    ])
    attempts = 0

    def post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        return next(responses)

    monkeypatch.setattr("requests.post", post)
    parsed = NutritionParserService().parse("2 large eggs")

    assert attempts == 2
    assert parsed[0].model_dump() == {
        "food_name": "egg",
        "quantity": 2.0,
        "unit": "large egg",
    }


def test_create_meal_from_image_uses_existing_usda_pipeline(
    client, monkeypatch, valid_png_bytes
):
    headers = _register_and_login(client, "image@example.com")
    _mock_grounded_calls(monkeypatch)

    response = client.post(
        "/meals/from-image",
        headers=headers,
        files={"image": ("meal.png", valid_png_bytes, "image/png")},
        data={"logged_at": "2026-08-31T09:00:00Z"},
    )

    assert response.status_code == 200
    meal = response.json()
    assert meal["source"] == "image"
    assert meal["raw_description"] == "Photo: 1 serving oatmeal"
    assert meal["items"][0]["fdc_id"] == 12345
    assert meal["items"][0]["calories"] == 71.0


def test_unparseable_image_retries_and_is_not_saved(client, monkeypatch, valid_png_bytes):
    headers = _register_and_login(client, "bad-image@example.com")
    attempts = 0

    def no_food(url, **kwargs):
        nonlocal attempts
        attempts += 1
        return FakeResponse({"message": {"content": json.dumps({
            "image_type": "other",
            "items": [],
            "uncertain_items": [],
        })}})

    monkeypatch.setattr("requests.post", no_food)
    response = client.post(
        "/meals/from-image",
        headers=headers,
        files={"image": ("empty.png", valid_png_bytes, "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["detail"].startswith("Could not identify food in image")
    assert attempts == 2
    assert client.get("/meals/", headers=headers).json() == []


def test_image_upload_rejects_invalid_type_and_oversized_file(client):
    headers = _register_and_login(client, "invalid-upload@example.com")

    invalid = client.post(
        "/meals/from-image",
        headers=headers,
        files={"image": ("notes.txt", b"not an image", "text/plain")},
    )
    assert invalid.status_code == 415
    assert "Unsupported file type" in invalid.json()["detail"]

    oversized = client.post(
        "/meals/from-image",
        headers=headers,
        files={"image": ("large.jpg", b"x" * (8 * 1024 * 1024 + 1), "image/jpeg")},
    )
    assert oversized.status_code == 413
    assert "Maximum size is 8 MB" in oversized.json()["detail"]


def test_meal_image_upload_rejects_spoofed_content(client, monkeypatch):
    headers = _register_and_login(client, "spoofed-meal@example.com")

    def must_not_run(*args, **kwargs):
        raise AssertionError("Spoofed image reached Ollama")

    monkeypatch.setattr(ImageParserService, "parse", must_not_run)
    response = client.post(
        "/meals/from-image",
        headers=headers,
        files={"image": ("meal.png", b"not actually a PNG", "image/png")},
    )

    assert response.status_code == 422
    assert "valid supported image" in response.json()["detail"].lower()


def test_vision_model_unavailable_returns_clear_error(
    client, monkeypatch, valid_png_bytes
):
    headers = _register_and_login(client, "vision-down@example.com")

    def unavailable(url, **kwargs):
        raise requests.ConnectionError("vision model is not available")

    monkeypatch.setattr("requests.post", unavailable)
    response = client.post(
        "/meals/from-image",
        headers=headers,
        files={"image": ("meal.png", valid_png_bytes, "image/png")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Vision model unavailable. Pull and start the configured Ollama vision model."
    )
    assert client.get("/meals/", headers=headers).json() == []


def test_image_parser_rejects_fields_outside_vision_schema(monkeypatch):
    responses = iter([
        FakeResponse({"message": {"content": json.dumps({
            "image_type": "food",
            "items": [{
                "name": "toast",
                "confidence": "high",
                "visual_evidence": "browned bread",
                "calories": 500,
            }],
            "uncertain_items": [],
        })}}),
        FakeResponse({"message": {"content": json.dumps({
            "image_type": "food",
            "items": [{
                "name": "toast",
                "confidence": "high",
                "visual_evidence": "browned bread",
            }],
            "uncertain_items": [],
        })}}),
    ])
    attempts = 0

    def post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        assert kwargs["json"]["messages"][-1]["images"]
        assert kwargs["json"]["keep_alive"] == settings.OLLAMA_KEEP_ALIVE
        assert kwargs["json"]["format"]["type"] == "object"
        assert kwargs["json"]["options"] == {"temperature": 0, "num_ctx": 8192}
        return next(responses)

    monkeypatch.setattr("requests.post", post)
    parsed = ImageParserService().parse(b"image-bytes")

    assert attempts == 2
    assert parsed[0].model_dump() == {
        "food_name": "toast",
        "quantity": 1.0,
        "unit": "serving",
    }
