"""Cached third-party identity hints; never a ticker-based mapping authority."""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .asset_verification import _digest, catalog_fingerprint, public_source_scope
from .identity import _chain_address
from .models import now_ms

DAY = 86400000
BASE = 'https://api.coingecko.com/api/v3'
# Product-specific CEX scopes already reviewed by public_source_scope. Not futures IDs.
EXCHANGES = {'gate': 'gate', 'bitget': 'bitget', 'kucoin': 'kucoin', 'coinbase': 'gdax'}
PLATFORMS = {'ethereum': 'ethereum', 'binance-smart-chain': 'bsc', 'polygon-pos': 'polygon',
             'arbitrum-one': 'arbitrum', 'optimistic-ethereum': 'optimism', 'base': 'base',
             'avalanche': 'avalanche', 'solana': 'solana', 'linea': 'linea', 'scroll': 'scroll',
             'zksync': 'zksync'}


class CatalogUnavailable(Exception):
    pass


class CoinGeckoCatalog:
    def __init__(self, store, client=None, *, key=None, clock=now_ms, sleep=asyncio.sleep):
        self.store, self.client, self.clock, self.sleep = store, client, clock, sleep
        self.key = os.getenv('COINGECKO_DEMO_API_KEY', '') if key is None else key
        self.owned = client is None
        self.lock = asyncio.Lock()

    async def close(self):
        if self.owned and self.client:
            await self.client.aclose()

    async def get(self, path, params):
        cache_key = 'cg:cache:' + _digest([path, params])
        async with self.lock:
            now = self.clock()
            cached = self.store.get(cache_key)
            if cached and 0 <= now-cached['at'] < DAY:
                return cached['data'], cached['at'], True
            if not self.key:
                raise CatalogUnavailable('demo_key_required')
            budget = self.store.get('cg:budget', {})
            if budget.get('blocked'):
                raise CatalogUnavailable('access_denied_requires_configuration_review')
            if now < budget.get('next_at', 0):
                wait = (budget['next_at']-now)/1000
                if wait > 60:
                    raise CatalogUnavailable('rate_limit_cooldown')
                await self.sleep(wait)
            now = self.clock()
            month = datetime.fromtimestamp(now/1000, UTC).strftime('%Y-%m')
            used = budget.get('used', 0) if budget.get('month') == month else 0
            if used >= 9000:
                raise CatalogUnavailable('local_monthly_budget_exhausted')
            # Count attempts before I/O, including failures/cancellation. Persist across restart.
            budget.update(month=month, used=used+1, next_at=self.clock()+3000)
            self.store.set('cg:budget', budget)
            if self.client is None:
                self.client = httpx.AsyncClient(timeout=20, follow_redirects=False)
            try:
                async with self.client.stream('GET', BASE+path, params=params,
                        headers={'x-cg-demo-api-key': self.key, 'Accept': 'application/json'}) as response:
                    if response.status_code in (401, 403):
                        budget['blocked'] = True
                        self.store.set('cg:budget', budget)
                        raise CatalogUnavailable('access_denied_requires_configuration_review')
                    if response.status_code == 429:
                        budget['next_at'] = self.clock()+3600000
                        self.store.set('cg:budget', budget)
                        raise CatalogUnavailable('rate_limit_cooldown')
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 20*1024*1024:
                            raise CatalogUnavailable('response_size_limit')
                    received = self.clock()
                    data = json.loads(body)
            except (httpx.HTTPError, ValueError) as exc:
                # Do not persist exception text, request headers, or authentication values.
                raise CatalogUnavailable(type(exc).__name__) from None
            self.store.set(cache_key, {'at': received, 'data': data})
            return data, received, False

    async def snapshot(self, venues, max_pages=50):
        result = {'coins': [], 'exchanges': {}, 'sources': {}, 'complete': False}
        try:
            coins, at, cached = await self.get('/coins/list', {'include_platform': 'true'})
            if not isinstance(coins, list) or len(coins) > 100000 or any(not isinstance(c, dict) or not isinstance(c.get('id'), str) or not isinstance(c.get('platforms'), dict) for c in coins):
                raise CatalogUnavailable('invalid_coin_directory')
            result.update(coins=coins, coins_at=at, coins_cached=cached)
        except CatalogUnavailable as exc:
            result['error'] = str(exc)
            return result
        for venue in venues:
            rows, seen, complete, pages, stamp = [], set(), False, 0, None
            error = None
            for page in range(1, max_pages+1):
                try:
                    payload, at, _ = await self.get('/exchanges/'+EXCHANGES[venue]+'/tickers',
                                                  {'page': page, 'order': 'base_target'})
                    batch = payload.get('tickers') if isinstance(payload, dict) else None
                    if (not isinstance(batch, list) or len(batch) > 100 or any(
                            not isinstance(r, dict) or not isinstance(r.get('base'), str)
                            or not isinstance(r.get('target'), str) or not isinstance(r.get('market'), dict)
                            or not isinstance(r['market'].get('identifier'), str) for r in batch)):
                        raise CatalogUnavailable('invalid_ticker_page')
                    digest = _digest(batch)
                    if batch and digest in seen:
                        raise CatalogUnavailable('repeated_page')
                    seen.add(digest)
                    rows.extend(batch)
                    pages += 1
                    stamp = min(stamp, at) if stamp is not None else at
                    if len(batch) < 100:
                        complete = True
                        break
                except CatalogUnavailable as exc:
                    error = str(exc)
                    break
            result['exchanges'][venue] = rows
            result['sources'][venue] = {'pages': pages, 'rows': len(rows), 'complete': complete,
                'observed_at': stamp, 'error': error or (None if complete else 'page_budget_reached')}
            if error == 'access_denied_requires_configuration_review':
                break
        result['complete'] = all(result['sources'].get(v, {}).get('complete') for v in venues)
        return result


