# 教学草稿导出

AI 助手的“内容编辑”面板支持 Word、PDF、Markdown，并保留原有文本下载。导出的是点击时当前编辑内容，不自动覆盖已保存草稿；纯本地排版不调用模型，也不扣教师积分。与创作中心生成 PPT 等新内容的计费分开。

## 接口与权限

`POST /api/v1/conversations/{conversation_id}/export`，JSON 为 `content`（最多 200000 字符）和 `format`（docx/pdf/md）。仅当前学校的对话所有者可以导出；管理员不绕过私人对话权限，已删除对话拒绝。请求受现有来源、会话和请求字节数限制。

返回对应 MIME 的附件字节，响应不缓存；不创建公开 URL 或永久导出文件，因此没有可长期访问的下载链接。`draft.exported` 审计仅记录格式和字节数，不记录正文。前端读取服务端错误，失败时显示原因。

## 排版与边界

- Word：A4、可编辑原生段落/标题/列表文本/表格，支持粗体、斜体、代码文本。中文采用宋体字体声明，具体字形由 Word 客户端字体提供。
- PDF：A4、自动分页、页码、表格重复表头与跨页处理。随项目提供并嵌入中文及拉丁补充字体，保留下标等字符，不依赖阅读设备安装中文字体。
- Markdown：原始内容的 UTF-8 字节，不转换正文。
- 标题、列表、引用、表格和代码按基础 Markdown 结构转换。图片只保留说明和地址；链接作为文字输出；不抓取远程图片，不执行 HTML/脚本，不创建外部 Word 关系。LaTeX/数学公式保留原文，尚未转换为公式对象。
- 非法控制字符、空正文、过多结构/超宽或超长表格、超过 100 页的 PDF 均明确拒绝，不静默截断。PDF 中字体未覆盖的字符返回可改用 Word/Markdown 的提示。

## 字体来源与打包

`backend/app/assets/fonts/` 包含中文 Regular/Bold、Noto Sans Regular/Bold、两份许可证及 `provenance.json`（固定上游提交、来源和哈希）。中文字体从 [Noto CJK](https://github.com/notofonts/noto-cjk) 的简体中文变量字体固定为 400/700 字重并更名为 Campus Export Sans；许可为 [SIL OFL 1.1](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)。拉丁补充字体来自固定提交的 Noto Sans，保持上游字体文件。

构建字体只需开发阶段运行 `python scripts/build_export_fonts.py`，使用 dev 依赖 FontTools 和公共上游下载；应用导出期间只读随包字体，不联网。setuptools package-data 包含字体、许可证和来源记录。

已构建 Python wheel 并检查包含全部四个字体、两份许可证和来源记录，避免部署后遗漏字体资源。依赖锁文件同步 ReportLab、MarkdownIt 与字体构建依赖，pip check 通过。

## 验证

`python -m pytest tests/test_draft_exports.py tests/test_workspace.py -q` 覆盖三种真实文件格式、中文与下标提取、PDF 字体内嵌、可编辑 Word 结构/表格/粗体、跨页 80 行表格、权限、已删除对话、控制字符/不支持格式/不支持字符、无额外积分和无正文审计。

`npm run test:live -- tests/livezzzz-exports.spec.ts` 验证实际 Next/FastAPI 链路的三种附件下载、当前编辑稿、不覆盖保存稿、不扣积分和手机面板。

PDF 示例由 Poppler 渲染并查看页面，发现原 CID 字体替换丢失下标后改为随包字体，重新渲染检查通过。Word 目前完成结构/内容和真实下载检查；本机没有 LibreOffice/Word 自动渲染器，尚未完成 Word 客户端视觉验收。
