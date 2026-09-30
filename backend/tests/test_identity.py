import io
from datetime import timedelta

from PIL import Image
from sqlalchemy import select

from app.auth import hash_token
from app.models import AuthSession, User, now


def test_password_hash_cookie_and_logout(environment):
    anonymous = environment["client"](None)
    assert anonymous.get("/api/v1/me").status_code == 401
    assert (
        anonymous.post("/api/v1/auth/login", json={"username": "teacher", "password": "wrong"}).status_code
        == 401
    )
    logged = environment["client"]()
    token = logged.cookies.get("xflj_session")
    with environment["app"].state.sessions() as db:
        user = db.scalar(
            select(User).where(User.tenant_id == environment["tenant_id"], User.username == "teacher")
        )
        assert user.password_hash.startswith("$argon2id$")
        assert environment["password"] not in user.password_hash
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        assert row and row.token_hash != token
    assert logged.get("/api/v1/me").json()["data"]["roles"] == ["teacher"]
    assert logged.post("/api/v1/auth/logout").status_code == 200
    logged.cookies.set("xflj_session", token)
    assert logged.get("/api/v1/me").status_code == 401


def test_input_cannot_choose_tenant_or_role(environment, client):
    response = client.patch(
        "/api/v1/me",
        json={
            "name": "教师",
            "subject": "语文",
            "grade": "高一",
            "roles": ["admin"],
            "tenant_id": "school-b",
        },
    )
    assert response.status_code == 422
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post("/api/v1/admin/users", json={}).status_code == 403


def test_preferences_and_profile_persist_after_relogin(environment, client):
    assert (
        client.patch("/api/v1/me", json={"name": "张老师", "subject": "数学", "grade": "高二"}).status_code
        == 200
    )
    preferences = {"skill": "", "format": "视频", "pages": 12, "style": "探究教学"}
    assert client.put("/api/v1/me/preferences", json=preferences).status_code == 200
    next_client = environment["client"]()
    assert next_client.get("/api/v1/me").json()["data"]["name"] == "张老师"
    assert next_client.get("/api/v1/me/preferences").json()["data"] == preferences
    assert next_client.put("/api/v1/me/preferences", json={**preferences, "pages": 100}).status_code == 422


