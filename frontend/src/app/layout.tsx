import type { Metadata } from "next";
import { Providers } from "./providers";
import "./globals.css";
import "katex/dist/katex.min.css";
export const metadata: Metadata = {
  title: "西附两江 · 智能教育助手",
  description: "西南大学附属中学两江中学教师 AI 教学工作平台",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
