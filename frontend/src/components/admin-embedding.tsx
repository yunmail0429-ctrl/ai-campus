"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json } from "@/lib/api";
import { QueryState } from "./query-state";

interface Config {
  provider: string;
  model: string;
  dimensions: number;
  requestDimensions: boolean;
  maxInputTokens: number;
  timeoutSeconds: number;
  minScore: number;
  visionProvider: string | null;
  visionModel: string | null;
  rerankProvider: string | null;
  rerankModel: string | null;
  active: boolean;
  revision: number;
}
interface Overview {
  config: Config | null;
  completedCalls: number;
  inputTokens: number;
  uncertainCalls: number;
}
interface MirrorStatus {
  configured: boolean;
  connected: boolean;
  pendingDocuments: number;
}
export function AdminEmbedding() {
  const client = useQueryClient();
  const [notice, setNotice] = useState("");
  const query = useQuery({
    queryKey: ["embedding-config"],
    queryFn: () => api<Overview>("/admin/embedding"),
  });
  const providers = useQuery({
    queryKey: ["model-providers"],
    queryFn: () =>
      api<{ id: string; label: string; ready: boolean }[]>(
        "/admin/model-providers",
      ),
  });
  const mirror = useQuery({
    queryKey: ["qdrant-status"],
    queryFn: () => api<MirrorStatus>("/admin/embedding/qdrant"),
    refetchInterval: 30_000,
  });
  const save = useMutation({
    mutationFn: (data: unknown) => api("/admin/embedding", json("PUT", data)),
    onSuccess: () => {
      setNotice("向量模型配置已保存，后台会为已解析资料建立或更新索引。");
      void client.invalidateQueries({ queryKey: ["embedding-config"] });
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const check = useMutation({
    mutationFn: () => api("/admin/embedding/check", json("POST")),
    onSuccess: () => {
      setNotice("向量服务连接和返回维度检查通过。");
      void client.invalidateQueries({ queryKey: ["embedding-config"] });
    },
  });
  const config = query.data?.config;
  return (
    <section
      id="embedding-settings"
      className="work-panel embedding-settings ai-service-panel"
      aria-label="知识库向量模型"
    >
      <div className="ai-settings-section-heading">
        <div>
          <span className="ai-settings-eyebrow">02 / KNOWLEDGE</span>
          <h2>知识库检索</h2>
          <p>配置向量模型、检索阈值和可选的识别服务。</p>
        </div>
        <span className={`ai-model-state ${config?.active ? "is-active" : "is-inactive"}`}>
          {config?.active ? "已启用" : "未启用"}
        </span>
      </div>
      <p className="knowledge-intro">
        支持学校本地模型或已配置的外部服务。入库和检索不扣教师积分；选择付费外部服务时，其实际费用由学校承担。
      </p>
      <QueryState
        pending={query.isPending || providers.isPending}
        error={query.error || providers.error}
        retry={() => void query.refetch()}
      />
      {query.data && (
        <>
          <div className="ai-embedding-stats">
            <div><span>已完成调用</span><strong>{query.data.completedCalls.toLocaleString("zh-CN")}</strong><small>次</small></div>
            <div><span>已记录输入</span><strong>{query.data.inputTokens.toLocaleString("zh-CN")}</strong><small>Token</small></div>
            <div><span>计量未确认</span><strong>{query.data.uncertainCalls.toLocaleString("zh-CN")}</strong><small>次</small></div>
          </div>
          {mirror.data?.configured && (
            <p className="knowledge-meta" role="status">
              Qdrant {mirror.data.connected ? "已连接" : "暂不可用，使用 PostgreSQL 回退"} · 待同步 {mirror.data.pendingDocuments} 份资料
            </p>
          )}
          <form
            key={config?.revision || 0}
            className="embedding-form"
            onSubmit={(event) => {
              event.preventDefault();
              setNotice("");
              const form = new FormData(event.currentTarget);
              save.mutate({
                provider: form.get("provider"),
                model: form.get("model"),
                dimensions: Number(form.get("dimensions")),
                requestDimensions: form.has("requestDimensions"),
                active: form.has("active"),
                maxInputTokens: Number(form.get("maxInputTokens")),
                timeoutSeconds: Number(form.get("timeoutSeconds")),
                minScore: Number(form.get("minScore")),
                visionProvider: form.get("visionProvider") || null,
                visionModel: form.get("visionProvider") ? form.get("visionModel") || null : null,
                rerankProvider: form.get("rerankProvider") || null,
                rerankModel: form.get("rerankProvider") ? form.get("rerankModel") || null : null,
                expectedRevision: config?.revision || 0,
              });
            }}
          >
            <label>
              向量服务
              <select
                name="provider"
                required
                defaultValue={config?.provider || ""}
              >
                <option value="">选择学校已配置的服务</option>
                {providers.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                    {p.ready ? "" : "（连接未配置）"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              向量模型标识
              <input
                name="model"
                required
                maxLength={120}
                defaultValue={config?.model}
                placeholder="填写部署中的嵌入模型名称"
              />
            </label>
            <label>
              向量维度
              <input
                name="dimensions"
                type="number"
                required
                min={1}
                max={4096}
                defaultValue={config?.dimensions || 1024}
              />
            </label>
            <label>
              单条输入上限
              <input
                name="maxInputTokens"
                type="number"
                required
                min={256}
                max={32000}
                defaultValue={config?.maxInputTokens || 8192}
              />
            </label>
            <label>
              请求超时（秒）
              <input
                name="timeoutSeconds"
                type="number"
                required
                min={5}
                max={120}
                defaultValue={config?.timeoutSeconds || 30}
              />
            </label>
            <label>
              检索相似度下限
              <input
                name="minScore"
                type="number"
                required
                min={0}
                max={1}
                step={0.01}
                defaultValue={config?.minScore ?? 0.35}
              />
            </label>
            <label className="embedding-checkbox">
              <input
                name="requestDimensions"
                type="checkbox"
                defaultChecked={config?.requestDimensions}
              />
              请求指定维度（服务支持时启用）
            </label>
            <label>
              复杂图片与图表识别服务（可选）
              <select name="visionProvider" defaultValue={config?.visionProvider || ""}>
                <option value="">只使用本机 OCR</option>
                {providers.data?.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </label>
            <label>
              视觉模型标识
              <input name="visionModel" maxLength={120} defaultValue={config?.visionModel || ""} placeholder="支持图像输入的模型" />
            </label>
            <label>
              检索重排服务（可选）
              <select name="rerankProvider" defaultValue={config?.rerankProvider || ""}>
                <option value="">使用融合排序</option>
                {providers.data?.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </label>
            <label>
              重排模型标识
              <input name="rerankModel" maxLength={120} defaultValue={config?.rerankModel || ""} placeholder="支持 /rerank 的模型" />
            </label>
            <label className="embedding-checkbox">
              <input
                name="active"
                type="checkbox"
                defaultChecked={config?.active}
              />
              启用知识库向量检索
            </label>
            <div className="knowledge-actions">
              <button className="soft-button" disabled={save.isPending}>
                保存向量配置
              </button>
              <button
                type="button"
                className="soft-button"
                disabled={!config || check.isPending}
                onClick={() => {
                  setNotice("");
                  check.mutate();
                }}
              >
                检查向量连接
              </button>
            </div>
          </form>
        </>
      )}
      <QueryState pending={false} error={save.error || check.error} />
      {notice && (
        <p role="status" className="work-status">
          {notice}
        </p>
      )}
    </section>
  );
}
