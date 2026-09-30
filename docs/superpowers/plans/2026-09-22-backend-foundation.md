# 后端基础与真实登录计划

**目标：** React 页面通过真实服务认证并读写账号、偏好、会话和 Skill；为全量业务建立持久化底座。
**架构：** FastAPI 模块化单体 + SQLAlchemy + Alembic；生产 PostgreSQL，SQLite 仅用于本地快速验证。认证使用 Argon2id 和数据库可撤销随机会话，HttpOnly Cookie 经 Next 同源代理传递。学校由服务端配置解析，不信任请求中的租户字段。

## 实现步骤

- [x] 创建 backend/pyproject.toml、app/config.py、app/database.py 和 migrations，配置环境和迁移。
- [x] 定义 tenants、users、roles、user_roles、sessions、preferences、workspaces、conversations、messages、quota_accounts、audit_logs、skills 与版本审核表，增加租户及所有权约束。
- [x] 实现 Repository 统一 tenant_id 过滤、稳定错误响应、requestId、请求大小限制和登录限流。
- [x] 实现登录/退出、用户资料、偏好、密码修改、会话撤销、账号启停和管理员账号管理；提供 CLI 初始化，禁止默认密码。
- [x] 实现会话列表、创建/重命名/删除、消息恢复、草稿与个人 Skill 创建/编辑/提交/审核；未配置模型返回明确错误，不扣积分或写入假输出。
- [x] 添加真实 HTTP + 数据库测试：密码校验、会话撤销、停用、角色越权、跨用户/跨租户、持久化、Skill 状态与发布约束。
- [x] 启动后端与 Next，执行不使用 HTTP 夹具的浏览器登录、设置、会话、Skill 验证。
- [x] 记录 PostgreSQL、队列、对象存储、真实模型和管理员界面等剩余范围，不缩减总目标。

## 验证命令

```powershell
cd backend
.venv\Scripts\python -m pip install -e '.[dev]'
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check app tests
```

数据库迁移必须在空库和已升级库上运行；SQLite 与 PostgreSQL 验证记录分开。真实浏览器验证通过 Next 代理登录，刷新后资料仍存在，退出后的 Cookie 不再有权限。
