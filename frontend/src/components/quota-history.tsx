"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, PageResult } from "@/lib/api";
import { QueryState } from "./query-state";

interface Transaction {
  id: string;
  kind: "adjustment" | "reserve" | "settle" | "release";
  source: string;
  balanceDelta: number;
  reservedDelta: number;
  balanceAfter: number;
  reservedAfter: number;
  reason: string;
  createdAt: string;
}
const number = (value: number) => value.toLocaleString("zh-CN");

export function QuotaHistory({
  endpoint = "/me/quota/transactions",
}: {
  endpoint?: string;
}) {
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const history = useQuery({
    queryKey: ["quota-transactions", endpoint, cursor],
    queryFn: () =>
      api<PageResult<Transaction>>(
        `${endpoint}?limit=20${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
  });
  return (
    <section className="quota-history" aria-label="积分记录">
      <div className="section-heading">
        <h3>积分记录</h3>
        <button
          type="button"
          className="soft-button"
          disabled={history.isFetching}
          onClick={() => void history.refetch()}
        >
          刷新记录
        </button>
      </div>
      <QueryState
        pending={history.isPending}
        error={history.error}
        retry={() => void history.refetch()}
      />
      {history.data?.items.length === 0 && (
        <p className="work-status">
          暂无积分记录。学校分配和实际使用后，会在这里留下明细。
        </p>
      )}
      <div className="quota-records">
        {history.data?.items.map((row) => (
          <article key={row.id} className="quota-record">
            <div className="section-heading">
              <strong>{row.source}</strong>
              <span className={row.balanceDelta > 0 ? "quota-increase" : ""}>
                {row.kind === "reserve"
                  ? `预占 ${number(row.reservedDelta)}`
                  : row.kind === "release"
                    ? `释放 ${number(-row.reservedDelta)}`
                    : `${row.balanceDelta > 0 ? "+" : ""}${number(row.balanceDelta)}`}{" "}
                积分
              </span>
            </div>
            <p>{row.reason}</p>
            <time dateTime={row.createdAt}>
              {new Date(row.createdAt).toLocaleString("zh-CN")}
            </time>
            <p className="quota-record-meta">
              变更后余额 {number(row.balanceAfter)} · 可用{" "}
              {number(row.balanceAfter - row.reservedAfter)} · 预占{" "}
              {number(row.reservedAfter)}
            </p>
            <details>
              <summary>记录编号</summary>
              <code>{row.id}</code>
            </details>
          </article>
        ))}
      </div>
      <div className="admin-pagination">
        <button
          type="button"
          className="soft-button"
          disabled={!cursors.length || history.isFetching}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          上一页
        </button>
        <span>第 {cursors.length + 1} 页</span>
        <button
          type="button"
          className="soft-button"
          disabled={!history.data?.nextCursor || history.isFetching}
          onClick={() => setCursors([...cursors, history.data!.nextCursor!])}
        >
          下一页
        </button>
      </div>
    </section>
  );
}

interface PersonalQuota {
  balance: number;
  reserved: number;
  available: number;
  frozen: boolean;
  totalAllocated: number;
  totalReclaimed: number;
  totalConsumed: number;
}

export function PersonalCredits() {
  const quota = useQuery({
    queryKey: ["quota"],
    queryFn: () => api<PersonalQuota>("/me/quota"),
  });
  return (
    <section aria-label="我的积分总览">
      <div className="section-heading">
        <h3>我的积分</h3>
        <button
          type="button"
          className="soft-button"
          disabled={quota.isFetching}
          onClick={() => void quota.refetch()}
        >
          刷新余额
        </button>
      </div>
      <QueryState
        pending={quota.isPending}
        error={quota.error}
        retry={() => void quota.refetch()}
      />
      {quota.data && (
        <>
          {quota.data.frozen && (
            <p className="work-status" role="status">
              积分账户已冻结，暂时不能发起新的 AI 任务，请联系学校管理员。
            </p>
          )}
          {!quota.data.frozen && quota.data.available === 0 && (
            <p className="work-status">
              可用积分已用完，请联系学校管理员分配。
            </p>
          )}
          <dl className="quota-stats">
            {[
              ["可用积分", quota.data.available],
              ["任务预占", quota.data.reserved],
              ["当前余额", quota.data.balance],
              ["累计获得", quota.data.totalAllocated],
              ["历史总消耗", quota.data.totalConsumed],
              ["学校累计收回", quota.data.totalReclaimed],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{number(value as number)}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
      <p className="quota-explanation">
        积分由学校管理员分配。任务开始时预占，完成后按实际用量结算；取消或失败时释放预占。预占不计入历史总消耗。
      </p>
      <QuotaHistory />
    </section>
  );
}
