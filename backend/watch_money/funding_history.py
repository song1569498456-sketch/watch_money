"""Manually refreshed public settled funding records, separate from snapshot samples."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from .adapters.cex import CexAdapter, _number, _timestamp
from .models import now_ms

router = APIRouter(prefix="/api/funding", tags=["funding-history"])

# Explicitly reviewed public historical settlement contracts. A generic CCXT
# capability alone does not prove that a trailing/predicted rate was settled.
SETTLED_ENDPOINTS = {
    "bitget": (100, "https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market#get-historical-funding-rates"),
    "gate": (100, "https://www.gate.com/docs/developers/apiv4/en/futures/#futures-market-historical-funding-rate"),
    "binanceusdm": (1000, "https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History"),
    "binancecoinm": (1000, "https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Rate-History-of-Perpetual-Futures"),
    "okx": (100, "https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history"),
    "bybit": (200, "https://bybit-exchange.github.io/docs/v5/market/history-fund-rate"),
    "mexc": (100, "https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate-history"),
    "kucoinfutures": (100, "https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-public-funding-history"),
}


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

    def _read(self, instrument_id: str, since: int | None, limit: int) -> tuple[list[dict], int]:
        condition = "instrument_id=?"
        values: list[Any] = [instrument_id]
        if since is not None:
            condition += " AND settlement_at>=?"
            values.append(since)
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
                    changed = any(prior.get(field) != record.get(field) for field in
                                  ("funding_rate", "raw_funding_rate", "funding_interval_hours"))
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
                  since: int | None = None, limit: int = 100) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise HTTPException(422, "limit must be between 1 and 1000")
        if since is not None and (isinstance(since, bool) or not isinstance(since, int) or not 0 <= since <= 2**63 - 1):
            raise HTTPException(422, "since must be a nonnegative epoch millisecond integer")
        instrument = self.collector.instruments.get(instrument_id)
        if instrument is None:
            raise HTTPException(404, "Unknown instrument")
        issues = []
        summary = {"refreshed": False, "requested_since": since, "limit": limit,
                   "pages": 0, "fetched_count": 0, "accepted_count": 0,
                   "rejected_count": 0, "next_since": None, "has_more": None,
                   "truncated": False, "issues": issues}
        if refresh:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    async with self._refresh_lock:
                        summary.update(await self._refresh(instrument, since, limit))
            except TimeoutError:
                raise HTTPException(504, "Public funding history request timed out") from None
        records, count = await asyncio.to_thread(self._read, instrument_id, since, limit)
        summary.update(count=len(records), cached_matching_count=count,
                       earliest_settlement_at=records[0]["settlement_at"] if records else None,
                       latest_settlement_at=records[-1]["settlement_at"] if records else None)
        summary["truncated"] = summary["truncated"] or count > limit
        return {"instrument_id": instrument_id, "records": records, "summary": summary,
                "coverage": {"kind": "exchange_settled_funding_history", "all_history": False,
                             "background_backfill": False, "pagination": "one_manual_page",
                             "cursor_semantics": "forward_since_hint_only_not_gap_proof",
                             "direction": "positive_longs_pay_shorts_negative_shorts_pay_longs"}}

    async def _refresh(self, instrument, since: int | None, limit: int) -> dict:
        adapter = self.collector.adapters.get(instrument.venue)
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
        client = adapter._client(client_id)
        if client.has.get("fetchFundingRateHistory") is not True:
            raise HTTPException(501, "Native public funding rate history is not supported")
        # Never attach account credentials or mutate an existing client's settings.
        if any(getattr(client, field, None) for field in ("apiKey", "secret", "password", "uid", "token")):
            raise HTTPException(409, "Funding history requires a credential-free public client")
        page_limit = min(limit, SETTLED_ENDPOINTS[client_id][0])
        params: dict[str, Any] = {"paginate": False} if client_id in {
            "binanceusdm", "binancecoinm", "okx", "bybit", "gate", "bitget"} else {}
        if client_id.startswith("binance"):
            params["subType"] = "linear" if instrument.linear else "inverse"
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
                    payloads = await client.fetch_funding_rate_history(instrument.native_symbol, since, page_limit, params)
        except Exception as exc:
            name = type(exc).__name__
            if name in {"AuthenticationError", "PermissionDenied"} or getattr(exc, "status_code", None) in {401, 403}:
                raise HTTPException(401, "Public funding history access was rejected; no account credentials were requested") from None
            if isinstance(exc, TimeoutError) or name == "RequestTimeout":
                raise HTTPException(504, "Public funding history request timed out") from None
            if name == "NotSupported":
                raise HTTPException(501, "Public funding history endpoint unavailable") from None
            raise HTTPException(502, {"error": "Public funding history request failed", "type": name}) from None
        if not isinstance(payloads, list):
            raise HTTPException(502, "Malformed public funding history response")
        received = now_ms()
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
                            "source": f"ccxt:{client_id}:fetch_funding_rate_history:{instrument.native_symbol}",
                            "source_docs": SETTLED_ENDPOINTS[client_id][1], "event_type": event_type,
                            "received_at": received, "first_received_at": received, "last_seen_at": received,
                            "revision": 1, "is_demo": False,
                            **({"native_market_id": _market["id"], "settle": instrument.settle,
                                "rate_unit": "decimal_ratio_per_settlement",
                                "native_settlement_time": raw.get("t" if client_id == "gate" else "fundingTime"),
                                "native_time_unit": "seconds" if client_id == "gate" else "milliseconds"}
                               if client_id in {"gate", "bitget"} else {})})
        await asyncio.to_thread(self._save, records)
        latest = max((row["settlement_at"] for row in records), default=None)
        next_since = latest + 1 if latest is not None and latest < received else None
        issues = [f"rejected_{reason}:{count}" for reason, count in sorted(reasons.items())]
        if since is not None and next_since is None:
            issues.append("no_forward_cursor_progress")
        if client_id == "bitget":
            issues.append("Bitget latest page 1 only; since filters locally and does not fetch older pages")
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
                          limit: int = Query(default=100, ge=1, le=1000)):
    service = getattr(request.app.state, "funding_history", None)
    if service is None:
        raise HTTPException(503, "Funding history service is not initialized")
    return await service.get(instrument_id, refresh=refresh, since=since, limit=limit)
