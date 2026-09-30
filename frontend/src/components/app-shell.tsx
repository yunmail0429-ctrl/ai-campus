"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, json, Me } from "@/lib/api";
import { QueryState } from "./query-state";

const navigation = [
  ["/teacher/workspace", "🏠", "AI 工作台"],
  ["/teacher/assistant", "🤖", "AI 助手"],
  ["/teacher/skills", "⚙️", "教学 Skill"],
  ["/teacher/create", "🎨", "AI 创作中心"],
  ["/teacher/plugins", "🧩", "插件"],
  ["/teacher/knowledge", "📚", "知识库"],
  ["/teacher/settings", "👤", "个人设置"],
];
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const client = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [menu, setMenu] = useState(false);
  const [creditPressed, setCreditPressed] = useState(false);
  const creditPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const account = useRef<HTMLDivElement>(null);
  const accountButton = useRef<HTMLButtonElement>(null);
  useEffect(() => () => {
    if (creditPressTimer.current) clearTimeout(creditPressTimer.current);
  }, []);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const quota = useQuery({
    queryKey: ["quota"],
    queryFn: () => api<{ balance: number; available: number }>("/me/quota"),
    enabled: !!me.data,
  });
  const logout = useMutation({
    mutationFn: () => api("/auth/logout", json("POST")),
    onSuccess: () => {
      client.clear();
      router.replace("/login");
    },
  });
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      client.clear();
      router.replace("/login");
    }
  }, [me.error, client, router]);
  useEffect(() => {
    const close = (e: PointerEvent) => {
      if (!account.current?.contains(e.target as Node)) setMenu(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (menu && e.key === "Escape") {
        setMenu(false);
        accountButton.current?.focus();
      }
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [menu]);
  if (!me.data)
    return (
      <main className="session-status">
        <QueryState
          pending={me.isPending}
          error={me.error}
          retry={() => void me.refetch()}
        />
        <Link href="/login">返回登录</Link>
      </main>
    );
  const user = me.data;
  const links = user.roles.includes("admin")
    ? [...navigation, ["/admin", "🛠️", "学校管理"]]
    : navigation;
  return (
    <div className="dashboard">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside
        className={`dash-nav rail-nav ${collapsed ? "is-collapsed" : ""}`}
        id="workspaceNav"
      >
        <button
          className="nav-toggle"
          type="button"
          aria-controls="workspaceNav"
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开导航" : "收起导航"}
          onClick={() => setCollapsed(!collapsed)}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <path d="m14 6-6 6 6 6" />
          </svg>
        </button>
        <div className="dash-brand">
          <span className="dash-logo">
            <img src="/assets/school-emblem.png" alt="西南大学附属中学校徽" />
          </span>
          <div>
            <strong>西南大学附属中学</strong>
            <span className="campus-name">两江校区</span>
          </div>
        </div>
        <div className="brand-product">智能教育助手</div>
        <nav aria-label="主导航">
          {links.map(([href, icon, title]) => (
            <Link
              key={href}
              href={href}
              title={title}
              aria-label={title}
              className={
                pathname === href ||
                (href === "/admin" && pathname.startsWith("/admin"))
                  ? "active"
                  : ""
              }
              aria-current={
                pathname === href ||
                (href === "/admin" && pathname.startsWith("/admin"))
                  ? "page"
                  : undefined
              }
            >
              <span className="rail-icon emoji-icon" aria-hidden="true">
                {icon}
              </span>
              <span className="rail-label">{title}</span>
            </Link>
          ))}
        </nav>
        <div className="dash-bottom" ref={account}>
          <button
            type="button"
            className={`credit-line${creditPressed ? " is-pressed" : ""}`}
            onClick={() => {
              if (creditPressTimer.current) clearTimeout(creditPressTimer.current);
              setCreditPressed(true);
              creditPressTimer.current = setTimeout(() => setCreditPressed(false), 220);
            }}
          >
            <span>可用积分</span>
            <strong>
              {quota.data ? quota.data.available.toLocaleString("zh-CN") : "—"}
            </strong>
          </button>
          {menu && (
            <div className="account-popover" id="accountPopover">
              <p>
                {user.name} · {user.subject}
              </p>
              <button
                className="logout-btn"
                onClick={() => logout.mutate()}
                disabled={logout.isPending}
              >
                退出登录
              </button>
              {logout.error && <p role="alert">{logout.error.message}</p>}
            </div>
          )}
          <button
            className="account-button"
            ref={accountButton}
            onClick={() => setMenu(!menu)}
            aria-expanded={menu}
            aria-controls="accountPopover"
            aria-label="打开账号菜单"
          >
            <span className="avatar">{user.avatarUrl ? <img src={user.avatarUrl} alt="" /> : user.name.slice(0, 1)}</span>
            <span className="account-copy">
              <strong>{user.name}</strong>
              <small>
                {user.subject} ·{" "}
                {user.roles.includes("admin") ? "超级管理员" : "教师"}
              </small>
            </span>
            <span className="account-more" aria-hidden="true">
              ···
            </span>
          </button>
        </div>
      </aside>
      <main
        className={`dash-main ${pathname === "/teacher/assistant" ? "chat-host" : ""}`}
        id="main-content"
        tabIndex={-1}
      >
        {children}
      </main>
    </div>
  );
}
