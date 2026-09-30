"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { QueryState } from "./query-state";

interface Totals {
  calls: number;
  completed: number;
  failed: number;
  cancelled: number;
  running: number;
  skillCalls: number;
  inputTokens: number;
  outputTokens: number;
  consumedCredits: number;
  embeddingCalls: number;
  embeddingTokens: number;
  embeddingUncertain: number;
  feedbackCount: number;
  resourceOutputs: number;
}
interface Report {
  start: string;
  end: string;
  resourceOutputs: Record<string, number>;
  summary: Totals & {
    activeAccounts: number;
    successRate: number | null;
    feedbackAverage: number | null;
  };
  daily: (Totals & { date: string; logins: number })[];
}

export function AdminStatistics() {
  const [range, setRange] = useState("");
  const query = useQuery({
    queryKey: ["admin-statistics", range],
    queryFn: () => api<Report>(`/admin/statistics${range}`),
  });
  const data = query.data;
  return (
    <section className="work-panel" aria-label="学校使用统计">
      <h2>学校使用看板</h2>
      <p className="knowledge-intro">
        先看老师使用情况和产出，再查看每日明细。默认展示近 30 天，日期按北京时间计算。
      </p>
      <form
        key={`${data?.start}-${data?.end}`}
        className="statistics-filters"
        onSubmit={(event) => {
          event.preventDefault();
          const values = new FormData(event.currentTarget);
          setRange(
            `?${new URLSearchParams({ start: String(values.get("start")), end: String(values.get("end")) })}`,
          );
        }}
      >
        <label>
          开始日期
          <input type="date" name="start" required defaultValue={data?.start} />
        </label>
        <label>
          结束日期
          <input type="date" name="end" required defaultValue={data?.end} />
        </label>
        <button className="soft-button" disabled={query.isFetching}>
          查询统计
        </button>
        {data && (
          <a
            className="soft-button"
            href={`/api/v1/admin/statistics/export?start=${data.start}&end=${data.end}`}
            download
          >
            导出汇总 CSV
          </a>
        )}
      </form>
      <QueryState
        pending={query.isPending}
        error={query.error}
        retry={() => void query.refetch()}
      />
      {data && (
        <>
          <div className="statistics-cards">
            {[
              ["登录活跃账号", data.summary.activeAccounts],
              ["完成的教学任务", data.summary.completed],
              ["实际消耗积分", data.summary.consumedCredits],
              ["创作产出", data.summary.resourceOutputs],
            ].map(([label, value]) => (
              <article key={label}>
                <span>{label}</span>
                <strong>
                  {typeof value === "number"
                    ? value.toLocaleString("zh-CN")
                    : value}
                </strong>
              </article>
            ))}
          </div>
          <div className="school-chart" aria-label="每日 AI 使用趋势">
            <div className="section-heading">
              <div>
                <h3>每日使用趋势</h3>
                <p>柱形表示当天发起的 AI 调用次数。</p>
              </div>
            </div>
            {data.daily.length ? (
              <div className="school-chart-bars">
                {data.daily.slice(-14).map((day) => {
                  const max = Math.max(1, ...data.daily.slice(-14).map((item) => item.calls));
                  return (
                    <div className="school-chart-day" key={day.date}
                      title={`${day.date}：${day.calls} 次调用，完成 ${day.completed} 次`}>
                      <strong>{day.calls}</strong>
                      <span className="school-chart-track">
                        <span style={{ height: `${day.calls ? Math.max(8, day.calls / max * 100) : 0}%` }} />
                      </span>
                      <small>{day.date.slice(5)}</small>
                    </div>
                  );
                })}
              </div>
            ) : <p className="work-status">所选时间段还没有使用记录。</p>}
          </div>
          <details className="statistics-more">
            <summary>查看更多统计指标</summary>
            <div className="statistics-cards">
              {[
              ["模型调用", data.summary.calls],
              ["Skill 调用", data.summary.skillCalls],
              ["确认输入 Token", data.summary.inputTokens],
              ["确认输出 Token", data.summary.outputTokens],
              [
                "调用成功率",
                data.summary.successRate === null
                  ? "暂无已结束调用"
                  : `${(data.summary.successRate * 100).toFixed(1)}%`,
              ],
              ["回答评价数量", data.summary.feedbackCount],
              [
                "回答平均评分",
                data.summary.feedbackAverage == null
                  ? "暂无评价"
                  : `${data.summary.feedbackAverage.toFixed(1)} / 5`,
              ],
            ].map(([label, value]) => (
              <article key={label}>
                <span>{label}</span>
                <strong>
                  {typeof value === "number"
                    ? value.toLocaleString("zh-CN")
                    : value}
                </strong>
              </article>
            ))}
            </div>
          </details>
          <p className="knowledge-meta">
            完成 {data.summary.completed} · 失败 {data.summary.failed} · 取消{" "}
            {data.summary.cancelled} · 进行中 {data.summary.running}
          </p>
          <p className="knowledge-intro">
            调用和 Token
            按请求发起日统计；积分按实际结算日统计，不包含预占、释放或管理员收回。Token
            仅包含已完成调用的有效计量，积分不是供应商货币费用。
          </p>
          <h3>学校知识库用量</h3>
          <p className="knowledge-meta">
            嵌入调用 {data.summary.embeddingCalls} 次 · 确认输入{" "}
            {data.summary.embeddingTokens.toLocaleString("zh-CN")} Token ·
            计量未确认 {data.summary.embeddingUncertain} 次
          </p>
          <p className="knowledge-intro">
            入库与检索不扣教师积分。未确认计量不代表外部服务没有产生费用。
          </p>
          <h3>创作产出</h3>
          <p className="knowledge-meta">
            {([
              ["image", "图片"], ["ppt", "PPT"], ["video", "视频"],
              ["music", "音乐"], ["avatar", "数字人"], ["3d", "3D"],
            ] as const).map(([kind, label]) => `${label} ${data.resourceOutputs[kind] ?? 0}`).join(" · ")}
          </p>
          <p className="knowledge-intro">
            按成功完成日统计本系统记录的产出，失败和取消不计入；同一任务只计一次。
            统计数量不表示相应生成服务已经配置。
          </p>
          <h3>每日明细</h3>
          <div
            className="statistics-table"
            tabIndex={0}
            role="region"
            aria-label="每日使用明细"
          >
            <table>
              <thead>
                <tr>
                  {[
                    "日期",
                    "登录人数",
                    "调用",
                    "完成",
                    "失败/取消",
                    "Skill",
                    "输入 Token",
                    "输出 Token",
                    "消耗积分",
                    "资源产出",
                  ].map((label) => (
                    <th key={label} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.daily.map((row) => (
                  <tr key={row.date}>
                    <th scope="row">{row.date}</th>
                    {[
                      row.logins,
                      row.calls,
                      row.completed,
                      row.failed + row.cancelled,
                      row.skillCalls,
                      row.inputTokens,
                      row.outputTokens,
                      row.consumedCredits,
                      row.resourceOutputs,
                    ].map((value, index) => (
                      <td key={index}>{value.toLocaleString("zh-CN")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="knowledge-meta">
            导出包含每日汇总，不含教师姓名、账号、提问、回答或资料原文。
          </p>
        </>
      )}
    </section>
  );
}
