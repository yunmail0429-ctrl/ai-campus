import { NextRequest } from "next/server";
export const dynamic = "force-dynamic";
// Only a fixed, operator-configured backend can receive forwarded requests.
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const requestId = crypto.randomUUID();
  const fail = (message: string, status = 503, code = "SERVICE_UNAVAILABLE") =>
    Response.json(
      { code, message, data: null, requestId },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  const backend = process.env.BACKEND_URL;
  if (!backend) return fail("后台服务尚未配置，请联系管理员。");
  if (!["GET", "HEAD"].includes(request.method)) {
    const origin = request.headers.get("origin");
    const appOrigin =
      process.env.APP_ORIGIN ||
      `${request.nextUrl.protocol}//${request.headers.get("host")}`;
    const allowedOrigins = [appOrigin, ...(process.env.APP_ORIGINS?.split(",") || [])];
    const requestHost = request.headers.get("host");
    if (!origin || !allowedOrigins.includes(origin) || new URL(origin).host !== requestHost)
      return fail("请求来源无效，请刷新页面重试。", 403, "INVALID_ORIGIN");
  }
  const { path } = await context.params;
  if (
    path.some(
      (segment) => segment === ".." || segment === "." || /[/\\]/.test(segment),
    )
  )
    return fail("接口路径无效。", 400, "INVALID_PATH");
  const headers = new Headers({ "X-Request-ID": requestId });
  for (const name of [
    "cookie",
    "content-type",
    "accept",
    "idempotency-key",
    "origin",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    let body: Uint8Array | undefined;
    if (!["GET", "HEAD"].includes(request.method) && request.body) {
      const upload = request.headers
        .get("content-type")
        ?.toLowerCase()
        .startsWith("multipart/form-data");
      const configured = Number(
        process.env.MAX_UPLOAD_BYTES || 20 * 1024 * 1024,
      );
      const uploadLimit =
        Number.isSafeInteger(configured) &&
        configured >= 1024 &&
        configured <= 100 * 1024 * 1024
          ? configured
          : 20 * 1024 * 1024;
      const limit = upload ? uploadLimit + 64 * 1024 : 2 * 1024 * 1024;
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.byteLength;
          if (size > limit) {
            await reader.cancel();
            return fail("请求内容超过大小限制。", 413, "REQUEST_TOO_LARGE");
          }
          chunks.push(part.value);
        }
      } finally {
        reader.releaseLock();
      }
      body = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        body.set(chunk, offset);
        offset += chunk.byteLength;
      }
    }
    const upstream = await fetch(
      `${backend.replace(/\/$/, "")}/api/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`,
      {
        method: request.method,
        headers,
        redirect: "manual",
        cache: "no-store",
        body: body as BodyInit | undefined,
        signal: AbortSignal.any([request.signal, AbortSignal.timeout(330_000)]),
      },
    );
    const resultHeaders = new Headers({
      "Cache-Control": "no-store",
      "X-Request-ID": requestId,
    });
    for (const name of [
      "content-type",
      "content-disposition",
      "location",
      "x-request-id",
    ]) {
      const value = upstream.headers.get(name);
      if (value) resultHeaders.set(name, value);
    }
    for (const cookie of upstream.headers.getSetCookie())
      resultHeaders.append("Set-Cookie", cookie);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: resultHeaders,
    });
  } catch {
    return fail("无法连接后台服务，请稍后重试。");
  }
}
export {
  proxy as GET,
  proxy as POST,
  proxy as PATCH,
  proxy as PUT,
  proxy as DELETE,
};
