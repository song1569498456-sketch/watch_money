"""Asset review uses verified identity evidence, never a major-coin allowlist."""
from copy import deepcopy

import pytest
from watch_money.identity import identity_candidates
from watch_money.models import Instrument
from watch_money.storage import Storage

ADDRESS_A = "0x" + "a1" * 20
ADDRESS_B = "0x" + "b2" * 20


def market(identity, *, venue="alpha", base="BONK", asset_id=None, **values):
    return Instrument(id=identity, venue=venue, native_symbol=f"{base}/USDT",
                      asset_id=asset_id or f"cex:{venue}:catalog:{base}", base=base,
                      quote=values.pop("quote", "USDT"), kind=values.pop("kind", "spot"), **values)


def of_kind(rows, kind):
    return [row for row in rows if row["kind"] == kind]


def test_arbitrary_discovered_assets_are_reviewable_without_major_coin_allowlist():
    markets = [market("a", base="BRETT"), market("b", venue="beta", base="BRETT"),
               market("c", base="TURBO"), market("d", venue="beta", base="TURBO")]
    rows = identity_candidates(markets)
    assert len(of_kind(rows, "identity")) == 4
    candidates = of_kind(rows, "ticker_candidate")
    assert [row["symbols"] for row in candidates] == [["BRETT"], ["TURBO"]]
    assert all(not row["comparison_ready"] and "unverified_asset_mapping" in row["blockers"]
               for row in candidates)


def test_explicit_multi_exchange_mapping_allows_comparison_for_nonmajor_asset():
    markets = [market("a", asset_id="asset:BONK", mapping_verified=True),
               market("b", venue="beta", asset_id="asset:BONK", mapping_verified=True)]
    rows = identity_candidates(markets)
    assert len(rows) == 1
    assert rows[0]["comparison_ready"] and rows[0]["identity_compatible"]
    assert rows[0]["instrument_ids"] == ["a", "b"]


def test_btc_xbt_same_unit_alias_requires_verified_same_mapping():
    first = market("a", base="BTC", asset_id="bitcoin", mapping_verified=True)
    second = market("b", base="XBT", asset_id="bitcoin", venue="beta", mapping_verified=True)
    assert identity_candidates([first, second])[0]["comparison_ready"]
    second.mapping_verified = False
    assert not any(row["comparison_ready"] and len(row["instrument_ids"]) == 2
                   for row in identity_candidates([first, second]))


def test_same_chain_address_groups_without_claiming_unconfirmed_crossvenue_mapping():
    markets = [market("a", chain="base", asset_id=f"base:{ADDRESS_A}", kind="pool"),
               market("b", venue="beta", chain="base", asset_id=f"base:{ADDRESS_A.upper().replace('0X', '0x')}",
                      kind="pool")]
    rows = identity_candidates(markets)
    assert len(rows) == 1 and rows[0]["identity_compatible"]
    assert not rows[0]["comparison_ready"]
    assert rows[0]["blockers"] == ["cross_venue_mapping_requires_confirmation"]
    assert all(item["address"] == ADDRESS_A for item in rows[0]["markets"])


@pytest.mark.parametrize("second_chain,second_address,reason", [
    ("base", ADDRESS_B, "different_native_chain_addresses"),
    ("arbitrum", ADDRESS_A, "different_chains"),
])
def test_same_ticker_different_address_or_chain_is_a_conflicting_candidate(second_chain, second_address, reason):
    rows = identity_candidates([market("a", asset_id=f"base:{ADDRESS_A}", chain="base"),
                                market("b", venue="beta", asset_id=f"{second_chain}:{second_address}",
                                       chain=second_chain)])
    assert len(of_kind(rows, "identity")) == 2
    review = of_kind(rows, "ticker_candidate")[0]
    assert not review["identity_compatible"] and not review["comparison_ready"]
    assert reason in review["blockers"]


def test_verified_mapping_does_not_erase_native_address_conflicts():
    markets = [market("a", asset_id="shared:BONK", native_asset_id=f"base:{ADDRESS_A}",
                      chain="base", mapping_verified=True),
               market("b", venue="beta", asset_id="shared:BONK", native_asset_id=f"base:{ADDRESS_B}",
                      chain="base", mapping_verified=True)]
    rows = identity_candidates(markets)
    assert len(rows) == 1
    assert not rows[0]["comparison_ready"]
    assert "different_native_chain_addresses" in rows[0]["blockers"]


