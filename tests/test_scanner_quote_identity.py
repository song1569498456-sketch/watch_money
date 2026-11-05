"""Reliable scanner price paths require canonical, current DEX quote evidence."""
import pytest
from watch_money.models import Instrument, MarketSnapshot
from watch_money.scanner import scan_markets

NOW = 1_800_000_000_000
BASE = "base:0x" + "11" * 20
QUOTE = "base:0x" + "22" * 20


def market(identity, venue, **values):
    return Instrument(id=identity, venue=venue, native_symbol="SMALL/USDT", asset_id=BASE,
                      native_asset_id=BASE, base="SMALL", quote="USDT", kind="pool", mechanism="amm", chain="base",
                      mapping_verified=True, quote_asset_id=QUOTE, native_quote_asset_id=QUOTE,
                      quote_identity_source="evm-rpc:base:uniswap:v3:0x" + "33" * 20,
                      quote_identity_at=NOW).model_copy(update=values)


def scan(markets):
    snapshots = [MarketSnapshot(instrument_id=item.id, venue=item.venue, source="synthetic:pool-prices",
                                source_at=NOW, received_at=NOW, bid=99 if index == 0 else 105,
                                ask=100 if index == 0 else 106) for index, item in enumerate(markets)]
    return scan_markets(markets, snapshots, NOW, 60)


def test_same_canonical_quote_identity_can_enter_indicative_reliable_price_ranking():
    result = scan([market("a", "uniswap"), market("b", "pancakeswap")])
    best = result["groups"][0]["best_spread_candidate"]
    assert best is not None and best["price_rankable"] and best["spread_rate"] == pytest.approx(.05)
    assert set(best["quote_assets"].values()) == {QUOTE}
    assert "quote_token_contract_identity_not_verified" not in best["requirements"]
    assert best["net_profit"] is None and not best["executable"]


@pytest.mark.parametrize("change", [
    {"quote_asset_id": None, "native_quote_asset_id": None},
    {"native_quote_asset_id": "base:0x" + "44" * 20},
    {"quote_identity_at": NOW - 60001}, {"quote_identity_at": NOW + 1},
    {"quote_identity_source": "verified"}, {"chain": None},
])
def test_unknown_conflicting_stale_or_untraceable_quote_excludes_both_directions(change):
    result = scan([market("a", "uniswap"), market("b", "pancakeswap", **change)])
    assert result["groups"][0]["best_spread_candidate"] is None
    assert all(not row["price_rankable"] and not row["reverse_price_rankable"] for row in result["candidates"])
    assert all(row["spread_rate"] is not None for row in result["candidates"])  # labelled diagnostics survive


def test_two_real_but_different_quote_tokens_need_explicit_asset_fx_even_with_same_ticker():
    other_quote = "base:0x" + "44" * 20
    result = scan([market("a", "uniswap"), market("b", "pancakeswap",
                                                          quote_asset_id=other_quote, native_quote_asset_id=other_quote)])
    assert result["groups"][0]["best_spread_candidate"] is None
    assert all("quote_asset_fx_not_provided" in row["missing"] for row in result["candidates"])


def test_cex_quote_ledger_and_dex_token_are_not_implicitly_one_to_one():
    cex = market("a", "okx", kind="spot", mechanism="orderbook", chain=None,
                 native_asset_id="okx:SMALL", quote_asset_id=None, native_quote_asset_id=None)
    result = scan([cex, market("b", "uniswap")])
    assert result["groups"][0]["best_spread_candidate"] is None
    assert all("quote_asset_fx_not_provided" in row["missing"] for row in result["candidates"])
