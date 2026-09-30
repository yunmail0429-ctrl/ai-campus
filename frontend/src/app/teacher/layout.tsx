import { AppShell } from "@/components/app-shell";
import { TeachingDraftProvider } from "@/components/teaching-draft";
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      <TeachingDraftProvider>{children}</TeachingDraftProvider>
    </AppShell>
  );
}
