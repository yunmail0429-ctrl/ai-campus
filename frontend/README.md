# 西附两江 React 前端

技术栈：Next.js App Router + React + TypeScript + Tailwind CSS + TanStack Query。现有 HTML 原型仅作视觉对照，应用不加载旧 JS、不嵌入 iframe。

## 运行

需要 Node.js 20.9 或更高版本。

```powershell
cd frontend
npm ci
npm run dev -- --port 3100
```

访问 http://localhost:3100/login。复制 `.env.example` 为 `.env.local`，将 `BACKEND_URL` 指向 FastAPI，并使 `APP_ORIGIN` 与访问地址一致。先按 `../backend/README.md` 迁移数据库、初始化学校和创建账号。未配置时登录显示明确错误，不存在可绕过认证的演示账号。

当前机器 PATH 中优先命中的 Anaconda npm.cmd 无实际内容；如 npm 无输出，使用 `D:\Software\nodejs\npm.cmd` 执行以上命令。

## 验证

```powershell
npm run typecheck
npm run build
npm run test:e2e
```

浏览器测试使用本机 Edge 和独立端口 3217。配置补充 NO_PROXY，避免代理环境影响本地测试。5 项使用 HTTP 夹具验证 React 界面及不确定结果下的请求键复用；另 1 项访问实际 Next API 代理验证未配置服务时拒绝登录。这些不代表 FastAPI、数据库或真实 AI 已通过验收。

2026-09-22 验证：生产构建、TypeScript 检查成功，6 项浏览器夹具/代理测试通过。`test-results` 由每次测试重建；截图中的账号与积分来自隔离测试数据，不代表真实学校业务数据。

## 路由与结构

- `/login`：登录表单、记住账号、密码显隐、错误提示。
- `/teacher/workspace`：工作台、教学需求、附件选择、九个能力入口、最近工作和常用 Skill。
- `/teacher/assistant`：会话列表、重命名、删除、附件上传调用、SSE、引用、草稿保存和文本导出；已完成回答支持评分、说明、修改和撤回。
- `/teacher/skills`：公共/个人列表、创建、编辑、提交审核。
- `/teacher/create`：六种创作类型、任务提交、轮询、取消、重试、下载入口。
- `/teacher/settings`：资料、偏好、密码；我的积分含可用/预占/余额、累计获得/消耗/收回和分页来源日志。
- `/admin/users`：创建、启停学校账号。
- `/admin/skills`：查看审核资料、退回、发布、下架与回滚。
- `/admin/credits`：学校额度汇总、查找教师、分配/收回积分、冻结/恢复、查看个人明细。
- `/teacher/knowledge`、`/admin/knowledge`：个人/学校知识库、上传、处理与索引状态、预览、标签、下载、删除和检索。
- `/admin/models`：模型配置、积分费率、连接检查、启停与默认模型；独立向量配置与学校嵌入用量。
- `/admin/audit-logs`：只读操作记录和分页。
- `/admin/statistics`：学校调用、活跃登录账号、Token、实际积分消耗与嵌入用量；日期筛选、每日明细和脱敏 CSV。
- `/api/v1/*`：同源后端代理；后端 URL 仅由服务端环境变量配置。

`src/components` 保存 React 组件，`src/lib` 保存 API 客户端与 SSE 解析。`src/app/prototype.css` 提取自原型样式以保持视觉；`globals.css` 增加移动端和 React 布局适配。校徽使用本地资产；校园背景沿用原型远程地址。

## 当前边界与剩余工作

真实登录、资料与偏好、会话草稿、个人 Skill 及管理员账号/审核/积分/模型/审计已接入 FastAPI；`npm run test:live` 使用随机密码和真实服务验证。流式对话会保存消息并结算积分，支持停止生成与中断恢复。知识库支持上传、解析、索引和检索，回答来源随消息持久化，入库/检索不扣教师积分。完整文件服务浏览器验证使用后端 `tests/run_knowledge_services.py --browser`，含实际 MinIO、Redis、ClamAV、Celery。模型联调用例明确使用本机供应商协议夹具，不作为真实 AI 质量证据；真实供应商仍待配置验收。默认 Skill 与创作参数已联动，优先级与参数合同见 `../docs/PREFERENCES.md`。OCR、完整知识库组织权限与版本、头像、Word/PDF/PPT 导出、模型重试/熔断/降级、创作任务及生产部署仍需完成。完整目标见 `../docs/IMPLEMENTATION-DESIGN.md`。

## 参考

- [Next.js 安装与工程约定](https://nextjs.org/docs/app/getting-started/installation)
- [Tailwind CSS 与 Next.js](https://tailwindcss.com/docs/installation/framework-guides/nextjs)
- [TanStack Query 快速开始](https://tanstack.com/query/latest/docs/framework/react/quick-start)
