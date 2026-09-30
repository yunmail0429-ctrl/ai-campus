import os
import time

from app.accounts import provision_user
from app.cli import initialize
from app.config import Settings
from app.database import make_engine, make_sessions
from app.errors import AppError
from app.models import User
from app.schemas import CreateUserInput
from app.storage import ObjectStore

settings = Settings()
for attempt in range(60):
    try:
        ObjectStore(settings).initialize()
        break
    except AppError:
        if attempt == 59:
            raise RuntimeError("Private object storage did not become ready") from None
        time.sleep(2)
engine = make_engine(settings.database_url)
with make_sessions(engine).begin() as session:
    repo = initialize(session, settings.tenant_slug, "AI 校园办公")
    if not repo.one(User, User.username == "admin"):
        provision_user(
            repo,
            CreateUserInput(
                username="admin",
                password=os.environ["INITIAL_ADMIN_PASSWORD"],
                name="学校管理员",
                subject="",
                grade="高一",
                role="admin",
            ),
        )
engine.dispose()
print("School, private storage and administrator initialized.")
