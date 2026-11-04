"""Synthetic official-schema evidence; these tests are not real coverage acceptance."""
import json

# Imported pytest fixture is deliberately reused by name.
# ruff: noqa: F811

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_asset_verification import env, gate_response, bitget_response, manual, OTHER_ADDRESS  # noqa: F401
from watch_money.asset_verification import _digest, router
from watch_money.identity_batch import apply_plan, build_plan


async def refresh(env, gate=None, bitget=None):
    def handler(request):
        return httpx.Response(200, json=gate if request.url.host == "api.gateio.ws" else bitget)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        env.service.client = client
        await env.service.public_refresh(["gate", "bitget"])


@pytest.fixture
async def ready(env):
    # Remove pool proof from the generated evidence: that proof intentionally stays manual.
    with env.store.connect() as db:
        db.execute("DELETE FROM instruments WHERE id='onchain'")
    await refresh(env, gate_response(), bitget_response())
    return env


def mutate_fact(env, transform):
    rows = env.service._records()
    row = next(r for r in rows if r["instrument_id"] == "gate-spot")
    previous = row.pop("id")
    row.pop("status")
    transform(row)
    row["id"] = _digest(row)
    with env.store.connect() as db:
        db.execute("DELETE FROM asset_identity_evidence WHERE id=?", (previous,))
        db.execute("INSERT INTO asset_identity_evidence VALUES(?,?,'active')", (row["id"], json.dumps(row)))


def issues(plan):
    return {reason for group in plan["blocked_groups"] for reason in group["blockers"]}


@pytest.mark.asyncio
async def test_isolated_apply_revoke_audited_and_no_perp_mapping(ready):
    plan = build_plan(ready.service)
    assert plan["counts"]["eligible_groups"] == 1
    assert plan["counts"]["eligible_markets"] == 3
    assert not any(item.mapping_verified for item in ready.store.instruments().values())
    result = apply_plan(ready.service, plan["plan_token"])
    assert result["applied_markets"] == 3
    assert not ready.store.instruments()["gate-perp"].mapping_verified
    assert build_plan(ready.service)["counts"]["already_verified_groups"] == 1
    assert any(row["action"] == "asset_verification_apply" and
               row["payload"]["policy"]["plan_token"] == plan["plan_token"] for row in result["audit"])
    ready.service.revoke(result["evidence_ids"], "Synthetic rehearsal rollback")
    assert not any(item.mapping_verified for item in ready.store.instruments().values())


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["contract", "chain"])
async def test_same_ticker_does_not_merge(env, other):
    payload = bitget_response(OTHER_ADDRESS if other == "contract" else None)
    if other == "chain":
        payload = bitget_response()
        payload["data"][0]["chains"][0]["chain"] = "BSC"
    await refresh(env, gate_response(), payload)
    assert not build_plan(env.service)["eligible_evidence_ids"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change,reason", [
    (lambda f: f.update(observed_at=f["observed_at"] + 1), "evidence_older_than_24h_or_future"),
    (lambda f: f.update(expires_at=f["observed_at"] + 100), "evidence_validity_window_insufficient"),
    (lambda f: f.update(source_url="https://untrusted.invalid/coins"), "official_endpoint_unverified"),
    (lambda f: f.update(source_digest="bad"), "source_digest_invalid"),
    (lambda f: f.update(contracts=[]), "single_exact_chain_contract_required"),
    (lambda f: f.update(unit_multiplier=1000), "relationship_or_base_unit_requires_review"),
])
async def test_strict_fact_boundaries(ready, change, reason):
    mutate_fact(ready, change)
    assert reason in issues(build_plan(ready.service))
    assert not build_plan(ready.service)["eligible_evidence_ids"]


