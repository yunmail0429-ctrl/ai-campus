"use client";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, PageResult } from "@/lib/api";
import { PageHeading } from "./page-heading";
import { QueryState } from "./query-state";
import { Citation, Citations } from "./citations";
import { documentFormats } from "@/lib/document-formats";

interface KnowledgeBase {
  id: string;
  name: string;
  scope: string;
  canManage: boolean;
}
export interface KnowledgeDocument {
  id: string;
  filename: string;
  sizeBytes: number;
  status: string;
  tags: string[];
  chunkCount: number;
  errorCode: string | null;
  errorMessage: string | null;
  updatedAt: string;
  canManage: boolean;
  canRetry: boolean;
}
interface ParsedBlock {
  kind: string;
  text: string;
  source?: string;
  confidence?: number;
  cells?: string[][];
  merges?: { row: number; column: number; rowSpan: number; colSpan: number }[];
  needs_review?: boolean;
}

function TablePreview({ block }: { block: ParsedBlock }) {
  const origins = new Map((block.merges || []).map((merge) => [`${merge.row}:${merge.column}`, merge]));
  const covered = new Set<string>();
  for (const merge of block.merges || []) {
    for (let row = merge.row; row < merge.row + merge.rowSpan; row++) {
      for (let column = merge.column; column < merge.column + merge.colSpan; column++) {
        if (row !== merge.row || column !== merge.column) covered.add(`${row}:${column}`);
      }
    }
  }
  return (
    <table><tbody>{(block.cells || []).map((row, rowIndex) => (
      <tr key={rowIndex}>{row.map((cell, cellIndex) => {
        const key = `${rowIndex}:${cellIndex}`;
        if (covered.has(key)) return null;
        const merge = origins.get(key);
        return <td key={cellIndex} rowSpan={merge?.rowSpan} colSpan={merge?.colSpan}>{cell}</td>;
      })}</tr>
    ))}</tbody></table>
  );
}
interface ParsedPage {
  page: number;
  label: string;
  blocks: ParsedBlock[];
  assembledText: string;
  needsReview: boolean;
}
const statuses: Record<string, string> = {
  uploading: "上传待完成",
  queued: "等待处理",
  scanning: "安全扫描中",
  parsing: "解析中",
  parsed: "解析完成，待建立索引",
  indexing: "正在建立索引",
  index_failed: "索引失败",
  ready: "可用于检索",
  failed: "处理失败",
  quarantined: "已隔离",
};
const pending = new Set([
  "uploading",
  "queued",
  "scanning",
  "parsing",
  "indexing",
  "parsed",
]);

