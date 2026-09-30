import { ApiError } from "./api";
export interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}
export async function streamMessage(
  path: string,
  input: unknown,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
  operationKey: string = crypto.randomUUID(),
) {
  const response = await fetch(`/api/v1${path}`, {
    method: "POST",
    credentials: "same-origin",
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "Idempotency-Key": operationKey,
    },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      body?.message || "发送失败，请重试。",
      response.status,
      body?.code || "STREAM_ERROR",
      body?.requestId,
    );
  }
  if (
    !response.body ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  )
    throw new Error("服务器未返回有效的流式响应。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;
  function consume(frame: string) {
    let event = "message";
    const data: string[] = [];
    for (const raw of frame.split("\n")) {
      const line = raw.replace(/\r$/, "");
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return;
    const parsed = JSON.parse(data.join("\n"));
    if (event === "error")
      throw new ApiError(
        parsed.message || "生成失败，请重试。",
        503,
        parsed.code || "GENERATION_FAILED",
        parsed.invocationId,
      );
    if (event === "message.completed") completed = true;
    onEvent({ event, data: parsed });
  }
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let separator: RegExpExecArray | null;
      while ((separator = /\r?\n\r?\n/.exec(buffer))) {
        consume(buffer.slice(0, separator.index));
        buffer = buffer.slice(separator.index + separator[0].length);
      }
      if (buffer.length > 1_000_000) throw new Error("流式响应格式异常。");
      if (done) break;
    }
    if (!completed)
      throw new Error("连接已中断，请重新加载对话查看已保存内容。");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
