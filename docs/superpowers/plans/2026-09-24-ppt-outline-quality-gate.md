# PPT 大纲确认与质量关卡实现计划

**目标：** 教师先确认逐页大纲，再生成可编辑 PPTX；导出前检查页面结构与视觉结果。

**架构：** 沿用资源任务、模型快照和积分预占。PPT 任务先生成结构化大纲，保存在任务快照并暂停等待教师确认；确认后继续生成。规划与成稿共用一次预占，按实际模型用量结算。渲染后运行结构与页面检查，失败时仅修复有问题的页，次数有上限。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、python-pptx、LibreOffice/Poppler、Next.js。

## 文件职责

- `backend/app/ppt_generation.py`：大纲和成稿提示词、模型调用、用量汇总。
- `backend/app/ppt_render.py`：受约束的可编辑 PPTX 渲染。
- `backend/app/ppt_quality.py`：PPTX 结构和渲染质量检查。
- `backend/app/resource_execution.py`：规划、待确认、成稿任务状态转换。
- `backend/app/resource_tasks.py`：确认大纲接口与任务输出。
- `frontend/src/components/studio.tsx`：逐页大纲编辑与确认。
- `backend/tests/test_ppt_generation.py`、`backend/tests/test_resource_execution.py`：阶段、计费和失败回归。

## 实施步骤

- [ ] 定义逐页大纲 schema，模型规划提示词要求准确页数、教学目标和课堂活动。
- [ ] PPT 任务规划完成后持久化大纲并等待确认；前端允许逐页编辑，确认后继续。
- [ ] 将两阶段用量纳入同一积分预占与实际结算；取消、超时释放未使用部分。
- [ ] 渲染 PPTX，检查页数、内容边界与可编辑性；逐页渲染为图片并检查布局。
- [ ] 运行接口、任务、渲染及前端验证，更新本机服务。