function DocumentCard({ document }: { document: KnowledgeDocument }) {
  const client = useQueryClient();
  const [preview, setPreview] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [tags, setTags] = useState(document.tags.join("，"));
  const [chunkOffsets, setChunkOffsets] = useState<string[]>([]);
  const [pageOffsets, setPageOffsets] = useState<string[]>([]);
  const offset = chunkOffsets.at(-1) ?? "0";
  const pageOffset = pageOffsets.at(-1) ?? "0";
  const pages = useQuery({
    queryKey: ["document-pages", document.id, pageOffset],
    queryFn: () => api<PageResult<ParsedPage>>(
      `/documents/${document.id}/pages?offset=${encodeURIComponent(pageOffset)}`,
    ),
    enabled: preview,
  });
  const chunks = useQuery({
    queryKey: ["document-chunks", document.id, offset],
    queryFn: () =>
      api<
        PageResult<{
          id: string;
          content: string;
          location: string;
          position: number;
        }>
      >(
        `/documents/${document.id}/chunks?offset=${encodeURIComponent(offset)}`,
      ),
    enabled: preview && (pages.data?.items.length === 0 || pages.isError),
  });
  const action = useMutation({
    mutationFn: async (
      kind: "delete" | "retry" | "reindex" | "reparse" | "tags" | "download",
    ) => {
      if (kind === "download") {
        const result = await api<{ url: string }>(
          `/documents/${document.id}/download`,
          json("POST"),
        );
        window.location.assign(result.url);
      } else if (kind === "tags") {
        await api(
          `/documents/${document.id}`,
          json("PATCH", {
            tags: tags
              .split(/[,，]/)
              .map((t) => t.trim())
              .filter(Boolean),
          }),
        );
      } else {
        await api(
          `/documents/${document.id}${kind === "retry" ? "/retry" : kind === "reindex" ? "/reindex" : kind === "reparse" ? "/reparse" : ""}`,
          json(kind === "delete" ? "DELETE" : "POST"),
        );
      }
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ["documents"] }),
  });
  return (
    <article className="knowledge-document">
      <div className="section-heading">
        <strong>{document.filename}</strong>
        <span className="knowledge-status">
          {statuses[document.status] || document.status}
        </span>
      </div>
      <p className="knowledge-meta">
        {Math.max(1, Math.ceil(document.sizeBytes / 1024))} KB ·{" "}
        {document.chunkCount} 个分段 · 更新于{" "}
        {new Date(document.updatedAt).toLocaleString("zh-CN")}
      </p>
      {document.errorMessage && (
        <p className="work-status" role="status">
          {document.errorMessage}
        </p>
      )}
      {document.tags.length > 0 && (
        <p className="knowledge-meta">标签：{document.tags.join("、")}</p>
      )}
      <div className="knowledge-actions">
        {document.canManage &&
          ["parsed", "index_failed"].includes(document.status) && (
            <button
              type="button"
              className="soft-button"
              disabled={action.isPending}
              onClick={() => action.mutate("reindex")}
            >
              建立索引
            </button>
          )}
        {document.chunkCount > 0 && (
          <button
            type="button"
            className="soft-button"
            onClick={() => setPreview(!preview)}
          >
            {preview ? "收起预览" : "查看解析内容"}
          </button>
        )}
        {["parsed", "ready"].includes(document.status) && (
          <button
            type="button"
            className="soft-button"
            disabled={action.isPending}
            onClick={() => action.mutate("download")}
          >
            下载原件
          </button>
        )}
        {document.canRetry && (
          <button
            type="button"
            className="soft-button"
            disabled={action.isPending}
            onClick={() => action.mutate("retry")}
          >
            重试处理
          </button>
        )}
        {document.canManage && ["parsed", "ready"].includes(document.status) && (
          <button type="button" className="soft-button" disabled={action.isPending} onClick={() => action.mutate("reparse")}>重新解析原件</button>
        )}
        {document.canManage && (
          <button
            type="button"
            className="soft-button"
            disabled={action.isPending}
            onClick={() => setConfirmDelete(!confirmDelete)}
          >
            删除资料
          </button>
        )}
      </div>
      {confirmDelete && (
        <div className="work-status">
          <p>删除后，这份资料将不再可用。</p>
          <button
            type="button"
            className="soft-button"
            disabled={action.isPending}
            onClick={() => action.mutate("delete")}
          >
            确认删除
          </button>
          <button
            type="button"
            className="soft-button"
            onClick={() => setConfirmDelete(false)}
          >
            取消
          </button>
        </div>
      )}
      {document.canManage && (
        <details className="knowledge-tags">
          <summary>编辑标签</summary>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action.mutate("tags");
            }}
          >
            <label>
              资料标签
              <input
                maxLength={309}
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="用逗号分隔，最多 10 个"
              />
            </label>
            <button className="soft-button" disabled={action.isPending}>
              保存标签
            </button>
          </form>
        </details>
      )}
      <QueryState pending={false} error={action.error} />
      {preview && (
        <section
          aria-label={`${document.filename}解析预览`}
          className="knowledge-preview"
        >
          <QueryState
            pending={pages.isPending || (pages.data?.items.length === 0 && chunks.isPending)}
            error={pages.data?.items.length === 0 ? chunks.error : pages.error}
            retry={() => void pages.refetch()}
          />
          {pages.data?.items.map((page) => (
            <div key={page.page} className="knowledge-preview-page">
              <strong>{page.label}{page.needsReview ? " · 部分内容待核对" : ""}</strong>
              {!page.blocks.some((block) => block.text?.trim() || block.cells?.some((row) => row.some((cell) => cell.trim()))) && (
                <p className="knowledge-preview-empty">此页未提取到文字，请对照原件确认是否为空白页。</p>
              )}
              {page.blocks.map((block, index) => (
                <div key={index}>
                  {block.kind === "table" && block.cells?.length ? (
                    <TablePreview block={block} />
                  ) : block.text ? (
                    <p>{block.text}</p>
                  ) : null}
                  {block.needs_review && <small>此区域待核对</small>}
                </div>
              ))}
            </div>
          ))}
          {pages.data?.items.length === 0 && chunks.data?.items.map((chunk) => (
            <div key={chunk.id}>
              <strong>
                {chunk.location} · 分段 {chunk.position + 1}
              </strong>
              <p>{chunk.content}</p>
            </div>
          ))}
          {pages.data?.items.length === 0 && chunks.data && (
            <p className="knowledge-meta">
              本页 {chunks.data.items.length} 个分段 · 全文{" "}
              {document.chunkCount} 个分段
            </p>
          )}
          <div className="knowledge-actions">
            {pages.data?.items.length ? pageOffsets.length > 0 && (
              <button type="button" className="soft-button" disabled={pages.isFetching} onClick={() => setPageOffsets((values) => values.slice(0, -1))}>上一页</button>
            ) : chunkOffsets.length > 0 && (
              <button
                type="button"
                className="soft-button"
                disabled={chunks.isFetching}
                onClick={() => setChunkOffsets((values) => values.slice(0, -1))}
              >
                上一页分段
              </button>
            )}
            {pages.data?.items.length ? pages.data.nextCursor && (
              <button type="button" className="soft-button" disabled={pages.isFetching} onClick={() => setPageOffsets((values) => [...values, pages.data!.nextCursor!])}>下一页</button>
            ) : chunks.data?.nextCursor && (
              <button
                type="button"
                className="soft-button"
                disabled={chunks.isFetching}
                onClick={() =>
                  setChunkOffsets((values) => [
                    ...values,
                    chunks.data!.nextCursor!,
                  ])
                }
              >
                下一页分段
              </button>
            )}
          </div>
        </section>
      )}
    </article>
  );
}