@pytest.mark.asyncio
async def test_source_failure_blocks_cached_fact_and_invalidates_token(ready):
    plan = build_plan(ready.service)
    with ready.store.connect() as db:
        value = json.loads(db.execute("SELECT record FROM asset_identity_sources WHERE venue='gate'").fetchone()[0])
        value.update(state="error", error_type="ReadTimeout")
        db.execute("UPDATE asset_identity_sources SET record=? WHERE venue='gate'", (json.dumps(value),))
    assert "latest_official_source_receipt_unavailable" in issues(build_plan(ready.service))
    with pytest.raises(ValueError, match="changed"):
        apply_plan(ready.service, plan["plan_token"])


@pytest.mark.asyncio
async def test_time_changes_do_not_change_token_until_eligibility_changes(ready):
    plan = build_plan(ready.service)
    ready.clock.at += 1000
    assert build_plan(ready.service)["plan_token"] == plan["plan_token"]
    ready.clock.at += 86_400_000
    with pytest.raises(ValueError, match="changed"):
        apply_plan(ready.service, plan["plan_token"])


@pytest.mark.asyncio
async def test_conflicting_manual_evidence_and_catalog_changes_reject(ready):
    plan = build_plan(ready.service)
    ready.service.add_evidence([manual(ready, canonical="ethereum:" + OTHER_ADDRESS)])
    assert "conflicting_active_evidence" in issues(build_plan(ready.service))
    with pytest.raises(ValueError):
        apply_plan(ready.service, plan["plan_token"])


@pytest.mark.asyncio
@pytest.mark.parametrize("updates", [{"native_asset_id": "cex:gate:unknown:PEPE"},
                                     {"contract_size": 1000}, {"kind": "perpetual"}, {"active": False}])
async def test_catalog_scope_changes(ready, updates):
    plan = build_plan(ready.service)
    item = ready.store.instruments()["gate-spot"].model_copy(update=updates)
    ready.store.save_instruments([item])
    assert not build_plan(ready.service)["eligible_evidence_ids"]
    with pytest.raises(ValueError):
        apply_plan(ready.service, plan["plan_token"])


@pytest.mark.asyncio
async def test_guard_rechecks_changes_after_preliminary_preview(ready, monkeypatch):
    plan = build_plan(ready.service)
    original = ready.service.apply
    def race(*args, **kwargs):
        ready.clock.at += 86_400_001
        return original(*args, **kwargs)
    monkeypatch.setattr(ready.service, "apply", race)
    with pytest.raises(ValueError, match="policy"):
        apply_plan(ready.service, plan["plan_token"])
    assert not any(item.mapping_verified for item in ready.store.instruments().values())


@pytest.mark.asyncio
async def test_routes_only_accept_exact_plan_token(ready):
    app = FastAPI()
    app.state.asset_verification = ready.service
    app.include_router(router)
    with TestClient(app) as client:
        plan = client.get("/api/assets/verification/batch-plan").json()
        assert client.post("/api/assets/verification/batch-apply", json={"plan_token": plan["plan_token"], "evidence_ids": []}).status_code == 422
        assert client.post("/api/assets/verification/batch-apply", json={"plan_token": plan["plan_token"]}).status_code == 200


@pytest.mark.asyncio
async def test_unverified_explicit_mapping_is_preserved(ready):
    ready.store.map_assets(["gate-spot"], "manual:prior-choice")
    plan = build_plan(ready.service)
    assert "existing_explicit_mapping_requires_manual_review" in issues(plan)
    with pytest.raises(ValueError, match="No eligible"):
        apply_plan(ready.service, plan["plan_token"])
    assert ready.store.instruments()["gate-spot"].asset_id == "manual:prior-choice"


@pytest.mark.asyncio
async def test_source_failure_between_plan_and_transaction_is_atomic(ready, monkeypatch):
    plan = build_plan(ready.service)
    original = ready.service.apply
    def race(*args, **kwargs):
        with ready.store.connect() as db:
            db.execute("DELETE FROM asset_identity_sources WHERE venue='gate'")
        return original(*args, **kwargs)
    monkeypatch.setattr(ready.service, "apply", race)
    with pytest.raises(ValueError, match="policy"):
        apply_plan(ready.service, plan["plan_token"])
    assert not any(item.mapping_verified for item in ready.store.instruments().values())


