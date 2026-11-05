"""Whole-group filters and scoped policy apply in temporary databases only."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_identity_batch import ready as _ready, refresh
from test_asset_verification import env as _env, market, gate_response, bitget_response, manual, OTHER_ADDRESS
from watch_money.asset_verification import router
from watch_money.identity_batch import build_plan, apply_plan, review_groups

env = _env
ready = _ready


async def two_groups(env):
    rows = list(env.store.instruments().values()) + [market('other-'+v, v, base='OTHER') for v in ['gate', 'bitget']]
    env.store.save_instruments(rows)
    gate = gate_response() + [{**gate_response(OTHER_ADDRESS)[0], 'currency': 'OTHER'}]
    bitget = bitget_response()
    bitget['data'] += [{**bitget_response(OTHER_ADDRESS)['data'][0], 'coin': 'OTHER'}]
    await refresh(env, gate, bitget)


async def test_filtered_page_keeps_entire_evidence_group_and_real_coverage(ready):
    await two_groups(ready)
    before = ready.store.audit()
    packet = review_groups(ready.service, source='gate', chain='ethereum', limit=1)
    assert packet['actual_comparable_groups'] == 0 and packet['filtered_groups'] == 2
    assert len(packet['groups']) == 1 and packet['next_offset'] == 1
    group = packet['groups'][0]
    assert group['sources'] == ['bitget', 'gate'] and len(group['evidence']) >= 2
    assert all(row['source_digest'] for row in group['evidence'])
    other = review_groups(ready.service, offset=1, limit=1)
    assert other['groups'][0]['id'] != group['id'] and other['next_offset'] is None
    assert ready.store.audit() == before
    assert review_groups(ready.service, contract=group['contract'])['filtered_groups'] == 1
    assert review_groups(ready.service, chain='solana')['filtered_groups'] == 0


async def test_scoped_apply_changes_only_reviewed_group_and_can_revoke(ready):
    await two_groups(ready)
    group = review_groups(ready.service, limit=1)['groups'][0]
    scoped = build_plan(ready.service, [group['id']])
    assert scoped['counts']['eligible_groups'] == 1
    with pytest.raises(ValueError):
        apply_plan(ready.service, scoped['plan_token'])  # Omission cannot broaden the scope.
    result = apply_plan(ready.service, scoped['plan_token'], [group['id']])
    assert result['applied_groups'] == 1
    verified = {i.id for i in ready.store.instruments().values() if i.mapping_verified}
    assert verified == set(group['instrument_ids'])
    assert review_groups(ready.service)['actual_comparable_groups'] == 1
    ready.service.revoke(result['evidence_ids'], 'temporary grouped review rollback')
    assert review_groups(ready.service)['actual_comparable_groups'] == 0


async def test_source_filter_cannot_hide_conflict(ready):
    ready.service.add_evidence([manual(ready, canonical='ethereum:'+OTHER_ADDRESS)])
    packet = review_groups(ready.service, source='bitget', state='blocked', blocker='conflicting_active_evidence')
    assert packet['filtered_groups'] == 1
    row = packet['groups'][0]
    assert 'gate' in row['sources'] and 'conflicting_active_evidence' in row['blockers']
    scoped = build_plan(ready.service, [row['id']])
    assert scoped['counts']['eligible_groups'] == 0
    with pytest.raises(ValueError):
        apply_plan(ready.service, scoped['plan_token'], [row['id']])


async def test_expiry_reasons_filter_and_preview_token_revalidation(ready):
    group = review_groups(ready.service)['groups'][0]
    scoped = build_plan(ready.service, [group['id']])
    ready.clock.at += 25*3600000
    packet = review_groups(ready.service, state='blocked', blocker='evidence_older_than_24h_or_future')
    assert packet['filtered_groups'] == 1 and packet['actual_comparable_groups'] == 0
    assert 'evidence_older_than_24h_or_future' in packet['options']['blockers']
    with pytest.raises(ValueError):
        apply_plan(ready.service, scoped['plan_token'], [group['id']])


@pytest.mark.parametrize('ids', [[], ['missing'], ['missing']*101])
async def test_scoped_plan_rejects_unbounded_or_unknown_selection(ready, ids):
    with pytest.raises(ValueError):
        build_plan(ready.service, ids)


async def test_preview_mixed_selection_explicitly_counts_skips_without_applying_them(ready):
    await two_groups(ready)
    ready.service.add_evidence([manual(ready, 'other-gate', canonical='ethereum:'+'33'*20)])
    packet = review_groups(ready.service)
    plan = build_plan(ready.service, [row['id'] for row in packet['groups']])
    assert plan['counts']['eligible_groups'] == 1 and plan['counts']['blocked_groups'] >= 1
    applied = apply_plan(ready.service, plan['plan_token'], plan['selected_proposal_ids'])
    assert applied['applied_groups'] == 1
    audit = next(row for row in reversed(ready.store.audit()) if row['action'] == 'asset_verification_apply')
    assert audit['payload']['policy']['counts']['blocked_groups'] >= 1
    assert audit['payload']['policy']['skipped_groups'][0]['blockers']
    assert not ready.store.instruments()['other-gate'].mapping_verified


async def test_review_api_filters_preview_apply_and_bounds(ready):
    app = FastAPI()
    app.state.asset_verification = ready.service
    app.include_router(router)
    with TestClient(app) as client:
        result = client.get('/api/assets/verification/batch-review', params={'source':'gate','limit':1}).json()
        identity = result['groups'][0]['id']
        plan = client.post('/api/assets/verification/batch-preview', json={'proposal_ids':[identity]}).json()
        assert plan['selected_proposal_ids'] == [identity]
        response = client.post('/api/assets/verification/batch-apply', json={'proposal_ids':[identity], 'plan_token':plan['plan_token']})
        assert response.status_code == 200 and response.json()['applied_groups'] == 1
        assert client.get('/api/assets/verification/batch-review', params={'limit':101}).status_code == 422
        assert client.get('/api/assets/verification/batch-review', params={'state':'guess'}).status_code == 422
