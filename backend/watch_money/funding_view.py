"""Whole-catalog funding rankings and verified cross-venue comparison matrices."""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .identity import identity_candidates
from .models import Instrument, MarketSnapshot, now_ms
from .scanner import funding_field_state

router = APIRouter(prefix="/api/funding", tags=["funding"])
Sort = Literal["rate", "spread", "asset"]
RateBasis = Literal["raw", "normalized8h"]
_FIELDS = ("funding_rate", "funding_kind", "funding_interval_hours", "next_funding_at")


def _value(snapshot: MarketSnapshot | None, instrument: Instrument, field: str, as_of: int,
           stale_seconds: float) -> tuple[object, dict]:
    provenance = funding_field_state(snapshot, instrument, field, as_of, stale_seconds)
    value, issue = provenance["value"], provenance["issue"]
    source_at, received_at, source = [provenance[key] for key in ("source_at", "received_at", "source")]
    metadata = provenance["metadata_observation"]
    effective = min(received_at, source_at) if received_at is not None and source_at is not None else received_at
    return value, {"valid": issue is None, "rankable": provenance["rankable"],
                   "reason": issue, "source": source,
                   "source_at": source_at, "received_at": received_at,
                   "age_seconds": max(0, (as_of - effective) / 1000) if effective is not None else None,
                   "source_timestamp_missing": source_at is None, "metadata_observation": metadata,
                   "ttl_seconds": 21600 if metadata else stale_seconds}


def _market(instrument: Instrument, snapshot: MarketSnapshot | None, identity: dict,
            as_of: int, stale_seconds: float) -> dict:
    fields = {field: _value(snapshot, instrument, field, as_of, stale_seconds) for field in _FIELDS}
    rate, kind, interval, next_at = [fields[field][0] for field in _FIELDS]
    normalized = rate * 8 / interval if rate is not None and interval is not None else None
    missing = [validity["reason"] for _, validity in fields.values() if validity["reason"]]
    if normalized is not None and not math.isfinite(normalized):
        normalized = None
        missing.append("funding_normalization_overflow")
    warnings = []
    if any(value is not None and validity["source_timestamp_missing"] for value, validity in fields.values()):
        warnings.append("source_timestamp_missing")
    if kind == "settled":
        warnings.append("observed_settled_rate_is_not_a_settlement_history_series")
    if kind is None:
        warnings.append("funding_kind_unknown")
    if next_at is not None and next_at <= as_of:
        warnings.append("next_funding_time_has_elapsed")
    blockers = identity["blockers"]
    status = ("verified" if identity["comparison_ready"] else
              "confirmation_required" if identity["identity_compatible"] else
              "unverified" if "unverified_asset_mapping" in blockers else "conflict")
    return {"instrument_id": instrument.id, "venue": instrument.venue,
            "native_symbol": instrument.native_symbol, "base": instrument.base, "quote": instrument.quote,
            "settle": instrument.settle, "asset_id": instrument.asset_id,
            "native_asset_id": instrument.native_asset_id or instrument.asset_id,
            "identity_group_id": identity["id"], "identity_status": status, "identity_blockers": blockers,
            "raw_rate": rate, "kind": kind, "interval_hours": interval, "normalized_8h_rate": normalized,
            "next_funding_at": next_at, "countdown_seconds": max(0, (next_at - as_of) / 1000)
            if next_at is not None else None,
            "source": snapshot.source if snapshot else None, "source_at": snapshot.source_at if snapshot else None,
            "received_at": snapshot.received_at if snapshot else None,
            "field_validity": {field: validity for field, (_, validity) in fields.items()},
            "rate_rankable": all(fields[field][1]["rankable"] for field in ("funding_rate", "funding_kind")),
            "comparison_rankable": normalized is not None and all(fields[field][1]["rankable"]
                                      for field in ("funding_rate", "funding_kind", "funding_interval_hours")),
            "observed": {field: getattr(snapshot, field, None) if snapshot else None for field in _FIELDS},
            "missing": sorted(set(missing)), "warnings": warnings,
            "observation_type": "local_public_snapshot", "is_settled_history": False}


