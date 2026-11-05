"""Reviewed Gate perpetual BBO wire parser; no transport or funding-time inference.

Official schema: https://www.gate.com/docs/developers/futures/ws/en/#order-book-api
`result.t` is BBO generation time (ms); envelope time_ms is response time.
The settlement is connection-scoped, so callers must provide its exact scope.
This parser is deliberately not enabled as a live subscription by itself.
"""
from __future__ import annotations

import math
import re

from watch_money.models import Instrument, MarketSnapshot

CHANNEL = 'futures.book_ticker'
ENDPOINTS = {settle: f'wss://fx-ws.gateio.ws/v4/ws/{settle}' for settle in ('btc', 'usdt', 'usd1')}


def gate_update_id(value) -> int | None:
    """Docs use string IDs; examples also use integers. Reject float/bool casts."""
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,30}', value):
        return int(value)
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def gate_futures_book_patch(instrument: Instrument, message: dict, received_at: int,
                            *, native_contract: str, settle: str) -> MarketSnapshot | None:
    """Parse an exact settlement/contract BBO; amounts only prove side existence.

    Futures quantities are contract sizes, not base-coin quantities. This BBO
    patch exposes prices only; no unsupported quantity/volume conversion occurs.
    """
    if (instrument.venue != 'gate' or instrument.kind != 'perpetual' or not instrument.active
            or settle not in ENDPOINTS or (instrument.settle or '').lower() != settle
            or not isinstance(native_contract, str) or not native_contract):
        return None
    if (not isinstance(message, dict) or message.get('channel') != CHANNEL
            or message.get('event') != 'update' or message.get('error')):
        return None
    row = message.get('result')
    if not isinstance(row, dict) or row.get('s') != native_contract:
        return None
    stamp = row.get('t')
    if (type(stamp) is not int or type(received_at) is not int
            or not 100_000_000_000 <= stamp <= received_at or gate_update_id(row.get('u')) is None):
        return None
    source = f'gate:futures.book_ticker:{settle}:{native_contract}'
    values, sources, clears = {}, {}, []
    for field, price_key, amount_key in (('bid', 'b', 'B'), ('ask', 'a', 'A')):
        if price_key not in row or amount_key not in row:
            return None
        amount = _number(row[amount_key])
        if amount is None:
            return None
        # The documented empty side uses empty price and zero amount. Missing
        # or malformed fields are not an explicit clear and reject the frame.
        price = 0.0 if row[price_key] == '' and amount == 0 else _number(row[price_key])
        if price is None or (price == 0 and amount > 0):
            return None
        available = price > 0 and amount > 0
        values[field] = price if available else None
        sources[field] = f'{source}.result.{price_key}:book_generation_time'
        if not available:
            clears.append(field)
            sources[field] += ':explicit_unavailable'
    crossed = values['bid'] is not None and values['ask'] is not None and values['bid'] >= values['ask']
    return MarketSnapshot(instrument_id=instrument.id, venue='gate', source=source,
                          source_at=stamp, received_at=received_at,
                          field_timestamps={field: stamp for field in values},
                          field_received_at={field: received_at for field in values},
                          field_sources=sources, cleared_fields=clears, valid=not crossed,
                          issues=['crossed Gate perpetual BBO'] if crossed else [], is_demo=False, **values)
