"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, PageResult } from "@/lib/api";
import { QueryState } from "./query-state";

export interface DraftSnapshot {
  content: string;
  revision: number;
}
interface Version {
  id: string;
  number: number;
  createdAt: string;
  restoredFrom: number | null;
}

export function DraftHistory({
  conversationId,
  revision,
  dirty,
  busy,
  onBusy,
  onRestored,
}: {
  conversationId: string;
  revision: number;
  dirty: boolean;
  busy: boolean;
  onBusy: (value: boolean) => void;
  onRestored: (value: DraftSnapshot, conversationId: string) => void;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [cursors, setCursors] = useState<string[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const cursor = cursors.at(-1);
  const path = `/conversations/${conversationId}/draft/versions`;
  const versions = useQuery({
    queryKey: ["draft-versions", conversationId, cursor],
    queryFn: () =>
      api<PageResult<Version>>(
        `${path}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
      ),
    enabled: open,
  });
  const preview = useQuery({
    queryKey: ["draft-version", conversationId, selected],
    queryFn: () => api<DraftSnapshot>(`${path}/${selected}`),
    enabled: open && selected !== null,
  });
  const restore = useMutation({
    mutationFn: () =>
      api<DraftSnapshot>(
        `${path}/${selected}/restore`,
        json("POST", { revision }),
      ),
    onSuccess: (value) => {
      client.setQueryData(["draft", conversationId], value);
      void client.invalidateQueries({
        queryKey: ["draft-versions", conversationId],
      });
      onRestored(value, conversationId);
    },
    onError: () => {
      void client.invalidateQueries({ queryKey: ["draft", conversationId] });
      void client.invalidateQueries({
        queryKey: ["draft-versions", conversationId],
      });
    },
    onSettled: () => onBusy(false),
  });
  return (
    <details
      className="draft-history"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>历史版本 · 当前 v{revision}</summary>
      {open && (
        <>
          <QueryState
            pending={versions.isPending}
            error={versions.error || restore.error}
            retry={() => void versions.refetch()}
          />
          {versions.data?.items.length === 0 && (
            <p className="knowledge-meta">保存草稿后会生成历史版本。</p>
          )}
          {versions.data?.items.map((version) => (
            <p key={version.id}>
              <button
                className="soft-button"
                disabled={restore.isPending}
                onClick={() => setSelected(version.number)}
              >
                查看 v{version.number}
              </button>{" "}
              <small>
                {new Date(version.createdAt).toLocaleString("zh-CN", {
                  timeZone: "Asia/Shanghai",
                })}
                {version.restoredFrom !== null &&
                  ` · 恢复自 v${version.restoredFrom}`}
              </small>
            </p>
          ))}
          {cursor && (
            <button
              className="soft-button"
              onClick={() => setCursors((values) => values.slice(0, -1))}
            >
              上一页版本
            </button>
          )}
          {versions.data?.nextCursor && (
            <button
              className="soft-button"
              onClick={() =>
                setCursors((values) => [...values, versions.data!.nextCursor!])
              }
            >
              更早版本
            </button>
          )}
          {selected !== null && (
            <>
              <QueryState
                pending={preview.isPending}
                error={preview.error}
                retry={() => void preview.refetch()}
              />
              {preview.data && (
                <>
                  <p className="knowledge-meta">v{selected} 内容预览</p>
                  <textarea
                    className="editor"
                    aria-label="历史版本内容"
                    readOnly
                    value={preview.data.content}
                  />
                  <button
                    className="soft-button"
                    disabled={
                      dirty ||
                      busy ||
                      restore.isPending ||
                      selected === revision
                    }
                    onClick={() => {
                      onBusy(true);
                      restore.mutate();
                    }}
                  >
                    {restore.isPending ? "正在恢复…" : "恢复为新版本"}
                  </button>
                  <p className="knowledge-meta">
                    {dirty
                      ? "请先保存当前修改，再恢复历史版本。"
                      : "恢复会保存为新版本，现有历史保留。"}
                  </p>
                </>
              )}
            </>
          )}
        </>
      )}
    </details>
  );
}
