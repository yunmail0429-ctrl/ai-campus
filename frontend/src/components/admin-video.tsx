"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json } from "@/lib/api";
import { QueryState } from "./query-state";

interface VideoConfig {
  model: string;
  endpoint: string;
  resolution: string;
  ratio: string;
  duration: number;
  creditsPerSecond: number;
  active: boolean;
  revision: number;
}
export function AdminVideo() {
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ["admin-video"],
    queryFn: () =>
      api<{ config: VideoConfig | null; connectionReady: boolean }>(
        "/admin/video",
      ),
  });
  const save = useMutation({
    mutationFn: (value: unknown) => api("/admin/video", json("PUT", value)),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["admin-video"] });
      void cache.invalidateQueries({ queryKey: ["resource-capabilities"] });
    },
  });
  const config = query.data?.config;
  return (
    <section id="video-settings" className="work-panel ai-service-panel ai-video-panel">
      <div className="ai-settings-section-heading">
        <div>
          <span className="ai-settings-eyebrow">03 / VIDEO GENERATION</span>
          <h2>AI 动态视频</h2>
          <p>通义万相 · 设置文生视频规格、服务地域与积分费率。</p>
        </div>
        <span className={`ai-model-state ${config?.active ? "is-active" : "is-inactive"}`}>
          {config?.active ? "已启用" : "未启用"}
        </span>
      </div>
      <QueryState
        pending={query.isPending}
        error={query.error}
        retry={() => void query.refetch()}
      />
      {query.data && (
        <form
          key={config?.revision ?? 0}
          className="ai-video-form"
          onSubmit={(event) => {
            event.preventDefault();
            const fields = new FormData(event.currentTarget);
            save.mutate({
              model: fields.get("model"),
              endpoint: fields.get("endpoint"),
              resolution: fields.get("resolution"),
              ratio: fields.get("ratio"),
              duration: Number(fields.get("duration")),
              creditsPerSecond: Number(fields.get("creditsPerSecond")),
              active: fields.get("active") === "on",
              expectedRevision: config?.revision ?? 0,
            });
          }}
        >
          <div className="ai-video-group-title"><strong>生成规格</strong><span>这些参数将作为新任务的默认设置</span></div>
          <div className="ai-video-fields">
          <label>
            视频模型
            <select name="model" defaultValue={config?.model ?? "wan2.7-t2v"}>
              <option>wan2.7-t2v</option>
              <option>wan2.7-t2v-2026-06-12</option>
            </select>
          </label>
          <label>
            服务地域
            <select
              name="endpoint"
              defaultValue={
                config?.endpoint ?? "https://dashscope.aliyuncs.com"
              }
            >
              <option value="https://dashscope.aliyuncs.com">北京</option>
              <option value="https://dashscope-intl.aliyuncs.com">
                新加坡
              </option>
            </select>
          </label>
          <label>
            视频分辨率
            <select
              name="resolution"
              defaultValue={config?.resolution ?? "720P"}
            >
              <option>720P</option>
              <option>1080P</option>
            </select>
          </label>
          <label>
            画面比例
            <select name="ratio" defaultValue={config?.ratio ?? "16:9"}>
              {["16:9", "9:16", "1:1", "4:3", "3:4"].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            视频时长（秒）
            <input
              name="duration"
              type="number"
              required
              min={2}
              max={15}
              step={1}
              defaultValue={config?.duration ?? 5}
            />
          </label>
          <label>
            每秒消耗积分
            <input
              name="creditsPerSecond"
              type="number"
              required
              min={0}
              max={1000000}
              step={1}
              defaultValue={config?.creditsPerSecond ?? ""}
            />
          </label>
          </div>
          <p className="ai-video-hint">每秒消耗积分填 0 表示教师免费使用。分辨率和时长会影响供应商费用，请按学校规则设置。</p>
          {!query.data.connectionReady && (
            <p role="status" className="ai-video-warning">
              <strong>服务连接尚未就绪</strong>
              <span>可先保存参数。启用前需在部署环境配置 WANXIANG_API_KEY，并确保凭据与所选地域一致。</span>
            </p>
          )}
          <div className="ai-video-footer">
          <div className="ai-video-switch-row">
          <label className="ai-video-switch">
            <input
              name="active"
              type="checkbox"
              defaultChecked={config?.active ?? false}
              disabled={!query.data.connectionReady}
            />
            <span><strong>启用视频生成</strong><small>开启后，教师可提交视频任务</small></span>
          </label>
          </div>
          <p className="ai-video-settlement">任务提交时预占积分，成功后结算，失败则释放。生成中取消需等待供应商任务结束，期间积分保持预占。</p>
          {save.error && <p role="alert">{save.error.message}</p>}
          {save.isSuccess && <p role="status">视频配置已保存。</p>}
          <button
            className="start-button"
            disabled={save.isPending || query.isFetching}
          >
            保存视频配置
          </button>
          </div>
        </form>
      )}
    </section>
  );
}
