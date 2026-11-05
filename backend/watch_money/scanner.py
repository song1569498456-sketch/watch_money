"""Read-only, bounded whole-catalog indicative opportunity scanning."""
from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from itertools import product
from typing import Literal

from fastapi import APIRouter, Query, Request

from .identity import identity_candidates
from .models import Instrument, MarketSnapshot, now_ms
from .quote_identity import quote_identity, requires_quote_identity

router = APIRouter(prefix="/api/scanner", tags=["scanner"])
Sort = Literal["spread", "funding", "asset"]
MarketType = Literal["all", "spot", "perpetual", "spot_perp"]
MAX_CANDIDATES = 100000


def _field_provenance(snapshot: MarketSnapshot | None, field: str) -> dict:
    source = snapshot.field_sources.get(field, snapshot.source) if snapshot else None
    source_at = snapshot.field_timestamps.get(field, snapshot.source_at) if snapshot else None
    received_at = snapshot.field_received_at.get(field, snapshot.received_at) if snapshot else None
    metadata = (field == "funding_interval_hours" and source_at is None and snapshot is not None
                and field in snapshot.field_received_at and isinstance(source, str) and source.endswith(
                    (":catalog_metadata_observed_at", ":public_metadata_observed_at")))
    return {"source": source, "source_at": source_at, "received_at": received_at,
            "metadata_observation": metadata,
            "trusted": isinstance(source, str) and bool(source.strip()) and received_at is not None
            and received_at > 0 and ((source_at is not None and source_at > 0) or metadata)}


def _field(snapshot: MarketSnapshot | None, instrument: Instrument, field: str,
           as_of: int, stale_seconds: float) -> tuple[object, str | None]:
    if snapshot is None:
        return None, "snapshot_missing"
    if snapshot.instrument_id != instrument.id or snapshot.venue != instrument.venue:
        return None, "snapshot_identity_mismatch"
    if not snapshot.valid:
        return None, "snapshot_invalid"
    if snapshot.is_demo:
        return None, "demo_snapshot_excluded"
    if snapshot.bid is not None and snapshot.ask is not None and snapshot.bid >= snapshot.ask:
        return None, "crossed_orderbook"
    value = getattr(snapshot, field)
    if value is None:
        return None, f"{field}_missing"
    received = snapshot.field_received_at.get(field, snapshot.received_at)
    source = snapshot.field_timestamps.get(field, snapshot.source_at)
    if snapshot.received_at > as_of or received > as_of or (source is not None and source > as_of):
        return None, f"{field}_timestamp_in_future"
    metadata_interval = (field == "funding_interval_hours" and source is None
                         and snapshot.field_sources.get(field, "").endswith(
                             (":catalog_metadata_observed_at", ":public_metadata_observed_at")))
    if metadata_interval and field not in snapshot.field_received_at:
        return None, "funding_interval_observation_time_missing"
    effective = min(received, source) if source is not None else received
    ttl_ms = 6 * 3600000 if metadata_interval else stale_seconds * 1000
    if as_of - effective > ttl_ms:
        return None, f"{field}_stale"
    if source is not None and source > received:
        return None, f"{field}_timestamp_in_future"
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, f"{field}_invalid"
    if field != "funding_rate" and value <= 0:
        return None, f"{field}_invalid"
    return value, None


def _funding(instrument: Instrument, snapshot: MarketSnapshot | None, as_of: int,
             stale_seconds: float) -> tuple[dict | None, list[str]]:
    if instrument.kind != "perpetual":
        return None, []
    raw, raw_issue = _field(snapshot, instrument, "funding_rate", as_of, stale_seconds)
    interval, interval_issue = _field(snapshot, instrument, "funding_interval_hours", as_of, stale_seconds)
    kind, kind_issue = _scan_field(snapshot, instrument, "funding_kind", as_of, stale_seconds)
    next_at, next_issue = _field(snapshot, instrument, "next_funding_at", as_of, stale_seconds)
    normalized = raw * 8 / interval if raw is not None and interval is not None else None
    if normalized is not None and not math.isfinite(normalized):
        normalized = None
        interval_issue = "funding_normalization_overflow"
    return {"raw_rate": raw, "interval_hours": interval,
            "kind": kind,
            "normalized_8h_rate": normalized,
            "next_funding_at": next_at}, [
                issue for issue in (raw_issue, interval_issue, kind_issue, next_issue) if issue]