@pytest.mark.parametrize("bases", [("PEPE", "1000PEPE"), ("ETH", "WETH"), ("USDC", "USDC.e")])
def test_wrapped_bridged_or_scaled_symbols_cannot_be_forced_into_same_comparison(bases):
    rows = identity_candidates([market("a", base=bases[0], asset_id="shared", mapping_verified=True),
                                market("b", venue="beta", base=bases[1], asset_id="shared",
                                       mapping_verified=True)])
    assert len(rows) == 1 and not rows[0]["comparison_ready"]
    assert "different_base_symbols_require_unit_review" in rows[0]["blockers"]


def test_numeric_prefix_is_not_stripped_to_generate_ticker_match():
    rows = identity_candidates([market("a", base="PEPE"), market("b", base="1000PEPE", venue="beta")])
    assert len(rows) == 2
    assert not of_kind(rows, "ticker_candidate")


def test_quote_and_contract_size_are_preserved_as_conversion_requirements():
    rows = identity_candidates([market("a", asset_id="BONK", mapping_verified=True, quote="USDT"),
                                market("b", venue="beta", asset_id="BONK", mapping_verified=True,
                                       quote="KRW", kind="perpetual", contract_size=1000, linear=False)])
    assert len(rows) == 1 and rows[0]["comparison_ready"]  # Identity gate only.
    assert rows[0]["quotes"] == ["KRW", "USDT"]
    assert rows[0]["requirements"] == ["contract_quantity_to_base_conversion", "inverse_contract_conversion",
                                       "quote_conversion_with_timestamped_fx"]
    assert rows[0]["markets"][1]["contract_size"] == 1000
    assert rows[0]["markets"][1]["linear"] is False


def test_chain_metadata_conflict_and_malformed_address_do_not_gain_trust():
    wrong_chain = market("a", asset_id=f"base:{ADDRESS_A}", chain="arbitrum", mapping_verified=True)
    malformed = market("b", asset_id="base:BONK", chain="base")
    rows = identity_candidates([wrong_chain, malformed])
    first = next(row for row in rows if row["instrument_ids"] == ["a"])
    second = next(row for row in rows if row["instrument_ids"] == ["b"])
    assert not first["comparison_ready"] and "chain_metadata_conflict" in first["blockers"]
    assert not second["identity_compatible"] and "invalid_native_chain_address" in second["blockers"]


def test_solana_address_case_is_not_normalized():
    mint = "So11111111111111111111111111111111111111112"
    rows = identity_candidates([market("a", asset_id=f"solana:{mint}", chain="solana"),
                                market("b", asset_id=f"solana:{mint.lower()}", chain="solana", venue="beta")])
    assert len(of_kind(rows, "identity")) == 2
    assert not of_kind(rows, "ticker_candidate")[0]["comparison_ready"]


def test_function_is_pure_deterministic_and_keeps_singletons():
    markets = [market("z", base="NEWCOIN"), market("a", base="BONK", active=False)]
    before = deepcopy(markets)
    assert identity_candidates(markets) == identity_candidates(reversed(markets))
    assert markets == before
    rows = identity_candidates(markets)
    assert sorted(item for row in rows for item in row["instrument_ids"]) == ["a", "z"]
    assert "contains_inactive_markets" in next(row for row in rows if row["symbols"] == ["BONK"])["warnings"]


def test_duplicate_catalog_id_is_rejected_instead_of_overwriting_evidence():
    with pytest.raises(ValueError, match="Duplicate instrument"):
        identity_candidates([market("same"), market("same", venue="beta")])


def test_native_identity_survives_mapping_restart_resave_and_remapping(tmp_path):
    store = Storage(tmp_path)
    original = market("a", asset_id=f"base:{ADDRESS_A}", chain="base")
    store.save_instruments([original])
    assert original.native_asset_id is None  # Persistence must not mutate caller input.
    assert store.instruments()["a"].native_asset_id == f"base:{ADDRESS_A}"
    store.map_asset("a", "confirmed:BONK")
    restored = Storage(tmp_path)
    mapped = restored.instruments()["a"]
    assert mapped.asset_id == "confirmed:BONK" and mapped.mapping_verified
    assert mapped.native_asset_id == f"base:{ADDRESS_A}"
    restored.save_instruments([mapped])
    restored.map_asset("a", "reviewed:BONK")
    final = Storage(tmp_path).instruments()["a"]
    assert final.asset_id == "reviewed:BONK"
    assert final.native_asset_id == f"base:{ADDRESS_A}"
    row = identity_candidates([final])[0]
    assert row["markets"][0]["address"] == ADDRESS_A
    assert row["markets"][0]["native_asset_id"] == f"base:{ADDRESS_A}"
