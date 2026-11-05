"""Independent mock-RPC ABI fixtures. These are not recorded or verified live data."""

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from watch_money.adapters.onchain import (
    SELECTORS,
    NativePoolReader,
    OnchainError,
    UnsupportedPool,
    amount_to_raw,
    supported_deployments,
)

POOL = "0x0000000000000000000000000000000000000010"
T0 = "0x0000000000000000000000000000000000000001"
T1 = "0x0000000000000000000000000000000000000002"
OTHER = "0x0000000000000000000000000000000000000003"
BLOCK_HASH = "0x" + "ab" * 32
RPC_URL = "https://rpc.example.invalid/private-secret?api_key=query-secret"
PANCAKE = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
UNISWAP = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
UNISWAP_BASE = "0x33128a8fc17869897dce68ed026d694621f6fdfd"
AERO = "0x420dd381b31aef6683db6b902084cb0ffece40da"


def abi(*values):
    return "0x" + "".join(f"{(int(v, 16) if isinstance(v, str) else v) % (1 << 256):064x}" for v in values)


class FixtureRPC:
    """A strict fixture node: independent selector table and explicit canned state."""

    def __init__(self, protocol="pancakeswap", chain="bsc"):
        self.protocol, self.chain = protocol, chain
        self.factory = {"pancakeswap": PANCAKE, "uniswap": UNISWAP, "aerodrome": AERO}[protocol]
        if protocol == "uniswap" and chain == "base":
            self.factory = UNISWAP_BASE
        self.chain_id = {"bsc": 56, "ethereum": 1, "arbitrum": 42161, "base": 8453}[chain]
        self.calls = []
        self.overrides = {}
        self.reorg = False
        self.paused = False
        self.stable = False
        self.fee = 7
        self.decimals = (6, 18)
        self.reserves = (1_000_000_000, 500_000_000_000_000_000_000, 4_294_967_295)
        self.sqrt_price = 1 << 96
        self.liquidity = 1 << 100
        self.tick = 0
        self.block_queries = 0

    def result(self, method, params):
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_getBlockByNumber":
            self.block_queries += 1
            return {
                "number": "0x64", "timestamp": "0x6553f100",
                "hash": "0x" + "cd" * 32 if self.reorg and self.block_queries > 1 else BLOCK_HASH,
            }
        assert params[-1] == {"blockHash": BLOCK_HASH, "requireCanonical": True}
        if method == "eth_getCode":
            return self.overrides.get((params[0], "code"), "0x60006000")
        assert method == "eth_call", "No signing, transaction, or arbitrary RPC methods permitted"
        transaction = params[0]
        assert set(transaction) == {"to", "data"}
        target, data = transaction["to"], transaction["data"]
        assert len(data) >= 10
        selector = data[2:10]
        values = {
            (POOL, "c45a0155"): abi(self.factory),
            (POOL, "0dfe1681"): abi(T0),
            (POOL, "d21220a7"): abi(T1),
            (T0, "313ce567"): abi(self.decimals[0]),
            (T1, "313ce567"): abi(self.decimals[1]),
            (POOL, "0902f1ac"): abi(*self.reserves),
            (self.factory, "e6a43905"): abi(POOL),
            (self.factory, "1698ee82"): abi(POOL),
            (self.factory, "79bc57d5"): abi(POOL),
            (POOL, "ddca3f43"): abi(3000),
            (POOL, "d0c93a7c"): abi(60),
            (self.factory, "22afcccb"): abi(60),
            (POOL, "3850c7bd"): abi(self.sqrt_price, self.tick, 0, 1, 1, 0, 1),
            (POOL, "1a686502"): abi(self.liquidity),
            (POOL, "22be3de1"): abi(int(self.stable)),
            (self.factory, "cc56b2c5"): abi(self.fee),
            (self.factory, "5b16ebb7"): abi(1),
            (self.factory, "b187bd26"): abi(int(self.paused)),
            (POOL, "f140a35a"): abi(4_200_000_000_000_000_000),
        }
        values.update(self.overrides)
        assert (target, selector) in values, f"Unexpected ABI call: {target} {selector}"
        return values[(target, selector)]

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        result = self.result(body["method"], body["params"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def reader(self):
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return NativePoolReader({self.chain: RPC_URL}, self.client)


def state_args(node):
    return node.protocol, node.chain, {"uniswap": "v3", "pancakeswap": "v2", "aerodrome": "v1"}[node.protocol], POOL


def test_selectors_are_ethereum_keccak_and_registered_scope_is_bounded():
    # CCXT is an existing production dependency; it is not used by the native reader.
    from ccxt.static_dependencies.keccak import SHA3

    assert all(SHA3(signature.encode()).hex()[:8] == selector for signature, selector in SELECTORS.items())
    scopes = {(r["protocol"], r["chain"], r["version"]) for r in supported_deployments()}
    assert scopes == {
        ("uniswap", "ethereum", "v3"), ("uniswap", "arbitrum", "v3"),
        ("uniswap", "base", "v3"), ("pancakeswap", "bsc", "v2"), ("aerodrome", "base", "v1"),
    }


async def test_constructing_and_unsupported_scope_do_not_create_clients(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Construction must be lazy")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    reader = NativePoolReader({})
    with pytest.raises(UnsupportedPool, match="protocol/chain/version"):
        await reader.read_pool("uniswap", "bsc", "v3", POOL)
    with pytest.raises(UnsupportedPool, match="rpc_urls"):
        await reader.read_pool("pancakeswap", "bsc", "v2", POOL)
    with pytest.raises(UnsupportedPool, match="read-only"):
        await reader._rpc("bsc", "eth_sendRawTransaction", ["0xdeadbeef"])
    await reader.close()


async def test_v2_state_identity_decimals_block_and_modulo_reserve_timestamp():
    node = FixtureRPC()
    reader = node.reader()
    try:
        state = await reader.read_pool(*state_args(node))
        assert state.factory == PANCAKE and state.factory_membership_verified
        assert not state.deployment_bytecode_verified and not state.execution_verified
        assert state.chain_id == 56
        assert state.block_number == 100 and state.block_hash == BLOCK_HASH
        assert state.block_time == 1_700_000_000_000
        assert state.reserve_update_time_raw_seconds == "4294967295"
        assert state.reserve_update_time_modulo_2_32 is True
        assert state.spot_price_token1_per_token0 == Decimal("0.5")
        assert state.fee_rate == Decimal("0.0025")
        assert state.token0.asset_id == f"bsc:{T0}" and state.token0.decimals == 6
        assert state.token1.decimals == 18
        assert len(state.code_sha256) == 4
        assert "private-secret" not in state.model_dump_json()
        assert "query-secret" not in state.model_dump_json()
        assert node.calls[1]["params"] == ["latest", False]
        assert node.calls[-1]["params"] == ["0x64", False]
        reverse = next(r for r in node.calls if r["method"] == "eth_call"
                       and r["params"][0]["data"].startswith("0xe6a43905"))
        assert reverse["params"][0]["data"] == "0xe6a43905" + abi(T0, T1)[2:]
        await reader.close()
        assert not node.client.is_closed  # Injected clients remain caller-owned.
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("token,amount,expected,direction", [
    (T0, "100", "45351216185496703796", "token0-to-token1"),
    (T1, "50", "90702432", "token1-to-token0"),
])
async def test_v2_size_quote_golden_values_fee_once_and_both_quantity_directions(token, amount, expected, direction):
    node = FixtureRPC()
    reader = node.reader()
    try:
        quote = await reader.quote_exact_input(*state_args(node), token, amount, block=100)
        assert quote.amount_out_raw == expected
        assert quote.direction == direction and quote.token_in.address == token
        assert quote.route[0].pool_address == POOL
        assert quote.route[0].token_in == token and quote.route[0].token_out == quote.token_out.address
        assert quote.quote_method == "reserve-formula"
        assert quote.fee_included and quote.price_impact_included
        assert not quote.gas_included and not quote.executable
        assert node.calls[1]["params"] == ["0x64", False]
        serialized = quote.model_dump(mode="json")
        assert isinstance(serialized["amount_in"], str)
        assert isinstance(serialized["amount_out_raw"], str)
        assert serialized["route"][0]["fee_rate"] == "0.0025"
        with pytest.raises(ValidationError):
            quote.executable = True
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("chain", ["ethereum", "arbitrum", "base"])
async def test_v3_state_different_factories_raw_liquidity_decimal_direction_and_signed_tick(chain):
    node = FixtureRPC("uniswap", chain)
    node.decimals = (18, 6)
    node.sqrt_price = 1 << 95
    node.tick = -13864
    reader = node.reader()
    try:
        state = await reader.read_pool(*state_args(node), block="finalized")
        assert state.factory == (UNISWAP_BASE if chain == "base" else UNISWAP)
        assert state.spot_price_token1_per_token0 == Decimal(250000000000)
        assert state.fee_rate == Decimal("0.003") and state.tick == -13864
        assert state.liquidity_raw == str(1 << 100) and state.reserve0_raw is None
        assert "v3_size_quote_requires_verified_quoter_or_tick_traversal" in state.issues
        assert node.calls[1]["params"] == ["finalized", False]
        prior_calls = len(node.calls)
        with pytest.raises(UnsupportedPool, match="verified quoter"):
            await reader.quote_exact_input(*state_args(node), T0, "1")
        assert len(node.calls) == prior_calls
    finally:
        await node.client.aclose()


async def test_uninitialized_v3_has_no_invented_price():
    node = FixtureRPC("uniswap", "ethereum")
    node.sqrt_price, node.liquidity = 0, 0
    reader = node.reader()
    try:
        state = await reader.read_pool(*state_args(node))
        assert state.spot_price_token1_per_token0 is None
        assert "pool_uninitialized" in state.issues and "no_active_liquidity" in state.issues
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("stable,fee", [(False, 7), (True, 0), (True, 300)])
async def test_aerodrome_effective_dynamic_fee_and_pool_quote_includes_fee(stable, fee):
    node = FixtureRPC("aerodrome", "base")
    node.stable, node.fee = stable, fee
    reader = node.reader()
    try:
        quote = await reader.quote_exact_input(*state_args(node), T0, "9.123456")
        assert quote.pool.stable is stable and quote.pool.paused is False
        assert quote.pool.fee_rate == Decimal(fee) / 10000
        assert quote.amount_out == Decimal("4.2")
        assert quote.amount_in_raw == "9123456" and quote.quote_method == "pool-eth-call"
        if stable:
            assert quote.pool.pool_type == "stable"
            assert quote.pool.spot_price_token1_per_token0 is None
        call = next(r for r in node.calls if r["method"] == "eth_call"
                    and r["params"][0]["data"].startswith("0xf140a35a"))
        assert call["params"][0]["data"] == "0xf140a35a" + abi(9123456, T0)[2:]
        assert quote.route[0].fee_rate == quote.pool.fee_rate
    finally:
        await node.client.aclose()


async def test_aerodrome_pause_is_visible_but_size_quote_is_rejected():
    node = FixtureRPC("aerodrome", "base")
    node.paused = True
    reader = node.reader()
    try:
        state = await reader.read_pool(*state_args(node))
        assert state.paused is True and "factory_paused_at_observed_block" in state.issues
        with pytest.raises(UnsupportedPool, match="paused"):
            await reader.quote_exact_input(*state_args(node), T0, "1")
        assert not any(r["method"] == "eth_call" and r["params"][0]["data"].startswith("0xf140a35a")
                       for r in node.calls)
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("overrides,match", [
    ({(POOL, "c45a0155"): abi(OTHER)}, "Pool factory"),
    ({(PANCAKE, "e6a43905"): abi(OTHER)}, "reverse lookup"),
    ({(POOL, "0dfe1681"): abi(0)}, "zero address"),
    ({(POOL, "d21220a7"): abi(T0)}, "token order"),
    ({(POOL, "0dfe1681"): abi(1 << 160)}, "uint160"),
    ({(T0, "313ce567"): abi(256)}, "uint8"),
    ({(POOL, "code"): "0x"}, "code missing"),
    ({(PANCAKE, "code"): "0x0"}, "code missing"),
    ({(T0, "code"): None}, "code missing"),
    ({(POOL, "0902f1ac"): "0x1"}, "result length"),
    ({(POOL, "0902f1ac"): abi(1 << 112, 1, 0)}, "uint112"),
    ({(POOL, "0902f1ac"): abi(1, 1, 1 << 32)}, "uint32"),
])
async def test_v2_rejects_spoofed_pool_noncanonical_abi_and_missing_code(overrides, match):
    node = FixtureRPC()
    node.overrides.update(overrides)
    reader = node.reader()
    try:
        with pytest.raises(OnchainError, match=match):
            await reader.read_pool(*state_args(node))
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("signature,value,match", [
    ("d0c93a7c", abi(1 << 24), "sign extension"),
    ("3850c7bd", abi(1 << 96, (1 << 24) - 1, 0, 1, 1, 0, 1), "sign extension"),
    ("3850c7bd", abi(1, 0, 0, 1, 1, 0, 1), "sqrt price"),
    ("3850c7bd", abi(1 << 96, 0, 0, 1, 1, 0, 2), "bool"),
    ("1a686502", abi(1 << 128), "uint128"),
])
async def test_v3_rejects_invalid_abi_bounds_and_bool(signature, value, match):
    node = FixtureRPC("uniswap", "ethereum")
    node.overrides[(POOL, signature)] = value
    reader = node.reader()
    try:
        with pytest.raises(OnchainError, match=match):
            await reader.read_pool(*state_args(node))
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("protocol,chain,override,match", [
    ("uniswap", "ethereum", {(UNISWAP, "22afcccb"): abi(10)}, "fee/tick spacing"),
    ("aerodrome", "base", {(AERO, "5b16ebb7"): abi(0)}, "recognize pool"),
    ("aerodrome", "base", {(AERO, "79bc57d5"): abi(OTHER)}, "reverse lookup"),
    ("aerodrome", "base", {(AERO, "cc56b2c5"): abi(301)}, "MAX_FEE"),
    ("aerodrome", "base", {(POOL, "22be3de1"): abi(2)}, "bool"),
])
async def test_factory_membership_fee_and_pool_type_are_cross_checked(protocol, chain, override, match):
    node = FixtureRPC(protocol, chain)
    node.overrides.update(override)
    reader = node.reader()
    try:
        with pytest.raises(OnchainError, match=match):
            await reader.read_pool(*state_args(node))
    finally:
        await node.client.aclose()


async def test_chain_id_mismatch_prevents_all_contract_reads():
    node = FixtureRPC()
    node.chain_id = 97
    reader = node.reader()
    try:
        with pytest.raises(OnchainError, match="chain ID"):
            await reader.read_pool(*state_args(node))
        assert [r["method"] for r in node.calls] == ["eth_chainId"]
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("quote", [False, True])
async def test_reorg_discards_whole_state_or_quote(quote):
    node = FixtureRPC()
    node.reorg = True
    reader = node.reader()
    try:
        with pytest.raises(OnchainError, match="Canonical block changed"):
            if quote:
                await reader.quote_exact_input(*state_args(node), T0, "1")
            else:
                await reader.read_pool(*state_args(node))
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("amount", [True, 1.2, "NaN", "Infinity", "-1", "0", "0.0000001", "garbage", "1e999999"])
def test_amounts_require_finite_positive_exact_token_units(amount):
    with pytest.raises((ValueError, TypeError)):
        amount_to_raw(amount, 6)


def test_uint256_precision_is_not_lost_to_decimal_context_or_json():
    raw = (1 << 256) - 1
    assert amount_to_raw(str(raw), 0) == raw
    assert amount_to_raw("0." + "0" * 254 + "1", 255) == 1
    assert amount_to_raw(Decimal("12345.000001"), 6) == 12_345_000_001
    with pytest.raises(ValueError):
        amount_to_raw(str(1 << 256), 0)


@pytest.mark.parametrize("token,amount,match", [
    (OTHER, "1", "not a member"),
    (T0, "0.000001", "zero output"),
    (T0, "5192296858534827628530496329.220096", "reserve capacity"),
])
async def test_quote_rejects_nonmember_dust_or_reserve_overflow(token, amount, match):
    node = FixtureRPC()
    if amount == "0.000001":
        node.reserves = (1_000_000_000, 1, 0)
    reader = node.reader()
    try:
        with pytest.raises((ValueError, UnsupportedPool), match=match):
            await reader.quote_exact_input(*state_args(node), token, amount)
    finally:
        await node.client.aclose()


@pytest.mark.parametrize("block", ["pending", "0x64", -1, True, 1.0])
async def test_invalid_or_pending_block_selectors_never_connect(block):
    reader = NativePoolReader({})
    with pytest.raises(ValueError, match="Block must"):
        await reader.read_pool("pancakeswap", "bsc", "v2", POOL, block=block)


@pytest.mark.parametrize("case", ["transport", "http", "json", "rpc", "envelope", "unsupported-eip1898"])
async def test_errors_are_sanitized_and_no_unpinned_fallback(case):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if case == "transport":
            raise httpx.ConnectError(f"Cannot reach {RPC_URL}", request=request)
        if case == "http":
            return httpx.Response(403, text=RPC_URL)
        if case == "json":
            return httpx.Response(200, text=RPC_URL)
        if case == "envelope":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "wrong", "result": "0x38"})
        if case == "unsupported-eip1898" and body["method"] in ("eth_chainId", "eth_getBlockByNumber"):
            result = "0x38" if body["method"] == "eth_chainId" else {
                "hash": BLOCK_HASH, "number": "0x64", "timestamp": "0x6553f100",
            }
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                        "error": {"code": -32602, "message": RPC_URL, "data": RPC_URL}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reader = NativePoolReader({"bsc": RPC_URL}, client)
        with pytest.raises(OnchainError) as caught:
            await reader.read_pool("pancakeswap", "bsc", "v2", POOL)
        assert "private-secret" not in str(caught.value) and "query-secret" not in str(caught.value)
        for call in calls:
            if call["method"] in ("eth_getCode", "eth_call"):
                assert call["params"][-1] == {"blockHash": BLOCK_HASH, "requireCanonical": True}


async def test_redirect_is_not_followed_even_for_caller_client_that_follows_redirects():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://other.invalid/credential-capture"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        reader = NativePoolReader({"bsc": RPC_URL}, client)
        with pytest.raises(OnchainError, match="HTTPStatusError"):
            await reader.read_pool("pancakeswap", "bsc", "v2", POOL)
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["file:///secret", "https://rpc.invalid:bad/private-secret", "https://[bad"])
async def test_invalid_configured_urls_fail_without_transport_or_secret_in_error(url, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid URL must be rejected before client construction")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    reader = NativePoolReader({"bsc": url})
    with pytest.raises(UnsupportedPool, match="Invalid configured RPC URL") as caught:
        await reader.read_pool("pancakeswap", "bsc", "v2", POOL)
    assert "private-secret" not in str(caught.value)