def _scan_field(snapshot, instrument, field, as_of, stale_seconds):
    value, issue = _field(snapshot, instrument, field, as_of, stale_seconds)
    if (field == "funding_kind" and issue == "funding_kind_invalid" and snapshot
            and snapshot.funding_kind in {"current", "predicted", "settled"}):
        return snapshot.funding_kind, None
    return value, issue


def funding_field_state(snapshot: MarketSnapshot | None, instrument: Instrument, field: str,
                        as_of: int, stale_seconds: float) -> dict:
    """Shared numeric/category validity, provenance and reliable-ranking gate.

    Values may survive as labelled observations even when source timing is
    unverified. Only explicit interval metadata may use local observation time.
    """
    value, issue = _scan_field(snapshot, instrument, field, as_of, stale_seconds)
    provenance = _field_provenance(snapshot, field)
    return {**provenance, "value": value, "issue": issue,
            "rankable": issue is None and provenance["trusted"]}


def _market_detail(instrument, snapshot, as_of, stale_seconds):
    fields, values = {}, {}
    for field in ("bid", "ask", "funding_rate", "funding_kind", "funding_interval_hours", "next_funding_at"):
        state = funding_field_state(snapshot, instrument, field, as_of, stale_seconds)
        value, issue = state["value"], state["issue"]
        source_at, received_at = state["source_at"], state["received_at"]
        stamp = min(source_at, received_at) if source_at is not None and received_at is not None else received_at
        source = state["source"]
        values[field] = value
        fields[field] = {"valid": issue is None, "issue": issue, "source": source,
                         "source_at": source_at, "received_at": received_at,
                         "age_seconds": max(0, (as_of - stamp) / 1000) if stamp is not None else None,
                         "rankable": state["rankable"]}
    return {"instrument_id": instrument.id, "venue": instrument.venue, "native_symbol": instrument.native_symbol,
            "native_asset_id": instrument.native_asset_id or instrument.asset_id, "asset_id": instrument.asset_id,
            "base": instrument.base, "quote": instrument.quote, "kind": instrument.kind,
            "contract_size": instrument.contract_size, "linear": instrument.linear,
            "execution_restrictions": instrument.execution_restrictions,
            "order_entry_modes": instrument.order_entry_modes,
            **values, "fields": fields}


def _payment_direction(funding, position):
    if funding is None:
        return None
    rate = funding["raw_rate"]
    direction = "unknown" if rate is None else "none" if rate == 0 else (
        "pay" if (rate > 0) == (position == "long") else "receive")
    return {**funding, "position": position, "payment_direction": direction}


