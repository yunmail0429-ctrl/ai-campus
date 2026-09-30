"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, json, PageResult, SkillTemplate } from "@/lib/api";
import { PageHeading } from "./page-heading";
import { QueryState } from "./query-state";
import { usePreferences } from "@/lib/preferences";
import { studioInspirations } from "@/lib/studio-inspirations";
const modes = ["3D", "数字人", "PPT", "图片", "视频", "音乐"];
const kinds: Record<string, string> = {
  图片: "image",
  PPT: "ppt",
  视频: "video",
  音乐: "music",
  数字人: "avatar",
  "3D": "3d",
};
const statuses: Record<string, string> = {
  awaiting_outline: "待确认大纲",
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};
interface Task {
  id: string;
  kind: string;
  prompt: string;
  status: string;
  error?: string;
  progress?: number;
  cancelRequested?: boolean;
  payer: "teacher" | "school";
  maxCredits: number;
  credits: number;
  createdAt: string;
  outline?: { slides: Array<{ title: string; purpose: string; points: string[] }> } | null;
}
function PptOutlineEditor({ task, onApproved }: { task: Task; onApproved: () => void }) {
  const [slides, setSlides] = useState(task.outline?.slides ?? []);
  const approve = useMutation({
    mutationFn: () => api(`/resource-tasks/${task.id}/approve-outline`, json("POST", { slides })),
    onSuccess: onApproved,
  });
  const update = (index: number, values: Partial<(typeof slides)[number]>) =>
    setSlides((current) => current.map((slide, position) => position === index ? { ...slide, ...values } : slide));
  return (
    <div className="studio-outline-editor">
      <h3>确认课件大纲</h3>
      <p>可修改每页标题、教学作用和要点。确认后开始生成 PPT。</p>
      {slides.map((slide, index) => (
        <div className="studio-outline-slide" key={index}>
          <strong>第 {index + 1} 页</strong>
          <label>标题<input value={slide.title} maxLength={48} required onChange={(e) => update(index, { title: e.target.value })} /></label>
          <label>教学作用<input value={slide.purpose} maxLength={120} required onChange={(e) => update(index, { purpose: e.target.value })} /></label>
          <label>要点（每行一条，最多 4 条）<textarea value={slide.points.join("\n")} onChange={(e) => update(index, { points: e.target.value.split("\n").slice(0, 4) })} /></label>
        </div>
      ))}
      <button className="start-button" type="button" disabled={approve.isPending || slides.some((slide) => !slide.title.trim() || !slide.purpose.trim() || !slide.points.length || slide.points.some((point) => !point.trim() || point.length > 100))} onClick={() => approve.mutate()}>
        {approve.isPending ? "正在确认…" : "确认大纲并生成 PPT"}
      </button>
      {approve.error && <p className="work-status" role="alert">{approve.error.message}</p>}
    </div>
  );
}
function uniqueTasks(pages?: PageResult<Task>[]) {
  const records = new Map<string, Task>();
  for (const page of pages ?? []) {
    for (const task of page.items) {
      if (!records.has(task.id)) records.set(task.id, task);
    }
  }
  return [...records.values()];
}
export function Studio() {
  const search = useSearchParams();
  const preferences = usePreferences();
  const initial = search.get("mode");
  const [modeOverride, setMode] = useState<string | undefined>(
    initial && modes.includes(initial) ? initial : undefined,
  );
  const [pageOverride, setPages] = useState<number | undefined>();
  const [styleOverride, setStyle] = useState<string | undefined>();
  const mode = modeOverride ?? preferences.data?.format ?? "PPT";
  const capabilities = useQuery({
    queryKey: ["resource-capabilities"],
    queryFn: () =>
      api<
        Array<{
          kind: string;
          available: boolean;
          reason: string | null;
          video: {
            duration: number;
            resolution: string;
            ratio: string;
            estimatedCredits: number;
          } | null;
        }>
      >("/resource-tasks/capabilities"),
  });
  const videoService = capabilities.data?.find((item) => item.kind === "video");
  const currentService = capabilities.data?.find((item) => item.kind === kinds[mode]);
  const pages = pageOverride ?? preferences.data?.pages ?? 10;
  const style = styleOverride ?? preferences.data?.style ?? "简洁教学";
  const [prompt, setPrompt] = useState("");
  const templateKey = search.get("template");
  const template = useQuery({
    queryKey: ["skill-templates"],
    queryFn: () => api<SkillTemplate[]>("/skills/templates"),
    enabled: !!templateKey,
  });
  useEffect(() => {
    const starter = template.data?.find((item) => item.id === templateKey)?.promptStarter;
    if (starter) setPrompt(starter);
  }, [template.data, templateKey]);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const [inspirationPage, setInspirationPage] = useState(0);
  const [inspirationNotice, setInspirationNotice] = useState("");
  const [previewPage, setPreviewPage] = useState(0);
  const client = useQueryClient();
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const retryKeys = useRef(new Map<string, string>());
  const seenStatuses = useRef(new Map<string, string>());
  const tasks = useInfiniteQuery({
    queryKey: ["tasks"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<PageResult<Task>>(
        `/resource-tasks${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    refetchInterval: (q) =>
      uniqueTasks(q.state.data?.pages).some((t) =>
        ["queued", "running"].includes(t.status),
      )
        ? 3000
        : false,
  });
  const rows = useMemo(() => uniqueTasks(tasks.data?.pages), [tasks.data]);
  useEffect(() => {
    let settled = false;
    for (const task of rows) {
      if (
        seenStatuses.current.get(task.id) !== task.status &&
        ["succeeded", "failed", "cancelled"].includes(task.status)
      )
        settled = true;
      seenStatuses.current.set(task.id, task.status);
    }
    if (settled) {
      void client.invalidateQueries({ queryKey: ["quota"] });
      void client.invalidateQueries({ queryKey: ["quota-transactions"] });
    }
  }, [rows, client]);
  const create = useMutation({
    mutationFn: () => {
      const payload = {
        kind: kinds[mode],
        prompt,
        options: { style, ...(mode === "PPT" ? { pages } : {}) },
      };
      const fingerprint = JSON.stringify(payload);
      if (submission.current?.fingerprint !== fingerprint)
        submission.current = { fingerprint, key: crypto.randomUUID() };
      return api<Task>("/resource-tasks", {
        ...json("POST", payload),
        headers: { "Idempotency-Key": submission.current.key },
      });
    },
    onSuccess: () => {
      submission.current = null;
      void client.invalidateQueries({ queryKey: ["tasks"] });
      void client.invalidateQueries({ queryKey: ["quota"] });
      void client.invalidateQueries({ queryKey: ["quota-transactions"] });
    },
  });
  const action = useMutation({
    mutationFn: ({ id, operation }: { id: string; operation: string }) => {
      if (!retryKeys.current.has(id))
        retryKeys.current.set(id, crypto.randomUUID());
      return api(`/resource-tasks/${id}/${operation}`, {
        ...json("POST"),
        headers: { "Idempotency-Key": retryKeys.current.get(id)! },
      });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["tasks"] });
      void client.invalidateQueries({ queryKey: ["quota"] });
      void client.invalidateQueries({ queryKey: ["quota-transactions"] });
    },
  });
  return (
    <div className="workspace-wrap">
      <PageHeading title="AI 创作中心" />
      <div className="studio-intro">
        <h1 className="text-2xl font-semibold">AI 创作中心</h1>
        <p>生成可下载的教学课件、图片提示词与视频分镜脚本。</p>
      </div>
      <div className="studio-production-layout">
      <form
        className="work-panel studio-config-panel"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <div className="studio-panel-heading"><h2>开始一项创作</h2><span>选择类型，描述需求</span></div>
        <p className="studio-type-label">创作类型</p>
      <div className="mode-tabs studio-mode-tabs">
        {modes.map((m) => (
          <button
            key={m}
            type="button"
            className={m === mode ? "selected" : ""}
            aria-pressed={m === mode}
            onClick={() => { setMode(m); setPreviewPage(0); }}
            title={capabilities.data?.find((item) => item.kind === kinds[m])?.reason || undefined}
          >
            {m}
            {capabilities.data?.find((item) => item.kind === kinds[m])?.available === false && (
              <span className="mode-unavailable">暂不可用</span>
            )}
          </button>
        ))}
      </div>
        <QueryState pending={capabilities.isPending} error={capabilities.error}
          retry={() => void capabilities.refetch()} />
        {currentService && !currentService.available && (
          <p className="work-status" role="status">
            当前暂不能创建{mode}：{currentService.reason || "服务未准备好"} 请联系学校管理员。
          </p>
        )}
        {mode === "视频" && (
          <div aria-live="polite">
            <p className="work-status">当前生成分镜脚本，可下载并用于后续接入视频模型。</p>
          </div>
        )}
        {mode === "图片" && <p className="work-status">当前生成图片提示词，可下载并用于后续接入图片模型。</p>}
        <label className="studio-prompt-label" htmlFor="studio-prompt">描述您想要的内容 / 教学课题</label>
        <textarea
          id="studio-prompt"
          ref={promptInput}
          className="editor"
          required
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          aria-label="创作需求"
          placeholder="描述课题、用途和想要的效果…"
        />
        <div className="statistics-filters">
          {mode === "PPT" && (
            <label>
              PPT 页数
              <input
                type="number"
                min={1}
                max={60}
                required
                value={pages}
                onChange={(event) => setPages(Number(event.target.value))}
              />
            </label>
          )}
          <label>
            创作风格
            <input
              maxLength={80}
              value={style}
              onChange={(event) => setStyle(event.target.value)}
            />
          </label>
        </div>
        <QueryState
          pending={preferences.isPending}
          error={preferences.error}
          retry={() => void preferences.refetch()}
        />
        {inspirationNotice && (
          <p className="work-status" role="status">{inspirationNotice}</p>
        )}
        <button
          className="start-button"
          disabled={
            create.isPending ||
            capabilities.isPending ||
            capabilities.isError ||
            currentService?.available === false ||
            preferences.isPending ||
            !!preferences.error ||
            (mode === "视频" &&
              (!videoService?.available || capabilities.isError))
          }
        >
          {create.isPending ? "正在提交…" : "开始创作"}
        </button>
        {create.error && (
          <p className="work-status" role="alert">
            {create.error.message}
          </p>
        )}
        {create.isSuccess && (
          <p className="work-status" role="status">
            任务已提交，可在下方查看进度。
          </p>
        )}
      </form>
      <section className="studio-preview-panel" aria-label="作品版式预览">
        <div className="studio-preview-toolbar">
          <div><strong>{mode === "图片" ? "图片提示词" : mode === "视频" ? "视频脚本" : mode}预览</strong><span>版式示意</span></div>
          <span>生成结果请在下方“最近作品”查看与下载</span>
        </div>
        {mode === "PPT" ? (
          <>
            <div className="studio-preview-canvas">
              <div className="studio-preview-slide">
                <div className="studio-preview-slide-top"><strong>教学课件</strong><span>第 {previewPage + 1} / {Math.min(Math.max(Number(pages) || 1, 1), 60)} 页</span></div>
                <div className="studio-preview-slide-main">
                  <div className="studio-preview-slide-copy">
                    <small>{previewPage === 0 ? "课堂主题" : `课件结构示例 · ${previewPage + 1}`}</small>
                    <h3>{previewPage === 0 ? (prompt.trim().slice(0, 30) || "在这里呈现您的教学主题") : ["教学目标", "课堂探索", "互动与练习", "课堂小结"][Math.min(previewPage - 1, 3)]}</h3>
                    <p>{previewPage === 0 ? "输入教学需求后，这里会展示标题的排版效果。" : "页面内容将在任务完成后生成；这里仅展示页面结构。"}</p>
                  </div>
                  <div className="studio-preview-slide-outline" aria-label="课件结构示意">
                    <span>课堂结构</span>
                    <strong>01　引入主题</strong>
                    <strong>02　探究与交流</strong>
                    <strong>03　课堂练习</strong>
                  </div>
                </div>
                <div className="studio-preview-slide-bottom"><span>校园 AI 工作台</span><span>{style}</span></div>
              </div>
            </div>
            <div className="studio-preview-pagination">
              <div aria-label="课件示意页码">
                {Array.from({ length: Math.min(Math.max(Number(pages) || 1, 1), 6) }, (_, index) => (
                  <button key={index} type="button" className="soft-button" aria-pressed={previewPage === index} onClick={() => setPreviewPage(index)}>{index + 1}</button>
                ))}
              </div>
              <span>共 {pages} 页 · 示例预览</span>
            </div>
          </>
        ) : (
          <div className="studio-preview-empty">
            <div className="studio-preview-empty-icon" aria-hidden="true">{mode === "图片" ? "▧" : mode === "视频" ? "▷" : mode === "音乐" ? "♫" : mode === "数字人" ? "♙" : "◇"}</div>
            <h3>在这里预览{mode}创作</h3>
            <p>{mode === "图片" ? "提交后下载图片提示词。" : mode === "视频" ? "提交后下载视频分镜脚本。" : "填写需求并提交任务后，可在“最近作品”查看状态与下载结果。"}</p>
          </div>
        )}
      </section>
      </div>
      <section className="studio-inspirations" aria-labelledby="studio-inspirations-title">
        <div className="section-heading">
          <h2 id="studio-inspirations-title">从一个教学灵感开始</h2>
          <button type="button" className="soft-button" onClick={() => setInspirationPage((page) => (page + 1) % 2)}>
            换一组灵感
          </button>
        </div>
        <p className="studio-section-note">选择教学场景，填入需求后再按你的课堂调整。</p>
        <div className="studio-inspiration-grid">
          {studioInspirations.slice(inspirationPage * 4, inspirationPage * 4 + 4).map((item) => (
            <button
              key={item.title}
              type="button"
              className="work-panel studio-inspiration-card"
              onClick={() => {
                setMode(item.mode);
                setPrompt(item.prompt);
                setInspirationNotice(`已填入“${item.title}”的创作需求，可继续修改后开始创作。`);
                promptInput.current?.focus();
              }}
            >
              <span className="studio-inspiration-format">{item.mode}</span>
              <strong>{item.title}</strong>
              <span className="studio-inspiration-description">{item.description}</span>
              <span className="studio-inspiration-action">使用这个灵感 →</span>
            </button>
          ))}
        </div>
      </section>
      <section className="task-list">
        <h2 className="font-semibold">最近作品</h2>
        <p className="studio-section-note">点击已完成的作品下载课件、图片提示词或视频脚本；其他任务仍可在这里查看进度。</p>
        <QueryState
          pending={tasks.isPending}
          error={tasks.error || action.error}
          retry={() => void tasks.refetch()}
        />
        {tasks.data && rows.length === 0 && (
          <p className="work-status">暂无创作任务。</p>
        )}
        {rows.map((t) => (
          <article
            className={`work-panel task-item studio-task-choice${t.status === "succeeded" ? " can-select" : ""}`}
            key={t.id}
          >
            {t.status === "succeeded" && <a className="studio-task-download" href={`/api/v1/resource-tasks/${t.id}/download`}
              aria-label={`下载作品：${t.prompt}`} />}
            <span className="studio-task-download-icon" aria-hidden="true">{t.status === "succeeded" ? "↓" : "○"}</span>
            <div>
              <strong>{t.prompt}</strong>
              <small>
                {statuses[t.status] || t.status}
                {t.status === "running" && ` · ${t.progress || 0}%`}
                {t.cancelRequested &&
                  ["queued", "running", "awaiting_outline"].includes(t.status) &&
                  " · 正在请求取消"}
                {t.error && ` · ${t.error}`}
              </small>
              <small>
                {t.kind === "image" ? "图片提示词" : t.kind === "video" ? "视频脚本" : modes.find((mode) => kinds[mode] === t.kind) ?? t.kind} ·{" "}
                {new Date(t.createdAt).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                  hour12: false,
                })}
                {" · "}
                {t.payer === "school"
                  ? "学校承担"
                  : t.status === "succeeded" || t.credits > 0
                    ? `实际消耗 ${t.credits} 积分`
                    : ["queued", "running", "awaiting_outline"].includes(t.status)
                      ? `预占 ${t.maxCredits} 积分`
                      : "未扣积分，预占已释放"}
              </small>
            </div>
            {["queued", "running", "awaiting_outline"].includes(t.status) && (
              <button
                className="soft-button"
                disabled={action.isPending || t.cancelRequested}
                onClick={() => action.mutate({ id: t.id, operation: "cancel" })}
              >
                取消
              </button>
            )}
            {t.status === "awaiting_outline" && t.outline && <PptOutlineEditor key={t.id} task={t} onApproved={() => void client.invalidateQueries({ queryKey: ["tasks"] })} />}
            {["failed", "cancelled"].includes(t.status) && (
              <button
                className="soft-button"
                disabled={action.isPending}
                onClick={() => action.mutate({ id: t.id, operation: "retry" })}
              >
                重试
              </button>
            )}
            {t.status !== "succeeded" && <span className="studio-task-hint">完成后可选</span>}
          </article>
        ))}
        {tasks.hasNextPage && (
          <button
            className="soft-button"
            disabled={tasks.isFetching}
            onClick={() => void tasks.fetchNextPage()}
          >
            {tasks.isFetchingNextPage ? "正在加载…" : "加载更早的任务"}
          </button>
        )}
      </section>
    </div>
  );
}
