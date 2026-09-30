"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <section className="work-panel" role="alert">
      <h1>页面暂时无法加载</h1>
      <p>请重试；如果问题持续，请联系管理员。</p>
      <button className="soft-button" onClick={reset}>
        重新加载
      </button>
    </section>
  );
}
