import type { Citation } from "../components/citations";

// Keep source labels portable without persisting private download URLs or excerpts.
export function answerDraft(content: string, citations?: Citation[]): string {
  if (!citations?.length) return content;
  const plain = (value: string) =>
    value.replace(/[\r\n\t]+/g, " ").replace(/[\\`*_{}\[\]<>#|]/g, "\\$&");
  const sources = citations.map((source, index) => {
    const number = source.number ?? index + 1;
    if (source.unavailable) return `- \\[${number}\\] 来源已不可用`;
    return `- \\[${number}\\] ${plain(source.title)}${source.location ? ` · ${plain(source.location)}` : ""}`;
  });
  return `${content}\n\n### 参考资料\n\n${sources.join("\n")}`;
}
