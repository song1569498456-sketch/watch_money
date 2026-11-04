import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownUp,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Database,
  ExternalLink,
  Globe,
  Layers,
  LayoutDashboard,
  Loader2,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  ShieldCheck,
  SlidersHorizontal,
  Star,
  Wifi,
  WifiOff,
  X,
  Calculator,
  Save,
  Trash2,
  Send,
  Clock,
  Wallet,
  Radio,
  AlertTriangle,
} from "lucide-react";
import {
  api,
  age,
  kindName,
  num,
  pct,
  stateName,
  time,
  relevantPriceTimestamp,
  primaryPriceFields,
  fieldSourceTimestamp,
  fieldTimestamp,
} from "./api";
import type {
  Asset,
  Book,
  Coverage,
  Instrument,
  Json,
  MarketData,
  Settings,
  Snapshot,
  VenueStatus,
} from "./types";
import { IdentityCandidates } from "./IdentityCandidates";
import { NativePoolPanel } from "./NativePoolPanel";
import { ScannerPage } from "./ScannerPage";
import { fundingHistorySeries, marketPriceSummary } from "./marketData";
import { bookBaseForNotional } from "./bookSizing";
import { FundingPage } from "./FundingPage";
import { FeeProfiles, FeeCostFields, useTradeFees } from "./FeeProfiles";
import "./style.css";

type Page =
  | "markets"
  | "opportunities"
  | "scanner"
  | "funding"
  | "status"
  | "notifications"
  | "settings"
  | "detail";
