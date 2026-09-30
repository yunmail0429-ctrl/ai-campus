# 模型管理与对话适配实现计划

**目标：** 将管理员模型配置、适配器、流式教学对话与积分预占/结算接成真实可配置链路。

**架构：** 服务端维护按学校授权的供应商连接资料，数据库只存供应商标识、模型标识、上下文限制及积分费率；凭据由环境配置注入。先提供 OpenAI Chat Completions 兼容协议，不选定实际供应商。独立适配器负责传输与协议校验，业务层负责权限、消息持久化和计费。

**技术栈：** FastAPI、SQLAlchemy、Alembic、HTTPX、Next.js/React、PostgreSQL。

## 任务与证据

- [x] `app/config.py`：定义供应商配置和 SecretStr 密钥映射，URL 禁止内嵌密码/查询参数；生产强制 HTTPS。后台不能传入任意地址或密钥。
- [x] `app/models.py`、Alembic：模型配置、价格及调用记录持久化，带租户约束；价格变更保留调用时的快照。
- [x] `app/model_admin.py`、React `/admin/models`：配置列表、创建、编辑、启停、设为默认、连接检查，写入审计；教师及其他学校不能配置。
- [x] `app/adapters.py`：HTTPX 流式传输，严格解析 SSE、限制响应大小、设置连接/读取及整体超时；验证结束标识、Token 计量，不透传供应商错误或凭据。
- [x] `app/chat_runtime.py`：幂等请求、私有 Skill 权限、持久化消息、积分预占、完成结算、失败/取消释放、断线恢复。外部请求期间不持有数据库事务锁。
- [x] 自动化测试区分真实数据库/HTTP 链路与模拟供应商响应；缺少真实供应商凭据时，不把协议夹具当作真实 AI 质量验收。

## 验证命令

```powershell
cd backend
.venv/Scripts/python -m pytest -q
.venv/Scripts/python tests/run_postgres.py
.venv/Scripts/python -m alembic check
.venv/Scripts/python -m ruff check app tests migrations
cd ../frontend
npm run build
npm run test:live
```

模型适配依据官方 [Chat API](https://developers.openai.com/api/reference/resources/chat) 和 [HTTPX](https://www.python-httpx.org/api/) 文档。真实供应商与模型名仍待用户确认；不同协议需增加对应适配器。文件/RAG、异步多模态任务、生产监控等仍保持完整项目范围，不以本计划代替总体交付。
