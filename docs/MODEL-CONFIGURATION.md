# 模型连接与积分计量

## 学校接入

后端提供 OpenAI Chat Completions 兼容协议适配器，供应商需支持流式文本、`stream_options.include_usage` 和有效的输入/输出 Token 用量。真实供应商尚未指定；不内置模型账号、凭据或模拟回答。不同协议需要对应适配器。

服务端环境变量：

```dotenv
MODEL_PROVIDERS={"primary":{"label":"学校模型服务","base_url":"https://your-provider.example/v1","secret_ref":"primary","tenant_slugs":["xflj"],"token_limit_parameter":"max_completion_tokens"}}
MODEL_API_KEYS={"primary":"由部署密钥系统注入"}
```

- `base_url` 是 API 前缀，适配器追加 `/chat/completions`。服务端禁止 URL 内嵌凭据、查询参数和跳转；后台不能修改此地址。
- `tenant_slugs` 明确列出允许使用该连接的学校，与 `TENANT_SLUG` 对应。
- `secret_ref` 引用 `MODEL_API_KEYS` 的键。自托管免认证服务可显式省略；云供应商应保留该字段。
- `token_limit_parameter` 按供应商文档选择 `max_completion_tokens` 或 `max_tokens`。
- 本地开发允许回环地址 HTTP；staging/production 必须 HTTPS。HTTP 客户端不继承系统代理环境。
- 凭据不通过管理 API 返回，不写入模型表、调用快照或审计日志。实际密钥放部署 Secret Store 或本地未提交的 `.env`，不能写入本文件。

配置连接后，管理员在 `/admin/models` 添加模型，填写供应商实际模型标识、上下文上限、最大输出、超时和积分费率，检查连接、启用并设为默认。检查会向供应商发送固定短请求，可能产生供应商费用，不扣教师积分，结果写入审计。

积分费率的单位是“每百万输入/输出 Token 对应的整数积分”，并不是平台硬编码的供应商货币价格。零费率由管理员显式配置。模型配置和价格按修订号留存，在途调用使用启动时的快照。

## 对话与 Skill 调用

`POST /api/v1/conversations/{id}/messages` 与 `POST /api/v1/skills/{id}/invoke` 均要求 `Idempotency-Key`。同键、同输入的已完成请求回放数据库结果，不重复请求供应商或计费；不同输入复用同键返回冲突。未完成的同一对话不能并发生成。

对话顺序：权限与配置检查 → 输入脱敏 → 上下文检查 → 积分预占 → 提交数据库事务 → 按权限检索资料并保存引用 → 请求回答模型 → 逐段保存/推送 SSE → 验证结束及用量 → 原子结算、更新消息状态。供应商请求期间不持有数据库事务锁。

预占按 UTF-8 字节与消息开销形成保守输入上限，再结合最大输出和配置费率计算；实际消费只使用供应商返回的有效 Token 用量，输入和输出合计后向上取整到整数积分。用量缺失、超过预占上限、超时或中断均不向教师收费，并释放预占。已收到的文本保留且标注未完成，不伪装为完整答案。

SSE 事件包含 `knowledge`、`citation`、`message.delta`、`usage`、`message.completed`、`error`。`useKnowledge` 默认启用，可关闭自动检索；显式附加资料仍须完成索引并通过权限检查。引用来自实际检索段落，刷新与幂等重放保留，撤回资料后重新检查访问权限。发送状态会轮询更新，“停止生成”中断连接，服务端清理预占。

入库和检索不扣教师积分。回答生成的计量包含实际发送给回答模型的参考资料，预占预留上下文容量，最终按有效用量结算并释放余量。向量配置、独立学校用量和本地部署说明见 [知识库配置](KNOWLEDGE-CONFIGURATION.md)。

## 异常恢复

进程硬退出时无法依赖内存清理。超时记录在用户再次查询对话时恢复，也可由运维定期执行：

```powershell
cd backend
.venv/Scripts/python -m app.cli recover-chat
```

命令只处理 `TENANT_SLUG` 对应学校的到期调用，每批最多 100 条对话；重复执行不会重复退款。在正式部署的调度/Worker 中每分钟执行，直到后续任务服务接管统一恢复。数据库不可用时先恢复数据库，再运行此命令核对遗留预占。

持久化调用的恢复期限为回答超时配置加 180 秒，以覆盖检索和清理时间；HTTP 请求自身仍使用各模型的超时限制。

## 当前验证与边界

- 单元/集成测试使用真实 SQLite/PostgreSQL，并通过本机 HTTP 服务模拟供应商协议，包括分段 UTF-8、超时、限流、缺少用量、响应中断与重复调用。
- 浏览器验证真实 Next/FastAPI/数据库链路，但生成文本来自测试目录中的明确协议夹具；这不证明真实 AI 能力或教学质量。
- 当前仅默认对话模型。自动重试、熔断和备用模型降级尚未实现，避免把不确定的供应商重试当作幂等保证。
- 当前安全层包含固定系统规则、手机号/身份证号/邮箱脱敏、基础提示注入特征记录；它不是完整内容安全评测或人工升级流程。
- Embedding 兼容适配、权限内检索和来源持久化已实现；OCR、多模态适配、供应商实际货币成本统计、生产安全与质量评测仍待完成。

协议依据：[OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat)、[HTTPX 超时](https://www.python-httpx.org/advanced/timeouts/)、[HTTPX 环境变量](https://www.python-httpx.org/environment_variables/)。
