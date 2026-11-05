"""Fee catalog semantics, isolated overrides and atomic local audit; no account API."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from watch_money.fee_profiles import (
    CATALOG_PATH,
    OVERRIDES_KEY,
    VENUE_PRODUCTS,
    FeeProfileService,
    FeeProfileUpdate,
    router,
)
from watch_money.models import Instrument, Settings
from watch_money.storage import Storage

NOW = 1_793_900_000_000


def service(tmp_path, *, empty=False):
    catalog = CATALOG_PATH
    if empty:
        catalog = tmp_path / "empty-public-fees.json"
        catalog.write_text('{"schema_version":1,"reviewed_profiles":[]}', encoding="utf-8")
    return FeeProfileService(Storage(tmp_path), catalog, lambda: NOW)


def update(**values):
    return FeeProfileUpdate(venue="okx", kind="spot", liquidity="maker", rate=values.pop("rate", .0003), **values)


def instrument(venue="okx", kind="perpetual", **values):
    return Instrument(id="test", venue=venue, native_symbol=values.pop("native_symbol", "BTC/USDT:USDT"),
                      asset_id="BTC", base="BTC", quote=values.pop("quote", "USDT"),
                      settle=values.pop("settle", "USDT"), kind=kind, **values)


def test_every_platform_product_role_is_explicit_and_empty_catalog_never_guesses(tmp_path):
    fees = service(tmp_path, empty=True)
    result = fees.get()
    assert set(VENUE_PRODUCTS) == set(Settings().enabled_venues)
    assert len(result["profiles"]) == 28 * 2 * 2
    assert len({row["key"] for row in result["profiles"]}) == 112
    assert all(row["effective"]["rate"] is None and row["override"] is None for row in result["profiles"])
    assert all(row["default"]["source_verified"] is False for row in result["profiles"])
    assert result["audit"] == [] and result["account_api_used"] is False


def test_reviewed_sources_and_genuine_zero_remain_distinct_from_unknown(tmp_path):
    fees = service(tmp_path)
    assert fees.effective("okx", "spot", "maker")["rate"] == .0008
    assert fees.effective("okx", "perpetual", "taker")["rate"] == .0005
    assert fees.effective("lighter", "perpetual", "taker")["rate"] == 0
    assert fees.effective("lighter", "spot", "maker")["source_verified"] is True
    assert fees.effective("binance", "perpetual", "taker")["rate"] is None
    assert fees.effective("gmx", "perpetual", "maker")["rate"] is None
    assert fees.effective("upbit", "perpetual", "taker")["evidence_status"] == "not_applicable"


def test_override_is_scoped_to_one_venue_product_role_and_survives_restart(tmp_path):
    fees = service(tmp_path)
    original_taker = fees.effective("okx", "spot", "taker")
    original_perp = fees.effective("okx", "perpetual", "maker")
    original_other = fees.effective("bitget", "spot", "maker")
    fees.update(update(rate=-.0001, source="user:negotiated maker rebate", conditions="Explicit user assumption",
                       fee_currency="USDT", updated_at=NOW - 10000))
    restored = FeeProfileService(Storage(tmp_path), clock=lambda: NOW + 20000)
    value = restored.effective("okx", "spot", "maker")
    assert value["rate"] == -.0001 and value["source_verified"] is False
    assert value["evidence_status"] == "user_override" and value["tier"] == "user_assumption"
    assert value["updated_at"] == NOW - 10000 and value["saved_at"] == NOW
    assert restored.effective("okx", "spot", "taker") == original_taker
    assert restored.effective("okx", "perpetual", "maker") == original_perp
    assert restored.effective("bitget", "spot", "maker") == original_other


def test_reads_preserve_source_dates_and_do_not_create_audit_or_overrides(tmp_path):
    fees = service(tmp_path)
    value = fees.effective("okx", "perpetual", "maker")
    assert value["updated_at"] < NOW
    assert value["checked_at"] < NOW
    later = FeeProfileService(fees.store, clock=lambda: NOW + 365 * 86400000)
    assert later.effective("okx", "perpetual", "maker") == value
    assert fees.store.get(OVERRIDES_KEY) is None and later.get()["audit"] == []


def test_unknown_override_masks_known_default_and_reset_restores_only_that_profile(tmp_path):
    fees = service(tmp_path)
    fees.update(update(rate=None, source=None, conditions="Fee not yet established"))
    row = fees.get("okx", "spot", "maker")["profiles"][0]
    assert row["default"]["rate"] == .0008 and row["effective"]["rate"] is None
    assert row["effective"]["source_verified"] is False
    fees.update(FeeProfileUpdate(venue="okx", kind="spot", liquidity="taker", rate=.002))
    fees.update(FeeProfileUpdate(venue="okx", kind="spot", liquidity="maker", reset=True))
    assert fees.effective("okx", "spot", "maker")["rate"] == .0008
    assert fees.effective("okx", "spot", "taker")["rate"] == .002
    assert fees.audit()[0]["action"] == "fee_profile_reset"


def test_audit_retains_previous_effective_and_both_override_revisions(tmp_path):
    fees = service(tmp_path)
    fees.update(update(rate=.0003, source="user:first"))
    fees.update(update(rate=.0004, source="user:second"))
    audits = FeeProfileService(Storage(tmp_path), clock=lambda: NOW).audit()
    assert len(audits) == 2
    assert audits[0]["payload"]["previous_override"]["rate"] == .0003
    assert audits[0]["payload"]["override"]["rate"] == .0004
    assert audits[1]["payload"]["previous_override"] is None
    assert audits[1]["payload"]["previous_effective"]["rate"] == .0008


def test_audit_failure_rolls_back_override_and_does_not_leave_partial_change(tmp_path):
    fees = service(tmp_path)
    fees.update(update(rate=.0003))
    with fees.store.connect() as db:
        db.execute("CREATE TRIGGER reject_fee_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT,'disk test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        fees.update(update(rate=.0009))
    assert fees.effective("okx", "spot", "maker")["rate"] == .0003
    assert len(fees.audit()) == 1


def test_concurrent_service_instances_keep_each_separate_profile_update(tmp_path):
    first, second = service(tmp_path), service(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(first.update, update(rate=.0003)), executor.submit(second.update,
                   FeeProfileUpdate(venue="okx", kind="spot", liquidity="taker", rate=.0004))]
        for result in pending:
            result.result()
    assert first.effective("okx", "spot", "maker")["rate"] == .0003
    assert first.effective("okx", "spot", "taker")["rate"] == .0004
    assert len(first.audit()) == 2


def test_resolver_does_not_apply_linear_usdt_reference_to_inverse_or_other_quotes(tmp_path):
    fees = service(tmp_path)
    assert fees.resolve(instrument(), "maker")["rate"] == .0002
    wrong = instrument(quote="USD", settle="BTC", linear=False, native_symbol="BTC/USD:BTC")
    value = fees.resolve(wrong, "maker")
    assert value["rate"] is None and value["evidence_status"] == "out_of_scope"
    assert "contract type outside" in value["conditions"]
    assert fees.resolve(instrument(kind="spot", quote="USDC"), "taker")["rate"] is None


def test_hip3_and_unknown_special_zones_require_separate_fee_evidence(tmp_path):
    fees = service(tmp_path)
    native = instrument("hyperliquid", quote="USDC", settle="USDC", native_symbol="BTC")
    assert fees.resolve(native, "taker")["rate"] == .00045
    hip3 = native.model_copy(update={"native_symbol": "deployer:BTC"})
    assert fees.resolve(hip3, "taker")["rate"] is None
    bybit = instrument("bybit")
    assert fees.effective("bybit", "perpetual", "taker")["rate"] == .00055
    assert fees.resolve(bybit, "taker")["rate"] is None
    fees.update(FeeProfileUpdate(venue="bybit", kind="perpetual", liquidity="taker", rate=.0007,
                                source="user:reviewed applicable market fee"))
    assert fees.resolve(bybit, "taker")["rate"] == .0007
    assert fees.resolve(bybit, "taker")["source_verified"] is False


def test_future_override_is_persisted_but_cannot_be_used_at_earlier_decision(tmp_path):
    fees = service(tmp_path)
    fees.update(update(rate=.0001, updated_at=NOW + 1000))
    value = fees.effective("okx", "spot", "maker")
    assert value["rate"] is None and value["evidence_status"] == "future_evidence"
    assert value["updated_at"] == NOW + 1000
    assert fees.audit()[0]["payload"]["override"]["rate"] == .0001
    assert fees.audit()[0]["payload"]["effective"]["rate"] is None


@pytest.mark.parametrize("rate", [-.10001, 1.0001, float("nan"), float("inf"), True])
def test_invalid_fee_numbers_do_not_enter_profile_or_audit(rate):
    with pytest.raises(ValidationError):
        update(rate=rate)


def test_unknown_source_cannot_be_promoted_to_verified_through_update():
    with pytest.raises(ValidationError):
        FeeProfileUpdate(venue="okx", kind="spot", liquidity="maker", rate=.0001,
                         source="https://example.invalid/claimed-fees", source_verified=True)


def test_http_contract_and_instrument_scoped_loading(tmp_path):
    app = FastAPI()
    app.state.store = Storage(tmp_path)
    item = instrument()
    app.state.collector = SimpleNamespace(instruments={item.id: item})
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/fees")
        assert response.status_code == 200 and len(response.json()["profiles"]) == 112
        scoped = client.get("/api/fees", params={"instrument_id": item.id}).json()
        assert len(scoped["profiles"]) == 2
        assert scoped["profiles"][0]["effective"]["rate"] == .0002
        payload = {"venue": "okx", "kind": "perpetual", "liquidity": "maker", "rate": -.0001,
                   "source": "user:maker rebate", "conditions": "explicit assumption"}
        saved = client.put("/api/fees", json=payload)
        assert saved.status_code == 200
        assert saved.json()["profile"]["effective"]["source_verified"] is False
        assert len(saved.json()["audit"]) == 1
        invalid = client.put("/api/fees", json={**payload, "source_verified": True})
        assert invalid.status_code == 422 and len(client.get("/api/fees").json()["audit"]) == 1
        assert client.get("/api/fees", params={"instrument_id": "missing"}).status_code == 404
        assert client.get("/api/fees", params={"venue": "not-a-venue"}).status_code == 422
        assert client.put("/api/fees", json={"venue": "upbit", "kind": "perpetual",
                                            "liquidity": "maker", "rate": 0}).status_code == 422


def test_catalog_requires_review_evidence_and_https_instead_of_guessing_from_a_rate(tmp_path):
    path = tmp_path / "unreviewed.json"
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    record = raw["reviewed_profiles"][0]
    record["source"] = "unknown source"
    path.write_text(json.dumps({"schema_version": 1, "reviewed_profiles": [record]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="credential-free HTTPS"):
        FeeProfileService(Storage(tmp_path), path, lambda: NOW)
    record["source"] = "https://example.invalid/fees"
    path.write_text(json.dumps({"schema_version": 1, "reviewed_profiles": [record]}), encoding="utf-8")
    with pytest.raises(ValidationError, match="reviewed official publisher"):
        FeeProfileService(Storage(tmp_path), path, lambda: NOW)
