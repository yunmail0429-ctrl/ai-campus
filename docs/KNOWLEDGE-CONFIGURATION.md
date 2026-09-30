# 文件与知识库配置

## 已实现与当前边界

教师入口 `/teacher/knowledge`，管理员入口 `/admin/knowledge`。个人库只能本人访问，管理员也不能读取其他教师的私人资料；学校库由管理员创建和维护，同校教师可以阅读。对话附件沿用相同的上传和处理流程，只能对话所有者访问。

支持上传 PDF、docx、pptx、UTF-8 TXT/Markdown、HTML、PNG/JPEG/WebP。部署镜像内置 Tesseract 中英文 OCR 与 Poppler：图片、扫描 PDF 和 docx/pptx 内嵌图片可提取文字；若运行环境缺少 OCR 程序，任务记录 `OCR_UNAVAILABLE` 并可重试。Word 表格保留单元格，PPT 图表提取可读的系列数值；视觉模型可补充复杂图片和图表说明，但未识别区域会标记待核对。旧版 doc/ppt 需要先转换格式。

处理顺序：保存原件 → Outbox → Redis/Celery → ClamAV → 独立进程解析/OCR → 按需视觉补充 → 页面内容块与分段保存 → 向量化 → 检索就绪。`GET /api/v1/documents/{id}/pages` 返回按原阅读顺序保存的内容块、表格单元格、OCR 位置和待核对标记；原件仍用于查看准确版式。`parsed` 表示完成解析；配置并启用向量模型后，后台推进索引，所有分段向量保存后才显示 `ready`。旧资料可由有管理权限的用户调用 `POST /api/v1/documents/{id}/reparse` 重新解析，处理中暂不可检索。学校/个人范围已实现；管理员维护的学科/年级归属、细粒度 ACL、文档替换版本、质量抽检、网页 URL 抓取尚未实现。

入库和数据库检索没有内置的按次收费，也不扣教师积分。可使用学校本地部署的向量模型；只有学校自行选择付费外部接口时，才可能产生相应供应商费用。系统不默认接入付费服务。教师只按回答生成用量扣积分，其中回答模型的输入包含实际发送的资料上下文。

## 向量模型与回答引用

先按 [模型连接说明](MODEL-CONFIGURATION.md) 配置学校允许的服务连接，再在管理员 `/admin/models` 页底部配置向量模型。该服务应支持 `/embeddings`，返回每条输入对应的向量、索引和 `usage.prompt_tokens`。同页可选填视觉服务与支持 `/chat/completions` 图像输入的模型，以及支持 `/rerank` 的服务与模型；未配置时保留 OCR 与融合排序。视觉接口使用兼容 Chat Completions 的 JSON 响应，重排接口使用 `results[{index,relevance_score}]` 格式。可与对话使用不同的服务、模型和凭据；自托管免认证服务可省略 `secret_ref`。开发环境允许回环 HTTP，生产使用 HTTPS。

填写部署中的模型标识、实际输出维度、输入上限、超时与相似度阈值。只有服务支持 `dimensions` 参数时才勾选“请求指定维度”。“检查向量连接”验证协议与维度，不验证教学检索质量。配置启用后，调度器自动处理已解析资料；更换嵌入服务地址、模型或维度会重新建立索引，检索不会混用旧向量。更换视觉模型不会自动重新解析旧文档，需要在资料页面选择“重新解析原件”。没有启用嵌入配置时，文档停留在已解析状态，普通对话仍可使用。

PostgreSQL 仍需 pgvector 扩展，用于现有向量持久化与 Qdrant 故障时回退。部署 `compose.app.yaml` 时，Qdrant 保存稠密向量和多语言 BM25 关键词表示；后台逐文档镜像并在读回确认后启用。混合查询以 RRF 合并，每路最多 20 条、候选最多 30 条；可选重排后最多返回 6 段。候选始终回 PostgreSQL 复核学校、个人与对话权限并读取原文。未配置 Qdrant、尚未镜像的文档或 Qdrant 故障时继续使用 pgvector 精确余弦检索。SQLite 等价计算仅用于本地开发与测试。

