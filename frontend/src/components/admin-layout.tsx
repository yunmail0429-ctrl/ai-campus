"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, Me } from "@/lib/api";
import { PageHeading } from "./page-heading";
import { QueryState } from "./query-state";

export function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  if (!me.data) return <QueryState pending={me.isPending} error={me.error} />;
  if (!me.data.roles.includes("admin"))
    return (
      <section className="work-panel" role="alert">
        <h1>无权访问管理员后台</h1>
        <p>请使用学校管理员账号登录。</p>
        <Link
          className="soft-button inline-block mt-4"
          href="/teacher/workspace"
        >
          返回工作台
        </Link>
      </section>
    );
  return (
    <div className="workspace-wrap">
      <PageHeading title="学校管理" />
      <h1 className="text-2xl font-semibold">学校管理</h1>
      <nav className="admin-tabs" aria-label="后台模块">
        {[
          ["/admin/statistics", "学校看板"],
          ["/admin/users", "账号管理"],
          ["/admin/knowledge", "知识库管理"],
          ["/admin/skills", "教学 Skill 审核"],
          ["/admin/credits", "积分管理"],
          ["/admin/models", "AI 服务设置"],
          ["/admin/audit-logs", "操作记录"],
        ].map(([href, label]) => (
          <Link
            key={href}
            href={href}
            aria-current={path === href ? "page" : undefined}
          >
            {label}
          </Link>
        ))}
      </nav>
      {children}
    </div>
  );
}
