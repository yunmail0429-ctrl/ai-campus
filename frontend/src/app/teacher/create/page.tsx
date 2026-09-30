import { Suspense } from "react";
import { Studio } from "@/components/studio";
export default function Page() {
  return (
    <Suspense fallback={<p role="status">正在加载创作中心…</p>}>
      <Studio />
    </Suspense>
  );
}
