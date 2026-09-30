const paths: Record<string, string> = {
  home: "M3 3h18v18H3z M7 7h4v4H7z M15 7h2 M15 11h2 M7 15h10 M7 18h6",
  assistant:
    "M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3V7a2 2 0 0 1 1-2 M8 10h8 M8 14h5",
  skills: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  create: "M4 20h4L20 8l-4-4L4 16v4 M13 7l4 4",
  knowledge:
    "M12 5v16 M3 4c4-1 6 0 9 2 3-2 5-3 9-2v15c-4-1-6 0-9 2-3-2-5-3-9-2z",
  settings:
    "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4 21v-2a6 6 0 0 1 6-5h4a6 6 0 0 1 6 5v2",
  admin: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6",
  check: "M4 4h16v16H4z M8 12l3 3 5-6",
  document: "M5 3h10l4 4v14H5z M14 3v5h5 M9 12h6 M9 16h6",
  slides: "M3 4h18v13H3z M12 17v4 M8 21h8 M7 9h4 M7 12h8",
  image: "M3 3h18v18H3z M3 17l6-6 4 4 3-3 5 5 M16 7h.01",
  person:
    "M9 8a3 3 0 1 0 6 0 3 3 0 0 0-6 0 M5 21v-3a7 7 0 0 1 14 0v3 M3 3h3 M18 3h3",
  video: "M3 6h12v12H3z M15 10l6-4v12l-6-4",
  music:
    "M9 18V5l11-2v13 M9 8l11-2 M9 18c0 4-6 4-6 1s6-4 6-1 M20 16c0 4-6 4-6 1s6-4 6-1",
  cube: "m12 2 9 5v10l-9 5-9-5V7z M3 7l9 5 9-5 M12 12v10",
  arrow: "M5 12h14 M14 7l5 5-5 5",
  plus: "M12 5v14 M5 12h14",
};

export function UiIcon({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  return (
    <svg
      className={`ui-icon ${className}`}
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] || paths.document} />
    </svg>
  );
}