8 GB 服务器上 Worker 并发为 1，Qdrant 限制为 1.5 GB；视觉模型和重排模型应使用外部服务，避免在本机常驻。视觉每文档最多处理五张图片或页面，单张输入与响应都有大小限制。视觉或重排失败时保留 OCR 与原召回结果；未核对的图表数字不应直接当作准确事实。部署前须用真实文档测量内存、磁盘与检索延迟。

教师可在知识库页面搜索段落，或在对话中开启知识库参考。检索范围为同校学校库、本人的个人库与当前对话附件；其他教师的私人资料不会进入候选集。检索文本脱敏后发送到向量服务，引用上下文受回答模型容量限制，并标为不可信参考资料。未命中会明确显示，不制造来源。

来源编号、文件名、段落位置、更新时间和原文摘录随消息保存；刷新和幂等重放保留引用，不重复生成或扣费。历史引用再次检查资料访问权限；删除资料后不再返回其摘录或下载入口。引用表示提供给模型的参考材料，不能代替对回答准确性和引用对应关系的人工核验。

管理员可查看独立的嵌入调用次数、已确认输入 Token 和计量未确认次数。这是学校用量记录，不是货币账单；超时、进程中断或无有效计量的响应无法证明供应商未收费，记录保留为未确认。真实模型的语义检索与教育回答质量仍待配置后验收。

## 本地服务

在项目根目录通过进程环境配置随机的 `XFLJ_STORAGE_USER`、`XFLJ_STORAGE_PASSWORD`、`XFLJ_REDIS_PASSWORD`，不要把真实值提交或发到聊天中，然后运行：

```powershell
docker compose -f compose.knowledge.yaml up -d
```

该文件只绑定本机 S3 59000、控制台 59001、Redis 56379、ClamAV 53310。独立于现有 PostgreSQL Compose，避免影响已有数据库。ClamAV 首次需要加载病毒库，启动完成前资料保持待处理或重试状态。

在 `backend/.env` 或部署 Secret Store 中填写：

```dotenv
STORAGE={"bucket":"xflj-private","endpoint_url":"http://127.0.0.1:59000","region":"us-east-1","access_key":"替换为本机访问账号","secret_key":"替换为本机随机密码"}
REDIS_URL=redis://:经过URL编码的Redis密码@127.0.0.1:56379/0
CLAMAV_HOST=127.0.0.1
CLAMAV_PORT=53310
MAX_UPLOAD_BYTES=20971520
```

前端 `.env.local` 的 `MAX_UPLOAD_BYTES` 与后端保持一致。普通 JSON 请求仍限制为 2 MiB；文件请求允许配置的文件大小和 64 KiB 表单开销，BFF 与 FastAPI 都按实际读取字节限制，不能只靠 Content-Length 绕过。

在 backend 目录执行：

```powershell
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m app.cli init-storage
.venv\Scripts\python -m celery -A app.worker:app worker --pool=solo --loglevel=warning
```

另一个终端启动唯一的调度器：

```powershell
.venv\Scripts\python -m celery -A app.worker:app beat --loglevel=warning --schedule local-data/celerybeat-schedule
```

Windows 本地验证使用 solo；生产使用 Linux 容器的 prefork worker，配置内存/CPU 限制并使用同一版本代码。API、worker、beat 必须使用相同数据库、TENANT_SLUG、存储和 Redis 配置。队列按 `xflj-knowledge-<TENANT_SLUG>` 分开；同一 Redis 上不同学校使用不同 TENANT_SLUG，且部署凭据应按学校限制。beat 每 10 秒推动 Outbox，并恢复过期任务。

