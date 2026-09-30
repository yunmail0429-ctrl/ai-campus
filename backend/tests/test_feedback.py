# ruff: noqa: F811
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from test_model_runtime import enable_chat, provider  # noqa: F401


def answer(env):
    enable_chat(env)
    teacher = env["client"]()
    chat = teacher.post("/api/v1/conversations", json={"title": "回答评价"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{chat}/messages"
    result = teacher.post(
        path,
        json={"content": "教学设计", "useKnowledge": False},
        headers={"Idempotency-Key": "feedback-answer"},
    )
    assert "event: message.completed" in result.text
    messages = teacher.get(path).json()["data"]["items"]
    return teacher, chat, messages, path


def test_feedback_owner_update_withdraw_and_no_extra_billing(environment, provider):
    env = environment
    teacher, chat, messages, path = answer(env)
    api = f"{path}/{messages[-1]['id']}/feedback"
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    count = len(provider[2])
    for client in (env["client"]("peer"), env["client"]("admin"), env["client"]("teacher", "school-b")):
        assert client.put(api, json={"rating": 5}).status_code == 404
    for rating in (0, 6, True, "3"):
        assert teacher.put(api, json={"rating": rating}).status_code == 422
    data = {"rating": 2, "comment": "联系13812345678，例子需要改进"}
    first = teacher.put(api, json=data)
    assert first.status_code == 200 and "13812345678" not in first.text
    repeated = teacher.put(api, json=data)
    assert first.json()["data"] == repeated.json()["data"]
    assert teacher.get(path).json()["data"]["items"][-1]["feedback"]["rating"] == 2
    admin = env["client"]("admin")
    stats = admin.get("/api/v1/admin/statistics").json()["data"]["summary"]
    assert stats["feedbackCount"] == 1 and stats["feedbackAverage"] == 2
    assert teacher.put(api, json={"rating": 5, "comment": "已解决"}).status_code == 200
    stats = admin.get("/api/v1/admin/statistics").json()["data"]["summary"]
    assert stats["feedbackCount"] == 1 and stats["feedbackAverage"] == 5
    assert "已解决" not in admin.get("/api/v1/admin/statistics/export").text
    assert "例子需要改进" not in admin.get("/api/v1/admin/audit-logs").text
    assert teacher.put(api, json={"rating": None}).json()["data"] is None
    stats = admin.get("/api/v1/admin/statistics").json()["data"]["summary"]
    assert stats["feedbackCount"] == 0 and stats["feedbackAverage"] is None
    assert teacher.get("/api/v1/me/quota").json()["data"] == quota and len(provider[2]) == count
    assert teacher.put(f"{path}/{messages[0]['id']}/feedback", json={"rating": 5}).status_code == 404
    assert teacher.delete(f"/api/v1/conversations/{chat}").status_code == 200
    assert teacher.put(api, json={"rating": 5}).status_code == 404


def test_feedback_migration_preserves_invocation_constraints(environment, provider):
    teacher, _, _, path = answer(environment)
    before = teacher.get(path).json()["data"]
    quota = teacher.get("/api/v1/me/quota").json()["data"]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "05fffabb1d01")
    command.upgrade(config, "head")
    assert teacher.get(path).json()["data"] == before
    assert teacher.get("/api/v1/me/quota").json()["data"] == quota
    constraints = str(inspect(environment["app"].state.engine).get_check_constraints("model_invocations"))
    assert "feedback_rating" in constraints and "input_tokens" in constraints and "status" in constraints


def test_unfinished_answer_cannot_be_rated(environment, provider):
    env = environment
    enable_chat(env, model="missing-usage")
    teacher = env["client"]()
    chat = teacher.post("/api/v1/conversations", json={"title": "中断"}).json()["data"]["id"]
    path = f"/api/v1/conversations/{chat}/messages"
    teacher.post(
        path, json={"content": "教学活动", "useKnowledge": False}, headers={"Idempotency-Key": "unfinished"}
    )
    message = teacher.get(path).json()["data"]["items"][-1]
    result = teacher.put(f"{path}/{message['id']}/feedback", json={"rating": 5})
    assert result.status_code == 409 and result.json()["code"] == "ANSWER_NOT_COMPLETED"