def candidates(catalog, records, snapshot):
    coins = defaultdict(list)
    for coin in snapshot.get('coins', []):
        coins[coin['id']].append(coin)
    facts = defaultdict(list)
    for fact in records:
        if fact['status'] == 'active' and fact['evidence_kind'] == 'public_contract_metadata':
            facts[fact['instrument_id']].append(fact)
    pairs = defaultdict(list)
    for venue, rows in snapshot.get('exchanges', {}).items():
        for row in rows:
            if (row.get('market') or {}).get('identifier') == EXCHANGES.get(venue):
                pairs[(venue, row.get('base'), row.get('target'))].append(row)
    output = []
    for item in catalog.values():
        if not item.active:
            continue
        _, issue = public_source_scope(item, item.venue)
        reasons, contracts, coin_ids = [], [], []
        if issue or item.contract_size != 1 or re.match(r'^\d', item.base):
            reasons.append(issue or 'scaled_product_requires_independent_proof')
        source = snapshot.get('sources', {}).get(item.venue, {})
        rows = pairs.get((item.venue, item.base, item.quote), [])
        # This pair lookup only selects hints. Official native currency/contract proof is mandatory.
        coin_ids = sorted({row['coin_id'] for row in rows if isinstance(row.get('coin_id'), str)})
        if len(coin_ids) != 1:
            reasons.append('missing_or_ambiguous_exchange_coin_id')
        elif len(coins[coin_ids[0]]) != 1:
            reasons.append('missing_or_duplicate_coin_id')
        else:
            platforms = coins[coin_ids[0]][0]['platforms']
            for platform, address in platforms.items():
                chain = PLATFORMS.get(platform)
                parsed = _chain_address(f'{chain}:{address}') if chain and isinstance(address, str) else None
                if parsed:
                    contracts.append(':'.join(parsed))
                else:
                    reasons.append('unsupported_or_missing_platform_contract')
            contracts = sorted(set(contracts))
            if len(contracts) != 1:
                reasons.append('cross_chain_or_native_asset_requires_review')
        if any(row.get('is_anomaly') is not False or row.get('is_stale') is not False for row in rows):
            reasons.append('provider_ticker_flagged_or_unknown')
        official = sorted({f['canonical_asset_id'] for f in facts[item.id] if not f['blockers'] and f['canonical_asset_id']})
        if len(official) != 1 or official != contracts:
            reasons.append('official_contract_not_corroborated')
        if not source.get('complete'):
            reasons.append('provider_coverage_incomplete')
        output.append({'instrument_id': item.id, 'venue': item.venue, 'symbol': item.native_symbol,
            'kind': item.kind, 'coin_ids': coin_ids, 'contracts': contracts, 'official_contracts': official,
            'fingerprint': catalog_fingerprint(item), 'reasons': sorted(set(reasons)),
            'assessment': 'corroborated_hint' if not reasons else 'unresolved',
            'source_at': source.get('observed_at'), 'coins_at': snapshot.get('coins_at')})
    return output


