import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from watch_money.adapters.dex import DydxAdapter, HyperliquidAdapter, LighterAdapter
from watch_money.adapters.dex_streaming import (
    ENDPOINTS,
    DexStreamAdapter,
    _connection_factory,
    create_dex_stream,
    dydx_patches,
    hyperliquid_patch,
)
from watch_money.adapters.streaming import StreamUnavailable
from watch_money.models import Instrument

EVENT = 1_800_000_000_000


@pytest.fixture(autouse=True)
def source_clock(monkeypatch):
    monkeypatch.setattr("watch_money.adapters.dex_streaming.now_ms", lambda: EVENT + 1000)


def instrument(venue="hyperliquid", symbol="BTC"):
    return Instrument(id=f"{venue}:perpetual:{symbol}", venue=venue, native_symbol=symbol,
                      asset_id=f"{venue}:{symbol}", base=symbol.split(":")[-1].split("-")[0],
                      quote="USDC" if venue == "hyperliquid" else "USD", settle="USDC", kind="perpetual")


def bbo(symbol="BTC", time=EVENT, bid="100", ask="101"):
    return {"channel": "bbo", "data": {"coin": symbol, "time": time,
                                          "bbo": [{"px": bid, "sz": "1"}, {"px": ask, "sz": "2"}]}}


def markets(contents, *, type="channel_data", sequence=2):
    return {"type": type, "channel": "v4_markets", "connection_id": "session", "message_id": sequence,
            "contents": contents}


class Socket:
    def __init__(self, payloads=(), *, auto_pong=True):
        self.events = asyncio.Queue()
        for payload in payloads:
            self.events.put_nowait(payload)
        self.sent = []
        self.closed = False
        self.auto_pong = auto_pong

    async def recv(self):
        value = await self.events.get()
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, str) else json.dumps(value)

    async def send(self, text):
        value = json.loads(text)
        self.sent.append(value)
        if value == {"method": "ping"} and self.auto_pong:
            self.events.put_nowait({"channel": "pong"})

    async def close(self):
        self.closed = True


def connector(sockets):
    calls = []

    @asynccontextmanager
    async def connect(endpoint):
        calls.append(endpoint)
        socket = sockets[len(calls) - 1]
        try:
            yield socket
        finally:
            await socket.close()

    connect.calls = calls
    return connect


def test_hyperliquid_bbo_timestamp_and_sparse_context_funding_semantics():
    item = instrument(symbol="xyz:BTC")
    patch = hyperliquid_patch(item, bbo("xyz:BTC"))
    values = patch.model_dump(exclude_unset=True)
    assert patch.source_at == EVENT and patch.bid == 100 and patch.ask == 101
    assert patch.field_timestamps == {"bid": EVENT, "ask": EVENT}
    assert "xyz:BTC" in patch.source and not patch.is_demo
    assert "funding_rate" not in values and "mark" not in values
    patch = hyperliquid_patch(item, {"channel": "activeAssetCtx", "data": {"coin": "xyz:BTC", "ctx": {
        "markPx": "100", "funding": "0", "openInterest": "0", "dayNtlVlm": "0", "time": EVENT,
    }}})
    assert patch.funding_rate == 0 and patch.funding_interval_hours == 1 and patch.funding_kind == "current"
    assert patch.volume == 0 and patch.volume_unit == "USDC" and patch.oi_unit == "BTC"
    assert patch.source_at is None and all(value is None for value in patch.field_timestamps.values())
    assert set(patch.field_received_at) == set(patch.field_sources)
    assert "index" not in patch.model_dump(exclude_unset=True)


