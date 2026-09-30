import Link from "next/link";
export default function NotFound() {
  return (
    <main className="session-status">
      <h1>页面不存在</h1>
      <p>请检查地址或返回工作台。</p>
      <Link href="/teacher/workspace">返回工作台</Link>
    </main>
  );
}
