# 西附两江智能教育助手：系统开发文档

## 1. 开发目标

本文档用于指导 MVP 的架构设计、模块拆分、接口实现、环境部署、测试和后续多校复制。系统采用前后端分离、模型适配层、异步任务和多租户可扩展设计。

## 2. 推荐技术架构

### 2.1 总体分层

```text
Web / Mobile Web
      |
API Gateway / BFF
      |
Auth & RBAC  |  Workspace  |  Knowledge  |  Skill  |  Resource Tasks
      |
AI Orchestrator（检索、提示编排、模型路由、内容安全、计量）
      |
Model Adapters（通用大模型、Embedding、OCR、图片、音频、视频、3D）
      |
PostgreSQL + Object Storage + Vector Database + Redis + Queue
```

### 2.2 推荐技术栈

- 前端：Next.js + TypeScript + Tailwind CSS + TanStack Query
- 后端：NestJS 或 FastAPI，统一 REST API，长任务通过 SSE/WebSocket 推送
- 数据库：PostgreSQL
- 知识库检索：Qdrant 稠密向量与多语言 BM25 混合召回；PostgreSQL/pgvector 保留为镜像回填与故障回退
- 缓存与队列：Redis + BullMQ/Celery
- 文件存储：S3 兼容对象存储
- 身份认证：OIDC/OAuth2，MVP 可提供账号密码和短信登录适配器
- 可观测性：OpenTelemetry + Prometheus + Grafana + 集中式日志
- 部署：Docker，生产环境支持 Kubernetes；多校复制通过 Helm 和环境配置完成

## 3. 服务与模块

### 3.1 Identity Service

负责教师、管理员、组织、角色、登录会话和权限校验。学生、家长及绑定关系属于后续版本。

核心表：`tenants`、`users`、`roles`、`user_roles`、`classes`、`student_guardians`、`sessions`。

### 3.2 Workspace Service

负责教师的个人空间、会话、收藏、历史和个人设置。学生与家长空间属于后续版本。

核心表：`workspaces`、`conversations`、`messages`、`favorites`、`user_preferences`。

### 3.3 Knowledge Service

负责文件上传、解析、OCR、切片、Embedding、检索和引用返回。

核心表：`knowledge_bases`、`documents`、`document_chunks`、`document_acl`、`ingestion_jobs`。

处理流程：上传 → 病毒扫描 → 文件解析 → OCR（如需要）→ 清洗切片 → 向量化 → 建立索引 → 可检索。

### 3.4 Skill Service

负责 Skill 定义、版本、测试、审核、发布、作用域和调用统计。

核心表：`skills`、`skill_versions`、`skill_scopes`、`skill_test_cases`、`skill_reviews`、`skill_invocations`。

### 3.5 AI Orchestrator

统一编排模型调用：

1. 校验用户、租户、角色和 Skill 权限。
2. 收集输入并执行敏感信息脱敏。
3. 根据 Skill 配置检索知识库。
4. 组合系统提示、Skill 提示、上下文和引用。
5. 选择模型与降级策略。
6. 流式返回文本或创建异步资源任务。
7. 记录 Token、耗时、费用、结果状态和反馈。

### 3.6 Resource Task Service

负责 PPT、图片、音频、视频、数字人和 3D 模型等长耗时任务。

核心表：`resource_tasks`、`resource_outputs`、`task_events`。

状态：`queued`、`running`、`succeeded`、`failed`、`cancelled`。

### 3.7 Quota & Billing Service

负责学校额度、角色额度、个人额度、模型成本换算、预警和冻结策略。

核心表：`quota_accounts`、`quota_transactions`、`model_prices`、`quota_alerts`。

### 3.8 Admin & Analytics Service

负责后台配置、审计、指标聚合、报表和数据导出。

核心表：`audit_logs`、`usage_daily`、`risk_events`、`exports`。

## 4. 数据模型约定

所有业务表必须包含：`id`、`tenant_id`、`created_at`、`updated_at`，需要软删除的表增加 `deleted_at`。

关键租户隔离规则：所有查询必须携带 `tenant_id`，服务端禁止从客户端直接信任租户字段；通过认证上下文注入并在 Repository 层统一校验。

资源可见性枚举：`private`、`class`、`grade`、`subject`、`school`。

## 5. API 约定

