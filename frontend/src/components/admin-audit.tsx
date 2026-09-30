"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, PageResult } from "@/lib/api";
import { QueryState } from "./query-state";
interface Audit {
  id: string;
  action: string;
  actorId: string | null;
  targetId: string | null;
  createdAt: string;
  requestId: string;
}
const labels: Record<string, string> = {
  "draft.saved": "保存教学草稿版本",
  "draft.restored": "恢复教学草稿版本",
  "draft.exported": "导出教学草稿",
  "resource.created": "提交创作任务",
  "resource.cancel_requested": "请求取消创作任务",
  "resource.downloaded": "下载创作结果",
  "answer.feedback_changed": "修改或撤回回答评价",
  "statistics.exported": "导出使用统计",
  "auth.login": "账号登录",
  "auth.logout": "退出登录",
  "auth.login_failed": "登录失败",
  "profile.updated": "修改资料",
  "password.changed": "修改密码",
  "user.created": "创建账号",
  "user.disabled": "停用账号",
  "user.enabled": "启用账号",
  "skill.created": "创建教学 Skill",
  "skill.submitted": "提交教学 Skill 审核",
  "skill.published": "共享教学 Skill",
  "skill.rejected": "退回教学 Skill",
  "skill.unpublished": "停止共享教学 Skill",
  "skill.rolled_back": "恢复教学 Skill 旧版本",
  "conversation.deleted": "删除对话",
  "quota.adjusted": "分配或收回积分",
  "quota.frozen": "冻结积分账户",
  "quota.unfrozen": "恢复积分账户",
  "model.created": "添加模型",
  "model.updated": "修改模型配置",
  "model.enabled": "启用模型",
  "model.disabled": "停用模型",
  "model.default_changed": "更换默认模型",
  "model.connection_checked": "检查模型连接",
  "chat.completed": "对话生成完成",
  "chat.failed": "对话生成失败",
  "chat.cancelled": "对话生成中断",
  "chat.safety_indicator": "检测到提示注入特征",
  "knowledge.created": "创建知识库",
  "document.uploaded": "上传教学资料",
  "document.parsed": "资料解析完成",
  "document.failed": "资料处理失败或隔离",
  "document.retried": "重试资料处理",
  "document.tagged": "修改资料标签",
  "document.downloaded": "下载教学资料",
  "document.deleted": "删除教学资料",
  "embedding.configured": "配置知识库向量模型",
  "document.indexed": "资料索引完成",
  "document.index_failed": "资料索引失败",
  "document.reindexed": "重建资料索引",
  "chat.source_safety_indicator": "资料中检测到提示注入特征",
};
export function AdminAudit() {
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const records = useQuery({
    queryKey: ["audit", cursor],
    queryFn: () =>
      api<PageResult<Audit>>(
        `/admin/audit-logs?limit=30${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
  });
  return (
    <section>
      <h2 className="font-semibold mb-5">操作审计</h2>
      <QueryState
        pending={records.isPending}
        error={records.error}
        retry={() => void records.refetch()}
      />
      <div className="admin-records">
        {records.data?.items.map((row) => (
          <article className="work-panel" key={row.id}>
            <h3>{labels[row.action] || row.action}</h3>
            <p>{new Date(row.createdAt).toLocaleString("zh-CN")}</p>
            <details>
              <summary>查看追踪信息</summary>
              <p>
                操作账号：{row.actorId || "未登录"}
                <br />
                目标记录：{row.targetId || "无"}
                <br />
                请求编号：{row.requestId}
              </p>
            </details>
          </article>
        ))}
      </div>
      {records.data?.items.length === 0 && (
        <p className="work-status">暂无审计记录。</p>
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