def test_hyperliquid_rejects_fake_times_and_unusable_values_and_flags_crossed_pair():
    item = instrument()
    patch = hyperliquid_patch(item, bbo(time=EVENT // 1000, bid="102"))
    assert patch.source_at is None and not patch.valid and patch.issues
    assert hyperliquid_patch(item, bbo(bid="NaN", ask="0")) is None
    assert hyperliquid_patch(item, bbo("ETH")) is None
    assert hyperliquid_patch(item, {"channel": "pong"}) is None
    empty = bbo()
    empty["data"]["bbo"] = [None, {"px": "101", "sz": "0"}]
    assert hyperliquid_patch(item, empty) is None
    with pytest.raises(ValueError, match="malformed bbo"):
        hyperliquid_patch(item, {"channel": "bbo", "data": {"coin": "BTC", "bbo": []}})


def test_dydx_initial_and_delta_fields_never_refresh_unrelated_timestamps():
    item = instrument("dydx", "BTC-USD")
    initial = markets({"markets": {"BTC-USD": {"oraclePrice": "100", "openInterest": "0",
                                               "volume24H": "0", "nextFundingRate": "-.0001"}}},
                      type="subscribed", sequence=1)
    patch, = dydx_patches({item.native_symbol: item}, initial)
    assert patch.index == 100 and patch.open_interest == 0 and patch.volume == 0
    assert patch.funding_kind == "predicted" and patch.funding_interval_hours == 1
    assert patch.source_at is None and patch.field_timestamps["funding_rate"] is None
    patch, = dydx_patches({item.native_symbol: item}, markets({"oraclePrices": {
        "BTC-USD": {"oraclePrice": "101", "effectiveAt": "2027-01-15T08:00:00.000Z",
                    "effectiveAtHeight": "123", "nextFundingRate": "9"},
    }}))
    assert patch.index == 101 and patch.source_at == EVENT
    assert patch.field_timestamps == {"index": EVENT}
    assert "funding_rate" not in patch.model_dump(exclude_unset=True)
    patch, = dydx_patches({item.native_symbol: item}, markets({"trading": {
        "BTC-USD": {"openInterest": "8", "effectiveAt": "2027-01-15T08:00:00.000Z", "oraclePrice": "9"},
    }}))
    assert patch.open_interest == 8 and patch.source_at is None
    assert "index" not in patch.model_dump(exclude_unset=True)


def test_dydx_empty_unknown_and_nonfinite_values_produce_no_patch():
    item = instrument("dydx", "BTC-USD")
    for contents in ({}, {"trading": {"ETH-USD": {"openInterest": "1"}}},
                     {"trading": {"BTC-USD": {"nextFundingRate": "Infinity", "volume24H": -1}}}):
        assert dydx_patches({item.native_symbol: item}, markets(contents)) == []
    with pytest.raises(TypeError, match="malformed"):
        dydx_patches({item.native_symbol: item}, markets({"trading": []}))


@pytest.mark.asyncio
async def test_hyperliquid_native_topics_keep_namespace_and_deduplicate_selection():
    item = instrument(symbol="xyz:BTC")
    socket = Socket(["Websocket connection established.",
                     {"channel": "subscriptionResponse", "data": {}}, bbo("xyz:BTC")])
    connect = connector([socket])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connect)
    generator = stream.stream([item, item])
    assert (await anext(generator)).instrument_id == item.id
    assert socket.sent == [{"method": "subscribe", "subscription": {"type": channel, "coin": "xyz:BTC"}}
                           for channel in ("bbo", "activeAssetCtx")]
    assert connect.calls == [ENDPOINTS["hyperliquid"]]
    await generator.aclose()
    assert socket.closed and not stream._running and not stream._tasks and stream._socket is None


@pytest.mark.asyncio
async def test_dydx_waits_for_connected_then_requests_one_all_market_channel():
    item = instrument("dydx", "BTC-USD")
    socket = Socket([{"type": "connected", "connection_id": "session"},
                     markets({"markets": {"BTC-USD": {"oraclePrice": "100"}, "ETH-USD": {"oraclePrice": "5"}}},
                             type="subscribed", sequence=1),
                     markets({"trading": {"BTC-USD": {"nextFundingRate": "0"}}})])
    stream = DexStreamAdapter(DydxAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([item])
    assert (await anext(generator)).index == 100
    assert (await anext(generator)).funding_rate == 0
    assert socket.sent == [{"type": "subscribe", "channel": "v4_markets", "batched": False}]
    await generator.aclose()


@pytest.mark.asyncio
async def test_disconnect_propagates_and_restart_uses_fresh_socket_and_subscriptions():
    item = instrument()
    first, second = Socket([ConnectionError("offline")]), Socket([bbo()])
    connect = connector([first, second])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connect)
    with pytest.raises(ConnectionError, match="offline"):
        await anext(stream.stream([item]))
    assert first.closed and not stream._running
    generator = stream.stream([item])
    assert (await anext(generator)).bid == 100
    assert first.sent == second.sent and len(connect.calls) == 2
    await generator.aclose()


@pytest.mark.asyncio
async def test_hyperliquid_json_heartbeat_pong_does_not_create_price_updates():
    socket = Socket()
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]),
                              heartbeat_seconds=.01, heartbeat_timeout=.2)
    generator = stream.stream([instrument()])
    task = asyncio.create_task(anext(generator))
    await asyncio.sleep(.05)
    assert not task.done() and {"method": "ping"} in socket.sent
    await socket.events.put(bbo())
    assert (await asyncio.wait_for(task, 1)).bid == 100
    await generator.aclose()


