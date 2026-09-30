# React 前端迁移实现计划

**目标：** 保留当前视觉，将原型迁移到开发文档指定的 React 技术栈，接入真实业务接口。
**架构：** Next.js App Router；独立页面和共享 AppShell；TanStack Query 管理服务端状态。API 使用同源 /api/v1，Next 服务端代理至可配置后端。后端尚未启动时显示可操作错误，不放行假登录。
**技术栈：** React、Next.js、TypeScript、Tailwind CSS、TanStack Query。

## 文件与步骤

- [x] 在 frontend/package.json、tsconfig.json、next.config.ts 和 postcss.config.mjs 配置工程。执行 npm install、npm run typecheck、npm run build。
- [x] 将原型样式机械提取到 frontend/src/app/prototype.css，复制 assets/school-emblem.png 到 public/assets；原文件保留。新增 globals.css 处理 React 布局、移动端和无障碍覆盖。
- [x] 在 src/lib/api.ts 定义统一响应、ApiError 和凭据请求；src/app/providers.tsx 创建 QueryClientProvider；/api/v1 代理未配置时返回稳定 SERVICE_UNAVAILABLE。
- [x] 在 src/components/login.tsx 迁移账号、密码显隐、记住账号和登录状态；成功后刷新 me 并进入工作台。不得用 teacher 字符串模拟认证。
- [x] 在 src/components/app-shell.tsx 迁移侧栏、折叠、账号菜单、退出；使用 Link 和 pathname 管理路由；会话读取失败允许重试，401 返回登录。
- [ ] 在 src/components/workspace.tsx 迁移工作台，最近工作和 Skill 使用查询结果；按实际记录显示空态。
- [x] 在 src/components/assistant.tsx 迁移聊天与编辑区；在 skills.tsx、studio.tsx、settings.tsx 迁移其余页面。界面与实际 API 契约同步，未接入流程保持明确说明。
- [x] 新增 Playwright 测试验证登录失败、API 成功后的导航、折叠、键盘、移动端无横向溢出；HTTP 测试夹具仅用于组件验证，不能作为后端验收。
- [ ] 实际启动 Next.js，检查浏览器异常和桌面/移动端截图，记录运行方式与未完成业务依赖。

## 验证命令

```powershell
cd frontend
npm install
npm run typecheck
npm run build
npm run start -- --hostname 127.0.0.1 --port 3000
npm run test:e2e
```

预期：类型检查和构建退出码为 0；浏览器回归通过；视觉接近原型，无未处理页面异常。测试后端夹具与真实服务验收分开记录。

## 后续完整工程

迁移不缩减 IMPLEMENTATION-DESIGN.md 中的后端、知识库、Skill 治理、异步任务、积分、管理员和部署范围。这些业务尚未实现，前端构建成功不表示整个项目完成。
