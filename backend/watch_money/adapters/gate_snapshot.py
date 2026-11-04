"""Bounded on-demand Gate spot snapshots with explicit native clock semantics."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from time import monotonic

from ..models import MarketSnapshot, OrderBook, now_ms


class SnapshotBusy(RuntimeError):
    pass


def stamp(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return (
            int(n)
            if math.isfinite(n) and n == int(n) and 100_000_000_000 <= n < 100_000_000_000_000
            else None
        )
    except (TypeError, ValueError, OverflowError):
        return None


def parse_snapshot(item, raw, received):
    if not isinstance(raw, dict) or item.venue != "gate" or item.kind != "spot":
        raise ValueError("Gate spot snapshot scope invalid")
    current, changed = stamp(raw.get("current")), stamp(raw.get("update"))
    kind = (
        "exchange_snapshot_generation"
        if current is not None
        else "book_last_change"
        if changed is not None
        else "unknown"
    )
    source_at = current if current is not None else changed
    if (source_at is not None and source_at > received) or (
        changed is not None and current is not None and changed > current
    ):
        raise ValueError("Gate snapshot native clocks inconsistent or future")
    sides = {}
    for side in ("bids", "asks"):
        rows = raw.get(side)
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Gate snapshot side missing or over budget")
        levels = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 2 or any(isinstance(v, bool) for v in row):
                raise ValueError("Gate snapshot level malformed")
            p, q = map(float, row)
            if not all(math.isfinite(v) and v > 0 for v in (p, q)):
                raise ValueError("Gate snapshot level invalid")
            levels.append((p, q))
        sides[side] = sorted(levels, reverse=side == "bids")
    source = f"gate:spot.order_book:{item.native_symbol}:{kind}"
    book = OrderBook(
        instrument_id=item.id,
        source=source,
        source_at=source_at,
        received_at=received,
        quantity_unit="base",
        **sides,
    )
    values = {
        "bid": sides["bids"][0][0] if sides["bids"] else None,
        "ask": sides["asks"][0][0] if sides["asks"] else None,
    }
    clears = [field for field, value in values.items() if value is None]
    patch = MarketSnapshot(
        instrument_id=item.id,
        venue="gate",
        source=source,
        source_at=source_at,
        received_at=received,
        field_timestamps={f: source_at for f in values},
        field_received_at={f: received for f in values},
        field_sources={f: source + (":explicit_unavailable" if f in clears else "") for f in values},
        cleared_fields=clears,
        **values,
    )
    observation = {
        "source_time_kind": kind,
        "exchange_generated_at": current,
        "book_last_changed_at": changed,
        "received_at": received,
        "timestamp_available": source_at is not None,
        "note": "Exchange snapshot generation is not the last book-change time; receipt is never substituted.",
    }
    return book, patch, observation


class GateSnapshotService:
    """No scheduler: only user-requested depth, two in flight, per-market cooldown."""

    def __init__(self, collector, *, clock=monotonic):
        self.collector = collector
        self.clock = clock
        self.inflight = 0
        self.last = OrderedDict()
        self.next_start = 0
        self.requests = self.failures = self.applied = 0

    def diagnostics(self):
        return {
            "scope": "on_demand_gate_spot_only",
            "inflight": self.inflight,
            "max_inflight": 2,
            "market_cooldown_seconds": 30,
            "requests": self.requests,
            "failures": self.failures,
            "applied": self.applied,
        }

    async def fetch(self, item):
        adapter = self.collector.adapters.get("gate")
        if adapter is None or not item.active or item.venue != "gate" or item.kind != "spot":
            raise ValueError("Only active discovered Gate spot markets support this snapshot")
        client_id, market = adapter._resolve(item)
        if client_id != "gate" or not market.get("id"):
            raise ValueError("Gate native market identity unavailable")
        native_id = str(market["id"])
        now = self.clock()
        if self.inflight >= 2 or now < self.next_start or now - self.last.get(item.id, -math.inf) < 30:
            raise SnapshotBusy("Public snapshot budget busy; retry after market cooldown")
        self.last[item.id] = now
        self.last.move_to_end(item.id)
        if len(self.last) > 5000:
            self.last.popitem(last=False)
        self.next_start = now + 0.25
        self.requests += 1
        self.inflight += 1
        try:
            raw = await asyncio.wait_for(
                adapter._client(client_id).publicSpotGetOrderBook(
                    {"currency_pair": native_id, "limit": 20, "with_id": True}
                ),
                15,
            )
            received = now_ms()
            book, patch, observation = parse_snapshot(item, raw, received)
            current = self.collector.instruments.get(item.id)
            # Discovery/identity edits during an HTTP request invalidate its binding.
            unchanged = (
                current is not None and current == item and adapter._resolve(current)[1]["id"] == native_id
            )
            merged_fields = []
            if patch.source_at is not None and unchanged:
                merged = self.collector.merge_patch(patch)
                self.collector.snapshots[item.id] = merged
                merged_fields = [
                    field
                    for field in ("bid", "ask")
                    if merged.field_sources.get(field) == patch.field_sources[field]
                    and merged.field_timestamps.get(field) == patch.source_at
                    and merged.field_received_at.get(field) == received
                    and getattr(merged, field) == getattr(patch, field)
                ]
                self.applied += int(bool(merged_fields))
            observation["merged"] = bool(merged_fields)
            observation["merged_fields"] = merged_fields
            return {**book.model_dump(), "observation": observation}
        except Exception:
            self.failures += 1
            raise
        finally:
            self.inflight -= 1
