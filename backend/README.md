# 西附两江 FastAPI 后端

Python 3.12 + FastAPI + SQLAlchemy + Alembic。学校由服务端 `TENANT_SLUG` 选定，每次业务查询通过 Repository 注入租户；不同学校部署可共享数据库，但前端不能指定或修改租户。密码采用 Argon2id，会话为数据库可撤销的随机 HttpOnly Cookie。

## 安装与数据库

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install -e . --no-deps
```

推荐 PostgreSQL。在项目根目录设置 `XFLJ_DB_PASSWORD` 后运行 `docker compose up -d --wait postgres`。Compose 只绑定本机 55432 端口，无默认数据库密码。将 backend/.env.example 复制为 .env，填写一致的密码和端口：

```text
DATABASE_URL=postgresql+psycopg://xflj:你的数据库密码@127.0.0.1:55432/xflj
TENANT_SLUG=xflj
```

无 PostgreSQL 时，仅本地调试允许 `DATABASE_URL=sqlite:///./local-data/xflj.db`。staging/production 强制使用 PostgreSQL、Secure Cookie 和 HTTPS Origin。

```powershell
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m app.cli init-school
.venv\Scripts\python -m app.cli create-user --username admin --name 学校管理员 --role admin
.venv\Scripts\python -m app.cli create-user --username teacher --name 教师 --role teacher
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

创建用户时在终端安全输入密码，不把密码放命令行或源码中。初始余额为 0；没有默认教师或管理员密码。

前端 `.env.local` 配置 `BACKEND_URL=http://127.0.0.1:8000` 和 `APP_ORIGIN=http://localhost:3100`，运行前端后访问 http://localhost:3100/login。后台 `ALLOWED_ORIGINS` 必须与前端实际访问地址一致。Next 代理转发 Cookie 和 Origin，后端二次校验 Origin。角色、租户和对象归属始终由后端校验。

## 本轮实现

- 登录、退出、账号查询、Argon2id、会话到期和撤销、失败登录限流。
- 资料和偏好持久化；修改密码撤销其他设备会话；管理员创建和启停账号。
- 会话创建/列表/重命名/软删除，消息查询，草稿保存和恢复。
- 个人 Skill 创建/修改/提交，发布资料、测试样例，管理员退回/发布/下架/回滚，版本快照和审计。
- 公共发布要求五个不同测试输入和预期输出、风险边界、适用年级、创建者和审核记录；第一次发布生成禁用基线版本 0 供回滚。该门槛只是发布配置与人工审核约束，尚未替代自动 AI 质量评测。
- 学校管理员直接分配/收回个人积分，必填原因与幂等标识；冻结/恢复积分账户，学校汇总及分页账本，相关变更写入审计。
- 教师可查可用/预占/余额、累计获得/消耗/收回，以及每笔变更的时间、来源、原因、余额和编号。未开放普通用户修改或删除账本的接口。
- 内部计量服务采用账户行锁，支持预占、按实际用量结算、取消或失败释放；冻结不阻止在途结算，重试不会重复扣费。费用不能超出预占上限；调用适配器必须预先确定最大用量并限制输出。
- 管理员只读审计、统一错误/requestId、请求大小限制、OpenAPI。
- 模型配置、启停、默认路由、连接检查与版本费率；服务端按学校授权供应商连接，密钥不进入数据库或响应。
- OpenAI 兼容对话适配器，真实 HTTP/SSE、超时和响应校验；对话及 Skill 调用使用持久化消息、请求幂等、输入脱敏、预占/结算/释放。浏览器可停止生成、刷新恢复已保存内容。

模型接入配置、接口语义、计量和进程中断恢复命令见 [模型配置说明](../docs/MODEL-CONFIGURATION.md)。真实供应商尚待指定，测试供应商仅存在于 tests 目录。

文件与知识库已支持个人/学校资料、S3 私有原件、ClamAV 扫描、CPU OCR、按页结构化内容、Celery/Redis 和 Outbox、幂等恢复、签名下载、删除清理，以及教师和管理员页面。已接入兼容 Embedding 服务、Qdrant 向量与 BM25 混合召回、pgvector 回退、可选视觉识别与重排、权限复核和持久化回答引用。入库和检索不扣教师积分，回答生成按实际用量结算。配置见 [知识库配置](../docs/KNOWLEDGE-CONFIGURATION.md)。真实视觉与重排模型质量、目标服务器容量尚待验收。

接口文档：http://127.0.0.1:8000/docs。通过 `.venv\Scripts\python -m app.cli export-openapi` 更新 `../docs/openapi.json`，文件由后端 schema 生成。

管理员使用统计及脱敏 CSV 已接入 `/api/v1/admin/statistics`，按北京时间筛选、汇总真实调用和积分结算，不返回个人内容；指标定义与验证见 [统计说明](../docs/STATISTICS.md)。

已完成回答可由本人通过 `PUT /api/v1/conversations/{id}/messages/{messageId}/feedback` 提交 1–5 分与可选说明，`rating: null` 撤回。反馈随消息恢复，不调用模型、不扣积分；统计只展示数量和均分，不导出说明原文。

## 验证

```powershell
$env:PYTEST_DEBUG_TEMPROOT=(Resolve-Path local-data).Path
.venv\Scripts\python -m pytest -q
.venv\Scripts\python tests/run_postgres.py
.venv\Scripts\python tests/run_knowledge_services.py --browser
.venv\Scripts\python -m alembic check
.venv\Scripts\python -m ruff check app tests
cd ../frontend
npm run build
npm run test:live
```

`run_postgres.py` 为本次测试创建独立、随机密码与随机端口的 PostgreSQL 容器，结束后停止并移除该测试容器。各测试使用独立 schema，只清理自己创建的 schema；不会重建已有业务表。普通 pytest 使用临时 SQLite。两种数据库测试均通过迁移创建 schema，并检查重复执行升级不会重复建表。

浏览器 `test:live` 自动启动真实 FastAPI、隔离 SQLite 与 Next 服务，随机创建测试账号密码，验证登录、Cookie 属性、资料/Skill/草稿持久化、退出撤销、积分分配、模型配置与对话计量。新增模型用例通过本机 HTTP 供应商协议夹具生成确定性测试文本，验证停止生成会释放预占；不代表真实 AI 质量。浏览器数据库为 SQLite，PostgreSQL 由后端集成测试单独验证。

## 未完成的完整项目范围

真实供应商验收、模型重试/熔断/降级、完整安全策略、OCR、组织范围与文件版本、多模态队列任务及产物、供应商货币成本统计、积分预警、资源产出及细分维度统计、Skill 自动评测和多版本迭代、OIDC/短信、头像、教学产物导出和运维部署仍未完成。管理员账号、知识库、Skill、积分、模型、统计和审计页面已接通接口。未配置回答模型明确返回 `MODEL_NOT_CONFIGURED`，不会伪造答案或扣费。

登录限流当前在数据库中按学校和账号串行计数，适用于基础部署；公共网关按 IP 限流、WAF、观测、告警和数据保留清理须在生产阶段补齐。