生产 S3 兼容服务必须使用 HTTPS；AWS S3 可省略 endpoint_url。访问账号至少需要桶 ACL/Policy 的只读查询与本项目对象的 Put/Get/Delete 权限；`init-storage` 创建新桶时另外需要 CreateBucket 权限，可由运维单独初始化后撤销。系统不会覆盖已有公开策略；检测到公开授权会拒绝上传/签发下载链接。对象键不使用文件名，服务端返回的元数据不含桶凭据或对象键。

下载前校验用户权限和扫描状态，使用 120 秒签名 URL，并强制附件下载。已签发的 URL 在有效期内仍可使用；删除触发对象清理后失效。存储端不得配置匿名公开访问；病毒扫描服务只应在受信网络监听，不向公网开放。生产数据加密、备份和保留策略仍需在部署阶段配置。

## 一致性与故障恢复

- 上传必须有 Idempotency-Key；同一个教师、同一个键、同样的内容/文件名/目标返回同一文件，不同内容返回 409。
- 先持久化 uploading 记录，再写入对象，最后在一个数据库事务中记录 queued、任务和 Outbox。存储失败后保留上传记录，相同请求可恢复；放弃的上传可在页面删除。
- Outbox 先向队列发布，再记录发布成功。发布后进程崩溃允许重复投递，任务租约保证正常情况下只有一个处理者提交分段。
- Worker 持有 3 分钟租约；恢复时撤销旧 token，旧进程不能覆盖新结果。扫描/存储暂时故障最多自动尝试 3 次，退避 15/30 秒；最终失败保留原因，页面可以手动重试。病毒文件不可重试，只能删除。
- 处理进程没有数据库和供应商凭据，最长 60 秒；Linux 增加 768 MiB 地址空间和 CPU 时间限制。解析上限 500 页/幻灯片、50 MiB ZIP 展开内容、500,000 字符。仍需生产容器资源限制，Windows 不提供同等地址空间限制。
- 删除立即撤回页面访问并清除分段，撤销处理租约，随后异步删除原件。删除对话同时撤回其附件。未完成的对象清理和丢失的队列投递在 10 分钟后重新入队。
- 索引任务每批最多处理 16 段，保存向量和下一批 Outbox 在同一事务中完成。过期租约恢复后复用已提交向量；旧任务不能在删除后写回。临时服务故障按 15/30 秒退避，连续失败 3 次后等待人工重试。外部服务已响应但本地未提交时，恢复可能重新请求该批次，不能承诺供应商侧只计费一次。
- `failed` 任务记录和审计保留故障线索；面向运维的死信队列控制台、数据保留清理和监控告警尚未完成。

## 验证入口

```powershell
# 临时 SQLite / 独立 PostgreSQL schema；S3 用 Moto，扫描成功由测试夹具控制
.venv\Scripts\python -m pytest -q
.venv\Scripts\python tests/run_postgres.py
# 真实 MinIO、Redis、ClamAV、Celery 和文件解析
.venv\Scripts\python tests/run_knowledge_services.py
# 再加浏览器上传、权限、标签、向量配置、检索、对话引用与积分回归
.venv\Scripts\python tests/run_knowledge_services.py --browser
```

实际服务测试创建随机端口、随机密码、无共享卷的独立容器，并在结束时清理。验证匿名下载拒绝、签名下载内容一致、标准无害 EICAR 测试字符串被 ClamAV 隔离、删除确实移除对象。此入口不使用模拟扫描服务；普通单元/数据库测试与真实服务证据分开记录。

向量与生成模型在上述测试中均使用明确标注的本机协议夹具。数据库测试验证真实 pgvector 算子、不同维度隔离、权限、模型变更和索引恢复；浏览器验证来源持久化及入库/检索不扣教师积分。这些证据不等同于真实模型质量验收。

实现所依据的官方资料：[Boto3 S3](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)、[ClamAV 扫描](https://docs.clamav.net/manual/Usage/Scanning.html)、[Celery 任务](https://docs.celeryq.dev/en/stable/userguide/tasks.html)、[pypdf 文字提取](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)。
