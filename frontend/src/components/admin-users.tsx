"use client";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, Me, PageResult } from "@/lib/api";
import { QueryState } from "./query-state";

type User = Me & { active: boolean };
const avatarColors = ["blue", "violet", "teal", "amber", "rose", "indigo"] as const;

function avatarColor(user: User) {
  const seed = [...user.id].reduce((value, character) => value + character.charCodeAt(0), 0);
  return avatarColors[seed % avatarColors.length];
}

export function AdminUsers() {
  const client = useQueryClient();
  const dialog = useRef<HTMLDialogElement>(null);
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const users = useQuery({
    queryKey: ["admin-users", cursor],
    queryFn: () =>
      api<PageResult<User>>(
        `/admin/users?limit=30${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
  });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const create = useMutation({
    mutationFn: (data: Record<string, string>) =>
      api("/admin/users", json("POST", data)),
    onSuccess: () => {
      dialog.current?.close();
      void client.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const toggle = useMutation({
    mutationFn: (user: User) =>
      api(`/admin/users/${user.id}`, json("PATCH", { active: !user.active })),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const remove = useMutation({
    mutationFn: (user: User) => api(`/admin/users/${user.id}`, { method: "DELETE" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  return (
    <section>
      <div className="section-heading">
        <h2>学校账号</h2>
        <button
          className="start-button"
          onClick={() => {
            create.reset();
            dialog.current?.showModal();
          }}
        >
          ＋ 创建账号
        </button>
      </div>
      <QueryState
        pending={users.isPending}
        error={users.error || toggle.error || remove.error}
        retry={() => void users.refetch()}
      />
      <div className="admin-records">
        {users.data?.items.map((u) => (
          <article className="work-panel admin-record" key={u.id}>
            <div className="admin-user-profile">
              <span className={`admin-user-avatar is-${avatarColor(u)}`} aria-hidden="true">
                {u.avatarUrl ? <img src={u.avatarUrl} alt="" /> : u.name.trim().slice(0, 1) || "人"}
              </span>
              <div>
              <div className="admin-user-identity">
                <h3>{u.name}</h3>
                <span className={`admin-user-role ${u.roles.includes("admin") ? "is-admin" : ""}`}>
                  {u.roles.includes("admin") ? "超级管理员" : "普通用户"}
                </span>
              </div>
              <p className="admin-user-details">
                <span>{u.subject || "未设置学科"}</span>
                <span className="admin-user-username">账号 {u.username}</span>
              </p>
              <span className="skill-state">
                {u.active ? "使用中" : "已停用"}
              </span>
              </div>
            </div>
            <div className="admin-record-actions">
              <button
                className="soft-button"
                disabled={toggle.isPending || u.id === me.data?.id}
                onClick={() => {
                  if (
                    !u.active ||
                    window.confirm(`停用 ${u.name} 的账号并撤销其登录会话？`)
                  )
                    toggle.mutate(u);
                }}
              >
                {u.active ? "停用账号" : "启用账号"}
              </button>
              {!u.roles.includes("admin") && (
                <button
                  className="soft-button"
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm(`确定删除教师账号 ${u.name}？删除后将无法登录，账号不会再出现在列表中。`))
                      remove.mutate(u);
                  }}
                >
                  删除账号
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
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
          disabled={!users.data?.nextCursor}
          onClick={() => setCursors([...cursors, users.data!.nextCursor!])}
        >
          下一页
        </button>
      </div>
      <dialog ref={dialog} aria-labelledby="newUserTitle">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const values = Object.fromEntries(
              new FormData(form).entries(),
            ) as Record<string, string>;
            create.mutate(values, { onSuccess: () => form.reset() });
          }}
        >
          <div className="section-heading">
            <h2 id="newUserTitle">创建账号</h2>
            <button
              type="button"
              className="soft-button"
              aria-label="关闭"
              onClick={() => dialog.current?.close()}
            >
              ×
            </button>
          </div>
          <label>
            登录账号
            <input
              name="username"
              required
              pattern="[A-Za-z0-9_.@\-]{3,80}"
              minLength={3}
              maxLength={80}
              autoComplete="off"
            />
          </label>
          <label>
            初始密码
            <input
              name="password"
              type="password"
              required
              minLength={6}
              maxLength={1024}
              autoComplete="new-password"
            />
          </label>
          <label>
            姓名
            <input name="name" required maxLength={30} />
          </label>
          <label>
            学科
            <input name="subject" maxLength={30} />
          </label>
          <label>
            年级
            <select name="grade">
              {["高一", "高二", "高三", "跨年级"].map((g) => (
                <option key={g}>{g}</option>
              ))}
            </select>
          </label>
          <label>
            角色
            <select name="role">
              <option value="teacher" data-description="备课、创作与资料管理">
                教师
              </option>
              <option value="admin" data-description="学校配置与使用管理">
                超级管理员
              </option>
            </select>
          </label>
          <p>密码至少 6 位。账号创建后可由用户自行修改密码。</p>
          {create.error && <p role="alert">{create.error.message}</p>}
          <button className="start-button" disabled={create.isPending}>
            创建账号
          </button>
        </form>
      </dialog>
    </section>
  );
}
