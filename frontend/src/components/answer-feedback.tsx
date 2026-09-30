"use client";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, json } from "@/lib/api";
import { QueryState } from "./query-state";

export interface Feedback {
  rating: number;
  comment: string;
  updatedAt: string;
}

export function AnswerFeedback({
  conversation,
  message,
  feedback,
}: {
  conversation: string;
  message: string;
  feedback?: Feedback | null;
}) {
  const cache = useQueryClient();
  const [notice, setNotice] = useState("");
  const save = useMutation({
    mutationFn: (data: { rating: number | null; comment?: string }) =>
      api<Feedback | null>(
        `/conversations/${conversation}/messages/${message}/feedback`,
        json("PUT", data),
      ),
    onSuccess: (data) => {
      setNotice(data ? "评价已保存，可随时修改。" : "评价已撤回。");
      void cache.invalidateQueries({ queryKey: ["messages", conversation] });
    },
  });
  return (
    <details className="answer-feedback">
      <summary>
        {feedback ? `我的评价：${feedback.rating} 分` : "评价这条回答"}
      </summary>
      <p className="knowledge-meta">
        1 分表示不满意，5 分表示很满意。学校统计只展示汇总评分，不导出说明原文。
      </p>
      <form
        key={feedback?.updatedAt || "new"}
        onSubmit={(event) => {
          event.preventDefault();
          setNotice("");
          const data = new FormData(event.currentTarget);
          save.mutate({
            rating: Number(data.get("rating")),
            comment: String(data.get("comment")),
          });
        }}
      >
        <label>
          回答评分
          <select name="rating" required defaultValue={feedback?.rating || ""}>
            <option value="" disabled>
              请选择
            </option>
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                {value} 分
              </option>
            ))}
          </select>
        </label>
        <label>
          反馈说明（选填）
          <textarea
            name="comment"
            maxLength={1000}
            rows={3}
            defaultValue={feedback?.comment || ""}
            placeholder="哪些内容有帮助，哪些需要改进？"
          />
        </label>
        <div className="knowledge-actions">
          <button className="soft-button" disabled={save.isPending}>
            保存评价
          </button>
          {feedback && (
            <button
              type="button"
              className="soft-button"
              disabled={save.isPending}
              onClick={() => {
                setNotice("");
                save.mutate({ rating: null });
              }}
            >
              撤回评价
            </button>
          )}
        </div>
      </form>
      <QueryState pending={false} error={save.error} />
      {notice && (
        <p role="status" className="work-status">
          {notice}
        </p>
      )}
    </details>
  );
}
