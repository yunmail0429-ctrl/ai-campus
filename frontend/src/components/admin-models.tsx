"use client";
import { AdminEmbedding } from "./admin-embedding";
import { AdminVideo } from "./admin-video";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json, PageResult } from "@/lib/api";
import { QueryState } from "./query-state";

interface Provider {
  id: string;
  label: string;
  ready: boolean;
}
interface Model {
  id: string;
  name: string;
  provider: string;
  model: string;
  apiBaseUrl: string | null;
  keyConfigured: boolean;
  active: boolean;
  contextTokens: number;
  maxOutputTokens: number;
  timeoutSeconds: number;
  inputPrice: number;
  outputPrice: number;
  revision: number;
  default: boolean;
  connectionReady: boolean;
}
interface Check {
  connected: boolean;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
}
type ModelInput = Omit<
  Model,
  "id" | "revision" | "default" | "connectionReady" | "keyConfigured"
> & { apiKey?: string | null };

const directProviderUrls: Record<string, string> = {
  deepseek: "https://api.deepseek.com",
  doubao: "https://ark.cn-beijing.volces.com/api/v3",
  glm: "https://open.bigmodel.cn/api/paas/v4",
  claude: "https://api.anthropic.com/v1",
  openai: "https://api.openai.com/v1",
  custom: "",
};

