"""Temporary SQLite and official-shaped HTTP fixtures; no live coverage claims."""
import asyncio

import httpx
import pytest
from test_identity_batch import ready as _ready
from test_asset_verification import env as _env, gate_response, bitget_response, OTHER_ADDRESS
from watch_money.identity_batch import build_plan, apply_plan
from watch_money.identity_catalog import CoinGeckoCatalog, CatalogUnavailable, IdentityCatalogService, candidates, DAY
from watch_money.storage import Storage

ready = _ready
env = _env


def ticker(coin='small', venue='gate'):
    return {'base': 'PEPE', 'target': 'USDT', 'market': {'identifier': venue}, 'coin_id': coin,
            'target_coin_id': 'tether', 'is_anomaly': False, 'is_stale': False}


def snap(ready, *, platforms=None):
    address = next(r['canonical_asset_id'].split(':', 1)[1] for r in ready.service._records() if r['canonical_asset_id'])
    return {'coins': [{'id': 'small', 'platforms': platforms or {'ethereum': address}}],
        'coins_at': ready.clock(), 'exchanges': {v: [ticker(venue=v)] for v in ['gate', 'bitget']},
        'sources': {v: {'complete': True, 'observed_at': ready.clock(), 'pages': 1, 'rows': 1} for v in ['gate', 'bitget']}, 'complete': True}


async def no_sleep(seconds):
    pass


async def test_missing_key_makes_no_request(tmp_path):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))) as client:
        provider = CoinGeckoCatalog(Storage(tmp_path), client, key='')
        result = await provider.snapshot(['gate'])
    assert result['error'] == 'demo_key_required' and not calls


async def test_real_headers_params_cache_restart_and_no_persisted_key(tmp_path):
    store, calls, now = Storage(tmp_path), [], [100000]
    def handler(request):
        calls.append(request)
        assert request.headers['x-cg-demo-api-key'] == 'test-not-real-secret'
        assert 'api_key' not in str(request.url)
        if request.url.path.endswith('/coins/list'):
            assert request.url.params['include_platform'] == 'true'
            return httpx.Response(200, json=[{'id': 'small', 'platforms': {'ethereum': OTHER_ADDRESS}}])
        assert request.url.params['order'] == 'base_target'
        return httpx.Response(200, json={'tickers': [ticker()]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CoinGeckoCatalog(store, client, key='test-not-real-secret', clock=lambda: now[0], sleep=no_sleep)
        first = await provider.snapshot(['gate'])
        second = await CoinGeckoCatalog(store, client, key='', clock=lambda: now[0]).snapshot(['gate'])
        assert first['complete'] and second['complete'] and len(calls) == 2
        now[0] += DAY+1
        assert (await CoinGeckoCatalog(store, client, key='', clock=lambda: now[0]).snapshot(['gate']))['error'] == 'demo_key_required'
    assert 'test-not-real-secret' not in store.db_path.read_bytes().decode(errors='ignore')
    assert store.get('cg:budget')['used'] == 2


@pytest.mark.parametrize('code, reason', [(401, 'access_denied_requires_configuration_review'), (403, 'access_denied_requires_configuration_review'), (429, 'rate_limit_cooldown')])
async def test_denial_and_rate_limit_do_not_retry_or_switch_host(tmp_path, code, reason):
    calls = []
    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(code)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = Storage(tmp_path)
        provider = CoinGeckoCatalog(store, client, key='fixture', clock=lambda: 100000)
        assert (await provider.snapshot(['gate']))['error'] == reason
        assert (await provider.snapshot(['gate']))['error'] == reason
    assert len(calls) == 1 and store.get('cg:budget')['used'] == 1


async def test_full_page_cap_repetition_and_conflicting_rows_retained(tmp_path):
    count = 0
    def handler(r):
        nonlocal count
        if r.url.path.endswith('/coins/list'):
            return httpx.Response(200, json=[])
        count += 1
        return httpx.Response(200, json={'tickers': [ticker(str(i)) for i in range(100)]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CoinGeckoCatalog(Storage(tmp_path), client, key='fixture', sleep=no_sleep)
        capped = await provider.snapshot(['gate'], max_pages=1)
        assert not capped['complete'] and capped['sources']['gate']['error'] == 'page_budget_reached'
        repeated = await provider.snapshot(['gate'], max_pages=3)
        assert repeated['sources']['gate']['error'] == 'repeated_page' and count == 2


async def test_monthly_budget_is_persistent_and_counts_failed_attempts(tmp_path):
    store = Storage(tmp_path)
    store.set('cg:budget', {'month': '1970-01', 'used': 9000})
    provider = CoinGeckoCatalog(store, key='fixture', clock=lambda: 100000)
    with pytest.raises(CatalogUnavailable, match='budget'):
        await provider.get('/coins/list', {})
    assert provider.client is None


async def test_candidate_requires_official_contract_not_symbol_and_excludes_derivatives(ready):
    snapshot = snap(ready)
    rows = candidates(ready.store.instruments(), ready.service._records(), snapshot)
    by_id = {r['instrument_id']: r for r in rows}
    assert by_id['gate-spot']['assessment'] == 'corroborated_hint'
    assert by_id['gate-perp']['assessment'] == 'unresolved'
    snapshot['coins'][0]['platforms'] = {'ethereum': OTHER_ADDRESS}
    rows = candidates(ready.store.instruments(), ready.service._records(), snapshot)
    assert all(r['assessment'] == 'unresolved' for r in rows)
    assert not any(i.mapping_verified for i in ready.store.instruments().values())


async def test_multiple_chains_duplicate_coin_id_and_bad_flags_require_review(ready):
    snapshot = snap(ready)
    snapshot['coins'][0]['platforms']['base'] = OTHER_ADDRESS
    assert all(r['assessment'] == 'unresolved' for r in candidates(ready.store.instruments(), ready.service._records(), snapshot))
    snapshot = snap(ready)
    snapshot['coins'].append(snapshot['coins'][0])
    assert all(r['assessment'] == 'unresolved' for r in candidates(ready.store.instruments(), ready.service._records(), snapshot))
    snapshot = snap(ready)
    snapshot['exchanges']['gate'][0]['is_stale'] = True
    assert next(r for r in candidates(ready.store.instruments(), ready.service._records(), snapshot) if r['instrument_id'] == 'gate-spot')['assessment'] == 'unresolved'


async def test_unchanged_approved_official_proofs_renew_but_contract_change_pauses(ready):
    plan = build_plan(ready.service)
    apply_plan(ready.service, plan['plan_token'])
    before = ready.store.instruments()['gate-spot']
    ready.clock.at += DAY
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=gate_response() if 'gateio' in r.url.host else bitget_response()))) as client:
        ready.service.client = client
        await ready.service.public_refresh(['gate', 'bitget'], renew_unchanged=True)
    after = ready.store.instruments()['gate-spot']
    assert after.mapping_verified and after.asset_id == before.asset_id
    assert after.mapping_evidence_ids != before.mapping_evidence_ids
    assert any(a['action'] == 'asset_identity_unchanged_renewal' for a in ready.store.audit())
    ready.clock.at += DAY
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=gate_response(OTHER_ADDRESS) if 'gateio' in r.url.host else bitget_response()))) as client:
        ready.service.client = client
        await ready.service.public_refresh(['gate', 'bitget'], renew_unchanged=True)
    assert not ready.store.instruments()['gate-spot'].mapping_verified
    assert ready.store.instruments()['gate-spot'].asset_id == before.asset_id  # No new target silently approved.


