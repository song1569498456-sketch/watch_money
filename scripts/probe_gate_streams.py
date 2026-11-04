#!/usr/bin/env python3
"""Explicit live PUBLIC Gate BBO probe; no database/account/identity modifications."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from watch_money.adapters.cex import CexAdapter
from watch_money.adapters.gate_streaming import GateStreamAdapter
from watch_money.models import now_ms
from watch_money.scanner import funding_field_state


def scope(item):
    return "spot" if item.kind == "spot" else "futures:" + (item.settle or "unknown").lower()


async def probe(adapter, stream, *, seconds, max_markets, scopes):
    started = now_ms()
    latest, selected, samples = {}, [], []
    count = 0
    error_type = None
    elapsed = 0
    catalog_count = requested_count = eligible_count = 0
    live_diagnostics = None
    checked_at = None
    cleanup_errors = []
    try:
        catalog = await asyncio.wait_for(adapter.discover(), 90)
        requested = [item for item in catalog if scope(item) in scopes]
        requested_count = len(requested)
        eligible = [item for item in requested if stream.supports_instrument(item)]
        catalog_count, eligible_count = len(catalog), len(eligible)
        selected = stream.select_instruments(sorted(eligible, key=lambda item: item.id))[:max_markets]
        if not selected:
            raise ValueError("No supported discovered Gate markets")
        started_stream = monotonic()
        iterator = stream.stream(selected).__aiter__()
        pending = None
        try:
            while monotonic() - started_stream < seconds:
                remaining = seconds - (monotonic() - started_stream)
                pending = asyncio.create_task(anext(iterator))
                done, _ = await asyncio.wait({pending}, timeout=max(0.001, remaining))
                if not done:
                    break
                patch = pending.result()
                pending = None
                count += 1
                latest[patch.instrument_id] = patch
                if len(samples) < 16:
                    samples.append(
                        {
                            key: getattr(patch, key)
                            for key in ("instrument_id", "source", "source_at", "received_at", "bid", "ask")
                        }
                    )
        finally:
            checked_at = now_ms()
            elapsed = monotonic() - started_stream
            live_diagnostics = stream.diagnostics()
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await iterator.aclose()
    except Exception as exc:
        error_type = type(exc).__name__
    finally:
        if live_diagnostics is None:
            live_diagnostics = stream.diagnostics()
        for resource in (stream, adapter):
            try:
                await resource.close()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
    at = checked_at if checked_at is not None else now_ms()
    by_scope = {key: {"selected": 0, "observed": 0, "timed_bbo": 0, "fresh_bbo": 0} for key in sorted(scopes)}
    for item in selected:
        row = by_scope.setdefault(scope(item), {"selected": 0, "observed": 0, "timed_bbo": 0, "fresh_bbo": 0})
        row["selected"] += 1
        patch = latest.get(item.id)
        if patch is None:
            continue
        row["observed"] += 1
        states = [funding_field_state(patch, item, field, at, 60) for field in ("bid", "ask")]
        row["timed_bbo"] += int(
            all(
                state["trusted"] and getattr(patch, field) is not None
                for field, state in zip(("bid", "ask"), states)
            )
        )
        row["fresh_bbo"] += int(all(state["rankable"] for state in states))
    return {
        "scope": "public Gate BBO only; no user database touched",
        "started_at": started,
        "checked_at": at,
        "stream_elapsed_seconds": round(elapsed, 3),
        "error_type": error_type,
        "cleanup_error_types": cleanup_errors,
        "catalog_markets": catalog_count,
        "discovery_complete": getattr(adapter, "discovery_complete", None),
        "requested_scope_catalog_markets": requested_count,
        "unsupported_requested_scope_markets": requested_count - eligible_count,
        "eligible_markets_in_requested_scopes": eligible_count,
        "selected_markets": len(selected),
        "truncated": len(selected) < eligible_count,
        "received_patches": count,
        "coverage_by_scope": by_scope,
        "source_streams": live_diagnostics,
        "closed_source_streams": stream.diagnostics(),
        "first_patch_samples": samples,
        "limits": [
            "Fresh BBO counts do not establish verified asset identity, executable profit or funding freshness.",
            "No funding-rate clock is inferred from BBO or envelope time.",
            "A short probe does not establish 24-hour stability; diagnostic acknowledgements are not fresh coverage.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly request public exchange network access"
    )
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--max-markets", type=int, default=8000)
    parser.add_argument("--scopes", default="spot,usdt,btc,usd1")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    values = args.scopes.split(",")
    if (
        not args.live
        or not 5 <= args.seconds <= 600
        or not 1 <= args.max_markets <= 8000
        or not set(values) <= {"spot", "usdt", "btc", "usd1"}
    ):
        parser.error("Require --live, 5..600 seconds, 1..8000 markets, scopes spot/usdt/btc/usd1")
    scopes = {"spot" if value == "spot" else "futures:" + value for value in values}
    adapter = CexAdapter("gate")
    report = asyncio.run(
        probe(
            adapter,
            GateStreamAdapter(adapter),
            seconds=args.seconds,
            max_markets=args.max_markets,
            scopes=scopes,
        )
    )
    report["provenance"] = "live public exchange probe; failures and missing coverage retained"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=".gate-probe-", dir=args.report.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(path, args.report)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print(
        json.dumps(
            {
                "received_patches": report["received_patches"],
                "error_type": report["error_type"],
                "coverage_by_scope": report["coverage_by_scope"],
            }
        )
    )


if __name__ == "__main__":
    main()
