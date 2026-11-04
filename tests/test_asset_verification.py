"""Official-schema fixtures exercise mapping reviews; none are live market evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from watch_money import asset_verification as module
from watch_money import identity as identity_module
from watch_money.asset_verification import (
    AssetVerificationService,
    EvidenceInput,
    parse_public_metadata,
    router,
)
from watch_money.identity import identity_candidates
from watch_money.models import Instrument, now_ms
from watch_money.storage import Storage

ADDRESS = "0x" + "a1" * 20
OTHER_ADDRESS = "0x" + "b2" * 20


class Clock:
    def __init__(self):
        self.at = now_ms()

    def __call__(self):
        return self.at


def market(identity, venue="gate", *, base="PEPE", kind="spot", native=None, **values):
    native = native or f"cex:{venue}:{venue}:{base}"
    raw_currency_id = values.pop("native_currency_id", native.split(":")[-1] if native.startswith("cex:") else None)
    return Instrument(id=identity, venue=venue, native_symbol=f"{base}/USDT", asset_id=native,
                      native_asset_id=native, native_currency_id=raw_currency_id, base=base, quote="USDT", kind=kind, **values)


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(module, "now_ms", clock)
    monkeypatch.setattr(identity_module, "now_ms", clock)
    store = Storage(tmp_path)
    items = [market("gate-spot"), market("gate-quote"), market("gate-perp", kind="perpetual"),
             market("bitget-perp", "bitget", kind="perpetual"), market("bitget-spot", "bitget"),
             market("onchain", "uniswap", kind="pool", native=f"ethereum:{ADDRESS}", chain="ethereum")]
    store.save_instruments(items)
    collector = SimpleNamespace(instruments=store.instruments())
    service = AssetVerificationService(store, collector, clock=clock)
    return SimpleNamespace(clock=clock, store=store, collector=collector, service=service, items=items)


def manual(env, instrument_id="gate-spot", canonical=f"ethereum:{ADDRESS}", **values):
    inst = env.store.instruments()[instrument_id]
    return EvidenceInput(instrument_id=instrument_id, native_asset_id=inst.native_asset_id,
                         canonical_asset_id=canonical, source_url="https://evidence-fixture.invalid/token-spec",
                         source_field="listing.baseCurrency.chainContract", observed_at=env.clock(),
                         expires_at=values.pop("expires_at", env.clock() + 86400000),
                         note="Synthetic human-reviewed source; not a live official verification", **values)


def gate_response(address=ADDRESS):
    return [{"currency": "PEPE", "name": "Fixture token", "chain": "ETH",
             "chains": [{"name": "ETH", "addr": address}]}]


def bitget_response(address=ADDRESS):
    return {"code": "00000", "data": [{"coin": "PEPE", "coinId": "fixture-id",
            "chains": [{"chain": "ERC20", "contractAddress": address}]}]}


def kucoin_response(currency="PEPE", address=ADDRESS, chains=None):
    return {"code": "200000", "data": [{"currency": currency, "name": "Renamable display name",
            "chains": chains if chains is not None else [{"chainId": "eth", "chainName": "ERC20",
                                                          "contractAddress": address}]}]}


def coinbase_response(currency="PEPE", address=ADDRESS):
    return [{"id": currency, "name": "Synthetic Exchange currency", "default_network": "ethereum",
             "supported_networks": [{"id": "ethereum", "contract_address": address}]}]


def public_client():
    def handle(request):
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        if request.url.host == "api.gateio.ws":
            assert request.url.path == "/api/v4/spot/currencies"
            return httpx.Response(200, json=gate_response())
        assert request.url.host == "api.bitget.com" and request.url.path == "/api/v2/spot/public/coins"
        return httpx.Response(200, json=bitget_response())
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


async def test_public_bulk_import_preview_apply_restart_and_revoke_form_traceable_closed_loop(env):
    async with public_client() as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate", "bitget"])
    assert imported["imported"] == 4 and imported["mappings_changed"] is False
    assert all(not item.mapping_verified for item in env.collector.instruments.values())
    preview = env.service.preview(imported["evidence_ids"])
    assert preview["ready"] == 1 and preview["blocked"] == 0
    assert preview["proposals"][0]["venues"] == ["bitget", "gate", "uniswap"]
    applied = env.service.apply(imported["evidence_ids"], preview["preview_token"])
    assert applied["applied_markets"] == 4
    assert all(item.mapping_verified and item.mapping_evidence_ids for item in env.collector.instruments.values()
               if item.kind != "perpetual")
    assert all(not item.mapping_verified for item in env.collector.instruments.values() if item.kind == "perpetual")
    assert env.collector.instruments["gate-perp"].native_asset_id == "cex:gate:gate:PEPE"
    restored = Storage(env.store.directory).instruments()
    assert restored["gate-spot"].mapping_evidence_status == "verified"
    assert any(row["comparison_ready"] and len(row["instrument_ids"]) == 4
               for row in identity_candidates(restored.values(), as_of=env.clock()))
    status = env.service.status()
    assert status["coverage"]["traceable_verified_markets"] == 4
    assert status["coverage"]["source_covered_markets"] == 3
    assert all(source["response_rows"] == 1 and source["error_type"] is None for source in status["sources"]
               if source["venue"] in {"gate", "bitget"})
    assert all(source["state"] == "not_requested" for source in status["sources"]
               if source["venue"] in {"kucoin", "coinbase"})
    revoked = env.service.revoke([imported["evidence_ids"][0]], "Synthetic correction: withdraw reviewed proof")
    assert len(revoked["affected_instrument_ids"]) == 4
    assert all(not item.mapping_verified for item in env.collector.instruments.values())
    assert any(row["action"] == "asset_evidence_revoke" for row in env.store.audit())


@pytest.mark.parametrize("chains", [
    [{"name": "ETH", "addr": ADDRESS}, {"name": "BSC", "addr": ADDRESS}],
    [{"name": "ETH", "addr": ADDRESS}, {"name": "ETH", "addr": OTHER_ADDRESS}],
    [{"name": "ETH", "addr": ADDRESS}, {"name": "BTC", "addr": ""}],
    [{"name": "UNKNOWN", "addr": ADDRESS}], [],
])
def test_multichain_wrapped_unknown_or_missing_contract_never_chooses_first_network(chains):
    rows = parse_public_metadata("gate", [{"currency": "PEPE", "chains": chains}])
    assert rows[0]["canonical_asset_id"] is None and rows[0]["blockers"]


def test_bitget_and_gate_normalize_exact_same_evm_contract_without_symbol_global_mapping():
    gate = parse_public_metadata("gate", gate_response(ADDRESS.upper().replace("0X", "0x")))[0]
    bitget = parse_public_metadata("bitget", bitget_response())[0]
    assert gate["canonical_asset_id"] == bitget["canonical_asset_id"] == f"ethereum:{ADDRESS}"
    assert gate["source_digest"] != bitget["source_digest"]  # Preserve source-specific fields.


async def test_kucoin_fixed_currency_id_links_renamed_nonmajor_coin_to_contract_not_display_ticker(env):
    # Synthetic official schema: XRB is the immutable currency id; NANO is only
    # the local display/base symbol. This is not a live KuCoin listing assertion.
    renamed = market("kucoin-renamed", "kucoin", base="NANO", native="cex:kucoin:kucoin:XRB")
    other = market("gate-nano", base="NANO")
    env.store.save_instruments([renamed, other])
    calls = []

    def handle(request):
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        calls.append(str(request.url))
        if request.url.host == "api.kucoin.com":
            assert request.url.path == "/api/v3/currencies" and not request.url.query
            return httpx.Response(200, json=kucoin_response("XRB", OTHER_ADDRESS))
        assert request.url.host == "api.gateio.ws"
        return httpx.Response(200, json=[{"currency": "NANO", "chains": [{"name": "ETH", "addr": OTHER_ADDRESS}]}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate", "kucoin"])
    assert len(calls) == 2 and imported["imported"] == 2
    records = env.service._records()
    kucoin = next(row for row in records if row["venue"] == "kucoin")
    assert kucoin["native_currency_id"] == kucoin["currency_id"] == "XRB"
    assert kucoin["canonical_asset_id"] == f"ethereum:{OTHER_ADDRESS}"
    assert kucoin["expires_at"] - kucoin["observed_at"] == 7 * 86400000  # No TTL widening.
    preview = env.service.preview(imported["evidence_ids"])
    assert preview["ready"] == 1 and preview["proposals"][0]["symbols"] == ["NANO"]
    env.service.apply(imported["evidence_ids"], preview["preview_token"])
    assert env.collector.instruments["kucoin-renamed"].mapping_verified
    assert env.collector.instruments["gate-nano"].mapping_verified
    assert env.collector.instruments["kucoin-renamed"].native_currency_id == "XRB"


async def test_coinbase_exchange_contract_registry_does_not_spread_to_advanced_trade_or_derivatives(env):
    exchange = market("cb-exchange", "coinbase", native="cex:coinbase:coinbaseexchange:PEPE")
    advanced = market("cb-advanced", "coinbase")
    derivative = market("cb-derivative", "coinbase", native="cex:coinbase:coinbaseexchange:PEPE", kind="perpetual")
    env.store.save_instruments([exchange, advanced, derivative])
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=coinbase_response()))) as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["coinbase"])
    direct = [row for row in env.service._records() if row["evidence_kind"] == "public_contract_metadata"]
    assert [row["instrument_id"] for row in direct] == ["cb-exchange"]
    assert direct[0]["source_field"].endswith("supported_networks")
    source = imported["sources"][0]
    assert source["scope_eligible_markets"] == source["matched_markets"] == 1
    assert source["scope_reasons"] == {"native_client_out_of_source_scope": 1, "product_out_of_source_scope": 1}
    coverage = next(row for row in env.service.status()["platform_coverage"] if row["venue"] == "coinbase")
    assert coverage["active_markets"] == 3 and coverage["source_covered_markets"] == 1
    assert coverage["scope_rejected_markets"] == 2
    assert not env.collector.instruments["cb-advanced"].mapping_verified


async def test_all_spot_currency_sources_reject_missing_original_id_and_perpetual_product_scope(env):
    missing = market("missing-base-id", native_currency_id=None)
    mismatched = market("wrong-base-id", native_currency_id="OTHER")
    kucoin_spot = market("kc-spot", "kucoin")
    kucoin_future = market("kc-perp", "kucoin", native="cex:kucoin:kucoinfutures:PEPE", kind="perpetual")
    env.store.save_instruments([missing, mismatched, kucoin_spot, kucoin_future])

    def handle(request):
        payload = kucoin_response() if request.url.host == "api.kucoin.com" else gate_response()
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate", "kucoin"])
    records = env.service._records()
    attached = {row["instrument_id"] for row in records}
    assert "kc-spot" in attached
    assert not {"missing-base-id", "wrong-base-id", "gate-perp", "kc-perp"}.intersection(attached)
    gate = next(row for row in imported["sources"] if row["venue"] == "gate")
    assert gate["scope_reasons"]["original_currency_id_missing"] == 1
    assert gate["scope_reasons"]["original_currency_id_conflicts_with_native_identity"] == 1
    assert gate["scope_reasons"]["product_out_of_source_scope"] == 1
    kucoin = next(row for row in imported["sources"] if row["venue"] == "kucoin")
    assert kucoin["scope_reasons"] == {"native_client_out_of_source_scope": 1}


async def test_original_currency_ids_are_case_sensitive_and_not_replaced_by_unified_base(env):
    item = market("case-sensitive-id", "kucoin", base="PEPE", native="cex:kucoin:kucoin:pepe")
    env.store.save_instruments([item])
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=kucoin_response("PEPE")))) as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["kucoin"])
    assert imported["imported"] == 0
    assert imported["sources"][0]["scope_eligible_markets"] == 1
    assert imported["sources"][0]["unmatched_currency_markets"] == 1


@pytest.mark.parametrize("chains", [
    [{"chainId": "eth", "chainName": "ERC20", "contractAddress": ADDRESS},
     {"chainId": "base", "chainName": "BASE", "contractAddress": ADDRESS}],
    [{"chainId": "base", "chainName": "ERC20", "contractAddress": ADDRESS}],
    [{"chainId": "unknown", "chainName": "ERC20", "contractAddress": ADDRESS}],
    [{"chainId": "eth", "chainName": "ERC20", "contractAddress": ""}],
    [{"chainId": "eth", "chainName": "ERC20", "contractAddress": "0x" + "0" * 40}],
])
def test_kucoin_schema_never_selects_first_chain_or_uses_conflicting_network_labels(chains):
    parsed = parse_public_metadata("kucoin", kucoin_response(chains=chains))[0]
    assert parsed["canonical_asset_id"] is None and parsed["blockers"]


def test_coinbase_missing_contract_does_not_scrape_explorer_url_or_convertible_symbol():
    payload = coinbase_response(address="")
    payload[0]["convertible_to"] = ["USDC"]
    payload[0]["supported_networks"][0]["crypto_address_link"] = "https://etherscan.io/token/" + ADDRESS + "?a={{address}}"
    parsed = parse_public_metadata("coinbase", payload)[0]
    assert parsed["canonical_asset_id"] is None
    assert "network_or_contract_identity_incomplete" in parsed["blockers"]


def test_old_public_evidence_without_original_id_is_downgraded_on_service_restart(env):
    item = env.store.instruments()["gate-spot"]
    old = {"id": "synthetic-old-public-evidence", "instrument_id": item.id, "native_asset_id": item.native_asset_id,
           "venue": "gate", "canonical_asset_id": f"ethereum:{ADDRESS}", "catalog_fingerprint": module.catalog_fingerprint(item),
           "evidence_kind": "public_contract_metadata", "source_url": module.SOURCES["gate"]["endpoint"],
           "source_field": "synthetic-old-schema.chains", "observed_at": env.clock(),
           "expires_at": env.clock() + 86400000, "relationship": "same_asset", "unit_multiplier": 1,
           "blockers": []}  # Deliberately no original native_currency_id provenance.
    env.service._insert([old])
    env.store.map_assets([item.id], old["canonical_asset_id"], evidence={item.id: {
        "status": "verified", "canonical_asset_id": old["canonical_asset_id"],
        "catalog_fingerprint": module.catalog_fingerprint(item), "evidence_ids": [old["id"]],
        "verified_at": env.clock(), "expires_at": old["expires_at"]}})
    env.collector.instruments = env.store.instruments()
    assert env.collector.instruments[item.id].mapping_verified
    restarted = AssetVerificationService(env.store, env.collector, clock=env.clock)
    assert not env.collector.instruments[item.id].mapping_verified
    assert restarted._records()[0]["status"] == "scope_unverified"
    assert any(row["action"] == "asset_public_scope_downgrade" for row in env.store.audit())


async def test_new_sources_network_errors_have_zero_real_verified_coverage(env):
    env.store.save_instruments([market("kc", "kucoin"),
                               market("cb", "coinbase", native="cex:coinbase:coinbaseexchange:PEPE")])
    requests = []

    def fail(request):
        requests.append(str(request.url))
        raise httpx.ProxyError("synthetic proxy rejection, not a real API response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["kucoin", "coinbase"])
    assert requests == [module.SOURCES["kucoin"]["endpoint"], module.SOURCES["coinbase"]["endpoint"]]
    assert imported["imported"] == 0 and not imported["evidence_ids"]
    assert all(row["error_type"] == "ProxyError" and row["response_rows"] == 0 for row in imported["sources"])
    assert env.service.status()["coverage"]["traceable_verified_markets"] == 0


async def test_public_metadata_does_not_propagate_by_ticker_to_other_venues_or_scaled_units(env):
    other = market("unproven", "bybit")
    scaled = market("scaled", base="1000PEPE", kind="perpetual")
    env.store.save_instruments([other, scaled])
    async with public_client() as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate"])
    records = env.service._records()
    assert "unproven" not in {row["instrument_id"] for row in records}
    assert "scaled" not in {row["instrument_id"] for row in records}
    assert imported["sources"][0]["matched_markets"] == 2
    scaled_metadata = parse_public_metadata("gate", [{"currency": "1000PEPE", "chains": [{"name": "ETH", "addr": ADDRESS}]}])
    assert "base_unit_scale_requires_review" in scaled_metadata[0]["blockers"]


async def test_network_failure_is_explicit_not_a_successful_or_simulated_import(env):
    def fail(request):
        raise httpx.ProxyError("fixture secret must not appear in status")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        env.service.client, env.service._owns_client = client, False
        result = await env.service.public_refresh(["gate", "bitget"])
    assert result["imported"] == 0 and not result["evidence_ids"]
    status = env.service.status()
    assert status["coverage"]["source_covered_markets"] == 0
    assert all(source["state"] == "error" and source["error_type"] == "ProxyError" and source["response_rows"] == 0
               for source in status["sources"] if source["venue"] in {"gate", "bitget"})
    assert "fixture secret" not in str(status)


def test_manual_revision_requires_evidence_then_supersedes_conflicts_and_is_audited(env):
    first = env.service.add_evidence([manual(env)])
    other = env.service.add_evidence([manual(env, canonical=f"ethereum:{OTHER_ADDRESS}")])
    assert env.service.preview(other["evidence_ids"])["blocked"] == 1
    revised = env.service.add_evidence([manual(env, canonical=f"ethereum:{OTHER_ADDRESS}",
                                             supersedes_evidence_ids=first["evidence_ids"] + other["evidence_ids"])])
    preview = env.service.preview(revised["evidence_ids"])
    assert preview["ready"] == 1
    env.service.apply(revised["evidence_ids"], preview["preview_token"])
    assert env.collector.instruments["gate-spot"].asset_id == f"ethereum:{OTHER_ADDRESS}"
    assert sum(row["status"] == "superseded" for row in env.service._records()) == 2


def test_new_conflicting_evidence_immediately_downgrades_existing_confirmed_mapping(env):
    first = env.service.add_evidence([manual(env)])
    preview = env.service.preview(first["evidence_ids"])
    env.service.apply(first["evidence_ids"], preview["preview_token"])
    assert env.collector.instruments["gate-spot"].mapping_verified
    conflict = env.service.add_evidence([manual(env, canonical=f"ethereum:{OTHER_ADDRESS}")])
    current = env.collector.instruments["gate-spot"]
    assert not current.mapping_verified and current.mapping_evidence_status == "conflicting_evidence"
    assert not identity_candidates([current], as_of=env.clock())[0]["comparison_ready"]
    env.service.revoke(conflict["evidence_ids"], "Discard contradictory manual fixture after review")
    assert env.collector.instruments["gate-spot"].mapping_verified  # Original approved active proof survives.


def test_preview_token_is_invalid_after_catalog_or_mapping_change_no_partial_write(env):
    imported = env.service.add_evidence([manual(env, "gate-spot"), manual(env, "bitget-perp")])
    preview = env.service.preview(imported["evidence_ids"])
    changed = env.store.instruments()["gate-spot"].model_copy(update={"contract_size": 1000})
    env.store.save_instruments([changed])
    with pytest.raises(ValueError, match="Preview changed"):
        env.service.apply(imported["evidence_ids"], preview["preview_token"])
    assert not env.store.instruments()["bitget-perp"].mapping_verified


def test_batch_with_one_ready_group_and_one_conflict_does_not_partially_apply(env):
    imported = env.service.add_evidence([manual(env, "gate-spot"),
        manual(env, "bitget-perp", canonical=f"ethereum:{OTHER_ADDRESS}", relationship="wrapped")])
    preview = env.service.preview(imported["evidence_ids"])
    assert preview["ready"] == preview["blocked"] == 1
    with pytest.raises(ValueError, match="no mappings changed"):
        env.service.apply(imported["evidence_ids"], preview["preview_token"])
    assert all(not item.mapping_verified for item in env.store.instruments().values())


def test_concurrent_apply_of_same_preview_has_only_one_successful_review_commit(env):
    imported = env.service.add_evidence([manual(env, "gate-spot"), manual(env, "bitget-perp")])
    preview = env.service.preview(imported["evidence_ids"])

    def apply_once(_):
        try:
            env.service.apply(imported["evidence_ids"], preview["preview_token"])
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(apply_once, range(2)))
    assert sorted(outcomes) == [False, True]
    assert sum(row["action"] == "asset_verification_apply" for row in env.store.audit()) == 1


def test_database_write_failure_rolls_back_every_mapping_in_batch(env, monkeypatch):
    imported = env.service.add_evidence([manual(env, "gate-spot"),
                                        manual(env, "bitget-perp", canonical=f"ethereum:{OTHER_ADDRESS}")])
    preview = env.service.preview(imported["evidence_ids"])
    original = env.store.map_assets
    calls = []

    def failing_write(*args, **kwargs):
        original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            raise OSError("Synthetic database fault after second staged mapping")

    monkeypatch.setattr(env.store, "map_assets", failing_write)
    with pytest.raises(OSError):
        env.service.apply(imported["evidence_ids"], preview["preview_token"])
    assert all(not item.mapping_verified for item in env.store.instruments().values())
    assert not any(row["action"] == "asset_verification_apply" for row in env.store.audit())


@pytest.mark.parametrize("relationship,multiplier", [("wrapped", 1), ("bridged", 1), ("same_asset", 1000)])
def test_wrapped_bridged_and_scaled_review_is_not_approved_as_plain_asset_equivalence(env, relationship, multiplier):
    imported = env.service.add_evidence([manual(env, relationship=relationship, unit_multiplier=multiplier)])
    preview = env.service.preview(imported["evidence_ids"])
    assert preview["blocked"] == 1
    with pytest.raises(ValueError, match="unresolved"):
        env.service.apply(imported["evidence_ids"], preview["preview_token"])


def test_cannot_map_different_native_addresses_even_with_explicit_same_manual_target(env):
    second = market("other-chain-token", "curve", native=f"ethereum:{OTHER_ADDRESS}", chain="ethereum", kind="pool")
    env.store.save_instruments([second])
    imported = env.service.add_evidence([manual(env, "onchain"), manual(env, "other-chain-token")])
    preview = env.service.preview(imported["evidence_ids"])
    assert "different_native_chain_addresses" in preview["proposals"][0]["blockers"]
    assert preview["blocked"] == 1


def test_expiry_immediately_blocks_cached_instruments_and_future_review_cannot_enter_replay(env):
    imported = env.service.add_evidence([manual(env, "gate-spot", expires_at=env.clock() + 1000),
                                       manual(env, "bitget-perp", expires_at=env.clock() + 1000)])
    preview = env.service.preview(imported["evidence_ids"])
    env.service.apply(imported["evidence_ids"], preview["preview_token"])
    cached = list(env.collector.instruments.values())
    matched = [item for item in cached if item.mapping_evidence_ids]
    before_review = identity_candidates(matched, as_of=env.clock() - 1)[0]
    assert "mapping_not_known_as_of" in before_review["blockers"]
    env.clock.at += 1001
    expired = identity_candidates(matched, as_of=env.clock())[0]
    assert not expired["comparison_ready"] and "mapping_evidence_expired" in expired["blockers"]
    # Passing an earlier replay time cannot resurrect evidence expired today.
    assert not identity_candidates(matched, as_of=env.clock() - 500)[0]["comparison_ready"]
    assert env.service.status()["coverage"]["expired_markets"] == 2


async def test_refresh_changed_source_supersedes_proof_and_downgrades_current_collector(env):
    async with public_client() as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate", "bitget"])
    preview = env.service.preview(imported["evidence_ids"])
    env.service.apply(imported["evidence_ids"], preview["preview_token"])
    env.clock.at += 100
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=gate_response(OTHER_ADDRESS)))) as client:
        env.service.client = client
        await env.service.public_refresh(["gate"])
    assert all(not item.mapping_verified for item in env.collector.instruments.values())
    assert any(item.mapping_evidence_status == "evidence_changed" for item in env.collector.instruments.values())


def test_input_batch_is_atomic_and_duplicate_import_is_idempotent(env):
    item = manual(env)
    before = deepcopy(item)
    first = env.service.add_evidence([item])
    second = env.service.add_evidence([item])
    assert first["evidence_ids"] == second["evidence_ids"] and second["imported"] == 0
    assert item == before
    wrong_native = manual(env, "bitget-perp").model_copy(update={"native_asset_id": "wrong"})
    with pytest.raises(ValueError, match="native identity"):
        env.service.add_evidence([manual(env, "gate-quote"), wrong_native])
    assert len(env.service._records()) == 1


def test_legacy_mappings_are_counted_separately_and_not_given_fabricated_proof(env):
    env.store.map_asset("gate-spot", "manually-mapped:PEPE")
    status = env.service.status()
    assert status["coverage"]["legacy_verified_markets"] == 1
    assert status["coverage"]["traceable_verified_markets"] == 0
    assert not env.store.instruments()["gate-spot"].mapping_evidence_ids


async def test_market_coverage_uses_active_denominator_without_erasing_inactive_evidence(env):
    async with public_client() as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate", "bitget"])
    preview = env.service.preview(imported["evidence_ids"])
    env.service.apply(imported["evidence_ids"], preview["preview_token"])
    current = env.store.instruments()
    env.store.save_instruments([current[identity].model_copy(update={"active": False}) for identity in
                                ["gate-quote", "bitget-perp", "onchain"]])
    coverage = env.service.status()["coverage"]
    assert coverage["catalog_markets"] == 6 and coverage["active_markets"] == 3
    assert coverage["inactive_markets"] == 3 and coverage["market_count_scope"] == "active_catalog"
    assert coverage["verified_markets"] == coverage["traceable_verified_markets"] == 2
    assert coverage["unverified_markets"] == 1 and coverage["legacy_verified_markets"] == 0
    assert coverage["source_covered_markets"] == 2
    assert coverage["source_observed_markets"] == 3 and coverage["evidence_records"] == 4
    env.store.map_asset("gate-spot", "legacy:PEPE")
    coverage = env.service.status()["coverage"]
    assert coverage["verified_markets"] == 2 and coverage["active_markets"] == 3
    assert coverage["legacy_verified_markets"] == coverage["traceable_verified_markets"] == 1


def test_routes_support_manual_batch_preview_apply_revoke_and_reject_credentials(env):
    app = FastAPI()
    app.state.asset_verification = env.service
    app.include_router(router)
    with TestClient(app) as client:
        payload = [manual(env, "gate-spot").model_dump(), manual(env, "bitget-perp").model_dump()]
        imported = client.post("/api/assets/verification/evidence", json={"items": payload}).json()
        preview = client.post("/api/assets/verification/preview", json={"evidence_ids": imported["evidence_ids"]}).json()
        applied = client.post("/api/assets/verification/apply", json={"evidence_ids": imported["evidence_ids"],
                                                                   "preview_token": preview["preview_token"]})
        assert applied.status_code == 200 and applied.json()["applied_markets"] == 2
        assert client.get("/api/assets/verification").json()["coverage"]["traceable_verified_markets"] == 2
        revoked = client.post("/api/assets/verification/revoke", json={"evidence_ids": imported["evidence_ids"],
                                                                     "reason": "fixture correction"})
        assert revoked.status_code == 200 and len(revoked.json()["affected_instrument_ids"]) == 2
        payload[0]["source_url"] = "https://user:secret@example.invalid/token?apiKey=secret"
        assert client.post("/api/assets/verification/evidence", json={"items": payload}).status_code == 422
        assert client.post("/api/assets/verification/public-refresh", json={"venues": ["private-account-api"]}).status_code == 422


async def test_bulk_small_asset_catalog_verifies_distinct_contracts_without_ticker_allowlist(env):
    """200 synthetic assets, two official-schema sources; never live coverage evidence."""
    items, gate, bitget = [], [], []
    for index in range(200):
        symbol = f"SMALL{index}"
        address = f"0x{index + 1:040x}"
        items.extend([market(f"gate-{index}", base=symbol), market(f"bitget-{index}", "bitget", base=symbol)])
        gate.append({"currency": symbol, "chains": [{"name": "ETH", "addr": address}]})
        bitget.append({"coin": symbol, "chains": [{"chain": "ERC20", "contractAddress": address}]})
    # Same display ticker with different contracts must not become a cross-venue group.
    items.extend([market("conflict-gate", base="CLASH"), market("conflict-bitget", "bitget", base="CLASH")])
    gate.append({"currency": "CLASH", "chains": [{"name": "ETH", "addr": ADDRESS}]})
    bitget.append({"coin": "CLASH", "chains": [{"chain": "ERC20", "contractAddress": OTHER_ADDRESS}]})
    env.store.save_instruments(items)
    env.collector.instruments = env.store.instruments()
    def handle(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json=gate if request.url.host == "api.gateio.ws" else {"code": "00000", "data": bitget})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        env.service.client, env.service._owns_client = client, False
        imported = await env.service.public_refresh(["gate", "bitget"])
    preview = env.service.preview(imported["evidence_ids"])
    ready_ids = [identity for proposal in preview["proposals"] if proposal["status"] == "ready"
                 for identity in proposal["evidence_ids"]]
    checked = env.service.preview(ready_ids)
    env.service.apply(ready_ids, checked["preview_token"])
    groups = identity_candidates(env.collector.instruments.values(), as_of=env.clock())
    comparable = [row for row in groups if row["kind"] == "identity" and row["comparison_ready"] and len(row["venues"]) > 1]
    assert len(comparable) == 200
    assert all(len(row["instrument_ids"]) == 2 for row in comparable)
    assert not any("CLASH" in row["symbols"] for row in comparable)
    assert env.service.status()["coverage"]["traceable_verified_markets"] >= 400
