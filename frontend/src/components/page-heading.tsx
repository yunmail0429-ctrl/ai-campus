export function PageHeading({ title }: { title: string }) {
  return (
    <header className="workspace-top">
      <span>西南大学附属中学两江校区 / {title}</span>
    </header>
  );
}
