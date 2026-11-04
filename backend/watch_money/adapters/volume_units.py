"""Explicit ticker-volume units shared by REST and WebSocket adapters.

Audited against installed CCXT 4.5.91 parsers and the official field contracts:
https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-tickers
https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/24hr-Ticker-Price-Change-Statistics
https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/24hr-Ticker-Price-Change-Statistics
https://www.gate.com/docs/developers/futures/
https://docs.deribit.com/api-reference/market-data/public-ticker

CCXT's unified ``baseVolume`` is not proof of base units for derivatives:
okx.parse_ticker currently copies raw contract count vol24h into that field.
Unreviewed derivative schemas remain unknown instead of inheriting that label.
"""
from __future__ import annotations

import math
from typing import Any

from watch_money.models import Instrument

VolumeFields = tuple[float | None, str | None, str | None]
UNKNOWN: VolumeFields = (None, None, None)


def _amount(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def volume_fields(client_id: str, instrument: Instrument, ticker: dict,
                  market: dict | None = None) -> VolumeFields:
    """Return (value, unit, source suffix), or three Nones for an unknown unit.

    Sources are relative to the caller's endpoint, e.g.
    ``info.volCcy24h:rolling_24h``. No current/last price estimates a historic
    inverse-contract base volume. The optional catalog market is used only to
    reject contradictory metadata, so WebSocket callers need no market object.
    """
    if not isinstance(ticker, dict):
        return UNKNOWN
    raw = ticker.get("info")
    raw = raw if isinstance(raw, dict) else {}
    market = market if isinstance(market, dict) else {}
    if ticker.get("symbol") not in (None, instrument.native_symbol):
        return UNKNOWN

    def field(container, key, unit, prefix="info"):
        value = _amount(container.get(key))
        return (value, unit, f"{prefix}.{key}:rolling_24h") if value is not None else UNKNOWN

    if client_id == "okx":
        expected_types = {"SPOT", "MARGIN"} if instrument.kind == "spot" else {"SWAP", "FUTURES"}
        if raw.get("instType") is not None and raw["instType"] not in expected_types:
            return UNKNOWN
        if raw.get("instId") and market.get("id") and raw["instId"] != market["id"]:
            return UNKNOWN
        if instrument.kind == "perpetual":
            # volCcy24h is base currency on BOTH linear and inverse contracts.
            native = field(raw, "volCcy24h", instrument.base)
            if native[0] is not None:
                return native
            contracts = _amount(raw.get("vol24h"))
            if contracts is None:
                return UNKNOWN
            metadata = market.get("info")
            metadata = metadata if isinstance(metadata, dict) else {}
            currency = metadata.get("ctValCcy")
            currencies = {instrument.base, market.get("baseId")}
            multiplier = _amount(metadata.get("ctMult", "1"))
            size = _amount(market.get("contractSize", instrument.contract_size))
            if (instrument.linear and currency in currencies | {None, ""} and multiplier == 1
                    and size == instrument.contract_size and size > 0):
                converted = _amount(contracts * size)
                if converted is not None:
                    return converted, instrument.base, "info.vol24h*instrument.contract_size:rolling_24h"
            # Inverse contract counts are useful, but are not a BTC volume.
            return contracts, "contracts", "info.vol24h:rolling_24h"
        if instrument.kind == "spot":
            for key, unit in (("vol24h", instrument.base), ("volCcy24h", instrument.quote)):
                result = field(raw, key, unit)
                if result[0] is not None:
                    return result

    if instrument.kind == "spot":
        # The ordinary spot unified fields retain base/quote semantics.
        for key, unit in (("baseVolume", instrument.base), ("quoteVolume", instrument.quote)):
            value = _amount(ticker.get(key))
            if value is not None:
                return value, unit, f"{key}:rolling_24h"
        return UNKNOWN
    if instrument.kind != "perpetual":
        return UNKNOWN

    if client_id in {"binance", "binanceusdm", "binancecoinm"}:
        if instrument.linear:
            candidates = (("volume", instrument.base), ("v", instrument.base),
                          ("quoteVolume", instrument.quote), ("q", instrument.quote))
        else:
            # COIN-M REST baseVolume / stream q is coin volume; volume / v is
            # contracts, unlike the same-named USD-M field.
            candidates = (("baseVolume", instrument.base), ("q", instrument.base),
                          ("volume", "contracts"), ("v", "contracts"))
        for key, unit in candidates:
            result = field(raw, key, unit)
            if result[0] is not None:
                return result
    elif client_id in {"gate", "gateio"}:
        for key, unit in (("volume_24h_base", instrument.base), ("volume_24h_quote", instrument.quote)):
            result = field(raw, key, unit)
            if result[0] is not None:
                return result
    elif client_id == "deribit":
        stats = raw.get("stats")
        stats = stats if isinstance(stats, dict) else raw
        result = field(stats, "volume", instrument.base, "info.stats" if stats is not raw else "info")
        if result[0] is not None:
            return result
        # volume_usd is explicitly USD, not necessarily the market's quote.
        result = field(stats, "volume_usd", "USD", "info.stats" if stats is not raw else "info")
        if result[0] is not None:
            return result
    return UNKNOWN
