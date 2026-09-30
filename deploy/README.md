# 本机 Docker 部署

2026-09-23 已完成实际启动验证：8 个常驻服务运行，init 退出码为 0；前后端、PostgreSQL 与杀毒健康检查正常。容器访问闭环验证通过管理员登录、角色、草稿保存、Word/PDF/Markdown 导出、Celery 调度与扫描解析、浏览器可达的签名下载、无额外积分消耗、删除与退出撤销。真实浏览器验证账号管理和手机工作台通过。

Docker Desktop 项目名：`ai-campus-office`。网页地址：`http://localhost:3100`。

部署闭环检查使用 `py -3 deploy/smoke.py`。运行前设置 `DEPLOY_ADMIN_PASSWORD` 为当前管理员密码；`admin.env` 只保存初始化时生成的旧值，改密后不会自动同步，检查脚本不再读取它，避免用过期凭据误报部署故障。

应用四个容器重启后同一闭环再次通过，管理员和数据库保留；迁移确认位于 `8dde7b50e065 (head)`。

在项目根目录执行：

```powershell
docker compose -f compose.app.yaml up -d --build
docker compose -f compose.app.yaml ps -a
docker compose -f compose.app.yaml logs --tail 100 backend worker
```

首次配置由 `backend/.venv/Scripts/python.exe deploy/configure.py` 生成，已有配置拒绝覆盖。管理员用户名为 `admin`，随机初始密码仅保存在本目录 `credentials.txt`。登录后修改密码；初始化任务不会覆盖已有账号。

服务包含前端、FastAPI、PostgreSQL/pgvector、Qdrant、Redis、MinIO、ClamAV、Celery Worker 和单实例定时调度器；init 完成迁移、学校、账号与私有文件桶初始化后退出，退出码应为 0。数据库、向量、对象、队列和病毒库使用持久化卷。现有备份脚本不包含 Qdrant `vectors` 卷；丢失该卷后后台会从 PostgreSQL 已提交向量逐文档重建，重建期间使用 pgvector 回退。若需缩短恢复时间，应另行备份 Qdrant 卷。停止请用 `docker compose -f compose.app.yaml stop`；不要使用 `down -v`，它会删除持久化数据。

这份配置仅绑定本机回环地址，应用环境为 local，不能视为公网 HTTPS 生产配置。原本机 SQLite 数据未自动迁移至这个独立 PostgreSQL 数据库。外部 AI、向量与媒体服务仍需配置和验收；未配置时显示明确不可用状态，不伪造生成结果。公开发布前仍需域名、证书、备份恢复与完整上线验收。

`deploy/*.env` 和 `credentials.txt` 是本机秘密配置，不应提交、发送或打入镜像。后端存储内部地址为 `storage:9000`，签名下载使用浏览器可达的 `localhost:19000`；更换访问主机时需同步 origins、前端 APP_ORIGIN 和存储 public_endpoint_url。

需要验证数据库与私有文件能否从同一快照恢复时，在没有运行中模型调用或媒体任务的维护时段执行 `backend/.venv/Scripts/python.exe deploy/coordinated-backup-restore.py`。脚本会短暂停止前端、后端、worker 和 scheduler，在写入静止期间采集 PostgreSQL 与对象文件，立即恢复应用，然后在一次性隔离 PostgreSQL/MinIO 中验证恢复、引用摘要和匿名访问。结果写入 `docs/COORDINATED-BACKUP-RESTORE.json`，备份副本留在 `local-data/coordinated-backup-*`。当前工具是本机最多 512 MiB 的手动验收演练，不是自动备份服务；备份目录包含实际学校数据，需按敏感数据管理。
