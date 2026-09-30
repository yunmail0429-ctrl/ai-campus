import { Suspense } from "react";
import { Skills } from "@/components/skills";
export default function Page() {
  return <Suspense fallback={<p role="status">正在加载教学 Skill…</p>}><Skills /></Suspense>;
}
