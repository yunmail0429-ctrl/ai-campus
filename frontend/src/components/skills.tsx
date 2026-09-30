"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, Me, PageResult, Skill, SkillTemplate } from "@/lib/api";
import { PageHeading } from "./page-heading";
import { QueryState } from "./query-state";
import { SkillReadiness } from "./skill-readiness";
import {
  buildTeachingSkill,
  emptyTeachingSkill,
  parseTeachingSkill,
  teachingSkillExamples,
  type TeachingSkillFields,
} from "@/lib/teaching-skill";
const labels: Record<string, string> = {
  draft: "草稿",
  pending: "审核中",
  published: "已发布",
  rejected: "已退回",
  archived: "已下架",
};
const teachingTasks = [
  { id: "all", label: "全部类别" },
  { id: "review", label: "试题审题", words: /审题|试题审核|试卷审核/ },
  { id: "slides", label: "课件创作", words: /课件|PPT/ },
  { id: "planning", label: "备课设计", words: /备课|教案|教学设计|教学方案|课时|教学目标/ },
  { id: "practice", label: "练习出题", words: /练习|出题|命题|试题|试卷|作业|测验|题目/ },
  { id: "activity", label: "课堂活动", words: /课堂活动|讨论|互动|课堂导入|小组活动/ },
  { id: "feedback", label: "评价反馈", words: /评价|评语|量规|批改|反馈|评分/ },
  { id: "materials", label: "资料整理", words: /资料|讲义|知识梳理|知识点|总结|素材/ },
  { id: "other", label: "其他" },
] as const;
type TeachingTaskId = (typeof teachingTasks)[number]["id"];
function taskFor(skill: Skill): TeachingTaskId {
  const text = `${skill.name} ${skill.description}`;
  return teachingTasks.find((task) => "words" in task && task.words.test(text))?.id || "other";
}
const knownSubjects = ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理", "科学", "信息技术", "通用技术", "音乐", "美术", "体育", "劳动"];
function subjectFor(skill: Skill): string {
  const guidedSubject = parseTeachingSkill(skill.prompt || "")?.subject.trim();
  const source = guidedSubject || `${skill.name} ${skill.description}`;
  if (/跨学科|综合实践/.test(source)) return "综合";
  const match = knownSubjects.find((subject) => source.includes(subject));
  return match || guidedSubject || "综合";
}
export function Skills() {
  const searchParams = useSearchParams();
  const autoTemplate = searchParams.get("template");
  const autoInstalled = useRef<string | null>(null);
  const [tab, setTab] = useState("school");
  const [search, setSearch] = useState("");
  const [task, setTask] = useState<TeachingTaskId>("all");
  const [subject, setSubject] = useState("all");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favorites, setFavorites] = useState<string[]>([]);
  const client = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [teachingFields, setTeachingFields] = useState<TeachingSkillFields>(emptyTeachingSkill);
  const [guided, setGuided] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [readinessSkill, setReadinessSkill] = useState<Skill | null>(null);
  const records = useQuery({
    queryKey: ["skills", tab],
    queryFn: async () => {
      const items: Skill[] = [];
      const cursors = new Set<string>();
      let cursor: string | null = null;
      do {
        const result: PageResult<Skill> = await api<PageResult<Skill>>(
          `/skills?scope=${tab}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
        );
        items.push(...result.items);
        if (!result.nextCursor || cursors.has(result.nextCursor)) break;
        cursor = result.nextCursor;
        cursors.add(cursor);
      } while (cursor);
      return { items, nextCursor: null };
    },
  });
  const templates = useQuery({ queryKey: ["skill-templates"], queryFn: () => api<SkillTemplate[]>("/skills/templates") });
  const install = useMutation({
    mutationFn: (id: string) => api<Skill>(`/skills/templates/${encodeURIComponent(id)}/install`, json("POST")),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["skills"] }),
  });
  useEffect(() => {
    if (!autoTemplate || autoInstalled.current === autoTemplate || !templates.data?.some((item) => item.id === autoTemplate && item.kind === "chat")) return;
    autoInstalled.current = autoTemplate;
    install.mutate(autoTemplate, {
      onSuccess: (skill) => { window.location.href = `/teacher/assistant?skill=${encodeURIComponent(skill.id)}`; },
      onError: () => { autoInstalled.current = null; },
    });
  }, [autoTemplate, templates.data, install]);
  const favoriteKey = me.data ? `teacher-skill-favorites:${me.data.id}` : "";
  useEffect(() => {
    if (!favoriteKey) return;
    try {
      const saved = JSON.parse(localStorage.getItem(favoriteKey) || "[]");
      setFavorites(Array.isArray(saved) ? saved.filter((id): id is string => typeof id === "string") : []);
    } catch {
      setFavorites([]);
    }
  }, [favoriteKey]);
  const subjects = useMemo(() => {
    const available = new Set((records.data?.items || []).map(subjectFor));
    knownSubjects.forEach((item) => available.delete(item));
    return [...knownSubjects, ...Array.from(available).sort((a, b) => a.localeCompare(b, "zh-CN"))];
  }, [records.data?.items]);
  const visibleSkills = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return (records.data?.items || [])
      .filter((skill) => {
        const category = taskFor(skill);
        return (task === "all" || category === task)
          && (subject === "all" || subjectFor(skill) === subject)
          && (!favoritesOnly || favorites.includes(skill.id))
          && (!query || `${skill.name} ${skill.description} ${subjectFor(skill)} ${teachingTasks.find((item) => item.id === category)?.label || ""}`.toLocaleLowerCase().includes(query));
      })
      .sort((a, b) => Number(favorites.includes(b.id)) - Number(favorites.includes(a.id)));
  }, [records.data?.items, search, task, subject, favoritesOnly, favorites]);
  const visibleTemplates = (tab === "school" && !favoritesOnly && (subject === "all" || subject === "综合"))
    ? (templates.data || []).filter((item) =>
        (task === "all" || item.category === task)
        && (!search.trim() || `${item.name} ${item.description}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())))
    : [];
  function toggleFavorite(id: string) {
    if (!favoriteKey) return;
    const next = favorites.includes(id) ? favorites.filter((item) => item !== id) : [...favorites, id];
    setFavorites(next);
    localStorage.setItem(favoriteKey, JSON.stringify(next));
  }
  const create = useMutation({
    mutationFn: () =>
      api<Skill>(
        editing ? `/skills/${editing}` : "/skills",
        json(editing ? "PATCH" : "POST", {
          name,
          description: guided
            ? `${teachingFields.grade}${teachingFields.subject} · ${teachingFields.topic} · ${teachingFields.goal}`.slice(0, 300)
            : description,
          prompt: guided ? buildTeachingSkill(teachingFields) : prompt,
          scope: "private",
        }),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["skills"] });
      setTab("mine");
      dialog.current?.close();
      setName("");
      setDescription("");
      setPrompt("");
      setTeachingFields({ ...emptyTeachingSkill });
      setGuided(true);
      setEditing(null);
    },
  });
  const loadEdit = useMutation({
    mutationFn: (id: string) => api<Skill>(`/skills/${id}`),
    onSuccess: (s) => {
      setEditing(s.id);
      setName(s.name);
      setDescription(s.description);
      setPrompt(s.prompt || "");
      const parsed = parseTeachingSkill(s.prompt || "");
      setTeachingFields(parsed || { ...emptyTeachingSkill });
      setGuided(!!parsed);
      create.reset();
      dialog.current?.showModal();
    },
  });
  const submit = useMutation({
    mutationFn: (id: string) => api(`/skills/${id}/submit`, json("POST")),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["skills"] }),
  });
  return (
    <div className="workspace-wrap">
      <PageHeading title="教学 Skill" />
      <div className="section-heading">
        <div>
          <h1 className="text-2xl font-semibold">教学 Skill</h1>
          <p className="skill-intro">从备课、出题到课堂评价，找到适合这节课的帮手。</p>
        </div>
        <button
          className="start-button"
          onClick={() => {
            setEditing(null);
            setName("");
            setDescription("");
            setPrompt("");
            setTeachingFields({ ...emptyTeachingSkill });
            setGuided(true);
            create.reset();
            dialog.current?.showModal();
          }}
        >
          ＋ 创建教学 Skill
        </button>
      </div>
      <div className="skill-discovery" aria-label="查找教学 Skill">
        <label className="skill-search">
          <span className="sr-only">搜索教学 Skill</span>
          <span aria-hidden="true">⌕</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索教学任务或助手名称，例如：备课、出题、作文评价" />
        </label>
        <div className="skill-primary-filters" aria-label="教学 Skill 分类">
          <label className="skill-primary-filter">
            <span>学科</span>
            <select value={subject} onChange={(event) => setSubject(event.target.value)}>
              <option value="all">全部学科</option>
              {subjects.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="skill-primary-filter">
            <span>类别</span>
            <select value={task} onChange={(event) => setTask(event.target.value as TeachingTaskId)}>
              {teachingTasks.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        </div>
      </div>
      <div className="skill-library-heading">
        <div className="mode-tabs">
        {[
          ["school", "学校共享"],
          ["mine", "我的助手"],
        ].map(([id, title]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={tab === id ? "selected" : ""}
            aria-pressed={tab === id}
          >
            {title}
          </button>
        ))}
        </div>
        <button type="button" className={`skill-favorites-filter${favoritesOnly ? " is-active" : ""}`}
          aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)}>
          <span aria-hidden="true">☆</span> 只看常用
        </button>
      </div>
      <QueryState
        pending={records.isPending}
        error={records.error || templates.error || install.error || submit.error || loadEdit.error}
        retry={() => void records.refetch()}
      />
      {tab === "school" && templates.isPending && <p className="work-status" role="status">正在加载教学功能…</p>}
      <div className="skill-catalog">
        {visibleTemplates.map((item) => (
          <article className="skill-library-card" key={`template-${item.id}`}>
            {item.kind === "chat" ? (
              <button type="button" className="skill-select" disabled={install.isPending}
                aria-label={`应用教学 Skill：${item.name}`}
                onClick={() => install.mutate(item.id, {
                  onSuccess: (skill) => { window.location.href = `/teacher/assistant?skill=${encodeURIComponent(skill.id)}`; },
                })}>
                <span className="skill-select-name">{item.name}</span><span className="skill-select-arrow" aria-hidden="true">→</span>
              </button>
            ) : (
              <Link className="skill-select"
                href={`/teacher/create?mode=${encodeURIComponent(({ ppt: "PPT", image: "图片", video: "视频", avatar: "数字人", music: "音乐", "3d": "3D" } as Record<string, string>)[item.kind])}&template=${encodeURIComponent(item.id)}`}
                aria-label={`打开教学创作：${item.name}`}>
                <span className="skill-select-name">{item.name}</span><span className="skill-select-arrow" aria-hidden="true">→</span>
              </Link>
            )}
            <div className="skill-card-details"><span className="skill-task-label">{teachingTasks.find((entry) => entry.id === item.category)?.label || "教学工具"}</span><p>{item.description}</p></div>
          </article>
        ))}
        {visibleSkills.map((s) => (
          <article className="skill-library-card" key={s.id}>
            <Link className="skill-select" href={`/teacher/assistant?skill=${encodeURIComponent(s.id)}`}
              aria-label={`应用教学 Skill：${s.name}`}>
              <span className="skill-select-name">{s.name}</span>
              <span className="skill-select-arrow" aria-hidden="true">→</span>
            </Link>
            <button type="button" className={`skill-favorite${favorites.includes(s.id) ? " is-active" : ""}`}
              aria-label={`${favorites.includes(s.id) ? "取消常用" : "设为常用"} ${s.name}`}
              aria-pressed={favorites.includes(s.id)} disabled={!favoriteKey}
              onClick={() => toggleFavorite(s.id)}>{favorites.includes(s.id) ? "★" : "☆"}</button>
            <details className="skill-card-details">
              <summary>查看说明</summary>
              <div className="skill-card-tags"><span className="skill-subject-label">{subjectFor(s)}</span><span className="skill-task-label">{teachingTasks.find((item) => item.id === taskFor(s))?.label}</span></div>
              <p>{s.description}</p>
              <div className="skill-card-meta">
                <span className={`skill-state ${s.status === "published" ? "approved" : s.status}`}>
                  {labels[s.status] || s.status}
                </span>
                <small>{s.scope === "private" ? "我的助手" : "学校共享"}</small>
              </div>
              {s.reviewerNotes && <p className="skill-feedback">审核意见：{s.reviewerNotes}</p>}
            </details>
            {tab === "mine" && ["draft", "rejected"].includes(s.status) && <div className="skill-actions">
                  <button
                    className="soft-button"
                    onClick={() => setReadinessSkill(s)}
                  >
                    分享前检查
                  </button>
                  <button
                    className="soft-button"
                    onClick={() => loadEdit.mutate(s.id)}
                    disabled={loadEdit.isPending}
                  >
                    编辑
                  </button>
                  <button
                    className="start-button"
                    onClick={() => submit.mutate(s.id)}
                    disabled={submit.isPending}
                  >
                    提交学校审核
                  </button>
            </div>}
          </article>
        ))}
      </div>
      {!records.isPending && !templates.isPending && !records.error && visibleSkills.length === 0 && visibleTemplates.length === 0 && (
        <p className="work-status">
          {records.data?.items.length === 0
            ? tab === "mine" ? "还没有个人教学 Skill，点击“创建教学 Skill”开始。" : "学校还没有共享教学 Skill。"
            : "没有找到符合条件的助手，试试其他学科或类别，或清空搜索。"}
        </p>
      )}
      {readinessSkill && (
        <SkillReadiness
          skill={readinessSkill}
          onClose={() => setReadinessSkill(null)}
        />
      )}
      <dialog ref={dialog} className="skill-editor-dialog" aria-labelledby="skillDialogTitle">
        <form className="skill-editor"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <header className="skill-editor-header">
            <div>
              <small>我的教学工具</small>
              <h2 id="skillDialogTitle">{editing ? "编辑教学 Skill" : "创建教学 Skill"}</h2>
              <p>告诉我们这位助手要帮你完成哪件教学工作。</p>
            </div>
            <button
              type="button"
              className="skill-editor-close"
              onClick={() => dialog.current?.close()}
              aria-label="关闭"
            >
              ×
            </button>
          </header>
          <div className="skill-editor-body">
            <div className="skill-editor-fields">
              <div className="skill-editor-section-title">
                <span>01</span><div><strong>先说说这位助手</strong><small>用老师熟悉的教学信息来描述它</small></div>
              </div>
              <label>给这个教学 Skill 起个名字
                <input required maxLength={50} value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="例如：高一议论文写作助手" />
              </label>
              {guided ? (
                <>
                  <div className="skill-teacher-fields">
                    <label>学科
                      <select required aria-label="学科" value={teachingFields.subject}
                        onChange={(e) => setTeachingFields({ ...teachingFields, subject: e.target.value })}>
                        <option value="" disabled>请选择学科</option>
                        {teachingFields.subject && !knownSubjects.includes(teachingFields.subject) &&
                          <option value={teachingFields.subject}>{teachingFields.subject}</option>}
                        {knownSubjects.map((item) => <option key={item} value={item}>{item}</option>)}
                      </select>
                    </label>
                    <label>适用年级
                      <select value={teachingFields.grade}
                        onChange={(e) => setTeachingFields({ ...teachingFields, grade: e.target.value })}>
                        {["高一", "高二", "高三", "跨年级"].map((grade) => <option key={grade}>{grade}</option>)}
                      </select>
                    </label>
                  </div>
                  <label>教学主题
                    <input required maxLength={100} value={teachingFields.topic}
                      onChange={(e) => setTeachingFields({ ...teachingFields, topic: e.target.value })}
                      placeholder="例如：议论文论证方法" />
                  </label>
                  <div className="skill-editor-section-title skill-editor-second-section">
                    <span>02</span><div><strong>告诉助手怎么帮你</strong><small>写出学习目标和你需要的成果</small></div>
                  </div>
                  <label>希望学生学会什么
                    <textarea required maxLength={180} value={teachingFields.goal}
                      onChange={(e) => setTeachingFields({ ...teachingFields, goal: e.target.value })}
                      placeholder="例如：能识别并运用事实论据与道理论据" />
                  </label>
                  <label>希望助手给你什么
                    <input required maxLength={100} value={teachingFields.result}
                      onChange={(e) => setTeachingFields({ ...teachingFields, result: e.target.value })}
                      placeholder="例如：含活动和评价标准的教学方案" />
                  </label>
                  <details className="skill-editor-optional">
                    <summary>其他教学要求（选填）{teachingFields.notes ? " · 已填写" : ""}</summary>
                    <label className="sr-only" htmlFor="skill-notes">其他教学要求</label>
                    <textarea id="skill-notes" maxLength={180} value={teachingFields.notes}
                      onChange={(e) => setTeachingFields({ ...teachingFields, notes: e.target.value })}
                      placeholder="例如：活动控制在 15 分钟内，兼顾基础薄弱的学生" />
                  </details>
                </>
              ) : (
                <>
                  <p className="skill-legacy-note">这份旧草稿使用原有格式，仍可继续编辑。</p>
                  <label>用途说明
                    <textarea required maxLength={300} value={description}
                      onChange={(e) => setDescription(e.target.value)} />
                  </label>
                  <label>已有教学要求
                    <textarea required maxLength={10000} value={prompt}
                      onChange={(e) => setPrompt(e.target.value)} />
                  </label>
                </>
              )}
            </div>
            <aside className="skill-editor-preview" aria-label="教学 Skill 实时预览">
              <div className="skill-editor-preview-heading"><span>实时预览</span><small>边填写，边看使用效果</small></div>
              <div className="skill-preview-card">
                <span className="skill-preview-badge">教学 Skill</span>
                <h3>{name.trim() || "你的教学 Skill"}</h3>
                <p>{guided
                  ? `${teachingFields.grade}${teachingFields.subject || "学科"} · ${teachingFields.topic || "填写教学主题后显示用途"}`
                  : description || "填写用途说明后显示在这里"}</p>
                {guided && <div className="skill-preview-outcome">
                  <small>它会帮你</small>
                  <strong>{teachingFields.result || "准备教学成果"}</strong>
                  <span>{teachingFields.goal || "填写学习目标后，这里会展示课堂重点。"}</span>
                </div>}
              </div>
              {guided && <div className="skill-preview-examples">
                <strong>可以这样向它提问</strong>
                {teachingSkillExamples(teachingFields).slice(0, 3).map((example) => (
                  <p key={example.input}>{example.input}</p>
                ))}
              </div>}
              {guided && <details className="skill-template-preview">
                <summary>查看自动生成的详细设置</summary>
                <pre>{buildTeachingSkill(teachingFields)}</pre>
              </details>}
            </aside>
          </div>
          <footer className="skill-editor-footer">
            <div><p>保存后可先试用，确认合适再分享给学校。</p>
              {create.error && <p role="alert">{create.error.message}</p>}</div>
            <div className="skill-editor-footer-actions">
              <button type="button" className="soft-button" onClick={() => dialog.current?.close()}>取消</button>
              <button className="start-button" disabled={create.isPending}>保存草稿</button>
            </div>
          </footer>
        </form>
      </dialog>
    </div>
  );
}