class IdentityCatalogService:
    def __init__(self, verification, *, provider=None, clock=now_ms):
        self.verification, self.store, self.clock = verification, verification.store, clock
        self.provider = provider or CoinGeckoCatalog(self.store)
        self.lock, self.task = asyncio.Lock(), None

    def status(self, offset=0, limit=50, changed_only=True):
        saved = self.store.get('identity_catalog', {})
        rows = saved.get('rows', [])
        shown = [r for r in rows if not changed_only or r['change'] != 'unchanged']
        return {**{k: v for k, v in saved.items() if k != 'rows'},
            'enabled': self.store.get('identity_catalog_enabled', False), 'demo_key_configured': bool(self.provider.key),
            'budget': self.store.get('cg:budget', {}), 'counts': dict(Counter(r['change'] for r in rows)),
            'total': len(shown), 'offset': offset, 'next_offset': offset+limit if offset+limit < len(shown) else None,
            'rows': shown[offset:offset+limit], 'interval_hours': 24}

    async def refresh(self):
        if self.lock.locked():
            raise ValueError('Identity catalog update already running')
        async with self.lock:
            old = self.store.get('identity_catalog', {})
            old_rows = {r['instrument_id']: r for r in old.get('rows', [])}
            venues = sorted({i.venue for i in self.store.instruments().values() if i.active and i.venue in EXCHANGES})
            denied = set(old.get('denied_official_sources', []))
            allowed = [v for v in venues if v not in denied]
            official = await self.verification.public_refresh(allowed, renew_unchanged=True) if allowed else {'sources': []}
            denied.update(a['venue'] for a in official['sources'] if a.get('http_status') in (401, 403))
            self.store.set('identity_catalog', {**old, 'denied_official_sources': sorted(denied),
                                                'next_update_at': self.clock()+DAY, 'last_attempt_at': self.clock()})
            snapshot = await self.provider.snapshot(venues)
            current_catalog = self.store.instruments()
            rows = candidates(current_catalog, self.verification._records(), snapshot)
            for row in rows:
                previous = old_rows.get(row['instrument_id'])
                def signature(r):
                    return {k: r[k] for k in ('coin_ids', 'contracts', 'official_contracts', 'fingerprint')}
                # A failed/truncated read is not evidence of removal or changed identity.
                if previous and not snapshot.get('sources', {}).get(row['venue'], {}).get('complete'):
                    row['coin_ids'], row['contracts'] = previous['coin_ids'], previous['contracts']
                    row['source_at'], row['coins_at'] = previous.get('source_at'), previous.get('coins_at')
                row['change'] = ('new' if previous is None else 'changed' if signature(previous) != signature(row)
                                 else 'unresolved' if row['reasons'] else 'unchanged')
                if previous and row['change'] == 'unchanged':
                    current = current_catalog.get(row['instrument_id'])
                    if not current or not current.mapping_verified:
                        row['change'] = previous['change'] if previous['change'] in ('new', 'changed') else 'new'
                if row['change'] == 'changed':
                    row['previous'] = previous.get('previous', signature(previous))
                baseline = previous.get('last_corroborated') if previous else None
                if baseline is None and previous and previous.get('assessment') == 'corroborated_hint':
                    baseline = signature(previous)
                row['last_corroborated'] = baseline or (signature(row) if row['assessment'] == 'corroborated_hint' else None)
            # Never approve from provider hints. Pause existing evidence-backed mappings
            # when previously corroborated identity changes; retain the mapping for explicit review.
            with self.store.lock, self.store.connect() as db:
                paused = []
                for row in rows:
                    previous = old_rows.get(row['instrument_id'], {})
                    if row['change'] != 'changed' or not row.get('last_corroborated'):
                        continue
                    record = db.execute('SELECT metadata FROM asset_mapping_verifications WHERE instrument_id=?', (row['instrument_id'],)).fetchone()
                    if record:
                        metadata = json.loads(record[0])
                        metadata['status'] = 'catalog_review_required'
                        db.execute('UPDATE asset_mapping_verifications SET metadata=? WHERE instrument_id=?', (json.dumps(metadata), row['instrument_id']))
                        paused.append(row['instrument_id'])
                if paused:
                    self.verification._audit(db, 'identity_catalog_pause', {'instrument_ids': paused})
            self.verification.collector.instruments = self.store.instruments()
            saved = {'at': self.clock(), 'rows': rows, 'provider': {k: v for k, v in snapshot.items() if k not in ('coins', 'exchanges')},
                     'official_sources': official['sources'], 'denied_official_sources': sorted(denied),
                     'paused': paused, 'next_update_at': self.clock()+DAY}
            self.store.set('identity_catalog', saved)
            return self.status()

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            settings = self.store.settings()
            if self.store.get('identity_catalog_enabled', False) and settings.collection_enabled and not settings.demo_mode:
                saved = self.store.get('identity_catalog', {})
                if self.clock() >= saved.get('next_update_at', 0):
                    try:
                        await self.refresh()
                    except ValueError:
                        pass
            await asyncio.sleep(60)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.provider.close()


router = APIRouter(prefix='/api/assets/catalog', tags=['asset catalog'])


@router.get('')
def status(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), changed_only: bool = True):
    return request.app.state.identity_catalog.status(offset, limit, changed_only)


@router.post('/refresh')
async def refresh(request: Request):
    try:
        return await request.app.state.identity_catalog.refresh()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


class AutomaticUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


@router.put('/automatic')
def automatic(payload: AutomaticUpdate, request: Request):
    request.app.state.store.set('identity_catalog_enabled', payload.enabled)
    return request.app.state.identity_catalog.status()


class AccessReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=10, max_length=300)


@router.post('/access-reviewed')
def access_reviewed(payload: AccessReview, request: Request):
    service = request.app.state.identity_catalog
    if service.lock.locked():
        raise HTTPException(409, 'Wait for the current catalog update')
    budget = service.store.get('cg:budget', {})
    budget.pop('blocked', None)
    service.store.set('cg:budget', budget)
    saved = service.store.get('identity_catalog', {})
    saved.update(denied_official_sources=[], next_update_at=service.clock()+DAY)
    service.store.set('identity_catalog', saved)
    with service.store.lock, service.store.connect() as db:
        service.verification._audit(db, 'identity_source_access_reviewed', {'reason': payload.reason})
    return service.status()