def test_avatar_upload_is_private_and_visible_in_account_list(environment, client):
    admin = environment["client"]("admin")
    peer = environment["client"]("peer")
    image = io.BytesIO()
    Image.new("RGB", (600, 400), "#3467a2").save(image, format="PNG")
    uploaded = client.post("/api/v1/me/avatar", files={"file": ("portrait.png", image.getvalue(), "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    avatar_url = uploaded.json()["data"]["avatarUrl"]
    assert avatar_url.startswith("/api/v1/users/")
    assert client.get(avatar_url).headers["content-type"] == "image/jpeg"
    assert peer.get(avatar_url).status_code == 403
    listed = admin.get("/api/v1/admin/users").json()["data"]["items"]
    assert any(row["avatarUrl"] == avatar_url for row in listed)
    assert admin.get(avatar_url).status_code == 200
    assert client.post("/api/v1/me/avatar", files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 422
    assert client.get("/api/v1/me").json()["data"]["avatarUrl"] == avatar_url
    assert client.delete("/api/v1/me/avatar").json()["data"]["avatarUrl"] is None
    assert client.get(avatar_url).status_code == 404


def test_password_change_revokes_other_devices(environment, client):
    other_device = environment["client"]()
    assert (
        client.post(
            "/api/v1/me/password",
            json={"currentPassword": environment["password"], "newPassword": "new-test-password-123"},
        ).status_code
        == 200
    )
    assert other_device.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me").status_code == 200
    anonymous = environment["client"](None)
    assert (
        anonymous.post(
            "/api/v1/auth/login", json={"username": "teacher", "password": environment["password"]}
        ).status_code
        == 401
    )
    assert (
        anonymous.post(
            "/api/v1/auth/login", json={"username": "teacher", "password": "new-test-password-123"}
        ).status_code
        == 200
    )


def test_expired_session_rejected(environment, client):
    with environment["app"].state.sessions.begin() as db:
        row = db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_token(client.cookies.get("xflj_session"))
            )
        )
        row.expires_at = now() - timedelta(seconds=1)
    assert client.get("/api/v1/me").status_code == 401


def test_disable_revokes_sessions_and_audit_is_readonly(environment, client):
    admin = environment["client"]("admin")
    user_id = client.get("/api/v1/me").json()["data"]["id"]
    assert admin.patch(f"/api/v1/admin/users/{user_id}", json={"active": False}).status_code == 200
    assert client.get("/api/v1/me").status_code == 401
    assert admin.patch(f"/api/v1/admin/users/{user_id}", json={"active": True}).status_code == 200
    assert client.get("/api/v1/me").status_code == 401
    logs = admin.get("/api/v1/admin/audit-logs").json()["data"]["items"]
    assert any(row["action"] == "user.disabled" for row in logs)
    assert all(row["createdAt"].endswith("+00:00") for row in logs)
    assert admin.delete("/api/v1/admin/audit-logs").status_code == 405
    assert environment["password"] not in str(logs)


def test_admin_user_order_and_teacher_deletion(environment):
    admin = environment["client"]("admin")
    teacher = environment["client"]("teacher")
    created = admin.post("/api/v1/admin/users", json={
        "username": "second-admin", "password": environment["password"],
        "name": "第二管理员", "subject": "语文", "grade": "高一", "role": "admin",
    })
    assert created.status_code == 201
    second_admin = environment["client"]("second-admin")

    def all_ids(client):
        ids, cursor = [], None
        while True:
            response = client.get("/api/v1/admin/users", params={"limit": 2, **({"cursor": cursor} if cursor else {})})
            assert response.status_code == 200, response.text
            page = response.json()["data"]
            ids.extend(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
            if not cursor:
                return ids

    admin_id = admin.get("/api/v1/me").json()["data"]["id"]
    second_id = created.json()["data"]["id"]
    teacher_id = teacher.get("/api/v1/me").json()["data"]["id"]
    assert all_ids(admin)[:2] == [admin_id, second_id]
    assert all_ids(second_admin)[:2] == [second_id, admin_id]
    assert admin.delete(f"/api/v1/admin/users/{admin_id}").status_code == 409
    assert admin.delete(f"/api/v1/admin/users/{second_id}").status_code == 403
    assert admin.delete(f"/api/v1/admin/users/{teacher_id}").status_code == 200
    assert teacher.get("/api/v1/me").status_code == 401
    assert teacher_id not in all_ids(admin)


def test_six_character_password_is_accepted(environment):
    admin = environment["client"]("admin")
    response = admin.post("/api/v1/admin/users", json={
        "username": "sixpass", "password": "abc123", "name": "六位密码",
        "subject": "语文", "grade": "高一", "role": "teacher",
    })
    assert response.status_code == 201, response.text
    assert environment["client"](None).post("/api/v1/auth/login", json={
        "username": "sixpass", "password": "abc123",
    }).status_code == 200
    too_short = admin.post("/api/v1/admin/users", json={
        "username": "shortpass", "password": "abc12", "name": "五位密码",
        "subject": "语文", "grade": "高一", "role": "teacher",
    })
    assert too_short.status_code == 422


def test_login_rate_limit_persists_failed_attempts(environment):
    anonymous = environment["client"](None)
    for _ in range(10):
        assert (
            anonymous.post(
                "/api/v1/auth/login", json={"username": "missing", "password": "wrong"}
            ).status_code
            == 401
        )
    response = anonymous.post("/api/v1/auth/login", json={"username": "missing", "password": "wrong"})
    assert response.status_code == 429
    assert response.json()["code"] == "LOGIN_RATE_LIMITED"


def test_login_rate_limit_counts_all_attempts_and_recovers_after_window(environment, monkeypatch):
    environment["app"].state.settings.login_attempts = 2
    client = environment["client"](None)
    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login", json={"username": "teacher", "password": environment["password"]}
        )
        assert response.status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 200
    credentials = {"username": "teacher", "password": environment["password"]}
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 429
    later = now() + timedelta(seconds=environment["app"].state.settings.login_window_seconds + 1)
    monkeypatch.setattr("app.auth.now", lambda: later)
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200


def test_origin_size_and_safe_errors(environment, client):
    assert client.post("/api/v1/auth/logout", headers={"Origin": "https://wrong.example"}).status_code == 403
    assert client.post("/api/v1/auth/login", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    response = client.post("/api/v1/auth/login", json={"password": "secret-do-not-echo"})
    assert response.status_code == 422
    assert "secret-do-not-echo" not in response.text
    assert response.json()["requestId"] == response.headers["X-Request-ID"]


def test_cross_tenant_cookie_cannot_authenticate(environment, client):
    school_b = environment["client"](None, "school-b")
    school_b.cookies.set("xflj_session", client.cookies.get("xflj_session"))
    assert school_b.get("/api/v1/me").status_code == 401
