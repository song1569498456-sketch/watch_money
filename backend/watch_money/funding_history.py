"""Manually refreshed public settled funding records, separate from snapshot samples."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any
from datetime import datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request

from .adapters.cex import CexAdapter, _number, _timestamp
from .models import now_ms
from .adapters.dex import DydxAdapter, HyperliquidAdapter

router = APIRouter(prefix="/api/funding", tags=["funding-history"])

# Explicitly reviewed public historical settlement contracts. A generic CCXT
# capability alone does not prove that a trailing/predicted rate was settled.
SETTLED_ENDPOINTS = {
    "htx": (50, "https://huobiapi.github.io/docs/coin_margined_swap/v1/en/#query-historical-funding-rate"),
    "bitget": (100, "https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-historical-funding-rates"),
    "gate": (100, "https://www.gate.com/docs/developers/apiv4/en/futures/#futures-market-historical-funding-rate"),
    "binanceusdm": (1000, "https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History"),
    "binancecoinm": (1000, "https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Rate-History-of-Perpetual-Futures"),
    "okx": (100, "https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history"),
    "bybit": (200, "https://bybit-exchange.github.io/docs/v5/market/history-fund-rate"),
    "mexc": (100, "https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate-history"),
    "kucoinfutures": (100, "https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history"),
}


HTX_LINEAR_HISTORY_PATH = "linear-swap-api/v1/swap_historical_funding_rate"
HTX_LINEAR_HISTORY_DOCS = "https://huobiapi.github.io/docs/usdt_swap/v1/en/#general-query-historical-funding-rate"


class FundingHistoryService:
    def __init__(self, data_dir: str | Path, collector: Any, *, timeout_seconds: float = 15):
        self.collector = collector
        self.timeout_seconds = timeout_seconds
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "funding_history.sqlite3"
        self._db_lock = RLock()
        self._refresh_lock = asyncio.Lock()
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS funding_records (
                    instrument_id TEXT NOT NULL, settlement_at INTEGER NOT NULL,
                    event_type TEXT NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY(instrument_id,settlement_at,event_type)
                );
                CREATE TABLE IF NOT EXISTS funding_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, instrument_id TEXT NOT NULL,
                    settlement_at INTEGER NOT NULL, event_type TEXT NOT NULL,
                    previous_record TEXT NOT NULL, revised_at INTEGER NOT NULL
                );
            """)
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def _read(self, instrument_id: str, since: int | None, limit: int, before_height: int | None = None) -> tuple[list[dict], int]:
        condition = "instrument_id=?"
        values: list[Any] = [instrument_id]
        if since is not None:
            condition += " AND settlement_at>=?"
            values.append(since)
        if before_height is not None:
            condition += " AND CAST(json_extract(record, '$.native_block_height') AS INTEGER)<=?"
            values.append(before_height)
        with self._db_lock, closing(self._connect()) as db:
            count = db.execute(f"SELECT COUNT(*) FROM funding_records WHERE {condition}", values).fetchone()[0]
            direction = "ASC" if since is not None else "DESC"
            rows = db.execute(
                f"SELECT record FROM funding_records WHERE {condition} ORDER BY settlement_at {direction},event_type LIMIT ?",
                [*values, limit]).fetchall()
        records = [json.loads(row[0]) for row in rows]
        return sorted(records, key=lambda row: (row["settlement_at"], row["event_type"])), count

    def _save(self, records: list[dict]) -> None:
        with self._db_lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            for record in records:
                key = (record["instrument_id"], record["settlement_at"], record["event_type"])
                prior_row = db.execute("SELECT record FROM funding_records WHERE instrument_id=? AND settlement_at=? AND event_type=?", key).fetchone()
                if prior_row:
                    prior = json.loads(prior_row[0])
                    # Provenance changes are revisions even when the rate is
                    # unchanged (e.g. a corrected native block height).
                    observation_fields = {"received_at", "first_received_at", "last_seen_at", "revision"}
                    changed = any(prior.get(field) != record.get(field) for field in
                                  (prior.keys() | record.keys()) - observation_fields)
                    if not changed:
                        prior["last_seen_at"] = record["received_at"]
                        record = prior
                    else:
                        db.execute("INSERT INTO funding_revisions(instrument_id,settlement_at,event_type,previous_record,revised_at) VALUES(?,?,?,?,?)",
                                   (*key, prior_row[0], record["received_at"]))
                        record["revision"] = prior["revision"] + 1
                        record["first_received_at"] = prior["first_received_at"]
                db.execute("INSERT INTO funding_records VALUES(?,?,?,?) ON CONFLICT(instrument_id,settlement_at,event_type) DO UPDATE SET record=excluded.record",
                           (*key, json.dumps(record, allow_nan=False)))
            db.commit()

    async def get(self, instrument_id: str, *, refresh: bool = False,
                  since: int | None = None, limit: int = 100, before_height: int | None = None) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise HTTPException(422, "limit must be between 1 and 1000")
        if since is not None and (isinstance(since, bool) or not isinstance(since, int) or not 0 <= since <= 2**63 - 1):
            raise HTTPException(422, "since must be a nonnegative epoch millisecond integer")
        instrument = self.collector.instruments.get(instrument_id)
        if instrument is None:
            raise HTTPException(404, "Unknown instrument")
        if before_height is not None:
            if (isinstance(before_height, bool) or not isinstance(before_height, int)
                    or not 1 <= before_height <= 2**53 - 1):
                raise HTTPException(422, "before_height must be a positive safe integer")
            if instrument.venue != "dydx":
                raise HTTPException(422, "before_height is supported only for dYdX native history")
        instrument = deepcopy(instrument)
        issues = []
        summary = {"refreshed": False, "requested_since": since, "limit": limit,
                   "requested_before_height": before_height, "next_before_height": None,
                   "pages": 0, "fetched_count": 0, "accepted_count": 0,
                   "rejected_count": 0, "next_since": None, "has_more": None,
                   "truncated": False, "issues": issues}
        if refresh:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    async with self._refresh_lock:
                        if self.collector.instruments.get(instrument_id) != instrument:
                            raise HTTPException(409, "Market scope changed while waiting to retrieve history")
                        summary.update(await self._refresh(instrument, since, limit, before_height=before_height))
            except TimeoutError:
                raise HTTPException(504, "Public funding history request timed out") from None
        records, count = await asyncio.to_thread(self._read, instrument_id, since, limit, before_height)
        summary.update(count=len(records), cached_matching_count=count,
                       earliest_settlement_at=records[0]["settlement_at"] if records else None,
                       latest_settlement_at=records[-1]["settlement_at"] if records else None)
        summary["truncated"] = summary["truncated"] or count > limit
        return {"instrument_id": instrument_id, "records": records, "summary": summary,
                "coverage": {"kind": "exchange_settled_funding_history", "all_history": False,
                             "background_backfill": False, "pagination": "one_manual_page",
                             "cursor_semantics": "dydx_backward_height_hint_not_gap_proof" if instrument.venue == "dydx"
                                                 else "forward_since_hint_only_not_gap_proof",
                             "direction": "positive_longs_pay_shorts_negative_shorts_pay_longs"}}

    async def _refresh_dydx(self, instrument, adapter, since, limit, before_height=None):
        native = instrument.native_symbol
        if (instrument.kind != "perpetual" or not instrument.active or instrument.chain != "dydx"
                or instrument.id != f"dydx:perpetual:{native}"
                or native != f"{instrument.base}-{instrument.quote}"
                or instrument.settle != "USDC"):
            raise HTTPException(409, "Discover the exact dYdX v4 market first")
        page_limit = min(limit, 100)
        endpoint = f"{adapter.endpoint}/historicalFunding/{quote(native, safe='')}"
        params = {"limit": page_limit}
        if before_height is not None:
            params["effectiveBeforeOrAtHeight"] = before_height
        try:
            payload = await adapter.request("GET", endpoint, params=params)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403, 451}:
                raise HTTPException(403, "Public history access restricted; no alternate route attempted") from None
            if isinstance(exc, TimeoutError) or type(exc).__name__ in {"ReadTimeout", "ConnectTimeout"}:
                raise HTTPException(504, "Public funding history request timed out") from None
            raise HTTPException(502, {"error": "Public funding history request failed", "type": type(exc).__name__}) from None
        received = getattr(payload, "received_at", None)
        if received is None:
            received = now_ms()
        if (self.collector.instruments.get(instrument.id) != instrument
                or self.collector.adapters.get(instrument.venue) is not adapter):
            raise HTTPException(409, "Market scope changed while retrieving history")
        rows = payload.get("historicalFunding") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) > page_limit:
            raise HTTPException(502, "Malformed or oversized public funding history response")
        records, reasons = [], {}
        for row in rows:
            reason, stamp = None, None
            if not isinstance(row, dict):
                row, reason = {}, "malformed_record"
            raw_time = row.get("effectiveAt")
            try:
                parsed = datetime.fromisoformat(raw_time) if isinstance(raw_time, str) and "T" in raw_time else None
                if parsed is not None and parsed.tzinfo is not None:
                    stamp = _timestamp(int(parsed.timestamp() * 1000))
            except (ValueError, OverflowError, OSError):
                pass
            if row.get("ticker") != native:
                reason = reason or "wrong_native_market"
            if stamp is None or stamp > received:
                reason = reason or "invalid_or_future_settlement_time"
            elif since is not None and stamp < since:
                reason = reason or "before_requested_since"
            rate = _number(row.get("rate"))
            height = row.get("effectiveAtHeight")
            if not isinstance(height, str) or not height.isascii() or not height.isdigit():
                reason = reason or "invalid_native_height"
            if isinstance(height, str) and height.isascii() and height.isdigit():
                if len(height) > 16 or int(height) > 2**53 - 1:
                    reason = reason or "unsafe_native_height"
                elif before_height is not None and int(height) > before_height:
                    reason = reason or "after_requested_height"
            if rate is None:
                reason = reason or "missing_or_nonfinite_settled_rate"
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            records.append({"instrument_id": instrument.id, "venue": "dydx", "symbol": native,
                "native_market_id": native, "settle": instrument.settle, "settlement_at": stamp,
                "native_settlement_time": raw_time, "native_time_unit": "timezone_aware_iso8601",
                "native_block_height": height, "funding_rate": rate, "raw_funding_rate": str(row["rate"]),
                "rate_unit": "decimal_ratio_per_published_funding_tick", "funding_kind": "settled",
                "funding_interval_hours": None, "period_source": None,
                "long_cashflow_sign": -1 if rate > 0 else 1 if rate < 0 else 0,
                "short_cashflow_sign": 1 if rate > 0 else -1 if rate < 0 else 0,
                "source": endpoint, "source_docs": "https://docs.dydx.xyz/indexer-client/http#get-historical-funding",
                "event_type": "regular", "received_at": received, "first_received_at": received,
                "last_seen_at": received, "revision": 1, "is_demo": False})
        await asyncio.to_thread(self._save, records)
        lowest = min((int(row["native_block_height"]) for row in records), default=0)
        # Rejected rows may conceal an earlier boundary; do not skip over them.
        next_height = lowest - 1 if lowest > 1 and not reasons else None
        return {"refreshed": True, "next_before_height": next_height,
                "pages": 1, "page_limit": page_limit,
                "fetched_count": len(rows), "accepted_count": len(records),
                "rejected_count": sum(reasons.values()), "next_since": None, "has_more": None,
                "truncated": len(rows) >= page_limit or limit > page_limit,
                "issues": [f"rejected_{reason}:{count}" for reason, count in sorted(reasons.items())] + [
                    "One descending page; before_height is inclusive, since filters locally; cursor does not prove gap-free history",
                    "Historical funding epoch duration is unknown; governance can change the default hourly interval"]}

    async def _refresh_hyperliquid(self, instrument, adapter, since, limit):
        """One bounded market-history call; never userFunding or wallet data."""
        docs = "https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals#retrieve-historical-funding-rates"
        native = instrument.native_symbol
        namespace = native.split(":", 1)[0] if ":" in native else ""
        if (instrument.kind != "perpetual" or not instrument.active
                or instrument.id != f"hyperliquid:perpetual:{native}"
                or namespace not in adapter._namespaces
                or adapter._quotes.get(namespace) != instrument.settle
                or instrument.quote != instrument.settle or not instrument.settle):
            raise HTTPException(409, "Discover the exact Hyperliquid market and collateral namespace first")
        end = now_ms()
        page_limit = min(limit, 500)
        start = since if since is not None else max(0, end - page_limit * 3600000)
        if start > end:
            raise HTTPException(422, "History start must not be in the future")
        # Bound the time range as well as accepted rows; pagination is explicit.
        end = min(end, start + 500 * 3600000)
        try:
            rows = await adapter.info("fundingHistory", coin=native, startTime=start, endTime=end)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {401, 403, 451}:
                raise HTTPException(403, "Public history access restricted; no alternate route attempted") from None
            if isinstance(exc, TimeoutError) or type(exc).__name__ in {"ReadTimeout", "ConnectTimeout"}:
                raise HTTPException(504, "Public funding history request timed out") from None
            raise HTTPException(502, {"error": "Public funding history request failed", "type": type(exc).__name__}) from None
        received = getattr(rows, "received_at", None)
        if received is None:
            received = now_ms()
        current = self.collector.instruments.get(instrument.id)
        if (current != instrument or adapter._quotes.get(namespace) != instrument.settle
                or self.collector.adapters.get(instrument.venue) is not adapter):
            raise HTTPException(409, "Market scope changed while retrieving history")
        if not isinstance(rows, list) or len(rows) > 500:
            raise HTTPException(502, "Malformed or oversized public funding history response")
        records, reasons = [], {}
        for row in rows:
            reason = None
            if not isinstance(row, dict):
                row, reason = {}, "malformed_record"
            stamp = _timestamp(row.get("time"))
            rate = _number(row.get("fundingRate"))
            if row.get("coin") != native:
                reason = reason or "wrong_native_market"
            if stamp is None or row.get("time") != stamp:
                reason = reason or "invalid_native_millisecond_time"
            elif not start <= stamp <= min(end, received):
                reason = reason or "outside_requested_settlement_window"
            if rate is None:
                reason = reason or "missing_or_nonfinite_settled_rate"
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            records.append({"instrument_id": instrument.id, "venue": instrument.venue,
                "symbol": native, "native_market_id": native, "settle": instrument.settle,
                "settlement_at": stamp, "native_settlement_time": row["time"], "native_time_unit": "milliseconds",
                "funding_rate": rate, "raw_funding_rate": str(row["fundingRate"]),
                "rate_unit": "decimal_ratio_per_hourly_settlement", "funding_kind": "settled",
                "funding_interval_hours": 1, "period_source": "https://hyperliquid.gitbook.io/hyperliquid-docs/trading/funding",
                "long_cashflow_sign": -1 if rate > 0 else 1 if rate < 0 else 0,
                "short_cashflow_sign": 1 if rate > 0 else -1 if rate < 0 else 0,
                "source": f"hyperliquid:info:fundingHistory:{native}", "source_docs": docs,
                "event_type": "regular", "received_at": received, "first_received_at": received,
                "last_seen_at": received, "revision": 1, "is_demo": False})
        # Stable chronological paging: rejected or unsorted responses cannot
        # advance the cursor past valid rows omitted by the requested limit.
        records.sort(key=lambda record: record["settlement_at"])
        omitted = len(records) > page_limit
        records = records[:page_limit]
        await asyncio.to_thread(self._save, records)
        latest = max((record["settlement_at"] for record in records), default=None)
        return {"refreshed": True, "pages": 1, "page_limit": page_limit,
                "requested_window_start": start, "requested_window_end": end,
                "fetched_count": len(rows), "accepted_count": len(records),
                "rejected_count": sum(reasons.values()), "next_since": latest + 1 if latest is not None else None,
                "has_more": None, "truncated": omitted or len(rows) == 500 or limit > page_limit,
                "issues": [f"rejected_{reason}:{count}" for reason, count in sorted(reasons.items())] + [
                    "One public market-history page; no wallet data, missing intervals and retention remain unknown",
                    "Hourly published rate is not an account payment or a guaranteed profit"]}

    async def _refresh(self, instrument, since: int | None, limit: int, *, before_height=None) -> dict:
        adapter = self.collector.adapters.get(instrument.venue)
        if instrument.venue == "dydx" and isinstance(adapter, DydxAdapter):
            return await self._refresh_dydx(instrument, adapter, since, limit, before_height)
        if instrument.venue == "hyperliquid" and isinstance(adapter, HyperliquidAdapter):
            return await self._refresh_hyperliquid(instrument, adapter, since, limit)
        if instrument.kind != "perpetual" or not isinstance(adapter, CexAdapter):
            raise HTTPException(501, "No reviewed public settled funding history for this market")
        try:
            client_id, _market = adapter._resolve(instrument)
        except ValueError:
            raise HTTPException(409, "Discover this market before refreshing funding history") from None
        if client_id not in SETTLED_ENDPOINTS:
            raise HTTPException(501, "Historical settled-rate endpoint semantics not yet reviewed for this service")
        if client_id in {"gate", "bitget"}:
            allowed = {"USDT", "BTC", "USD1"} if client_id == "gate" else {"USDT", "USDC", instrument.base}
            if (not _market.get("id") or _market.get("swap") is not True
                    or _market.get("symbol") != instrument.native_symbol
                    or _market.get("settle") != instrument.settle or instrument.settle not in allowed):
                raise HTTPException(409, "Historical market native ID, product or settlement scope is unresolved")
        htx_linear = client_id == "htx" and _market.get("linear") is True and _market.get("inverse") is False
        if client_id == "htx":
            inverse = _market.get("inverse") is True and _market.get("linear") is False
            if not htx_linear and not inverse:
                raise HTTPException(501, "HTX historical contract type is unresolved")
            if (not _market.get("id") or _market.get("swap") is not True
                    or _market.get("symbol") != instrument.native_symbol
                    or _market.get("settle") != instrument.settle
                    or (htx_linear and (instrument.settle != "USDT" or instrument.quote != "USDT"
                                        or instrument.linear is not True
                                        or _market.get("id") != f"{instrument.base}-USDT"))
                    or (inverse and instrument.settle != instrument.base)):
                raise HTTPException(409, "HTX native contract and fee currency scope is unresolved")
        # Snapshot the interpretation, not mutable metadata shared with discovery.
        scope_fields = ("id", "symbol", "base", "quote", "settle", "swap", "linear", "inverse", "contractSize", "active")
        market_scope = {key: deepcopy(_market.get(key)) for key in scope_fields}
        _market = deepcopy(_market)

        def check_scope():
            if (self.collector.instruments.get(instrument.id) != instrument
                    or self.collector.adapters.get(instrument.venue) is not adapter):
                raise HTTPException(409, "Market scope changed while retrieving history")
            try:
                current_client, current_market = adapter._resolve(instrument)
            except ValueError:
                raise HTTPException(409, "Market scope changed while retrieving history") from None
            if current_client != client_id or any(current_market.get(key) != value for key, value in market_scope.items()):
                raise HTTPException(409, "Market scope changed while retrieving history")

        client = adapter._client(client_id)
        if client.has.get("fetchFundingRateHistory") is not True:
            raise HTTPException(501, "Native public funding rate history is not supported")
        # Never attach account credentials or mutate an existing client's settings.
        if any(getattr(client, field, None) for field in ("apiKey", "secret", "password", "uid", "token")):
            raise HTTPException(409, "Funding history requires a credential-free public client")
        page_limit = min(limit, SETTLED_ENDPOINTS[client_id][0])
        params: dict[str, Any] = {"paginate": False} if client_id in {
            "binanceusdm", "binancecoinm", "okx", "bybit", "gate", "bitget", "htx"} else {}
        if client_id.startswith("binance"):
            params["subType"] = "linear" if instrument.linear else "inverse"
        if client_id == "htx":
            params["page_index"] = 1
        if client_id == "gate":
            params["settle"] = instrument.settle.lower()
        if client_id == "bitget":
            params.update(uta=False, pageNo=1)
        if client_id == "kucoinfutures":
            params["uta"] = False
        if client_id == "bybit" and since is not None:
            # Do not let CCXT estimate a historical query window from today's
            # catalog funding interval; this is only a request boundary.
            params["until"] = now_ms()
        try:
            # Count both limiter wait and I/O against a bounded manual refresh.
            async with asyncio.timeout(self.timeout_seconds):
                async with adapter._semaphore:
                    check_scope()
                    if htx_linear:
                        # Deliberate documented v1 public endpoint. CCXT's unified
                        # linear method now uses v5 with a different rate schema.
                        query = urlencode({"contract_code": _market["id"], "page_index": 1, "page_size": page_limit})
                        # Pin the documented host; never mutate the shared client
                        # or inherit a library hostname substitution.
                        if getattr(client, "enableRateLimit", False):
                            await client.throttle(1)
                        check_scope()
                        response = await client.fetch(f"https://api.hbdm.com/{HTX_LINEAR_HISTORY_PATH}?{query}", "GET")
                        received = now_ms()
                        data = response.get("data") if isinstance(response, dict) else None
                        rows = data.get("data") if isinstance(data, dict) else None
                        if (not isinstance(response, dict) or response.get("status") != "ok"
                                or not isinstance(data, dict) or data.get("current_page") != 1
                                or not isinstance(rows, list) or len(rows) > page_limit):
                            raise HTTPException(502, "Malformed or oversized HTX public history response")
                        payloads = [{"symbol": instrument.native_symbol, "timestamp": row.get("funding_time"),
                                     "info": row} if isinstance(row, dict) else row for row in rows]
                    else:
                        payloads = await client.fetch_funding_rate_history(instrument.native_symbol, since, page_limit, params)
                        received = now_ms()
        except HTTPException:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if name in {"AuthenticationError", "PermissionDenied"} or getattr(exc, "status_code", None) in {401, 403}:
                raise HTTPException(401, "Public funding history access was rejected; no account credentials were requested") from None
            if isinstance(exc, TimeoutError) or name == "RequestTimeout":
                raise HTTPException(504, "Public funding history request timed out") from None
            if name == "NotSupported":
                raise HTTPException(501, "Public funding history endpoint unavailable") from None
            raise HTTPException(502, {"error": "Public funding history request failed", "type": name}) from None
        check_scope()
        if not isinstance(payloads, list):
            raise HTTPException(502, "Malformed public funding history response")
        records = []
        rejected = 0
        reasons: dict[str, int] = {}
        for payload in payloads[:page_limit]:
            reason = None
            if not isinstance(payload, dict):
                reason = "malformed_record"
                payload = {}
            timestamp = _timestamp(payload.get("timestamp"))
            raw = payload.get("info")
            raw = raw if isinstance(raw, dict) else {}
            # OKX fundingRate is predicted even in history; only realizedRate
            # proves the actual charged rate. Guard against parser regressions.
            value = raw.get("realizedRate") if client_id == "okx" else raw.get("fundingRate", payload.get("fundingRate"))
            if client_id in {"gate", "bitget"}:
                # Independently validate the native settlement clock against
                # CCXT. Never use response time or today's period as history.
                native_time = _number(raw.get("t" if client_id == "gate" else "fundingTime"), positive=True)
                native_time = native_time * 1000 if native_time is not None and client_id == "gate" else native_time
                if native_time is None or native_time != timestamp:
                    reason = reason or "native_settlement_timestamp_mismatch"
                value = raw.get("r" if client_id == "gate" else "fundingRate")
                if client_id == "bitget" and raw.get("symbol") != _market["id"]:
                    reason = reason or "wrong_native_market"
            if client_id == "htx":
                value = raw.get("realized_rate")
                if _timestamp(raw.get("funding_time")) != timestamp or timestamp is None:
                    reason = reason or "native_settlement_timestamp_mismatch"
                if raw.get("contract_code") != _market["id"]:
                    reason = reason or "wrong_native_market"
                if raw.get("fee_asset") != instrument.settle or raw.get("symbol") != instrument.base:
                    reason = reason or "wrong_native_fee_asset_or_base"
            rate = _number(value)
            if timestamp is None:
                reason = reason or "missing_settlement_timestamp"
            elif timestamp > received:
                reason = reason or "future_unsettled_timestamp"
            elif since is not None and timestamp < since:
                reason = reason or "before_requested_since"
            if rate is None:
                reason = reason or "missing_or_nonfinite_settled_rate"
            if payload.get("symbol") not in (None, instrument.native_symbol):
                reason = reason or "wrong_market"
            if reason:
                rejected += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            period = _number(raw.get("collectCycle"), positive=True) if client_id == "mexc" else None
            event_type = raw.get("rateType", "regular") if client_id.startswith("binance") else "regular"
            event_type = event_type.lower() if isinstance(event_type, str) else "unknown"
            event_type = event_type if event_type in {"regular", "special"} else "unknown"
            records.append({"instrument_id": instrument.id, "venue": instrument.venue,
                            "symbol": instrument.native_symbol, "settlement_at": timestamp,
                            "funding_rate": rate, "raw_funding_rate": str(value),
                            "long_cashflow_sign": -1 if rate > 0 else 1 if rate < 0 else 0,
                            "short_cashflow_sign": 1 if rate > 0 else -1 if rate < 0 else 0,
                            "funding_kind": "settled", "funding_interval_hours": period,
                            "period_source": "history.info.collectCycle" if period is not None else None,
                            "source": (f"ccxt:htx:public:{HTX_LINEAR_HISTORY_PATH}:{_market['id']}" if htx_linear
                                       else f"ccxt:{client_id}:fetch_funding_rate_history:{instrument.native_symbol}"),
                            "source_docs": HTX_LINEAR_HISTORY_DOCS if htx_linear else SETTLED_ENDPOINTS[client_id][1], "event_type": event_type,
                            "received_at": received, "first_received_at": received, "last_seen_at": received,
                            "revision": 1, "is_demo": False,
                            **({"native_market_id": _market["id"], "settle": instrument.settle,
                                "rate_unit": "decimal_ratio_per_settlement",
                                "native_settlement_time": raw.get("t" if client_id == "gate" else "fundingTime"),
                                "native_time_unit": "seconds" if client_id == "gate" else "milliseconds"}
                               if client_id in {"gate", "bitget"} else {}),
                            **({"native_market_id": _market["id"], "settle": instrument.settle,
                                "rate_unit": "decimal_ratio_per_settlement", "rate_field": "realized_rate",
                                "native_settlement_time": raw.get("funding_time"),
                                "native_time_unit": "milliseconds",
                                "advertised_funding_rate": str(raw["funding_rate"])
                                    if _number(raw.get("funding_rate")) is not None else None}
                               if client_id == "htx" else {})})
        await asyncio.to_thread(self._save, records)
        latest = max((row["settlement_at"] for row in records), default=None)
        next_since = latest + 1 if latest is not None and latest < received else None
        issues = [f"rejected_{reason}:{count}" for reason, count in sorted(reasons.items())]
        if since is not None and next_since is None:
            issues.append("no_forward_cursor_progress")
        if client_id in {"bitget", "htx"}:
            issues.append(f"{client_id} latest page 1 only; since filters locally and does not fetch older pages")
            next_since = None
        issues.append("One manual page only; exchange retention and missing earlier records remain unknown")
        return {"refreshed": True, "pages": 1, "page_limit": page_limit,
                "fetched_count": len(payloads), "accepted_count": len(records), "rejected_count": rejected,
                "next_since": next_since, "has_more": None,
                "truncated": len(payloads) >= page_limit or limit > page_limit,
                "issues": issues}


@router.get("/history/{instrument_id:path}")
async def funding_history(instrument_id: str, request: Request, refresh: bool = False,
                          since: int | None = Query(default=None, ge=0, le=2**63 - 1),
                          limit: int = Query(default=100, ge=1, le=1000),
                          before_height: int | None = Query(default=None, ge=1, le=2**53 - 1)):
    service = getattr(request.app.state, "funding_history", None)
    if service is None:
        raise HTTPException(503, "Funding history service is not initialized")
    return await service.get(instrument_id, refresh=refresh, since=since, limit=limit, before_height=before_height)