def _candidate(buy: Instrument, sell: Instrument, snapshots: dict[str, MarketSnapshot],
               as_of: int, stale_seconds: float, symbols: list[str], details: dict) -> dict:
    buy_snapshot, sell_snapshot = snapshots.get(buy.id), snapshots.get(sell.id)
    ask, buy_issue = _field(buy_snapshot, buy, "ask", as_of, stale_seconds)
    bid, sell_issue = _field(sell_snapshot, sell, "bid", as_of, stale_seconds)
    missing = [f"buy:{buy_issue}" for _ in [0] if buy_issue]
    missing.extend(f"sell:{sell_issue}" for _ in [0] if sell_issue)
    same_quote = buy.quote == sell.quote
    if not same_quote:
        missing.append("quote_fx_not_provided")
    quote_problems, quote_assets = [], {}
    has_chain_quote = any(requires_quote_identity(item) for item in (buy, sell))
    if has_chain_quote:
        for role, item in (("buy", buy), ("sell", sell)):
            asset_id, problems = quote_identity(item, as_of, stale_seconds * 1000)
            quote_assets[item.id] = asset_id
            quote_problems.extend(f"{role}:{problem}" for problem in problems)
        if not quote_problems and quote_assets[buy.id] != quote_assets[sell.id]:
            quote_problems.append("quote_asset_fx_not_provided")
        missing.extend(quote_problems)
    execution_problems = [f"{role}:{reason}" for role, item in (("buy", buy), ("sell", sell))
                          for reason in item.execution_restrictions]
    missing.extend(execution_problems)
    quote_rankable = not quote_problems and not execution_problems
    spread = bid - ask if same_quote and ask is not None and bid is not None else None
    spread_rate = spread / ask if spread is not None else None
    if spread_rate is not None and not math.isfinite(spread_rate * 100):
        spread = spread_rate = None
        missing.append("price_calculation_overflow")
    long_funding, long_issues = _funding(buy, buy_snapshot, as_of, stale_seconds)
    short_funding, short_issues = _funding(sell, sell_snapshot, as_of, stale_seconds)
    long_funding = _payment_direction(long_funding, "long")
    short_funding = _payment_direction(short_funding, "short")
    missing.extend(f"long:{issue}" for issue in long_issues)
    missing.extend(f"short:{issue}" for issue in short_issues)
    # Zero here denotes a spot leg with no perpetual funding, never a missing rate.
    long_rate = long_funding["normalized_8h_rate"] if long_funding else 0
    short_rate = short_funding["normalized_8h_rate"] if short_funding else 0
    funding_rate = (short_rate - long_rate if (long_funding or short_funding)
                    and long_rate is not None and short_rate is not None else None)
    if funding_rate is not None and not math.isfinite(funding_rate * 100):
        funding_rate = None
        missing.append("funding_comparison_overflow")
    warnings = []
    for role, snapshot, values in [("buy", buy_snapshot, {"ask": ask}),
                                   ("sell", sell_snapshot, {"bid": bid}),
                                   ("long", buy_snapshot, {"funding_rate": long_funding["raw_rate"],
                                    "funding_interval_hours": long_funding["interval_hours"]}
                                    if long_funding else {}),
                                   ("short", sell_snapshot, {"funding_rate": short_funding["raw_rate"],
                                    "funding_interval_hours": short_funding["interval_hours"]}
                                    if short_funding else {})]:
        if snapshot and any(value is not None and snapshot.field_timestamps.get(field, snapshot.source_at) is None
                            for field, value in values.items()):
            warnings.extend(["source_timestamp_missing", f"{role}:source_timestamp_missing"])
    kinds = {item["kind"] for item in (long_funding, short_funding) if item}
    if "settled" in kinds:
        warnings.append("settled_funding_is_not_a_forward_estimate")
    if None in kinds:
        warnings.append("funding_kind_not_provided")
    if len(kinds) > 1:
        warnings.append("funding_kinds_differ")
    if funding_rate is not None:
        warnings.extend(["normalized_rates_are_not_actual_settlement_cashflows",
                         "funding_comparison_assumes_equal_valuation_notionals"])
    buy_detail, sell_detail = details[buy.id], details[sell.id]
    price_rankable = (spread_rate is not None and quote_rankable and buy_detail["fields"]["ask"]["rankable"]
                      and sell_detail["fields"]["bid"]["rankable"])
    reverse_ask, reverse_bid = sell_detail["ask"], buy_detail["bid"]
    reverse = ((reverse_bid - reverse_ask) / reverse_ask if same_quote
               and reverse_ask is not None and reverse_bid is not None else None)
    if reverse is not None and not math.isfinite(reverse * 100):
        reverse = None
    rate_legs = [detail for detail, funding in ((buy_detail, long_funding), (sell_detail, short_funding)) if funding]
    funding_rankable = (funding_rate is not None and len(kinds) == 1 and None not in kinds
                        and all(detail["fields"][field]["rankable"] for detail in rate_legs
                                for field in ("funding_rate", "funding_kind", "funding_interval_hours")))
    next_times = [funding["next_funding_at"] for funding in (long_funding, short_funding) if funding]
    alignment = ("not_applicable" if not next_times else "single_perpetual_leg" if len(next_times) == 1 else
                 "unknown" if any(stamp is None for stamp in next_times) else
                 "synchronized" if next_times[0] == next_times[1] else "different")
    if alignment in {"unknown", "different"}:
        warnings.append("settlement_time_unknown" if alignment == "unknown" else "settlement_times_differ")
    requirements = ["trade_size_not_provided", "fees_not_configured", "executable_depth_not_checked"]
    if buy.mechanism != "orderbook" or sell.mechanism != "orderbook":
        requirements.append("size_specific_route_quotes_not_provided")
    if long_funding or short_funding:
        requirements.append("funding_settlement_schedule_not_verified")
    if buy.contract_size != 1 or sell.contract_size != 1:
        requirements.append("contract_quantity_to_base_conversion")
    if not buy.linear or not sell.linear:
        requirements.append("inverse_contract_conversion")
    if has_chain_quote and quote_problems:
        requirements.append("quote_asset_fx_not_provided" if quote_problems == ["quote_asset_fx_not_provided"]
                            else "quote_token_contract_identity_not_verified")
    strategy = ("perp_spread" if buy.kind == sell.kind == "perpetual" else
                "spot_perp_basis" if "perpetual" in {buy.kind, sell.kind} else "spot_spread")
    return {"id": f"{buy.id}->{sell.id}", "asset_id": buy.asset_id, "asset_symbols": symbols,
            "strategy": strategy, "buy_instrument_id": buy.id, "sell_instrument_id": sell.id,
            "buy_venue": buy.venue, "sell_venue": sell.venue, "buy_kind": buy.kind, "sell_kind": sell.kind,
            "buy_quote": buy.quote, "sell_quote": sell.quote, "buy_ask": ask, "sell_bid": bid,
            "spread_absolute": spread, "spread_rate": spread_rate,
            "spread_pct": spread_rate * 100 if spread_rate is not None else None,
            "spread_quote": buy.quote if same_quote else None,
            "price_rankable": price_rankable, "funding_rankable": funding_rankable,
            "quote_assets": quote_assets,
            "reverse_spread_rate": reverse,
            "reverse_price_rankable": reverse is not None and quote_rankable and sell_detail["fields"]["ask"]["rankable"]
            and buy_detail["fields"]["bid"]["rankable"],
            "buy_market": buy_detail, "sell_market": sell_detail,
            "settlement_alignment": alignment,
            "settlement_time_difference_seconds": abs(next_times[0] - next_times[1]) / 1000
            if len(next_times) == 2 and all(stamp is not None for stamp in next_times) else None,
            "funding": {"long": long_funding, "short": short_funding},
            "funding_8h_rate": funding_rate,
            "funding_8h_pct": funding_rate * 100 if funding_rate is not None else None,
            "buy_source": buy_snapshot.source if buy_snapshot else None,
            "sell_source": sell_snapshot.source if sell_snapshot else None,
            "buy_received_at": buy_snapshot.received_at if buy_snapshot else None,
            "sell_received_at": sell_snapshot.received_at if sell_snapshot else None,
            "missing": sorted(set(missing)), "requirements": sorted(set(requirements)),
            "warnings": sorted(set(warnings)), "net_profit": None, "executable": False,
            "status": "indicative" if spread_rate is not None or funding_rate is not None else "data_unavailable"}


