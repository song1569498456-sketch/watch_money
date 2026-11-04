"""Settled-history fixtures; no external traffic or account credentials are used."""
import asyncio
import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from watch_money.adapters.cex import CexAdapter
from watch_money.funding_history import FundingHistoryService, router
from watch_money.models import Instrument

NOW = 1_800_000_000_000
EVENT = NOW - 86_400_000


class HistoryClient:
    def __init__(self):
        self.has = {"fetchFundingRateHistory": True}
        self.response = []
        self.calls = []
        self.error = None
        self.hang = False
        self.cancelled = False

    async def fetch_funding_rate_history(self, symbol, since, limit, params):
        self.calls.append((symbol, since, limit, deepcopy(params)))
        if self.error:
            raise self.error
        if self.hang:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True
        return deepcopy(self.response)


@pytest.fixture
def setup_history(tmp_path, monkeypatch):
    monkeypatch.setattr("watch_money.funding_history.now_ms", lambda: NOW)

    def make(client_id="binanceusdm", *, venue="binance", kind="perpetual", timeout=15):
        item = Instrument(id=f"cex:{venue}:{client_id}:{kind}:PEPEUSDT", venue=venue,
                          native_symbol="PEPE/USDT:USDT", asset_id="cex:PEPE", base="PEPE", quote="USDT",
                          kind=kind, settle="USDT")
        client = HistoryClient()
        adapter = CexAdapter(venue, client_factory=lambda _: client)
        adapter._markets[item.id] = (client_id, {"symbol": item.native_symbol,
                                               "info": {"fundingIntervalHours": 8}})
        collector = SimpleNamespace(instruments={item.id: item}, adapters={venue: adapter})
        service = FundingHistoryService(tmp_path, collector, timeout_seconds=timeout)
        return service, item, client, adapter

    return make


def entry(timestamp=EVENT, rate="0.0001", **updates):
    return {"timestamp": timestamp, "fundingRate": rate, **updates}


@pytest.mark.asyncio
async def test_default_is_local_only_and_persisted_records_survive_new_service(setup_history):
    service, item, client, _ = setup_history()
    assert (await service.get(item.id))["records"] == []
    assert not client.calls
    client.response = [entry()]
    refreshed = await service.get(item.id, refresh=True)
    assert refreshed["summary"]["refreshed"]
    replacement = FundingHistoryService(service.directory, service.collector)
    cached = await replacement.get(item.id)
    assert len(cached["records"]) == 1 and len(client.calls) == 1
    assert cached["summary"]["pages"] == 0 and not cached["summary"]["refreshed"]
    assert cached["coverage"]["kind"] == "exchange_settled_funding_history"
    assert cached["coverage"]["all_history"] is False


@pytest.mark.asyncio
async def test_settled_positive_negative_zero_signs_and_unknown_historical_period(setup_history):
    service, item, client, _ = setup_history()
    client.response = [entry(EVENT, "0.0001"), entry(EVENT + 1000, "-0.0002"), entry(EVENT + 2000, "0")]
    result = await service.get(item.id, refresh=True)
    records = result["records"]
    assert [row["funding_rate"] for row in records] == [0.0001, -0.0002, 0]
    assert [row["long_cashflow_sign"] for row in records] == [-1, 1, 0]
    assert [row["short_cashflow_sign"] for row in records] == [1, -1, 0]
    assert all(row["funding_kind"] == "settled" and row["funding_interval_hours"] is None for row in records)
    assert all(row["settlement_at"] < row["received_at"] == NOW for row in records)
    assert records[0]["raw_funding_rate"] == "0.0001"
    assert "fetch_funding_rate_history" in records[0]["source"]
    assert client.calls[0][3] == {"paginate": False, "subType": "linear"}