export function DocumentList({ endpoint }: { endpoint: string }) {
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const documents = useQuery({
    queryKey: ["documents", endpoint, cursor],
    queryFn: () =>
      api<PageResult<KnowledgeDocument>>(
        `${endpoint}?limit=20${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
    refetchInterval: (q) =>
      q.state.data?.items.some((d) => pending.has(d.status)) ? 3000 : false,
  });
  return (
    <div>
      <div className="section-heading">
        <h3>资料列表</h3>
        <button
          type="button"
          className="soft-button"
          disabled={documents.isFetching}
          onClick={() => void documents.refetch()}
        >
          刷新资料
        </button>
      </div>
      <QueryState
        pending={documents.isPending}
        error={documents.error}
        retry={() => void documents.refetch()}
      />
      {documents.data?.items.length === 0 && (
        <p className="work-status">暂无资料，上传教学文件后会显示在这里。</p>
      )}
      {documents.data?.items.map((document) => (
        <DocumentCard key={document.id} document={document} />
      ))}
      {(cursors.length > 0 || documents.data?.nextCursor) && (
        <div className="admin-pagination">
          <button
            className="soft-button"
            disabled={!cursors.length}
            onClick={() => setCursors(cursors.slice(0, -1))}
          >
            上一页
          </button>
          <span>第 {cursors.length + 1} 页</span>
          <button
            className="soft-button"
            disabled={!documents.data?.nextCursor}
            onClick={() =>
              setCursors([...cursors, documents.data!.nextCursor!])
            }
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}

export function Knowledge({ admin = false }: { admin?: boolean }) {
  const client = useQueryClient();
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const operation = useRef<{ file: File; base: string; key: string } | null>(
    null,
  );
  const [baseCursors, setBaseCursors] = useState<string[]>([]);
  const baseCursor = baseCursors.at(-1);
  const bases = useQuery({
    queryKey: ["knowledge-bases", baseCursor],
    queryFn: () =>
      api<PageResult<KnowledgeBase>>(
        `/knowledge-bases?limit=50${baseCursor ? "&cursor=" + encodeURIComponent(baseCursor) : ""}`,
      ),
  });
  const current = bases.data?.items.find((b) => b.id === selected);
  const create = useMutation({
    mutationFn: () =>
      api<KnowledgeBase>(
        "/knowledge-bases",
        json("POST", { name, scope: admin ? "school" : "private" }),
      ),
    onSuccess: (result) => {
      setName("");
      setSelected(result.id);
      setBaseCursors([]);
      void client.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
  });
  const upload = useMutation({
    mutationFn: async () => {
      if (!file || !selected) throw new Error("请先选择知识库和文件。");
      if (
        operation.current?.file !== file ||
        operation.current.base !== selected
      )
        operation.current = { file, base: selected, key: crypto.randomUUID() };
      const form = new FormData();
      form.append("file", file);
      return api<KnowledgeDocument>(`/knowledge-bases/${selected}/documents`, {
        method: "POST",
        body: form,
        headers: { "Idempotency-Key": operation.current.key },
      });
    },
    onSuccess: () => {
      operation.current = null;
      setFile(null);
      if (input.current) input.current.value = "";
      setNotice("上传已保存，正在等待安全扫描和解析。");
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: () => void client.invalidateQueries({ queryKey: ["documents"] }),
  });
  return (
    <div className={admin ? "" : "workspace-wrap"}>
      {!admin && <PageHeading title="知识库" />}
      <section className="work-panel knowledge-panel">
        <h1>{admin ? "学校知识库" : "我的知识库"}</h1>
        <p className="knowledge-intro">
          {admin
            ? "管理全校共享的教学资料。教师个人库保持私有。"
            : "个人资料仅自己可见，也可查阅学校共享的教学资料。"}
        </p>
        <form
          className="knowledge-create"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <label>
            {admin ? "学校知识库名称" : "个人知识库名称"}
            <input
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：高一语文备课资料"
            />
          </label>
          <button
            className="soft-button"
            disabled={create.isPending || !name.trim()}
          >
            新建知识库
          </button>
        </form>
        <QueryState
          pending={bases.isPending}
          error={bases.error || create.error}
          retry={() => void bases.refetch()}
        />
        {bases.data?.items.length === 0 && (
          <p className="work-status">还没有知识库，可以先创建一个。</p>
        )}
        {(bases.data?.items.length ?? 0) > 0 && (
          <div className="knowledge-library-heading">
            <h2>已有知识库</h2>
            <span>选择一个知识库，查看和管理其中的资料</span>
          </div>
        )}
        <div className="knowledge-bases" aria-label="已有知识库">
          {bases.data?.items.map((base) => (
            <button
              type="button"
              className={`knowledge-base ${selected === base.id ? "is-selected" : ""}`}
              aria-pressed={selected === base.id}
              key={base.id}
              disabled={upload.isPending}
              onClick={() => {
                setSelected(base.id);
                setNotice("");
              }}
            >
              <strong>{base.name}</strong>
              <span>{base.scope === "school" ? "学校共享" : "仅自己可见"}</span>
              {selected === base.id && <small>当前查看</small>}
            </button>
          ))}
        </div>
        {(baseCursors.length > 0 || bases.data?.nextCursor) && (
          <div className="admin-pagination">
            <button
              className="soft-button"
              disabled={!baseCursors.length || upload.isPending}
              onClick={() => {
                setSelected("");
                setBaseCursors(baseCursors.slice(0, -1));
              }}
            >
              上一页知识库
            </button>
            <button
              className="soft-button"
              disabled={!bases.data?.nextCursor || upload.isPending}
              onClick={() => {
                setSelected("");
                setBaseCursors([...baseCursors, bases.data!.nextCursor!]);
              }}
            >
              下一页知识库
            </button>
          </div>
        )}
        {current && (
          <section className="knowledge-selected" aria-label="当前知识库">
            <div className="knowledge-selected-heading">
              <div>
                <span className="knowledge-section-label">当前知识库</span>
                <h2>{current.name}</h2>
              </div>
              <span className="knowledge-scope">{current.scope === "school" ? "学校共享" : "仅自己可见"}</span>
            </div>
            {current.canManage && (
              <form
                className="knowledge-upload"
                onSubmit={(e) => {
                  e.preventDefault();
                  setNotice("");
                  upload.mutate();
                }}
              >
                <label>
                  上传教学资料
                  <input
                    ref={input}
                    type="file"
                    accept={documentFormats}
                    disabled={upload.isPending}
                    onChange={(e) => {
                      setFile(e.target.files?.[0] || null);
                      setNotice("");
                      upload.reset();
                    }}
                  />
                </label>
                <p className="knowledge-meta">
                  PDF、Word（docx）、PPT（pptx）、文本、HTML
                  和图片。图片与扫描件需 OCR 服务。默认单个文件不超过 20 MB。
                </p>
                <button
                  className="soft-button"
                  disabled={!file || upload.isPending}
                >
                  {upload.isPending ? "正在上传…" : "上传资料"}
                </button>
              </form>
            )}
            {notice && (
              <p role="status" className="work-status">
                {notice}
              </p>
            )}
            <QueryState pending={false} error={upload.error} />
            <DocumentList
              key={current.id}
              endpoint={`/knowledge-bases/${current.id}/documents`}
            />
            <KnowledgeSearch key={`search-${current.id}`} baseId={current.id} />
          </section>
        )}
      </section>
    </div>
  );
}

function KnowledgeSearch({ baseId }: { baseId: string }) {
  const [query, setQuery] = useState("");
  const search = useMutation({
    mutationFn: () =>
      api<{ items: Citation[]; status: string }>(
        "/knowledge/search",
        json("POST", { query, knowledgeBaseIds: [baseId] }),
      ),
  });
  return (
    <section className="knowledge-search" aria-label="查找资料内容">
      <h3>查找资料内容</h3>
      <form
        className="knowledge-create"
        onSubmit={(e) => {
          e.preventDefault();
          search.mutate();
        }}
      >
        <label>
          教学问题
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            required
            maxLength={4000}
            placeholder="例如：函数单调性的课堂活动"
          />
        </label>
        <button
          className="soft-button"
          disabled={search.isPending || !query.trim()}
        >
          {search.isPending ? "正在查找…" : "查找相关段落"}
        </button>
      </form>
      <QueryState pending={false} error={search.error} />
      {search.data && !search.data.items.length && (
        <p className="work-status">
          {search.data.status === "not_configured"
            ? "学校尚未启用向量检索服务。"
            : "暂无相关段落，请检查资料是否完成索引，或换个问题。"}
        </p>
      )}
      <Citations items={search.data?.items} />
    </section>
  );
}