def _group(identity: dict, rows: list[dict], *, details: bool = False) -> dict:
    valid = [row for row in rows if row["comparison_rankable"]]
    kinds = {row["kind"] for row in valid}
    missing, warnings = [], ["normalized_rate_difference_is_not_settlement_cashflow"]
    if not identity["comparison_ready"]:
        missing.extend(identity["blockers"] or ["asset_identity_not_verified"])
    if len({row["venue"] for row in valid}) < 2:
        missing.append("two_venues_with_valid_normalized_rates_required")
    if None in kinds or not kinds:
        missing.append("funding_kind_unknown")
    if len(kinds) > 1:
        warnings.append("funding_kinds_differ")
    if len({row["quote"] for row in rows}) > 1:
        warnings.append("quotes_differ_equal_valuation_notionals_required")
    if len({row["next_funding_at"] for row in valid if row["next_funding_at"] is not None}) > 1:
        warnings.append("settlement_times_differ")
    if "settled" in kinds:
        warnings.append("settled_snapshots_are_not_forward_estimates")
    if any("source_timestamp_missing" in row["warnings"] for row in valid):
        warnings.append("source_timestamp_missing")
    best, comparisons = None, []
    if not missing:
        # Compare current/predicted/settled within their own type. A third,
        # incompatible venue must not disable an otherwise valid two-venue pair.
        for kind in sorted(kinds):
            compatible = [row for row in valid if row["kind"] == kind]
            by_venue = {}
            for row in compatible:
                previous = by_venue.get(row["venue"])
                if previous is None or row["normalized_8h_rate"] > previous["normalized_8h_rate"]:
                    by_venue[row["venue"]] = row
            maxima = sorted(by_venue.values(), key=lambda row: (-row["normalized_8h_rate"], row["instrument_id"]))[:2]
            kind_best = None
            for long in compatible:
                short = next((item for item in maxima if item["venue"] != long["venue"]), None)
                if short is None:
                    continue
                difference = short["normalized_8h_rate"] - long["normalized_8h_rate"]
                if math.isfinite(difference) and (kind_best is None or difference > kind_best[0]):
                    kind_best = difference, long, short
            comparisons.append({"funding_kind": kind, "spread_8h_rate": kind_best[0] if kind_best else None,
                                "long_instrument_id": kind_best[1]["instrument_id"] if kind_best else None,
                                "short_instrument_id": kind_best[2]["instrument_id"] if kind_best else None,
                                "comparison_ready": kind_best is not None})
            if kind_best is not None and (best is None or kind_best[0] > best[0]):
                best = kind_best
        if best is None:
            missing.append("funding_kinds_differ" if len(kinds) > 1
                           else "two_venues_with_valid_normalized_rates_required")
    columns = ("instrument_id", "venue", "native_symbol", "quote", "raw_rate", "kind", "interval_hours",
               "normalized_8h_rate", "next_funding_at", "countdown_seconds", "missing", "warnings")
    if details:
        columns += ("base", "asset_id", "identity_group_id", "identity_status", "identity_blockers",
                    "field_validity", "observed", "source")
    spread = best[0] if best and math.isfinite(best[0]) else None
    return {"id": identity["id"], "asset_id": rows[0]["asset_id"], "symbols": identity["symbols"],
            "instrument_ids": identity["instrument_ids"], "venues": identity["venues"],
            "cells": [{key: row[key] for key in columns} for row in rows],
            "spread_8h_rate": spread, "funding_kind": best[1]["kind"] if best else next(iter(kinds)) if len(kinds) == 1 else None,
            "compatible_comparisons": comparisons,
            "long_instrument_id": best[1]["instrument_id"] if best else None,
            "short_instrument_id": best[2]["instrument_id"] if best else None,
            "identity_status": rows[0]["identity_status"], "identity_blockers": identity["blockers"],
            "comparison_ready": spread is not None, "missing": missing, "warnings": warnings,
            "comparison_type": "normalized_rate_difference_not_cashflow", "net_profit": None}


