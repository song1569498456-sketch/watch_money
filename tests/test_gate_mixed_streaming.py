"""Public mixed Gate namespace simulation; no live acceptance or private access."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from watch_money.adapters.gate_futures import ENDPOINTS
from watch_money.adapters.gate_streaming import ENDPOINT, GateFuturesStreamAdapter, GateStreamAdapter, _connection_factory
from watch_money.models import Instrument
from test_gate_streaming import AT, Socket, frame, item


def perp(settle='USDT', multiplier=.001):
    return Instrument(id=f'gate:perp:{settle}', venue='gate', native_symbol=f'SMALL/{settle}:{settle}',
                      asset_id='SMALL', base='SMALL', quote=settle, settle=settle,
                      kind='perpetual', contract_size=multiplier)


def rest():
    def resolve(instrument):
        return 'gate', {'id': 'SMALL_USDT' if instrument.kind == 'perpetual' else 'PEPE_USDT',
                        'settle': instrument.settle, 'swap': instrument.kind == 'perpetual'}
    return SimpleNamespace(_resolve=resolve)


def book(**updates):
    value = frame('SMALL', **({'u': '5'} | updates))
    value['channel'] = 'futures.book_ticker'
    return value


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('watch_money.adapters.gate_streaming.now_ms', lambda: AT + 1000)


class FuturesSocket(Socket):
    async def send(self, raw):
        await super().send(raw)
        value = json.loads(raw)
        if value['channel'] == 'futures.ping' and self.auto_pong:
            self.incoming.put_nowait({'channel': 'futures.pong'})


def factory(sockets):
    @asynccontextmanager
    async def connect(endpoint):
        socket = sockets[endpoint]
        try:
            yield socket
        finally:
            socket.closed = True
    return connect


@pytest.mark.asyncio
async def test_four_namespaces_same_contract_remain_separate_and_prices_unscaled():
    sockets = {ENDPOINT: Socket([frame()])}
    sockets.update({endpoint: FuturesSocket([book(b=str(110 + index), a=str(111 + index))])
                    for index, endpoint in enumerate(ENDPOINTS.values())})
    adapter = GateStreamAdapter(rest(), connection_factory=factory(sockets))
    stream = adapter.stream([item(), perp('BTC', 1000), perp('USDT', .001), perp('USD1', 1)])
    patches = [await asyncio.wait_for(anext(stream), 1) for _ in range(4)]
    assert len({patch.instrument_id for patch in patches}) == 4
    assert {patch.bid for patch in patches} == {100, 110, 111, 112}
    assert all(patch.source_at == AT for patch in patches)
    assert {patch.source.split(':')[2] for patch in patches if 'futures' in patch.source} == {'btc', 'usdt', 'usd1'}
    assert all(patch.funding_rate is None for patch in patches)
    assert adapter.health_state() == 'online'
    diag = adapter.diagnostics()
    assert diag['namespace_count'] == 4 and diag['unique_accepted_markets'] == 4
    await stream.aclose()
    assert all(socket.closed for socket in sockets.values())


@pytest.mark.asyncio
async def test_futures_empty_side_clear_same_time_barrier_and_next_event_recovery():
    socket = FuturesSocket([book(b='', B='0')])
    adapter = GateFuturesStreamAdapter(rest(), settle='usdt', connection_factory=factory({ENDPOINTS['usdt']: socket}))
    stream = adapter.stream([perp()])
    patch = await anext(stream)
    assert patch.bid is None and patch.cleared_fields == ['bid']
    socket.incoming.put_nowait(book(u='6', a='102'))
    assert (await anext(stream)).bid is None
    socket.incoming.put_nowait(book(u='7', t=AT + 1, B='.5'))
    assert (await anext(stream)).bid == 100
    await stream.aclose()


def test_strict_metadata_scope_and_deterministic_selection_budgets():
    adapter = GateStreamAdapter(rest())
    assert adapter.supports_instrument(perp())
    assert not adapter.supports_instrument(perp('USDC'))
    bad = SimpleNamespace(_resolve=lambda instrument: ('gate', {'id': 'SMALL_USDT', 'settle': 'BTC', 'swap': True}))
    assert not GateFuturesStreamAdapter(bad, settle='usdt').supports_instrument(perp())
    bad = SimpleNamespace(_resolve=lambda instrument: ('gate', {'id': 'SMALL_USDT', 'settle': 'USDT', 'swap': False}))
    assert not GateFuturesStreamAdapter(bad, settle='usdt').supports_instrument(perp())
    adapter.namespace_limit = 2
    adapter.max_catalog_markets = 3
    catalog = [item().model_copy(update={'id': f'spot:{n}'}) for n in range(5)] + [perp()]
    selected = adapter.select_instruments(reversed(catalog))
    assert len(selected) == 3
    assert sum(value.kind == 'spot' for value in selected) == 2
    assert selected == adapter.select_instruments(catalog)


@pytest.mark.asyncio
async def test_mutable_catalog_cannot_rebind_running_futures_parser():
    source = rest()
    socket = FuturesSocket([book()])
    adapter = GateFuturesStreamAdapter(source, settle='usdt', connection_factory=factory({ENDPOINTS['usdt']: socket}))
    stream = adapter.stream([perp()])
    await anext(stream)
    source._resolve = lambda instrument: (_ for _ in ()).throw(AssertionError('No midstream catalog access'))
    socket.incoming.put_nowait(book(u='6', t=AT + 1))
    assert (await anext(stream)).source == 'gate:futures.book_ticker:usdt:SMALL_USDT'
    await stream.aclose()


@pytest.mark.asyncio
async def test_futures_failure_backoff_reconnect_does_not_restart_healthy_spot():
    spot = Socket([frame()])
    failed = FuturesSocket([ConnectionError('synthetic failure')])
    recovered = FuturesSocket([book()])
    attempts = {ENDPOINT: 0, ENDPOINTS['usdt']: 0}
    @asynccontextmanager
    async def connect(endpoint):
        attempts[endpoint] += 1
        socket = spot if endpoint == ENDPOINT else failed if attempts[endpoint] == 1 else recovered
        try:
            yield socket
        finally:
            socket.closed = True
    adapter = GateStreamAdapter(rest(), connection_factory=connect, retry_initial=.05, retry_max=.1)
    stream = adapter.stream([item(), perp()])
    assert (await anext(stream)).instrument_id == item().id
    await asyncio.sleep(.01)
    assert adapter.health_state() == 'partial'
    assert adapter.diagnostics()['backoff_namespaces'] == 1
    assert attempts[ENDPOINTS['usdt']] == 1
    spot.incoming.put_nowait(frame(t=AT + 1, u=6))
    assert (await anext(stream)).instrument_id == item().id
    assert (await asyncio.wait_for(anext(stream), 1)).instrument_id == perp().id
    diag = adapter.diagnostics()
    assert attempts[ENDPOINT] == 1 and attempts[ENDPOINTS['usdt']] == 2
    assert diag['streams']['futures:usdt']['generation'] == 2
    assert diag['streams']['futures:usdt']['reconnects'] == 1
    assert adapter.health_state() == 'online'
    await stream.aclose()
    assert spot.closed and recovered.closed


@pytest.mark.asyncio
async def test_composite_pressure_preserves_clear_and_original_receipt():
    socket = FuturesSocket([book(b='', B='0')] + [book(u=str(n + 6)) for n in range(200)])
    adapter = GateStreamAdapter(rest(), connection_factory=factory({ENDPOINTS['usdt']: socket}))
    stream = adapter.stream([perp()])
    patch = await anext(stream)
    assert patch.bid is None
    assert patch.field_received_at['bid'] == AT + 1000
    assert len(adapter._inbox.pending) <= 1
    await stream.aclose()


@pytest.mark.asyncio
async def test_repeated_cancel_during_child_close_retains_ownership():
    entered, release = asyncio.Event(), asyncio.Event()
    socket = FuturesSocket()
    @asynccontextmanager
    async def connect(endpoint):
        try:
            yield socket
        finally:
            entered.set()
            await release.wait()
            socket.closed = True
    adapter = GateStreamAdapter(rest(), connection_factory=connect)
    pending = asyncio.create_task(anext(adapter.stream([perp()])))
    await asyncio.sleep(.01)
    pending.cancel()
    await entered.wait()
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done() and not socket.closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert socket.closed and not adapter._tasks and not adapter._running


def test_public_decimal_header_only_for_reviewed_futures(monkeypatch):
    calls = []
    def connect(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return object()
    monkeypatch.setattr('watch_money.adapters.dex_streaming._connection_factory', connect)
    _connection_factory(ENDPOINT)
    _connection_factory(ENDPOINTS['usd1'])
    assert calls[0][1] == {}
    assert calls[1][1] == {'additional_headers': {'X-Gate-Size-Decimal': '1'}}


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [401, 403, 451])
async def test_explicit_access_rejection_is_terminal_for_session_healthy_spot_continues(code):
    class AccessRejected(Exception):
        response = SimpleNamespace(status_code=code)
    attempts = []
    spot = Socket([frame()])
    @asynccontextmanager
    async def connect(endpoint):
        attempts.append(endpoint)
        if endpoint != ENDPOINT:
            raise AccessRejected('Do not include or retry restricted response')
        try:
            yield spot
        finally:
            spot.closed = True
    adapter = GateStreamAdapter(rest(), connection_factory=connect, retry_initial=.01, retry_max=.02)
    stream = adapter.stream([item(), perp()])
    assert (await anext(stream)).instrument_id == item().id
    await asyncio.sleep(.05)
    assert attempts.count(ENDPOINTS['usdt']) == 1
    assert adapter.health_state() == 'partial'
    diag = adapter.diagnostics()
    assert diag['blocked_namespaces'] == 1
    state = diag['streams']['futures:usdt']
    assert state['blocked_http_status'] == code and state['next_retry_at'] is None
    assert state['reconnects'] == 0
    assert 'restricted response' not in json.dumps(diag)
    spot.incoming.put_nowait(frame(u=6, t=AT + 1))
    assert (await anext(stream)).source_at == AT + 1
    await stream.aclose()
    assert spot.closed


@pytest.mark.asyncio
async def test_observer_tags_preserve_spot_and_disambiguate_futures_settlement():
    sockets = {ENDPOINT: Socket([frame()]), ENDPOINTS['usdt']: FuturesSocket([book()])}
    adapter = GateStreamAdapter(rest(), connection_factory=factory(sockets))
    tags = []
    async def observe(venue, tag, message):
        tags.append(tag)
    adapter.on_payload = observe
    stream = adapter.stream([item(), perp()])
    await anext(stream)
    await anext(stream)
    await asyncio.sleep(.01)
    assert set(tags) == {'websocket:gate:spot.book_ticker', 'websocket:gate:futures.book_ticker:usdt'}
    await stream.aclose()


@pytest.mark.asyncio
async def test_direct_oversized_namespace_rejected_not_silently_dropped():
    catalog = SimpleNamespace(_resolve=lambda value: ('gate', {'id': value.native_symbol.replace('/', '_')}))
    adapter = GateStreamAdapter(catalog)
    adapter.namespace_limit = 1
    with pytest.raises(RuntimeError, match='budget'):
        await anext(adapter.stream([item(), item('OTHER')]))
