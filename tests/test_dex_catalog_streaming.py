"""Official native all-catalog stream fixtures; no real exchange traffic."""
import asyncio

import pytest
from test_dex_streaming import EVENT, Socket, connector, instrument
from watch_money.adapters.dex import HyperliquidAdapter, LighterAdapter
from watch_money.adapters.dex_streaming import (
    DexStreamAdapter,
    HyperliquidCatalogStream,
    _SubscriptionWindow,
    create_dex_stream,
    hyperliquid_patch,
    lighter_patches,
)
from watch_money.adapters.streaming import StreamUnavailable


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: EVENT + 1000)


def book(symbol="BTC", at=EVENT):
    return {"channel": "l2Book", "data": {"coin": symbol, "time": at, "levels": [
        [{"px": "100", "sz": "2", "n": 1}], [{"px": "101", "sz": "3", "n": 1}]]}}


def stats(rows, at=EVENT, *, type="update/market_stats"):
    return {"type": type, "channel": "market_stats:all", "timestamp": at, "market_stats": rows}


def lighter_item(market_id="0"):
    return instrument("lighter", market_id).model_copy(update={"base": "SMALL", "quote": "USDC"})


def row(market_id=0, **values):
    # Shape and temporal semantics: docs.lighter.xyz/api/websocket#market-stats
    return {"market_id": market_id, "best_bid_price": "1899.94", "best_ask_price": "1899.95",
            "mark_price": "1899.99", "index_price": "1900.86", "last_trade_price": "1899.98",
            "current_funding_rate": "0.0008", "funding_rate": "0.0004", "funding_timestamp": EVENT - 3600000,
            "daily_quote_token_volume": 0, "open_interest": "81885817.089747", **values}


def test_lighter_all_market_stats_has_real_bbo_time_and_explicitly_unverified_raw_funding():
    item = lighter_item()
    patch, = lighter_patches({"0": item}, stats({"0": row()}))
    assert patch.bid == 1899.94 and patch.ask == 1899.95 and patch.source_at == EVENT
    assert patch.field_timestamps["bid"] == patch.field_timestamps["ask"] == EVENT
    assert patch.volume == 0 and patch.volume_unit == "USDC"
    supplied = patch.model_dump(exclude_unset=True)
    assert all(field not in supplied for field in ("funding_rate", "funding_kind", "funding_interval_hours", "next_funding_at"))
    assert patch.protocol_metrics["raw_upcoming_funding_rate"] == "0.0008"
    assert patch.protocol_metrics["raw_last_settled_funding_rate"] == "0.0004"
    assert patch.protocol_metrics["last_settled_funding_at_ms"] == str(EVENT - 3600000)
    assert patch.protocol_metrics["funding_unit"] == "unverified"
    assert patch.protocol_metrics["funding_rate_basis"] == "unverified_not_normalized"
    assert patch.protocol_metrics["funding_source_publication_at_ms"] == str(EVENT)
    assert patch.open_interest is None and patch.protocol_metrics["open_interest_unit"] == "unverified"