@pytest.mark.asyncio
async def test_missing_hyperliquid_pong_closes_and_raises_to_collector():
    socket = Socket(auto_pong=False)
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]),
                              heartbeat_seconds=.01, heartbeat_timeout=.01)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream.stream([instrument()])), 1)
    assert socket.closed and not stream._tasks and not stream._running


@pytest.mark.asyncio
async def test_subscription_replacement_requires_cancellation_and_fresh_session():
    first, second = Socket(), Socket([bbo("ETH")])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([first, second]))
    generator = stream.stream([instrument()])
    task = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="Cancel"):
        await anext(stream.stream([instrument(symbol="ETH")]))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert first.closed and not stream._running
    generator = stream.stream([instrument(symbol="ETH")])
    assert (await anext(generator)).instrument_id.endswith(":ETH")
    assert second.sent[0]["subscription"]["coin"] == "ETH"
    await generator.aclose()


@pytest.mark.asyncio
async def test_duplicate_and_old_hyperliquid_events_do_not_move_price_backwards():
    socket = Socket([bbo(), bbo(), bbo(time=EVENT - 1, bid="99"), bbo(time=EVENT + 1)])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([instrument()])
    assert (await anext(generator)).source_at == EVENT
    assert (await anext(generator)).source_at == EVENT + 1
    await generator.aclose()


@pytest.mark.asyncio
async def test_future_timestamp_cannot_poison_session_watermark():
    socket = Socket([bbo(time=EVENT + 40_000), bbo(time=EVENT)])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([instrument()])
    assert (await anext(generator)).source_at == EVENT + 40_000
    # Collector validation rejects the first patch; source session must still
    # deliver the subsequent correctly timed update for recovery.
    assert (await anext(generator)).source_at == EVENT
    await generator.aclose()


@pytest.mark.asyncio
async def test_dydx_replayed_sequence_and_old_oracle_time_are_ignored():
    socket = Socket([{"type": "connected", "connection_id": "session"},
                     markets({"markets": {}}, type="subscribed", sequence=1)])
    for sequence, price, time in ((2, "100", "2027-01-15T08:00:00Z"),
                                  (2, "99", "2027-01-15T08:00:01Z"),
                                  (3, "98", "2027-01-15T07:59:59Z"),
                                  (4, "101", "2027-01-15T08:00:01Z")):
        await socket.events.put(markets({"oraclePrices": {"BTC-USD": {"oraclePrice": price,
                                                                        "effectiveAt": time}}},
                                       sequence=sequence))
    stream = DexStreamAdapter(DydxAdapter(), connection_factory=connector([socket]))
    generator = stream.stream([instrument("dydx", "BTC-USD")])
    assert (await anext(generator)).index == 100
    assert (await anext(generator)).index == 101
    await generator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("payloads,exception", [
    ([{"type": "error"}], ConnectionError),
    ([{"type": "connected", "connection_id": "session"}, markets({})], ConnectionError),
    ([{"type": "connected", "connection_id": "session"},
      markets({}, type="unsubscribed")], ConnectionError),
    ([{"type": "connected", "connection_id": "session"},
      {**markets({}, type="subscribed"), "connection_id": "other"}], ConnectionError),
    (["not-json"], ValueError),
])
async def test_dydx_protocol_errors_close_source_without_fabricated_data(payloads, exception):
    socket = Socket(payloads)
    stream = DexStreamAdapter(DydxAdapter(), connection_factory=connector([socket]))
    with pytest.raises(exception):
        await anext(stream.stream([instrument("dydx", "BTC-USD")]))
    assert socket.closed and not stream._running


@pytest.mark.asyncio
async def test_dydx_handshake_timeout_is_bounded_and_cleans_up():
    socket = Socket()
    stream = DexStreamAdapter(DydxAdapter(), connection_factory=connector([socket]), handshake_timeout=.01)
    with pytest.raises(TimeoutError):
        await anext(stream.stream([instrument("dydx", "BTC-USD")]))
    assert socket.closed and not socket.sent