async def test_multiplier_change_and_manual_mapping_never_renew(ready):
    plan = build_plan(ready.service)
    apply_plan(ready.service, plan['plan_token'])
    ready.store.map_asset('gate-spot', 'manual:protected')
    item = ready.store.instruments()['bitget-spot']
    ready.store.save_instruments([item.model_copy(update={'contract_size': 1000})])
    ready.clock.at += 100
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=gate_response() if 'gateio' in r.url.host else bitget_response()))) as client:
        ready.service.client = client
        await ready.service.public_refresh(['gate', 'bitget'], renew_unchanged=True)
    assert ready.store.instruments()['gate-spot'].asset_id == 'manual:protected'
    assert not ready.store.instruments()['bitget-spot'].mapping_verified


async def test_catalog_persists_changes_paginates_and_missing_provider_does_not_erase(ready, monkeypatch):
    snapshot = snap(ready)
    class Provider:
        key = ''
        async def snapshot(self, venues):
            return snapshot
        async def close(self):
            pass
    async def official(venues, **kwargs):
        return {'sources': []}
    monkeypatch.setattr(ready.service, 'public_refresh', official)
    service = IdentityCatalogService(ready.service, provider=Provider(), clock=ready.clock)
    first = await service.refresh()
    assert first['counts']['new'] == len(ready.store.instruments())
    assert service.status(limit=1)['next_offset'] == 1
    assert IdentityCatalogService(ready.service, provider=Provider()).status()['total'] == first['total']
    second = await service.refresh()
    assert second['counts'].get('new', 0) >= 2  # Unapproved matches remain visible.
    snapshot = {'error': 'demo_key_required', 'coins': [], 'exchanges': {}, 'sources': {}, 'complete': False}
    await service.refresh()
    saved = ready.store.get('identity_catalog')['rows']
    assert next(r for r in saved if r['instrument_id'] == 'gate-spot')['coin_ids'] == ['small']
    assert not any(i.mapping_verified for i in ready.store.instruments().values())


async def test_shutdown_and_disabled_scheduler_do_not_request(ready):
    calls = []
    class Provider:
        key = ''
        async def close(self):
            calls.append('close')
    service = IdentityCatalogService(ready.service, provider=Provider())
    await service.start()
    await asyncio.sleep(0)
    await service.close()
    assert calls == ['close'] and service.task is None

