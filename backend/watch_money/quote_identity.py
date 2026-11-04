"""Canonical token identities and explicit quote-denomination evidence.

Symbols, user-supplied validity flags and an FX number cannot establish a token
contract. Quote evidence remains bound to the instrument's chain/native token.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from .models import Instrument

EVM_CHAINS = frozenset({"ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "avalanche",
                        "linea", "scroll", "zksync"})
DEX_VENUES = frozenset({"hyperliquid", "lighter", "dydx", "paradex", "aster", "edgex", "variational",
                        "gmx", "jupiter-perps", "drift", "uniswap", "pancakeswap", "aerodrome", "curve",
                        "raydium", "orca"})
# Local metadata review policy, not an exchange promise of immutable identity.
MAX_QUOTE_IDENTITY_TTL_MS = 6 * 3_600_000
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_SOLANA_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}\Z")
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def canonical_chain_address(asset_id: str) -> tuple[str, str] | None:
    chain, separator, address = asset_id.partition(":")
    if not separator:
        return None
    if chain in EVM_CHAINS and _EVM_ADDRESS.fullmatch(address):
        return chain, address.lower()
    if chain == "solana" and _SOLANA_ADDRESS.fullmatch(address):
        number = 0
        for char in address:
            number = number * 58 + _BASE58.index(char)
        length = (number.bit_length() + 7) // 8 + len(address) - len(address.lstrip("1"))
        if length == 32:
            return chain, address
    return None


def canonical_asset_id(asset_id: str | None) -> str | None:
    identity = canonical_chain_address(asset_id) if asset_id else None
    return f"{identity[0]}:{identity[1]}" if identity is not None else None


def requires_quote_identity(instrument: Instrument) -> bool:
    return bool(instrument.chain) or instrument.kind == "pool" or instrument.mechanism in {"amm", "rfq"} or instrument.venue in DEX_VENUES


def traceable_quote_source(source: str | None) -> bool:
    if not source:
        return False
    try:
        parsed = urlsplit(source)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
        return True
    # This is the credential-free provenance emitted by the native pool reader,
    # not its configured RPC URL. A plain string such as "verified" is insufficient.
    if source.startswith("evm-rpc:"):
        parts = source.split(":")
        return len(parts) >= 3 and parts[1] in EVM_CHAINS and bool(_EVM_ADDRESS.search(parts[-1].split("#")[0]))
    return False


def quote_identity(instrument: Instrument, as_of: int, max_age_ms: float) -> tuple[str | None, list[str]]:
    if not requires_quote_identity(instrument):
        return f"cex:{quote(instrument.venue, safe='')}:{quote(instrument.quote, safe='')}", []
    problems = []
    native = canonical_asset_id(instrument.native_quote_asset_id)
    mapped = canonical_asset_id(instrument.quote_asset_id)
    if instrument.native_quote_asset_id and native is None:
        problems.append("quote native token contract identity has invalid chain/address format")
    if instrument.quote_asset_id and mapped is None:
        problems.append("quote token contract identity has invalid chain/address format")
    if native is not None and mapped is not None and native != mapped:
        problems.append("quote mapped token conflicts with native quote contract identity")
    asset_id = native or mapped
    if asset_id is None:
        problems.append("quote token contract identity is unavailable; matching symbols are insufficient")
    elif asset_id.partition(":")[0] != instrument.chain:
        problems.append("quote token chain conflicts with the instrument chain")
    if asset_id and asset_id.partition(":")[2] == "0x" + "0" * 40:
        problems.append("quote token zero address does not establish a token contract")
    if not instrument.chain:
        problems.append("quote token requires an explicit instrument chain")
    if not traceable_quote_source(instrument.quote_identity_source):
        problems.append("quote token identity requires a traceable source")
    at = instrument.quote_identity_at
    if at is None:
        problems.append("quote token identity observation time is unavailable")
    elif at > as_of:
        problems.append("quote token identity evidence is stale or from the future")
    elif instrument.quote_identity_expires_at is not None:
        expires = instrument.quote_identity_expires_at
        if not at < expires <= at + MAX_QUOTE_IDENTITY_TTL_MS:
            problems.append("quote token identity expiry exceeds the bounded metadata review policy")
        elif as_of >= expires:
            problems.append("quote token identity evidence is stale: metadata review has expired")
    elif as_of - at > max_age_ms:
        problems.append("quote token identity evidence is stale or from the future")
    return (None if problems else asset_id), problems