@pytest.mark.asyncio
async def test_dedup_and_revised_rates_keep_audit_and_first_observation(setup_history, monkeypatch):
    service, item, client, _ = setup_history()
    client.response = [entry(), entry()]
    assert len((await service.get(item.id, refresh=True))["records"]) == 1
    monkeypatch.setattr("watch_money.funding_history.now_ms", lambda: NOW + 1000)
    duplicate = (await service.get(item.id, refresh=True))["records"][0]
    assert duplicate["revision"] == 1 and duplicate["received_at"] == NOW
    assert duplicate["last_seen_at"] == NOW + 1000
    client.response = [entry(rate="0.0003")]
    revised = (await service.get(item.id, refresh=True))["records"][0]
    assert revised["revision"] == 2 and revised["first_received_at"] == NOW
    assert revised["received_at"] == NOW + 1000 and revised["funding_rate"] == 0.0003
    with sqlite3.connect(service.path) as db:
        old = db.execute("SELECT previous_record FROM funding_revisions").fetchall()
    assert len(old) == 1 and json.loads(old[0][0])["funding_rate"] == 0.0001


@pytest.mark.asyncio
async def test_same_timestamp_special_and_regular_events_are_not_mutual_revisions(setup_history):
    service, item, client, _ = setup_history()
    client.response = [entry(info={"fundingRate": "0.0001", "rateType": "Regular"}),
                       entry(rate="0.0002", info={"fundingRate": "0.0002", "rateType": "Special"})]
    records = (await service.get(item.id, refresh=True))["records"]
    assert len(records) == 2 and {row["event_type"] for row in records} == {"regular", "special"}
    assert all(row["revision"] == 1 for row in records)


@pytest.mark.asyncio
async def test_rejects_missing_timestamp_predictions_future_wrong_market_and_nonfinite(setup_history):
    service, item, client, _ = setup_history()
    client.response = [entry(None), entry(NOW + 1000), entry(EVENT, "NaN"),
                       entry(EVENT, "0.01", symbol="OTHER/USDT"),
                       {"timestamp": EVENT, "nextFundingRate": "0.01"}, entry(12345)]
    result = await service.get(item.id, refresh=True)
    assert result["records"] == [] and result["summary"]["rejected_count"] == 6
    assert any("missing_settlement_timestamp" in issue for issue in result["summary"]["issues"])


@pytest.mark.asyncio
async def test_okx_only_actual_realized_rate_is_accepted(setup_history):
    service, item, client, _ = setup_history("okx", venue="okx")
    client.response = [entry(EVENT, "0.009", info={"fundingRate": "0.009", "realizedRate": "0.0002"}),
                       entry(EVENT + 1000, "0.009", info={"fundingRate": "0.009"})]
    result = await service.get(item.id, refresh=True)
    assert len(result["records"]) == 1
    assert result["records"][0]["funding_rate"] == 0.0002
    assert result["records"][0]["raw_funding_rate"] == "0.0002"
    assert result["summary"]["rejected_count"] == 1


@pytest.mark.asyncio
async def test_mexc_only_history_specific_period_can_fill_period(setup_history):
    service, item, client, _ = setup_history("mexc", venue="mexc")
    client.response = [entry(info={"fundingRate": "0.0001", "collectCycle": "4"}),
                       entry(EVENT + 1000, info={"fundingRate": "0.0001"})]
    records = (await service.get(item.id, refresh=True))["records"]
    assert records[0]["funding_interval_hours"] == 4
    assert records[0]["period_source"] == "history.info.collectCycle"
    assert records[1]["funding_interval_hours"] is None


@pytest.mark.asyncio
async def test_one_page_endpoint_cap_and_cursor_no_endless_loop(setup_history):
    service, item, client, _ = setup_history("bybit", venue="bybit")
    client.response = [entry(EVENT)] * 200
    first = await service.get(item.id, refresh=True, since=EVENT, limit=1000)
    assert len(client.calls) == 1 and client.calls[0][2] == 200
    assert first["summary"]["truncated"] and first["summary"]["has_more"] is None
    assert first["summary"]["next_since"] == EVENT + 1
    second = await service.get(item.id, refresh=True, since=EVENT + 1, limit=1000)
    assert len(client.calls) == 2 and second["summary"]["next_since"] is None
    assert "no_forward_cursor_progress" in second["summary"]["issues"]
    assert second["records"] == []


