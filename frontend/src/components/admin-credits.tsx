"use client";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, PageResult } from "@/lib/api";
import { QueryState } from "./query-state";
import { QuotaHistory } from "./quota-history";

interface Account {
  id: string;
  name: string;
  username: string;
  active: boolean;
  balance: number;
  available: number;
  reserved: number;
  frozen: boolean;
}
interface Summary {
  balance: number;
  reserved: number;
  available: number;
  consumed: number;
  accounts: number;
}
interface RecentTransaction {
  id: string;
  teacherName: string;
  teacherUsername: string;
  source: string;
  kind: string;
  balanceDelta: number;
  reservedDelta: number;
  reason: string;
  createdAt: string;
}
interface Operation {
  account: Account;
  mode: "grant" | "reclaim" | "state";
  key: string;
}

export function AdminCredits() {
  const cache = useQueryClient();
  const editDialog = useRef<HTMLDialogElement>(null);
  const historyDialog = useRef<HTMLDialogElement>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [history, setHistory] = useState<Account | null>(null);
  const [search, setSearch] = useState("");
  const [cursors, setCursors] = useState<string[]>([]);
  const [transactionCursors, setTransactionCursors] = useState<string[]>([]);
  const transactionCursor = transactionCursors.at(-1);
  const cursor = cursors.at(-1);
  const accounts = useQuery({
    queryKey: ["admin-quota", "accounts", search, cursor],
    queryFn: () =>
      api<PageResult<Account>>(
        `/admin/quota/accounts?limit=20&q=${encodeURIComponent(search)}${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
  });
  const summary = useQuery({
    queryKey: ["admin-quota", "summary"],
    queryFn: () => api<Summary>("/admin/quota/summary"),
  });
  const transactions = useQuery({
    queryKey: ["admin-quota", "transactions", transactionCursor],
    queryFn: () => api<PageResult<RecentTransaction>>(
      `/admin/quota/transactions?limit=12${transactionCursor ? "&cursor=" + encodeURIComponent(transactionCursor) : ""}`,
    ),
  });
  const save = useMutation({
    mutationFn: ({
      op,
      amount,
      reason,
    }: {
      op: Operation;
      amount: number;
      reason: string;
    }) => {
      const path = `/admin/quota/accounts/${op.account.id}`;
      if (op.mode === "state")
        return api(path, json("PATCH", { frozen: !op.account.frozen, reason }));
      return api(`${path}/adjustments`, {
        ...json("POST", {
          amount: op.mode === "grant" ? amount : -amount,
          reason,
        }),
        headers: { "Idempotency-Key": op.key },
      });
    },
    onSuccess: () => {
      editDialog.current?.close();
      for (const key of ["admin-quota", "quota", "quota-transactions", "audit"])
        void cache.invalidateQueries({ queryKey: [key] });
    },
  });
  function open(account: Account, mode: Operation["mode"]) {
    save.reset();
    setOperation({ account, mode, key: crypto.randomUUID() });
    editDialog.current?.showModal();
  }
  const title = !operation
    ? "调整积分"
    : operation.mode === "grant"
      ? "分配积分"
      : operation.mode === "reclaim"
        ? "收回积分"
        : operation.account.frozen
          ? "恢复积分账户"
          : "冻结积分账户";
  return (
    <section>
      <h2 className="font-semibold mb-5">学校积分管理</h2>
      <p>学校管理员直接为教师分配积分，所有变更均保留原因和审计记录。</p>
      <QueryState
        pending={summary.isPending}
        error={summary.error}
        retry={() => void summary.refetch()}
      />
      {summary.data && (
        <dl className="quota-stats">
          {[
            ["全校可用积分", summary.data.available],
            ["任务预占", summary.data.reserved],
            ["历史总消耗", summary.data.consumed],
            ["账户数量", summary.data.accounts],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{(value as number).toLocaleString("zh-CN")}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="section-heading quota-activity-heading">
        <div>
          <h3>最近积分变化</h3>
          <p>先看谁用了积分、为什么变化；需要调整时再查找教师。</p>
        </div>
      </div>
      <QueryState pending={transactions.isPending} error={transactions.error}
        retry={() => void transactions.refetch()} />
      {transactions.data?.items.length === 0 && (
        <p className="work-status">目前没有积分变化记录。</p>
      )}
      {!!transactions.data?.items.length && (
        <div className="quota-activity-table" role="region" aria-label="最近积分变化明细" tabIndex={0}>
          <table>
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">教师</th>
                <th scope="col">变化类型</th>
                <th scope="col">积分变动</th>
                <th scope="col">事项说明</th>
              </tr>
            </thead>
            <tbody>
              {transactions.data.items.map((row) => (
                <tr key={row.id}>
                  <td><time dateTime={row.createdAt}>{new Date(row.createdAt).toLocaleString("zh-CN")}</time></td>
                  <td><strong>{row.teacherName}</strong><small>{row.teacherUsername}</small></td>
                  <td><span className="quota-kind">{row.source}</span></td>
                  <td className={row.balanceDelta > 0 ? "quota-plus" : row.balanceDelta < 0 ? "quota-minus" : ""}>
                    {row.balanceDelta === 0
                      ? row.kind === "reserve" ? `预占 ${row.reservedDelta}` : `释放 ${-row.reservedDelta}`
                      : `${row.balanceDelta > 0 ? "+" : ""}${row.balanceDelta.toLocaleString("zh-CN")}`}
                  </td>
                  <td>{row.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="admin-pagination">
        <button type="button" className="soft-button"
          disabled={!transactionCursors.length || transactions.isFetching}
          onClick={() => setTransactionCursors(transactionCursors.slice(0, -1))}>上一页</button>
        <span>第 {transactionCursors.length + 1} 页</span>
        <button type="button" className="soft-button"
          disabled={!transactions.data?.nextCursor || transactions.isFetching}
          onClick={() => setTransactionCursors([...transactionCursors, transactions.data!.nextCursor!])}>下一页</button>
      </div>
      <form
        className="quota-search"
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(String(new FormData(e.currentTarget).get("search") || ""));
          setCursors([]);
        }}
      >
        <label>
          查找教师
          <input name="search" maxLength={80} placeholder="姓名或登录账号" />
        </label>
        <button className="soft-button">查询</button>
      </form>
      <QueryState
        pending={accounts.isPending}
        error={accounts.error}
        retry={() => void accounts.refetch()}
      />
      {accounts.data?.items.length === 0 && (
        <p className="work-status">未找到匹配的账号。</p>
      )}
      <div className="admin-records">
        {accounts.data?.items.map((account) => (
          <article key={account.id} className="work-panel">
            <div className="section-heading">
              <h3>{account.name}</h3>
              <span>{account.frozen ? "积分已冻结" : "积分正常"}</span>
            </div>
            <p>
              {account.username}
              {account.active ? "" : " · 登录账号已停用"}
            </p>
            <p className="quota-account-balance">
              可用 <strong>{account.available.toLocaleString("zh-CN")}</strong>{" "}
              · 预占 {account.reserved.toLocaleString("zh-CN")} · 余额{" "}
              {account.balance.toLocaleString("zh-CN")}
            </p>
            <div className="quota-actions">
              <button
                type="button"
                className="start-button"
                onClick={() => open(account, "grant")}
              >
                分配积分
              </button>
              <button
                type="button"
                className="soft-button"
                disabled={account.available === 0}
                onClick={() => open(account, "reclaim")}
              >
                收回积分
              </button>
              <button
                type="button"
                className="soft-button"
                onClick={() => open(account, "state")}
              >
                {account.frozen ? "恢复账户" : "冻结账户"}
              </button>
              <button
                type="button"
                className="soft-button"
                onClick={() => {
                  setHistory(account);
                  historyDialog.current?.showModal();
                }}
              >
                积分明细
              </button>
            </div>
          </article>
        ))}
      </div>
      <div className="admin-pagination">
        <button
          type="button"
          className="soft-button"
          disabled={!cursors.length || accounts.isFetching}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          上一页
        </button>
        <button
          type="button"
          className="soft-button"
          disabled={!accounts.data?.nextCursor || accounts.isFetching}
          onClick={() => setCursors([...cursors, accounts.data!.nextCursor!])}
        >
          下一页
        </button>
      </div>
      <dialog
        ref={editDialog}
        aria-labelledby="quotaEditTitle"
        onCancel={(e) => {
          if (save.isPending) e.preventDefault();
        }}
      >
        <form
          key={operation?.key}
          onSubmit={(e) => {
            e.preventDefault();
            if (!operation || save.isPending) return;
            const values = new FormData(e.currentTarget);
            save.mutate({
              op: operation,
              amount: Number(values.get("amount")),
              reason: String(values.get("reason")),
            });
          }}
        >
          <div className="section-heading">
            <h2 id="quotaEditTitle">{title}</h2>
            <button
              type="button"
              className="soft-button"
              aria-label="关闭"
              disabled={save.isPending}
              onClick={() => editDialog.current?.close()}
            >
              ×
            </button>
          </div>
          <p>
            {operation?.account.name} · {operation?.account.username}
          </p>
          {operation?.mode !== "state" && (
            <label>
              积分数量
              <input
                name="amount"
                type="number"
                required
                min={1}
                max={
                  operation?.mode === "reclaim"
                    ? operation.account.available
                    : 2_000_000_000
                }
                step={1}
              />
            </label>
          )}
          <label>
            变更原因
            <textarea
              name="reason"
              required
              maxLength={300}
              placeholder="例如：本月教学额度"
            />
          </label>
          {operation?.mode === "reclaim" && (
            <p>仅能收回可用积分，进行中的任务预占不受影响。</p>
          )}
          {operation?.mode === "state" && !operation.account.frozen && (
            <p>
              冻结后不能发起新的 AI 调用，进行中的任务仍会正常结算或释放预占。
            </p>
          )}
          {save.error && <p role="alert">{save.error.message}</p>}
          <button className="start-button" disabled={save.isPending}>
            {save.isPending ? "正在保存…" : `确认${title}`}
          </button>
        </form>
      </dialog>
      <dialog ref={historyDialog} aria-labelledby="quotaHistoryTitle">
        <div className="section-heading">
          <h2 id="quotaHistoryTitle">{history?.name}的积分明细</h2>
          <button
            type="button"
            className="soft-button"
            aria-label="关闭明细"
            onClick={() => historyDialog.current?.close()}
          >
            ×
          </button>
        </div>
        {history && (
          <QuotaHistory
            key={history.id}
            endpoint={`/admin/quota/accounts/${history.id}/transactions`}
          />
        )}
      </dialog>
    </section>
  );
}