def test_lighter_missing_publication_time_never_borrows_funding_settlement_or_local_receipt():
    patch, = lighter_patches({"0": lighter_item()}, stats({"0": row()}, at=None))
    assert patch.source_at is None and patch.field_timestamps["bid"] is None
    assert patch.protocol_metrics["funding_source_publication_at_ms"] is None
    assert patch.protocol_metrics["funding_observed_at_ms"] == str(EVENT + 1000)
    assert patch.protocol_metrics["last_settled_funding_at_ms"] == str(EVENT - 3600000)
    patch, = lighter_patches({"0": lighter_item()}, stats({"0": row()}, at=EVENT // 1000))
    assert patch.source_at is None
    patch, = lighter_patches({"0": lighter_item()}, stats({"0": row(funding_timestamp=EVENT // 1000)}))
    assert patch.protocol_metrics["raw_last_settled_funding_timestamp"] == str(EVENT // 1000)
    assert "last_settled_funding_at_ms" not in patch.protocol_metrics


def test_lighter_selects_native_market_ids_and_single_market_shape_without_fabricating_bbo():
    item = lighter_item("12")
    message = {"type": "subscribed/market_stats", "channel": "market_stats:12", "timestamp": EVENT,
               "market_stats": row(12, best_bid_price=None, best_ask_price=None)}
    patch, = lighter_patches({"12": item}, message)
    assert patch.instrument_id == item.id and patch.bid is None and patch.ask is None
    assert "bid" not in patch.model_dump(exclude_unset=True)
    assert lighter_patches({"12": item}, stats({"11": row(11)})) == []
    with pytest.raises(ValueError, match="identity mismatch"):
        lighter_patches({"12": item}, stats({"12": row(11)}))


def test_lighter_funding_only_messages_remain_explicit_raw_metrics_not_rate_patches():
    patch, = lighter_patches({"0": lighter_item()}, stats({"0": {"market_id": 0, "current_funding_rate": "0"}}))
    assert patch.funding_rate is None and patch.protocol_metrics["raw_upcoming_funding_rate"] == "0"
    assert not patch.field_timestamps


def test_hyperliquid_snapshot_time_is_only_for_book_not_asset_context():
    item = instrument(symbol="xyz:BTC")
    patch = hyperliquid_patch(item, book("xyz:BTC"))
    assert patch.bid == 100 and patch.ask == 101
    assert patch.field_timestamps == {"bid": EVENT, "ask": EVENT}
    context = hyperliquid_patch(item, {"channel": "activeAssetCtx", "data": {"coin": "xyz:BTC", "ctx": {
        "funding": "0.0001", "markPx": "100", "time": EVENT,
    }}})
    assert context.source_at is None
    assert context.funding_rate == .0001 and context.funding_interval_hours == 1
    assert all(stamp is None for stamp in context.field_timestamps.values())


@pytest.mark.asyncio
async def test_hyperliquid_347_catalog_markets_are_four_connections_and_694_total_topics():
    items = [instrument(symbol=f"SMALL{index}") for index in range(347)]
    sockets = [Socket([book(items[index].native_symbol)]) for index in (0, 100, 200, 300)]
    factory = connector(sockets)
    stream = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=factory)
    generator = stream.stream(items)
    patches = [await asyncio.wait_for(anext(generator), 1) for _ in range(4)]
    assert len({patch.instrument_id for patch in patches}) == 4
    assert len(factory.calls) == 4 and stream.subscription_count == 694
    assert sum(len(socket.sent) for socket in sockets) == 694
    assert all(socket.sent[0]["subscription"]["type"] == "l2Book" for socket in sockets)
    assert all(socket.sent[0]["subscription"]["fast"] is True for socket in sockets)
    assert stream.catalog_stream and stream.max_catalog_markets == 500
    await generator.aclose()
    assert all(socket.closed for socket in sockets) and not stream._tasks and not stream._streams


@pytest.mark.asyncio
async def test_hyperliquid_global_budget_501_raises_before_any_subscription_no_silent_drop():
    factory = connector([])
    stream = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=factory)
    with pytest.raises(StreamUnavailable, match="500 markets"):
        await anext(stream.stream([instrument(symbol=f"SMALL{index}") for index in range(501)]))
    assert not factory.calls


@pytest.mark.asyncio
async def test_hyperliquid_shard_failure_closes_all_and_root_restart_recreates_subscriptions():
    items = [instrument(symbol=f"SMALL{index}") for index in range(101)]
    sockets = [Socket([ConnectionError("fixture shard disconnect")]), Socket(),
               Socket([book(items[0].native_symbol)]), Socket([book(items[100].native_symbol)])]
    stream = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=connector(sockets))
    with pytest.raises(ConnectionError):
        await anext(stream.stream(items))
    assert sockets[0].closed and sockets[1].closed and not stream._running
    generator = stream.stream(items)
    assert (await asyncio.wait_for(anext(generator), 1)).bid == 100
    await generator.aclose()
    assert all(socket.closed for socket in sockets)


@pytest.mark.asyncio
async def test_lighter_213_markets_need_one_public_subscription_and_json_ping_gets_pong():
    items = [lighter_item(str(index)) for index in range(213)]
    socket = Socket([{"type": "connected"}, {"type": "ping"}, stats({str(index): row(index) for index in range(213)})])
    stream = DexStreamAdapter(LighterAdapter(), connection_factory=connector([socket]))
    generator = stream.stream(items)
    patches = [await asyncio.wait_for(anext(generator), 1) for _ in items]
    assert len({patch.instrument_id for patch in patches}) == 213
    assert socket.sent == [{"type": "subscribe", "channel": "market_stats/all"}, {"type": "pong"}]
    assert stream.subscription_count == 1 and stream.catalog_stream and stream.max_catalog_markets == 5000
    await generator.aclose()
    assert socket.closed


@pytest.mark.asyncio
async def test_lighter_disconnect_reconnect_duplicate_old_and_future_time_handling():
    socket = Socket([{"type": "connected"}, stats({"0": row()}, EVENT + 60_000),
                     stats({"0": row()}, EVENT), stats({"0": row()}, EVENT),
                     stats({"0": row()}, EVENT - 1), stats({"0": row()}, EVENT + 1)])
    second = Socket([{"type": "connected"}, stats({"0": row()}, EVENT + 2)])
    stream = DexStreamAdapter(LighterAdapter(), connection_factory=connector([socket, second]))
    generator = stream.stream([lighter_item()])
    assert (await anext(generator)).source_at == EVENT + 60_000
    assert (await anext(generator)).source_at == EVENT
    assert (await anext(generator)).source_at == EVENT + 1
    await socket.events.put(ConnectionError("fixture disconnected"))
    with pytest.raises(ConnectionError):
        await anext(generator)
    assert socket.closed
    generator = stream.stream([lighter_item()])
    assert (await anext(generator)).source_at == EVENT + 2
    await generator.aclose()


@pytest.mark.asyncio
async def test_lighter_metrics_only_deltas_keep_changes_and_new_source_publications():
    payload = {"0": {"market_id": 0, "current_funding_rate": "0.0008"}}
    socket = Socket([{"type": "connected"}, stats(payload), stats(payload),
                     stats({"0": {"market_id": 0, "current_funding_rate": "0.0009"}}, EVENT + 1)])
    stream = DexStreamAdapter(LighterAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([lighter_item()])
    assert (await anext(generator)).protocol_metrics["raw_upcoming_funding_rate"] == "0.0008"
    next_patch = await asyncio.wait_for(anext(generator), 1)
    assert next_patch.source_at == EVENT + 1 and next_patch.protocol_metrics["raw_upcoming_funding_rate"] == "0.0009"
    assert next_patch.funding_rate is None
    await generator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("payloads", [[{"type": "error"}], [{"type": "connected"}, {"type": "shutdown"}]])
async def test_lighter_source_errors_and_shutdown_fail_to_root(payloads):
    socket = Socket(payloads)
    stream = DexStreamAdapter(LighterAdapter(), connection_factory=connector([socket]))
    with pytest.raises(ConnectionError):
        await anext(stream.stream([lighter_item()]))
    assert socket.closed and not stream._running


def test_native_factory_catalog_capabilities_are_lazy():
    assert isinstance(create_dex_stream(HyperliquidAdapter()), HyperliquidCatalogStream)
    assert create_dex_stream(LighterAdapter()).catalog_stream


@pytest.mark.asyncio
async def test_hyperliquid_subscription_window_survives_close_and_retry_without_real_wait():
    clock = [0.0]
    waits = []

    async def wait(delay):
        waits.append(delay)
        clock[0] += delay

    sockets = [Socket([book()]), Socket([book()]), Socket([book()])]
    stream = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=connector(sockets),
                                      subscription_clock=lambda: clock[0], subscription_wait=wait, subscription_limit=4)
    for _ in range(3):
        generator = stream.stream([instrument()])
        assert (await anext(generator)).bid == 100
        await generator.aclose()
    assert waits == [60.0] and sum(len(socket.sent) for socket in sockets) == 6
    assert all(socket.closed for socket in sockets)


@pytest.mark.asyncio
async def test_failed_partial_send_consumes_full_reservation_across_restart():
    clock = [0.0]
    waits = []

    async def wait(delay):
        waits.append(delay)
        clock[0] += delay

    class FailingSend(Socket):
        async def send(self, text):
            await super().send(text)
            raise ConnectionError("fixture failed after first send")

    first, second = FailingSend(), Socket([book()])
    stream = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=connector([first, second]),
                                      subscription_clock=lambda: clock[0], subscription_wait=wait, subscription_limit=2)
    with pytest.raises(ConnectionError):
        await anext(stream.stream([instrument()]))
    assert first.closed and len(first.sent) == 1
    generator = stream.stream([instrument()])
    assert (await anext(generator)).bid == 100
    assert waits == [60.0]
    await generator.aclose()


@pytest.mark.asyncio
async def test_subscription_window_starts_after_last_send_not_before_opening():
    clock = [0.0]
    waits = []

    async def wait(delay):
        waits.append(delay)
        clock[0] += delay

    class SlowSend(Socket):
        async def send(self, text):
            clock[0] += 2
            await super().send(text)

    first, second = SlowSend([book()]), Socket([book()])
    stream = HyperliquidCatalogStream(HyperliquidAdapter(), connection_factory=connector([first, second]),
                                      subscription_clock=lambda: clock[0], subscription_wait=wait, subscription_limit=2)
    generator = stream.stream([instrument()])
    await anext(generator)
    await generator.aclose()
    assert clock[0] == 4
    generator = stream.stream([instrument()])
    await anext(generator)
    assert clock[0] == 64 and waits == [60.0]
    await generator.aclose()


@pytest.mark.asyncio
async def test_pending_unsealed_reservation_wakes_after_seal_without_lost_event():
    clock = [0.0]
    waits = []

    async def wait(delay):
        waits.append(delay)
        clock[0] += delay

    window = _SubscriptionWindow(limit=2, clock=lambda: clock[0], wait=wait)
    first = await window.reserve(2)
    pending = asyncio.create_task(window.reserve(2))
    await asyncio.sleep(0)
    assert not pending.done()
    window.seal(first)
    second = await asyncio.wait_for(pending, 1)
    assert second["count"] == 2 and waits == [60.0] and clock[0] == 60