### 5.1 统一响应

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "requestId": "req_xxx"
}
```

错误响应必须包含稳定错误码、用户可读消息和 `requestId`，内部堆栈只写入服务端日志。

### 5.2 核心接口

```text
POST   /api/v1/auth/login
GET    /api/v1/me
GET    /api/v1/skills
POST   /api/v1/skills/{skillId}/invoke
GET    /api/v1/conversations/{id}/messages
POST   /api/v1/conversations/{id}/messages
POST   /api/v1/knowledge-bases/{id}/documents
GET    /api/v1/resource-tasks/{id}
POST   /api/v1/resource-tasks/{id}/cancel
GET    /api/v1/admin/usage
POST   /api/v1/admin/skills/{id}/publish
POST   /api/v1/admin/models
GET    /api/v1/admin/audit-logs
```

### 5.3 流式对话

- 默认使用 SSE。
- 事件类型：`message.delta`、`citation`、`usage`、`message.completed`、`error`。
- 客户端断线后可通过会话消息接口恢复，不依赖前端内存。

## 6. 模型适配层

定义统一接口：

```ts
interface ModelAdapter {
  chat(input: ChatInput): AsyncIterable<ChatEvent>;
  embed(input: EmbedInput): Promise<number[]>;
  generate(input: MediaInput): Promise<TaskHandle>;
  estimateCost(input: UsageInput): CostEstimate;
}
```

适配器必须支持：超时、重试、熔断、模型降级、敏感内容拦截、请求去重和成本计量。业务模块不得直接调用供应商 SDK。

## 7. Skill 设计规范

Skill 配置建议采用 JSON：

```json
{
  "name": "高中语文教案生成",
  "scope": "school",
  "roles": ["teacher"],
  "inputSchema": {},
  "knowledgeScopes": ["school", "subject", "personal"],
  "modelPolicy": {"task": "balanced", "fallback": true},
  "outputSchema": {"type": "markdown"},
  "safetyPolicy": "education-default",
  "version": "1.0.0"
}
```

每个公共 Skill 上线前必须具备：至少 5 个测试样例、预期输出、风险边界、适用年级、负责人和回滚版本。

## 8. 安全实现要求

- API 网关启用 HTTPS、限流、请求体大小限制和基础 WAF 规则。
- 密码使用 Argon2id；密钥放入 Secret Manager，不进入仓库。
- 对象存储使用私有桶和短时签名 URL。
- 数据库和备份加密，生产环境按租户和敏感级别限制访问。
- 提示词与文件内容视为不可信输入，执行提示注入检测。
- 对学生心理、生涯、健康类内容启用专门安全策略和人工升级通道。
- 审计日志不可由普通管理员删除，导出文件设置有效期。

## 9. 前端页面与组件

### 9.1 页面路由

```text
/login
/teacher/workspace
/teacher/assistant
/teacher/skills
/teacher/create
/admin
/admin/users
/admin/knowledge
/admin/skills
/admin/models
/admin/credits
/admin/usage
```

第一版以 6 个核心界面为产品边界：登录页、教师 AI 工作台、AI 助手页面、Skill 中心、AI 创作中心、管理员后台。管理员后台的子路由用于承载账号、知识库、Skill、模型、积分和使用数据模块。

### 9.2 关键组件

- `LoginCard`：学校 Logo、学校名称、教师账号登录和错误提示。
- `AppShell`：角色导航、组织切换、通知和账号菜单。
- `WorkspaceCapabilityGrid`：命题审题、教案、PPT、绘图、数字人、视频、音乐、3D 能力入口。
- `AssistantThreePane`：左侧对话、中间工作区、右侧知识库与 Skill 状态。
- `SkillCenter`：公共 Skill、我的 Skill、创建 Skill。
- `CreationCenter`：图片、PPT、视频、音乐、数字人、3D 统一创作入口。
- `AdminConsole`：账号、知识库、Skill、模型、积分和使用数据。
- `ConversationPanel`：流式消息、引用、重新生成、反馈。
- `SkillLauncher`：输入字段、知识范围、模型策略和额度预估。
- `TaskCenter`：长任务状态、重试、取消和下载。
- `KnowledgeUploader`：上传、解析状态、标签和可见范围。
- `UsageChart`：调用量、额度和趋势。

所有页面必须提供加载、空数据、错误、无权限和移动端布局。

## 10. 异步任务与可靠性

- 长任务写入队列并持久化任务状态。
- Worker 使用幂等任务 ID，结果写入对象存储后再更新数据库状态。
- 失败按指数退避重试，超过阈值进入死信队列。
- 用户可查看失败原因和重试入口，管理员可重放任务。
- 关键操作采用 Outbox 事件保证数据库与消息发布一致。

## 11. 测试计划

### 11.1 单元测试

覆盖权限判断、额度扣减、Skill 版本规则、提示构建、模型路由和数据脱敏。

### 11.2 集成测试

覆盖登录、知识库入库、RAG 引用、Skill 调用、异步任务、对象存储和队列。

### 11.3 端到端测试

至少覆盖：

1. 教师生成教案并导出。
2. 管理员上传资料并发布 Skill。
5. 无权限调用、额度不足、模型超时和任务失败。

### 11.4 AI 质量评测

建立固定评测集，至少评估：事实正确性、引用命中率、格式符合度、拒答准确率、敏感内容拦截率和平均成本。

## 12. 部署与环境

- 环境：`local`、`dev`、`staging`、`production`。
- 配置通过环境变量和 Secret Manager 注入。
- CI 流程：代码检查 → 单元测试 → 构建镜像 → 数据库迁移检查 → 集成测试 → 发布。
- 数据库迁移必须向前兼容，禁止直接修改生产表结构。
- 生产发布采用灰度或可回滚版本，模型和 Skill 发布独立于应用发布。

## 13. 开发阶段计划

### 阶段 1：基础底座

身份认证、租户隔离、RBAC、基础布局、数据库、日志和对象存储。

### 阶段 2：教师 MVP

工作台、对话、知识库、公共 Skill、个人 Skill、教案/试题/课件生成。

### 阶段 4：治理后台

账号、知识库、Skill、模型、额度、审计和统计。

### 阶段 5：多模态与复制部署

PPT、图片、音视频、数字人、3D 任务，以及 Helm、租户初始化和学校配置模板。

## 14. 上线检查清单

- 权限矩阵和越权测试通过。
- 生产密钥、备份、日志和告警配置完成。
- 关键 Skill 评测集达标并完成审核。
- 额度扣减、模型成本和异常重试验证通过。

- 任务失败、模型不可用、知识库为空等异常状态可操作。
- 完成管理员培训、教师试点和问题反馈闭环。