const titles: Record<Page, [string, string]> = {
  markets: ["市场总览", "跨平台观察价格、资金费率与市场流动性"],
  detail: ["资产详情", "核对市场身份、原始费率与数据来源"],
  opportunities: ["收益计算", "以当前行情计算执行成本与持有情景"],
  funding: ["资金费率", "全目录原始费率、周期与跨平台比较"],
  scanner: ["全市场扫描", "从已发现市场筛选指示价差与资金费差候选"],
  status: ["平台与数据源", "连接诊断、真实覆盖范围和接入条件"],
  notifications: ["通知中心", "管理机会与系统提醒，追踪每次发送"],
  settings: ["工作台配置", "控制采集节奏、关注范围与历史保存"],
};
const navigation: [Page, typeof Activity, string][] = [
  ["markets", LayoutDashboard, "市场总览"],
  ["scanner", Search, "全市场扫描"],
  ["funding", Activity, "资金费率"],
  ["opportunities", Calculator, "收益计算"],
  ["notifications", Bell, "通知中心"],
  ["status", Radio, "平台状态"],
  ["settings", SettingsIcon, "配置"],
];
function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function Empty({
  title = "暂无数据",
  text = "数据到达后将在这里显示。",
  icon: Icon = Database,
}: {
  title?: string;
  text?: string;
  icon?: typeof Database;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={26} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function Notice({
  children,
  error = false,
}: {
  children: React.ReactNode;
  error?: boolean;
}) {
  return (
    <div
      role={error ? "alert" : "status"}
      className={`notice ${error ? "error" : ""}`}
    >
      <AlertTriangle size={16} />
      <span>{children}</span>
    </div>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function DataAge({
  snapshot,
  now,
  staleSeconds,
}: {
  snapshot?: Snapshot;
  now: number;
  staleSeconds: number;
}) {
  if (!snapshot) return <Badge>未接收</Badge>;
  const timestamp = relevantPriceTimestamp(snapshot);
  const fields = primaryPriceFields(snapshot);
  const sourceKnown = fields.length
    ? fields.every((key) => fieldSourceTimestamp(snapshot, key) != null)
    : snapshot.source_at != null;
  const old = !!snapshot.stale || now - timestamp > staleSeconds * 1000;
  return (
    <span
      title={`显示价格字段时间 ${time(timestamp)} · 最新消息来源 ${time(snapshot.source_at)} · 最新消息接收 ${time(snapshot.received_at)}`}
      className={old ? "muted warning" : "muted"}
    >
      <i className={`dot ${old ? "amber" : "green"}`} />
      {age(timestamp, now)}
      {!sourceKnown ? " · 来源时间未提供" : ""}
      {old ? " · 过期" : ""}
      {!snapshot.valid ? " · 无效" : ""}
      {snapshot.is_demo && <Badge tone="amber">演示</Badge>}
    </span>
  );
}
function Countdown({ at, now }: { at?: number | null; now: number }) {
  if (!at) return <>未提供</>;
  const diff = Math.max(0, Math.floor((at - now) / 1000));
  return (
    <span title={time(at)}>
      {diff === 0
        ? "等待更新"
        : `${String(Math.floor(diff / 3600)).padStart(2, "0")}:${String(Math.floor((diff % 3600) / 60)).padStart(2, "0")}:${String(diff % 60).padStart(2, "0")}`}
    </span>
  );
}
function App() {
  const [page, setPage] = useState<Page>("markets");
  const [markets, setMarkets] = useState<MarketData>({
    instruments: [],
    snapshots: [],
  });
  const [statuses, setStatuses] = useState<VenueStatus[]>([]);
  const [coverage, setCoverage] = useState<Coverage[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [favorites, setFavorites] = useState<string[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [wsState, setWsState] = useState("connecting");
  const [now, setNow] = useState(Date.now());
  const [selectedAsset, setSelectedAsset] = useState("");
  const [scanSelection, setScanSelection] = useState<{buy_instrument_id: string; sell_instrument_id: string; strategy: string} | null>(null);
  const [toast, setToast] = useState("");
  const latest = useRef<MarketData | null>(null);
  const refresh = useRef(1000);
  refresh.current = settings?.refresh_ms ?? 1000;
  const error = (key: string, value: string) =>
    setErrors((old) => ({ ...old, [key]: value }));
  const load = async () => {
    setLoading(true);
    const resources: [string, string, (value: any) => void][] = [
      ["行情", "/api/markets", setMarkets],
      [
        "状态",
        "/api/status",
        (value) => {
          setStatuses(value.venues ?? []);
          setCoverage(value.coverage ?? []);
        },
      ],
      ["配置", "/api/settings", setSettings],
      [
        "收藏",
        "/api/favorites",
        (value) => setFavorites(value.instrument_ids ?? []),
      ],
      [
        "资产",
        "/api/assets",
        (value) =>
          setAssets(Array.isArray(value) ? value : (value.assets ?? [])),
      ],
    ];
    await Promise.allSettled(
      resources.map(async ([key, path, set]) => {
        try {
          set(await api(path));
          error(key, "");
        } catch (e) {
          error(key, String(e instanceof Error ? e.message : e));
        }
      }),
    );
    setLoading(false);
  };
  useEffect(() => {
    void load();
    let stopped = false;
    let reconnect: ReturnType<typeof setTimeout>;
    let socket: WebSocket;
    let streamData: MarketData | null = null;
    const connect = () => {
      setWsState("connecting");
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`,
      );
      socket.onopen = () => setWsState("connected");
      socket.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          if (data.type === "markets") {
            streamData = data.data ?? data.payload ?? data;
            latest.current = streamData;
          } else if (data.type === "markets_delta" && streamData) {
            const instruments = new Map(streamData.instruments.map(i => [i.id, i]));
            const snapshots = new Map(streamData.snapshots.map(s => [s.instrument_id, s]));
            for (const id of data.removed_instruments ?? []) instruments.delete(id);
            for (const id of data.removed_snapshots ?? []) snapshots.delete(id);
            for (const item of data.instruments ?? []) instruments.set(item.id, item);
            for (const item of data.snapshots ?? []) snapshots.set(item.instrument_id, item);
            streamData = {...streamData, mode: data.mode,
              instruments: Array.from(instruments.values()), snapshots: Array.from(snapshots.values())};
            latest.current = streamData;
          }
        } catch {
          /* Invalid messages never replace current market data. */
        }
      };
      socket.onerror = () => setWsState("disconnected");
      socket.onclose = () => {
        setWsState("disconnected");
        if (!stopped) reconnect = setTimeout(connect, 5000);
      };
    };
    connect();
    let last = 0;
    const timer = setInterval(() => {
      const timestamp = Date.now();
      setNow(timestamp);
      if (latest.current && timestamp - last >= refresh.current) {
        setMarkets(latest.current);
        latest.current = null;
        last = timestamp;
      }
    }, 500);
    const poll = setInterval(() => {
      void api<{ venues: VenueStatus[]; coverage: Coverage[] }>("/api/status")
        .then((data) => {
          setStatuses(data.venues);
          setCoverage(data.coverage);
          error("状态", "");
        })
        .catch((e) => error("状态", e.message));
      if (stopped) return;
      if (socket?.readyState !== WebSocket.OPEN)
        void api<MarketData>("/api/markets")
          .then((data) => {
            setMarkets(data);
            error("行情", "");
          })
          .catch((e) => error("行情", e.message));
    }, 15000);
    return () => {
      stopped = true;
      clearInterval(timer);
      clearInterval(poll);
      clearTimeout(reconnect);
      socket?.close();
    };
  }, []);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 4000);
      return () => clearTimeout(t);
    }
  }, [toast]);
  const snapshots = useMemo(
    () => new Map(markets.snapshots.map((s) => [s.instrument_id, s])),
    [markets.snapshots],
  );
  const identityVersion = useMemo(() => markets.instruments.map((item) =>
    `${item.id}:${item.asset_id}:${item.native_asset_id ?? ""}:${item.mapping_verified}`).sort().join("|"),
    [markets.instruments]);
  const toggleFavorite = async (id: string) => {
    const next = favorites.includes(id)
      ? favorites.filter((x) => x !== id)
      : [...favorites, id];
    try {
      const data = await api<{ instrument_ids: string[] }>(
        "/api/favorites",
        "PUT",
        { instrument_ids: next },
      );
      setFavorites(data.instrument_ids);
    } catch (e) {
      setToast(`收藏保存失败：${(e as Error).message}`);
    }
  };
  const openAsset = (id: string) => {
    setSelectedAsset(id);
    setPage("detail");
  };
  const fresh = markets.snapshots.filter(
    (s) =>
      s.valid &&
      !s.stale &&
      now - relevantPriceTimestamp(s) < (settings?.stale_seconds ?? 60) * 1000,
  ).length;
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("markets");
          }}
        >
          <span className="brand-mark">
            <BarChart3 size={22} />
          </span>
          <span>
            WATCH MONEY<small>市场观察台</small>
          </span>
        </a>
        <div className="workspace-label">
          WORKSPACE <span>本地</span>
        </div>
        <nav aria-label="主导航">
          {navigation.map(([id, Icon, label]) => (
            <button
              key={id}
              className={
                page === id || (page === "detail" && id === "markets")
                  ? "nav-item active"
                  : "nav-item"
              }
              onClick={() => setPage(id)}
            >
              <Icon size={19} />
              <span>{label}</span>
              {id === "markets" && (
                <span className="nav-count">
                  {new Set(markets.instruments.map((i) => i.asset_id)).size}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-card">
            <ShieldCheck size={19} />
            <div>
              公开行情 · 本地运行<small>不绑定账户或固定本金</small>
            </div>
          </div>
          <span className="version">
            WATCH MONEY <span>v0.1.0</span>
          </span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            工作台 <ChevronRight size={13} />
            <strong>{titles[page][0]}</strong>
          </div>
          <div className="topbar-right">
            <span
              className="connection"
              title="后端到当前页面的行情推送连接；平台数据源连接详见平台状态"
            >
              {wsState === "connected" ? (
                <Wifi size={14} />
              ) : (
                <WifiOff size={14} />
              )}
              <i
                className={`dot ${wsState === "connected" ? "green" : "amber"}`}
              />
              {wsState === "connected"
                ? "实时推送已连接"
                : wsState === "connecting"
                  ? "正在连接"
                  : "推送断开 · REST 轮询"}
            </span>
            <button
              className="icon-button"
              aria-label="刷新全部数据"
              title="刷新全部数据"
              disabled={loading}
              onClick={() => void load()}
            >
              <RefreshCw size={17} className={loading ? "spin" : ""} />
            </button>
            <span className="avatar">WM</span>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">MARKET INTELLIGENCE</span>
              <h1>{titles[page][0]}</h1>
              <p>{titles[page][1]}</p>
            </div>
            <div className="heading-note">
              <i className="dot green" /> 界面刷新{" "}
              {settings ? `${settings.refresh_ms / 1000}s` : "—"}
              <small>数据时效以来源时间为准</small>
            </div>
          </div>
          {Object.entries(errors).filter(([, e]) => e).length > 0 && (
            <Notice error>
              {Object.entries(errors)
                .filter(([, e]) => e)
                .map(([k, e]) => `${k}：${e}`)
                .join("；")}
              <button className="text-button" onClick={() => void load()}>
                重试
              </button>
            </Notice>
          )}
          {settings?.demo_mode && (
            <Notice>
              演示模式已显式开启。标记为「演示」的数据不可用于实际决策。
              <button
                className="text-button"
                onClick={() => setPage("settings")}
              >
                管理配置
              </button>
            </Notice>
          )}
          {page === "markets" && (
            <>
              <div className="stats">
                <Stat
                  label="观察资产"
                  value={num(
                    new Set(markets.instruments.map((i) => i.asset_id)).size,
                    0,
                  )}
                  icon={Layers}
                  detail={`${markets.instruments.length} 个独立市场`}
                />
                <Stat
                  label="有效行情"
                  value={num(fresh, 0)}
                  icon={Activity}
                  detail={`共 ${markets.snapshots.length} 条快照 · 依照来源时间`}
                />
                <Stat
                  label="已启用平台"
                  value={num(settings?.enabled_venues.length ?? null, 0)}
                  icon={Globe}
                  detail={`${coverage.length} 个候选数据源已登记`}
                />
                <Stat
                  label="我的收藏"
                  value={num(favorites.length, 0)}
                  icon={Star}
                  detail="收藏市场跨会话保留"
                />
              </div>
              <IdentityCandidates refreshKey={identityVersion} />
              <Markets
                instruments={markets.instruments}
                snapshots={snapshots}
                assets={assets}
                favorites={favorites}
                toggleFavorite={toggleFavorite}
                openAsset={openAsset}
                now={now}
                staleSeconds={settings?.stale_seconds ?? 60}
              />
            </>
          )}
          {page === "detail" && (
            <Detail
              assetId={selectedAsset}
              instruments={markets.instruments}
              snapshots={snapshots}
              now={now}
              staleSeconds={settings?.stale_seconds ?? 60}
              back={() => setPage("markets")}
              favorites={favorites}
              toggleFavorite={toggleFavorite}
            />
          )}
          {page === "scanner" && <ScannerPage instruments={markets.instruments} onSelectCandidate={(candidate) => {setScanSelection(candidate); setPage("opportunities");}} />}
          {page === "funding" && <FundingPage />}
          {page === "status" && (
            <Status statuses={statuses} coverage={coverage} now={now} />
          )}
          {page === "settings" && (
            <>
              <SettingsPage
                settings={settings}
                coverage={coverage}
                onSave={(data) => {
                  setSettings(data);
                  setToast("配置已保存");
                }}
              />
              <NativePoolPanel settings={settings} />
              <IdentityCandidates refreshKey={identityVersion} />
              <AssetMappings
                instruments={markets.instruments}
                assets={assets}
                onSave={() => {
                  void load();
                  setToast("资产映射已更新，审计记录已保存");
                }}
              />
            </>
          )}
          {page === "opportunities" && (
            <Opportunities
              initial={scanSelection}
              instruments={markets.instruments}
              snapshots={snapshots}
              toast={setToast}
            />
          )}
          {page === "notifications" && <Notifications toast={setToast} />}
          <footer>
            <span>
              <ShieldCheck size={13} /> 只读市场观察 · 不执行交易
            </span>
            <span>
              UTC {new Date(now).toISOString().slice(11, 19)} ·
              缺失数值均显示为未提供
            </span>
          </footer>
        </div>
      </main>
      {toast && (
        <div className="toast" role="status">
          <Check size={17} />
          {toast}
          <button aria-label="关闭提示" onClick={() => setToast("")}>
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}
function Stat({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof Activity;
}) {
  return (
    <div className="stat">
      <span className="stat-label">
        {label}
        <Icon size={17} />
      </span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
function Markets({
  instruments,
  snapshots,
  assets,
  favorites,
  toggleFavorite,
  openAsset,
  now,
  staleSeconds,
}: {
  instruments: Instrument[];
  snapshots: Map<string, Snapshot>;
  assets: Asset[];
  favorites: string[];
  toggleFavorite: (id: string) => void;
  openAsset: (id: string) => void;
  now: number;
  staleSeconds: number;
}) {
  const [query, setQuery] = useState("");
  const [venue, setVenue] = useState("");
  const [chain, setChain] = useState("");
  const [kind, setKind] = useState("");
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [sort, setSort] = useState("name");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const filtered = instruments.filter(
    (i) =>
      (!venue || i.venue === venue) &&
      (!chain || i.chain === chain) &&
      (!kind || i.kind === kind) &&
      (!onlyFavorites || favorites.includes(i.id)) &&
      `${i.base} ${i.asset_id} ${i.native_asset_id ?? ""} ${i.venue} ${i.native_symbol}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const groups = Array.from(
    filtered.reduce(
      (map, i) => map.set(i.asset_id, [...(map.get(i.asset_id) ?? []), i]),
      new Map<string, Instrument[]>(),
    ),
  ).sort((a, b) =>
    sort === "count"
      ? b[1].length - a[1].length
      : sort === "freshness"
        ? Math.max(
            ...b[1].map((i) => relevantPriceTimestamp(snapshots.get(i.id))),
          ) -
          Math.max(
            ...a[1].map((i) => relevantPriceTimestamp(snapshots.get(i.id))),
          )
        : a[1][0].base.localeCompare(b[1][0].base),
  );
  const toggleGroup = (id: string) =>
    setExpanded((old) => {
      const next = new Set(old);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  return (
    <section className="panel markets-panel">
      <div className="panel-heading">
        <div>
          <h2>
            资产与市场 <span className="count">{groups.length}</span>
          </h2>
          <p>按内部资产身份分组 · 未确认市场保留独立身份 · 搜索覆盖全部已发现币种</p>
        </div>
        <button
          className={`button secondary ${onlyFavorites ? "selected" : ""}`}
          onClick={() => setOnlyFavorites(!onlyFavorites)}
        >
          <Star size={15} fill={onlyFavorites ? "currentColor" : "none"} />{" "}
          仅看收藏
        </button>
      </div>
      <div className="filters">
        <div className="search">
          <Search size={17} />
          <input
            aria-label="搜索资产或市场"
            placeholder="搜索资产、市场或平台…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button aria-label="清除搜索" onClick={() => setQuery("")}>
              <X size={14} />
            </button>
          )}
        </div>
        <select
          aria-label="筛选平台"
          value={venue}
          onChange={(e) => setVenue(e.target.value)}
        >
          <option value="">全部平台</option>
          {[...new Set(instruments.map((i) => i.venue))].sort().map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <select
          aria-label="筛选链"
          value={chain}
          onChange={(e) => setChain(e.target.value)}
        >
          <option value="">全部链</option>
          {[...new Set(instruments.map((i) => i.chain).filter(Boolean))]
            .sort()
            .map((v) => (
              <option key={v}>{v}</option>
            ))}
        </select>
        <select
          aria-label="筛选市场类型"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          <option value="">全部产品</option>
          {Object.entries(kindName).map(([key, name]) => (
            <option value={key} key={key}>
              {name}
            </option>
          ))}
        </select>
        <select
          aria-label="排序"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
        >
          <option value="name">资产名称排序</option>
          <option value="count">市场数量排序</option>
          <option value="freshness">价格最新优先</option>
        </select>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>资产 / 交易市场</th>
              <th>覆盖平台</th>
              <th>价格与报价币</th>
              <th>资金费率 / 原周期</th>
              <th>成交量 / 单位</th>
              <th>来源数据时间</th>
              <th aria-label="操作" />
            </tr>
          </thead>
          <tbody>
            {groups.map(([id, items]) => {
              const asset = assets.find((a) => a.id === id);
              const open = expanded.has(id);
              const summary = marketPriceSummary(items, snapshots, now, staleSeconds);
              const available = items.filter((i) => snapshots.has(i.id));
              return (
                <React.Fragment key={id}>
                  <tr className="asset-row">
                    <td>
                      <div className="asset-cell">
                        <button
                          className="icon-button"
                          aria-label={`${open ? "收起" : "展开"} ${items[0].base} 市场`}
                          onClick={() => toggleGroup(id)}
                        >
                          {open ? (
                            <ChevronDown size={16} />
                          ) : (
                            <ChevronRight size={16} />
                          )}
                        </button>
                        <span className="coin">
                          {items[0].base.slice(0, 2)}
                        </span>
                        <button
                          className="asset-title"
                          onClick={() => openAsset(id)}
                        >
                          <strong>{items[0].base}</strong>
                          <small>{asset?.name ?? id}</small>
                        </button>
                        {items.every((i) => i.mapping_verified) ? (
                          <ShieldCheck
                            size={14}
                            className="mint"
                            aria-label="已核验映射"
                          />
                        ) : <Badge tone="amber">映射待核验</Badge>}
                      </div>
                    </td>
                    <td>
                      <div className="platform-stack">
                        {[...new Set(items.map((i) => i.venue))]
                          .slice(0, 3)
                          .map((v) => (
                            <Badge key={v}>{v}</Badge>
                          ))}
                        {new Set(items.map((i) => i.venue)).size > 3 && (
                          <span className="muted">
                            +{new Set(items.map((i) => i.venue)).size - 3}
                          </span>
                        )}
                      </div>
                      <small className="muted">
                        {items.length} 个市场 · {available.length} 条行情
                      </small>
                    </td>
                    <td>
                      {summary.ranges.map((range) => (
                        <div key={range.key} className="mono">
                          {num(range.min, 8)}{range.max !== range.min ? ` – ${num(range.max, 8)}` : ""} {range.quote}
                          <small className="muted">
                            {{ mid: "盘口中间价", last: "最新成交", mark: "标记价格", index: "指数价格" }[range.basis]}
                            {range.isDemo ? " · 演示" : " · 公开行情"}
                            {!range.sourceKnown ? " · 来源时间未知" : ""}
                          </small>
                        </div>
                      ))}
                      {!summary.ranges.length && <span className="muted">无可用参考价格</span>}
                      <small className="muted">参考范围 · 已纳入 {summary.includedMarkets} / {items.length} 市场 · 排除 {summary.excludedMarkets}</small>
                      {Object.keys(summary.exclusions).length > 0 && <small className="muted">
                        过滤项：{Object.entries(summary.exclusions).map(([reason, count]) =>
                          `${({ inactive: "非活跃", missing: "缺快照", invalid: "无效快照", identity: "身份不符",
                            invalid_price: "无效价格", missing_time: "缺时间", future: "未来时间", stale: "过期",
                            missing_price: "缺价格" } as Record<string, string>)[reason] ?? reason} ${count}`).join("、")}
                      </small>}
                    </td>
                    <td>
                      <span className="muted">展开查看原始费率</span>
                    </td>
                    <td>
                      <span className="muted">按市场独立显示</span>
                    </td>
                    <td>
                      {summary.ranges.map((range) => <small className="muted" key={range.key}
                        title={`纳入范围最早字段 ${time(range.oldestAt)} · 最新字段 ${time(range.newestAt)}`}>
                        {range.quote} · {{ mid: "中间价", last: "成交", mark: "标记", index: "指数" }[range.basis]}
                        {range.isDemo ? " · 演示" : ""} · 最旧 {age(range.oldestAt, now)}
                        {!range.sourceKnown ? " · 按本地接收，来源时间未知" : ""}
                      </small>)}
                      {!summary.ranges.length && <Badge tone="amber">无有效价格字段</Badge>}
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`查看 ${items[0].base} 详情`}
                        onClick={() => openAsset(id)}
                      >
                        <ArrowRight size={17} />
                      </button>
                    </td>
                  </tr>
                  {open &&
                    items.map((i) => (
                      <MarketRow
                        key={i.id}
                        instrument={i}
                        snapshot={snapshots.get(i.id)}
                        favorite={favorites.includes(i.id)}
                        toggleFavorite={() => toggleFavorite(i.id)}
                        now={now}
                        staleSeconds={staleSeconds}
                      />
                    ))}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {groups.length === 0 && (
        <Empty
          title={instruments.length ? "没有符合条件的市场" : "等待公开行情"}
          text={
            instruments.length
              ? "调整搜索或筛选条件后重试。"
              : "检查平台状态和采集配置。连接失败时不会填充模拟行情。"
          }
        />
      )}
      <div className="panel-foot">
        <span>
          {filtered.length} 个市场 · {groups.length} 个独立资产
        </span>
        <span>
          <CircleHelp size={13} /> 资金费率为小数转换后的百分比，周期由来源提供
        </span>
      </div>
    </section>
  );
}
function MarketRow({
  instrument: i,
  snapshot: s,
  favorite,
  toggleFavorite,
  now,
  staleSeconds,
}: {
  instrument: Instrument;
  snapshot?: Snapshot;
  favorite: boolean;
  toggleFavorite: () => void;
  now: number;
  staleSeconds: number;
}) {
  return (
    <tr className="market-row">
      <td>
        <div className="asset-cell indent">
          <button
            className={`icon-button ${favorite ? "favorite" : ""}`}
            aria-label={`${favorite ? "取消收藏" : "收藏"} ${i.native_symbol}`}
            onClick={toggleFavorite}
          >
            <Star size={15} fill={favorite ? "currentColor" : "none"} />
          </button>
          <div>
            <strong>{i.native_symbol}</strong>
            <small>
              {kindName[i.kind]} · {i.chain ?? "链下市场"}
            </small>
            <small className="muted break" title={i.native_asset_id ?? undefined}>
              原生身份：{i.native_asset_id ?? "未提供"}
            </small>
          </div>
        </div>
      </td>
      <td>
        {i.venue}
        <small className="muted">
          {i.mapping_verified ? "资产身份已核验" : "独立身份 · 映射待核验"}
        </small>
      </td>
      <td className="mono">
        {num(s?.mid ?? s?.last ?? s?.mark ?? s?.index, 6)}{" "}
        <small>{i.quote}</small>
      </td>
      <td className="mono">
        <span className={(s?.funding_rate ?? 0) > 0 ? "mint" : ""}>
          {pct(s?.funding_rate)}
        </span>
        <small>
          {s?.funding_interval_hours
            ? `${s.funding_interval_hours}h · ${fundingKind(s.funding_kind)}`
            : "周期未提供"}
        </small>
        {s?.funding_rate != null && (
          <small>
            <FieldTime
              snapshot={s}
              field="funding_rate"
              now={now}
              staleSeconds={staleSeconds}
            />
          </small>
        )}
      </td>
      <td className="mono">
        {num(s?.volume)}
        <small>{s?.volume_unit ?? "单位未提供"}</small>
      </td>
      <td colSpan={2}>
        <DataAge snapshot={s} now={now} staleSeconds={staleSeconds} />
      </td>
    </tr>
  );
}
function fundingKind(kind?: string) {
  return kind === "settled"
    ? "已结算"
    : kind === "predicted"
      ? "预测"
      : kind === "current"
        ? "当前估算"
        : "类型未提供";
}
function Chart({
  snapshots,
  field,
  label,
}: {
  snapshots: Snapshot[];
  field: "price" | "funding";
  label: string;
}) {
  const funding = field === "funding" ? fundingHistorySeries(snapshots) : null;
  const priceData = (field === "price" ? snapshots : [])
    .map((s) => ({
      at: s.source_at ?? s.received_at,
      value: (s.mid ??
            s.last ??
            s.mark ??
            (s.bid != null && s.ask != null ? (s.bid + s.ask) / 2 : null)),
    }))
    .filter((p): p is { at: number; value: number } => p.value != null)
    .sort((a, b) => a.at - b.at);
  const series = funding ? funding.series.map((item) => ({ ...item,
    label: `${item.kind === "settled" ? "已结算值快照" : fundingKind(item.kind)} · ${item.intervalHours ?? "周期未提供"}${item.intervalHours ? "h" : ""} · ${
      item.basis === "source" ? "来源字段时间" : item.basis === "field_received" ? "本地字段接收" : "本地快照（来源时间未知）"}${item.isDemo ? " · 演示" : ""}`,
  })) : [{ key: "price", label, points: priceData }];
  const data = series.flatMap((item) => item.points).sort((left, right) => left.at - right.at);
  const historyNote = funding ? `资金费按字段时间去重：${funding.pointCount} 个点，去重 ${funding.duplicates} 条，排除 ${funding.excluded} 条；本图为本地快照序列，字段时间不自动等同实际结算时间。` : "";
  if (data.length < 2)
    return (
      <div className="chart-empty">
        <Activity size={20} />
        <span>{label}至少需要两条独立有效记录（当前 {data.length} 条）
          {funding && <small>{series.map((item) => item.label).join("；")}</small>}
          {historyNote && <small>{historyNote}</small>}</span>
      </div>
    );
  const min = Math.min(...data.map((d) => d.value)),
    max = Math.max(...data.map((d) => d.value));
  const range = max - min || Math.abs(max) * 0.01 || 1;
  const first = data[0].at,
    last = data[data.length - 1].at;
  const position = (point: { at: number; value: number }) => ({
    x: 48 + (last === first ? .5 : (point.at - first) / (last - first)) * 680,
    y: 135 - ((point.value - min) / range) * 108,
  });
  const colors = ["#56d6ba", "#d5ae6e", "#8eacf8", "#ee91b4", "#a9c572", "#c0a2f4"];
  return (
    <div
      className="chart"
      role="img"
      aria-label={`${label}历史，共 ${data.length} 条记录；最低 ${field === "funding" ? pct(min) : num(min, 6)}，最高 ${field === "funding" ? pct(max) : num(max, 6)}${funding ? `；${series.map((item) => item.label).join("；")}；${historyNote}` : ""}`}
    >
      <svg viewBox="0 0 760 176">
        <defs>
          <linearGradient id={`fill-${field}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#56d6ba" stopOpacity=".17" />
            <stop offset="100%" stopColor="#56d6ba" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[27, 81, 135].map((y, i) => (
          <g key={y}>
            <line
              x1="48"
              x2="728"
              y1={y}
              y2={y}
              stroke="#26303d"
              strokeDasharray="3 5"
            />
            <text x="0" y={y + 4} fill="#8593a6" fontSize="10">
              {field === "funding"
                ? `${((max - (i * range) / 2) * 100).toFixed(3)}%`
                : num(max - (i * range) / 2, 2)}
            </text>
          </g>
        ))}
        {series.map((item, index) => <g key={item.key}>
          <polyline points={item.points.map((point) => { const p = position(point); return `${p.x},${p.y}`; }).join(" ")}
            stroke={colors[index % colors.length]} strokeWidth="2" fill="none" />
          {field === "funding" && item.points.map((point) => { const p = position(point); return (
            <circle key={point.at} cx={p.x} cy={p.y} r="3" fill={colors[index % colors.length]}>
              <title>{item.label} · {time(point.at)} · {pct(point.value)}</title>
            </circle>
          ); })}
        </g>)}
        <text x="48" y="166" fill="#8593a6" fontSize="10">
          {new Date(first).toLocaleTimeString("zh-CN", { hour12: false })}
        </text>
        <text x="728" y="166" textAnchor="end" fill="#8593a6" fontSize="10">
          {new Date(last).toLocaleTimeString("zh-CN", { hour12: false })}
        </text>
      </svg>
      {funding && <><div className="tags">{series.map((item, index) =>
        <span className="badge" key={item.key} style={{ color: colors[index % colors.length] }}>{item.label}</span>)}</div>
        <small className="muted">{historyNote}</small></>}
    </div>
  );
}
function Detail({
  assetId,
  instruments,
  snapshots,
  now,
  staleSeconds,
  back,
  favorites,
  toggleFavorite,
}: {
  assetId: string;
  instruments: Instrument[];
  snapshots: Map<string, Snapshot>;
  now: number;
  staleSeconds: number;
  back: () => void;
  favorites: string[];
  toggleFavorite: (id: string) => void;
}) {
  const items = instruments.filter((i) => i.asset_id === assetId);
  const [selected, setSelected] = useState(items[0]?.id ?? "");
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [book, setBook] = useState<Book | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [bookError, setBookError] = useState("");
  const [bookBusy, setBookBusy] = useState(false);
  const instrument = items.find((i) => i.id === selected);
  const snapshot = snapshots.get(selected);
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setBusy(true);
    setError("");
    setHistory([]);
    setBook(null);
    setBookError("");
    void api<{ snapshots: Snapshot[] }>(
      `/api/history?instrument_id=${encodeURIComponent(selected)}&limit=500`,
    )
      .then((data) => {
        if (alive) setHistory(data.snapshots);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [selected]);
  const loadBook = async () => {
    setBookBusy(true);
    setBookError("");
    try {
      setBook(
        await api<Book>(`/api/orderbook/${encodeURIComponent(selected)}`),
      );
    } catch (e) {
      setBookError((e as Error).message);
    } finally {
      setBookBusy(false);
    }
  };
  if (!items.length)
    return (
      <>
        <button className="text-button" onClick={back}>
          <ArrowLeft size={16} /> 返回总览
        </button>
        <Empty
          title="尚未选择资产"
          text="从市场总览进入一个资产，查看各市场详情。"
        />
      </>
    );
  return (
    <>
      <button className="text-button back" onClick={back}>
        <ArrowLeft size={16} /> 返回市场总览
      </button>
      <div className="detail-heading">
        <span className="coin large">{items[0].base.slice(0, 2)}</span>
        <div>
          <h2>
            {items[0].base} <Badge>{items.length} 个市场</Badge>
          </h2>
          <p className="muted">资产身份：{assetId}</p>
        </div>
      </div>
      <div className="two-columns">
        {[
          [
            "现货与池子",
            items.filter((i) => i.kind === "spot" || i.kind === "pool"),
          ],
          [
            "永续与 RFQ",
            items.filter((i) => i.kind === "perpetual" || i.kind === "rfq"),
          ],
        ].map(([title, list]) => (
          <section className="panel" key={title as string}>
            <div className="panel-heading">
              <h2>{title as string}</h2>
              <Badge>{(list as Instrument[]).length}</Badge>
            </div>
            {(list as Instrument[]).length ? (
              (list as Instrument[]).map((i) => {
                const s = snapshots.get(i.id);
                return (
                  <button
                    className={`instrument-card ${selected === i.id ? "selected" : ""}`}
                    key={i.id}
                    onClick={() => setSelected(i.id)}
                  >
                    <span>
                      <strong>
                        {i.venue} <Badge>{kindName[i.kind]}</Badge>
                      </strong>
                      <small>
                        {i.native_symbol} · {i.quote}
                        {i.chain && ` · ${i.chain}`}
                      </small>
                      <DataAge
                        snapshot={s}
                        now={now}
                        staleSeconds={staleSeconds}
                      />
                    </span>
                    <span className="align-right mono">
                      <strong>
                        {num(s?.mid ?? s?.last ?? s?.mark ?? s?.index, 6)}
                      </strong>
                      <small>
                        {pct(s?.funding_rate)}
                        {s?.funding_interval_hours
                          ? ` / ${s.funding_interval_hours}h`
                          : ""}
                      </small>
                    </span>
                  </button>
                );
              })
            ) : (
              <Empty
                title="没有此类市场"
                text="当前资产的市场目录未提供此产品。"
              />
            )}
          </section>
        ))}
      </div>
      {instrument && (
        <>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>
                  {instrument.venue} · {instrument.native_symbol}
                </h2>
                <p>{instrument.id}</p>
              </div>
              <button
                className="button secondary"
                onClick={() => toggleFavorite(instrument.id)}
              >
                <Star
                  size={15}
                  fill={
                    favorites.includes(instrument.id) ? "currentColor" : "none"
                  }
                />
                {favorites.includes(instrument.id) ? "已收藏" : "收藏市场"}
              </button>
            </div>
            <div className="metrics-grid">
              <Metric
                label={`买一 / ${instrument.quote}`}
                value={num(snapshot?.bid, 8)}
              />
              <Metric
                label={`卖一 / ${instrument.quote}`}
                value={num(snapshot?.ask, 8)}
              />
              <Metric
                label="标记价格 / 指数价格"
                value={`${num(snapshot?.mark, 6)} / ${num(snapshot?.index, 6)}`}
              />
              <Metric
                label="持仓量 / 单位"
                value={`${num(snapshot?.open_interest)} ${snapshot?.oi_unit ?? "单位未提供"}`}
              />
              <Metric
                label="原始资金费率"
                value={pct(snapshot?.funding_rate)}
                sub={fundingKind(snapshot?.funding_kind)}
              />
              <Metric
                label="实际结算周期"
                value={
                  snapshot?.funding_interval_hours
                    ? `${snapshot.funding_interval_hours} 小时`
                    : "未提供"
                }
              />
              <Metric
                label="8 小时归一化"
                value={pct(
                  snapshot?.funding_8h ??
                    (snapshot?.funding_rate != null &&
                    snapshot.funding_interval_hours
                      ? (snapshot.funding_rate * 8) /
                        snapshot.funding_interval_hours
                      : null),
                )}
                sub="用于比较，不代表实际结算"
              />
              <Metric
                label="距下次资金费结算"
                value={<Countdown at={snapshot?.next_funding_at} now={now} />}
                sub={
                  snapshot?.next_funding_at
                    ? time(snapshot.next_funding_at)
                    : undefined
                }
              />
            </div>
            <FieldAges
              snapshot={snapshot}
              now={now}
              staleSeconds={staleSeconds}
            />
          </section>
          {snapshot &&
            Object.keys(snapshot.protocol_metrics ?? {}).length > 0 && (
              <section className="panel protocol-metrics">
                <div className="panel-heading">
                  <div>
                    <h2>协议持仓与状态指标</h2>
                    <p>
                      保留来源字段名称与单位；借贷费、协议状态和资金费率分别展示。
                    </p>
                  </div>
                  <Badge>协议原始指标</Badge>
                </div>
                <dl className="source-list">
                  {Object.entries(snapshot.protocol_metrics ?? {}).map(
                    ([key, value]) => (
                      <React.Fragment key={key}>
                        <dt className="mono break">{key}</dt>
                        <dd className="mono break">
                          {value == null ? "未提供" : String(value)}
                        </dd>
                      </React.Fragment>
                    ),
                  )}
                </dl>
              </section>
            )}
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>历史走势</h2>
                <p>最多 500 条本地历史快照 · {instrument.native_symbol}</p>
              </div>
              <Badge>{busy ? "加载中" : `${history.length} 条`}</Badge>
            </div>
            {error && <Notice error>{error}</Notice>}
            <div className="chart-container">
              <h3>
                价格 <small>{instrument.quote}</small>
              </h3>
              <Chart snapshots={history} field="price" label="价格" />
              <h3>
                原始资金费率 <small>原始结算周期，未作周期混合</small>
              </h3>
              <Chart snapshots={history} field="funding" label="资金费率" />
            </div>
          </section>
          <div className="two-columns">
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>
                    {instrument.mechanism === "orderbook"
                      ? "按需盘口"
                      : "报价与流动性"}
                  </h2>
                  <p>
                    {instrument.mechanism === "orderbook"
                      ? "主动获取最新买卖深度"
                      : "非订单簿市场保留实际报价机制"}
                  </p>
                </div>
                <button
                  className="button secondary"
                  disabled={bookBusy || instrument.mechanism !== "orderbook"}
                  onClick={() => void loadBook()}
                >
                  {bookBusy ? (
                    <Loader2 size={14} className="spin" />
                  ) : (
                    <RefreshCw size={14} />
                  )}{" "}
                  获取盘口
                </button>
              </div>
              {bookError && <Notice error>{bookError}</Notice>}
              {book ? (
                <>
                  <div className="book-grid">
                    <div>
                      <h4>买盘 · 价格 / 数量</h4>
                      {book.bids.slice(0, 10).map(([p, q], idx) => (
                        <div className="book-level" key={idx}>
                          <span className="mint mono">{num(p, 8)}</span>
                          <span className="mono">{num(q, 8)}</span>
                        </div>
                      ))}
                    </div>
                    <div>
                      <h4>卖盘 · 价格 / 数量</h4>
                      {book.asks.slice(0, 10).map(([p, q], idx) => (
                        <div className="book-level" key={idx}>
                          <span className="red mono">{num(p, 8)}</span>
                          <span className="mono">{num(q, 8)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="panel-foot">
                    数量单位：{book.quantity_unit} ·{" "}
                    {age(book.source_at ?? book.received_at, now)} ·{" "}
                    {book.source}
                  </div>
                </>
              ) : (
                <Empty
                  title={
                    instrument.mechanism === "orderbook"
                      ? "按需获取订单簿"
                      : instrument.mechanism === "amm"
                        ? "AMM / 池子型市场"
                        : "RFQ 报价型市场"
                  }
                  text={
                    instrument.mechanism === "orderbook"
                      ? "点击获取盘口查看来源提供的深度与数量单位。"
                      : "未提供可执行规模报价时，不能用展示价替代成交价。池子和路线信息按来源字段显示。"
                  }
                />
              )}
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>市场身份与来源</h2>
                <Badge tone={instrument.mapping_verified ? "green" : "amber"}>
                  {instrument.mapping_verified ? "映射已核验" : "映射待核验"}
                </Badge>
              </div>
              {instrument.kind === "pool" && (
                <PoolInfo instrumentId={instrument.id} />
              )}
              <dl className="source-list">
                <dt>报价币 / 结算币</dt>
                <dd>
                  {instrument.quote} / {instrument.settle ?? "未提供"}
                </dd>
                <dt>链 / 市场机制</dt>
                <dd>
                  {instrument.chain ?? "链下 / 未提供"} · {instrument.mechanism}
                </dd>
                <dt>合约乘数 / 方向</dt>
                <dd>
                  {instrument.contract_size} ·{" "}
                  {instrument.linear ? "线性" : "反向"}
                </dd>
                <dt>原生市场 / 池标识</dt>
                <dd className="break">{instrument.native_symbol}</dd>
                <dt>原生资产身份</dt>
                <dd className="break mono">{instrument.native_asset_id ?? "未提供"}</dd>
                <dt>内部资产身份</dt>
                <dd className="break mono">{instrument.asset_id}</dd>
                <dt>最小名义价值</dt>
                <dd>
                  {num(instrument.min_notional)} {instrument.quote}
                </dd>
                <dt>指数来源</dt>
                <dd>{instrument.index_source ?? "未提供"}</dd>
                <dt>行情来源</dt>
                <dd className="break">{snapshot?.source ?? "未提供"}</dd>
                <dt>来源时间</dt>
                <dd>{time(snapshot?.source_at)}</dd>
                <dt>本地接收时间</dt>
                <dd>{time(snapshot?.received_at)}</dd>
                <dt>数据有效性</dt>
                <dd>
                  {snapshot ? (snapshot.valid ? "有效" : "无效") : "未提供"}
                  {snapshot?.is_demo && " · 演示数据"}
                </dd>
              </dl>
              {snapshot?.issues?.length ? (
                <Notice>{snapshot.issues.join("；")}</Notice>
              ) : null}
              {snapshot &&
                Object.keys(snapshot.field_sources ?? {}).length > 0 && (
                  <details className="raw-details">
                    <summary>字段来源 / 池子与路线元数据</summary>
                    <pre>{JSON.stringify(snapshot.field_sources, null, 2)}</pre>
                  </details>
                )}
            </section>
          </div>
        </>
      )}
    </>
  );
}
function Metric({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="metric">
      <small>{label}</small>
      <strong className="mono">{value}</strong>
      {sub && <span>{sub}</span>}
    </div>
  );
}
function Status({
  statuses,
  coverage,
  now,
}: {
  statuses: VenueStatus[];
  coverage: Coverage[];
  now: number;
}) {
  const [filter, setFilter] = useState("");
  return (
    <>
      <div className="stats">
        <Stat
          label="已登记平台"
          value={num(coverage.length, 0)}
          detail="包含受限与暂不支持的来源"
          icon={Globe}
        />
        <Stat
          label="采集中的平台"
          value={num(
            statuses.filter((s) =>
              [
                "online",
                "polling",
                "connected",
                "healthy",
                "live",
                "ok",
              ].includes(s.state),
            ).length,
            0,
          )}
          detail="以采集器实际状态为准"
          icon={Wifi}
        />
        <Stat
          label="重试 / 限流次数"
          value={`${statuses.reduce((n, s) => n + s.retries, 0)} / ${statuses.reduce((n, s) => n + s.rate_limited, 0)}`}
          detail="保留失败与退避诊断"
          icon={RefreshCw}
        />
        <Stat
          label="解析错误"
          value={num(
            statuses.reduce((n, s) => n + s.parse_errors, 0),
            0,
          )}
          detail="错误不会产生替代行情"
          icon={AlertTriangle}
        />
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>连接诊断</h2>
            <p>
              此处为交易平台到后端的行情链路；右上角为后端到页面的推送连接。
            </p>
          </div>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>平台</th>
                <th>采集状态</th>
                <th>行情源链路 / WebSocket 状态</th>
                <th>资金费目录采集</th>
                <th>源流重连</th>
                <th>最后接收</th>
                <th>订阅数</th>
                <th>重试 / 限流 / 解析错误</th>
                <th>诊断信息</th>
              </tr>
            </thead>
            <tbody>
              {statuses.map((s) => (
                <tr key={s.venue}>
                  <td>
                    <strong>{s.venue}</strong>
                  </td>
                  <td>
                    <Badge
                      tone={
                        [
                          "error",
                          "blocked",
                          "backoff",
                          "rate_limited",
                        ].includes(s.state)
                          ? "amber"
                          : [
                                "online",
                                "polling",
                                "connected",
                                "healthy",
                                "live",
                                "ok",
                              ].includes(s.state)
                            ? "green"
                            : "neutral"
                      }
                    >
                      {stateName[s.state] ?? s.state}
                    </Badge>
                  </td>
                  <td className="wrap source-stream">
                    <span>{s.transport ?? "未提供"}</span>
                    <small>
                      源 WebSocket：
                      {stateName[s.stream_state] ?? s.stream_state ?? "未提供"}
                    </small>
                    <small>全目录：{s.light_state ?? "未提供"} · {s.light_subscriptions ?? 0} 市场 · 最近 {age(s.light_last_received_at, now)}</small>
                    <small>轻量重试：{num(s.light_retries, 0)} · 目录发现：{s.discovery_complete ? "已完成" : "尚未完成"}
                      {s.discovery_state ? ` · ${stateName[s.discovery_state] ?? s.discovery_state}` : ""}</small>
                  </td>
                  <td className="wrap">
                    <Badge tone={Object.keys(s.funding_errors ?? {}).length ? "amber" : "neutral"}>
                      {stateName[s.funding_state] ?? s.funding_state ?? "未提供"}
                    </Badge>
                    <small className="muted">{num(s.funding_subscriptions, 0)} 个市场 · 重试 {num(s.funding_retries, 0)}</small>
                    <small className="muted" title={time(s.funding_last_received_at)}>最近 {age(s.funding_last_received_at, now)}</small>
                  </td>
                  <td className="mono">{num(s.stream_reconnects, 0)}</td>
                  <td title={time(s.last_received_at)}>
                    {age(s.last_received_at, now)}
                  </td>
                  <td className="mono">{s.subscriptions}</td>
                  <td className="mono">
                    {s.retries} / {s.rate_limited} / {s.parse_errors}
                  </td>
                  <td className="wrap muted">
                    {s.message ?? "—"}
                    {s.missing_fields?.length > 0 && (
                      <small>缺失：{s.missing_fields.join("、")}</small>
                    )}
                    {([
                      ["目录发现", s.discovery_errors],
                      ["全目录轻量", s.light_errors],
                      ["关注市场详情", s.detail_errors],
                      ["资金费", s.funding_errors],
                    ] as [string, Record<string, string> | undefined][]).map(([label, errors]) => (
                      <details key={label}>
                        <summary>{label}错误（{Object.keys(errors ?? {}).length}）</summary>
                        {Object.entries(errors ?? {}).map(([service, message]) => (
                          <p key={service} className="break">{service}：{message}</p>
                        ))}
                        {!Object.keys(errors ?? {}).length && <p>未记录错误</p>}
                      </details>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!statuses.length && (
          <Empty
            title="尚无采集器状态"
            text="后端连接成功后将显示逐平台诊断。"
          />
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>平台覆盖登记</h2>
            <p>所有候选平台均公开能力边界；受限状态不代表已接入</p>
          </div>
          <select
            aria-label="筛选覆盖状态"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="">全部状态</option>
            {[...new Set(coverage.map((c) => c.status))].map((s) => (
              <option key={s} value={s}>
                {stateName[s] ?? s}
              </option>
            ))}
          </select>
        </div>
        <div className="coverage-grid">
          {coverage
            .filter((c) => !filter || c.status === filter)
            .map((c, idx) => (
              <article
                className="coverage-card"
                key={`${c.venue}-${c.category}-${idx}`}
              >
                <div className="coverage-title">
                  <h3>
                    {c.venue}
                    <small>{c.category}</small>
                  </h3>
                  <Badge
                    tone={
                      ["blocked", "unsupported"].includes(c.status)
                        ? "amber"
                        : c.status === "partial"
                          ? "neutral"
                          : "green"
                    }
                  >
                    {stateName[c.status] ?? c.status}
                  </Badge>
                </div>
                <div className="tags">
                  {c.products.map((p) => (
                    <Badge key={p}>{kindName[p] ?? p}</Badge>
                  ))}
                  {c.chains.map((ch) => (
                    <Badge key={ch}>{ch}</Badge>
                  ))}
                </div>
                <p>{c.method}</p>
                <dl>
                  <dt>提供能力</dt>
                  <dd>{c.capabilities.join("、") || "未提供"}</dd>
                  <dt>接入条件</dt>
                  <dd>{c.requirements.join("；") || "无额外条件"}</dd>
                  <dt>限制与缺失</dt>
                  <dd>{c.limitations.join("；") || "未记录额外限制"}</dd>
                </dl>
                <div className="coverage-footer">
                  <span>核验 {c.last_verified_at ?? "尚未记录"}</span>
                  {c.docs_url && /^https?:\/\//.test(c.docs_url) && (
                    <a href={c.docs_url} target="_blank" rel="noreferrer">
                      接口文档 <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              </article>
            ))}
        </div>
        {!coverage.length && (
          <Empty title="覆盖登记尚未加载" text="请检查后端状态接口。" />
        )}
      </section>
    </>
  );
}
function SettingsPage({
  settings,
  coverage,
  onSave,
}: {
  settings: Settings | null;
  coverage: Coverage[];
  onSave: (s: Settings) => void;
}) {
  const [draft, setDraft] = useState<Settings | null>(settings);
  const [watch, setWatch] = useState(settings?.watch_assets.join(", ") ?? "");
  const [rpc, setRpc] = useState(
    JSON.stringify(settings?.rpc_urls ?? {}, null, 2),
  );
  const [dexQueries, setDexQueries] = useState(
    JSON.stringify(settings?.dex_search_queries ?? {}, null, 2),
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    setDraft(settings);
    setWatch(settings?.watch_assets.join(", ") ?? "");
    setRpc(JSON.stringify(settings?.rpc_urls ?? {}, null, 2));
    setDexQueries(JSON.stringify(settings?.dex_search_queries ?? {}, null, 2));
  }, [settings]);
  if (!draft)
    return <Empty title="配置尚未加载" text="连接后端后可编辑配置。" />;
  const update = (key: keyof Settings, value: unknown) =>
    setDraft((old) => (old ? { ...old, [key]: value } : old));
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const urls = JSON.parse(rpc);
      if (
        !urls ||
        Array.isArray(urls) ||
        typeof urls !== "object" ||
        Object.values(urls).some((v) => typeof v !== "string")
      )
        throw new Error("RPC 配置必须是链名称到 URL 字符串的 JSON 对象");
      const queries = JSON.parse(dexQueries);
      if (!queries || Array.isArray(queries) || typeof queries !== "object" ||
          Object.entries(queries).some(([protocol, values]) => !protocol.trim() || !Array.isArray(values) ||
            values.some((value) => typeof value !== "string" || !value.trim())))
        throw new Error("DEX 搜索配置必须是协议名称到非空搜索字符串数组的 JSON 对象");
      const result = await api<Settings>("/api/settings", "PUT", {
        ...draft,
        watch_assets: watch
          .split(/[,，\s]+/)
          .map((v) => v.trim())
          .filter(Boolean),
        rpc_urls: urls,
        dex_search_queries: queries,
      });
      onSave(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <>
    <FeeProfiles />
    <form onSubmit={(e) => void save(e)}>
      <div className="settings-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>运行模块</h2>
              <p>各模块可独立启停</p>
            </div>
            <SlidersHorizontal size={18} />
          </div>
          <div className="form-content">
            {(
              [
                [
                  "collection_enabled",
                  "市场采集",
                  "公开接口目录发现与行情采集",
                ],
                [
                  "prefer_websocket",
                  "优先行情 WebSocket",
                  "优先使用平台公开行情流；断开或暂不支持时以 REST 回退，元数据和资金费仍按来源机制更新",
                ],
                ["broad_collection_enabled", "全目录轻量采集", "CEX 按批次轮转已发现市场；关注资产继续采集详细数据。轮转周期不等于全市场实时覆盖"],
                ["recording_enabled", "行情诊断录制", "本地保存公开行情负载，按容量与天数限制；默认关闭，不记录认证信息"],
                ["history_enabled", "历史存储", "保存定时快照，用于查询和回放"],
                ["computation_enabled", "收益计算", "允许策略计算与机会分析"],
                [
                  "notifications_enabled",
                  "消息通知",
                  "本地中心与已配置的外部渠道",
                ],
                ["demo_mode", "演示模式", "仅显式开启；演示快照始终带标识"],
              ] as [keyof Settings, string, string][]
            ).map(([key, label, hint]) => (
              <label className="toggle-row" key={key}>
                <span>
                  <strong>{label}</strong>
                  <small>{hint}</small>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  checked={
                    key === "prefer_websocket"
                      ? draft.prefer_websocket !== false
                      : !!draft[key]
                  }
                  onChange={(e) => update(key, e.target.checked)}
                />
              </label>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>采集与保留</h2>
              <p>界面刷新与行情源更新是不同的节奏</p>
            </div>
            <Clock size={18} />
          </div>
          <div className="form-grid">
            <Field label="关注资产" hint="任意已发现 ticker，逗号分隔；详细采集关注资产，全目录轻量采集独立控制">
              <input
                value={watch}
                onChange={(e) => setWatch(e.target.value)}
                placeholder="例如 PEPE, BTC, 任意已发现 ticker"
              />
            </Field>
            <Field label="页面合并刷新 / 毫秒">
              <input
                type="number"
                min={500}
                max={60000}
                required
                value={draft.refresh_ms}
                onChange={(e) => update("refresh_ms", Number(e.target.value))}
              />
            </Field>
            <Field label="采集周期 / 秒">
              <input
                type="number"
                min={5}
                max={3600}
                required
                value={draft.poll_seconds}
                onChange={(e) => update("poll_seconds", Number(e.target.value))}
              />
            </Field>
            <Field label="全目录每批间隔 / 秒">
              <input type="number" min={10} max={3600} required value={draft.broad_poll_seconds}
                onChange={(e) => update("broad_poll_seconds", Number(e.target.value))} />
            </Field>
            <Field label="全目录每批市场数" hint="1–100；完成整轮所需时间随市场数增长">
              <input type="number" min={1} max={100} required value={draft.broad_batch_size}
                onChange={(e) => update("broad_batch_size", Number(e.target.value))} />
            </Field>
            <Field label="资金费采集周期 / 秒" hint="资金费目录独立刷新；行情源未提供的字段不会用零替代">
              <input type="number" min={10} max={3600} required value={draft.funding_poll_seconds ?? 60}
                onChange={(e) => update("funding_poll_seconds", Number(e.target.value))} />
            </Field>
            <Field label="市场目录刷新 / 秒" hint="300–21600；定期发现新增币种、市场及下架状态">
              <input type="number" min={300} max={21600} required value={draft.discovery_seconds ?? 1800}
                onChange={(e) => update("discovery_seconds", Number(e.target.value))} />
            </Field>
            <Field label="诊断录制容量 / MB">
              <input type="number" min={1} max={10000} required value={draft.recording_max_mb}
                onChange={(e) => update("recording_max_mb", Number(e.target.value))} />
            </Field>
            <Field label="诊断录制保留 / 天">
              <input type="number" min={1} max={30} required value={draft.recording_retention_days}
                onChange={(e) => update("recording_retention_days", Number(e.target.value))} />
            </Field>
            <Field label="诊断平台" hint="逗号分隔平台 ID；留空表示所有已启用平台">
              <input value={draft.recording_venues.join(", ")}
                onChange={(e) => update("recording_venues", e.target.value.split(",").map(s => s.trim()).filter(Boolean))} />
            </Field>
            <Field label="数据过期阈值 / 秒">
              <input
                type="number"
                min={5}
                max={86400}
                required
                value={draft.stale_seconds}
                onChange={(e) =>
                  update("stale_seconds", Number(e.target.value))
                }
              />
            </Field>
            <Field label="历史保留 / 天">
              <input
                type="number"
                min={1}
                max={3650}
                required
                value={draft.retention_days}
                onChange={(e) =>
                  update("retention_days", Number(e.target.value))
                }
              />
            </Field>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>平台开关</h2>
              <p>开启只表示允许采集，实际能力见平台覆盖表</p>
            </div>
            <Globe size={18} />
          </div>
          <div className="venue-checks">
            {[
              ...new Set([
                ...coverage.map((c) => c.venue),
                ...draft.enabled_venues,
              ]),
            ]
              .sort()
              .map((v) => (
                <label key={v}>
                  <input
                    type="checkbox"
                    checked={draft.enabled_venues.includes(v)}
                    onChange={(e) =>
                      update(
                        "enabled_venues",
                        e.target.checked
                          ? [...draft.enabled_venues, v]
                          : draft.enabled_venues.filter((x) => x !== v),
                      )
                    }
                  />
                  <span>{v}</span>
                  <Badge>
                    {stateName[
                      coverage.find((c) => c.venue === v)?.status ?? ""
                    ] ??
                      coverage.find((c) => c.venue === v)?.status ??
                      "未登记"}
                  </Badge>
                </label>
              ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>RPC 与私有连接</h2>
              <p>RPC 地址存放于本地配置</p>
            </div>
            <Database size={18} />
          </div>
          <div className="form-content">
            <Field label="链 → RPC URL（JSON）">
              <textarea
                rows={7}
                spellCheck={false}
                className="mono"
                value={rpc}
                onChange={(e) => setRpc(e.target.value)}
                placeholder={'{\n  "ethereum": "https://…"\n}'}
              />
            </Field>
            <div className="info-box">
              <ShieldCheck size={17} />
              <p>
                公开行情不需要交易账户。代理及可选 API key
                由后端环境变量配置；前端不读取或回显私密凭证。具体变量和接入条件请查看项目配置说明。
              </p>
            </div>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>DEX 池发现范围</h2>
              <p>为各协议添加任意币种搜索词或指定池地址；来源搜索结果不代表完整链上目录。</p>
            </div>
            <Search size={18} />
          </div>
          <div className="form-content">
            <Field label="协议 → 搜索词数组（JSON）" hint="协议 ID 如 uniswap、pancakeswap、aerodrome、curve、raydium、orca。留空对象使用已有默认配置。">
              <textarea aria-label="DEX 搜索词 JSON" rows={7} spellCheck={false} className="mono" value={dexQueries}
                onChange={(e) => setDexQueries(e.target.value)}
                placeholder={'{\n  "uniswap": ["PEPE USDC", "pool:ethereum:0x…"]\n}'} />
            </Field>
          </div>
        </section>
      </div>
      {error && <Notice error>{error}</Notice>}
      <div className="form-actions">
        <span className="muted">
          配置保存到本地，采集状态可在平台状态页核对。
        </span>
        <button className="button primary" type="submit" disabled={saving}>
          {saving ? <Loader2 className="spin" size={16} /> : <Save size={16} />}
          保存配置
        </button>
      </div>
    </form>
    </>
  );
}
const strategies: Record<string, string> = {
  spot_spread: "跨平台现货价差",
  spot_perp_basis: "现货与永续基差",
  perp_spread: "跨平台永续价差",
  funding_spread: "跨平台资金费差",
  funding_basis: "资金费与基差持有",
  cex_dex_spot: "CEX / DEX 现货比较",
  dex_pool_spread: "同链跨池价格比较",
};
function Opportunities({
  initial,
  instruments,
  snapshots,
  toast,
}: {
  initial?: {buy_instrument_id: string; sell_instrument_id: string; strategy: string} | null;
  instruments: Instrument[];
  snapshots: Map<string, Snapshot>;
  toast: (t: string) => void;
}) {
  const [strategy, setStrategy] = useState(initial?.strategy ?? "spot_spread");
  const [buy, setBuy] = useState(initial?.buy_instrument_id ?? "");
  const [sell, setSell] = useState(initial?.sell_instrument_id ?? "");
  const [notional, setNotional] = useState("10000");
  const [sizeBase, setSizeBase] = useState("");
  const [sizeMode, setSizeMode] = useState("notional");
  const [hold, setHold] = useState("24");
  const [customHold, setCustomHold] = useState("12");
  const [batchSizes, setBatchSizes] = useState("");
  const [fxRate, setFxRate] = useState("");
  const [fxSource, setFxSource] = useState("");
  const [fxAt, setFxAt] = useState("");
  const [extras, setExtras] = useState("");
  const [exitMode, setExitMode] = useState("unknown");
  const [longExitPrice, setLongExitPrice] = useState("");
  const [shortExitPrice, setShortExitPrice] = useState("");
  const [exitBasis, setExitBasis] = useState("");
  const [fundingBasis, setFundingBasis] = useState("mark");
  const [monitorVersion, setMonitorVersion] = useState(0);
  const [result, setResult] = useState<Json | null>(null);
  const [resultStatus, setResultStatus] = useState("");
  const [resultSort, setResultSort] = useState("net_profit");
  const [calculation, setCalculation] = useState<Json | null>(null);
  const [saved, setSaved] = useState<Json[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [savedError, setSavedError] = useState("");
  const [busy, setBusy] = useState(false);
  const [bookMode, setBookMode] = useState(true);
  const [sizingNote, setSizingNote] = useState("");
  const [delay, setDelay] = useState("0");
  const [asOf, setAsOf] = useState("");
  const buyI = instruments.find((i) => i.id === buy),
    sellI = instruments.find((i) => i.id === sell);
  const tradeFees = useTradeFees(buyI, sellI);
  const buyS = snapshots.get(buy);
  const estimate = buyS?.ask != null && Number.isFinite(buyS.ask) && buyS.ask > 0 ? buyS.ask : null;
  const indicativeQuantity = estimate ? Number(notional) / estimate : null;
  const loadSaved = () =>
    api<{ samples: Json[] }>("/api/opportunities/saved")
      .then((data) => {
        setSaved(data.samples);
        setSavedError("");
      })
      .catch((e) => setSavedError(e.message));
  useEffect(() => {
    void loadSaved();
  }, []);
  const calculate = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    setResult(null);
    setSizingNote("");
    try {
      if (!buyI || !sellI) throw new Error("请选择买入与卖出市场");
      if (buy === sell) throw new Error("买卖两腿必须选择不同市场");
      if (exitMode === "prices" && (![longExitPrice, shortExitPrice].every(v => v.trim() && Number.isFinite(Number(v)) && Number(v) > 0)))
        throw new Error("请填写两腿正数退出价格；它们是模拟假设，不是已锁定报价");
      if (exitMode === "basis" && (!exitBasis.trim() || !Number.isFinite(Number(exitBasis))))
        throw new Error("请填写有效的退出基差假设");
      let quantity = Number(sizeBase);
      if (sizeMode === "base" && (!Number.isFinite(quantity) || quantity <= 0))
        throw new Error("请输入有效的基础资产数量");
      if (sizeMode === "notional" && (!Number.isFinite(Number(notional)) || Number(notional) <= 0))
        throw new Error("请输入有效的买方报价币名义金额");
      if (sizeMode === "notional" && !bookMode)
        throw new Error("按金额核算需要启用订单簿深度获取；也可改用基础资产数量模式");
      const books: Book[] = [];
      const bookErrors: string[] = [];
      if (bookMode) {
        for (const i of [buyI, sellI])
          if (i.mechanism === "orderbook") {
            try {
              books.push(
                await api<Book>(`/api/orderbook/${encodeURIComponent(i.id)}`),
              );
            } catch (e) {
              bookErrors.push(`${i.venue}: ${(e as Error).message}`);
            }
          }
      }
      const calculationAt = Date.now();
      if (sizeMode === "notional") {
        const buyBook = books.find((book) => book.instrument_id === buyI.id);
        if (!buyBook) throw new Error(`无法取得买方盘口，不能按金额核算；可改用基础资产数量模式。${bookErrors.join("；")}`);
        const sizing = bookBaseForNotional(buyI, buyBook, Number(notional), calculationAt, 60);
        quantity = sizing.sizeBase;
        setSizingNote(`本次买方名义金额 ${num(sizing.spentQuote, 8)} ${buyI.quote}（不含额外费用），按 ${sizing.levelsUsed} 档卖盘换算 ${num(sizing.sizeBase, 12)} ${buyI.base}；未自动调整两腿数量步长。${sizing.sourceTimeKnown ? "" : "源事件时间未提供，仅按盘口本地接收时间检查时效。"}`);
      }
      const input: Json = {
        instruments: [buyI, sellI],
        snapshots: [snapshots.get(buy), snapshots.get(sell)].filter(Boolean),
        orderbooks: books,
        buy_instrument_id: buy,
        sell_instrument_id: sell,
        size_base: quantity,
        strategy,
        valuation_currency: buyI.quote,
        fx_rates:
          sellI.quote !== buyI.quote && fxRate && fxSource && fxAt
            ? [
                {
                  base: sellI.quote,
                  quote: buyI.quote,
                  rate: Number(fxRate),
                  source: fxSource,
                  at: new Date(fxAt).getTime(),
                },
              ]
            : [],
        sizes_base: batchSizes
          .split(/[,，\s]+/)
          .filter(Boolean)
          .map(Number),
        costs: [buyI, sellI].map((instrument, side) => tradeFees.cost(instrument, side, calculationAt)),
        extra_costs: [
          {
            name: "Gas / 转账 / 其他执行成本",
            amount: extras === "" ? null : Number(extras),
            currency: buyI.quote,
            source: "用户配置",
            at: calculationAt,
          },
        ],
        hold_hours: Number(hold === "custom" ? customHold : hold),
        assume_exit_at_entry: exitMode === "entry",
        exit_prices: exitMode === "prices" ? {[buy]: Number(longExitPrice), [sell]: Number(shortExitPrice)} : {},
        exit_basis_quote: exitMode === "basis" ? Number(exitBasis) : null,
        stale_seconds: 60,
        as_of: calculationAt,
        funding_notional_basis: fundingBasis,
      };
      const output = await api<Json>(
        "/api/opportunities/calculate",
        "POST",
        input,
      );
      setCalculation(input);
      setResult(output);
      if (bookErrors.length)
        setError(
          `盘口获取失败：${bookErrors.join("；")}。计算结果包含对应的缺失条件。`,
        );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const monitor = async () => {
    if (!calculation) return;
    setError("");
    try {
      await api("/api/monitors", "POST", {
        name: name || `${strategies[strategy]} ${buyI?.base ?? ""}`,
        enabled: true,
        interval_seconds: 30,
        calculation,
      });
      setMonitorVersion((v) => v + 1);
      toast("持续监控已创建，每 30 秒更新行情与计算；基础数量保持不变");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const save = async () => {
    if (!calculation) return;
    setError("");
    try {
      await api("/api/opportunities/saved", "POST", {
        name:
          name ||
          `${strategies[strategy]} ${new Date().toLocaleString("zh-CN")}`,
        calculation,
      });
      await loadSaved();
      toast("计算输入与结果已保存，可在历史样本中回放");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const replay = async (sample: Json) => {
    setError("");
    setBusy(true);
    try {
      const timestamp = asOf
        ? new Date(asOf).getTime()
        : (sample.calculation?.as_of ?? sample.created_at);
      const output = await api<Json>("/api/opportunities/replay", "POST", {
        sample_id: sample.id,
        as_of: timestamp,
        receive_delay_ms: Number(delay),
        operation_delay_ms: Number(delay),
      });
      setResult(output);
      setCalculation(sample.calculation);
      toast("已按保存样本回放");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const opportunities = (result?.opportunities ?? [])
    .filter((o: Json) => !resultStatus || o.status === resultStatus)
    .slice()
    .sort(
      (a: Json, b: Json) =>
        (b[resultSort] ?? -Infinity) - (a[resultSort] ?? -Infinity),
    );
  const instrumentOption = (i: Instrument) => (
    <option key={i.id} value={i.id}>
      {i.base} · {i.venue} · {kindName[i.kind]} · {i.quote} · {i.native_symbol}
    </option>
  );
  return (
    <>
      <Notice>
        计算使用当前来源快照与明确成本。不同报价币不默认等价；数据、费用或成交深度缺失时会显示计算前置条件。不会执行交易。
      </Notice>
      {sizingNote && <Notice>{sizingNote}</Notice>}
      <form onSubmit={(e) => void calculate(e)}>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>配置收益情景</h2>
              <p>输入规模可自由调整，不依赖账户余额</p>
            </div>
            <Calculator size={19} />
          </div>
          <div className="form-grid three">
            <Field label="策略">
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
              >
                {Object.entries(strategies).map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="买入 / 做多市场">
              <select
                required
                value={buy}
                onChange={(e) => {
                  setBuy(e.target.value);
                  setSell("");
                }}
              >
                <option value="">选择市场</option>
                {instruments.map(instrumentOption)}
              </select>
            </Field>
            <Field label="卖出 / 做空市场">
              <select
                required
                value={sell}
                onChange={(e) => setSell(e.target.value)}
              >
                <option value="">选择市场</option>
                {instruments
                  .filter((i) => !buyI || i.asset_id === buyI.asset_id)
                  .map(instrumentOption)}
              </select>
            </Field>
            <Field label="规模输入方式">
              <select
                value={sizeMode}
                onChange={(e) => setSizeMode(e.target.value)}
              >
                <option value="notional">按买方报价币名义金额</option>
                <option value="base">按基础资产数量</option>
              </select>
            </Field>
            {sizeMode === "notional" ? (
              <Field
                label={`计算金额 / ${buyI?.quote ?? "买方报价币"}`}
                hint={
                  `金额为买方报价币名义金额，不含额外费用。${estimate
                    ? `仅按卖一的指示估算 ${num(indicativeQuantity, 8)} ${buyI?.base}；`
                    : "卖一指示报价未提供；"}最终数量由本次获取的买方卖盘逐档换算，深度不足时停止核算。`
                }
              >
                <input
                  required
                  type="number"
                  min="0.00000001"
                  step="any"
                  value={notional}
                  onChange={(e) => setNotional(e.target.value)}
                />
              </Field>
            ) : (
              <Field label={`基础资产数量 / ${buyI?.base ?? "BASE"}`}>
                <input
                  required
                  type="number"
                  min="0.00000001"
                  step="any"
                  value={sizeBase}
                  onChange={(e) => setSizeBase(e.target.value)}
                />
              </Field>
            )}
            <Field
              label="批量比较的基础数量（可选）"
              hint="以逗号分隔，按同一成本配置分别计算"
            >
              <input
                value={batchSizes}
                onChange={(e) => setBatchSizes(e.target.value)}
                placeholder="例如 0.1, 0.5, 1"
              />
            </Field>
            <Field label="持有时间 / 小时">
              <select value={hold} onChange={(e) => setHold(e.target.value)}>
                <option value="8">8 小时</option>
                <option value="24">24 小时</option>
                <option value="72">72 小时</option>
                <option value="custom">自定义</option>
              </select>
              {hold === "custom" && (
                <input
                  required
                  type="number"
                  min="0.01"
                  step="any"
                  value={customHold}
                  onChange={(e) => setCustomHold(e.target.value)}
                />
              )}
            </Field>
          </div>
          {buyI && sellI && buyI.quote !== sellI.quote && (
            <div className="form-grid three">
              <Field label={`汇率 / 1 ${sellI.quote} = ? ${buyI.quote}`}>
                <input
                  type="number"
                  step="any"
                  min="0.000000001"
                  value={fxRate}
                  onChange={(e) => setFxRate(e.target.value)}
                  placeholder="不默认稳定币等价"
                />
              </Field>
              <Field label="汇率来源">
                <input
                  value={fxSource}
                  onChange={(e) => setFxSource(e.target.value)}
                  placeholder="核验过的公开报价来源"
                />
              </Field>
              <Field label="汇率来源时间（本地时间）">
                <input
                  type="datetime-local"
                  value={fxAt}
                  onChange={(e) => setFxAt(e.target.value)}
                />
              </Field>
            </div>
          )}
          <details className="cost-details" open>
            <summary>
              费用与执行假设 <span>空白 = 待配置；输入 0 表示明确假设为零</span>
            </summary>
            <FeeCostFields fees={tradeFees} />
            <div className="form-grid four">
              <Field label="资金费名义价值假设">
                <select
                  value={fundingBasis}
                  onChange={(e) => setFundingBasis(e.target.value)}
                >
                  <option value="mark">以当前标记价估算</option>
                  <option value="entry_vwap">显式假设使用入场均价</option>
                </select>
              </Field>
              <Field
                label={`Gas / 转账 / 其他成本 / ${buyI?.quote ?? "报价币"}`}
              >
                <input
                  type="number"
                  min="0"
                  step="any"
                  placeholder="待配置"
                  value={extras}
                  onChange={(e) => setExtras(e.target.value)}
                />
              </Field>
            </div>
            <div className="inline-checks">
              <label>
                <input
                  type="checkbox"
                  checked={bookMode}
                  onChange={(e) => setBookMode(e.target.checked)}
                />
                按规模获取订单簿计算均价
              </label>
            </div>
            <div className="form-grid">
              <Field label="退出情景" hint="仅辅助模拟；未来退出盘口与实际成交价格未知">
                <select value={exitMode} onChange={(e) => setExitMode(e.target.value)}>
                  <option value="unknown">未提供退出假设，持有净值未知</option>
                  <option value="entry">假设退出价等于各自入场价</option>
                  <option value="prices">分别输入两腿退出价格</option>
                  <option value="basis">输入退出基差（空腿减多腿）</option>
                </select>
              </Field>
              {exitMode === "prices" && <>
                <Field label={`多腿退出卖价 / ${buyI?.quote ?? "报价币"}`}>
                  <input type="number" min="0" step="any" value={longExitPrice} onChange={(e) => setLongExitPrice(e.target.value)} />
                </Field>
                <Field label={`空腿退出买价 / ${sellI?.quote ?? "报价币"}`}>
                  <input type="number" min="0" step="any" value={shortExitPrice} onChange={(e) => setShortExitPrice(e.target.value)} />
                </Field>
              </>}
              {exitMode === "basis" && <Field label={`退出基差 / ${buyI?.quote ?? "计价币"}`} hint="固定多腿退出为入场均价，再按此基差推导空腿退出价；可输入 0 模拟收敛">
                <input type="number" step="any" value={exitBasis} onChange={(e) => setExitBasis(e.target.value)} />
              </Field>}
            </div>
          </details>
          <div className="panel-foot">
            <span>
              计价币：{buyI?.quote ?? "未选择"}
              {sellI && buyI?.quote !== sellI.quote && (
                <span className="warning">
                  {" "}
                  · 报价币不同，缺少有来源的 FX 时不可计算
                </span>
              )}
            </span>
            <button
              type="submit"
              disabled={busy || !instruments.length}
              className="button primary"
            >
              {busy ? (
                <Loader2 size={16} className="spin" />
              ) : (
                <Calculator size={16} />
              )}
              计算收益情景
            </button>
          </div>
        </section>
      </form>
      {error && <Notice error>{error}</Notice>}
      {result && (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>计算结果</h2>
              <p>所有数值以结果计价币计量；未知收益不补为零</p>
            </div>
            <div className="save-controls">
              <input
                aria-label="保存样本名称"
                placeholder="样本名称（可选）"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <button
                type="button"
                className="button secondary"
                onClick={() => void save()}
              >
                <Save size={15} />
                保存样本
              </button>
              <button
                type="button"
                className="button primary"
                onClick={() => void monitor()}
              >
                <Activity size={15} />
                持续监控此参数
              </button>
            </div>
          </div>
          <div className="result-toolbar">
            <span className="muted">{opportunities.length} 个结果</span>
            <select
              aria-label="筛选机会状态"
              value={resultStatus}
              onChange={(e) => setResultStatus(e.target.value)}
            >
              <option value="">全部机会状态</option>
              {[
                ...new Set(
                  (result.opportunities ?? []).map((o: Json) =>
                    String(o.status),
                  ),
                ),
              ].map((status) => (
                <option key={status as string} value={status as string}>
                  {stateName[status as string] ?? (status as string)}
                </option>
              ))}
            </select>
            <select
              aria-label="机会结果排序"
              value={resultSort}
              onChange={(e) => setResultSort(e.target.value)}
            >
              <option value="net_profit">净收益从高到低</option>
              <option value="capacity_base">可成交容量从大到小</option>
              <option value="size_base">基础数量从大到小</option>
            </select>
          </div>
          {opportunities.map((o: Json, index: number) => (
            <div className="opportunity" key={o.id ?? index}>
              <div className="opportunity-title">
                <h3>
                  {strategies[o.strategy] ?? o.strategy}{" "}
                  <small>
                    {o.asset_id} · 基础规模 {num(o.size_base, 8)}
                  </small>
                </h3>
                <Badge tone={o.status === "threshold_met" ? "green" : "amber"}>
                  {stateName[o.status] ?? o.status}
                </Badge>
                {o.is_demo && <Badge tone="amber">演示</Badge>}
              </div>
              <div className="metrics-grid">
                <Metric
                  label={`毛收益 / ${o.valuation_currency}`}
                  value={num(o.gross_profit, 6)}
                />
                <Metric label="已知成本合计" value={num(o.known_costs, 6)} />
                <Metric
                  label={`净收益 / ${o.valuation_currency}`}
                  value={num(o.net_profit, 6)}
                />
                <Metric
                  label="回本时间 / 小时"
                  value={num(o.breakeven_hours, 2)}
                  sub={`数据时间 ${time(o.as_of)}`}
                />
              </div>
              {o.missing_prerequisites?.length > 0 && (
                <Notice>
                  计算前置条件：{o.missing_prerequisites.join("；")}
                </Notice>
              )}
              {o.invalid_reasons?.length > 0 && (
                <Notice error>失效原因：{o.invalid_reasons.join("；")}</Notice>
              )}
              {o.scenarios?.length > 0 && (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>持有时间 / 小时</th>
                        <th>资金费情景</th>
                        <th>资金费收益</th>
                        <th>净收益</th>
                      </tr>
                    </thead>
                    <tbody>
                      {o.scenarios.map((s: Json, idx: number) => (
                        <tr key={idx}>
                          <td>{num(s.hold_hours)}</td>
                          <td>
                            {(
                              {
                                maintain: "费差维持",
                                half: "费差减半",
                                zero: "费差归零",
                                reverse: "费差反转",
                              } as Record<string, string>
                            )[s.scenario] ??
                              s.name ??
                              s.scenario ??
                              s.label ??
                              `情景 ${idx + 1}`}
                          </td>
                          <td className="mono">{num(s.funding_profit, 6)}</td>
                          <td className="mono">{num(s.net_profit, 6)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <CalculationDetails opportunity={o} />
              <details className="raw-details">
                <summary>原始计算记录与假设</summary>
                <pre>{JSON.stringify(o, null, 2)}</pre>
              </details>
            </div>
          ))}
          {!(result.opportunities ?? []).length && (
            <Empty title="未返回可计算机会" text="检查输入及策略模块状态。" />
          )}
        </section>
      )}
      <Monitors version={monitorVersion} toast={toast} />
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>历史样本与回放</h2>
            <p>保存完整计算输入与当时结果；延迟分别作用于接收与操作</p>
          </div>
          <button
            className="icon-button"
            aria-label="刷新保存样本"
            onClick={() => void loadSaved()}
          >
            <RefreshCw size={16} />
          </button>
        </div>
        <div className="form-grid">
          <Field label="回放时刻（本地时间；留空使用样本时间）">
            <input
              type="datetime-local"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
          </Field>
          <Field label="接收延迟与操作延迟 / 毫秒（分别）">
            <input
              type="number"
              min="0"
              value={delay}
              onChange={(e) => setDelay(e.target.value)}
            />
          </Field>
        </div>
        {savedError && <Notice error>{savedError}</Notice>}
        {saved.length ? (
          <div className="saved-list">
            {saved.map((sample) => (
              <div key={sample.id}>
                <span>
                  <strong>{sample.name}</strong>
                  <small>
                    {time(sample.created_at)} · {sample.id}
                  </small>
                </span>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void replay(sample)}
                >
                  <Clock size={15} />
                  回放样本
                </button>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            title="尚无保存的样本"
            text="完成计算后保存输入，可重新计算并检查延迟影响。"
          />
        )}
      </section>
    </>
  );
}
function Notifications({ toast }: { toast: (t: string) => void }) {
  const [rules, setRules] = useState<Json[]>([]);
  const [history, setHistory] = useState<Json[]>([]);
  const [deliveries, setDeliveries] = useState<Json[]>([]);
  const [channels, setChannels] = useState<Json[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [channel, setChannel] = useState("local");
  const initial = {
    name: "",
    enabled: true,
    category: "opportunity",
    event_types: [],
    strategies: [],
    assets: [],
    platforms: [],
    channels: ["local"],
    max_age_seconds: 60,
    duration_seconds: 0,
    consecutive_hits: 1,
    cooldown_seconds: 300,
    priority: "normal",
  };
  const [rule, setRule] = useState<Json>(initial);
  const [assetsText, setAssetsText] = useState("");
  const [platformText, setPlatformText] = useState("");
  const load = async () => {
    const result = await Promise.allSettled([
      api<{ rules: Json[] }>("/api/notifications/rules"),
      api<{ notifications: Json[]; deliveries: Json[] }>(
        "/api/notifications/history?limit=100",
      ),
      api<{ channels: Json[] }>("/api/notifications/channels"),
    ]);
    const messages: string[] = [];
    result.forEach((r, i) => {
      if (r.status === "rejected") messages.push(r.reason.message);
      else if (i === 0) setRules((r.value as { rules: Json[] }).rules);
      else if (i === 1) {
        setHistory((r.value as { notifications: Json[] }).notifications);
        setDeliveries((r.value as { deliveries: Json[] }).deliveries);
      } else setChannels((r.value as { channels: Json[] }).channels);
    });
    setError(messages.join("；"));
  };
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => clearInterval(timer);
  }, []);
  const test = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/api/notifications/test", "POST", {
        channel,
        message: "Watch Money 测试通知：渠道连接验证。",
      });
      toast("测试通知已加入持久化队列，请查看发送记录");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/notifications/rules", "POST", {
        ...rule,
        assets: assetsText.split(/[,，\s]+/).filter(Boolean),
        platforms: platformText.split(/[,，\s]+/).filter(Boolean),
      });
      setEditing(false);
      setRule(initial);
      await load();
      toast("通知规则已保存");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async (id: string) => {
    try {
      await api(`/api/notifications/rules/${encodeURIComponent(id)}`, "DELETE");
      await load();
      toast("规则已删除");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const change = (key: string, value: unknown) =>
    setRule((old) => ({ ...old, [key]: value }));
  const numeric = (label: string, key: string, rate = false) => (
    <Field label={label}>
      <input
        type="number"
        step="any"
        min={rate ? undefined : "0"}
        placeholder="不限制"
        value={rule[key] == null ? "" : rate ? rule[key] * 100 : rule[key]}
        onChange={(e) =>
          change(
            key,
            e.target.value === ""
              ? undefined
              : Number(e.target.value) / (rate ? 100 : 1),
          )
        }
      />
    </Field>
  );
  return (
    <>
      <div className="channel-row">
        {["local", "feishu", "telegram"].map((c) => {
          const info = channels.find((x) => x.channel === c);
          return (
            <div className="channel-card" key={c}>
              <span className="channel-icon">
                {c === "local" ? (
                  <Bell size={21} />
                ) : c === "telegram" ? (
                  <Send size={21} />
                ) : (
                  <Radio size={21} />
                )}
              </span>
              <span>
                <strong>
                  {c === "local"
                    ? "本地通知中心"
                    : c === "feishu"
                      ? "飞书机器人"
                      : "Telegram Bot"}
                </strong>
                <small>
                  {c === "local"
                    ? "默认渠道 · 消息本地保存"
                    : info?.configured
                      ? "凭证已配置"
                      : "等待后端配置私有凭证"}
                </small>
              </span>
              <Badge
                tone={c === "local" || info?.configured ? "green" : "neutral"}
              >
                {c === "local" || info?.configured ? "可用" : "未配置"}
              </Badge>
            </div>
          );
        })}
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>渠道测试</h2>
            <p>只向所选渠道发送测试消息，真实发送需配置凭证</p>
          </div>
          <div className="save-controls">
            <select
              aria-label="测试渠道"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              <option value="local">本地通知中心</option>
              <option value="feishu">飞书</option>
              <option value="telegram">Telegram</option>
            </select>
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void test()}
            >
              <Send size={15} />
              发送测试
            </button>
          </div>
        </div>
      </section>
      {error && <Notice error>{error}</Notice>}
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>
              提醒规则 <span className="count">{rules.length}</span>
            </h2>
            <p>机会阈值、持续时间与冷却共同控制触发</p>
          </div>
          <button
            className="button primary"
            onClick={() => {
              setRule(initial);
              setAssetsText("");
              setPlatformText("");
              setEditing(!editing);
            }}
          >
            {editing ? <X size={15} /> : <Bell size={15} />}{" "}
            {editing ? "关闭编辑" : "新建规则"}
          </button>
        </div>
        {editing && (
          <form className="rule-form" onSubmit={(e) => void save(e)}>
            <div className="form-grid three">
              <Field label="规则名称">
                <input
                  required
                  value={rule.name}
                  onChange={(e) => change("name", e.target.value)}
                />
              </Field>
              <Field label="事件类别">
                <select
                  value={rule.category}
                  onChange={(e) => change("category", e.target.value)}
                >
                  <option value="opportunity">交易机会</option>
                  <option value="system">系统故障</option>
                  <option value="market">市场变化</option>
                </select>
              </Field>
              <Field label="优先级">
                <select
                  value={rule.priority}
                  onChange={(e) => change("priority", e.target.value)}
                >
                  <option value="low">低</option>
                  <option value="normal">普通</option>
                  <option value="high">高</option>
                </select>
              </Field>
              <Field label="资产范围（逗号分隔）">
                <input
                  placeholder="留空为全部"
                  value={assetsText}
                  onChange={(e) => setAssetsText(e.target.value)}
                />
              </Field>
              <Field label="平台范围（逗号分隔）">
                <input
                  placeholder="留空为全部"
                  value={platformText}
                  onChange={(e) => setPlatformText(e.target.value)}
                />
              </Field>
              <Field label="策略范围">
                <select
                  value={rule.strategies?.[0] ?? ""}
                  onChange={(e) =>
                    change("strategies", e.target.value ? [e.target.value] : [])
                  }
                >
                  <option value="">全部策略</option>
                  {Object.entries(strategies).map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="事件类型">
                <select
                  value={rule.event_types?.[0] ?? ""}
                  onChange={(e) =>
                    change(
                      "event_types",
                      e.target.value ? [e.target.value] : [],
                    )
                  }
                >
                  <option value="">类别内全部事件</option>
                  {[
                    ["data_stale", "数据过期"],
                    ["disconnected", "行情断连"],
                    ["collection_failure", "持续采集失败"],
                    ["new_market", "新增市场"],
                    ["opportunity_invalidated", "机会失效"],
                    ["opportunity_changed", "机会变化"],
                  ].map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              {numeric("最低净收益 / 计价币", "min_net_profit")}
              {numeric("最低价差 / %", "min_spread", true)}
              {numeric("最低资金费差 / %", "min_funding_spread", true)}
              {numeric("最大回本时间 / 小时", "max_breakeven_hours")}
              {numeric("计算名义金额", "notional")}
              {numeric("最低可成交容量", "min_capacity")}
              {numeric("最大数据年龄 / 秒", "max_age_seconds")}
              {numeric("持续命中 / 秒", "duration_seconds")}
              {numeric("连续命中次数", "consecutive_hits")}
              {numeric("通知冷却 / 秒", "cooldown_seconds")}
              {numeric("最小变化幅度 / %", "min_change_fraction", true)}
              <Field label="通知时段起点 / UTC">
                <input
                  type="time"
                  value={rule.window_start ?? ""}
                  onChange={(e) =>
                    change("window_start", e.target.value || undefined)
                  }
                />
              </Field>
              <Field label="通知时段终点 / UTC">
                <input
                  type="time"
                  value={rule.window_end ?? ""}
                  onChange={(e) =>
                    change("window_end", e.target.value || undefined)
                  }
                />
              </Field>
            </div>
            <div className="inline-checks">
              {[
                ["local", "本地通知"],
                ["feishu", "飞书"],
                ["telegram", "Telegram"],
              ].map(([id, label]) => (
                <label key={id}>
                  <input
                    type="checkbox"
                    checked={rule.channels.includes(id)}
                    onChange={(e) =>
                      change(
                        "channels",
                        e.target.checked
                          ? [...rule.channels, id]
                          : rule.channels.filter((v: string) => v !== id),
                      )
                    }
                  />
                  {label}
                </label>
              ))}
              <label>
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  onChange={(e) => change("enabled", e.target.checked)}
                />
                启用规则
              </label>
              <button
                type="submit"
                disabled={busy || !rule.channels.length}
                className="button primary"
              >
                <Save size={15} />
                保存规则
              </button>
            </div>
          </form>
        )}
        {rules.length ? (
          <div className="rule-list">
            {rules.map((r) => (
              <div key={r.id}>
                <span className="rule-state">
                  <i className={`dot ${r.enabled ? "green" : "amber"}`} />
                </span>
                <span className="rule-text">
                  <strong>
                    {r.name}{" "}
                    <Badge>
                      {r.category === "system"
                        ? "系统故障"
                        : r.category === "market"
                          ? "市场变化"
                          : "机会提醒"}
                    </Badge>
                  </strong>
                  <small>
                    {r.assets?.length ? r.assets.join("、") : "全部资产"} ·{" "}
                    {r.channels?.join(" / ")} · {r.cooldown_seconds}s 冷却 ·{" "}
                    {r.consecutive_hits} 次命中
                  </small>
                </span>
                <button
                  className="button secondary"
                  onClick={() => {
                    setRule(r);
                    setAssetsText(r.assets?.join(", ") ?? "");
                    setPlatformText(r.platforms?.join(", ") ?? "");
                    setEditing(true);
                  }}
                >
                  编辑
                </button>
                <button
                  className="icon-button"
                  aria-label={`删除规则 ${r.name}`}
                  onClick={() => void remove(r.id)}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          !editing && (
            <Empty
              title="尚未设置提醒规则"
              text="新建机会或系统故障规则。消息默认只进入本地通知中心。"
              icon={Bell}
            />
          )
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>本地通知与发送记录</h2>
            <p>队列状态、重试次数与发送失败均可追踪</p>
          </div>
          <button
            className="icon-button"
            aria-label="刷新通知记录"
            onClick={() => void load()}
          >
            <RefreshCw size={16} />
          </button>
        </div>
        {history.length ? (
          <div className="notification-list">
            {history.map((n) => (
              <article key={n.id}>
                <span className="notification-icon">
                  <Bell size={18} />
                </span>
                <div>
                  <h3>
                    {n.title} <Badge>{n.priority}</Badge>
                  </h3>
                  <p className="notification-message">{n.message}</p>
                  <small>
                    {time(n.created_at)} · 事件 {n.event_id}
                  </small>
                  <div className="deliveries">
                    {deliveries
                      .filter((d) => d.notification_id === n.id)
                      .map((d) => (
                        <div key={d.id}>
                          <Badge
                            tone={
                              d.status === "sent"
                                ? "green"
                                : d.status === "failed"
                                  ? "amber"
                                  : "neutral"
                            }
                          >
                            {d.channel} ·{" "}
                            {(
                              {
                                sent: "已发送",
                                expired: "已过期，未推送",
                                queued: "排队中",
                                pending: "待发送",
                                retry: "等待重试",
                                failed: "发送失败",
                                delivered: "已送达",
                              } as Record<string, string>
                            )[d.status] ?? d.status}
                          </Badge>
                          <span>
                            尝试 {d.attempts} 次
                            {d.sent_at && ` · ${time(d.sent_at)}`}
                            {d.last_error && ` · ${d.last_error}`}
                          </span>
                        </div>
                      ))}
                  </div>
                  <details className="raw-details">
                    <summary>事件明细</summary>
                    <pre>{JSON.stringify(n.event, null, 2)}</pre>
                  </details>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <Empty
            title="通知中心很安静"
            text="触发的规则和测试消息会保存在这里。"
            icon={Bell}
          />
        )}
      </section>
    </>
  );
}

function FieldTime({
  snapshot,
  field,
  now,
  staleSeconds,
}: {
  snapshot: Snapshot;
  field: string;
  now: number;
  staleSeconds: number;
}) {
  const sourceAt = snapshot.field_timestamps?.[field];
  const receivedAt = snapshot.field_received_at?.[field];
  if (sourceAt == null && receivedAt == null) return <>字段时间未提供</>;
  const at = fieldTimestamp(snapshot, field);
  const stale =
    !!snapshot.field_stale?.[field] || now - at > staleSeconds * 1000;
  return (
    <span className={stale ? "warning" : ""}>
      {age(at, now)}
      {stale ? " · 过期" : ""}
      {sourceAt == null ? " · 来源时间未提供" : ""}
    </span>
  );
}
function FieldAges({
  snapshot,
  now,
  staleSeconds,
}: {
  snapshot?: Snapshot;
  now: number;
  staleSeconds: number;
}) {
  return (
    <div className="field-ages">
      <span>字段更新时间</span>
      {(["bid", "ask", "funding_rate", "mark"] as const).map((key) => {
        const label = {
          bid: "买一",
          ask: "卖一",
          funding_rate: "资金费",
          mark: "标记价",
        }[key];
        const sourceAt = snapshot?.field_timestamps?.[key];
        const receivedAt = snapshot?.field_received_at?.[key];
        const at =
          snapshot && (sourceAt != null || receivedAt != null)
            ? fieldTimestamp(snapshot, key)
            : undefined;
        return (
          <span
            key={key}
            className={at && now - at > staleSeconds * 1000 ? "warning" : ""}
          >
            {label}：
            {at
              ? `${age(at, now)}${now - at > staleSeconds * 1000 ? " · 过期" : ""}${sourceAt == null ? " · 来源时间未提供" : ""}`
              : "字段时间未提供"}
          </span>
        );
      })}
    </div>
  );
}
function AssetMappings({
  instruments,
  assets,
  onSave,
}: {
  instruments: Instrument[];
  assets: Asset[];
  onSave: () => void;
}) {
  const [instrument, setInstrument] = useState("");
  const [asset, setAsset] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [audit, setAudit] = useState<Json[]>([]);
  const load = () =>
    api<{ audit: Json[] }>("/api/assets")
      .then((data) => setAudit(data.audit ?? []))
      .catch((e) => setError(e.message));
  useEffect(() => {
    void load();
  }, []);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ audit: Json[] }>(
        "/api/assets/mappings",
        "PUT",
        { instrument_id: instrument, asset_id: asset.trim() },
      );
      setAudit(result.audit ?? []);
      onSave();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>资产身份与人工映射</h2>
          <p>
            相同 ticker
            不自动视作同一资产。核对链、合约和包装关系后再合并；修改保存审计记录。
          </p>
        </div>
        <ShieldCheck size={18} />
      </div>
      <form onSubmit={(e) => void save(e)}>
        <div className="form-grid">
          <Field label="需要修正的独立市场">
            <select
              required
              value={instrument}
              onChange={(e) => {
                setInstrument(e.target.value);
                setAsset(
                  instruments.find((i) => i.id === e.target.value)?.asset_id ??
                    "",
                );
              }}
            >
              <option value="">选择市场</option>
              {instruments.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.venue} · {i.native_symbol} · {i.asset_id}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="核验后的内部资产 ID"
            hint="选择现有身份或输入一个新的独立 ID"
          >
            <input
              required
              list="asset-identities"
              value={asset}
              onChange={(e) => setAsset(e.target.value)}
              placeholder="例如 asset:bitcoin"
            />
            <datalist id="asset-identities">
              {assets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.symbol}
                </option>
              ))}
            </datalist>
          </Field>
        </div>
        <div className="panel-foot">
          <span>
            {instruments.find((i) => i.id === instrument)?.chain ??
              "链信息未提供"}{" "}
            · 当前映射状态：
            {instruments.find((i) => i.id === instrument)?.mapping_verified
              ? "已核验"
              : "待核验"}
          </span>
          <button
            type="submit"
            className="button secondary"
            disabled={busy || !instrument}
          >
            <Save size={15} />
            保存人工映射
          </button>
        </div>
      </form>
      {error && <Notice error>{error}</Notice>}
      <details className="raw-details">
        <summary>映射审计记录（{audit.length}）</summary>
        {audit.length ? (
          <pre>{JSON.stringify(audit, null, 2)}</pre>
        ) : (
          <p className="muted">尚无人工修改。</p>
        )}
      </details>
    </section>
  );
}
function Monitors({
  version,
  toast,
}: {
  version: number;
  toast: (s: string) => void;
}) {
  const [monitors, setMonitors] = useState<Json[]>([]);
  const [results, setResults] = useState<Json[]>([]);
  const [error, setError] = useState("");
  const load = async () => {
    try {
      const data = await api<{ monitors: Json[]; results: Json[] }>(
        "/api/monitors",
      );
      setMonitors(data.monitors);
      setResults(data.results);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => clearInterval(timer);
  }, [version]);
  const update = async (m: Json) => {
    try {
      await api("/api/monitors", "POST", { ...m, enabled: !m.enabled });
      await load();
      toast(m.enabled ? "持续监控已暂停" : "持续监控已开启");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const remove = async (id: string) => {
    try {
      await api(`/api/monitors/${encodeURIComponent(id)}`, "DELETE");
      await load();
      toast("持续监控已删除");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>
            持续机会监控 <span className="count">{monitors.length}</span>
          </h2>
          <p>
            以保存的基础资产数量、成本及假设重新获取公开行情，满足通知规则时触发提醒；演示模式暂停监控。
          </p>
        </div>
        <button
          className="icon-button"
          aria-label="刷新持续监控"
          onClick={() => void load()}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      {error && <Notice error>{error}</Notice>}
      {monitors.length ? (
        <div className="monitor-list">
          {monitors.map((m) => {
            const result = results.find((r) => r.monitor_id === m.id);
            return (
              <article key={m.id}>
                <div className="monitor-title">
                  <div>
                    <strong>{m.name}</strong>
                    <small>
                      {num(m.calculation.size_base, 8)} BASE · 每{" "}
                      {m.interval_seconds}s 计算 · {time(result?.at)}
                    </small>
                  </div>
                  <Badge tone={m.enabled ? "green" : "neutral"}>
                    {m.enabled ? "已启用" : "已暂停"}
                  </Badge>
                  <button
                    className="button secondary"
                    onClick={() => void update(m)}
                  >
                    {m.enabled ? "暂停" : "开启"}
                  </button>
                  <button
                    className="icon-button"
                    aria-label={`删除持续监控 ${m.name}`}
                    onClick={() => void remove(m.id)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
                {result?.errors?.length > 0 && (
                  <Notice>{result?.errors?.join("；")}</Notice>
                )}
                {result?.opportunities?.map((o: Json) => (
                  <div className="monitor-result" key={o.id}>
                    <Badge
                      tone={o.status === "threshold_met" ? "green" : "amber"}
                    >
                      {stateName[o.status] ?? o.status}
                    </Badge>
                    <span>
                      净收益 {num(o.net_profit, 6)} {o.valuation_currency}
                    </span>
                    <small>
                      {[
                        ...(o.missing_prerequisites ?? []),
                        ...(o.invalid_reasons ?? []),
                      ].join("；") || "计算前置条件已满足"}
                    </small>
                  </div>
                ))}
                {!result && <p className="muted">等待下一次计算。</p>}
              </article>
            );
          })}
        </div>
      ) : (
        <Empty
          title="尚无持续监控"
          text="计算一次收益后，点击「持续监控此参数」将行情更新、计算结果与通知规则连接起来。"
          icon={Activity}
        />
      )}
    </section>
  );
}

function PoolInfo({ instrumentId }: { instrumentId: string }) {
  const [pool, setPool] = useState<Json | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setPool(null);
    setError("");
    void api<{ pools: Json[] }>("/api/pools")
      .then((data) => {
        if (alive)
          setPool(data.pools.find((p) => p.id === instrumentId) ?? null);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [instrumentId]);
  return (
    <div className="pool-info">
      {error && <Notice error>池子元数据：{error}</Notice>}
      {pool ? (
        <>
          <h3>
            链上池子登记{" "}
            <Badge tone={pool.verified ? "green" : "amber"}>
              {pool.verified ? "已核验" : "池身份待核验"}
            </Badge>
          </h3>
          <dl className="source-list">
            <dt>协议 / 版本</dt>
            <dd>
              {pool.protocol} / {pool.version}
            </dd>
            <dt>链 / 池类型</dt>
            <dd>
              {pool.chain} / {pool.pool_type}
            </dd>
            <dt>池合约地址</dt>
            <dd className="break mono">{pool.address}</dd>
            <dt>资产 0 合约</dt>
            <dd className="break mono">{pool.asset0}</dd>
            <dt>资产 1 合约</dt>
            <dd className="break mono">{pool.asset1}</dd>
            <dt>池手续费</dt>
            <dd>{pct(pool.fee_rate)}</dd>
            <dt>流动性 / USD</dt>
            <dd>{num(pool.liquidity_usd)}</dd>
            <dt>报价区块</dt>
            <dd>{num(pool.block_number, 0)}</dd>
            <dt>区块时间</dt>
            <dd>{time(pool.block_time)}</dd>
          </dl>
        </>
      ) : (
        !error && <p className="muted">来源尚未提供此市场的池子元数据。</p>
      )}
    </div>
  );
}
function CalculationDetails({ opportunity: o }: { opportunity: Json }) {
  return (
    <details className="calculation-details">
      <summary>
        成交容量、方向与费用明细{" "}
        <span>
          基础规模 {num(o.size_base, 8)} · 最大可计算容量{" "}
          {num(o.capacity_base, 8)}
        </span>
      </summary>
      {o.legs?.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>方向 / 平台</th>
                <th>可成交均价 / 报价币</th>
                <th>成交基础数量 / 最大容量</th>
                <th>深度状态</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {o.legs.map((leg: Json, idx: number) => (
                <tr key={idx}>
                  <td>
                    {leg.side === "buy" ? "买入 / 做多" : "卖出 / 做空"} ·{" "}
                    {leg.venue}
                  </td>
                  <td className="mono">
                    {num(leg.average_price, 8)} {leg.quote_currency}
                  </td>
                  <td className="mono">
                    {num(leg.filled_base, 8)} / {num(leg.capacity_base, 8)}
                  </td>
                  <td>
                    <Badge tone={leg.complete ? "green" : "amber"}>
                      {leg.complete ? "规模已覆盖" : "深度不足"}
                    </Badge>
                  </td>
                  <td className="wrap">
                    {leg.source}
                    <small>
                      {time(leg.source_at ?? leg.received_at)}
                      {leg.block_number ? ` · 区块 ${leg.block_number}` : ""}
                    </small>
                    {leg.route && <small>路线 {leg.route.join(" → ")}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {o.costs?.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>成本项</th>
                <th>原始金额 / 币种</th>
                <th>计价金额 / {o.valuation_currency}</th>
                <th>来源 / 适用时间</th>
              </tr>
            </thead>
            <tbody>
              {o.costs.map((cost: Json, idx: number) => (
                <tr key={idx}>
                  <td>
                    {cost.name}
                    {cost.included_in_price && <small>已包含于成交价格</small>}
                  </td>
                  <td className="mono">
                    {num(cost.amount, 8)} {cost.currency}
                  </td>
                  <td className="mono">{num(cost.valuation_amount, 8)}</td>
                  <td>
                    {cost.source ?? "未提供"}
                    <small>{time(cost.at)}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {o.assumptions?.length > 0 && (
        <ul className="assumptions">
          {o.assumptions.map((a: string, idx: number) => (
            <li key={idx}>{a}</li>
          ))}
        </ul>
      )}
    </details>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
