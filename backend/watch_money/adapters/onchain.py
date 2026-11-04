"""Optional, read-only EVM pool state and single-pool exact-input estimates.

No RPC defaults, discovery, signing, transaction simulation or executable quotes.
All contract reads use one canonical block hash (EIP-1898), without a weaker
fallback. Factory membership is checked; deployed implementation code is not
attested against a compiler artifact. See docs/ONCHAIN.md for that distinction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from watch_money.models import now_ms

CHAIN_IDS = {"ethereum": 1, "arbitrum": 42161, "base": 8453, "bsc": 56}
SUPPORTED = {
    ("uniswap", "ethereum", "v3"),
    ("uniswap", "arbitrum", "v3"),
    ("uniswap", "base", "v3"),
    ("pancakeswap", "bsc", "v2"),
    ("aerodrome", "base", "v1"),
}
UINT256_MAX = (1 << 256) - 1
# First four bytes of Ethereum Keccak-256 of the canonical ABI signatures.
# Literal selectors keep this reader independent of Web3/ABI/crypto packages.
SELECTORS = {
    "factory()": "c45a0155",
    "token0()": "0dfe1681",
    "token1()": "d21220a7",
    "decimals()": "313ce567",
    "getReserves()": "0902f1ac",
    "getPair(address,address)": "e6a43905",
    "getPool(address,address,uint24)": "1698ee82",
    "getPool(address,address,bool)": "79bc57d5",
    "fee()": "ddca3f43",
    "tickSpacing()": "d0c93a7c",
    "feeAmountTickSpacing(uint24)": "22afcccb",
    "slot0()": "3850c7bd",
    "liquidity()": "1a686502",
    "stable()": "22be3de1",
    "getFee(address,bool)": "cc56b2c5",
    "isPool(address)": "5b16ebb7",
    "isPaused()": "b187bd26",
    "getAmountOut(uint256,address)": "f140a35a",
}


class OnchainError(RuntimeError):
    """Sanitized configured-RPC failure; never contains its credential-bearing URL."""


class UnsupportedPool(OnchainError):
    pass


class NativeModel(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class NativeToken(NativeModel):
    asset_id: str
    address: str
    decimals: int


class NativePoolState(NativeModel):
    protocol: str
    chain: str
    chain_id: int
    version: str
    address: str
    factory: str
    token0: NativeToken
    token1: NativeToken
    pool_type: Literal["constant-product", "concentrated-liquidity", "stable"]
    fee_rate: Decimal
    fee_source: str
    block_number: int
    block_hash: str
    block_time: int  # UTC epoch milliseconds; observed block, not last swap time.
    received_at: int = Field(default_factory=now_ms)
    source: str
    reserve0_raw: str | None = None
    reserve1_raw: str | None = None
    # V2's uint32 is modulo 2**32 seconds; do not convert it to an epoch timestamp.
    reserve_update_time_raw_seconds: str | None = None
    reserve_update_time_modulo_2_32: bool | None = None
    sqrt_price_x96_raw: str | None = None
    liquidity_raw: str | None = None  # V3 active liquidity; not token reserves or USD.
    tick: int | None = None
    tick_spacing: int | None = None
    unlocked: bool | None = None
    stable: bool | None = None
    paused: bool | None = None
    spot_price_token1_per_token0: Decimal | None = None
    spot_price_kind: Literal["reserve-ratio", "sqrt-price"] | None = None
    deployment_source: str
    deployment_source_sha256: str
    code_sha256: dict[str, str]
    factory_membership_verified: Literal[True] = True
    deployment_bytecode_verified: Literal[False] = False
    execution_verified: Literal[False] = False
    issues: tuple[str, ...] = ()


class NativeRouteHop(NativeModel):
    protocol: str
    chain: str
    version: str
    pool_address: str
    token_in: str
    token_out: str
    fee_rate: Decimal


class NativePoolQuote(NativeModel):
    pool: NativePoolState
    kind: Literal["exact-input"] = "exact-input"
    quote_method: Literal["reserve-formula", "pool-eth-call"]
    token_in: NativeToken
    token_out: NativeToken
    direction: Literal["token0-to-token1", "token1-to-token0"]
    amount_in_raw: str
    amount_out_raw: str
    amount_in: Decimal
    amount_out: Decimal
    average_out_per_in: Decimal
    route: tuple[NativeRouteHop, ...]
    fee_included: Literal[True] = True
    price_impact_included: Literal[True] = True
    gas_included: Literal[False] = False
    executable: Literal[False] = False
    received_at: int = Field(default_factory=now_ms)
    issues: tuple[str, ...] = (
        "read_only_estimate_not_transaction_simulation",
        "token_transfer_taxes_hooks_balances_and_allowances_unverified",
        "gas_slippage_tolerance_mev_and_future_state_not_included",
        "deployed_implementation_bytecode_not_attested",
    )


def address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise ValueError("Expected an EVM contract address")
    if int(value, 16) == 0:
        raise ValueError("Zero address is not a pool or token")
    return value.lower()


def _uint(value: int, bits: int = 256) -> int:
    if not 0 <= value < 1 << bits:
        raise OnchainError(f"Malformed ABI uint{bits}")
    return value


def _signed(value: int, bits: int) -> int:
    result = value - (1 << 256) if value >= 1 << 255 else value
    if not -(1 << (bits - 1)) <= result < 1 << (bits - 1):
        raise OnchainError(f"Malformed ABI int{bits} sign extension")
    return result


def _bool(value: int) -> bool:
    if value not in (0, 1):
        raise OnchainError("Malformed ABI bool")
    return bool(value)


def _address_word(value: int) -> str:
    _uint(value, 160)
    if not value:
        raise OnchainError("Contract returned a zero address")
    return f"0x{value:040x}"


def _quantity(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value):
        raise OnchainError("Malformed JSON-RPC hex quantity")
    return int(value, 16)


def _block_tag(value: str | int) -> str:
    if type(value) is int and value >= 0:
        return hex(value)
    if isinstance(value, str) and value in ("latest", "safe", "finalized"):
        return value
    raise ValueError("Block must be a nonnegative integer, latest, safe or finalized")


def amount_to_raw(value: str | Decimal | int, decimals: int) -> int:
    """Human token units -> exact uint256. No float inputs or silent rounding."""
    if isinstance(value, bool) or not isinstance(value, (str, Decimal, int)):
        raise TypeError("Use a decimal string or Decimal for the input amount")
    if not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 255:
        raise ValueError("Invalid token decimals")
    if isinstance(value, str) and len(value) > 1000:
        raise ValueError("Input amount is too long")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid input amount") from None
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("Input amount must be finite and positive")
    if len(parsed.as_tuple().digits) > 1000 or abs(parsed.as_tuple().exponent) > 1000:
        raise ValueError("Input amount precision exceeds supported bounds")
    numerator, denominator = parsed.as_integer_ratio()
    raw, remainder = divmod(numerator * 10**decimals, denominator)
    if remainder or not 0 < raw <= UINT256_MAX:
        raise ValueError("Input amount is not representable as a positive token uint256")
    return raw


def _human(raw: int, decimals: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 400
        return Decimal(raw).scaleb(-decimals)


def supported_deployments() -> list[dict]:
    registry = json.loads(Path(__file__).with_name("deployments.json").read_text(encoding="utf-8"))
    return [
        row for row in registry["deployments"]
        if (row["protocol"], row["chain"], row["version"]) in SUPPORTED
        and row["address_role"] == "factory"
    ]


class NativePoolReader:
    """Explicit pool addresses only; create with settings.rpc_urls, close on shutdown."""

    def __init__(self, rpc_urls: dict[str, str], client: httpx.AsyncClient | None = None):
        self._urls = dict(rpc_urls)
        self._client = client
        self._owns_client = client is None
        self._next_id = 0

    async def close(self):
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, chain: str, method: str, params: list):
        if method not in {"eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_call"}:
            raise UnsupportedPool("Only read-only pool RPC methods are supported")
        url = self._urls.get(chain)
        if not url:
            raise UnsupportedPool(f"Configure settings.rpc_urls[{chain!r}] for native pool reads")
        try:
            parsed = urlsplit(url)
            rpc_port = parsed.port  # Access also validates port syntax.
            valid_url = (parsed.scheme in ("http", "https") and bool(parsed.hostname)
                         and (rpc_port is None or rpc_port > 0))
        except (ValueError, TypeError):
            valid_url = False
        if not valid_url:
            raise UnsupportedPool(f"Invalid configured RPC URL for {chain}")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20, trust_env=True, follow_redirects=False)
        self._next_id += 1
        request_id = self._next_id
        try:
            response = await self._client.post(
                url, json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                follow_redirects=False,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise OnchainError(f"{chain}: configured RPC failed ({type(exc).__name__})") from None
        except ValueError:
            raise OnchainError(f"{chain}: configured RPC returned invalid JSON") from None
        if (
            not isinstance(body, dict) or body.get("jsonrpc") != "2.0"
            or type(body.get("id")) is not int or body["id"] != request_id
        ):
            raise OnchainError(f"{chain}: malformed RPC response envelope")
        if body.get("error") is not None:
            error = body["error"]
            code = error.get("code") if isinstance(error, dict) else None
            code = code if type(code) is int else "unknown"
            raise OnchainError(f"{chain}: RPC error {code}; canonical block reads are required")
        if "result" not in body:
            raise OnchainError(f"{chain}: RPC result missing")
        return body["result"]

    async def _block(self, chain: str, tag: str) -> dict:
        block = await self._rpc(chain, "eth_getBlockByNumber", [tag, False])
        if not isinstance(block, dict):
            raise OnchainError("Requested mined block is unavailable")
        digest = block.get("hash")
        if not isinstance(digest, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", digest):
            raise OnchainError("Mined block hash is missing or malformed")
        number, seconds = _quantity(block.get("number")), _quantity(block.get("timestamp"))
        if tag.startswith("0x") and number != int(tag, 16):
            raise OnchainError("RPC returned a different block height")
        return {"number": number, "hash": digest.lower(), "time": seconds * 1000}

    async def _canonical(self, chain: str, block: dict):
        current = await self._block(chain, hex(block["number"]))
        if current != block:
            raise OnchainError("Canonical block changed during pool read; retry the complete request")

    @staticmethod
    def _pin(block: dict) -> dict:
        return {"blockHash": block["hash"], "requireCanonical": True}

    async def _code(self, chain: str, target: str, block: dict) -> str:
        code = await self._rpc(chain, "eth_getCode", [target, self._pin(block)])
        if not isinstance(code, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code):
            raise OnchainError("Required contract code missing or malformed at pinned block")
        return hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()

    async def _call(
        self, chain: str, target: str, signature: str, block: dict, args: tuple[int, ...] = (),
        words: int = 1,
    ) -> list[int]:
        data = "0x" + SELECTORS[signature] + "".join(f"{_uint(v):064x}" for v in args)
        raw = await self._rpc(chain, "eth_call", [{"to": target, "data": data}, self._pin(block)])
        if not isinstance(raw, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", raw):
            raise OnchainError("Malformed or empty contract ABI result")
        if len(raw) != 2 + words * 64:
            raise OnchainError("Unexpected contract ABI result length")
        return [int(raw[2 + i * 64: 66 + i * 64], 16) for i in range(words)]

    async def read_pool(
        self, protocol: str, chain: str, version: str, pool_address: str,
        *, block: str | int = "latest",
    ) -> NativePoolState:
        scope, target, tag = self._scope(protocol, chain, version), address(pool_address), _block_tag(block)
        block_info = await self._begin(chain, tag)
        state = await self._read_at(scope, target, block_info)
        await self._canonical(chain, block_info)
        return state

    @staticmethod
    def _scope(protocol: str, chain: str, version: str) -> dict:
        if (protocol, chain, version) not in SUPPORTED:
            raise UnsupportedPool("Native reader does not support this protocol/chain/version")
        rows = [d for d in supported_deployments() if (d["protocol"], d["chain"], d["version"])
                == (protocol, chain, version)]
        if len(rows) != 1:
            raise UnsupportedPool("Exactly one source-registered factory is required")
        return rows[0]

    async def _begin(self, chain: str, tag: str) -> dict:
        chain_id = _quantity(await self._rpc(chain, "eth_chainId", []))
        if chain_id != CHAIN_IDS[chain]:
            raise OnchainError(f"Configured RPC chain ID does not match {chain}")
        return await self._block(chain, tag)

    async def _read_at(self, scope: dict, target: str, block: dict) -> NativePoolState:
        protocol, chain, version = scope["protocol"], scope["chain"], scope["version"]
        factory = address(scope["address"])
        codes = dict(zip((factory, target), await asyncio.gather(
            self._code(chain, factory, block), self._code(chain, target, block)
        ), strict=True))
        native_factory, t0, t1 = await asyncio.gather(*(
            self._call(chain, target, sig, block) for sig in ("factory()", "token0()", "token1()")
        ))
        if _address_word(native_factory[0]) != factory:
            raise OnchainError("Pool factory does not match the source-registered deployment")
        token0, token1 = _address_word(t0[0]), _address_word(t1[0])
        if int(token0, 16) >= int(token1, 16):
            raise OnchainError("Pool token order is invalid or tokens are identical")
        if target in (factory, token0, token1) or factory in (token0, token1):
            raise OnchainError("Pool, factory and token contracts must be distinct")
        decimal_rows, token_codes = await asyncio.gather(
            asyncio.gather(*(self._call(chain, t, "decimals()", block) for t in (token0, token1))),
            asyncio.gather(*(self._code(chain, t, block) for t in (token0, token1))),
        )
        decimals0, decimals1 = (_uint(row[0], 8) for row in decimal_rows)
        codes.update(zip((token0, token1), token_codes, strict=True))
        fields: dict = {}
        lookup_args = (int(token0, 16), int(token1, 16))
        if protocol == "uniswap":
            fee, spacing, slot0, liquidity = await asyncio.gather(
                self._call(chain, target, "fee()", block),
                self._call(chain, target, "tickSpacing()", block),
                self._call(chain, target, "slot0()", block, words=7),
                self._call(chain, target, "liquidity()", block),
            )
            fee_units, tick_spacing = _uint(fee[0], 24), _signed(spacing[0], 24)
            if not 0 < fee_units < 1_000_000 or tick_spacing <= 0:
                raise OnchainError("Invalid Uniswap V3 fee or tick spacing")
            enabled = await self._call(chain, factory, "feeAmountTickSpacing(uint24)", block, (fee_units,))
            if _signed(enabled[0], 24) != tick_spacing:
                raise OnchainError("Pool fee/tick spacing disagrees with factory")
            lookup_sig, lookup_args = "getPool(address,address,uint24)", (*lookup_args, fee_units)
            sqrt_price, tick = _uint(slot0[0], 160), _signed(slot0[1], 24)
            for raw in slot0[2:5]:
                _uint(raw, 16)
            _uint(slot0[5], 8)
            unlocked = _bool(slot0[6])
            active_liquidity = _uint(liquidity[0], 128)
            if not -887272 <= tick <= 887272:
                raise OnchainError("Uniswap V3 tick outside TickMath bounds")
            if sqrt_price and not 4295128739 <= sqrt_price < 1461446703485210103287273052203988822378723970342:
                raise OnchainError("Uniswap V3 sqrt price outside TickMath bounds")
            price = None
            if sqrt_price:
                with localcontext() as ctx:
                    ctx.prec = 400
                    price = (Decimal(sqrt_price**2) / Decimal(1 << 192)).scaleb(decimals0 - decimals1)
            fields.update(
                pool_type="concentrated-liquidity", fee_rate=Decimal(f"{fee_units}e-6"),
                fee_source=f"evm-rpc:{chain}:{target}#fee()",
                sqrt_price_x96_raw=str(sqrt_price), liquidity_raw=str(active_liquidity), tick=tick,
                tick_spacing=tick_spacing, unlocked=unlocked, spot_price_token1_per_token0=price,
                spot_price_kind="sqrt-price" if price is not None else None,
                issues=tuple(issue for condition, issue in (
                    (sqrt_price == 0, "pool_uninitialized"),
                    (active_liquidity == 0, "no_active_liquidity"),
                    (not unlocked, "pool_locked_at_observed_block"),
                    (True, "v3_size_quote_requires_verified_quoter_or_tick_traversal"),
                ) if condition),
            )
        else:
            reserves = await self._call(chain, target, "getReserves()", block, words=3)
            reserve_bits = 112 if protocol == "pancakeswap" else 256
            r0, r1 = (_uint(v, reserve_bits) for v in reserves[:2])
            timestamp_raw = _uint(reserves[2], 32 if protocol == "pancakeswap" else 256)
            stable, paused = None, None
            if protocol == "aerodrome":
                stable_word, paused_word, membership = await asyncio.gather(
                    self._call(chain, target, "stable()", block),
                    self._call(chain, factory, "isPaused()", block),
                    self._call(chain, factory, "isPool(address)", block, (int(target, 16),)),
                )
                stable, paused = _bool(stable_word[0]), _bool(paused_word[0])
                if not _bool(membership[0]):
                    raise OnchainError("Aerodrome factory does not recognize pool")
                fee = await self._call(chain, factory, "getFee(address,bool)", block,
                                       (int(target, 16), int(stable)))
                if fee[0] > 300:
                    raise OnchainError("Aerodrome effective fee exceeds source MAX_FEE")
                fee_rate = Decimal(f"{fee[0]}e-4")
                fee_source = f"evm-rpc:{chain}:{factory}#getFee(address,bool)"
                lookup_sig, lookup_args = "getPool(address,address,bool)", (*lookup_args, int(stable))
            else:
                fee_rate = Decimal("0.0025")
                fee_source = "https://raw.githubusercontent.com/pancakeswap/pancake-swap-sdk/master/src/constants.ts"
                lookup_sig = "getPair(address,address)"
            price = None
            if r0 and r1 and not stable:
                with localcontext() as ctx:
                    ctx.prec = 400
                    price = (Decimal(r1) / Decimal(r0)).scaleb(decimals0 - decimals1)
            fields.update(
                pool_type="stable" if stable else "constant-product", fee_rate=fee_rate,
                fee_source=fee_source, reserve0_raw=str(r0), reserve1_raw=str(r1),
                reserve_update_time_raw_seconds=str(timestamp_raw),
                reserve_update_time_modulo_2_32=protocol == "pancakeswap", stable=stable, paused=paused,
                spot_price_token1_per_token0=price,
                spot_price_kind="reserve-ratio" if price is not None else None,
                issues=tuple(issue for condition, issue in (
                    (r0 == 0 or r1 == 0, "empty_pool_reserves"),
                    (paused, "factory_paused_at_observed_block"),
                    (stable, "stable_pool_reserve_ratio_is_not_marginal_price"),
                ) if condition),
            )
        pool = await self._call(chain, factory, lookup_sig, block, lookup_args)
        if _address_word(pool[0]) != target:
            raise OnchainError("Factory reverse lookup does not match requested pool")
        return NativePoolState(
            protocol=protocol, chain=chain, chain_id=CHAIN_IDS[chain], version=version,
            address=target, factory=factory,
            token0=NativeToken(asset_id=f"{chain}:{token0}", address=token0, decimals=decimals0),
            token1=NativeToken(asset_id=f"{chain}:{token1}", address=token1, decimals=decimals1),
            block_number=block["number"], block_hash=block["hash"], block_time=block["time"],
            source=f"evm-rpc:{chain}:{protocol}:{version}:{target}",
            deployment_source=scope["source_url"], deployment_source_sha256=scope["source_sha256"],
            code_sha256=codes, **fields,
        )

    async def quote_exact_input(
        self, protocol: str, chain: str, version: str, pool_address: str,
        token_in: str, amount_in: str | Decimal | int, *, block: str | int = "latest",
    ) -> NativePoolQuote:
        scope = self._scope(protocol, chain, version)
        if protocol == "uniswap":
            raise UnsupportedPool("Uniswap V3 size quotes need a verified quoter or complete tick traversal")
        target, input_address, tag = address(pool_address), address(token_in), _block_tag(block)
        block_info = await self._begin(chain, tag)
        state = await self._read_at(scope, target, block_info)
        if input_address == state.token0.address:
            input_token, output_token = state.token0, state.token1
            r_in, r_out = int(state.reserve0_raw), int(state.reserve1_raw)
            direction = "token0-to-token1"
        elif input_address == state.token1.address:
            input_token, output_token = state.token1, state.token0
            r_in, r_out = int(state.reserve1_raw), int(state.reserve0_raw)
            direction = "token1-to-token0"
        else:
            raise ValueError("Input token is not a member of the requested pool")
        raw_in = amount_to_raw(amount_in, input_token.decimals)
        if not r_in or not r_out:
            raise UnsupportedPool("Cannot quote an empty pool")
        if state.paused:
            raise UnsupportedPool("Aerodrome factory is paused at the requested block")
        if protocol == "pancakeswap":
            weighted_input = raw_in * 9975
            numerator, denominator = weighted_input * r_out, r_in * 10000 + weighted_input
            if max(numerator, denominator) > UINT256_MAX or r_in + raw_in >= 1 << 112:
                raise UnsupportedPool("Amount exceeds V2 arithmetic or reserve capacity")
            raw_out, method = numerator // denominator, "reserve-formula"
        else:
            output = await self._call(chain, target, "getAmountOut(uint256,address)", block_info,
                                      (raw_in, int(input_address, 16)))
            raw_out, method = output[0], "pool-eth-call"
        if not 0 < raw_out < r_out:
            raise UnsupportedPool("Pool quote has zero output or exceeds reserve capacity")
        await self._canonical(chain, block_info)
        human_in, human_out = _human(raw_in, input_token.decimals), _human(raw_out, output_token.decimals)
        with localcontext() as ctx:
            ctx.prec = 400
            average = human_out / human_in
        return NativePoolQuote(
            pool=state, quote_method=method, token_in=input_token, token_out=output_token,
            direction=direction, amount_in_raw=str(raw_in), amount_out_raw=str(raw_out),
            amount_in=human_in, amount_out=human_out, average_out_per_in=average,
            route=(NativeRouteHop(protocol=protocol, chain=chain, version=version,
                                  pool_address=target, token_in=input_address,
                                  token_out=output_token.address, fee_rate=state.fee_rate),),
        )