@pytest.mark.asyncio
async def test_raw_diagnostic_hook_receives_data_and_failure_does_not_stop_collection():
    socket = Socket([bbo(), bbo(time=EVENT + 1)])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connector([socket]))
    recorded = []

    async def hook(venue, transport, payload):
        recorded.append((venue, transport, payload))
        raise OSError("recording unavailable")

    stream.on_payload = hook
    generator = stream.stream([instrument()])
    assert (await anext(generator)).bid == 100
    assert (await anext(generator)).source_at == EVENT + 1
    assert recorded[0] == ("hyperliquid", "websocket:hyperliquid", bbo())
    await generator.aclose()


@pytest.mark.asyncio
async def test_identity_active_market_and_local_subscription_limit_validate_before_connect():
    connect = connector([])
    stream = DexStreamAdapter(HyperliquidAdapter(), connection_factory=connect)
    with pytest.raises(ValueError, match="discovered"):
        await anext(stream.stream([instrument("dydx", "BTC-USD")]))
    inactive = instrument().model_copy(update={"active": False})
    with pytest.raises(StreamUnavailable, match="use REST"):
        await anext(stream.stream([inactive]))
    assert stream.unsupported == [f"{inactive.id}:inactive"]
    with pytest.raises(StreamUnavailable, match="100 markets"):
        await anext(stream.stream([instrument(symbol=f"ASSET{i}") for i in range(101)]))
    assert not connect.calls


def test_factory_is_lazy_public_only_and_excludes_unsupported_dexes():
    assert create_dex_stream(object()) is None
    assert isinstance(create_dex_stream(LighterAdapter()), DexStreamAdapter)
    assert isinstance(create_dex_stream(HyperliquidAdapter()), DexStreamAdapter)
    assert isinstance(create_dex_stream(DydxAdapter()), DexStreamAdapter)
    with pytest.raises(TypeError):
        DexStreamAdapter(object())


def test_transport_uses_inherited_proxy_verified_tls_and_control_heartbeats(monkeypatch):
    import websockets.asyncio.client

    captured = []
    monkeypatch.setenv("WSS_PROXY", "http://test-proxy.invalid:8080")
    monkeypatch.setattr(websockets.asyncio.client, "connect", lambda *args, **kwargs: captured.append((args, kwargs)))
    _connection_factory(ENDPOINTS["dydx"])
    args, kwargs = captured[0]
    assert args == (ENDPOINTS["dydx"],)
    assert kwargs["proxy"] == "http://test-proxy.invalid:8080"
    assert kwargs["ssl"].check_hostname and kwargs["ssl"].verify_mode != 0
    assert kwargs["ping_interval"] == 20 and kwargs["ping_timeout"] == 10
    assert kwargs["max_queue"] == 16 and "additional_headers" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["hyperliquid", "dydx"])
async def test_real_local_socket_automatically_answers_server_control_ping(venue):
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    confirmed = asyncio.Event()
    subscribed = []

    async def handler(socket):
        if venue == "dydx":
            await socket.send(json.dumps({"type": "connected", "connection_id": "session"}))
        for _ in range(2 if venue == "hyperliquid" else 1):
            subscribed.append(json.loads(await socket.recv()))
        pong = await socket.ping(b"public-protocol-fixture")
        await asyncio.wait_for(pong, 1)
        confirmed.set()
        payload = (bbo() if venue == "hyperliquid" else
                   markets({"markets": {"BTC-USD": {"oraclePrice": "100"}}}, type="subscribed", sequence=1))
        await socket.send(json.dumps(payload))
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        # Loopback test only; production factory remains fixed to verified WSS.
        def factory(_):
            return connect(f"ws://127.0.0.1:{port}", proxy=None)
        rest = HyperliquidAdapter() if venue == "hyperliquid" else DydxAdapter()
        item = instrument() if venue == "hyperliquid" else instrument("dydx", "BTC-USD")
        stream = DexStreamAdapter(rest, connection_factory=factory)
        generator = stream.stream([item])
        patch = await asyncio.wait_for(anext(generator), 2)
        assert confirmed.is_set() and patch.instrument_id == item.id and subscribed
        await generator.aclose()
