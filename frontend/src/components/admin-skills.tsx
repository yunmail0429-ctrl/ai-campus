"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, PageResult, Skill } from "@/lib/api";
import { QueryState } from "./query-state";
interface Readiness {
  riskBoundary: string;
  grades: string[];
  tests: { input: string; expectedOutput: string }[];
}
function ReviewCard({ skill }: { skill: Skill }) {
  const client = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [notes, setNotes] = useState("");
  const [version, setVersion] = useState("");
  const readiness = useQuery({
    queryKey: ["review-materials", skill.id],
    queryFn: () => api<Readiness>(`/admin/skills/${skill.id}/readiness`),
    enabled: expanded,
  });
  const versions = useQuery({
    queryKey: ["skill-versions", skill.id],
    queryFn: () =>
      api<{ version: number; disabled: boolean }[]>(
        `/admin/skills/${skill.id}/versions`,
      ),
    enabled: expanded,
  });
  const operation = useMutation({
    mutationFn: (action: string) =>
      api(
        `/admin/skills/${skill.id}/${action}`,
        json(
          "POST",
          action === "rollback"
            ? { version: Number(version) }
            : action === "unpublish"
              ? undefined
              : { notes },
        ),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["admin-skills"] });
      void client.invalidateQueries({ queryKey: ["skills"] });
      void client.invalidateQueries({ queryKey: ["skill-versions", skill.id] });
    },
  });
  const labels: Record<string, string> = {
    pending: "待审核",
    published: "已发布",
    rejected: "已退回",
    archived: "已下架",
    draft: "草稿",
  };
  return (
    <article className="work-panel">
      <div className="section-heading">
        <h2>{skill.name}</h2>
        <span className="skill-state">
          {labels[skill.status] || skill.status}
        </span>
      </div>
      <p>{skill.description}</p>
      <button
        className="soft-button mt-3"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        查看审核资料
      </button>
      {expanded && (
        <div className="review-materials">
          <h3>教学指令</h3>
          <pre>{skill.prompt}</pre>
          <QueryState
            pending={readiness.isPending}
            error={readiness.error || versions.error || operation.error}
            retry={() => {
              void readiness.refetch();
              void versions.refetch();
            }}
          />
          {readiness.data && (
            <>
              <h3>适用年级</h3>
              <p>{readiness.data.grades.join("、") || "尚未填写"}</p>
              <h3>风险边界</h3>
              <p>{readiness.data.riskBoundary || "尚未填写"}</p>
              <h3>测试样例与预期输出</h3>
              {readiness.data.tests.map((t, i) => (
                <details key={i}>
                  <summary>
                    样例 {i + 1}：{t.input}
                  </summary>
                  <p>{t.expectedOutput}</p>
                </details>
              ))}
              {readiness.data.tests.length < 5 && (
                <p className="work-status">
                  不足五个样例，不能发布；请退回创建者补充。
                </p>
              )}
            </>
          )}
          {skill.status === "pending" && (
            <>
              <label>
                审核意见
                <textarea
                  className="editor"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  maxLength={2000}
                />
              </label>
              <div className="skill-actions">
                <button
                  className="start-button"
                  disabled={
                    operation.isPending ||
                    !notes.trim() ||
                    !readiness.data?.riskBoundary ||
                    readiness.data.tests.length < 5
                  }
                  onClick={() => operation.mutate("publish")}
                >
                  审核通过并发布
                </button>
                <button
                  className="soft-button"
                  disabled={operation.isPending || !notes.trim()}
                  onClick={() => operation.mutate("reject")}
                >
                  退回修改
                </button>
              </div>
            </>
          )}
          {skill.status === "published" && (
            <button
              className="soft-button mt-3"
              disabled={operation.isPending}
              onClick={() => {
                if (window.confirm("下架后其他教师将无法调用此 Skill，继续？"))
                  operation.mutate("unpublish");
              }}
            >
              下架 Skill
            </button>
          )}
          {!!versions.data?.length && (
            <div className="skill-actions">
              <label>
                回滚版本
                <select
                  aria-label="回滚版本"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                >
                  <option value="">选择版本</option>
                  {versions.data.map((v) => (
                    <option key={v.version} value={v.version}>
                      {v.disabled ? "禁用基线" : `版本 ${v.version}`}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="soft-button"
                disabled={operation.isPending || version === ""}
                onClick={() => {
                  if (window.confirm("确认切换到所选历史版本？"))
                    operation.mutate("rollback");
                }}
              >
                回滚
              </button>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
export function AdminSkills() {
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const records = useQuery({
    queryKey: ["admin-skills", cursor],
    queryFn: () =>
      api<PageResult<Skill>>(
        `/admin/skills?limit=30${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
  });
  return (
    <section>
      <h2 className="font-semibold mb-5">教学 Skill 审核与共享</h2>
      <QueryState
        pending={records.isPending}
        error={records.error}
        retry={() => void records.refetch()}
      />
      <div className="admin-records">
        {records.data?.items.map((skill) => (
          <ReviewCard key={skill.id} skill={skill} />
        ))}
      </div>
      {records.data?.items.length === 0 && (
        <p className="work-status">暂无已提交的 Skill。</p>
      )}
      <div className="admin-pagination">
        <button
          className="soft-button"
          disabled={!cursors.length}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          上一页
        </button>
        <button
          className="soft-button"
          disabled={!records.data?.nextCursor}
          onClick={() => setCursors([...cursors, records.data!.nextCursor!])}
        >
          下一页
        </button>
      </div>
    </section>
  );
}
