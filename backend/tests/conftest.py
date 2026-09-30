import os
import secrets
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.accounts import provision_user
from app.cli import initialize
from app.config import Settings
from app.database import make_engine
from app.main import create_app
from app.schemas import CreateUserInput

ORIGIN = "http://localhost:3100"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    password = secrets.token_urlsafe(20)
    database_url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    cleanup = None
    if database_url.startswith("postgresql"):
        schema = "test_" + uuid.uuid4().hex
        admin_engine = make_engine(database_url)
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        separator = "&" if "?" in database_url else "?"
        database_url += f"{separator}options=-csearch_path%3D{schema}"
        cleanup = (admin_engine, schema)
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    settings = Settings(
        database_url=database_url, app_env="test", tenant_slug="school-a", allowed_origins=[ORIGIN]
    )
    app = create_app(settings)
    with app.state.sessions.begin() as session:
        repo = initialize(session, "school-a", "学校 A")
        repo_b = initialize(session, "school-b", "学校 B")
        users = {}
        for username, role, tenant_repo in [
            ("teacher", "teacher", repo),
            ("peer", "teacher", repo),
            ("admin", "admin", repo),
            ("teacher", "teacher", repo_b),
        ]:
            user = provision_user(
                tenant_repo,
                CreateUserInput(
                    username=username,
                    password=password,
                    name=username,
                    subject="语文",
                    grade="高一",
                    role=role,
                ),
            )
            users[(tenant_repo.tenant_id, username)] = user.id
        tenant_id = repo.tenant_id
    clients = []

    def client(username="teacher", tenant="school-a"):
        target = (
            app if tenant == "school-a" else create_app(settings.model_copy(update={"tenant_slug": tenant}))
        )
        instance = TestClient(target, headers={"Origin": ORIGIN})
        clients.append(instance)
        if username:
            response = instance.post("/api/v1/auth/login", json={"username": username, "password": password})
            assert response.status_code == 200, response.text
        return instance

    yield {
        "app": app,
        "client": client,
        "password": password,
        "users": users,
        "tenant_id": tenant_id,
        "url": database_url,
    }
    for instance in clients:
        instance.close()
        instance.app.state.engine.dispose()
    app.state.engine.dispose()
    if cleanup:
        admin_engine, schema = cleanup
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def client(environment):
    return environment["client"]()
