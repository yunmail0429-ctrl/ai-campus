"use client";
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api, json, Me } from "@/lib/api";

export function Login() {
  const router = useRouter();
  const client = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [remember, setRemember] = useState(true);
  const [help, setHelp] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(true);
    try {
      setUsername(localStorage.getItem("xflj.remembered-account") || "");
    } catch {}
  }, []);
  const login = useMutation({
    mutationFn: async () => {
      await api(
        "/auth/login",
        json("POST", { username: username.trim(), password }),
      );
      return api<Me>("/me");
    },
    onSuccess: (user) => {
      client.clear();
      client.setQueryData(["me"], user);
      try {
        if (remember)
          localStorage.setItem("xflj.remembered-account", username.trim());
        else localStorage.removeItem("xflj.remembered-account");
      } catch {}
      setPassword("");
      router.replace("/teacher/workspace");
    },
  });
  return (
    <>
      <div className="login-bg bg-anim" aria-hidden="true" />
      <div className="light-sweep" aria-hidden="true" />
      <div className="login-stage">
        <aside className="left">
          <div className="badge enter d1">
            <span className="ring school-emblem">
              <img src="/assets/school-emblem.png" alt="西南大学附属中学校徽" />
            </span>
            <span>
              <span className="sch block">西南大学附属中学两江中学</span>
              <span className="sub">智能教育助手</span>
            </span>
          </div>
          <h1 className="enter d2">
            让 AI 成为教师
            <br />
            日常教学与办公的智能助手
          </h1>
          <div className="motto enter d3">
            <div className="row">
              <span className="k">校训</span>
              <span className="v">行己有耻，君子不器</span>
            </div>
            <div className="row">
              <span className="k">校风</span>
              <span className="v">崇德 · 尚美 · 创新 · 笃行</span>
            </div>
          </div>
          <p className="slogan enter d4">
            智能备课 · 作业辅助 · 学情分析 ·
            文档生成，为一线教师减负增效，把时间还给教学。
          </p>
        </aside>
        <main className="right">
          <form
            method="post"
            className="login form-card"
            onSubmit={(e) => {
              e.preventDefault();
              login.mutate();
            }}
          >
            <div className="login-head">
              <h2>平台登录</h2>
              <p>教师账号登录入口</p>
            </div>
            <div className="tag-row enter d5">
              {["AI备课", "命题审题", "知识库", "多模态创作"].map((t) => (
                <span key={t}>{t}</span>
              ))}
            </div>
            <div className="field enter d5">
              <label htmlFor="username">账号</label>
              <div className="box">
                <span className="ic" aria-hidden="true">
                  <svg
                    width="19"
                    height="19"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2" />
                    <circle cx="9" cy="7" r="4" />
                    <path d="M22 21v-2a4 4 0 00-3-3.87" />
                  </svg>
                </span>
                <input
                  id="username"
                  name="username"
                  disabled={!ready}
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="请输入教师账号"
                />
              </div>
            </div>
            <div className="field enter d5">
              <label htmlFor="password">登录密码</label>
              <div className="box">
                <span className="ic" aria-hidden="true">
                  <svg
                    width="19"
                    height="19"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                  >
                    <rect x="4" y="11" width="16" height="10" rx="2" />
                    <path d="M8 11V7a4 4 0 018 0v4" />
                  </svg>
                </span>
                <input
                  id="password"
                  name="password"
                  disabled={!ready}
                  type={visible ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="请输入登录密码"
                />
                <button
                  type="button"
                  className="eye"
                  onClick={() => setVisible(!visible)}
                  aria-label={visible ? "隐藏密码" : "显示密码"}
                  aria-pressed={visible}
                >
                  <svg
                    width="19"
                    height="19"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
                    <circle cx="12" cy="12" r="3" />
                    {visible && <path d="m2 2 20 20" />}
                  </svg>
                </button>
              </div>
            </div>
            <div className="row-opt enter d6">
              <label>
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                />
                记住账号
              </label>
              <button
                className="text-button"
                type="button"
                onClick={() => setHelp(!help)}
              >
                忘记密码？
              </button>
            </div>
            {help && (
              <p className="work-status" role="status">
                请联系学校管理员重置账号密码。
              </p>
            )}
            {login.error && (
              <p className="form-error" role="alert">
                {login.error.message}
              </p>
            )}
            <button
              type="submit"
              className="btn-login btn-anim"
              disabled={!ready || login.isPending}
            >
              {login.isPending ? "正在登录…" : "登 录 系 统"}
            </button>
            <p className="login-foot enter d6">使用学校管理员分配的账号登录</p>
          </form>
        </main>
      </div>
    </>
  );
}
