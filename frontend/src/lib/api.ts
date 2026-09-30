export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public requestId?: string,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        ...(init.body && !(init.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("网络连接失败，请检查网络后重试。", 0, "NETWORK_ERROR");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok || body?.code !== "OK") {
    throw new ApiError(
      body?.message || "服务暂时不可用，请稍后重试。",
      response.status,
      body?.code || "INVALID_RESPONSE",
      body?.requestId,
    );
  }
  return body.data as T;
}
export const json = (method: string, data?: unknown): RequestInit => ({
  method,
  ...(data === undefined ? {} : { body: JSON.stringify(data) }),
});
export async function exportFile(path: string, data: unknown): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch {
    throw new ApiError("网络连接失败，请检查网络后重试。", 0, "NETWORK_ERROR");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      body?.message || "文件导出失败，请稍后重试。",
      response.status,
      body?.code || "EXPORT_FAILED",
      body?.requestId,
    );
  }
  if (!response.headers.get("content-disposition")?.startsWith("attachment;"))
    throw new ApiError(
      "文件导出响应无效，请稍后重试。",
      response.status,
      "INVALID_EXPORT_RESPONSE",
    );
  return response.blob();
}
export interface Me {
  id: string;
  name: string;
  username: string;
  subject: string;
  grade: string;
  roles: string[];
  avatarUrl?: string;
}
export interface Skill {
  id: string;
  name: string;
  description: string;
  status: string;
  scope: string;
  prompt?: string;
  reviewerNotes?: string;
  version?: number;
}
export interface SkillTemplate {
  id: string;
  name: string;
  description: string;
  category: string;
  kind: "chat" | "ppt" | "image" | "video" | "avatar" | "music" | "3d";
  promptStarter?: string;
}
export interface Conversation {
  id: string;
  title: string;
  updatedAt: string;
}
export interface PageResult<T> {
  items: T[];
  nextCursor: string | null;
}
