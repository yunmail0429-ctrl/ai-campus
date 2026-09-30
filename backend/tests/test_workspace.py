def test_conversation_crud_and_draft_persistence(environment, client):
    created = client.post("/api/v1/conversations", json={"title": "函数教案"})
    assert created.status_code == 201
    assert created.json()["data"]["updatedAt"].endswith("+00:00")
    identity = created.json()["data"]["id"]
    assert (
        client.put(
            f"/api/v1/conversations/{identity}/draft", json={"content": "教学目标", "revision": 0}
        ).status_code
        == 200
    )
    assert client.patch(f"/api/v1/conversations/{identity}", json={"title": "函数复习"}).status_code == 200
    second = environment["client"]()
    assert second.get(f"/api/v1/conversations/{identity}/draft").json()["data"]["content"] == "教学目标"
    assert second.get("/api/v1/conversations").json()["data"]["items"][0]["title"] == "函数复习"
    assert second.delete(f"/api/v1/conversations/{identity}").status_code == 200
    assert second.get(f"/api/v1/conversations/{identity}/draft").status_code == 404
    assert second.get("/api/v1/conversations").json()["data"]["items"] == []


def test_cross_user_and_tenant_conversation_access(environment, client):
    identity = client.post("/api/v1/conversations", json={"title": "私人教案"}).json()["data"]["id"]
    for other in [
        environment["client"]("peer"),
        environment["client"]("teacher", "school-b"),
        environment["client"]("admin"),
    ]:
        assert other.get(f"/api/v1/conversations/{identity}/messages").status_code == 404
        assert (
            other.put(
                f"/api/v1/conversations/{identity}/draft", json={"content": "替换", "revision": 0}
            ).status_code
            == 404
        )
        assert other.delete(f"/api/v1/conversations/{identity}").status_code == 404


def test_pagination_no_duplicates_and_invalid_cursor(client):
    for n in range(4):
        client.post("/api/v1/conversations", json={"title": str(n)})
    first = client.get("/api/v1/conversations?limit=2").json()["data"]
    second = client.get("/api/v1/conversations", params={"limit": 2, "cursor": first["nextCursor"]}).json()[
        "data"
    ]
    assert len({row["id"] for row in first["items"] + second["items"]}) == 4
    assert second["nextCursor"] is None
    assert client.get("/api/v1/conversations?cursor=broken").status_code == 422


def test_missing_model_does_not_write_fake_answer_or_charge(client):
    identity = client.post("/api/v1/conversations", json={"title": "生成"}).json()["data"]["id"]
    before = client.get("/api/v1/me/quota").json()["data"]
    response = client.post(
        f"/api/v1/conversations/{identity}/messages",
        json={"content": "生成教案"},
        headers={"Idempotency-Key": "missing-model"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "MODEL_NOT_CONFIGURED"
    assert client.get(f"/api/v1/conversations/{identity}/messages").json()["data"]["items"] == []
    assert client.get("/api/v1/me/quota").json()["data"] == before


def test_cursor_rejects_database_control_characters(client):
    import base64
    import json

    cursor = base64.urlsafe_b64encode(json.dumps([
        "2026-09-23T00:00:00+00:00", "\x00" * 36
    ]).encode()).decode()
    response = client.get("/api/v1/conversations", params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_CURSOR"


def test_conversation_rejects_nul_title_without_writing(client):
    before = client.get("/api/v1/conversations").json()["data"]
    response = client.post("/api/v1/conversations", json={"title": "invalid\x00title"})
    assert response.status_code == 422
    assert client.get("/api/v1/conversations").json()["data"] == before


def test_nul_rejected_in_query_path_and_independent_inputs(client):
    for path in ("/api/v1/conversations/bad%00id", "/api/v1/conversations?q=bad%00query"):
        assert client.get(path).status_code == 422
    for path, data in (
        ("/api/v1/knowledge-bases", {"name": "bad\x00name", "scope": "private"}),
        ("/api/v1/resource-tasks", {"kind": "ppt", "prompt": "bad\x00prompt", "options": {}}),
    ):
        response = client.post(path, json=data, headers={"Idempotency-Key": "nul-validation"})
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
