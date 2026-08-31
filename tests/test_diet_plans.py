import json

from app.api import diet_plans
from app.services.diet_plan_service import DietPlanGenerationError, DietPlanService
from app.services.usda_nutrition_service import NutritionResult


class FakeResponse:
    def __init__(self, content): self.content = content
    def raise_for_status(self): return None
    def json(self): return {"message": {"content": self.content}}


def _headers(client, email="plans@example.com"):
    client.post("/users/", json={"fullname": "Plan User", "email": email, "password": "plan-password"})
    login = client.post("/users/login", json={"email": email, "password": "plan-password"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _profile(client, headers):
    return client.put("/profile/", headers=headers, json={
        "goal": "gain_weight", "target_calories": 2400, "target_protein_g": 140,
        "target_carbs_g": 280, "target_fat_g": 75, "allergies": ["peanuts"],
        "dietary_restrictions": ["vegetarian"], "disliked_foods": ["olives"],
    })


def _proposal():
    return {"days": [{"day_of_week": day, "meals": [
        {"meal_slot": slot, "description": f"{slot} meal", "items": [
            {"food_name": "oatmeal" if not (day == 0 and slot == "snack") else "mystery food", "quantity": 100, "unit": "g"}
        ]} for slot in ("breakfast", "lunch", "dinner", "snack")]} for day in range(7)]}


def test_generate_grounded_plan_prompt_list_get_delete(client, monkeypatch):
    headers = _headers(client); _profile(client, headers)
    captured = []
    def ollama(url, **kwargs):
        captured.append(kwargs["json"]["messages"][0]["content"])
        return FakeResponse(json.dumps(_proposal()))
    monkeypatch.setattr("requests.post", ollama)
    def lookup(self, item):
        if item.food_name == "mystery food": return NutritionResult(item.food_name, item.quantity, item.unit)
        return NutritionResult(item.food_name, item.quantity, item.unit, 123, 100, 4, 18, 2)
    monkeypatch.setattr("app.services.usda_nutrition_service.UsdaNutritionService.lookup", lookup)

    response = client.post("/diet-plans/generate", headers=headers)
    assert response.status_code == 200
    plan = response.json()
    assert "peanuts" in captured[0] and "vegetarian" in captured[0]
    assert "HARD EXCLUSIONS" in captured[0]
    assert len(plan["meals"]) == 28
    assert plan["daily_totals"][0]["matched_items"] == 3
    assert plan["daily_totals"][0]["unmatched_items"] == 1
    assert plan["daily_totals"][0]["calories"] == 300
    unmatched = next(item for meal in plan["meals"] for item in meal["items"] if item["fdc_id"] is None)
    assert unmatched["calories"] is None
    assert client.get("/diet-plans/", headers=headers).json()[0]["id"] == plan["id"]
    assert client.get(f"/diet-plans/{plan['id']}", headers=headers).status_code == 200
    assert client.delete(f"/diet-plans/{plan['id']}", headers=headers).status_code == 200


def test_generate_requires_profile(client):
    headers = _headers(client, "no-profile@example.com")
    response = client.post("/diet-plans/generate", headers=headers)
    assert response.status_code == 400


def test_malformed_json_retries_once_then_fails(client, monkeypatch):
    headers = _headers(client, "bad-plan@example.com"); _profile(client, headers)
    attempts = 0
    def invalid(url, **kwargs):
        nonlocal attempts; attempts += 1
        return FakeResponse("not json")
    monkeypatch.setattr("requests.post", invalid)
    response = client.post("/diet-plans/generate", headers=headers)
    assert response.status_code == 422
    assert attempts == 2
    assert client.get("/diet-plans/", headers=headers).json() == []