@pytest.mark.asyncio
async def test_local_limit_since_and_cache_range_are_explicit(setup_history):
    service, item, client, _ = setup_history()
    client.response = [entry(EVENT + i * 1000) for i in range(5)]
    await service.get(item.id, refresh=True)
    latest = await service.get(item.id, limit=2)
    assert [row["settlement_at"] for row in latest["records"]] == [EVENT + 3000, EVENT + 4000]
    assert latest["summary"]["cached_matching_count"] == 5 and latest["summary"]["truncated"]
    early = await service.get(item.id, since=EVENT + 1000, limit=2)
    assert [row["settlement_at"] for row in early["records"]] == [EVENT + 1000, EVENT + 2000]
    assert len(client.calls) == 1


@pytest.mark.parametrize("client_id,venue", [("deribit", "deribit"), ("krakenfutures", "kraken")])
@pytest.mark.asyncio
async def test_unreviewed_settlement_semantics_are_501_without_requests(setup_history, client_id, venue):
    service, item, client, _ = setup_history(client_id, venue=venue)
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True)
    assert raised.value.status_code == 501 and not client.calls


@pytest.mark.asyncio
async def test_missing_capability_emulation_and_spot_are_501(setup_history):
    service, item, client, _ = setup_history()
    client.has["fetchFundingRateHistory"] = "emulated"
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True)
    assert raised.value.status_code == 501 and not client.calls
    service, item, client, _ = setup_history(kind="spot")
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True)
    assert raised.value.status_code == 501 and not client.calls


@pytest.mark.asyncio
async def test_credentialed_client_is_not_used_or_modified(setup_history):
    service, item, client, _ = setup_history()
    client.apiKey = "private-key-must-not-be-sent"
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True)
    assert raised.value.status_code == 409
    assert "private-key" not in str(raised.value.detail)
    assert client.apiKey == "private-key-must-not-be-sent" and not client.calls


@pytest.mark.asyncio
async def test_auth_rejection_is_sanitized_and_no_credentials_requested(setup_history):
    class AuthenticationError(Exception):
        pass

    service, item, client, _ = setup_history()
    client.error = AuthenticationError("401 https://username:secret@proxy/ token=secret")
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True)
    assert raised.value.status_code == 401
    assert "secret" not in str(raised.value.detail) and "https://" not in str(raised.value.detail)
    assert not hasattr(client, "apiKey")


@pytest.mark.asyncio
async def test_timeout_cancels_request_and_returns_504(setup_history):
    service, item, client, _ = setup_history(timeout=0.01)
    client.hang = True
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True)
    assert raised.value.status_code == 504 and client.cancelled


@pytest.mark.asyncio
async def test_concurrent_manual_refreshes_deduplicate_transactionally(setup_history):
    service, item, client, _ = setup_history()
    client.response = [entry()]
    results = await asyncio.gather(*(service.get(item.id, refresh=True) for _ in range(5)))
    assert all(len(result["records"]) == 1 for result in results)
    assert len((await service.get(item.id))["records"]) == 1


@pytest.mark.asyncio
async def test_api_default_cache_refresh_bounds_and_unknown_instrument(setup_history):
    service, item, client, _ = setup_history()
    app = FastAPI()
    app.include_router(router)
    app.state.funding_history = service
    client.response = [entry()]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        path = f"/api/funding/history/{item.id}"
        assert (await http.get(path)).status_code == 200 and not client.calls
        response = await http.get(path, params={"refresh": "true", "limit": 100})
        assert response.status_code == 200 and len(response.json()["records"]) == 1
        assert (await http.get(path, params={"limit": 1001})).status_code == 422
        assert (await http.get(path, params={"since": -1})).status_code == 422
        assert (await http.get(path, params={"since": 2**70})).status_code == 422
        assert (await http.get("/api/funding/history/unknown")).status_code == 404


@pytest.mark.asyncio
async def test_api_401_never_echoes_proxy_url_or_credentials(setup_history):
    class AuthenticationError(Exception):
        pass

    service, item, client, _ = setup_history()
    client.error = AuthenticationError("HTTP 401 https://user:super-secret@proxy.invalid/private token=super-secret")
    app = FastAPI()
    app.include_router(router)
    app.state.funding_history = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(f"/api/funding/history/{item.id}", params={"refresh": "true"})
        assert response.status_code == 401
        assert "super-secret" not in response.text and "proxy.invalid" not in response.text
        assert "no account credentials were requested" in response.text