export function AdminModels() {
  const cache = useQueryClient();
  const dialog = useRef<HTMLDialogElement>(null);
  const [editing, setEditing] = useState<Model | null>(null);
  const [providerChoice, setProviderChoice] = useState("");
  const [formKey, setFormKey] = useState(0);
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const providers = useQuery({
    queryKey: ["model-providers"],
    queryFn: () => api<Provider[]>("/admin/model-providers"),
  });
  const models = useQuery({
    queryKey: ["admin-models", cursor],
    queryFn: () =>
      api<PageResult<Model>>(
        `/admin/models?limit=20${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`,
      ),
  });
  function refresh() {
    void cache.invalidateQueries({ queryKey: ["admin-models"] });
    void cache.invalidateQueries({ queryKey: ["audit"] });
  }
  const save = useMutation({
    mutationFn: (data: ModelInput) =>
      api(
        editing ? `/admin/models/${editing.id}` : "/admin/models",
        json(
          editing ? "PUT" : "POST",
          editing ? { ...data, expectedRevision: editing.revision } : data,
        ),
      ),
    onSuccess: () => {
      dialog.current?.close();
      refresh();
    },
  });
  const select = useMutation({
    mutationFn: (id: string) =>
      api(`/admin/models/${id}/default`, json("POST")),
    onSuccess: refresh,
  });
  const toggle = useMutation({
    mutationFn: (model: Model) =>
      api(
        `/admin/models/${model.id}/state`,
        json("PATCH", {
          active: !model.active,
          expectedRevision: model.revision,
        }),
      ),
    onSuccess: refresh,
  });
  const check = useMutation({
    mutationFn: (id: string) =>
      api<Check>(`/admin/models/${id}/check`, json("POST")),
    onSettled: () => void cache.invalidateQueries({ queryKey: ["audit"] }),
  });
  function open(model: Model | null) {
    setEditing(model);
    setProviderChoice(model?.provider || "custom");
    setFormKey((value) => value + 1);
    save.reset();
    dialog.current?.showModal();
  }
  return (
    <section className="ai-settings-page">
      <header className="ai-settings-hero">
        <div>
          <span className="ai-settings-eyebrow">学校管理 / AI 服务</span>
          <h1>AI 服务设置</h1>
          <p>统一管理对话模型、知识库检索与视频生成服务。</p>
        </div>
        <nav aria-label="AI 服务设置分区" className="ai-settings-nav">
          <a href="#school-models">对话模型</a>
          <a href="#embedding-settings">知识库检索</a>
          <a href="#video-settings">视频生成</a>
        </nav>
      </header>
      <div id="school-models" className="ai-settings-section-heading">
        <div>
          <span className="ai-settings-eyebrow">01 / CONVERSATION</span>
          <h2>对话模型</h2>
          <p>设置默认模型、调用限制与教师积分费率。</p>
        </div>
        <button
          className="start-button"
          disabled={!providers.data?.length}
          onClick={() => open(null)}
        >
          ＋ 添加模型
        </button>
      </div>
      <p className="ai-settings-note">新请求使用默认模型；已开始的任务仍按提交时的费率结算。</p>
      <QueryState
        pending={models.isPending || providers.isPending}
        error={models.error || providers.error || select.error || toggle.error}
        retry={() => {
          void models.refetch();
          void providers.refetch();
        }}
      />
      {providers.data?.length === 0 && (
        <p className="work-status">
          学校尚未接入模型供应商，请联系部署人员配置服务连接和凭据。
        </p>
      )}
      {models.data?.items.length === 0 && (
        <p className="work-status">
          暂无模型配置。添加并启用模型后，可将它设为默认对话模型。
        </p>
      )}
      <div className="admin-records">
        {models.data?.items.map((model) => (
          <article key={model.id} className="ai-model-card">
            <div className="ai-model-topline">
              <div>
                <h3>{model.name}</h3>
                <p className="ai-model-identity">
                  {providers.data?.find((p) => p.id === model.provider)?.label || model.provider}
                  <span aria-hidden="true"> / </span>{model.model}
                </p>
                {model.apiBaseUrl && (
                  <p className="ai-model-endpoint">{model.apiBaseUrl} · {model.keyConfigured ? "API Key 已配置" : "API Key 未配置"}</p>
                )}
              </div>
              <span className={`ai-model-state ${model.default ? "is-default" : model.active ? "is-active" : "is-inactive"}`}>
                {model.default
                  ? "默认对话模型"
                  : model.active
                    ? "已启用"
                    : "已停用"}
              </span>
            </div>
            <div className="ai-model-metrics">
              <div><span>上下文上限</span><strong>{model.contextTokens.toLocaleString("zh-CN")}</strong><small>Token</small></div>
              <div><span>单次最大输出</span><strong>{model.maxOutputTokens.toLocaleString("zh-CN")}</strong><small>Token</small></div>
              <div><span>请求超时</span><strong>{model.timeoutSeconds}</strong><small>秒</small></div>
            </div>
            <div className="ai-model-pricing">
              <span className="ai-model-pricing-title">积分费率 <small>每百万 Token</small></span>
              <span>输入 <strong>{model.inputPrice.toLocaleString("zh-CN")}</strong> 积分</span>
              <span>输出 <strong>{model.outputPrice.toLocaleString("zh-CN")}</strong> 积分</span>
            </div>
            {!model.connectionReady && (
              <p className="work-status">
                供应商连接或凭据未配置，暂不可调用。
              </p>
            )}
            <div className="quota-actions ai-model-actions">
              <button className="soft-button" onClick={() => open(model)}>
                编辑配置
              </button>
              <button
                className="soft-button"
                disabled={
                  toggle.isPending || (!model.active && !model.connectionReady)
                }
                onClick={() => {
                  if (
                    !model.default ||
                    window.confirm(
                      "停用默认模型后，新的对话将暂停，直到选择新的默认模型。继续停用？",
                    )
                  )
                    toggle.mutate(model);
                }}
              >
                {model.active ? "停用模型" : "启用模型"}
              </button>
              <button
                className="soft-button"
                disabled={
                  select.isPending ||
                  model.default ||
                  !model.active ||
                  !model.connectionReady
                }
                onClick={() => select.mutate(model.id)}
              >
                设为默认
              </button>
              <button
                className="soft-button"
                disabled={check.isPending || !model.connectionReady}
                onClick={() => check.mutate(model.id)}
              >
                {check.isPending && check.variables === model.id
                  ? "正在检查…"
                  : "检查连接"}
              </button>
            </div>
            {check.variables === model.id && check.error && (
              <p role="alert">{check.error.message}</p>
            )}
            {check.variables === model.id && check.data && (
              <p role="status">
                连接可用 · 耗时 {check.data.latencyMs} 毫秒 · 输入{" "}
                {check.data.inputTokens} / 输出 {check.data.outputTokens} Token
              </p>
            )}
          </article>
        ))}
      </div>
      <p className="quota-explanation">
        连接检查会发送简短测试请求，供应商可能计费，不扣教师积分。每次教学调用按输入与输出合计费用向上取整到整数积分。
      </p>
      {(cursors.length > 0 || Boolean(models.data?.nextCursor)) && <div className="admin-pagination">
        <button
          className="soft-button"
          disabled={!cursors.length}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          上一页
        </button>
        <button
          className="soft-button"
          disabled={!models.data?.nextCursor}
          onClick={() => setCursors([...cursors, models.data!.nextCursor!])}
        >
          下一页
        </button>
      </div>}
      <dialog
        ref={dialog}
        aria-labelledby="modelTitle"
        onCancel={(e) => {
          if (save.isPending) e.preventDefault();
        }}
      >
        <form
          key={formKey}
          onSubmit={(e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            save.mutate({
              name: String(form.get("name")),
              provider: String(form.get("provider")),
              model: String(form.get("model")),
              apiBaseUrl: providerChoice in directProviderUrls ? String(form.get("apiBaseUrl") || "") : null,
              apiKey: providerChoice in directProviderUrls ? String(form.get("apiKey") || "") || null : null,
              active: form.get("active") === "on",
              contextTokens: Number(form.get("contextTokens")),
              maxOutputTokens: Number(form.get("maxOutputTokens")),
              timeoutSeconds: Number(form.get("timeoutSeconds")),
              inputPrice: Number(form.get("inputPrice")),
              outputPrice: Number(form.get("outputPrice")),
            });
          }}
        >
          <div className="section-heading">
            <h2 id="modelTitle">{editing ? "编辑模型" : "添加模型"}</h2>
            <button
              type="button"
              className="soft-button"
              aria-label="关闭"
              disabled={save.isPending}
              onClick={() => dialog.current?.close()}
            >
              ×
            </button>
          </div>
          <label>
            显示名称
            <input
              name="name"
              required
              maxLength={80}
              defaultValue={editing?.name}
            />
          </label>
          <label>
            供应商
            <select
              name="provider"
              required
              defaultValue={editing?.provider || "custom"}
              onChange={(event) => setProviderChoice(event.target.value)}
            >
              <option value="" disabled>
                请选择供应商
              </option>
              {providers.data?.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.label}
                  {provider.ready ? "" : "（凭据未配置）"}
                </option>
              ))}
            </select>
          </label>
          {providerChoice in directProviderUrls && (
            <div className="ai-model-connection-fields">
              <p>填写 API 基础地址，系统会调用其 /chat/completions。模型标识以供应商控制台为准。</p>
              {providerChoice === "claude" && <p>Claude 的 OpenAI 兼容接口由 Anthropic 定位为测试和对比用途；生产使用建议接入原生 Messages API。</p>}
              <label>
                API 地址
                <input key={providerChoice} name="apiBaseUrl" type="url" required maxLength={500}
                  defaultValue={editing?.provider === providerChoice ? editing.apiBaseUrl || "" : directProviderUrls[providerChoice]}
                  placeholder="https://example.com/v1" autoComplete="off" />
              </label>
              <label>
                API Key
                <input name="apiKey" type="password" required={!editing?.keyConfigured}
                  placeholder={editing?.keyConfigured ? "已配置；留空则保持不变" : "填写服务商提供的密钥"}
                  autoComplete="new-password" />
              </label>
            </div>
          )}
          <label>
            模型标识
            <input
              name="model"
              required
              maxLength={120}
              defaultValue={editing?.model}
              placeholder="填写供应商提供的模型标识"
            />
          </label>
          <label>
            上下文上限（Token）
            <input
              name="contextTokens"
              type="number"
              min={256}
              max={1_000_000}
              step={1}
              required
              defaultValue={editing?.contextTokens}
            />
          </label>
          <label>
            单次最大输出（Token）
            <input
              name="maxOutputTokens"
              type="number"
              min={1}
              max={100_000}
              step={1}
              required
              defaultValue={editing?.maxOutputTokens}
            />
          </label>
          <label>
            超时秒数
            <input
              name="timeoutSeconds"
              type="number"
              min={10}
              max={300}
              step={1}
              required
              defaultValue={editing?.timeoutSeconds || 90}
            />
          </label>
          <label>
            每百万输入 Token 的积分
            <input
              name="inputPrice"
              type="number"
              min={0}
              max={100_000_000}
              step={1}
              required
              defaultValue={editing?.inputPrice}
            />
          </label>
          <label>
            每百万输出 Token 的积分
            <input
              name="outputPrice"
              type="number"
              min={0}
              max={100_000_000}
              step={1}
              required
              defaultValue={editing?.outputPrice}
            />
          </label>
          <label className="model-enabled">
            <input
              name="active"
              type="checkbox"
              defaultChecked={editing?.active || false}
            />
            启用模型
          </label>
          {save.error && <p role="alert">{save.error.message}</p>}
          <button className="start-button" disabled={save.isPending}>
            保存模型
          </button>
        </form>
      </dialog>
      <AdminEmbedding />
      <AdminVideo />
    </section>
  );
}
