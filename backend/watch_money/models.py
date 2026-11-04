"""Shared data contracts. Rates are fractions; timestamps are UTC milliseconds."""
import math
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_ms() -> int:
    return int(time.time() * 1000)

class Model(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

class Asset(Model):
    id: str
    symbol: str
    name: str | None = None
    chain: str | None = None
    address: str | None = None
    decimals: int | None = None
    relationship: str | None = None
    verified: bool = False

class Instrument(Model):
    id: str
    venue: str
    native_symbol: str
    asset_id: str
    native_asset_id: str | None = None
    native_currency_id: str | None = None
    quote_asset_id: str | None = None
    native_quote_asset_id: str | None = None
    quote_identity_source: str | None = None
    quote_identity_at: int | None = None
    quote_identity_expires_at: int | None = None
    base: str
    quote: str
    settle: str | None = None
    kind: Literal['spot', 'perpetual', 'pool', 'rfq']
    mechanism: Literal['orderbook', 'amm', 'rfq'] = 'orderbook'
    contract_size: float = Field(default=1, gt=0)
    linear: bool = True
    chain: str | None = None
    active: bool = True
    price_precision: float | None = None
    amount_precision: float | None = None
    # Exchange order amount: contracts for perpetuals, base units for spot.
    min_amount: float | None = Field(default=None, ge=0)
    min_notional: float | None = None
    index_source: str | None = None
    mapping_verified: bool = False
    mapping_evidence_ids: list[str] = Field(default_factory=list)
    mapping_verified_at: int | None = None
    mapping_expires_at: int | None = None
    mapping_evidence_status: str | None = None

class Pool(Model):
    id: str
    chain: str
    protocol: str
    version: str
    address: str
    asset0: str
    asset1: str
    fee_rate: float | None = None
    pool_type: str
    liquidity_usd: float | None = None
    block_number: int | None = None
    block_time: int | None = None
    verified: bool = False

class MarketSnapshot(Model):
    instrument_id: str
    venue: str
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    last: float | None = Field(default=None, gt=0)
    mark: float | None = Field(default=None, gt=0)
    index: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)
    volume_unit: str | None = None
    open_interest: float | None = Field(default=None, ge=0)
    oi_unit: str | None = None
    funding_rate: float | None = None
    funding_kind: Literal['settled', 'current', 'predicted'] | None = None
    funding_interval_hours: float | None = Field(default=None, gt=0)
    next_funding_at: int | None = None
    source_at: int | None = None
    received_at: int = Field(default_factory=now_ms)
    source: str
    # Explicit unavailable quote observations; omission remains a sparse no-op.
    cleared_fields: list[Literal['bid', 'ask']] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)
    field_timestamps: dict[str, int | None] = Field(default_factory=dict)
    field_received_at: dict[str, int] = Field(default_factory=dict)
    protocol_metrics: dict[str, float | str | None] = Field(default_factory=dict)
    protocol_metric_sources: dict[str, str] = Field(default_factory=dict)
    protocol_metric_timestamps: dict[str, int | None] = Field(default_factory=dict)
    protocol_metric_received_at: dict[str, int] = Field(default_factory=dict)
    cleared_protocol_metrics: list[str] = Field(default_factory=list)
    valid: bool = True
    issues: list[str] = Field(default_factory=list)
    is_demo: bool = False

    @model_validator(mode='after')
    def clear_only_missing_quotes(self):
        if any(getattr(self, field) is not None for field in self.cleared_fields):
            raise ValueError('Cleared quote fields must have no value')
        if any(self.protocol_metrics.get(key) is not None for key in self.cleared_protocol_metrics):
            raise ValueError('Cleared protocol metrics must have no value')
        return self

class OrderBook(Model):
    instrument_id: str
    bids: list[tuple[float, float]] = Field(default_factory=list)
    asks: list[tuple[float, float]] = Field(default_factory=list)
    received_at: int = Field(default_factory=now_ms)
    source_at: int | None = None
    quantity_unit: Literal['base', 'contracts'] = 'base'
    source: str
    is_demo: bool = False

    @model_validator(mode='after')
    def valid_levels(self):
        for levels in (self.bids, self.asks):
            if any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0 for p, q in levels):
                raise ValueError('Order book prices and quantities must be finite and positive')
        if self.bids != sorted(self.bids, reverse=True) or self.asks != sorted(self.asks):
            raise ValueError('Order book must be sorted best price first')
        if self.bids and self.asks and self.bids[0][0] >= self.asks[0][0]:
            raise ValueError('Crossed order book is not executable')
        return self

