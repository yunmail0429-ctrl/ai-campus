from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from app.cli import initialize
from app.database import make_engine
from app.models import QuotaAccount, QuotaTransaction, SkillTestCase, new_id, now


def test_case_order_migration_preserves_existing_data(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "6a116a1a12f0")
    engine = make_engine(url)
    try:
        with Session(engine) as session, session.begin():
            repo = initialize(session, "legacy", "旧版学校")
            # Seed using the historical table layout; today's ORM has newer columns.
            timestamp = now()
            user_id, skill_id = new_id(), new_id()
            users = Table("users", MetaData(), autoload_with=session.connection())
            session.execute(users.insert().values(
                id=user_id, tenant_id=repo.tenant_id, username="teacher",
                password_hash="unused-test-account", name="教师", subject="", grade="高一",
                active=True, created_at=timestamp, updated_at=timestamp,
            ))
            skills = Table("skills", MetaData(), autoload_with=session.connection())
            session.execute(skills.insert().values(
                id=skill_id, tenant_id=repo.tenant_id, owner_id=user_id, name="教案",
                description="说明", prompt="生成教案", scope="private", status="draft",
                version=1, active_version=0, risk_boundary="", grades=[], reviewer_notes="",
                created_at=timestamp, updated_at=timestamp,
            ))
            legacy = Table("skill_test_cases", MetaData(), autoload_with=session.connection())
            old_account = Table("quota_accounts", MetaData(), autoload_with=session.connection())
            session.execute(
                old_account.insert().values(
                    id=new_id(),
                    tenant_id=repo.tenant_id,
                    user_id=user_id,
                    balance=42,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            # Identical timestamps reproduce the ordering issue in existing records.
            for identity in ("c", "a", "b"):
                session.execute(
                    legacy.insert().values(
                        id=identity,
                        tenant_id=repo.tenant_id,
                        skill_id=skill_id,
                        input=identity,
                        expected_output="预期",
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with Session(engine) as session:
            account = session.scalar(select(QuotaAccount))
            assert account.balance == 42
            assert account.reserved == 0
            assert account.frozen is False
            opening = session.scalar(select(QuotaTransaction))
            assert opening.balance_delta == opening.balance_after == 42
            assert opening.reason == "历史余额迁移"
            cases = session.scalars(select(SkillTestCase).order_by(SkillTestCase.position)).all()
            assert [(case.input, case.position, case.expected_output) for case in cases] == [
                ("a", 0, "预期"),
                ("b", 1, "预期"),
                ("c", 2, "预期"),
            ]
        command.check(config)
    finally:
        engine.dispose()