@pytest.mark.asyncio
async def test_record_integrity_and_existing_verified_mapping(ready):
    with ready.store.connect() as db:
        key, text = db.execute("SELECT id,record FROM asset_identity_evidence LIMIT 1").fetchone()
        record = json.loads(text)
        record["note"] = "tampered without recomputing record id"
        db.execute("UPDATE asset_identity_evidence SET record=? WHERE id=?", (json.dumps(record), key))
    assert "evidence_record_integrity_failed" in issues(build_plan(ready.service))


@pytest.mark.asyncio
async def test_manual_same_target_remains_manual_review(ready):
    ready.service.add_evidence([manual(ready)])
    assert "manual_or_nonpublic_evidence_requires_review" in issues(build_plan(ready.service))
    assert not build_plan(ready.service)["eligible_evidence_ids"]


@pytest.mark.asyncio
async def test_scaled_symbol_not_approved(ready):
    item = ready.store.instruments()["gate-spot"].model_copy(update={"base": "1000PEPE"})
    ready.store.save_instruments([item])
    assert "base_unit_scale_requires_review" in issues(build_plan(ready.service))


@pytest.mark.asyncio
async def test_empty_plan_and_malformed_api_token(ready):
    ready.service.revoke([row["id"] for row in ready.service._records()], "Synthetic revocation")
    plan = build_plan(ready.service)
    with pytest.raises(ValueError, match="No eligible"):
        apply_plan(ready.service, plan["plan_token"])
    app = FastAPI()
    app.state.asset_verification = ready.service
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post("/api/assets/verification/batch-apply", json={"plan_token": "bad"}).status_code == 422
        assert client.post("/api/assets/verification/batch-apply", json={"plan_token": "0" * 64}).status_code == 409


@pytest.mark.asyncio
async def test_apply_and_revoke_persist_across_service_restart(ready):
    from types import SimpleNamespace
    from watch_money.asset_verification import AssetVerificationService
    plan = build_plan(ready.service)
    result = apply_plan(ready.service, plan["plan_token"])
    restarted = AssetVerificationService(ready.store, SimpleNamespace(), clock=ready.clock)
    assert build_plan(restarted)["counts"]["already_verified_groups"] == 1
    restarted.revoke(result["evidence_ids"], "Synthetic persistence check")
    again = AssetVerificationService(ready.store, SimpleNamespace(), clock=ready.clock)
    assert not build_plan(again)["eligible_evidence_ids"]
    assert not any(item.mapping_verified for item in ready.store.instruments().values())


@pytest.mark.asyncio
async def test_successive_refresh_uses_only_latest_active_records(ready):
    old = set(build_plan(ready.service)["eligible_evidence_ids"])
    ready.clock.at += 1000
    await refresh(ready, gate_response(), bitget_response())
    plan = build_plan(ready.service)
    assert plan["counts"]["eligible_groups"] == 1
    assert plan["counts"]["eligible_markets"] == 3
    assert not old.intersection(plan["eligible_evidence_ids"])
    assert all(row["status"] == "superseded" for row in ready.service._records() if row["id"] in old)
    result = apply_plan(ready.service, plan["plan_token"])
    assert result["applied_markets"] == 3


@pytest.mark.asyncio
async def test_revoked_different_target_does_not_contaminate_latest_group(ready):
    added = ready.service.add_evidence([manual(ready, canonical="ethereum:" + OTHER_ADDRESS)])
    ready.service.revoke(added["evidence_ids"], "Synthetic rejected identity")
    plan = build_plan(ready.service)
    assert plan["counts"]["eligible_groups"] == 1
    assert not set(added["evidence_ids"]).intersection(plan["eligible_evidence_ids"])
    assert not any(group["canonical_asset_id"] == "ethereum:" + OTHER_ADDRESS for group in plan["blocked_groups"])
    assert apply_plan(ready.service, plan["plan_token"])["applied_markets"] == 3
