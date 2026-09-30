import { Suspense } from "react";
import { Assistant } from "@/components/assistant";
export default function Page() {
  return (
    <Suspense fallback={<p role="status">正在加载对话…</p>}>
      <Assistant />
    </Suspense>
  );
}
