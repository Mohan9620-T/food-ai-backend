DOCUMENTED_OPERATIONS = {
    ("/chat/sessions", "get"),
    ("/chat/sessions", "post"),
    ("/chat/sessions/consolidate", "post"),
    ("/chat/sessions/{session_id}", "get"),
    ("/chat/sessions/{session_id}/import", "post"),
    ("/chat/sessions/{session_id}", "put"),
    ("/chat/sessions/{session_id}", "delete"),
    ("/chat/", "post"),
    ("/chat/vision", "post"),
    ("/chat/stream", "post"),
    ("/meals/", "post"),
    ("/meals/from-image", "post"),
    ("/meals/", "get"),
    ("/meals/totals", "get"),
    ("/meals/{meal_id}", "delete"),
    ("/diet-plans/generate", "post"),
    ("/diet-plans/", "get"),
    ("/diet-plans/{plan_id}", "get"),
    ("/diet-plans/{plan_id}", "delete"),
    ("/profile/", "get"),
    ("/profile/", "put"),
    ("/users/", "post"),
    ("/users/login", "post"),
    ("/users/refresh", "post"),
    ("/users/logout", "post"),
}


def test_target_api_routes_have_summaries_and_descriptions(client):
    schema = client.get("/openapi.json").json()

    for path, method in DOCUMENTED_OPERATIONS:
        operation = schema["paths"][path][method]
        assert operation["summary"].strip(), f"Missing summary for {method.upper()} {path}"
        assert operation["description"].strip(), f"Missing description for {method.upper()} {path}"


def test_openapi_lists_key_documented_error_responses(client):
    schema = client.get("/openapi.json").json()

    assert {"401", "404", "413", "415", "422", "429", "503"} <= set(
        schema["paths"]["/chat/vision"]["post"]["responses"]
    )
    assert {"401", "413", "415", "422", "429", "503"} <= set(
        schema["paths"]["/meals/from-image"]["post"]["responses"]
    )
    assert {"400", "401", "422", "429"} <= set(
        schema["paths"]["/diet-plans/generate"]["post"]["responses"]
    )
    assert {"400", "422", "429"} <= set(
        schema["paths"]["/users/"]["post"]["responses"]
    )


def test_swagger_ui_renders(client):
    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text
    assert "/openapi.json" in response.text
