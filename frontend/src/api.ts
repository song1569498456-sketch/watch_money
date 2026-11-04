import type { Snapshot } from "./types";

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "";
    try {
      const json = await response.json();
      detail =
        typeof json.detail === "string"
          ? json.detail
          : JSON.stringify(json.detail ?? json);
    } catch {
      detail = response.statusText;
    }
    throw new Error(`${response.status} · ${detail || "请求失败"}`);
  }
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}
export const num = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value)
    ? "未提供"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(
        value,
      );
export const pct = (value: number | null | undefined) =>
  value == null
    ? "未提供"
    : `${value > 0 ? "+" : ""}${(value * 100).toFixed(4)}%`;
export const time = (value: number | null | undefined) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未提供";
export const age = (ms: number | null | undefined, now = Date.now()) =>
  !ms
    ? "未接收"
    : Math.max(0, Math.floor((now - ms) / 1000)) < 60
      ? `${Math.max(0, Math.floor((now - ms) / 1000))} 秒前`
      : Math.floor((now - ms) / 60000) < 60
        ? `${Math.floor((now - ms) / 60000)} 分钟前`
        : `${Math.floor((now - ms) / 3600000)} 小时前`;
export const kindName: Record<string, string> = {
  spot: "现货",
  perpetual: "永续",
  pool: "现货池",
  rfq: "RFQ",
};
export const stateName: Record<string, string> = {
  online: "采集中",
  polling: "采集中",
  connected: "已连接",
  healthy: "正常",
  live: "实时",
  ok: "正常",
  idle: "待启动",
  disabled: "已停用",
  error: "采集异常",
  backoff: "退避重试",
  rate_limited: "触发限流",
  blocked: "受限",
  unsupported: "暂不支持",
  implemented: "已实现",
  partial: "部分支持",
  supported: "已支持",
  available: "可用",
  disconnected: "已断开",
  connecting: "连接中",
  data_incomplete: "数据待补全",
  cost_pending: "成本待配置",
  invalidated: "已失效",
  missing_data: "数据待补全",
  missing_costs: "成本待配置",
  watching: "观察中",
  threshold_met: "达到阈值",
  invalid: "已失效",
};

/** Fields that supply the displayed primary price, in the same priority as the API. */
export function primaryPriceFields(snapshot: Snapshot): string[] {
  if (snapshot.bid != null && snapshot.ask != null) return ["bid", "ask"];
  if (snapshot.last != null) return ["last"];
  if (snapshot.mark != null) return ["mark"];
  if (snapshot.index != null) return ["index"];
  return [];
}
export function fieldSourceTimestamp(
  snapshot: Snapshot,
  key: string,
): number | null | undefined {
  return Object.prototype.hasOwnProperty.call(
    snapshot.field_timestamps ?? {},
    key,
  )
    ? snapshot.field_timestamps?.[key]
    : snapshot.source_at;
}
export function fieldTimestamp(snapshot: Snapshot, key: string): number {
  const received = snapshot.field_received_at?.[key] ?? snapshot.received_at;
  return Math.min(received, fieldSourceTimestamp(snapshot, key) ?? received);
}
/** A last-only update cannot refresh the age of an older displayed bid/ask mid. */
export function relevantPriceTimestamp(snapshot?: Snapshot): number {
  if (!snapshot) return 0;
  const fields = primaryPriceFields(snapshot);
  return fields.length
    ? Math.min(...fields.map((key) => fieldTimestamp(snapshot, key)))
    : Math.min(
        snapshot.received_at,
        snapshot.source_at ?? snapshot.received_at,
      );
}
