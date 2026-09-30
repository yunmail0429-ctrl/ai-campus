"""A real, isolated backend for browser tests. No endpoint or response mocking."""

import os
import secrets
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

import uvicorn
from alembic import command
from alembic.config import Config
from model_server import provider_server
from pydantic import SecretStr

from app.accounts import provision_user
from app.cli import initialize
from app.config import ProviderProfile, Settings
from app.main import create_app
from app.schemas import CreateUserInput


@contextmanager
def knowledge_worker(settings):
    if os.environ.get("XFLJ_TEST_KNOWLEDGE_SERVICES") != "1":
        yield
        return
    from celery.contrib.testing.worker import start_worker

    from app import worker

    worker.settings = settings
    stopped = Event()

    def publish_outbox():
        while not stopped.wait(0.5):
            worker.dispatch_outbox()

    with start_worker(
        worker.app, pool="solo", queues=[worker.QUEUE], perform_ping_check=False, shutdown_timeout=15
    ):
        dispatcher = Thread(target=publish_outbox, daemon=True)
        dispatcher.start()
        try:
            yield
        finally:
            stopped.set()
            dispatcher.join(timeout=10)


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    data = root / "local-data"
    data.mkdir(exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{(data / ('e2e-' + uuid.uuid4().hex + '.db')).as_posix()}"
    os.environ["APP_ENV"] = "test"
    command.upgrade(Config(str(root / "alembic.ini")), "head")
    settings = Settings(
        tenant_slug="browser-test", cookie_secure=False, allowed_origins=["http://127.0.0.1:3218"]
    )
    app = create_app(settings)
    with app.state.sessions.begin() as session:
        repo = initialize(session, settings.tenant_slug, "浏览器测试学校")
        provision_user(
            repo,
            CreateUserInput(
                username="browser-teacher",
                name="测试教师",
                subject="数学",
                grade="高一",
                password=os.environ["XFLJ_E2E_PASSWORD"],
            ),
        )
        provision_user(
            repo,
            CreateUserInput(
                username="browser-admin",
                name="测试管理员",
                subject="教务",
                grade="跨年级",
                role="admin",
                password=os.environ["XFLJ_E2E_PASSWORD"],
            ),
        )
    # Only this isolated test runner exposes a local deterministic protocol fixture.
    # Normal app configuration never installs a fake supplier or simulated answer.
    key = secrets.token_urlsafe(24)
    with provider_server(key) as (url, _):
        settings.model_providers = {
            "protocol-test": ProviderProfile(
                label="本机协议测试（模拟供应商）",
                base_url=url,
                secret_ref="test",
                tenant_slugs=[settings.tenant_slug],
            )
        }
        settings.model_api_keys = {"test": SecretStr(key)}
        with knowledge_worker(settings):
            uvicorn.run(app, host="127.0.0.1", port=8018, log_level="warning")


if __name__ == "__main__":
    main()
