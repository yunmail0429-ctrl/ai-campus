"use client";
import { useState } from "react";
import { api, json } from "@/lib/api";

export interface Citation {
  number?: number;
  documentId?: string;
  chunkId?: string;
  title: string;
  location?: string;
  updatedAt?: string;
  excerpt?: string;
  unavailable?: boolean;
}
export function Citations({ items }: { items?: Citation[] }) {
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  if (!items?.length) return null;
  async function download(id: string) {
    setError("");
    setDownloading(true);
    try {
      const result = await api<{ url: string }>(
        `/documents/${id}/download`,
        json("POST"),
      );
      window.location.assign(result.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "原件暂不可用。");
    } finally {
      setDownloading(false);
    }
  }
  return (
    <section className="message-citations" aria-label="回答参考资料">
      <h4>参考资料</h4>
      {items.map((item, i) =>
        item.unavailable ? (
          <p key={i}>
            [{item.number || i + 1}] {item.title}
          </p>
        ) : (
          <details key={`${item.chunkId || item.documentId}-${i}`}>
            <summary>
              [{item.number || i + 1}] {item.title}{" "}
              {item.location && `· ${item.location}`}
            </summary>
            {item.updatedAt && (
              <p className="knowledge-meta">
                资料更新：{new Date(item.updatedAt).toLocaleString("zh-CN")}
              </p>
            )}
            {item.excerpt && <blockquote>{item.excerpt}</blockquote>}
            {item.documentId && (
              <button
                type="button"
                className="soft-button"
                disabled={downloading}
                onClick={() => void download(item.documentId!)}
              >
                下载来源原件
              </button>
            )}
          </details>
        ),
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