class VenueStatus(Model):
    venue: str
    state: str = 'idle'
    last_received_at: int | None = None
    last_attempt_at: int | None = None
    retries: int = 0
    rate_limited: int = 0
    parse_errors: int = 0
    subscriptions: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    message: str | None = None
    transport: str = 'REST'
    stream_state: str = 'idle'
    stream_reconnects: int = 0
    stream_requested_markets: int = 0
    stream_selected_markets: int = 0
    stream_truncated: bool = False
    stream_received_patches: int = 0
    stream_observer_dropped: int = 0
    stream_last_patch_at: int | None = None
    stream_last_gap_seconds: float | None = None
    stream_max_gap_seconds: float | None = None
    stream_last_error_type: str | None = None
    stream_last_error_at: int | None = None
    funding_state: str = 'idle'
    funding_subscriptions: int = 0
    funding_last_received_at: int | None = None
    funding_retries: int = 0
    funding_errors: dict[str, str] = Field(default_factory=dict)
    funding_bulk_markets: int = 0
    funding_fallback_markets: int = 0
    funding_bulk_groups: int = 0
    funding_observed_markets: int = 0
    funding_fresh_markets: int = 0
    funding_coverage_ratio: float = 0
    funding_cycle_seconds: float | None = None
    funding_estimated_cycle_seconds: float | None = None
    funding_last_cycle_at: int | None = None
    funding_last_attempt_at: int | None = None
    funding_mode: str | None = None
    funding_effective_poll_seconds: float | None = None
    funding_schedule_reason: str | None = None
    funding_arrival_source_age_p90_seconds: float | None = None
    funding_arrival_source_age_max_seconds: float | None = None
    funding_arrival_source_age_samples: int = 0
    funding_arrival_checked_at: int | None = None
    funding_source_stale_markets: int = 0
    funding_source_missing_markets: int = 0
    funding_source_future_markets: int = 0
    light_state: str = 'idle'
    light_subscriptions: int = 0
    light_last_received_at: int | None = None
    light_retries: int = 0
    light_bulk_markets: int = 0
    light_fallback_markets: int = 0
    light_bulk_groups: int = 0
    light_observed_markets: int = 0
    light_fresh_markets: int = 0
    light_coverage_ratio: float = 0
    light_cycle_seconds: float | None = None
    light_estimated_cycle_seconds: float | None = None
    light_last_cycle_at: int | None = None
    light_last_attempt_at: int | None = None
    light_mode: str | None = None
    detail_errors: dict[str, str] = Field(default_factory=dict)
    discovery_state: str | None = None
    discovery_complete: bool = False
    discovery_errors: dict[str, str] = Field(default_factory=dict)
    light_errors: dict[str, str] = Field(default_factory=dict)

class Settings(Model):
    enabled_venues: list[str] = Field(default_factory=lambda: ['binance', 'okx', 'bybit', 'bitget', 'gate', 'mexc', 'kucoin', 'coinbase', 'kraken', 'htx', 'deribit', 'upbit', 'hyperliquid', 'lighter', 'dydx', 'paradex', 'aster', 'edgex', 'variational', 'gmx', 'jupiter-perps', 'drift', 'uniswap', 'pancakeswap', 'aerodrome', 'curve', 'raydium', 'orca'])
    watch_assets: list[str] = Field(default_factory=lambda: ['BTC', 'ETH', 'SOL'])
    poll_seconds: float = Field(default=15, ge=5, le=3600)
    refresh_ms: int = Field(default=1000, ge=500, le=60000)
    stale_seconds: float = Field(default=60, ge=5, le=86400)
    retention_days: int = Field(default=30, ge=1, le=3650)
    collection_enabled: bool = True
    history_enabled: bool = True
    computation_enabled: bool = True
    notifications_enabled: bool = True
    demo_mode: bool = False
    prefer_websocket: bool = True
    broad_collection_enabled: bool = True
    broad_poll_seconds: float = Field(default=30, ge=10, le=3600)
    funding_poll_seconds: float = Field(default=30, ge=10, le=3600)
    discovery_seconds: float = Field(default=1800, ge=300, le=21600)
    broad_batch_size: int = Field(default=100, ge=1, le=100)
    broad_symbol_batch_size: int = Field(default=20, ge=1, le=100)
    broad_inter_batch_seconds: float = Field(default=.25, ge=0, le=60)
    broad_request_timeout_seconds: float = Field(default=25, ge=5, le=90)
    recording_enabled: bool = False
    recording_venues: list[str] = Field(default_factory=list)
    recording_max_mb: int = Field(default=100, ge=1, le=10000)
    recording_retention_days: int = Field(default=3, ge=1, le=30)
    rpc_urls: dict[str, str] = Field(default_factory=dict)
    dex_search_queries: dict[str, list[str]] = Field(default_factory=dict)

class CoverageEntry(Model):
    venue: str
    category: str
    products: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)
    method: str
    capabilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: str
    docs_url: str
    last_verified_at: str | None = None
