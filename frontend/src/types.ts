export interface Instrument {
  id: string;
  venue: string;
  native_symbol: string;
  asset_id: string;
  base: string;
  quote: string;
  settle?: string;
  kind: "spot" | "perpetual" | "pool" | "rfq";
  mechanism: "orderbook" | "amm" | "rfq";
  contract_size: number;
  linear: boolean;
  chain?: string;
  active: boolean;
  mapping_verified: boolean;
  index_source?: string;
  min_notional?: number;
}
export interface Snapshot {
  instrument_id: string;
  venue: string;
  bid?: number | null;
  ask?: number | null;
  last?: number | null;
  mark?: number | null;
  index?: number | null;
  mid?: number | null;
  volume?: number | null;
  volume_unit?: string;
  open_interest?: number | null;
  oi_unit?: string;
  funding_rate?: number | null;
  funding_kind?: string;
  funding_interval_hours?: number | null;
  funding_8h?: number | null;
  next_funding_at?: number | null;
  source_at?: number | null;
  received_at: number;
  source: string;
  field_sources: Record<string, string>;
  field_timestamps?: Record<string, number | null>;
  field_received_at?: Record<string, number>;
  field_ages_seconds?: Record<string, number>;
  field_stale?: Record<string, boolean>;
  protocol_metrics?: Record<string, number | string | null>;
  valid: boolean;
  issues: string[];
  is_demo: boolean;
  age_seconds?: number;
  stale?: boolean;
}
export interface MarketData {
  instruments: Instrument[];
  snapshots: Snapshot[];
}
export interface VenueStatus {
  venue: string;
  state: string;
  transport: string;
  stream_state: string;
  stream_reconnects: number;
  last_received_at?: number;
  last_attempt_at?: number;
  retries: number;
  rate_limited: number;
  parse_errors: number;
  subscriptions: number;
  missing_fields: string[];
  message?: string;
}
export interface Coverage {
  venue: string;
  category: string;
  products: string[];
  chains: string[];
  method: string;
  capabilities: string[];
  requirements: string[];
  limitations: string[];
  status: string;
  docs_url: string;
  last_verified_at?: string;
}
export interface Settings {
  enabled_venues: string[];
  watch_assets: string[];
  poll_seconds: number;
  refresh_ms: number;
  stale_seconds: number;
  retention_days: number;
  collection_enabled: boolean;
  prefer_websocket: boolean;
  history_enabled: boolean;
  computation_enabled: boolean;
  notifications_enabled: boolean;
  demo_mode: boolean;
  rpc_urls: Record<string, string>;
}
export interface Book {
  instrument_id: string;
  bids: [number, number][];
  asks: [number, number][];
  received_at: number;
  source_at?: number;
  quantity_unit: string;
  source: string;
}
export interface Asset {
  id: string;
  symbol: string;
  name?: string;
  chain?: string;
  address?: string;
  verified: boolean;
}
export type Json = Record<string, any>;
