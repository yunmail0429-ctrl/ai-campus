import { AppShell } from "@/components/app-shell";
import { AdminLayout } from "@/components/admin-layout";
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell>
      <AdminLayout>{children}</AdminLayout>
    </AppShell>
  );
}
