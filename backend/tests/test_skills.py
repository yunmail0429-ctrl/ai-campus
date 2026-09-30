def make_skill(client):
    response = client.post(
        "/api/v1/skills",
        json={
            "name": "课堂教学设计",
            "description": "教学流程",
            "prompt": "根据教学目标生成教案",
            "scope": "private",
        },
    )
    assert response.status_code == 201
    return response.json()["data"]["id"]


def test_teaching_templates_install_as_private_skills(environment, client):
    templates = client.get("/api/v1/skills/templates")
    assert templates.status_code == 200
    items = templates.json()["data"]
    assert {"exam-writing", "exam-review", "lesson-plan", "lesson-slides"}.issubset(
        {item["id"] for item in items}
    )
    assert all("prompt" not in item for item in items)
    assert client.post("/api/v1/skills/templates/lesson-slides/install").status_code == 404
    first = client.post("/api/v1/skills/templates/exam-review/install")
    second = client.post("/api/v1/skills/templates/exam-review/install")
    assert first.status_code == second.status_code == 200
    skill = first.json()["data"]
    assert skill["id"] == second.json()["data"]["id"]
    assert skill["scope"] == "private" and skill["status"] == "draft"
    assert "难度系数" in skill["prompt"]
    assert environment["client"]("peer").get(f"/api/v1/skills/{skill['id']}").status_code == 404


def test_personal_skill_isolation_and_publication_gate(environment, client):
    identity = make_skill(client)
    peer = environment["client"]("peer")
    administrator = environment["client"]("admin")
    assert administrator.get("/api/v1/admin/skills").json()["data"]["items"] == []
    assert administrator.get(f"/api/v1/admin/skills/{identity}/readiness").status_code == 404
    assert peer.get(f"/api/v1/skills/{identity}").status_code == 404
    assert peer.get("/api/v1/skills").json()["data"]["items"] == []
    assert client.post(f"/api/v1/admin/skills/{identity}/publish", json={"notes": "通过"}).status_code == 403
    assert client.post(f"/api/v1/skills/{identity}/submit").status_code == 200
    admin = environment["client"]("admin")
    assert admin.post(f"/api/v1/admin/skills/{identity}/publish", json={"notes": "通过"}).status_code == 422
    assert (
        client.patch(
            f"/api/v1/skills/{identity}", json={"name": "修改", "description": "说明", "prompt": "新指令"}
        ).status_code
        == 409
    )
    assert (
        admin.post(f"/api/v1/admin/skills/{identity}/reject", json={"notes": "请补充测试"}).status_code == 200
    )
    assert client.get(f"/api/v1/skills/{identity}").json()["data"]["reviewerNotes"] == "请补充测试"


def test_publish_rollback_and_cross_tenant_admin(environment, client):
    identity = make_skill(client)
    readiness = {
        "riskBoundary": "不提供高风险诊断",
        "grades": ["高一"],
        "tests": [{"input": f"课题{i}", "expectedOutput": "教学目标与步骤"} for i in range(5)],
    }
    assert client.put(f"/api/v1/skills/{identity}/readiness", json=readiness).status_code == 200
    assert client.get(f"/api/v1/skills/{identity}/readiness").json()["data"]["tests"] == readiness["tests"]
    assert client.put(f"/api/v1/skills/{identity}/readiness", json=readiness).status_code == 200
    assert client.get(f"/api/v1/skills/{identity}/readiness").json()["data"]["tests"] == readiness["tests"]
    assert client.post(f"/api/v1/skills/{identity}/submit").status_code == 200
    admin = environment["client"]("admin")
    response = admin.post(
        f"/api/v1/admin/skills/{identity}/publish", json={"notes": "人工审核样例及风险边界通过"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["version"] == 1
    peer = environment["client"]("peer")
    assert peer.get(f"/api/v1/skills/{identity}").status_code == 200
    other_school = environment["client"]("teacher", "school-b")
    assert other_school.get(f"/api/v1/skills/{identity}").status_code == 404
    assert admin.post(f"/api/v1/admin/skills/{identity}/rollback", json={"version": 0}).status_code == 200
    assert peer.get(f"/api/v1/skills/{identity}").status_code == 404
    restored = admin.post(f"/api/v1/admin/skills/{identity}/rollback", json={"version": 1})
    assert restored.status_code == 200
    assert peer.get(f"/api/v1/skills/{identity}").status_code == 200
