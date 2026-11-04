"""Synthetic schema-shaped fixtures, not captured or complete exchange payloads.

The user described OKX vol24h=contracts, volCcy24h=BTC, ctVal=.01 BTC.
They did not provide an original response. Numbers below are invented to test
that relationship, including the reported 100x scale-error mechanism.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from watch_money.adapters.cex import CexAdapter, _open_interest
from watch_money.adapters.volume_units import UNKNOWN, volume_fields
from watch_money.models import Instrument
from watch_money.strategies import _fill_book

USER_REPORTED_OKX_SYNTHETIC_SHAPE = {
    "instType": "SWAP", "instId": "BTC-USDT-SWAP", "vol24h": "10000", "volCcy24h": "100",
}


def instrument(*, linear=True, kind="perpetual", contract_size=0.01):
    return Instrument(id="test:BTC", venue="test", native_symbol="BTC/USDT:USDT", asset_id="BTC",
                      base="BTC", quote="USDT", settle="USDT", kind=kind,
                      linear=linear, contract_size=contract_size)


@pytest.mark.parametrize("linear", [True, False])
def test_okx_native_currency_volume_prevents_contract_count_mislabel(linear):
    ticker = {"baseVolume": 10000, "quoteVolume": 999999999,
              "info": USER_REPORTED_OKX_SYNTHETIC_SHAPE.copy()}
    assert volume_fields("okx", instrument(linear=linear), ticker) == (
        100, "BTC", "info.volCcy24h:rolling_24h")


def test_installed_ccxt_okx_parser_exposes_the_reported_scale_error_without_network():
    import ccxt

    client = ccxt.okx()
    market = {"id": "BTC-USDT-SWAP", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
              "spot": False, "swap": True, "contract": True, "linear": True, "inverse": False,
              "type": "swap", "contractSize": 0.01}
    client.set_markets([market])
    parsed = client.parse_ticker(USER_REPORTED_OKX_SYNTHETIC_SHAPE.copy(), market)
    # This asserts the observed installed parser behavior, not a correct unit.
    assert parsed["baseVolume"] == 10000
    assert volume_fields("okx", instrument(), parsed, market) == (
        100, "BTC", "info.volCcy24h:rolling_24h")


@pytest.mark.parametrize("linear", [True, False])
@pytest.mark.asyncio
async def test_okx_synthetic_book_contracts_convert_once_and_open_interest_remains_contracts(linear):
    import ccxt

    quote, settle, contract_size = ("USDT", "USDT", 0.01) if linear else ("USD", "BTC", 100)
    raw_id = f"BTC-{quote}-SWAP"
    item = instrument(linear=linear, contract_size=contract_size).model_copy(update={
        "venue": "okx", "quote": quote, "settle": settle, "native_symbol": f"BTC/{quote}:{settle}",
    })
    market = {"id": raw_id, "symbol": item.native_symbol, "base": "BTC", "quote": quote,
              "spot": False, "swap": True, "contract": True, "linear": linear, "inverse": not linear,
              "type": "swap", "contractSize": contract_size}
    client = ccxt.okx()
    client.set_markets([market])
    # Per official OKX book schema, second element is contract count for SWAP.
    # Both this book and the OI row are invented schema-shaped test data.
    native_book = {"ts": "1800000000000", "bids": [["9999", "100", "0", "1"]],
                   "asks": [["10000", "100", "0", "1"]]}
    parsed_book = client.parse_order_book(native_book, item.native_symbol, timestamp=1800000000000)
    adapter = CexAdapter("okx")
    adapter._markets[item.id] = ("okx", market)
    adapter._clients["okx"] = SimpleNamespace(fetch_order_book=AsyncMock(return_value=parsed_book))
    book = await adapter.orderbook(item)
    assert book.quantity_unit == "contracts" and book.asks == [(10000, 100)]
    fill = _fill_book(item, book, "buy", 0.5)
    assert fill["contracts"] == 50 and fill["quote_amount"] == 5000
    native_oi = {"instId": raw_id, "instType": "SWAP", "oi": "10000", "oiCcy": "100",
                 "oiUsd": "5000000", "ts": "1800000000000"}
    parsed_oi = client.parse_open_interest(native_oi, market)
    assert parsed_oi["openInterestAmount"] == 10000
    assert parsed_oi["baseVolume"] == 100
    assert _open_interest("okx", market, parsed_oi) == (10000, "contracts", "openInterestAmount")
    parsed_oi["openInterestAmount"] = None
    assert _open_interest("okx", market, parsed_oi) == (5000000, "USD", "openInterestValue")


def test_okx_spot_contract_and_currency_fields_have_different_semantics():
    value, unit, source = volume_fields("okx", instrument(kind="spot"), {
        "info": {"instType": "SPOT", "vol24h": "100", "volCcy24h": "5000000"}})
    assert (value, unit, source) == (100, "BTC", "info.vol24h:rolling_24h")


def test_okx_linear_fallback_converts_once_using_explicit_contract_size():
    assert volume_fields("okx", instrument(), {"baseVolume": 10000, "info": {"vol24h": "10000"}}) == (
        100, "BTC", "info.vol24h*instrument.contract_size:rolling_24h")


def test_okx_inverse_fallback_keeps_contracts_and_never_uses_current_price():
    ticker = {"baseVolume": 10000, "last": 50000, "info": {"vol24h": "10000"}}
    assert volume_fields("okx", instrument(linear=False, contract_size=100), ticker) == (
        10000, "contracts", "info.vol24h:rolling_24h")


@pytest.mark.parametrize("market", [
    {"info": {"ctValCcy": "USD"}}, {"info": {"ctMult": "10"}}, {"contractSize": 1},
])
def test_okx_conflicting_multiplier_metadata_cannot_create_base_volume(market):
    assert volume_fields("okx", instrument(), {"info": {"vol24h": "10000"}}, market) == (
        10000, "contracts", "info.vol24h:rolling_24h")


@pytest.mark.parametrize("client_id", ["okx", "bybit", "bitget", "mexc", "unknown"])
def test_unreviewed_derivative_schema_does_not_trust_unified_names(client_id):
    assert volume_fields(client_id, instrument(), {"baseVolume": 10000, "quoteVolume": 50000}) == UNKNOWN


@pytest.mark.parametrize("value", [None, "nan", "inf", -1, True, "garbage"])
def test_invalid_native_volumes_remain_unknown(value):
    assert volume_fields("okx", instrument(), {"info": {"volCcy24h": value, "vol24h": value}}) == UNKNOWN


def test_zero_native_volume_does_not_fall_back_to_wrong_unified_amount():
    assert volume_fields("okx", instrument(), {"baseVolume": 999, "info": {"volCcy24h": "0"}}) == (
        0, "BTC", "info.volCcy24h:rolling_24h")


@pytest.mark.parametrize("client_id,linear,raw,expected", [
    ("binanceusdm", True, {"volume": "100"}, (100, "BTC", "info.volume:rolling_24h")),
    ("binanceusdm", True, {"v": "100"}, (100, "BTC", "info.v:rolling_24h")),
    ("binancecoinm", False, {"volume": "10000", "baseVolume": "20"}, (20, "BTC", "info.baseVolume:rolling_24h")),
    ("binancecoinm", False, {"v": "10000", "q": "20"}, (20, "BTC", "info.q:rolling_24h")),
    ("binancecoinm", False, {"volume": "10000"}, (10000, "contracts", "info.volume:rolling_24h")),
    ("gate", True, {"volume_24h_base": "20"}, (20, "BTC", "info.volume_24h_base:rolling_24h")),
    ("deribit", False, {"stats": {"volume": "20"}}, (20, "BTC", "info.stats.volume:rolling_24h")),
])
def test_reviewed_native_fields_preserve_linear_inverse_and_transport_units(client_id, linear, raw, expected):
    assert volume_fields(client_id, instrument(linear=linear), {"baseVolume": 999999, "info": raw}) == expected


def test_spot_unified_quote_volume_retains_original_currency_and_zero():
    assert volume_fields("upbit", instrument(kind="spot"), {"quoteVolume": 0}) == (
        0, "USDT", "quoteVolume:rolling_24h")


def test_wrong_market_or_product_payload_cannot_refresh_another_volume():
    assert volume_fields("okx", instrument(), {"info": {"instType": "SPOT", "volCcy24h": "100"}}) == UNKNOWN
    assert volume_fields("okx", instrument(), {"symbol": "ETH/USDT:USDT", "info": {"volCcy24h": "100"}}) == UNKNOWN
    assert volume_fields("okx", instrument(), {"info": {"instId": "ETH-USDT-SWAP", "volCcy24h": "100"}},
                         {"id": "BTC-USDT-SWAP"}) == UNKNOWN