@pytest.mark.parametrize("client_id", ["okx", "bybit"])
@pytest.mark.asyncio
async def test_installed_ccxt_history_uses_public_endpoint_and_actual_settlement_parser(setup_history, client_id):
    ccxt = pytest.importorskip("ccxt.async_support")
    service, item, _fake_client, adapter = setup_history(client_id, venue=client_id)
    client = getattr(ccxt, client_id)()
    market = {"id": "PEPE-USDT-SWAP" if client_id == "okx" else "PEPEUSDT",
              "symbol": item.native_symbol, "base": "PEPE", "quote": "USDT", "settle": "USDT",
              "type": "swap", "spot": False, "swap": True, "contract": True,
              "linear": True, "inverse": False, "contractSize": 1, "info": {"fundingInterval": "480"}}
    client.set_markets([market])
    adapter._clients[client_id] = client
    requests = []

    async def public_endpoint(request):
        requests.append(deepcopy(request))
        if client_id == "okx":
            return {"code": "0", "data": [{"instId": market["id"], "fundingTime": str(EVENT),
                                              "fundingRate": "0.009", "realizedRate": "0.0002"}]}
        return {"retCode": 0, "result": {"list": [{"symbol": market["id"],
                                                  "fundingRateTimestamp": str(EVENT), "fundingRate": "0.0002"}]}}

    endpoint = "publicGetPublicFundingRateHistory" if client_id == "okx" else "publicGetV5MarketFundingHistory"
    setattr(client, endpoint, public_endpoint)
    try:
        result = await service.get(item.id, refresh=True)
        assert len(result["records"]) == len(requests) == 1
        assert result["records"][0]["funding_rate"] == 0.0002
        assert result["records"][0]["settlement_at"] == EVENT
        assert result["records"][0]["funding_interval_hours"] is None
        assert "paginate" not in requests[0]
        assert not client.apiKey and not client.secret
    finally:
        await client.close()


@pytest.mark.parametrize("limit", [0, 1001, True, 1.5])
@pytest.mark.asyncio
async def test_service_limits_reject_before_network(setup_history, limit):
    service, item, client, _ = setup_history()
    with pytest.raises(HTTPException) as raised:
        await service.get(item.id, refresh=True, limit=limit)
    assert raised.value.status_code == 422 and not client.calls


def configure_reviewed_history(setup_history, venue, settle='USDT'):
    service, item, client, adapter = setup_history(venue, venue=venue)
    item = item.model_copy(update={'settle': settle, 'base': 'BTC' if settle == 'BTC' else 'PEPE',
                                  'native_symbol': f'PEPE/USDT:{settle}'})
    service.collector.instruments[item.id] = item
    market = {'id': 'PEPE_USDT' if venue == 'gate' else 'PEPEUSDT',
              'symbol': item.native_symbol, 'swap': True, 'settle': settle}
    adapter._markets[item.id] = (venue, market)
    return service, item, client, adapter, market


@pytest.mark.parametrize('venue,settle', [('gate', 'USDT'), ('gate', 'BTC'), ('gate', 'USD1'),
                                        ('bitget', 'USDT'), ('bitget', 'USDC'), ('bitget', 'BTC')])
