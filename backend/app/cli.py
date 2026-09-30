import argparse
import getpass
import json
from pathlib import Path

from sqlalchemy import select

from .accounts import provision_user
from .config import Settings
from .database import make_engine, make_sessions
from .models import ModelInvocation, Role, Tenant, new_id, now
from .repository import Repository
from .schemas import CreateUserInput


def initialize(session, slug, name):
    tenant = session.scalar(select(Tenant).where(Tenant.slug == slug))
    if not tenant:
        identity = new_id()
        tenant = Tenant(id=identity, tenant_id=identity, slug=slug, name=name)
        session.add(tenant)
        session.flush()
    repo = Repository(session, tenant.id)
    for role_name in ("teacher", "admin"):
        if not repo.one(Role, Role.name == role_name):
            repo.add(Role, name=role_name)
    return repo


def main():
    parser = argparse.ArgumentParser(description="西附两江数据库初始化与接口导出")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-school")
    init.add_argument("--name", default="西南大学附属中学两江中学")
    user = sub.add_parser("create-user")
    user.add_argument("--username", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--role", choices=["teacher", "admin"], default="teacher")
    export = sub.add_parser("export-openapi")
    export.add_argument("--output", default="../docs/openapi.json")
    sub.add_parser("recover-chat", help="释放当前学校已超时中断的对话预占")
    sub.add_parser("init-storage", help="创建配置中的私有文件桶，已有桶只检查权限")
    args = parser.parse_args()
    if args.command == "export-openapi":
        from .main import app

        Path(args.output).write_text(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("OpenAPI exported")
        return
    settings = Settings()
    if args.command == "init-storage":
        from .errors import AppError
        from .storage import ObjectStore

        try:
            ObjectStore(settings).initialize()
        except AppError as exc:
            parser.error(exc.message)
        print("文件桶已初始化并通过私有权限检查。")
        return
    engine = make_engine(settings.database_url)
    with make_sessions(engine).begin() as session:
        if args.command == "init-school":
            initialize(session, settings.tenant_slug, args.name)
            print("学校和角色已初始化；没有创建默认密码或账号。")
        else:
            tenant = session.scalar(select(Tenant).where(Tenant.slug == settings.tenant_slug))
            if not tenant:
                parser.error("请先执行 init-school")
            if args.command == "recover-chat":
                from .chat_runtime import expire_for_conversation

                repo = Repository(session, tenant.id)
                conversations = list(
                    session.scalars(
                        repo.query(ModelInvocation)
                        .where(
                            ModelInvocation.status == "running",
                            ModelInvocation.deadline_at < now(),
                        )
                        .with_only_columns(ModelInvocation.conversation_id)
                        .distinct()
                        .limit(100)
                    )
                )
                for identity in conversations:
                    expire_for_conversation(repo, identity)
                print(f"已检查 {len(conversations)} 条超时对话。")
                session.commit()
                engine.dispose()
                return
            password = getpass.getpass("设置密码（至少 6 位）：")
            if not 12 <= len(password) <= 1024:
                parser.error("密码长度须为 12 至 1024 位")
            if password != getpass.getpass("再次输入密码："):
                parser.error("两次密码不一致")
            provision_user(
                Repository(session, tenant.id),
                CreateUserInput(
                    username=args.username,
                    password=password,
                    name=args.name,
                    subject="",
                    grade="高一",
                    role=args.role,
                ),
            )
            print("账号已创建。")
    engine.dispose()


if __name__ == "__main__":
    main()