def build_funding_view(instruments: Iterable[Instrument] | Mapping[str, Instrument],
                       snapshots: Iterable[MarketSnapshot] | Mapping[str, MarketSnapshot],
                       as_of: int, stale_seconds: float, *, sort: Sort = "rate", limit: int = 500,
                       rate_basis: RateBasis = "normalized8h") -> dict:
    """Build a full perpetual ranking and verified same-asset venue matrix."""
    if type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    if sort not in {"rate", "spread", "asset"} or rate_basis not in {"raw", "normalized8h"}:
        raise ValueError("invalid funding sort or rate basis")
    if (type(as_of) is not int or as_of < 0 or not isinstance(stale_seconds, (int, float))
            or not math.isfinite(stale_seconds) or stale_seconds <= 0):
        raise ValueError("invalid funding observation time or stale threshold")
    catalog = list(instruments.values() if isinstance(instruments, Mapping) else instruments)
    markets = [item for item in catalog if item.active and item.kind == "perpetual"]
    identities = [row for row in identity_candidates(markets, as_of=as_of) if row["kind"] == "identity"]
    by_market = {instrument_id: identity for identity in identities for instrument_id in identity["instrument_ids"]}
    latest = {}
    for snapshot in snapshots.values() if isinstance(snapshots, Mapping) else snapshots:
        old = latest.get(snapshot.instrument_id)
        if old is None or (snapshot.received_at <= as_of and
                           (old.received_at > as_of or snapshot.received_at > old.received_at)):
            latest[snapshot.instrument_id] = snapshot
    rows = [_market(item, latest.get(item.id), by_market[item.id], as_of, stale_seconds) for item in markets]
    rows_by_id = {row["instrument_id"]: row for row in rows}
    groups = [_group(identity, [rows_by_id[identity_id] for identity_id in identity["instrument_ids"]])
              for identity in identities if identity["comparison_ready"] and len(identity["venues"]) > 1]
    matrix_groups = [_group(identity, [rows_by_id[identity_id] for identity_id in identity["instrument_ids"]], details=True)
                     for identity in identities]
    spread_by_group = {group["id"]: group["spread_8h_rate"] for group in groups}
    metric = "raw_rate" if rate_basis == "raw" else "normalized_8h_rate"
    if sort == "asset":
        rows.sort(key=lambda row: (row["asset_id"], row["venue"], row["instrument_id"]))
        groups.sort(key=lambda group: group["asset_id"])
        matrix_groups.sort(key=lambda group: (group["asset_id"], group["id"]))
    elif sort == "spread":
        rows.sort(key=lambda row: (spread_by_group.get(row["identity_group_id"]) is None,
                                  -(spread_by_group.get(row["identity_group_id"]) or 0),
                                  row["asset_id"], row["instrument_id"]))
        groups.sort(key=lambda group: (group["spread_8h_rate"] is None, -(group["spread_8h_rate"] or 0), group["id"]))
        matrix_groups.sort(key=lambda group: (group["spread_8h_rate"] is None, -(group["spread_8h_rate"] or 0), group["id"]))
    else:
        rankability = "rate_rankable" if rate_basis == "raw" else "comparison_rankable"
        rows.sort(key=lambda row: (not row[rankability], row[metric] is None, -(row[metric] or 0), row["instrument_id"]))
        groups.sort(key=lambda group: (group["asset_id"], group["id"]))
        matrix_groups.sort(key=lambda group: (group["asset_id"], group["id"]))
    return {"as_of": as_of, "observation_type": "local_public_snapshots",
            "is_settled_history": False, "rows": rows[:limit], "groups": groups[:limit],
            "matrix_groups": matrix_groups[:limit], "venues": sorted({row["venue"] for row in rows}), "stats": {
                "catalog_markets": len(catalog), "perpetual_markets": len(markets),
                "fresh_rates": sum(row["rate_rankable"] for row in rows),
                "normalized_rates": sum(row["comparison_rankable"] for row in rows),
                "unknown_periods": sum(row["interval_hours"] is None for row in rows),
                "unverified_markets": sum(row["identity_status"] != "verified" for row in rows),
                "rows_total": len(rows), "rows_returned": min(len(rows), limit),
                "groups_total": len(groups), "groups_returned": min(len(groups), limit),
                "matrix_groups_total": len(matrix_groups), "matrix_groups_returned": min(len(matrix_groups), limit),
                "matrix_groups_truncated": len(matrix_groups) > limit,
                "comparable_groups": sum(group["comparison_ready"] for group in groups),
                "rows_truncated": len(rows) > limit, "groups_truncated": len(groups) > limit,
                "missing_counts": dict(Counter(reason for row in rows for reason in row["missing"])),
                "sort": sort, "rate_basis": rate_basis, "limit": limit}}


@router.get("")
def funding_api(request: Request, sort: Sort = "rate", limit: int = Query(500, ge=1, le=5000),
                rate_basis: RateBasis = "normalized8h", venue: str | None = Query(default=None, max_length=50),
                search: str = Query(default="", max_length=200),
                platforms: str | None = Query(default=None, max_length=2000)):
    collector = request.app.state.collector
    settings = request.app.state.store.settings()
    catalog = list(collector.instruments.values())
    enabled = not settings.demo_mode
    selected = [item for item in catalog if item.venue in settings.enabled_venues] if enabled else []
    venues = sorted({item.venue for item in selected if item.active and item.kind == "perpetual"})
    scoped = [item for item in selected if item.venue == venue] if venue is not None else selected
    platform_filter = None
    if platforms is not None:
        platform_filter = sorted({value.strip() for value in platforms.split(",") if value.strip()})
        scoped = [item for item in scoped if item.venue in platform_filter]
    if needle := search.strip().casefold():
        matching_assets = {item.asset_id for item in scoped if needle in
                           f"{item.base} {item.asset_id} {item.native_asset_id or ''} {item.native_symbol}".casefold()}
        scoped = [item for item in scoped if item.asset_id in matching_assets]
    result = build_funding_view(scoped, dict(collector.snapshots), now_ms(), settings.stale_seconds,
                                sort=sort, limit=limit, rate_basis=rate_basis)
    result.update(enabled=enabled, venues=venues, venue_filter=venue, platforms_filter=platform_filter, search=search)
    result["stats"].update(collector_catalog_markets=len(catalog), excluded_by_settings=len(catalog) - len(selected))
    return JSONResponse(result)
