"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, Me } from "@/lib/api";
import { PageHeading } from "./page-heading";
import { QueryState } from "./query-state";
import { PersonalCredits } from "./quota-history";
import { Preferences, useAvailableSkills } from "@/lib/preferences";
function SettingsForm({
  user,
  preferences,
}: {
  user: Me;
  preferences: Preferences;
}) {
  const client = useQueryClient();
  const [tab, setTab] = useState(0);
  const [profile, setProfile] = useState({
    name: user.name,
    subject: user.subject,
    grade: user.grade,
  });
  const [prefs, setPrefs] = useState(preferences);
  const skills = useAvailableSkills();
  const [password, setPassword] = useState("");
  const [nextPassword, setNextPassword] = useState("");
  const avatarChange = useMutation({
    mutationFn: (file: File | null) => {
      if (!file) return api<Me>("/me/avatar", { method: "DELETE" });
      const data = new FormData();
      data.append("file", file);
      return api<Me>("/me/avatar", { method: "POST", body: data });
    },
    onSuccess: (updated) => {
      client.setQueryData(["me"], updated);
      void client.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const save = useMutation({
    mutationFn: async () => {
      if (tab === 0) return api("/me", json("PATCH", profile));
      if (tab === 1) return api("/me/preferences", json("PUT", prefs));
      return api(
        "/me/password",
        json("POST", { currentPassword: password, newPassword: nextPassword }),
      );
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["me"] });
      void client.invalidateQueries({ queryKey: ["preferences"] });
      setPassword("");
      setNextPassword("");
    },
  });
  return (
    <section className="personal-settings settings-page">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <header>
          <div>
            <h1>个人设置</h1>
            <p>教师账号 · {user.username}</p>
          </div>
        </header>
        <nav className="settings-tabs" aria-label="设置分类">
          {["个人资料", "使用偏好", "账号安全", "我的积分"].map((t, i) => (
            <button
              type="button"
              key={t}
              aria-pressed={tab === i}
              onClick={() => {
                setTab(i);
                save.reset();
              }}
            >
              {t}
            </button>
          ))}
        </nav>
        {tab === 0 && (
          <section>
            <div className="settings-avatar-row">
              <span className="settings-avatar-preview" aria-hidden="true">
                {user.avatarUrl ? <img src={user.avatarUrl} alt="" /> : user.name.trim().slice(0, 1)}
              </span>
              <div>
                <strong>个人头像</strong>
                <label className="settings-avatar-upload" htmlFor="avatar-upload">上传新头像</label>
                <input
                  id="avatar-upload"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  disabled={avatarChange.isPending}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) avatarChange.mutate(file);
                    event.target.value = "";
                  }}
                />
                {user.avatarUrl && <button type="button" className="soft-button" disabled={avatarChange.isPending} onClick={() => avatarChange.mutate(null)}>恢复默认头像</button>}
                <small>支持 JPG、PNG、WebP，最大 5 MB。上传后自动裁成正方形。</small>
                {avatarChange.isPending && <small role="status">正在更新头像…</small>}
                {avatarChange.error && <small role="alert">{avatarChange.error.message}</small>}
              </div>
            </div>
            <label>
              姓名
              <input
                required
                maxLength={30}
                value={profile.name}
                onChange={(e) =>
                  setProfile({ ...profile, name: e.target.value })
                }
              />
            </label>
            <label>
              任教学科
              <input
                required
                maxLength={30}
                value={profile.subject}
                onChange={(e) =>
                  setProfile({ ...profile, subject: e.target.value })
                }
              />
            </label>
            <label>
              任教年级
              <select
                value={profile.grade}
                onChange={(e) =>
                  setProfile({ ...profile, grade: e.target.value })
                }
              >
                {["高一", "高二", "高三", "跨年级"].map((g) => (
                  <option key={g}>{g}</option>
                ))}
              </select>
            </label>
          </section>
        )}
        {tab === 1 && (
          <section>
            <label>
              默认教学 Skill
              <select
                value={prefs.skill}
                onChange={(event) =>
                  setPrefs({ ...prefs, skill: event.target.value })
                }
              >
                <option value="" data-description="自由提问，先把思路理清">
                  通用助手
                </option>
                {prefs.skill &&
                  !skills.data?.items.some(
                    (item) => item.id === prefs.skill,
                  ) && (
                    <option
                      value={prefs.skill}
                      disabled
                      data-description="请重新选择可用的 Skill"
                    >
                      原 Skill 暂不可用，请重新选择
                    </option>
                  )}
                {skills.data?.items.map((item) => (
                  <option
                    key={item.id}
                    value={item.id}
                    data-description={item.description}
                  >
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <QueryState
              pending={skills.isPending}
              error={skills.error}
              retry={() => void skills.refetch()}
            />
            <label>
              默认创作类型
              <select
                value={prefs.format}
                onChange={(e) => setPrefs({ ...prefs, format: e.target.value })}
              >
                {["3D", "数字人", "PPT", "图片", "视频", "音乐"].map((g) => (
                  <option key={g}>{g}</option>
                ))}
              </select>
            </label>
            <label>
              PPT 默认页数
              <input
                type="number"
                min={1}
                max={60}
                required
                value={prefs.pages}
                onChange={(e) =>
                  setPrefs({ ...prefs, pages: Number(e.target.value) })
                }
              />
            </label>
            <label>
              默认创作风格
              <input
                maxLength={80}
                value={prefs.style}
                onChange={(e) => setPrefs({ ...prefs, style: e.target.value })}
              />
            </label>
          </section>
        )}
        {tab === 2 && (
          <section>
            <h3>账号安全</h3>
            <p>登录账号：{user.username} · 角色由管理员管理</p>
            <label>
              当前密码
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <label>
              新密码
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={6}
                value={nextPassword}
                onChange={(e) => setNextPassword(e.target.value)}
              />
            </label>
            <p>新密码至少 6 位。</p>
          </section>
        )}
        {tab === 3 && <PersonalCredits />}
        <footer>
          <div aria-live="polite">
            {save.error ? (
              <p role="alert">{save.error.message}</p>
            ) : save.isSuccess ? (
              <p>已保存。</p>
            ) : (
              <p>个人资料和偏好随账号保存。</p>
            )}
          </div>
          {tab < 3 && (
            <button className="start-button" disabled={save.isPending}>
              {save.isPending
                ? "正在保存…"
                : tab === 2
                  ? "修改密码"
                  : "保存设置"}
            </button>
          )}
        </footer>
      </form>
    </section>
  );
}
export function Settings() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me") });
  const preferences = useQuery({
    queryKey: ["preferences"],
    queryFn: () => api<Preferences>("/me/preferences"),
  });
  return (
    <div className="workspace-wrap">
      <PageHeading title="个人设置" />
      <QueryState
        pending={me.isPending || preferences.isPending}
        error={me.error || preferences.error}
        retry={() => {
          void me.refetch();
          void preferences.refetch();
        }}
      />
      {me.data && preferences.data && (
        <SettingsForm user={me.data} preferences={preferences.data} />
      )}
    </div>
  );
}
