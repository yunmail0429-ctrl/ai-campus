# 文件与知识库入库实现计划

> 使用 executing-plans 在当前工作区内联执行；已有项目实施授权，不另建审批环节。工作区无 Git 仓库。

**目标：** 让教师和学校管理员能上传、管理、查看真实文件处理状态，并为向量检索提供经过扫描的持久化分段。

**架构：** 私有 S3 原件、数据库任务和 Outbox、Celery/Redis 执行、ClamAV 扫描、独立进程解析。个人资料只能本人访问，学校库由管理员维护、学校成员读取。文件未通过扫描不得下载或解析；解析完成不等于检索就绪。

**技术栈：** 现有 FastAPI/SQLAlchemy/React；boto3、Celery、ClamAV INSTREAM、pypdf/python-docx/python-pptx。

## 数据与 API

- `backend/app/models.py`：knowledge_bases、documents、document_chunks、ingestion_jobs、outbox_events；租户复合外键，文件内容摘要与幂等键，任务租约防止重复处理。
- `backend/app/knowledge.py`：库列表/创建、上传/附件、文件状态/标签/删除/重试、分段预览、短时下载签名。上传建立持久化 uploading 记录后写对象；同键同内容可恢复，不同内容拒绝。
- `backend/app/storage.py`：S3 私有对象写入、有限读取、短时下载 URL；错误脱敏。
- `backend/app/ingestion.py`、`file_parser.py`、`worker.py`：扫描、独立解析进程、分段保存、失败原因、Outbox 重发、任务租约恢复、删除清理。
- `frontend/src/components/knowledge.tsx`：教师/管理员共享库和文件管理，上传状态、预览、重试、标签和删除；原视觉样式。
- Next BFF 与 FastAPI：限制真实读取体积，上传默认 20 MiB，普通请求保留 2 MiB。

## 执行与验证

- [x] 添加权限、幂等上传、存储故障恢复、扫描隔离、任务去重和删除并发测试；首次运行确认 app.ingestion 缺失。
- [x] 实现模型、迁移、服务端配置、私有存储和上传 API。
- [x] 实现可恢复的 Outbox/Celery 任务、病毒扫描、PDF/Word/PPT/文本/HTML 解析和分段。图片/扫描件在 OCR 配置缺失时明确失败，不伪造内容。
- [x] 接入教师与管理员 React 页面和现有附件入口，验证上传不会重复创建文件。
- [x] SQLite / PostgreSQL 入口各 48 项通过；Ruff、Alembic、前端构建通过。真实 MinIO/Redis/ClamAV/Celery 专项 1 项通过，浏览器服务联调 5 项、前端协议夹具 6 项通过；两种证据分开记录。
- [x] 导出 OpenAPI，补充 KNOWLEDGE-CONFIGURATION.md、README 与实现状态。

## 与完整需求的关系

向量化/检索/RAG 引用、OCR、组织范围和文档版本管理属于同一知识库需求的后续连续实现，不因本计划入库测试通过而声称知识库或整个项目交付。继续保留 IMPLEMENTATION-DESIGN.md 的完整范围。
