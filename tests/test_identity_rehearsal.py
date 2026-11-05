"""Synthetic official-schema fixtures; no live exchange observations."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from watch_money.asset_verification import AssetVerificationService, EvidenceInput
from watch_money.models import Instrument, now_ms
from watch_money.storage import Storage

spec = importlib.util.spec_from_file_location('rehearsal', Path(__file__).parents[1] / 'scripts/rehearse_identity_batch.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
ADDRESS = '0x' + 'ab' * 20


def source(tmp_path):
    store = Storage(tmp_path / 'source')
    store.save_instruments([Instrument(id=venue, venue=venue, native_symbol='PEPE/USDT',
        asset_id=f'cex:{venue}:{venue}:PEPE', native_asset_id=f'cex:{venue}:{venue}:PEPE',
        native_currency_id='PEPE', base='PEPE', quote='USDT', kind='spot') for venue in ['gate', 'bitget']])
    service = AssetVerificationService(store, SimpleNamespace(instruments=store.instruments()))
    with store.connect() as db:
        db.execute("INSERT INTO kv VALUES ('private_account', 'DO_NOT_COPY')")
        db.execute("INSERT INTO latest VALUES ('private', 'DO_NOT_COPY')")
    return store, service


def client(*, other_chain=False):
    def handle(request):
        if request.url.host == 'api.gateio.ws':
            return httpx.Response(200, json=[{'currency': 'PEPE', 'chains': [{'name': 'ETH', 'addr': ADDRESS}]}])
        assert request.url.host == 'api.bitget.com'
        return httpx.Response(200, json={'code': '00000', 'data': [{'coin': 'PEPE', 'chains': [
            {'chain': 'BSC' if other_chain else 'ERC20', 'contractAddress': ADDRESS}]}]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_capture_is_readonly_bounded_and_does_not_copy_private_tables(tmp_path):
    store, _service = source(tmp_path)
    snapshot = r.capture(store.db_path)
    assert 'DO_NOT_COPY' not in json.dumps(snapshot)
    clone = r.load_capture(snapshot, tmp_path / 'clone')
    assert clone.store.get('private_account') is None
    assert r.capture(store.db_path) == snapshot
    with store.connect() as db:
        assert db.execute("SELECT value FROM kv WHERE key='private_account'").fetchone()[0] == 'DO_NOT_COPY'
    with pytest.raises(ValueError, match='limit'):
        r.capture(store.db_path, max_rows=1)
    with pytest.raises(ValueError, match='limit'):
        r.capture(store.db_path, max_bytes=1)
    with pytest.raises(FileNotFoundError):
        r.capture(tmp_path / 'missing.sqlite3')
    assert not (tmp_path / 'missing.sqlite3').exists()


def test_manual_notes_urls_are_redacted_but_existing_verified_baseline_survives(tmp_path):
    store, service = source(tmp_path)
    for instrument in store.instruments().values():
        service.add_evidence([EvidenceInput(instrument_id=instrument.id, native_asset_id=instrument.asset_id,
            canonical_asset_id=f'ethereum:{ADDRESS}', source_url='https://private.invalid/DO_NOT_COPY',
            source_field='private_field_DO_NOT_COPY', observed_at=now_ms(), expires_at=now_ms() + 86400000,
            note='private note DO_NOT_COPY')])
    ids = [record['id'] for record in service._records()]
    service.apply(ids, service.preview(ids)['preview_token'])
    snapshot = r.capture(store.db_path)
    assert 'DO_NOT_COPY' not in json.dumps(snapshot)
    clone = r.load_capture(snapshot, tmp_path / 'clone')
    assert r.coverage(clone) == r.coverage(service)
    assert r.coverage(clone)['confirmed_cross_platform_groups'] == 1


async def test_public_fixture_rehearsal_applies_revokes_without_source_changes(tmp_path):
    store, _ = source(tmp_path)
    snapshot = r.capture(store.db_path)
    async with client() as http:
        result = await r.rehearse(snapshot, refresh_public=True, client=http)
    assert result['before']['confirmed_cross_platform_groups'] == 0
    assert result['after_temporary_apply']['confirmed_cross_platform_groups'] == 1
    assert result['after_temporary_revoke']['confirmed_cross_platform_groups'] == 0
    assert result['baseline_coverage_restored']
    assert r.capture(store.db_path) == snapshot


async def test_same_ticker_same_address_different_chain_cannot_merge(tmp_path):
    store, _ = source(tmp_path)
    async with client(other_chain=True) as http:
        result = await r.rehearse(r.capture(store.db_path), refresh_public=True, client=http)
    assert result['after_temporary_apply']['confirmed_cross_platform_groups'] == 0
    assert result['temporary_applied_evidence_count'] == 0


async def test_empty_input_claims_no_real_demonstration(tmp_path):
    store = Storage(tmp_path / 'empty')
    result = await r.rehearse(r.capture(store.db_path))
    assert not result['actual_catalog_available']
    assert result['temporary_applied_evidence_count'] == 0
    assert result['platform_sources']['gate']['state'] == 'not_observed'


def test_atomic_report_replaces_complete_json(tmp_path):
    target = tmp_path / 'report.json'
    r.atomic_report(target, {'actual': False})
    assert json.loads(target.read_text()) == {'actual': False}
    assert list(tmp_path.iterdir()) == [target]


async def test_saved_public_evidence_survives_sanitized_capture_with_integrity(tmp_path):
    from watch_money.identity_batch import build_plan
    store, service = source(tmp_path)
    async with client() as http:
        service.client, service._owns_client = http, False
        await service.public_refresh(['gate', 'bitget'])
    snapshot = r.capture(store.db_path)
    clone = r.load_capture(snapshot, tmp_path / 'clone')
    assert build_plan(clone)['counts']['eligible_groups'] == 1
    report = await r.rehearse(snapshot)
    assert report['after_temporary_apply']['confirmed_cross_platform_groups'] == 1
    assert report['after_temporary_revoke']['confirmed_cross_platform_groups'] == 0
    assert r.capture(store.db_path) == snapshot