async def test_new_public_history_native_units_signs_and_scope(setup_history, venue, settle):
    service, item, client, _, market = configure_reviewed_history(setup_history, venue, settle)
    client.response = [entry(EVENT + i * 1000, rate, info=(
        {'t': (EVENT + i * 1000) // 1000, 'r': rate} if venue == 'gate' else
        {'fundingTime': str(EVENT + i * 1000), 'fundingRate': rate, 'symbol': market['id']}))
        for i, rate in enumerate(['0.0001', '-0.0002', '0'])]
    report = await service.get(item.id, refresh=True, limit=1000, since=EVENT)
    assert [r['funding_rate'] for r in report['records']] == [.0001, -.0002, 0]
    assert [r['long_cashflow_sign'] for r in report['records']] == [-1, 1, 0]
    assert all(r['funding_interval_hours'] is None and r['settle'] == settle for r in report['records'])
    assert all(r['native_market_id'] == market['id'] and r['rate_unit'] == 'decimal_ratio_per_settlement'
               for r in report['records'])
    assert client.calls[0][2] == 100 and client.calls[0][3]['paginate'] is False
    if venue == 'bitget':
        assert client.calls[0][3] == {'paginate': False, 'uta': False, 'pageNo': 1}
        assert report['summary']['next_since'] is None
        assert any('latest page 1' in issue for issue in report['summary']['issues'])
    else:
        assert client.calls[0][3]['settle'] == settle.lower()


@pytest.mark.parametrize('venue', ['gate', 'bitget'])
async def test_new_history_rejects_wrong_native_clocks_rates_and_scope(setup_history, venue):
    service, item, client, _, market = configure_reviewed_history(setup_history, venue)
    raw = {'t': EVENT // 1000, 'r': '.0001'} if venue == 'gate' else {
        'fundingTime': str(EVENT), 'fundingRate': '.0001', 'symbol': market['id']}
    client.response = [entry(info={}), entry(EVENT + 1, info=raw),
                       entry(info=raw | ({'r': 'NaN'} if venue == 'gate' else {'fundingRate': 'NaN'}))]
    result = await service.get(item.id, refresh=True)
    assert result['records'] == [] and result['summary']['rejected_count'] == 3
    if venue == 'bitget':
        client.response = [entry(info=raw | {'symbol': 'OTHERUSDT'})]
        assert (await service.get(item.id, refresh=True))['records'] == []
    market['settle'] = 'OTHER'
    calls = len(client.calls)
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True)
    assert error.value.status_code == 409 and len(client.calls) == calls


