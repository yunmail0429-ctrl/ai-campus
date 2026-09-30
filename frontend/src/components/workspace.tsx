"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, Conversation, PageResult } from "@/lib/api";
import { usePreferredSkill } from "@/lib/preferences";
import { PageHeading } from "./page-heading";
import { QueryState } from "./query-state";
import { useTeachingDraft } from "./teaching-draft";
import { documentFormats } from "@/lib/document-formats";
const tools = [
  ["🤖", "AI 助手"],
  ["✅", "命题审题"],
  ["📝", "教案"],
  ["📽️", "PPT"],
  ["🎨", "绘图"],
  ["🧑‍🏫", "数字人"],
  ["🎬", "视频"],
  ["🎵", "音乐"],
  ["🧊", "3D"],
];
export function Workspace() {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const { skill, setSkill, skills, pending, error } = usePreferredSkill();
  const [file, setFile] = useState<File | null>(null);
  const teaching = useTeachingDraft();
  const recent = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<PageResult<Conversation>>("/conversations?limit=5"),
  });
  function start() {
    teaching.setDraft({ prompt, skill, file, skillChosen: true });
    router.push("/teacher/assistant");
  }
  return (
    <div className="workspace-wrap">
      <PageHeading title="教师 AI 工作台" />
      <section className="welcome">
        <h1>今天准备什么教学内容？</h1>
        <p>从一个问题、一份资料，或一个教学工具开始。</p>
      </section>
      <section className="workspace-shortcuts" aria-label="常用入口">
        {[
          [
            "📝",
            "教学设计",
            "从目标到课堂活动",
            "/teacher/skills?template=lesson-plan",
          ],
          [
            "✅",
            "命题审题",
            "准备练习与课堂评价",
            "/teacher/skills?template=exam-review",
          ],
          [
            "📽️",
            "课件创作",
            "把教学思路整理成课件",
            "/teacher/create?mode=PPT&template=lesson-slides",
          ],
          ["📚", "教学知识库", "管理与检索教学资料", "/teacher/knowledge"],
        ].map(([icon, title, description, href]) => (
          <Link href={href} key={title} className="workspace-shortcut">
            <span className="workspace-shortcut-icon" aria-hidden="true">
              {icon}
            </span>
            <strong>{title}</strong>
            <span>{description}</span>
          </Link>
        ))}
      </section>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          start();
        }}
      >
        <label className="sr-only" htmlFor="teachingPrompt">
          教学需求
        </label>
        <textarea
          id="teachingPrompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          required={!file}
          placeholder="例如：为高一学生设计一节关于函数单调性的探究课，包含课堂活动和随堂练习。"
        />
        <div className="composer-tools">
          <label className={`soft-button workspace-attachment-button${file ? " is-selected" : ""}`}>
            <span aria-hidden="true">＋</span> {file ? "已添加资料" : "添加资料"}
            <input
              aria-label="添加资料"
              className="sr-only"
              type="file"
              accept={documentFormats}
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
          <select
            value={skill}
            onChange={(e) => setSkill(e.target.value)}
            aria-label="选择 Skill"
          >
            <option value="" data-description="自由提问，先把思路理清">
              通用助手
            </option>
            {skill && !skills.data?.items.some((item) => item.id === skill) && (
              <option
                value={skill}
                disabled
                data-description="请重新选择可用的 Skill"
              >
                所选 Skill 暂不可用
              </option>
            )}
            {skills.data?.items.map((s) => (
              <option value={s.id} key={s.id} data-description={s.description}>
                {s.name}
              </option>
            ))}
          </select>
          <button className="start-button" disabled={pending || !!error}>
            开始准备 →
          </button>
        </div>
        <QueryState pending={false} error={error} />
        {file && (
          <p className="file-label workspace-file-chip">
            <span title={file.name}>{file.name} · 发送时上传</span>{" "}
            <button
              type="button"
              className="text-button"
              onClick={() => setFile(null)}
              aria-label={`移除资料 ${file.name}`}
            >
              移除
            </button>
          </p>
        )}
      </form>
      <section className="workspace-tool-panel" aria-label="教学工具">
        <div className="section-heading">
          <h2>教学工具</h2>
          <span>从备课到创作，一站开启</span>
        </div>
        <div className="tool-row">
          {tools.map(([icon, name], i) => (
            <Link
              className="tool-button"
              key={name}
              href={[
                "/teacher/assistant",
                "/teacher/skills?template=exam-review",
                "/teacher/skills?template=lesson-plan",
                "/teacher/create?mode=PPT&template=lesson-slides",
                "/teacher/create?mode=图片&template=teaching-image",
                "/teacher/create?mode=数字人&template=digital-teacher",
                "/teacher/create?mode=视频&template=lesson-video",
                "/teacher/create?mode=音乐&template=classroom-music",
                "/teacher/create?mode=3D&template=teaching-3d",
              ][i]}
            >
              <span className="tool-icon emoji-icon" aria-hidden="true">
                {icon}
              </span>
              <span>{name}</span>
            </Link>
          ))}
        </div>
      </section>
      <div className="work-columns">
        <section>
          <div className="section-heading">
            <h2>最近工作</h2>
          </div>
          <QueryState
            pending={recent.isPending}
            error={recent.error}
            retry={() => void recent.refetch()}
          />
          {recent.data?.items.length === 0 && (
            <p className="work-status">还没有教学任务，从上方输入需求开始。</p>
          )}
          {recent.data?.items.map((c) => (
            <Link
              className="recent-item"
              key={c.id}
              href={`/teacher/assistant?id=${encodeURIComponent(c.id)}`}
              aria-label={`继续编辑 ${c.title}`}
            >
              <span className="tool-icon emoji-icon">📝</span>
              <div>
                <strong>{c.title}</strong>
                <small>
                  {new Date(c.updatedAt).toLocaleDateString("zh-CN")}
                </small>
              </div>
              <span className="recent-item-action">继续编辑 →</span>
            </Link>
          ))}
        </section>
        <aside>
          <div className="section-heading">
            <h2>常用教学 Skill</h2>
          </div>
          <QueryState
            pending={skills.isPending}
            error={skills.error}
            retry={() => void skills.refetch()}
          />
          {skills.data?.items.length === 0 && (
            <p className="work-status">
              暂无可用教学 Skill，可前往“教学 Skill”创建。
            </p>
          )}
          {skills.data?.items.slice(0, 3).map((s) => (
            <Link
              className="skill-choice"
              key={s.id}
              href={`/teacher/assistant?skill=${encodeURIComponent(s.id)}`}
              aria-label={`使用 ${s.name} 开始编辑`}
            >
              <strong>{s.name} <span aria-hidden="true">↗</span></strong>
              <small>{s.description}</small>
            </Link>
          ))}
        </aside>
      </div>
    </div>
  );
}