async def test_successful_registry_removal_invalidates_old_proof_but_failed_read_does_not(ready):
    plan = build_plan(ready.service)
    apply_plan(ready.service, plan['plan_token'])
    ready.clock.at += 100
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        ready.service.client = client
        await ready.service.public_refresh(['gate'], renew_unchanged=True)
    assert ready.store.instruments()['gate-spot'].mapping_verified
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[]))) as client:
        ready.service.client = client
        await ready.service.public_refresh(['gate'], renew_unchanged=True)
    assert not ready.store.instruments()['gate-spot'].mapping_verified
    assert any(r['status'] == 'not_observed' for r in ready.service._records())


async def test_provider_change_after_outage_pauses_approved_mapping_without_overwrite(ready, monkeypatch):
    snapshot = snap(ready)
    class Provider:
        key = ''
        async def snapshot(self, venues):
            return snapshot
        async def close(self):
            pass
    async def official(venues, **kwargs):
        return {'sources': []}
    monkeypatch.setattr(ready.service, 'public_refresh', official)
    service = IdentityCatalogService(ready.service, provider=Provider(), clock=ready.clock)
    plan = build_plan(ready.service)
    apply_plan(ready.service, plan['plan_token'])
    target = ready.store.instruments()['gate-spot'].asset_id
    await service.refresh()
    snapshot = {'coins': [], 'sources': {}, 'exchanges': {}, 'complete': False, 'error': 'ReadTimeout'}
    await service.refresh()
    assert ready.store.instruments()['gate-spot'].mapping_verified
    snapshot = snap(ready, platforms={'ethereum': OTHER_ADDRESS})
    result = await service.refresh()
    assert 'gate-spot' in result['paused']
    current = ready.store.instruments()['gate-spot']
    assert not current.mapping_verified and current.asset_id == target
    assert current.mapping_evidence_status == 'catalog_review_required'


async def test_refresh_lock_rejects_concurrent_call_and_cancellation_releases(ready, monkeypatch):
    started = asyncio.Event()
    async def slow(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(ready.service, 'public_refresh', slow)
    service = IdentityCatalogService(ready.service)
    task = asyncio.create_task(service.refresh())
    await started.wait()
    with pytest.raises(ValueError, match='already running'):
        await service.refresh()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service.lock.locked()

async def test_catalog_api_configuration_pagination_and_access_reset_preserve_budget(ready, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from watch_money.identity_catalog import router
    async def official(*args, **kwargs):
        return {'sources': []}
    monkeypatch.setattr(ready.service, 'public_refresh', official)
    provider = CoinGeckoCatalog(ready.store, key='')
    service = IdentityCatalogService(ready.service, provider=provider, clock=ready.clock)
    app = FastAPI()
    app.state.identity_catalog, app.state.store = service, ready.store
    app.include_router(router)
    with TestClient(app) as client:
        assert client.put('/api/assets/catalog/automatic', json={'enabled': True}).json()['enabled']
        result = client.post('/api/assets/catalog/refresh')
        assert result.status_code == 200 and result.json()['provider']['error'] == 'demo_key_required'
        assert client.get('/api/assets/catalog?limit=1').json()['next_offset'] == 1
        assert client.get('/api/assets/catalog?limit=101').status_code == 422
        ready.store.set('cg:budget', {'used': 14, 'blocked': True})
        assert client.post('/api/assets/catalog/access-reviewed', json={'reason': 'short'}).status_code == 422
        response = client.post('/api/assets/catalog/access-reviewed', json={'reason': 'Operator confirmed corrected local configuration'})
        assert response.status_code == 200 and response.json()['budget']['used'] == 14
        assert not response.json()['budget'].get('blocked')
        assert response.json()['next_update_at'] > ready.clock()

async def test_enabled_schedule_updates_once_and_respects_next_day(ready, monkeypatch):
    service = IdentityCatalogService(ready.service, provider=CoinGeckoCatalog(ready.store, key=''), clock=ready.clock)
    ready.store.set('identity_catalog_enabled', True)
    ready.store.set('settings', ready.store.settings().model_copy(update={'collection_enabled': True, 'demo_mode': False}).model_dump())
    called = asyncio.Event()
    calls = []
    async def refresh_once():
        calls.append(ready.clock())
        ready.store.set('identity_catalog', {'next_update_at': ready.clock()+DAY})
        called.set()
    monkeypatch.setattr(service, 'refresh', refresh_once)
    await service.start()
    await asyncio.wait_for(called.wait(), timeout=1)
    await service.close()
    await service.start()
    await asyncio.sleep(0)
    await service.close()
    assert calls == [ready.clock()]

async def test_malformed_ticker_shape_is_reported_without_crashing_or_claiming_coverage(tmp_path):
    def handler(request):
        return httpx.Response(200, json=[] if request.url.path.endswith('/coins/list') else {
            'tickers': [{'base': {'bad': 'shape'}, 'target': 'USDT', 'market': 'wrong'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CoinGeckoCatalog(Storage(tmp_path), client, key='fixture', sleep=no_sleep)
        result = await provider.snapshot(['gate'])
    assert not result['complete'] and result['sources']['gate']['error'] == 'invalid_ticker_page'
    assert result['sources']['gate']['rows'] == 0