@pytest.mark.parametrize('venue', ['gate', 'bitget'])
async def test_installed_ccxt_public_history_routing_and_clock_without_network(venue):
    import ccxt.async_support as ccxt
    client = getattr(ccxt, venue)()
    symbol = 'PEPE/USDT:USDT'
    market = {'id': 'PEPE_USDT' if venue == 'gate' else 'PEPEUSDT', 'symbol': symbol,
              'swap': True, 'spot': False, 'contract': True, 'option': False, 'type': 'swap',
              'settle': 'USDT', 'settleId': 'usdt', 'base': 'PEPE', 'quote': 'USDT',
              'linear': True, 'inverse': False}
    client.markets = {symbol: market}
    client.markets_by_id = {market['id']: [market]}
    calls = []
    async def public(request):
        calls.append(request)
        if venue == 'gate':
            return [{'t': EVENT // 1000, 'r': '-0.0002'}]
        return {'code': '00000', 'data': [{'symbol': market['id'], 'fundingTime': str(EVENT),
                                          'fundingRate': '-0.0002'}]}
    async def forbidden(*args, **kwargs):
        pytest.fail('unexpected transport or authenticated endpoint')
    client.fetch = forbidden
    if venue == 'gate':
        client.publicFuturesGetSettleFundingRate = public
        params = {'paginate': False, 'settle': 'usdt'}
    else:
        client.publicMixGetV2MixMarketHistoryFundRate = public
        params = {'paginate': False, 'uta': False, 'pageNo': 1}
    try:
        rows = await client.fetch_funding_rate_history(symbol, EVENT, 100, params)
        assert rows[0]['timestamp'] == EVENT and rows[0]['fundingRate'] == -.0002
        assert len(calls) == 1
        if venue == 'gate':
            assert calls[0]['contract'] == market['id'] and calls[0]['settle'] == 'usdt'
            assert calls[0]['from'] == EVENT // 1000
        else:
            assert calls[0]['productType'] == 'USDT-FUTURES' and calls[0]['pageNo'] == 1
    finally:
        await client.close()


def htx_inverse_history(setup_history):
    service, item, client, adapter = setup_history('htx', venue='htx')
    item = item.model_copy(update={'base': 'BTC', 'quote': 'USD', 'settle': 'BTC',
                                  'linear': False, 'native_symbol': 'BTC/USD:BTC'})
    service.collector.instruments[item.id] = item
    market = {'id': 'BTC-USD', 'symbol': item.native_symbol, 'swap': True, 'linear': False,
              'inverse': True, 'settle': 'BTC', 'base': 'BTC', 'quote': 'USD', 'type': 'swap',
              'spot': False, 'contract': True}
    adapter._markets[item.id] = ('htx', market)
    return service, item, client, adapter, market


def htx_raw(**changes):
    return {'contract_code': 'BTC-USD', 'symbol': 'BTC', 'fee_asset': 'BTC',
            'funding_time': str(EVENT), 'funding_rate': '.009', 'realized_rate': '.0001'} | changes


@pytest.mark.parametrize('rate,sign', [('.0001', -1), ('-.0002', 1), ('0', 0)])
async def test_htx_inverse_uses_realized_not_advertised_rate(setup_history, rate, sign):
    service, item, client, _, _ = htx_inverse_history(setup_history)
    client.response = [entry(rate='.009', info=htx_raw(realized_rate=rate))]
    result = await service.get(item.id, refresh=True, since=EVENT, limit=1000)
    record = result['records'][0]
    assert record['raw_funding_rate'] == rate and record['funding_rate'] == float(rate)
    assert record['long_cashflow_sign'] == sign
    assert record['advertised_funding_rate'] == '.009' and record['rate_field'] == 'realized_rate'
    assert record['native_time_unit'] == 'milliseconds' and record['settle'] == 'BTC'
    assert record['funding_interval_hours'] is None and result['summary']['next_since'] is None
    assert client.calls[0][2:] == (50, {'paginate': False, 'page_index': 1})


@pytest.mark.parametrize('changes', [{'realized_rate': None}, {'realized_rate': 'NaN'},
    {'funding_time': str(EVENT // 1000)}, {'funding_time': str(EVENT + 1)},
    {'fee_asset': 'ETH'}, {'symbol': 'OTHER'}, {'contract_code': 'OTHER-USD'}])
async def test_htx_inverse_missing_realized_or_native_mismatch_is_not_settled(setup_history, changes):
    service, item, client, _, _ = htx_inverse_history(setup_history)
    client.response = [entry(info=htx_raw(**changes))]
    result = await service.get(item.id, refresh=True)
    assert result['records'] == [] and result['summary']['rejected_count'] == 1


async def test_htx_linear_and_credentials_rejected_without_requests(setup_history):
    service, item, client, _, market = htx_inverse_history(setup_history)
    market.update(linear=True, inverse=False)
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True)
    assert error.value.status_code == 501 and not client.calls
    market.update(linear=False, inverse=True)
    client.apiKey = 'synthetic-marker'
    with pytest.raises(HTTPException) as error:
        await service.get(item.id, refresh=True)
    assert error.value.status_code == 409 and not client.calls


async def test_htx_installed_parser_routes_inverse_public_but_returns_advertised_rate(setup_history):
    import ccxt.async_support as ccxt
    service, item, _, adapter, market = htx_inverse_history(setup_history)
    client = ccxt.htx()
    client.markets = {item.native_symbol: market}
    client.markets_by_id = {market['id']: [market]}
    calls = []
    async def public(params):
        calls.append(params)
        return {'status': 'ok', 'data': {'current_page': 1, 'data': [htx_raw()]}}
    async def forbidden(*args, **kwargs):
        pytest.fail('unexpected network or linear endpoint')
    client.fetch = forbidden
    client.contractPublicGetV5MarketFundingRateHistory = forbidden
    client.contractPublicGetSwapApiV1SwapHistoricalFundingRate = public
    adapter._clients['htx'] = client
    try:
        report = await service.get(item.id, refresh=True)
        assert report['records'][0]['funding_rate'] == .0001
        assert calls == [{'contract_code': 'BTC-USD', 'page_size': 50, 'page_index': 1}]
    finally:
        await adapter.close()
