"""Explicit-input executable opportunity calculator and repeatable local samples.

All values returned in valuation_currency retain their original quote/FX provenance.
No live collector globals, last-trade prices, or assumed stablecoin pegs are used.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from .identity import identity_candidates
from .models import Instrument, MarketSnapshot, Model, OrderBook, now_ms
from .quote_identity import (
    canonical_asset_id,
    quote_identity,
    requires_quote_identity,
    traceable_quote_source,
)

Strategy = Literal[
    "spot_spread", "spot_perp_basis", "perp_spread", "funding_spread",
    "funding_basis", "cex_dex_spot", "dex_pool_spread",
]


class FXRate(Model):
    base: str
    quote: str
    rate: float = Field(gt=0)
    source: str = Field(min_length=1)
    at: int
    # Required for a conversion involving on-chain quote denominations, even
    # when both tokens share the same display symbol.
    base_asset_id: str | None = None
    quote_asset_id: str | None = None


class LegCost(Model):
    instrument_id: str
    open_fee_rate: float | None = Field(default=None, ge=-0.1, le=1)
    close_fee_rate: float | None = Field(default=None, ge=-0.1, le=1)
    # Explicit per-leg holding charge, fraction of entry notional per hour.
    # Required for short spot borrowing; optional on other held positions.
    holding_rate_per_hour: float | None = Field(default=None, ge=0)
    source: str | None = None
    at: int | None = None
    conditions: str | None = None


class ExtraCost(Model):
    name: str = Field(min_length=1)
    amount: float | None = Field(default=None, ge=0)
    currency: str
    source: str | None = None
    at: int | None = None


class RouteQuote(Model):
    instrument_id: str
    side: Literal["buy", "sell"]
    input_asset: str
    output_asset: str
    input_amount: float = Field(gt=0)
    output_amount: float = Field(gt=0)
    route: list[str] = Field(min_length=1)
    block_number: int = Field(ge=0)
    source: str = Field(min_length=1)
    source_at: int
    received_at: int
    quote_asset_id: str | None = None
    # The output already includes pool / route fees and price impact.
    fees_included: bool = True
    gas_amount: float | None = Field(default=None, ge=0)
    gas_currency: str | None = None
    is_demo: bool = False


class FundingEvent(Model):
    instrument_id: str
    settlement_at: int
    rate: float
    kind: Literal["settled", "current", "predicted"]
    known_at: int
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def settled_is_observed(self):
        if self.kind == "settled" and self.known_at < self.settlement_at:
            raise ValueError("A settled rate cannot be known before its settlement")
        return self


class CalculateRequest(Model):
    instruments: list[Instrument]
    snapshots: list[MarketSnapshot] = Field(default_factory=list)
    orderbooks: list[OrderBook] = Field(default_factory=list)
    route_quotes: list[RouteQuote] = Field(default_factory=list)
    buy_instrument_id: str
    sell_instrument_id: str
    size_base: float = Field(gt=0)
    sizes_base: list[float] = Field(default_factory=list, max_length=100)
    strategy: Strategy = "spot_spread"
    valuation_currency: str = "USD"
    fx_rates: list[FXRate] = Field(default_factory=list)
    costs: list[LegCost] = Field(default_factory=list)
    extra_costs: list[ExtraCost] = Field(default_factory=list)
    hold_hours: float = Field(default=24, gt=0, le=8760)
    assume_exit_at_entry: bool = False
    # Native quote per base, explicit expected execution prices at close.
    exit_prices: dict[str, float] = Field(default_factory=dict)
    # Alternative: short exit minus long exit in valuation currency per base.
    exit_basis_quote: float | None = None
    funding_events: list[FundingEvent] = Field(default_factory=list)
    # A supplied irregular schedule must declare its complete horizon.
    funding_schedule_complete_until: dict[str, int] = Field(default_factory=dict)
    funding_schedule_known_at: dict[str, int] = Field(default_factory=dict)
    funding_notional_basis: Literal["mark", "entry_vwap"] = "mark"
    inventory_path: Literal["prepositioned", "transfer"] = "prepositioned"
    transfer_cost: ExtraCost | None = None
    stale_seconds: float = Field(default=60, gt=0)
    fx_stale_seconds: float = Field(default=3600, gt=0)
    as_of: int = Field(default_factory=now_ms)
    threshold: float = 0

    @model_validator(mode="after")
    def finite_sizes(self):
        if any(not math.isfinite(x) or x <= 0 for x in self.sizes_base):
            raise ValueError("sizes_base must contain positive finite quantities")
        if any(not math.isfinite(x) or x <= 0 for x in self.exit_prices.values()):
            raise ValueError("exit_prices must be positive and finite")
        if self.buy_instrument_id == self.sell_instrument_id:
            raise ValueError("Buy and sell instruments must differ")
        ids = [item.id for item in self.instruments]
        if len(set(ids)) != len(ids):
            raise ValueError("Instrument ids must be unique")
        return self


class SaveRequest(Model):
    name: str = Field(min_length=1, max_length=200)
    calculation: CalculateRequest


class ReplayRequest(Model):
    sample_id: str
    as_of: int
    receive_delay_ms: int = Field(default=0, ge=0)
    operation_delay_ms: int = Field(default=0, ge=0)


router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])
SPOT_STRATEGIES = {"spot_spread", "cex_dex_spot", "dex_pool_spread"}


def _fresh(source_at: int | None, received_at: int, req: CalculateRequest) -> bool:
    stamps = [received_at] + ([source_at] if source_at is not None else [])
    return all(0 <= req.as_of - at <= req.stale_seconds * 1000 for at in stamps)


def _field_fresh(snapshot: MarketSnapshot, field: str, req: CalculateRequest) -> bool:
    # A merged envelope can describe an unrelated, late-arriving source event.
    source = snapshot.field_timestamps.get(field, snapshot.source_at)
    received = snapshot.field_received_at.get(field, snapshot.received_at)
    if field == "funding_interval_hours" and source is None and snapshot.field_sources.get(field, "").endswith(
            (":catalog_metadata_observed_at", ":public_metadata_observed_at")):
        # Configuration has an observation time, not a fabricated exchange event time.
        return field in snapshot.field_received_at and 0 <= req.as_of - received <= 6 * 3600_000
    if field in snapshot.field_timestamps and source is None:
        return False
    return _fresh(source, received, req)


def _fx(currency: str, req: CalculateRequest, *, asset_id: str | None = None,
        valuation_asset_id: str | None = None) -> tuple[float | None, dict | None]:
    if asset_id is not None or valuation_asset_id is not None:
        if asset_id is not None and asset_id == valuation_asset_id:
            return 1.0, {"base": currency, "quote": req.valuation_currency, "rate": 1.0,
                         "source": "same confirmed quote asset identity", "asset_id": asset_id,
                         "at": req.as_of}
        candidates = []
        for item in req.fx_rates:
            base_id = canonical_asset_id(item.base_asset_id) or item.base_asset_id
            quote_id = canonical_asset_id(item.quote_asset_id) or item.quote_asset_id
            if (not 0 <= req.as_of - item.at <= req.fx_stale_seconds * 1000
                    or not traceable_quote_source(item.source)):
                continue
            if (base_id == asset_id and quote_id == valuation_asset_id and item.base == currency
                    and item.quote == req.valuation_currency):
                candidates.append((item, item.rate))
            elif (quote_id == asset_id and base_id == valuation_asset_id and item.quote == currency
                  and item.base == req.valuation_currency):
                candidates.append((item, 1 / item.rate))
        if not candidates:
            return None, None
        item, rate = max(candidates, key=lambda pair: pair[0].at)
        return rate, {**item.model_dump(), "applied_rate": rate, "from": currency,
                      "to": req.valuation_currency, "from_asset_id": asset_id,
                      "to_asset_id": valuation_asset_id}
    if currency == req.valuation_currency:
        return 1.0, {"base": currency, "quote": currency, "rate": 1.0, "source": "identity", "at": req.as_of}
    candidates = [f for f in req.fx_rates if
                  ((f.base == currency and f.quote == req.valuation_currency) or
                   (f.quote == currency and f.base == req.valuation_currency)) and
                  0 <= req.as_of - f.at <= req.fx_stale_seconds * 1000]
    if not candidates:
        return None, None
    item = max(candidates, key=lambda f: f.at)
    rate = item.rate if item.base == currency else 1 / item.rate
    return rate, {**item.model_dump(), "applied_rate": rate, "from": currency, "to": req.valuation_currency}


def _fill_book(instrument: Instrument, book: OrderBook, side: str, size: float) -> dict:
    """Depth is base or contracts as explicitly declared, never guessed from ticker."""
    if not instrument.linear and (instrument.settle != instrument.base or instrument.quote == instrument.base):
        raise ValueError("Inverse contract requires explicit base settlement and distinct quote currency")
    if book.quantity_unit == "contracts" and instrument.kind != "perpetual":
        raise ValueError("Contract quantities require a perpetual instrument")
    step = instrument.amount_precision
    if step is not None and (not math.isfinite(step) or step <= 0):
        raise ValueError("Amount precision must be a positive quantity step")
    for levels in (book.bids, book.asks):
        if any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0 for p, q in levels):
            raise ValueError("Order book contains nonpositive or nonfinite levels")
    if book.bids and book.asks and max(p for p, _ in book.bids) >= min(p for p, _ in book.asks):
        raise ValueError("Crossed or locked order book")
    levels = sorted(book.asks if side == "buy" else book.bids, reverse=side == "sell")
    capacity = 0.0
    filled = 0.0
    quote_amount = 0.0
    contracts = 0.0
    used = []
    for price, amount in levels:
        if book.quantity_unit == "base":
            base_amount = amount
        elif instrument.linear:
            base_amount = amount * instrument.contract_size
        else:
            base_amount = amount * instrument.contract_size / price
        capacity += base_amount
        take = min(max(size - filled, 0.0), base_amount)
        if take:
            quote_amount += take * price
            filled += take
            contract_take = None
            if instrument.kind == "perpetual":
                contract_take = take / instrument.contract_size if instrument.linear else take * price / instrument.contract_size
                contracts += contract_take
            used.append({"price": price, "base_quantity": take, "contracts": contract_take})
    complete = filled > 0 and math.isclose(filled, size, rel_tol=1e-10, abs_tol=0)
    quantity = (size / instrument.contract_size if instrument.linear else contracts
                ) if instrument.kind == "perpetual" else size
    unit = "contracts" if instrument.kind == "perpetual" else "base"
    if ((complete or instrument.linear) and instrument.min_amount is not None and quantity < instrument.min_amount
            and not math.isclose(quantity, instrument.min_amount, rel_tol=1e-12, abs_tol=0)):
        raise ValueError(f"Requested quantity is below minimum order amount {instrument.min_amount} {unit}")
    if complete and step is not None:
        # Exchange amount ticks apply to contracts for perpetuals, even when
        # the supplied depth has already been normalized to base quantities.
        units = quantity / step
        if (not math.isfinite(units) or round(units) < 1
                or not math.isclose(units, round(units), rel_tol=0, abs_tol=1e-8)):
            raise ValueError(f"Requested quantity does not match amount precision step {step} {unit}")
    return {"instrument_id": instrument.id, "venue": instrument.venue, "side": side,
            "quote_currency": instrument.quote, "size_base": size, "filled_base": filled,
            "capacity_base": capacity, "complete": complete,
            "average_price": quote_amount / filled if filled else None,
            "quote_amount": quote_amount, "contracts": contracts if instrument.kind == "perpetual" else None,
            "quantity_unit": book.quantity_unit, "levels": used, "source": book.source,
            "source_at": book.source_at, "received_at": book.received_at,
            "contract_size": instrument.contract_size, "linear": instrument.linear,
            "is_demo": getattr(book, "is_demo", False)}


def _fill_route(instrument: Instrument, route: RouteQuote, size: float) -> dict:
    if requires_quote_identity(instrument):
        expected_quote = canonical_asset_id(instrument.native_quote_asset_id or instrument.quote_asset_id)
        if expected_quote is None or canonical_asset_id(route.quote_asset_id) != expected_quote:
            raise ValueError("Route quote token identity must match the instrument's confirmed quote token contract")
    expected_in = instrument.quote if route.side == "buy" else instrument.base
    expected_out = instrument.base if route.side == "buy" else instrument.quote
    if route.input_asset != expected_in or route.output_asset != expected_out:
        raise ValueError("Route input/output assets do not match market direction")
    base_amount = route.output_amount if route.side == "buy" else route.input_amount
    if not math.isclose(base_amount, size, rel_tol=1e-9, abs_tol=0):
        raise ValueError("DEX quote quantity differs from requested size; request a fresh exact-size quote")
    if (instrument.min_amount is not None and size < instrument.min_amount
            and not math.isclose(size, instrument.min_amount, rel_tol=1e-12, abs_tol=0)):
        raise ValueError(f"Requested quantity is below minimum order amount {instrument.min_amount} base")
    if not route.fees_included:
        raise ValueError("DEX quote must include pool and route fees in its input/output amounts")
    quote_amount = route.input_amount if route.side == "buy" else route.output_amount
    return {"instrument_id": instrument.id, "venue": instrument.venue, "side": route.side,
            "quote_currency": instrument.quote, "quote_asset_id": canonical_asset_id(route.quote_asset_id),
            "size_base": size, "filled_base": size,
            "capacity_base": size, "complete": True, "average_price": quote_amount / size,
            "quote_amount": quote_amount, "quantity_unit": "base", "source": route.source,
            "source_at": route.source_at, "received_at": route.received_at,
            "route": route.route, "block_number": route.block_number, "price_impact_included": True,
            "fees_included": True, "gas_amount": route.gas_amount, "gas_currency": route.gas_currency,
            "linear": instrument.linear, "contract_size": instrument.contract_size,
            "is_demo": route.is_demo}


def _classification(req: CalculateRequest, buy: Instrument, sell: Instrument) -> list[str]:
    kinds = {buy.kind, sell.kind}
    if req.strategy in SPOT_STRATEGIES and not kinds <= {"spot", "pool"}:
        return ["Selected strategy requires two spot/pool markets"]
    if req.strategy == "spot_perp_basis" and not ("perpetual" in kinds and kinds & {"spot", "pool"}):
        return ["Spot/perpetual basis requires one spot and one perpetual market"]
    if req.strategy in {"perp_spread", "funding_spread"} and kinds != {"perpetual"}:
        return ["Selected strategy requires two perpetual markets"]
    if req.strategy == "funding_basis" and "perpetual" not in kinds:
        return ["Funding/basis strategy requires a perpetual leg"]
    if req.strategy == "cex_dex_spot" and (buy.chain is None) == (sell.chain is None):
        return ["CEX/DEX comparison requires one on-chain and one exchange market"]
    if req.strategy == "dex_pool_spread" and (not buy.chain or buy.chain != sell.chain):
        return ["Cross-pool comparison requires markets on the same explicit chain"]
    if buy.asset_id != sell.asset_id:
        return ["Asset identities differ; ticker equality is not an asset mapping"]
    if buy.venue != sell.venue and not (buy.mapping_verified and sell.mapping_verified):
        return ["Cross-venue asset mapping requires explicit verification"]
    # A reviewed mapping cannot erase native token addresses, chain conflicts
    # or scaled/wrapped base units. Share the catalog review's identity gate;
    # its quote/contract conversion requirements remain separate below.
    shared_identity = next((row for row in identity_candidates([buy, sell], as_of=req.as_of)
                            if row["kind"] == "identity" and len(row["instrument_ids"]) == 2), None)
    if shared_identity is None:
        return ["Asset identity evidence does not establish a comparable base unit"]
    if shared_identity["blockers"]:
        return [f"Asset identity conflict: {reason}" for reason in shared_identity["blockers"]]
    if not buy.active or not sell.active:
        return ["A selected instrument is inactive"]
    return []


def _snapshot(req: CalculateRequest, instrument: Instrument) -> MarketSnapshot | None:
    candidates = [s for s in req.snapshots if s.instrument_id == instrument.id and s.received_at <= req.as_of]
    return max(candidates, key=lambda s: s.received_at) if candidates else None


def _funding_events(req: CalculateRequest, inst: Instrument, snap: MarketSnapshot | None,
                    hours: float) -> tuple[list[dict], str | None]:
    end = req.as_of + round(hours * 3_600_000)
    explicit = [e for e in req.funding_events if e.instrument_id == inst.id and e.known_at <= req.as_of
                and req.as_of < e.settlement_at <= end]
    # Both complete schedules and partial overrides use the latest estimate
    # actually known at decision time, subject to the same freshness gate.
    if any(e.kind == "settled" for e in explicit):
        return [], "Future settled funding would introduce lookahead"
    dedup = {e.settlement_at: e for e in sorted(explicit, key=lambda e: e.known_at)}
    if any(req.as_of - e.known_at > req.stale_seconds * 1000 for e in dedup.values()):
        return [], "Explicit funding rate estimate is stale"
    schedule_known = req.funding_schedule_known_at.get(inst.id, req.as_of)
    if req.funding_schedule_complete_until.get(inst.id, -1) >= end and schedule_known <= req.as_of:
        if req.as_of - schedule_known > req.stale_seconds * 1000:
            return [], "Explicit funding schedule is stale"
        return [e.model_dump() for _, e in sorted(dedup.items())], None
    if snap is None or snap.funding_rate is None or snap.funding_kind not in {"current", "predicted"}:
        return [], "Current/predicted funding rate or complete explicit schedule required"
    for field in ("funding_rate", "funding_interval_hours", "next_funding_at"):
        if not _field_fresh(snap, field, req):
            return [], f"Funding field {field} has stale, unknown, or future provenance timestamp"
    if snap.funding_interval_hours is None or snap.next_funding_at is None:
        return [], "Funding interval and next actual settlement timestamp required"
    if snap.next_funding_at <= req.as_of:
        return [], "Next funding timestamp is no longer in the future"
    step = snap.funding_interval_hours * 3_600_000
    count = max(0, math.floor((end - snap.next_funding_at) / step) + 1)
    if count > 100_000:
        return [], "Funding schedule exceeds supported event count"
    overrides = dict(dedup)
    events = []
    for index in range(count):
        at = round(snap.next_funding_at + index * step)
        if at in overrides:
            events.append(overrides.pop(at).model_dump())
        else:
            events.append({"instrument_id": inst.id, "settlement_at": at, "rate": snap.funding_rate,
                           "kind": snap.funding_kind, "known_at": snap.received_at, "source": snap.source,
                           "projection": "constant rate at supplied settlement interval"})
    if overrides:
        return [], "Irregular funding events require a declared complete schedule horizon"
    return events, None


def calculate(req: CalculateRequest) -> dict:
    """Pure deterministic calculation, suitable for saved samples and acceptance tests."""
    sizes = req.sizes_base or [req.size_base]
    return {"opportunities": [_calculate_size(req, size) for size in sizes]}


def _calculate_size(req: CalculateRequest, size: float) -> dict:
    instruments = {i.id: i for i in req.instruments}
    buy, sell = instruments.get(req.buy_instrument_id), instruments.get(req.sell_instrument_id)
    identity = f"{req.strategy}|{req.buy_instrument_id}|{req.sell_instrument_id}|{size}|{req.valuation_currency}"
    out = {"id": hashlib.sha256(identity.encode()).hexdigest()[:24], "strategy": req.strategy,
           "asset_id": buy.asset_id if buy else None, "buy_instrument_id": req.buy_instrument_id,
           "sell_instrument_id": req.sell_instrument_id, "size_base": size,
           "valuation_currency": req.valuation_currency, "as_of": req.as_of, "hold_hours": req.hold_hours,
           "status": "data_incomplete", "gross_profit": None, "known_costs": 0.0, "net_profit": None,
           "funding_profit": None, "basis_profit": None, "entry_spread": None, "capacity_base": None,
           "missing_prerequisites": [], "invalid_reasons": [], "legs": [], "costs": [], "scenarios": [],
           "fx_rates": [], "assumptions": [], "is_demo": False, "stale": False,
           "data_age_seconds": None, "breakeven_hours": None, "venues": [], "funding_events": [],
           "holding_cost": None, "borrowing_cost": None, "holding_costs": [], "quote_assets": []}
    missing, invalid = out["missing_prerequisites"], out["invalid_reasons"]
    if buy is None or sell is None:
        missing.append("Explicit metadata for both instruments is required")
        return out
    out["venues"] = list(dict.fromkeys([buy.venue, sell.venue]))
    invalid.extend(_classification(req, buy, sell))
    quote_ids = {}
    has_chain_quote = any(requires_quote_identity(inst) for inst in (buy, sell))
    if has_chain_quote:
        for inst in (buy, sell):
            asset_id, problems = quote_identity(inst, req.as_of, req.stale_seconds * 1000)
            quote_ids[inst.id] = asset_id
            invalid.extend(f"{inst.id}: {problem}" for problem in problems)
            out["quote_assets"].append({"instrument_id": inst.id, "symbol": inst.quote,
                                        "asset_id": asset_id, "source": inst.quote_identity_source,
                                        "at": inst.quote_identity_at, "expires_at": inst.quote_identity_expires_at})
    if invalid:
        out["status"] = "invalidated"
        return out
    held = req.strategy not in SPOT_STRATEGIES
    if held:
        for inst in (buy, sell):
            if inst.kind == "perpetual" and inst.linear and inst.settle != inst.quote:
                missing.append(
                    f"{inst.id}: linear settlement currency must be explicit and match quote; quanto/conversion payoff unsupported"
                )
    out["assumptions"].append(f"Inventory path: {req.inventory_path}")
    if req.inventory_path == "prepositioned":
        out["assumptions"].append("Assets and collateral are available on both venues; account feasibility is not verified")
    valuation_asset_id = None
    if has_chain_quote:
        if req.valuation_currency == buy.quote:
            valuation_asset_id = quote_ids[buy.id]
        elif req.valuation_currency == sell.quote:
            valuation_asset_id = quote_ids[sell.id]
        elif canonical_asset_id(req.valuation_currency):
            valuation_asset_id = canonical_asset_id(req.valuation_currency)
        elif req.valuation_currency in {"USD", "EUR", "GBP", "JPY", "KRW", "CHF", "CAD", "AUD", "CNY", "HKD", "SGD"}:
            valuation_asset_id = f"fiat:{req.valuation_currency}"
        else:
            invalid.append("Valuation quote token identity is unavailable; use a confirmed leg denomination or qualified chain:address")
            out["status"] = "invalidated"
            return out
    out["valuation_asset_id"] = valuation_asset_id
    leg_fx = {}
    leg_fx_evidence = {}
    snapshots = {}
    costs = {c.instrument_id: c for c in req.costs}
    for inst, side in ((buy, "buy"), (sell, "sell")):
        snap = _snapshot(req, inst)
        snapshots[inst.id] = snap
        if snap is not None:
            out["is_demo"] |= snap.is_demo
            if snap.venue != inst.venue:
                invalid.append(f"{inst.id}: snapshot venue differs from instrument")
            if not snap.valid:
                invalid.append(f"{inst.id}: snapshot marked invalid: {', '.join(snap.issues)}")
            if not snap.field_timestamps and not _fresh(snap.source_at, snap.received_at, req):
                invalid.append(f"{inst.id}: stale or future market snapshot")
                out["stale"] = True
        factor, fx = _fx(inst.quote, req, asset_id=quote_ids.get(inst.id), valuation_asset_id=valuation_asset_id)
        leg_fx[inst.id] = factor
        leg_fx_evidence[inst.id] = fx
        if factor is None:
            suffix = " with both specific quote asset identities bound" if has_chain_quote else ""
            missing.append(f"{inst.quote}/{req.valuation_currency}: fresh sourced FX rate required{suffix}")
        elif fx not in out["fx_rates"]:
            out["fx_rates"].append(fx)
        try:
            if inst.mechanism in {"amm", "rfq"} or inst.kind == "pool":
                routes = [r for r in req.route_quotes if r.instrument_id == inst.id and r.side == side
                          and r.received_at <= req.as_of]
                if not routes:
                    missing.append(f"{inst.id}: exact-size executable route quote required")
                    continue
                route = max(routes, key=lambda r: r.received_at)
                leg = _fill_route(inst, route, size)
                out["is_demo"] |= route.is_demo
                if not _fresh(route.source_at, route.received_at, req):
                    invalid.append(f"{inst.id}: stale or future route quote")
                    out["stale"] = True
                if held:
                    missing.append(f"{inst.id}: held AMM/RFQ perpetual/spot exit and protocol holding model unsupported")
            else:
                books = [b for b in req.orderbooks if b.instrument_id == inst.id and b.received_at <= req.as_of]
                if not books:
                    missing.append(f"{inst.id}: executable order book required; ticker bid/ask is not depth")
                    continue
                book = max(books, key=lambda b: b.received_at)
                leg = _fill_book(inst, book, side, size)
                out["is_demo"] |= getattr(book, "is_demo", False)
                if not _fresh(book.source_at, book.received_at, req):
                    invalid.append(f"{inst.id}: stale or future order book")
                    out["stale"] = True
                if not leg["complete"]:
                    missing.append(f"{inst.id}: insufficient depth for full requested size")
            ages = [req.as_of - leg["received_at"]]
            if leg.get("source_at") is not None:
                ages.append(req.as_of - leg["source_at"])
            age = max(ages) / 1000
            out["data_age_seconds"] = max(out["data_age_seconds"] or 0, age)
            if inst.min_notional is not None and leg["quote_amount"] < inst.min_notional:
                invalid.append(f"{inst.id}: below minimum market notional")
            leg["fx"] = fx
            leg["valuation_amount"] = leg["quote_amount"] * factor if factor is not None else None
            out["legs"].append(leg)
        except ValueError as exc:
            invalid.append(f"{inst.id}: {exc}")
    if out["legs"]:
        out["capacity_base"] = min(leg["capacity_base"] for leg in out["legs"])
    if len(out["legs"]) == 2 and all(leg.get("route") for leg in out["legs"]):
        if set(out["legs"][0]["route"]) & set(out["legs"][1]["route"]):
            invalid.append("Routes share underlying liquidity; an atomic combined route simulation is required")
        if buy.chain == sell.chain and out["legs"][0]["block_number"] != out["legs"][1]["block_number"]:
            missing.append("Same-chain pool comparison requires coherent quotes from the same block")
    if invalid or len(out["legs"]) < 2 or any(not leg["complete"] for leg in out["legs"]) or any(v is None for v in leg_fx.values()):
        out["status"] = "invalidated" if invalid else "data_incomplete"
        return out
    long_leg, short_leg = out["legs"]
    entry_buy, entry_sell = long_leg["valuation_amount"], short_leg["valuation_amount"]
    out["entry_spread"] = entry_sell - entry_buy

    def add_cost(name, amount, currency, source=None, at=None, category="execution", instrument_id=None):
        inst = instruments.get(instrument_id)
        if inst is not None and currency == inst.quote:
            factor, provenance = leg_fx[inst.id], leg_fx_evidence[inst.id]
        else:
            factor, provenance = _fx(currency, req)
        value = None if amount is None or factor is None else amount * factor
        if amount is None:
            missing.append(f"{name}: cost pending configuration")
        elif factor is None:
            missing.append(f"{name}: sourced {currency}/{req.valuation_currency} FX required")
        elif provenance not in out["fx_rates"]:
            out["fx_rates"].append(provenance)
        out["costs"].append({"name": name, "amount": amount, "currency": currency,
                             "valuation_amount": value, "source": source, "at": at,
                             "fx": provenance, "category": category})

    close_native = {}
    basis_profit = None
    if held:
        if all(i.id in req.exit_prices for i in (buy, sell)):
            close_native = {i.id: req.exit_prices[i.id] for i in (buy, sell)}
            out["assumptions"].append("Exit prices are user assumptions, not future executable quotes")
        elif req.assume_exit_at_entry:
            close_native = {i.id: leg["average_price"] for i, leg in ((buy, long_leg), (sell, short_leg))}
            out["assumptions"].append("Exit execution prices equal each leg's entry VWAP; basis does not change")
        elif req.exit_basis_quote is not None:
            close_native[buy.id] = long_leg["average_price"]
            close_native[sell.id] = (long_leg["average_price"] * leg_fx[buy.id] + req.exit_basis_quote) / leg_fx[sell.id]
            if close_native[sell.id] <= 0:
                invalid.append("Exit basis assumption implies a nonpositive short exit price")
            out["assumptions"].append("Exit basis scenario fixes long exit at long entry VWAP and derives short exit price")
        else:
            missing.append("Explicit exit prices, exit basis, or assume_exit_at_entry required for full net result")
        if close_native and not invalid:
            for inst in (buy, sell):
                if not inst.linear:
                    missing.append(f"{inst.id}: held inverse payoff/funding conversion is unsupported without settlement-price path")
            if all(inst.linear for inst in (buy, sell)):
                basis_profit = size * (close_native[buy.id] * leg_fx[buy.id] - long_leg["average_price"] * leg_fx[buy.id]
                                       + short_leg["average_price"] * leg_fx[sell.id] - close_native[sell.id] * leg_fx[sell.id])
    else:
        basis_profit = entry_sell - entry_buy
    out["basis_profit"] = basis_profit
    for inst, leg in ((buy, long_leg), (sell, short_leg)):
        config = costs.get(inst.id)
        if not held and config is not None and (config.holding_rate_per_hour or 0) > 0:
            missing.append(f"{inst.id}: nonzero holding cost requires a held strategy with a holding period")
        if config is not None and config.at is not None and config.at > req.as_of:
            missing.append(f"{inst.id}: fee cost configuration was not yet known")
            config = None
        if leg.get("fees_included"):
            # Route input/output includes all pool/route trading fees: no second fee debit.
            out["costs"].append({"name": f"{inst.id}: route trading fees", "amount": None,
                                 "currency": inst.quote, "valuation_amount": 0.0,
                                 "source": leg["source"], "included_in_price": True, "category": "embedded"})
            if leg.get("gas_amount") is not None and not leg.get("gas_currency"):
                missing.append(f"{inst.id}: gas currency is required")
            add_cost(f"{inst.id}: gas", leg.get("gas_amount"), leg.get("gas_currency") or inst.quote, leg["source"], leg["source_at"], instrument_id=inst.id)
        else:
            fee = config.open_fee_rate if config else None
            add_cost(f"{inst.id}: opening fee", leg["quote_amount"] * fee if fee is not None else None,
                     inst.quote, config.source if config else None, config.at if config else None, instrument_id=inst.id)
        if held:
            fee = config.close_fee_rate if config else None
            amount = size * close_native[inst.id] * fee if fee is not None and inst.id in close_native else None
            add_cost(f"{inst.id}: closing fee", amount, inst.quote,
                     config.source if config else None, config.at if config else None, instrument_id=inst.id)
    for extra in req.extra_costs:
        add_cost(extra.name, extra.amount if extra.at is None or extra.at <= req.as_of else None,
                 extra.currency, extra.source, extra.at)
    if req.inventory_path == "transfer":
        if req.transfer_cost is None:
            add_cost("transfer/withdrawal/bridge/rebalance", None, req.valuation_currency)
        else:
            extra = req.transfer_cost
            add_cost(extra.name, extra.amount if extra.at is None or extra.at <= req.as_of else None,
                     extra.currency, extra.source, extra.at)
        out["assumptions"].append("Transfer path is a conditional quote comparison; prices are not locked during transfer")
    known_fixed = sum(c["valuation_amount"] for c in out["costs"] if c["valuation_amount"] is not None)
    fixed_complete = all(c["valuation_amount"] is not None for c in out["costs"])
    out["known_costs"] = known_fixed
    if not held:
        out["gross_profit"] = basis_profit
        out["funding_profit"] = 0.0
        out["holding_cost"] = 0.0
        out["borrowing_cost"] = 0.0
        out["net_profit"] = basis_profit - known_fixed if fixed_complete and not missing and not invalid else None
    else:
        # Keep holding charges separate from fixed execution fees: each horizon
        # incurs its own time-dependent debit, with no funding-scenario scaling.
        holding_terms, holding_missing = [], []
        for inst, leg in ((buy, long_leg), (sell, short_leg)):
            config = costs.get(inst.id)
            rate = config.holding_rate_per_hour if config is not None else None
            borrowing = inst.id == sell.id and inst.kind == "spot"
            if rate is None and not borrowing:
                continue
            if config is not None and config.at is not None and config.at > req.as_of:
                holding_missing.append(f"{inst.id}: holding cost configuration was not yet known")
                rate = None
            elif rate is None:
                holding_missing.append(f"{inst.id}: short spot borrowing cost is unknown")
            if config is not None and config.at is None and rate is not None:
                out["assumptions"].append(f"{inst.id}: holding rate is a user assumption without an observation timestamp")
            holding_terms.append((inst, leg, config, rate, borrowing))
        holding_per_hour = sum(leg["valuation_amount"] * rate
                               for _, leg, _, rate, _ in holding_terms if rate is not None)
        horizons = sorted({8.0, 24.0, 72.0, req.hold_hours})
        factors = {"maintain": 1.0, "half": 0.5, "zero": 0.0, "reverse": -1.0}
        for hours in horizons:
            event_rows = []
            funding_missing = []
            for inst, leg, direction in ((buy, long_leg, -1), (sell, short_leg, 1)):
                if inst.kind != "perpetual":
                    continue
                if not inst.linear:
                    funding_missing.append(f"{inst.id}: inverse funding requires settlement-price path")
                    continue
                events, problem = _funding_events(req, inst, snapshots[inst.id], hours)
                if problem:
                    funding_missing.append(f"{inst.id}: {problem}")
                if not events:
                    continue
                snapshot = snapshots[inst.id]
                funding_price = leg["average_price"]
                if req.funding_notional_basis == "mark":
                    funding_price = snapshot.mark if snapshot is not None else None
                    if funding_price is None or snapshot is None or not _field_fresh(snapshot, "mark", req):
                        funding_missing.append(f"{inst.id}: fresh mark price required for funding notional; entry VWAP requires explicit opt-in")
                        continue
                funding_notional = size * funding_price * leg_fx[inst.id]
                for event in events:
                    cashflow = direction * funding_notional * event["rate"]
                    event_rows.append({**event, "position": "long" if direction == -1 else "short",
                                       "notional": funding_notional, "notional_price": funding_price,
                                       "notional_basis": req.funding_notional_basis,
                                       "quote_currency": inst.quote,
                                       "valuation_currency": req.valuation_currency,
                                       "cashflow": cashflow})
            event_rows.sort(key=lambda event: (event["settlement_at"], event["instrument_id"]))
            holding_rows = []
            for inst, leg, config, rate, borrowing in holding_terms:
                amount = leg["quote_amount"] * rate * hours if rate is not None else None
                holding_rows.append({
                    "name": f"{inst.id}: short spot borrowing" if borrowing else f"{inst.id}: holding cost",
                    "instrument_id": inst.id, "amount": amount, "currency": inst.quote,
                    "valuation_amount": amount * leg_fx[inst.id] if amount is not None else None,
                    "source": config.source if config is not None else None,
                    "at": config.at if config is not None else None,
                    "conditions": config.conditions if config is not None else None,
                    "fx": leg["fx"], "category": "holding", "borrowing": borrowing,
                    "rate_per_hour": rate, "hold_hours": hours,
                })
            known_holding = sum(row["valuation_amount"] for row in holding_rows if row["valuation_amount"] is not None)
            borrow_rows = [row for row in holding_rows if row["borrowing"]]
            borrow_cost = (sum(row["valuation_amount"] for row in borrow_rows)
                           if all(row["valuation_amount"] is not None for row in borrow_rows) else None)
            for scenario, multiplier in factors.items():
                funding = sum(e["cashflow"] for e in event_rows) * multiplier if not funding_missing else None
                gross = basis_profit + funding if basis_profit is not None and funding is not None else None
                net = gross - known_fixed - known_holding if gross is not None and fixed_complete and not missing and not funding_missing and not holding_missing and not invalid else None
                row = {"hold_hours": hours, "scenario": scenario, "funding_multiplier": multiplier,
                       "funding_profit": funding, "basis_profit": basis_profit, "gross_profit": gross,
                       "known_costs": known_fixed + known_holding,
                       "holding_cost": None if holding_missing else known_holding,
                       "borrowing_cost": borrow_cost, "holding_costs": holding_rows,
                       "net_profit": net,
                       "missing_prerequisites": list(dict.fromkeys(missing + funding_missing + holding_missing)),
                       "funding_events": [{**e, "scenario_cashflow": e["cashflow"] * multiplier} for e in event_rows]}
                out["scenarios"].append(row)
                if hours == req.hold_hours and scenario == "maintain":
                    out.update({k: row[k] for k in ("funding_profit", "gross_profit", "net_profit", "known_costs",
                                                   "holding_cost", "borrowing_cost", "holding_costs")})
                    out["costs"].extend(holding_rows)
                    out["funding_events"] = row["funding_events"]
                    missing.extend(funding_missing + holding_missing)
        out["assumptions"].append(
            f"Funding uses frozen {req.funding_notional_basis} prices and constant quote FX; future settlement marks may differ"
        )
        out["assumptions"].append("Funding scenarios scale each estimated rate; settled history is never projected as current funding")
        # Search the projected cashflow path, including continuously accrued
        # holding charges. A zero entry balance alone is not a profitable path.
        if (req.assume_exit_at_entry and out["net_profit"] is not None
                and basis_profit == 0 and sell.kind != "spot"):
            simultaneous = {}
            for event in out["funding_events"]:
                at = event["settlement_at"]
                simultaneous[at] = simultaneous.get(at, 0.0) + event["cashflow"]
            cumulative = -known_fixed
            trajectory = [(0.0, cumulative)]
            crossings = [(0, 0.0)] if cumulative > 0 else []
            for at, cashflow in sorted(simultaneous.items()):
                elapsed = (at - req.as_of) / 3_600_000
                trajectory.append((elapsed, cumulative - holding_per_hour * elapsed))
                cumulative += cashflow
                balance = cumulative - holding_per_hour * elapsed
                trajectory.append((elapsed, balance))
                if balance >= 0:
                    crossings.append((len(trajectory) - 1, elapsed))
            trajectory.append((req.hold_hours, out["net_profit"]))
            if crossings and any(balance > 0 for _, balance in trajectory):
                index, elapsed = crossings[0]
                out["breakeven_hours"] = elapsed
                if any(balance < 0 for _, balance in trajectory[index + 1:]):
                    out["assumptions"].append(
                        "Breakeven is the first projected crossing; later funding or holding charges make the projected balance negative again"
                    )
    out["missing_prerequisites"] = list(dict.fromkeys(missing))
    if invalid:
        out["status"] = "invalidated"
    elif missing:
        out["status"] = "cost_pending" if all("cost" in m or "fee" in m for m in missing) else "data_incomplete"
    elif out["net_profit"] is not None:
        out["status"] = "threshold_met" if out["net_profit"] >= req.threshold else "watching"
    return out


def _connect(data_dir: str | Path | None = None) -> sqlite3.Connection:
    directory = Path(data_dir or os.environ.get("WATCH_MONEY_DATA_DIR", "data"))
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / "opportunities.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("""CREATE TABLE IF NOT EXISTS samples (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL,
        calculation TEXT NOT NULL, result TEXT NOT NULL)""")
    return connection


def _sample(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"],
            "calculation": json.loads(row["calculation"]), "result": json.loads(row["result"])}


def opportunity_event(opportunity: dict, calculation: CalculateRequest) -> dict:
    """Notification contract shared by manual calculations and saved-sample monitors."""
    legs = opportunity["legs"]
    timestamps = [leg.get("source_at") or leg["received_at"] for leg in legs]
    timestamps.extend(event["known_at"] for event in opportunity["funding_events"])
    instruments = {i.id: i for i in calculation.instruments}
    funding_spread = 0.0
    funding_available = calculation.strategy not in SPOT_STRATEGIES
    for instrument_id, direction in ((calculation.buy_instrument_id, -1), (calculation.sell_instrument_id, 1)):
        instrument = instruments.get(instrument_id)
        if instrument is None:
            funding_available = False
            continue
        if instrument.kind == "perpetual":
            snapshot = _snapshot(calculation, instrument)
            funding_fields_fresh = snapshot is not None and all(
                _field_fresh(snapshot, field, calculation)
                for field in ("funding_rate", "funding_interval_hours")
            )
            if snapshot is None or not snapshot.valid or not funding_fields_fresh or snapshot.funding_rate is None or snapshot.funding_interval_hours is None or snapshot.funding_kind not in {"current", "predicted"}:
                funding_available = False
            else:
                funding_spread += direction * snapshot.funding_rate * 8 / snapshot.funding_interval_hours
                timestamps.append(snapshot.field_timestamps.get("funding_rate") or snapshot.source_at or snapshot.received_at)
                if calculation.funding_notional_basis == "mark":
                    timestamps.append(snapshot.field_timestamps.get("mark") or snapshot.source_at or snapshot.received_at)
    timestamps.extend(fx["at"] for fx in opportunity["fx_rates"] if fx.get("source") != "identity")
    timestamps = [at for at in timestamps if isinstance(at, (int, float)) and at > 0]
    buy = instruments.get(calculation.buy_instrument_id)
    buy_amount = legs[0].get("valuation_amount") if len(legs) == 2 else None
    return {
        **opportunity, "category": "opportunity", "type": "opportunity",
        "platforms": opportunity["venues"], "source_at": min(timestamps) if timestamps else None,
        "valid": opportunity["net_profit"] is not None and not opportunity["invalid_reasons"],
        "asset": buy.base if buy else opportunity["asset_id"],
        "total_cost": opportunity["known_costs"], "notional": buy_amount,
        "capacity": opportunity["capacity_base"], "capacity_unit": "base",
        "holding_hours": opportunity["hold_hours"],
        "buy_venue": legs[0]["venue"] if len(legs) == 2 else None,
        "sell_venue": legs[1]["venue"] if len(legs) == 2 else None,
        "spread": (opportunity["entry_spread"] / buy_amount
                   if buy_amount and opportunity["entry_spread"] is not None else None),
        "funding_spread": funding_spread if funding_available else None,
        "funding_spread_unit": "fraction_per_8h",
        "direction": "buy first venue / sell second venue",
        "reason": "; ".join(opportunity["invalid_reasons"] + opportunity["missing_prerequisites"]),
    }


def _computation_settings(request: Request):
    store = getattr(request.app.state, "store", None)
    settings = store.settings() if store is not None else getattr(request.app.state, "settings", None)
    if settings is not None and not getattr(settings, "computation_enabled", True):
        raise HTTPException(409, "Opportunity computation is disabled in settings")
    return settings


@router.post("/calculate")
async def calculate_endpoint(payload: CalculateRequest, request: Request):
    settings = _computation_settings(request)
    result = calculate(payload)
    service = getattr(request.app.state, "notifications", None)
    if service is not None and getattr(settings, "notifications_enabled", True):
        for opportunity in result["opportunities"]:
            await service.evaluate(opportunity_event(opportunity, payload))
    return result


@router.post("/saved")
def save_sample(payload: SaveRequest, request: Request):
    _computation_settings(request)
    serialized = payload.calculation.model_dump_json()
    result = calculate(payload.calculation)
    created_at = now_ms()
    sample_id = hashlib.sha256(f"{created_at}:{payload.name}:{serialized}".encode()).hexdigest()[:24]
    with _connect(getattr(request.app.state, "data_dir", None)) as connection:
        connection.execute("INSERT OR REPLACE INTO samples VALUES (?, ?, ?, ?, ?)",
                           (sample_id, payload.name, created_at, serialized, json.dumps(result, allow_nan=False)))
        return _sample(connection.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone())


@router.get("/saved")
def saved_samples(request: Request):
    with _connect(getattr(request.app.state, "data_dir", None)) as connection:
        return {"samples": [_sample(row) for row in connection.execute("SELECT * FROM samples ORDER BY created_at DESC LIMIT 500")]}


@router.post("/replay")
def replay_sample(payload: ReplayRequest, request: Request):
    _computation_settings(request)
    with _connect(getattr(request.app.state, "data_dir", None)) as connection:
        row = connection.execute("SELECT * FROM samples WHERE id = ?", (payload.sample_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Saved sample not found")
    req = CalculateRequest.model_validate_json(row["calculation"])
    # as_of is the simulated action time. Both reception and operation delays consume
    # the time available to observe input. Filtering every timed input prevents lookahead.
    cutoff = payload.as_of - payload.receive_delay_ms - payload.operation_delay_ms
    schedule_known_at = {key: req.funding_schedule_known_at.get(key, req.as_of)
                         for key in req.funding_schedule_complete_until}
    req.funding_schedule_complete_until = {
        key: end for key, end in req.funding_schedule_complete_until.items() if schedule_known_at[key] <= cutoff
    }
    req.funding_schedule_known_at = schedule_known_at
    req.as_of = payload.as_of
    req.snapshots = [s for s in req.snapshots if s.received_at <= cutoff
                     and (s.source_at is None or s.source_at <= cutoff)
                     and all(at is None or at <= cutoff for at in s.field_timestamps.values())]
    req.orderbooks = [b for b in req.orderbooks if b.received_at <= cutoff and (b.source_at is None or b.source_at <= cutoff)]
    req.route_quotes = [q for q in req.route_quotes if q.received_at <= cutoff and q.source_at <= cutoff]
    req.fx_rates = [f for f in req.fx_rates if f.at <= cutoff]
    req.funding_events = [e for e in req.funding_events if e.known_at <= cutoff]
    for inst in req.instruments:
        if inst.quote_identity_at is not None and inst.quote_identity_at > cutoff:
            inst.quote_identity_at = None
        if inst.mapping_verified_at is not None and inst.mapping_verified_at > cutoff:
            inst.mapping_verified = False
    for config in req.costs:
        if config.at is not None and config.at > cutoff:
            config.open_fee_rate = config.close_fee_rate = config.holding_rate_per_hour = None
    for extra in [*req.extra_costs, *([req.transfer_cost] if req.transfer_cost is not None else [])]:
        if extra.at is not None and extra.at > cutoff:
            extra.amount = None
    # Funding estimates from snapshots already obey the cutoff. Future schedule completeness
    # is user-declared input saved at the original decision, not future settlement evidence.
    result = calculate(req)
    result["replay"] = {"sample_id": payload.sample_id, "as_of": payload.as_of,
                        "information_cutoff": cutoff, "receive_delay_ms": payload.receive_delay_ms,
                        "operation_delay_ms": payload.operation_delay_ms,
                        "semantics": "action time minus reception and operation delay; no later input is visible"}
    return result
