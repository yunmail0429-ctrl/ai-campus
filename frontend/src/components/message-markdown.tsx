import Markdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

// MiMo commonly returns LaTeX with \(...\) and \[...\]. remark-math parses
// dollar delimiters, so normalize only prose and leave code examples intact.
function normalizeMath(content: string) {
  return content.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g).map((part) => {
    if (part.startsWith("`") || part.startsWith("~~~")) return part;
    return part
      .replace(/\\\[([\s\S]*?)\\\]/g, (_, formula: string) => `\n\n$$\n${formula}\n$$\n\n`)
      .replace(/\\\(([^\n]*?)\\\)/g, (_, formula: string) => `$${formula}$`);
  }).join("");
}

export function MessageMarkdown({ content }: { content: string }) {
  return (
    <div className="message-body message-markdown">
      <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} skipHtml>
        {normalizeMath(content)}
      </Markdown>
    </div>
  );
}