def _group_pairs(venues: Mapping[str, list[Instrument]], market_type: MarketType):
    """Prefer directly comparable quotes without dropping unavailable directions."""
    spot = {venue: [item for item in members if item.kind != "perpetual"]
            for venue, members in venues.items()}
    perpetual = {venue: [item for item in members if item.kind == "perpetual"]
                 for venue, members in venues.items()}
    for same_quote in (True, False):
        for buy_venue, sell_venue in product(sorted(venues), repeat=2):
            if buy_venue == sell_venue:
                continue
            pairs = (product(venues[buy_venue], venues[sell_venue]),) if market_type != "spot_perp" else (
                product(spot[buy_venue], perpetual[sell_venue]),
                product(perpetual[buy_venue], spot[sell_venue]))
            for buy, sell in (pair for pair_set in pairs for pair in pair_set):
                if (buy.quote == sell.quote) == same_quote:
                    yield buy, sell


def scan_markets(instruments: Iterable[Instrument] | Mapping[str, Instrument],
                 snapshots: Iterable[MarketSnapshot] | Mapping[str, MarketSnapshot],
                 as_of: int, stale_seconds: float, limit: int = 100, *, sort: Sort = "spread",
                 max_candidates: int = 10000, q: str = "", platforms: Iterable[str] | None = None,
                 market_type: MarketType = "all", group_offset: int = 0, group_limit: int | None = None) -> dict:
    """Scan all confirmed asset groups, with explicit enumeration/output budgets.

    Both directions of every cross-venue spot/perpetual/pool/RFQ pair are eligible.
    A bounded scan shares evaluations round-robin across asset groups. Missing
    prices/rates remain None. No costs, orders, network calls or account data.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if type(group_offset) is not int or not 0 <= group_offset <= 100_000_000:
        raise ValueError("group_offset must be between 0 and 100000000")
    group_limit = limit if group_limit is None else group_limit
    if type(group_limit) is not int or not 1 <= group_limit <= 1000:
        raise ValueError("group_limit must be between 1 and 1000")
    if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ValueError("max_candidates must be between 1 and 100000")
    if sort not in {"spread", "funding", "asset"}:
        raise ValueError("sort must be spread, funding or asset")
    if market_type not in {"all", "spot", "perpetual", "spot_perp"}:
        raise ValueError("market_type must be all, spot, perpetual or spot_perp")
    if not isinstance(q, str) or len(q) > 300:
        raise ValueError("q must be a string of at most 300 characters")
    if (not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0
            or not isinstance(stale_seconds, (int, float)) or not math.isfinite(stale_seconds)
            or stale_seconds <= 0):
        raise ValueError("as_of and stale_seconds must be valid positive time values")
    catalog = list(instruments.values() if isinstance(instruments, Mapping) else instruments)
    active_catalog = [item for item in catalog if item.active]
    selected_platforms = {name.strip().lower() for name in platforms or [] if name.strip()}
    active = [item for item in active_catalog if not selected_platforms or item.venue.lower() in selected_platforms]
    if market_type in {"spot", "perpetual"}:
        active = [item for item in active if (item.kind == "perpetual") == (market_type == "perpetual")]
    by_id = {item.id: item for item in active}
    identity_rows = [row for row in identity_candidates(active, as_of=as_of) if row["kind"] == "identity"]
    query = q.strip().casefold()
    if query:
        # Select a whole identity group when one native market matches. Searching
        # must not remove contradictory identity evidence inside a mapped group.
        matched_ids = {item.id for item in active if any(query in value.casefold() for value in
                       (item.id, item.asset_id, item.native_asset_id or "", item.base, item.native_symbol))}
        identity_rows = [row for row in identity_rows if matched_ids.intersection(row["instrument_ids"])]
        selected_ids = {identity_id for row in identity_rows for identity_id in row["instrument_ids"]}
        active = [item for item in active if item.id in selected_ids]
    eligible = [row for row in identity_rows if row["comparison_ready"]]
    blocked = [row for row in identity_rows if not row["comparison_ready"]]
    latest = {}
    snapshot_values = snapshots.values() if isinstance(snapshots, Mapping) else snapshots
    for snapshot in snapshot_values:
        old = latest.get(snapshot.instrument_id)
        # Historical callers cannot consume future information. Keep a future
        # envelope only when no past one exists so its missing reason is explicit.
        if old is None or (snapshot.received_at <= as_of and
                           (old.received_at > as_of or snapshot.received_at > old.received_at)):
            latest[snapshot.instrument_id] = snapshot
    details = {item.id: _market_detail(item, latest.get(item.id), as_of, stale_seconds) for item in active}
    groups = []
    possible_by_group = {}
    possible = 0
    for row in eligible:
        venues = defaultdict(list)
        for instrument_id in row["instrument_ids"]:
            venues[by_id[instrument_id].venue].append(by_id[instrument_id])
        count = sum(len(members) for members in venues.values())
        directions = count * count - sum(len(members) ** 2 for members in venues.values())
        if market_type == "spot_perp":
            perpetual = sum(item.kind == "perpetual" for members in venues.values() for item in members)
            directions = 2 * (perpetual * (count - perpetual) - sum(
                sum(item.kind == "perpetual" for item in members)
                * sum(item.kind != "perpetual" for item in members) for members in venues.values()))
        possible += directions
        possible_by_group[row["id"]] = directions
        groups.append((row, venues))
    candidates = []
    candidates_by_group = defaultdict(list)
    pending = deque((row, _group_pairs(venues, market_type)) for row, venues in groups
                    if possible_by_group[row["id"]])
    while pending and len(candidates) < max_candidates:
        row, pairs = pending.popleft()
        pair = next(pairs, None)
        if pair is None:
            continue
        buy, sell = pair
        candidate = _candidate(buy, sell, latest, as_of, stale_seconds, row["symbols"], details)
        candidate["identity_group_id"] = row["id"]
        candidates.append(candidate)
        candidates_by_group[row["id"]].append(candidate)
        pending.append((row, pairs))
    missing_counts = Counter(issue for candidate in candidates for issue in candidate["missing"])
    unavailable = sum(candidate["status"] == "data_unavailable" for candidate in candidates)
    if sort == "asset":
        candidates.sort(key=lambda row: (row["asset_id"], row["buy_instrument_id"], row["sell_instrument_id"]))
    else:
        metric = "spread_rate" if sort == "spread" else "funding_8h_rate"
        rankable = "price_rankable" if sort == "spread" else "funding_rankable"
        candidates.sort(key=lambda row: (not row[rankable], row[metric] is None, -(row[metric] or 0), row["id"]))
    summaries = []
    def compact(candidate):
        return {key: value for key, value in candidate.items()
                if key not in {"buy_market", "sell_market"}}
    for identity in identity_rows:
        members = [details[identity_id] for identity_id in identity["instrument_ids"]]
        evaluated = candidates_by_group[identity["id"]]
        price_rows = [row for row in evaluated if row["price_rankable"]]
        funding_rows = [row for row in evaluated if row["funding_rankable"]]
        best_price = max(price_rows, key=lambda row: row["spread_rate"], default=None)
        best_funding = max(funding_rows, key=lambda row: row["funding_8h_rate"], default=None)
        ordered_metric = "funding_8h_rate" if sort == "funding" else "spread_rate"
        ordered_rank = "funding_rankable" if sort == "funding" else "price_rankable"
        ordered = sorted(evaluated, key=lambda row: (not row[ordered_rank],
                         -(row[ordered_metric] or 0), row["id"]))
        valid_fields = [field for member in members for name, field in member["fields"].items()
                        if name in {"bid", "ask"} and field["valid"]]
        source_times = [field["source_at"] for field in valid_fields if field["source_at"] is not None]
        received_times = [field["received_at"] for field in valid_fields if field["received_at"] is not None]
        total = possible_by_group.get(identity["id"], 0)
        summaries.append({"id": identity["id"], "asset_id": identity["asset_ids"][0],
                          "asset_symbols": identity["symbols"], "identity_confirmed": identity["comparison_ready"],
                          "identity_blockers": identity["blockers"], "venues": identity["venues"],
                          "venue_count": len(identity["venues"]), "market_count": len(members),
                          "possible_directions": total, "scanned_directions": len(evaluated),
                          "reliable_price_directions": len(price_rows), "reliable_funding_directions": len(funding_rows),
                          "excluded_from_price_ranking": len(evaluated) - len(price_rows),
                          "best_spread_candidate": compact(best_price) if best_price else None,
                          "best_funding_candidate": compact(best_funding) if best_funding else None,
                          "best_within_scan": True, "scan_complete": len(evaluated) == total,
                          "directions": [compact(row) for row in ordered[:limit]],
                          "directions_returned": min(len(ordered), limit), "directions_truncated": len(ordered) > limit,
                          "markets": members, "as_of": as_of,
                          "oldest_price_source_at": min(source_times, default=None),
                          "latest_price_received_at": max(received_times, default=None),
                          "max_price_age_seconds": max((field["age_seconds"] for field in valid_fields), default=None)})
    best_key = "best_funding_candidate" if sort == "funding" else "best_spread_candidate"
    rate_key = "funding_8h_rate" if sort == "funding" else "spread_rate"
    if sort == "asset":
        summaries.sort(key=lambda row: row["asset_id"])
    else:
        summaries.sort(key=lambda row: (row[best_key] is None,
                       -(row[best_key][rate_key] if row[best_key] else 0), row["asset_id"]))
    scanned = len(candidates)
    returned = min(limit, scanned)
    return {"as_of": as_of, "stale_seconds": stale_seconds, "candidates": candidates[:limit], "groups": summaries[group_offset:group_offset + group_limit],
            "available_platforms": sorted({item.venue for item in active_catalog}),
            "filters": {"q": q, "platforms": sorted(selected_platforms), "market_type": market_type}, "stats": {
        "catalog_markets": len(catalog), "active_markets": len(active),
        "excluded_by_filters": len(active_catalog) - len(active),
        "identity_groups": len(identity_rows), "eligible_groups": len(eligible), "blocked_groups": len(blocked),
        "blocked_markets": sum(len(row["instrument_ids"]) for row in blocked),
        "identity_blockers": dict(Counter(issue for row in blocked for issue in row["blockers"])),
        "possible_candidates": possible, "scanned": scanned, "returned": returned,
        "unavailable_candidates": unavailable, "unscanned": possible - scanned,
        "reliable_price_candidates": sum(row["price_rankable"] for row in candidates),
        "reliable_funding_candidates": sum(row["funding_rankable"] for row in candidates),
        "groups_total": len(summaries), "groups_returned": len(summaries[group_offset:group_offset + group_limit]),
        "groups_truncated": len(summaries) > group_limit,
        "group_offset": group_offset, "group_limit": group_limit,
        "next_group_offset": group_offset + group_limit if group_offset + group_limit < len(summaries) else None,
        "group_pagination": "live_query_not_snapshot",
        "omitted_by_limit": scanned - returned, "enumeration_truncated": possible > scanned,
        "result_truncated": scanned > returned, "truncated": possible > returned,
        "selection": "bounded_asset_round_robin" if possible > scanned else "global",
        "sort": sort, "limit": limit, "max_candidates": max_candidates, "missing_counts": dict(missing_counts)}}


@router.get("")
async def scan_api(request: Request, limit: int = Query(100, ge=1, le=1000),
             sort: Sort = "spread", max_candidates: int = Query(10000, ge=1, le=MAX_CANDIDATES),
             q: str = Query("", max_length=300), platforms: str = Query("", max_length=2000),
             market_type: MarketType = "all", group_offset: int = Query(0, ge=0, le=100_000_000),
             group_limit: int | None = Query(None, ge=1, le=1000)):
    def build():
        collector = request.app.state.collector
        settings = request.app.state.store.settings()
        catalog = list(collector.instruments.values())
        enabled = settings.computation_enabled and not settings.demo_mode
        instruments = [item for item in catalog if item.venue in settings.enabled_venues] if enabled else []
        result = scan_markets(instruments, dict(collector.snapshots), now_ms(), settings.stale_seconds,
                              limit, sort=sort, max_candidates=max_candidates, q=q, platforms=platforms.split(","),
                              market_type=market_type, group_offset=group_offset, group_limit=group_limit)
        result.update(enabled=enabled, mode="indicative_public_market_scan")
        result["stats"].update(collector_catalog_markets=len(catalog),
                               excluded_by_settings=len(catalog) - len(instruments))
        return result

    return await request.app.state.query_work.json_response('scanner', build)
