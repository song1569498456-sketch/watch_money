"""Pure, conservative asset identity review over the full discovered catalog."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable

from .models import Instrument, now_ms
from .quote_identity import quote_identity, requires_quote_identity

_EVM_CHAINS = {"ethereum", "arbitrum", "base", "bsc", "optimism", "polygon", "avalanche",
               "linea", "scroll", "zksync"}
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
_SOLANA_ADDRESS = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}\Z")
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_VERIFIED_UNIT_ALIASES = [frozenset({"BTC", "XBT"})]


def _chain_address(asset_id: str) -> tuple[str, str] | None:
    chain, separator, address = asset_id.partition(":")
    if not separator:
        return None
    if chain in _EVM_CHAINS and _EVM_ADDRESS.fullmatch(address):
        return chain, address.lower()
    if chain == "solana" and _SOLANA_ADDRESS.fullmatch(address):
        number = 0
        for char in address:
            number = number * 58 + _BASE58.index(char)
        length = (number.bit_length() + 7) // 8 + len(address) - len(address.lstrip("1"))
        if length == 32:
            return chain, address  # Base58 is case sensitive.
    return None


def _symbol(instrument: Instrument) -> str:
    # No removal of numeric prefixes, wrapped-token prefixes, dots or suffixes.
    return instrument.base.strip().upper()


def _evidence(instrument: Instrument, as_of: int) -> dict:
    native_id = getattr(instrument, "native_asset_id", None) or instrument.asset_id
    native_address = _chain_address(native_id)
    mapped_address = _chain_address(instrument.asset_id)
    address = native_address or mapped_address
    issues = []
    if instrument.mapping_expires_at is not None and instrument.mapping_expires_at <= max(as_of, now_ms()):
        issues.append("mapping_evidence_expired")
    if instrument.mapping_verified_at is not None and instrument.mapping_verified_at > as_of:
        issues.append("mapping_not_known_as_of")
    if instrument.mapping_evidence_status not in {None, "verified"}:
        issues.append("mapping_evidence_" + instrument.mapping_evidence_status)
    if address and instrument.chain and address[0] != instrument.chain:
        issues.append("chain_metadata_conflict")
    if native_address and mapped_address and native_address != mapped_address:
        issues.append("mapped_address_conflicts_with_native_identity")
    native_chain = native_id.partition(":")[0]
    if native_chain in _EVM_CHAINS | {"solana"} and native_address is None:
        issues.append("invalid_native_chain_address")
    if instrument.mapping_verified:
        mapped = f"{mapped_address[0]}:{mapped_address[1]}" if mapped_address else instrument.asset_id
        group_key = f"asset:{mapped}"
        source = "verified_mapping"
    elif address and not issues:
        group_key = f"asset:{address[0]}:{address[1]}"
        source = "chain_address"
    else:
        # An exchange's symbol/catalog id establishes a local candidate only.
        group_key = f"unverified:{instrument.venue}:{instrument.asset_id}:{native_id}:{_symbol(instrument)}"
        source = "venue_scoped"
    return {"group_key": group_key, "asset_id": instrument.asset_id, "native_asset_id": native_id,
            "source": source, "chain": address[0] if address else instrument.chain,
            "address": address[1] if address else None, "issues": issues}


def _stable_id(kind: str, key: str) -> str:
    return f"{kind}:{hashlib.sha256(key.encode()).hexdigest()[:24]}"


def _row(kind: str, key: str, members: list[Instrument], evidence: dict[str, dict], as_of: int) -> dict:
    members = sorted(members, key=lambda item: item.id)
    facts = [evidence[item.id] for item in members]
    symbols = sorted({_symbol(item) for item in members})
    venues = sorted({item.venue for item in members})
    addresses = {(fact["chain"], fact["address"]) for fact in facts if fact["address"]}
    chains = {fact["chain"] for fact in facts if fact["chain"]}
    groups = {fact["group_key"] for fact in facts}
    blockers = {issue for fact in facts for issue in fact["issues"]}
    warnings, requirements = set(), set()
    trusted = all(fact["source"] != "venue_scoped" for fact in facts)
    if not trusted:
        blockers.add("unverified_asset_mapping")
    if len(groups) > 1:
        blockers.add("different_asset_identities")
    if len(addresses) > 1:
        blockers.add("different_native_chain_addresses")
    if len(chains) > 1:
        blockers.add("different_chains")
    verified_alias = (all(item.mapping_verified for item in members) and len(groups) == 1
                      and any(set(symbols) <= aliases for aliases in _VERIFIED_UNIT_ALIASES))
    if len(symbols) > 1 and not verified_alias:
        # A mapping alone cannot express PEPE/1000PEPE, native/wrapped or
        # bridged-token conversion ratios. Preserve identity evidence for review.
        blockers.add("different_base_symbols_require_unit_review")
    identity_issues = set(blockers)
    if len(venues) > 1 and not all(item.mapping_verified for item in members):
        blockers.add("cross_venue_mapping_requires_confirmation")
    if any(not item.active for item in members):
        warnings.add("contains_inactive_markets")
    quotes = sorted({item.quote for item in members})
    if len(quotes) > 1:
        requirements.add("quote_conversion_with_timestamped_fx")
    if any(item.kind == "perpetual" and item.contract_size != 1 for item in members):
        requirements.add("contract_quantity_to_base_conversion")
    if any(item.kind == "perpetual" and not item.linear for item in members):
        requirements.add("inverse_contract_conversion")
    for item in members:
        if not requires_quote_identity(item):
            continue
        _, problems = quote_identity(item, as_of, 60000)
        if not problems:
            continue
        if any("metadata review has expired" in problem for problem in problems):
            warnings.add("quote_identity_evidence_expired")
        elif all("stale or from the future" in problem for problem in problems):
            # The real strategy may use a different stale_seconds value. Do not
            # describe present contract/source fields as missing on that basis.
            warnings.add("quote_identity_requires_freshness_check")
        else:
            warnings.add("quote_token_contract_identity_unavailable")
    markets = []
    for item, fact in zip(members, facts, strict=True):
        markets.append({"instrument_id": item.id, "venue": item.venue, "native_symbol": item.native_symbol,
                        "asset_id": item.asset_id, "native_asset_id": fact["native_asset_id"],
                        "base": item.base, "quote": item.quote, "settle": item.settle, "kind": item.kind,
                        "chain": item.chain, "active": item.active, "contract_size": item.contract_size,
                        "linear": item.linear, "mapping_verified": item.mapping_verified,
                        "identity_source": fact["source"], "address": fact["address"]})
    return {"id": _stable_id(kind, key), "kind": kind, "symbols": symbols,
            "instrument_ids": [item.id for item in members], "venues": venues,
            "asset_ids": sorted({item.asset_id for item in members}), "quotes": quotes,
            "identity_compatible": trusted and not identity_issues,
            "comparison_ready": trusted and not blockers,
            "blockers": sorted(blockers), "requirements": sorted(requirements),
            "warnings": sorted(warnings), "markets": markets}


def identity_candidates(instruments: Iterable[Instrument], *, as_of: int | None = None) -> list[dict]:
    """Return deterministic JSON-ready identity groups and same-ticker reviews.

    Every discovered market appears in one ``identity`` row. A ``ticker_candidate``
    row additionally presents a ticker shared by multiple identities for human
    review, without merging them. ``comparison_ready`` covers the asset identity
    gate only; prices, FX, executable depth and costs are separate requirements.
    No catalog, mapping, or input object is changed; runtime is O(n log n).
    Duplicate instrument ids are rejected instead of silently replacing evidence.
    """
    identities, symbols = defaultdict(list), defaultdict(list)
    evidence = {}
    as_of = now_ms() if as_of is None else as_of
    for instrument in instruments:
        if instrument.id in evidence:
            raise ValueError("Duplicate instrument id in asset identity catalog")
        fact = _evidence(instrument, as_of)
        evidence[instrument.id] = fact
        identities[fact["group_key"]].append(instrument)
        symbols[_symbol(instrument)].append(instrument)
    rows = [_row("identity", key, members, evidence, as_of) for key, members in sorted(identities.items())]
    rows.extend(_row("ticker_candidate", symbol, members, evidence, as_of) for symbol, members in sorted(symbols.items())
                if len({evidence[item.id]["group_key"] for item in members}) > 1)
    return rows
