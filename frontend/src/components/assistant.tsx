"use client";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  api,
  ApiError,
  Conversation,
  exportFile,
  json,
  PageResult,
  Me,
} from "@/lib/api";
import { streamMessage } from "@/lib/stream";
import { QueryState } from "./query-state";
import { useTeachingDraft } from "./teaching-draft";
import { DocumentList, KnowledgeDocument } from "./knowledge";
import { Citation, Citations } from "./citations";
import { AnswerFeedback, Feedback } from "./answer-feedback";
import { usePreferredSkill } from "@/lib/preferences";
import { DraftHistory, DraftSnapshot } from "./draft-history";
import { answerDraft } from "@/lib/draft-content";
import { documentFormats } from "@/lib/document-formats";
import { MessageMarkdown } from "./message-markdown";
interface Message {
  id: string;
  role: string;
  content: string;
  status: string;
  citations?: Citation[];
  feedback?: Feedback | null;
}
export function Assistant() {
  const search = useSearchParams();
  const client = useQueryClient();
  const teaching = useTeachingDraft();
  const owner = client.getQueryData<Me>(["me"])?.id;
  const editKey = (id: string) => JSON.stringify([owner, id]);
  const [selected, setSelected] = useState(search.get("id") || "");
  const activeConversation = useRef(selected);
  activeConversation.current = selected;
  const [input, setInput] = useState(teaching.draft.prompt);
  const {
    skill,
    setSkill,
    skills,
    pending: preferencePending,
    error: preferenceError,
  } = usePreferredSkill(
    search.has("skill")
      ? search.get("skill")!
      : teaching.draft.skillChosen
        ? teaching.draft.skill
        : undefined,
  );
  const [panel, setPanel] = useState("");
  const [delta, setDelta] = useState("");
  const [busy, setBusy] = useState(false);
  const [outgoing, setOutgoing] = useState<{
    content: string;
    previousIds: Set<string>;
  } | null>(null);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [draftRevision, setDraftRevision] = useState(0);
  const [draftDirty, setDraftDirty] = useState(false);
  const [restoringDraft, setRestoringDraft] = useState(false);
  const [notice, setNotice] = useState("");
  const [exportFormat, setExportFormat] = useState("docx");
  const exportDraft = useMutation({
    mutationFn: async () => ({
      blob: await exportFile(`/conversations/${selected}/export`, {
        content: draft,
        format: exportFormat,
      }),
      format: exportFormat,
    }),
    onSuccess: ({ blob, format }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `教学草稿.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("文件导出已开始。");
    },
  });
  const [consumed, setConsumed] = useState<number | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [useKnowledge, setUseKnowledge] = useState(true);
  const [knowledgeState, setKnowledgeState] = useState("");
  const [file, setFile] = useState<File | null>(teaching.draft.file);
  const controller = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const attachment = useRef<{
    file: File;
    conversation: string;
    key: string;
    id?: string;
  } | null>(null);
  const chats = useInfiniteQuery({
    queryKey: ["conversations", "history"],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      api<PageResult<Conversation>>(
        `/conversations${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (page) => page.nextCursor || undefined,
  });
  const chatItems = (
    chats.data?.pages.flatMap((page) => page.items) || []
  ).filter(
    (chat, index, all) =>
      all.findIndex((item) => item.id === chat.id) === index,
  );
  const messages = useInfiniteQuery({
    queryKey: ["messages", selected, "history"],
    initialPageParam: "",
    queryFn: ({ pageParam }) =>
      api<PageResult<Message>>(
        `/conversations/${selected}/messages${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (page) => page.nextCursor || undefined,
    enabled: !!selected,
    refetchInterval: (query) =>
      !busy &&
      query.state.data?.pages.some((page) =>
        page.items.some((m) => m.status === "running"),
      )
        ? 1000
        : false,
  });
  // Pages arrive newest first; each page contains messages in reading order.
  // Prefer the refreshed newest page when page boundaries overlap.
  const newestMessages = new Map<string, Message>();
  for (const page of messages.data?.pages || []) {
    for (const message of page.items) {
      if (!newestMessages.has(message.id))
        newestMessages.set(message.id, message);
    }
  }
  const messageItems = [...(messages.data?.pages || [])]
    .reverse()
    .flatMap((page) => page.items)
    .filter(
      (message, index, all) =>
        all.findIndex((item) => item.id === message.id) === index,
    )
    .map((message) => newestMessages.get(message.id)!);
  const savedDraft = useQuery({
    queryKey: ["draft", selected],
    queryFn: () => api<DraftSnapshot>(`/conversations/${selected}/draft`),
    enabled: !!selected,
  });
  useEffect(() => {
    if (savedDraft.data && !draftDirty) {
      const local = owner
        ? teaching.edits.get(JSON.stringify([owner, selected]))
        : undefined;
      setDraft(local?.content ?? savedDraft.data.content);
      setDraftRevision(local?.revision ?? savedDraft.data.revision);
      if (local) {
        setDraftDirty(true);
        setNotice("已恢复本次打开应用期间的未保存修改，请保存草稿。");
      }
    }
  }, [savedDraft.data, draftDirty, owner, selected, teaching.edits]);
  useEffect(() => {
    if (!owner || !selected || !draftDirty) return;
    teaching.setEdit(JSON.stringify([owner, selected]), {
      content: draft,
      revision: draftRevision,
    });
  }, [owner, selected, draftDirty, draft, draftRevision, teaching.setEdit]);
  useEffect(() => () => controller.current?.abort(), []);
  function select(id: string) {
    if (inFlight.current) return;
    setSelected(id);
    setDraft("");
    setDraftDirty(false);
    setDraftRevision(0);
    setRestoringDraft(false);
    save.reset();
    exportDraft.reset();
    setDelta("");
    setError("");
    setNotice("");
    setConsumed(null);
    setCitations([]);
    setKnowledgeState("");
  }
  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api(`/conversations/${id}`, json("PATCH", { title })),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/conversations/${id}`, json("DELETE")),
    onSuccess: (_, id) => {
      teaching.deleteEdit(editKey(id));
      if (selected === id) select("");
      void client.invalidateQueries({ queryKey: ["conversations"] });
      client.removeQueries({ queryKey: ["messages", id] });
      client.removeQueries({ queryKey: ["draft", id] });
    },
  });
  const save = useMutation({
    mutationFn: ({
      conversationId,
      content,
      revision,
    }: DraftSnapshot & { conversationId: string }) =>
      api<DraftSnapshot>(
        `/conversations/${conversationId}/draft`,
        json("PUT", { content, revision }),
      ),
    onSuccess: (value, variables) => {
      teaching.deleteEdit(editKey(variables.conversationId));
      client.setQueryData(["draft", variables.conversationId], value);
      void client.invalidateQueries({
        queryKey: ["draft-versions", variables.conversationId],
      });
      if (activeConversation.current === variables.conversationId) {
        setDraft(value.content);
        setDraftRevision(value.revision);
        setDraftDirty(false);
        setNotice(`草稿已保存为 v${value.revision}。`);
      }
    },
    onError: (_error, variables) => {
      void client.invalidateQueries({
        queryKey: ["draft", variables.conversationId],
      });
      void client.invalidateQueries({
        queryKey: ["draft-versions", variables.conversationId],
      });
    },
  });
  async function send() {
    if (preferencePending || preferenceError) return;
    if ((!input.trim() && !file) || inFlight.current) return;
    inFlight.current = true;
    const sentInput = input;
    setOutgoing({
      content: input || (file ? `附件：${file.name}` : ""),
      previousIds: new Set(messageItems.map((message) => message.id)),
    });
    setInput("");
    setBusy(true);
    setKnowledgeState("");
    setConsumed(null);
    setError("");
    setDelta("");
    setCitations([]);
    const abort = new AbortController();
    controller.current = abort;
    let id = selected;
    try {
      if (!id) {
        const chat = await api<Conversation>("/conversations", {
          ...json("POST", { title: input.trim().slice(0, 60) || file?.name }),
          signal: abort.signal,
        });
        id = chat.id;
        setSelected(id);
      }
      let documentId: string | undefined;
      if (file) {
        if (
          attachment.current?.file !== file ||
          attachment.current.conversation !== id
        )
          attachment.current = {
            file,
            conversation: id,
            key: crypto.randomUUID(),
          };
        const form = new FormData();
        form.append("file", file);
        const result = attachment.current.id
          ? await api<KnowledgeDocument>(
              `/documents/${attachment.current.id}`,
              { signal: abort.signal },
            )
          : await api<KnowledgeDocument>(`/conversations/${id}/attachments`, {
              method: "POST",
              body: form,
              signal: abort.signal,
              headers: { "Idempotency-Key": attachment.current.key },
            });
        attachment.current.id = result.id;
        documentId = result.id;
        if (result.status && result.status !== "ready") {
          setPanel("status");
          throw new ApiError(
            result.errorMessage ||
              (result.status === "parsed"
                ? "资料已解析，但索引尚不可用。请稍后重试；若一直如此，请联系管理员检查向量模型配置。"
                : "资料已保存，处理进度可在调用详情中查看。完成解析和索引后才能用于对话。"),
            409,
            "DOCUMENT_NOT_READY",
          );
        }
      }
      const payload = {
        content: input,
        skillId: skill || null,
        documentIds: documentId ? [documentId] : [],
        useKnowledge,
      };
      const fingerprint = JSON.stringify([id, payload]);
      if (submission.current?.fingerprint !== fingerprint)
        submission.current = { fingerprint, key: crypto.randomUUID() };
      await streamMessage(
        `/conversations/${id}/messages`,
        payload,
        (event) => {
          if (event.event === "usage" && typeof event.data.credits === "number")
            setConsumed(event.data.credits);
          if (event.event === "message.delta")
            setDelta((value) => value + String(event.data.delta || ""));
          if (event.event === "knowledge") {
            const labels: Record<string, string> = {
              matched: "已找到参考资料",
              empty: "暂无可检索资料",
              no_match: "未找到相关资料，本次回答不引用知识库",
              not_configured: "学校暂未启用知识库检索",
              context_limit: "当前对话空间不足以加入资料，本次未引用知识库",
            };
            setKnowledgeState(labels[String(event.data.status)] || "");
          }
          if (event.event === "citation")
            setCitations((values) => [
              ...values,
              {
                ...event.data,
                title: String(event.data.title || "引用资料"),
              } as Citation,
            ]);
        },
        abort.signal,
        submission.current.key,
      );
      submission.current = null;
      setInput("");
      setFile(null);
      attachment.current = null;
      teaching.setDraft({ prompt: "", file: null, skill: "" });
    } catch (e) {
      setInput(sentInput);
      const uncertain =
        e instanceof ApiError &&
        [
          "SERVICE_UNAVAILABLE",
          "NETWORK_ERROR",
          "DATABASE_UNAVAILABLE",
          "GENERATION_IN_PROGRESS",
        ].includes(e.code);
      if (
        (e instanceof ApiError && !uncertain) ||
        (e instanceof DOMException && e.name === "AbortError")
      )
        submission.current = null;
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(
          e instanceof ApiError && e.requestId
            ? `${e.message}（请求编号：${e.requestId}）`
            : e instanceof Error
              ? e.message
              : "发送失败。",
        );
    } finally {
      // Keep the outgoing message and streamed answer visible until history
      // has caught up, then replace both in the same render.
      if (id) await client.invalidateQueries({ queryKey: ["messages", id] });
      inFlight.current = false;
      setBusy(false);
      setOutgoing(null);
      setDelta("");
      void client.invalidateQueries({ queryKey: ["conversations"] });
      void client.invalidateQueries({ queryKey: ["quota"] });
      void client.invalidateQueries({ queryKey: ["quota-transactions"] });
      void client.invalidateQueries({ queryKey: ["documents"] });
    }
  }
  const title = chatItems.find((c) => c.id === selected)?.title || "新对话";
  function newConversation() {
    select("");
    setInput("");
    setFile(null);
  }
  return (
    <div className="chat-app">
      <aside className="chat-history">
        <div className="history-top">
          <h2>聊天记录</h2>
        </div>
        <button
          className="action-button action-button-secondary new-conversation-button"
          disabled={busy}
          onClick={newConversation}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            aria-hidden="true"
          >
            <path d="M12 4v16M4 12h16" />
          </svg>
          新建对话
        </button>
        <p className="history-note">教学对话与工作草稿</p>
        <QueryState
          pending={chats.isPending}
          error={chats.error || rename.error || remove.error}
          retry={() => void chats.refetch()}
        />
        <div className="history-list">
          {chatItems.map((c) => (
            <div
              key={c.id}
              className={`history-row ${c.id === selected ? "selected" : ""}`}
            >
              <button disabled={busy} aria-current={c.id === selected ? "true" : undefined} onClick={() => select(c.id)}>
                {c.title}
              </button>
              <button
                disabled={busy || rename.isPending}
                aria-label={`重命名 ${c.title}`}
                onClick={() => {
                  const title = window.prompt("重命名对话", c.title);
                  if (title?.trim())
                    rename.mutate({ id: c.id, title: title.trim() });
                }}
              >
                ✎
              </button>
              <button
                disabled={busy || remove.isPending}
                aria-label={`删除 ${c.title}`}
                onClick={() => {
                  if (window.confirm("删除这条对话及其草稿？"))
                    remove.mutate(c.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
          {chats.hasNextPage && (
            <button
              className="soft-button"
              disabled={busy || chats.isFetching}
              onClick={() => void chats.fetchNextPage()}
            >
              {chats.isFetchingNextPage ? "加载中…" : "加载更早对话"}
            </button>
          )}
        </div>
      </aside>
      <section className="chat-conversation">
        <header className="chat-header">
          <div>
            <h1>{title}</h1>
            <small>AI 教学助手</small>
          </div>
          <div>
            <button
              className="action-button action-button-quiet header-new-conversation"
              disabled={busy}
              onClick={newConversation}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                aria-hidden="true"
              >
                <path d="M12 4v16M4 12h16" />
              </svg>
              新对话
            </button>
            <button
              className="soft-button"
              onClick={() => setPanel(panel === "status" ? "" : "status")}
              aria-expanded={panel === "status"}
            >
              调用详情
            </button>
            <button
              className="soft-button"
              onClick={() => setPanel(panel === "edit" ? "" : "edit")}
              aria-expanded={panel === "edit"}
            >
              内容编辑
            </button>
          </div>
        </header>
        <div className="chat-messages" role="log" aria-label="当前对话">
          {selected && (
            <QueryState
              pending={messages.isPending}
              error={messages.error}
              retry={() => void messages.refetch()}
            />
          )}
          {!selected && !busy && (
            <div className="chat-empty">
              <span>🤖</span>
              <h2>一起准备下一堂课</h2>
              <p>讨论教学思路、检查试题，或从一份资料开始。</p>
            </div>
          )}
          {messages.hasNextPage && (
            <button
              className="soft-button"
              disabled={busy || messages.isFetching}
              onClick={() => void messages.fetchNextPage()}
            >
              {messages.isFetchingNextPage ? "加载中…" : "加载更早消息"}
            </button>
          )}
          {messageItems
            .filter(
              (m) =>
                !(
                  busy &&
                  m.role === "assistant" &&
                  (m.status === "running" ||
                    (outgoing && !outgoing.previousIds.has(m.id)))
                ) &&
                !(
                  outgoing &&
                  m.role === "user" &&
                  !outgoing.previousIds.has(m.id)
                ),
            )
            .map((m) => (
              <article key={m.id} className={`chat-message ${m.role}`}>
                <div className="message-label">
                  {m.role === "user" ? "你" : "🤖 AI 助手"}
                </div>
                {m.role === "assistant" ? (
                  <MessageMarkdown content={m.content} />
                ) : (
                  <div className="message-body">{m.content}</div>
                )}
                {m.role === "assistant" && <Citations items={m.citations} />}
                {m.role === "assistant" && m.status === "completed" && (
                  <>
                    <button
                      className="soft-button"
                      disabled={
                        savedDraft.isPending ||
                        !!savedDraft.error ||
                        save.isPending ||
                        restoringDraft ||
                        draft.length +
                          answerDraft(m.content, m.citations).length +
                          (draft ? 2 : 0) >
                          200000
                      }
                      title={
                        draft.length +
                          answerDraft(m.content, m.citations).length +
                          (draft ? 2 : 0) >
                        200000
                          ? "合并后超过草稿长度上限，请先整理草稿。"
                          : undefined
                      }
                      onClick={() => {
                        setDraft((current) =>
                          current
                            ? `${current}\n\n${answerDraft(m.content, m.citations)}`
                            : answerDraft(m.content, m.citations),
                        );
                        setDraftDirty(true);
                        setPanel("edit");
                        setNotice("回答已加入草稿，保存后生成版本。");
                      }}
                    >
                      加入教学草稿
                    </button>
                    <AnswerFeedback
                      conversation={selected}
                      message={m.id}
                      feedback={m.feedback}
                    />
                  </>
                )}
                {m.role === "assistant" && m.status === "running" && (
                  <p className="work-status">正在生成，内容会自动更新。</p>
                )}
                {m.role === "assistant" &&
                  (m.status === "failed" || m.status === "cancelled") && (
                    <p className="work-status">
                      本次生成未完成，预占积分已释放。以上为中断前内容。
                    </p>
                  )}
              </article>
            ))}
          {outgoing && (
            <article className="chat-message user">
              <div className="message-label">你</div>
              <div className="message-body">{outgoing.content}</div>
            </article>
          )}
          {busy && (
            <article className="chat-message assistant">
              <div className="message-label">🤖 AI 助手</div>
              {delta ? (
                <MessageMarkdown content={delta} />
              ) : (
                <p className="work-status" role="status">
                  正在思考…
                </p>
              )}
              <Citations items={citations} />
            </article>
          )}
          {error && (
            <p className="work-status" role="alert">
              {error}
            </p>
          )}
          {consumed !== null && (
            <p className="work-status" role="status">
              本次消耗 {consumed} 积分，可在“我的积分”查看记录。
            </p>
          )}
        </div>
        <form
          className="chat-input"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <label className="sr-only" htmlFor="followup">
            发送消息
          </label>
          <textarea
            id="followup"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入教学需求…"
            rows={2}
            disabled={busy}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="chat-input-tools">
            <label className={`soft-button attachment-button${file ? " is-selected" : ""}`}>
              <span aria-hidden="true">📎</span> {file ? "已添加附件" : "附件"}
              <input
                type="file"
                accept={documentFormats}
                className="sr-only"
                disabled={busy}
                onChange={(e) => {
                  setFile(e.target.files?.[0] || null);
                  e.target.value = "";
                }}
              />
            </label>
            <select
              value={skill}
              disabled={busy}
              onChange={(e) => setSkill(e.target.value)}
              aria-label="选择 Skill"
            >
              <option value="" data-description="自由提问，先把思路理清">
                通用助手
              </option>
              {skill &&
                !skills.data?.items.some((item) => item.id === skill) && (
                  <option
                    value={skill}
                    disabled
                    data-description="请重新选择可用的 Skill"
                  >
                    所选 Skill 暂不可用
                  </option>
                )}
              {skills.data?.items.map((s) => (
                <option
                  key={s.id}
                  value={s.id}
                  data-description={s.description}
                >
                  {s.name}
                </option>
              ))}
            </select>
            <button
              className="start-button"
              disabled={
                busy ||
                preferencePending ||
                !!preferenceError ||
                (!input.trim() && !file)
              }
            >
              {busy ? "生成中…" : "发送 ↑"}
            </button>
            {busy && (
              <button
                type="button"
                className="soft-button"
                onClick={() => controller.current?.abort()}
              >
                停止生成
              </button>
            )}
          </div>
          <div className="chat-input-note">
            <QueryState pending={false} error={preferenceError} />
            <label className="knowledge-toggle">
              <input
                type="checkbox"
                checked={useKnowledge}
                disabled={busy || !!file}
                onChange={(e) => setUseKnowledge(e.target.checked)}
              />
              参考学校与个人知识库
            </label>
            {file && (
              <span className="attachment-chip" title={file.name}>
                <span aria-hidden="true">📄</span>
                <span className="attachment-chip-name">{file.name}</span>
                <button
                  type="button"
                  className="attachment-remove"
                  disabled={busy}
                  onClick={() => setFile(null)}
                  aria-label={`移除附件 ${file.name}`}
                >
                  ×
                </button>
              </span>
            )}
            <span>Enter 发送 · Shift + Enter 换行</span>
          </div>
        </form>
      </section>
      {panel && (
        <aside className="chat-detail">
          <div className="history-top">
            <h2>{panel === "edit" ? "内容编辑" : "调用详情"}</h2>
            <button
              className="soft-button"
              onClick={() => setPanel("")}
              aria-label="关闭面板"
            >
              ×
            </button>
          </div>
          {panel === "edit" ? (
            <>
              <p>编辑并保存本次对话的教学草稿。</p>
              <QueryState
                pending={!!selected && savedDraft.isPending}
                error={savedDraft.error || save.error || exportDraft.error}
                retry={() => void savedDraft.refetch()}
              />
              <textarea
                className="editor"
                aria-label="教学内容编辑"
                maxLength={200000}
                value={draft}
                disabled={
                  !selected ||
                  savedDraft.isPending ||
                  !!savedDraft.error ||
                  save.isPending ||
                  restoringDraft
                }
                onChange={(e) => {
                  setDraft(e.target.value);
                  setDraftDirty(true);
                  setNotice("");
                }}
              />
              <button
                className="soft-button"
                disabled={
                  !selected ||
                  save.isPending ||
                  restoringDraft ||
                  savedDraft.isPending ||
                  !!savedDraft.error
                }
                onClick={() =>
                  save.mutate({
                    conversationId: selected,
                    content: draft,
                    revision: draftRevision,
                  })
                }
              >
                保存草稿
              </button>
              {save.error instanceof ApiError &&
                save.error.code === "DRAFT_VERSION_CONFLICT" &&
                savedDraft.data && (
                  <>
                    <p className="knowledge-meta">
                      最新保存稿为 v{savedDraft.data.revision}
                      ，可在历史版本中查看。保存你的修改会保留已有历史。
                    </p>
                    <button
                      className="soft-button"
                      disabled={savedDraft.isFetching || save.isPending}
                      onClick={() =>
                        save.mutate({
                          conversationId: selected,
                          content: draft,
                          revision: savedDraft.data!.revision,
                        })
                      }
                    >
                      将我的修改保存为新版本
                    </button>
                  </>
                )}
              <label className="knowledge-meta">
                导出格式
                <select
                  aria-label="导出格式"
                  value={exportFormat}
                  onChange={(event) => setExportFormat(event.target.value)}
                  disabled={exportDraft.isPending}
                >
                  <option value="docx" data-description="继续编辑与排版">
                    Word 文档
                  </option>
                  <option value="pdf" data-description="便于分享和打印">
                    PDF 文档
                  </option>
                  <option value="md" data-description="保留结构化纯文本">
                    Markdown
                  </option>
                </select>
              </label>
              <button
                className="soft-button"
                disabled={
                  !selected || !draft.trim() || exportDraft.isPending || busy
                }
                onClick={() => exportDraft.mutate()}
              >
                {exportDraft.isPending ? "正在导出…" : "导出文件"}
              </button>
              <button
                className="soft-button"
                disabled={!draft}
                onClick={() => {
                  const url = URL.createObjectURL(
                    new Blob([draft], { type: "text/plain;charset=utf-8" }),
                  );
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "教学草稿.txt";
                  a.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
              >
                导出文本
              </button>
              <p className="knowledge-meta">
                导出当前编辑内容。图片保留说明与链接，公式保留原文。
              </p>
              <p role="status">{notice}</p>
              {selected && (
                <DraftHistory
                  key={selected}
                  conversationId={selected}
                  revision={draftRevision}
                  dirty={draftDirty}
                  busy={save.isPending || restoringDraft}
                  onBusy={setRestoringDraft}
                  onRestored={(value, id) => {
                    teaching.deleteEdit(editKey(id));
                    if (activeConversation.current !== id) return;
                    setDraft(value.content);
                    setDraftRevision(value.revision);
                    setDraftDirty(false);
                    setNotice(`已恢复为 v${value.revision}。`);
                  }}
                />
              )}
            </>
          ) : (
            <div>
              <dl>
                <dt>所选 Skill</dt>
                <dd>
                  {skills.data?.items.find((s) => s.id === skill)?.name ||
                    "通用助手"}
                </dd>
                <dt>知识引用</dt>
                <dd>{knowledgeState || "可引用学校与自己的资料"}</dd>
                <dt>调用状态</dt>
                <dd>{busy ? "正在生成" : error ? "调用未完成" : "等待输入"}</dd>
              </dl>
              <Citations
                items={
                  citations.length
                    ? citations
                    : messageItems.filter((m) => m.role === "assistant").at(-1)
                        ?.citations
                }
              />
              {selected && (
                <DocumentList
                  key={selected}
                  endpoint={`/conversations/${selected}/attachments`}
                />
              )}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
