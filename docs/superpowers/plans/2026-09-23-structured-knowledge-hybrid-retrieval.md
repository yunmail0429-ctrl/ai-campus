# 结构化知识入库与混合检索实现计划

> **面向 AI 代理的工作者：** 按下面的复选框逐项实施，完成每项后验证。当前工作区没有 Git 仓库，不能执行计划中的 commit 步骤；不要自行初始化仓库。

**目标：** 在 8 核、8 GB、100 GB 服务器上支持 OCR、复杂区域视觉解析、结构化内容持久化、Qdrant 语义与关键词召回、重排和带来源回答。

**架构：** PostgreSQL 与对象存储保存原文和结构化权威数据；Qdrant 保存可重建的检索点。沿用现有任务、模型连接和权限体系，先建立新索引并验证，再切换检索路径。

**技术栈：** FastAPI、SQLAlchemy/Alembic、Celery、PostgreSQL、Tesseract、Poppler、Qdrant、HTTP 模型接口、pytest。

---

## 文件职责

- `backend/app/file_parser.py`：原生文档解析与有序块生成。
- `backend/app/ocr.py`：有界的 PDF/图片 OCR 子进程调用。
- `backend/app/visual_extraction.py`：视觉接口适配、受限 JSON 校验和区域兜底。
- `backend/app/models.py`、`backend/migrations/versions/*`：结构化块、解析版本、索引状态。
- `backend/app/ingestion.py`：扫描后解析编排及事务性持久化。
- `backend/app/qdrant_index.py`：集合、BM25 配置、过滤索引、幂等 upsert/delete。
- `backend/app/indexing.py`：镜像写入、回填、版本切换与恢复。
- `backend/app/retrieval.py`：混合召回、数据库复核、重排和降级。
- `backend/app/config.py`、`compose.app.yaml`、`backend/Dockerfile`：服务和资源配置。
- `backend/tests/*`：解析、隔离、迁移和故障用例。

## 任务 1：结构化解析与迁移

- [ ] 为 `DocumentBlock` 和 `ParseVersion` 添加 ORM 与 Alembic 迁移：块记录 `document_id`、`parse_version_id`、`page`、`order`、`kind`、`bbox`、`text`、`markdown`、`cells`、`source`、`confidence`、`needs_review`；版本记录解析器、OCR/视觉模型、状态和错误码。复合外键保证同租户归属。
- [ ] 先写 PDF、Word 表格、PPT 表格和 HTML 的块顺序测试；运行定向 pytest 确认旧解析器无法满足。
- [ ] 改造 `file_parser.extract` 为有序块输出，同时保留旧 `chunks` 兼容层。把块按标题/段落/表格边界拼成检索段，包含 `block_ids`、页码范围和原文偏移。
- [ ] 运行定向测试、Alembic SQLite 升级及现有知识库测试。

## 任务 2：OCR 与视觉兜底

- [ ] 在 Docker 镜像中安装 Tesseract 中文/英文语言包和 PDF 渲染依赖；增加单页、像素、时间和任务并发限制。
- [ ] 先写图片、扫描 PDF、模糊页、超时与无服务配置的测试；确认失败。
- [ ] `ocr.py` 对图片和扫描页返回文本、位置及可信度。电子页保留原生文字。复杂表格/图表和低置信度区域由 `visual_extraction.py` 调用管理员配置的视觉接口，严格验证 JSON，失败保留可用 OCR 并标记待核对。
- [ ] `ingestion.py` 在扫描后调用编排器，事务性保存版本、块与段落；重复任务不会生成重复块。验证下载与删除路径。

## 任务 3：Qdrant 镜像与切换

- [ ] 添加 Qdrant 连接配置和 Compose 服务，限制本机资源与持久化目录；集合包含稠密向量和多语言 BM25 字段，按租户及索引版本建立过滤索引。
- [ ] 先写幂等 upsert、删除、版本隔离和回填测试；确认旧索引器不能满足。
- [ ] 在 PostgreSQL 向量入库成功后发布镜像 Outbox 事件。镜像任务读取已提交的段落和向量，写入 Qdrant 并读回校验。调度器恢复中断任务；回填旧文档，核对数量后逐文档切换。删除先撤销 SQL 可见性，异步清理 Qdrant。
- [ ] 验证 PostgreSQL 旧检索路径在 Qdrant 未就绪、故障、回填期间仍可工作；对已切换文档走 Qdrant。

## 任务 4：混合检索与重排

- [ ] 写中文精确词、近义词、编号、权限、删除和重排异常测试；确认当前纯向量检索不满足。
- [ ] Qdrant 对同一过滤范围分别召回稠密和 BM25 点，RRF 合并，每路最多 20 条、最终候选最多 30 条。候选 ID 回 SQL 检查租户、所有者、学校库/个人库/对话、文档状态和索引版本；从 SQL 取原文。
- [ ] 增加受限重排接口，验证返回 ID 与分数；失败时保留 RRF 次序。最终最多六条进入 `context_for`，引用补充页、块、解析版本和待核对状态。
- [ ] 跑完整后端测试与真实 PostgreSQL/Qdrant 集成测试；按中文问答样本检查引用。

## 任务 5：运维与验收

- [ ] 管理员可检查 OCR、视觉、Qdrant、重排配置和调用结果；凭据仅存 Secret 引用。更新配置文档、部署和备份说明。
- [ ] 在等价 8 核、8 GB、100 GB 限制下压测 OCR 单并发、混合检索、回填、重排降级；记录峰值 RSS、磁盘、延迟和恢复时间。测试容量必须包括 PostgreSQL、Qdrant、MinIO、ClamAV 和备份空间。
- [ ] 逐项对照设计规格验收。无 Git 仓库时以文件差异和验证输出交付，不声称已提交。
