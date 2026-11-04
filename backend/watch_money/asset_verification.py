"""Traceable public asset metadata import and explicit atomic mapping reviews."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .identity import _chain_address, identity_candidates
from .models import Instrument, now_ms

router = APIRouter(prefix="/api/assets/verification", tags=["asset-verification"])
SOURCES = {
    "gate": {"endpoint": "https://api.gateio.ws/api/v4/spot/currencies",
             "docs_url": "https://www.gate.com/docs/developers/apiv4/en/spot/#query-all-currency-information",
             "client_ids": ["gate"], "product_kinds": ["spot"],
             "scope_note": "Public spot currency registry; derivatives require independent underlying/unit evidence"},
    "bitget": {"endpoint": "https://api.bitget.com/api/v2/spot/public/coins",
               "docs_url": "https://www.bitget.com/docs/catalog/classic-spot-market/classic-spot-market",
               "client_ids": ["bitget"], "product_kinds": ["spot"],
               "scope_note": "Public spot currency registry; derivatives require independent underlying/unit evidence"},
    "kucoin": {"endpoint": "https://api.kucoin.com/api/v3/currencies",
               "docs_url": "https://www.kucoin.com/docs-new/rest/spot-trading/market-data/get-all-currencies",
               "client_ids": ["kucoin"], "product_kinds": ["spot"],
               "scope_note": "Spot currency IDs only; kucoinfutures requires independent underlying evidence"},
    "coinbase": {"endpoint": "https://api.exchange.coinbase.com/currencies",
                 "docs_url": "https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/currencies/get-all-known-currencies",
                 "client_ids": ["coinbaseexchange"], "product_kinds": ["spot"],
                 "scope_note": "Coinbase Exchange registry only; Advanced Trade client coinbase is not implicitly equivalent"},
}
_CHAINS = {"ETH": "ethereum", "ERC20": "ethereum", "ETHEREUM": "ethereum", "BSC": "bsc",
           "BEP20": "bsc", "BASE": "base", "ARBITRUM": "arbitrum", "SOL": "solana", "SOLANA": "solana"}
_SCALED = re.compile(r"^(?:[0-9]{2,}|[0-9]+[KM])[A-Z]")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def catalog_fingerprint(instrument: Instrument) -> str:
    value = {"id": instrument.id, "native_asset_id": instrument.native_asset_id or instrument.asset_id,
                    "base": instrument.base, "kind": instrument.kind, "chain": instrument.chain,
                    "contract_size": instrument.contract_size, "linear": instrument.linear}
    if instrument.native_currency_id is not None:
        value["native_currency_id"] = instrument.native_currency_id
    return _digest(value)


def public_source_scope(instrument: Instrument, venue: str) -> tuple[str | None, str | None]:
    """Require original baseId, correct business client, product and native id."""
    source = SOURCES.get(venue)
    if source is None or instrument.venue != venue:
        return None, "source_venue_not_supported"
    native = instrument.native_asset_id or instrument.asset_id
    parts = native.split(":")
    if len(parts) != 4 or parts[:2] != ["cex", venue]:
        return None, "native_currency_namespace_unresolved"
    if parts[2] not in source["client_ids"]:
        return None, "native_client_out_of_source_scope"
    if instrument.kind not in source["product_kinds"]:
        return None, "product_out_of_source_scope"
    if not instrument.native_currency_id:
        return None, "original_currency_id_missing"
    if parts[3] != instrument.native_currency_id:
        return None, "original_currency_id_conflicts_with_native_identity"
    return instrument.native_currency_id, None


def project_mapping_metadata(instrument: Instrument, metadata: dict, evidence_statuses: dict | None = None,
                             conflicting_evidence: bool = False) -> Instrument:
    status = metadata.get("status", "unverified")
    if metadata.get("catalog_fingerprint") != catalog_fingerprint(instrument):
        status = "catalog_changed"
    if metadata.get("canonical_asset_id") != instrument.asset_id:
        status = "mapping_changed"
    if evidence_statuses is not None and (not metadata.get("evidence_ids") or any(
            evidence_statuses.get(identity) != "active" for identity in metadata.get("evidence_ids", []))):
        status = "evidence_changed"
    if conflicting_evidence:
        status = "conflicting_evidence"
    if metadata.get("expires_at", 0) <= now_ms():
        status = "expired"
    return instrument.model_copy(update={
        "mapping_evidence_ids": metadata.get("evidence_ids", []),
        "mapping_verified_at": metadata.get("verified_at"), "mapping_expires_at": metadata.get("expires_at"),
        "mapping_evidence_status": status, "mapping_verified": instrument.mapping_verified and status == "verified"})


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    instrument_id: str = Field(min_length=1, max_length=400)
    native_asset_id: str = Field(min_length=1, max_length=300)
    canonical_asset_id: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=8, max_length=1500)
    source_field: str = Field(min_length=1, max_length=300)
    observed_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    relationship: str = Field(default="same_asset", max_length=30)
    unit_multiplier: float = Field(default=1, gt=0)
    note: str = Field(min_length=1, max_length=1500)
    supersedes_evidence_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_source(self):
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query:
            raise ValueError("Use a credential-free HTTPS reference without query parameters")
        if not self.observed_at < self.expires_at <= self.observed_at + 90 * 86400000:
            raise ValueError("Evidence expires_at must be within 90 days of its observation")
        address = _chain_address(self.canonical_asset_id)
        if address:
            self.canonical_asset_id = f"{address[0]}:{address[1]}"
        return self


class EvidenceBatch(BaseModel):
    items: list[EvidenceInput] = Field(min_length=1, max_length=10000)


class Selection(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=10000)


class ApplySelection(Selection):
    preview_token: str = Field(min_length=64, max_length=64)


class RevokeSelection(Selection):
    reason: str = Field(min_length=1, max_length=1500)


class RefreshSources(BaseModel):
    venues: list[str] = Field(default_factory=lambda: ["gate", "bitget", "kucoin"], min_length=1, max_length=len(SOURCES))


def parse_public_metadata(venue: str, payload) -> list[dict]:
    """Parse only published public currency schemas; no name-based global ids."""
    if venue == "bitget":
        if not isinstance(payload, dict) or payload.get("code") != "00000":
            raise ValueError("Invalid Bitget public coins response")
        payload = payload.get("data")
    elif venue == "kucoin":
        if not isinstance(payload, dict) or payload.get("code") != "200000":
            raise ValueError("Invalid KuCoin public currencies response")
        payload = payload.get("data")
    if venue not in SOURCES or not isinstance(payload, list):
        raise ValueError("Invalid public currency list")
    result = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        code = row.get("currency" if venue in {"gate", "kucoin"} else "id" if venue == "coinbase" else "coin")
        if not isinstance(code, str) or not code:
            continue
        contracts, unresolved = set(), False
        chains = row.get("supported_networks" if venue == "coinbase" else "chains")
        if not isinstance(chains, list) or not chains:
            unresolved = True
            chains = []
        selected_fields = []
        for network in chains:
            if not isinstance(network, dict):
                unresolved = True
                continue
            name_key = {"gate": "name", "bitget": "chain", "kucoin": "chainId", "coinbase": "id"}[venue]
            name = network.get(name_key)
            if venue == "kucoin" and not name:
                name = network.get("chainName")
            address = network.get("addr" if venue == "gate" else "contract_address" if venue == "coinbase"
                                  else "contractAddress")
            selected_fields.append({"network": name, "address": address,
                                    **({"chainName": network.get("chainName")} if venue == "kucoin" else {})})
            chain = _CHAINS.get(str(name).upper())
            if venue == "kucoin" and network.get("chainId") and network.get("chainName"):
                display_chain = _CHAINS.get(str(network["chainName"]).upper())
                if display_chain and display_chain != chain:
                    unresolved = True
                    continue
            identity = _chain_address(f"{chain}:{address}") if chain and isinstance(address, str) else None
            if identity and identity[1].lower() in {"0x" + "0" * 40, "0x" + "e" * 40}:
                identity = None  # Native placeholders are not token contracts.
            if identity is None:
                unresolved = True
            else:
                contracts.add(f"{identity[0]}:{identity[1]}")
        blockers = []
        if unresolved:
            blockers.append("network_or_contract_identity_incomplete")
        if len(contracts) != 1:
            blockers.append("multiple_or_missing_contract_identities")
        if _SCALED.match(code.upper()):
            blockers.append("base_unit_scale_requires_review")
        result.append({"currency_id": code, "canonical_asset_id": next(iter(contracts))
                       if len(contracts) == 1 and not blockers else None,
                       "contracts": sorted(contracts), "blockers": blockers,
                       "source_field": f"currencies[{index}]." + ("supported_networks" if venue == "coinbase" else "chains"),
                       "source_digest": _digest(selected_fields)})
    return result


class AssetVerificationService:
    def __init__(self, store, collector, client: httpx.AsyncClient | None = None, clock=now_ms):
        self.store, self.collector, self.client, self.clock = store, collector, client, clock
        self._owns_client = client is None
        with store.lock, store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS asset_identity_evidence (
                    id TEXT PRIMARY KEY, record TEXT NOT NULL, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS asset_identity_sources (
                    venue TEXT PRIMARY KEY, record TEXT NOT NULL);
            """)
            unsupported_old_proof = []
            catalog_for_scope = store.instruments()
            for evidence_id, value in db.execute("SELECT id,record FROM asset_identity_evidence WHERE status='active'"):
                fact = json.loads(value)
                if fact["evidence_kind"] == "public_contract_metadata":
                    instrument = catalog_for_scope.get(fact["instrument_id"])
                    raw, issue = public_source_scope(instrument, fact["venue"]) if instrument else (None, "missing")
                    if issue or not fact.get("native_currency_id") or raw != fact["native_currency_id"]:
                        unsupported_old_proof.append(evidence_id)
            if unsupported_old_proof:
                db.executemany("UPDATE asset_identity_evidence SET status='scope_unverified' WHERE id=?",
                               [(value,) for value in unsupported_old_proof])
                self._audit(db, "asset_public_scope_downgrade", {"evidence_ids": unsupported_old_proof,
                    "reason": "Original currency ID or reviewed business/product scope cannot be established"})
        if unsupported_old_proof:
            collector.instruments = store.instruments()

    async def close(self):
        if self._owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    def _records(self) -> list[dict]:
        with self.store.connect() as db:
            return [{**json.loads(value), "status": status}
                    for value, status in db.execute("SELECT record,status FROM asset_identity_evidence ORDER BY id")]

    def _audit(self, db, action: str, payload: dict):
        db.execute("INSERT INTO audit(at,action,payload) VALUES(?,?,?)",
                   (self.clock(), action, json.dumps(payload, allow_nan=False)))

    def _insert(self, records: list[dict], *, supersede_public=False):
        inserted = 0
        with self.store.lock, self.store.connect() as db:
            previous = self._records()
            previous_by_id = {row["id"]: row for row in previous}
            # Validate the entire batch before superseding or inserting anything.
            for record in records:
                for old_id in record.get("supersedes_evidence_ids", []):
                    old = previous_by_id.get(old_id)
                    if old is None or old["instrument_id"] != record["instrument_id"]:
                        raise ValueError("Superseded evidence must belong to the same instrument")
            for record in records:
                old_ids = list(record.get("supersedes_evidence_ids", []))
                if supersede_public:
                    old_ids.extend(row["id"] for row in previous if row["instrument_id"] == record["instrument_id"]
                                   and row["evidence_kind"] == record["evidence_kind"]
                                   and row["source_url"] == record["source_url"] and row["id"] != record["id"])
                for old_id in old_ids:
                    db.execute("UPDATE asset_identity_evidence SET status='superseded' WHERE id=?", (old_id,))
                inserted += db.execute("INSERT OR IGNORE INTO asset_identity_evidence VALUES(?,?,'active')",
                                       (record["id"], json.dumps(record, allow_nan=False))).rowcount
            self._audit(db, "asset_evidence_import", {"evidence_ids": [row["id"] for row in records],
                                                    "superseded": supersede_public})
        self.collector.instruments = self.store.instruments()
        return {"evidence_ids": [row["id"] for row in records], "imported": inserted, "submitted": len(records)}

    def add_evidence(self, items: list[EvidenceInput]) -> dict:
        catalog = self.store.instruments()
        records = []
        for item in items:
            instrument = catalog.get(item.instrument_id)
            if instrument is None or (instrument.native_asset_id or instrument.asset_id) != item.native_asset_id:
                raise ValueError("Instrument or immutable native identity changed; no evidence imported")
            if item.observed_at > self.clock() or item.expires_at <= self.clock():
                raise ValueError("Evidence observation is future or expired")
            record = {**item.model_dump(), "evidence_kind": "manual_review", "venue": instrument.venue,
                      "catalog_fingerprint": catalog_fingerprint(instrument), "blockers": [],
                      "contracts": [], "source_digest": None}
            record["id"] = _digest(record)
            records.append(record)
        return self._insert(records)

    async def public_refresh(self, venues: list[str]) -> dict:
        if any(venue not in SOURCES for venue in venues):
            raise ValueError("Only reviewed public contract registries are supported: " + ", ".join(SOURCES))
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=20, follow_redirects=False)
        catalog = self.store.instruments()
        records, attempts = [], []
        for venue in dict.fromkeys(venues):
            source = SOURCES[venue]
            attempt = {"venue": venue, **source, "state": "error", "last_attempt_at": self.clock(),
                       "received_at": None, "response_rows": 0, "matched_markets": 0,
                       "eligible_markets": 0, "blocked_markets": 0, "error_type": None,
                       "scope_count_scope": "active_catalog", "scope_eligible_markets": 0,
                       "scope_rejected_markets": 0, "scope_reasons": {}, "unmatched_currency_markets": 0}
            scoped = []
            scope_reasons = Counter()
            for instrument in catalog.values():
                if not instrument.active or instrument.venue != venue:
                    continue
                currency_id, problem = public_source_scope(instrument, venue)
                if problem:
                    scope_reasons[problem] += 1
                else:
                    scoped.append((instrument, currency_id))
            attempt.update(scope_eligible_markets=len(scoped), scope_rejected_markets=sum(scope_reasons.values()),
                           scope_reasons=dict(scope_reasons))
            try:
                response = await self.client.get(source["endpoint"], headers={"Accept": "application/json"})
                response.raise_for_status()
                if len(response.content) > 20 * 1024 * 1024:
                    raise ValueError("Public metadata response exceeded size limit")
                payload = response.json()
                parsed = parse_public_metadata(venue, payload)
                observed = self.clock()
                by_code = defaultdict(list)
                for currency in parsed:
                    by_code[currency["currency_id"]].append(currency)
                matched, eligible = set(), set()
                for instrument, currency_id in scoped:
                    native = instrument.native_asset_id or instrument.asset_id
                    # This is a venue-local currency id join, not ticker matching
                    # across exchanges or stripping 1000/bridge/wrapped prefixes.
                    for currency in by_code.get(currency_id, []):
                        matched.add(instrument.id)
                        if not currency["blockers"]:
                            eligible.add(instrument.id)
                        record = {**currency, "instrument_id": instrument.id, "native_asset_id": native,
                                  "native_currency_id": currency_id,
                                  "venue": venue, "source_url": source["endpoint"],
                                  "observed_at": observed, "expires_at": observed + 7 * 86400000,
                                  "evidence_kind": "public_contract_metadata", "relationship": "same_asset",
                                  "unit_multiplier": 1, "note": "Public currency-id/chain/contract metadata",
                                  "catalog_fingerprint": catalog_fingerprint(instrument)}
                        record["id"] = _digest(record)
                        records.append(record)
                attempt.update(state="observed", received_at=observed,
                               response_rows=len(payload if isinstance(payload, list) else payload["data"]),
                               matched_markets=len(matched), eligible_markets=len(eligible),
                               blocked_markets=len(matched) - len(eligible),
                               unmatched_currency_markets=len(scoped) - len(matched))
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                attempt["error_type"] = type(exc).__name__
            attempts.append(attempt)
            with self.store.lock, self.store.connect() as db:
                db.execute("INSERT INTO asset_identity_sources VALUES(?,?) ON CONFLICT(venue) DO UPDATE SET record=excluded.record",
                           (venue, json.dumps(attempt)))
                self._audit(db, "asset_public_source_attempt", attempt)
        # Native chain-address identities provide exact matching evidence for
        # on-chain markets without pretending a new exchange event was observed.
        canonical = {record["canonical_asset_id"] for record in records if record["canonical_asset_id"]}
        for instrument in catalog.values():
            if not instrument.active:
                continue
            native = instrument.native_asset_id or instrument.asset_id
            address = _chain_address(native)
            if address is None or f"{address[0]}:{address[1]}" not in canonical:
                continue
            observed = self.clock()
            record = {"instrument_id": instrument.id, "native_asset_id": native, "venue": instrument.venue,
                      "canonical_asset_id": f"{address[0]}:{address[1]}", "contracts": [f"{address[0]}:{address[1]}"],
                      "source_url": None, "source_field": "instrument.native_asset_id",
                      "source_digest": _digest(native), "observed_at": observed, "expires_at": observed + 7 * 86400000,
                      "evidence_kind": "catalog_contract_identity", "relationship": "same_asset", "unit_multiplier": 1,
                      "note": "Locally read native chain-address identity; not an exchange event timestamp",
                      "catalog_fingerprint": catalog_fingerprint(instrument), "blockers": []}
            record["id"] = _digest(record)
            records.append(record)
        imported = self._insert(records, supersede_public=True) if records else {"evidence_ids": [], "imported": 0}
        return {**imported, "sources": attempts, "mappings_changed": False}

    def preview(self, evidence_ids: list[str] | None = None) -> dict:
        catalog, now = self.store.instruments(), self.clock()
        all_records = self._records()
        return self._preview(catalog, all_records, now, evidence_ids)

    @staticmethod
    def _preview(catalog, all_records, now, evidence_ids=None) -> dict:
        """Build proposals/token from exactly the caller's captured inputs."""
        selected = set(evidence_ids) if evidence_ids is not None else {row["id"] for row in all_records if row["status"] == "active"}
        records = [row for row in all_records if row["id"] in selected]
        if len(records) != len(selected):
            raise ValueError("One or more evidence records do not exist")
        known_targets = defaultdict(set)
        for record in all_records:
            if record["status"] == "active" and record["expires_at"] > now and record["canonical_asset_id"]:
                known_targets[record["instrument_id"]].add(record["canonical_asset_id"])
        grouped = defaultdict(list)
        confirmed = defaultdict(list)
        for instrument in catalog.values():
            if instrument.mapping_verified:
                confirmed[instrument.asset_id].append(instrument)
        for record in records:
            grouped[record["canonical_asset_id"] or "unresolved:" + record["instrument_id"]].append(record)
        proposals = []
        for target, facts in sorted(grouped.items()):
            issues = set()
            projected = {}
            for fact in facts:
                instrument = catalog.get(fact["instrument_id"])
                issues.update(fact.get("blockers", []))
                if fact["status"] != "active":
                    issues.add("evidence_not_active")
                if fact["expires_at"] <= now or fact["observed_at"] > now:
                    issues.add("evidence_expired_or_future")
                if instrument is None or catalog_fingerprint(instrument) != fact["catalog_fingerprint"]:
                    issues.add("catalog_identity_changed")
                if fact["evidence_kind"] == "public_contract_metadata" and instrument is not None:
                    currency_id, scope_issue = public_source_scope(instrument, fact["venue"])
                    if scope_issue or currency_id != fact.get("native_currency_id"):
                        issues.add("public_native_currency_scope_unverified")
                if len(known_targets[fact["instrument_id"]]) > 1:
                    issues.add("conflicting_active_evidence")
                if fact["relationship"] != "same_asset" or fact["unit_multiplier"] != 1:
                    issues.add("relationship_or_base_unit_requires_review")
                if instrument is not None and _SCALED.match(instrument.base.upper()):
                    issues.add("base_unit_scale_requires_review")
                if fact["canonical_asset_id"] is None:
                    issues.add("canonical_identity_unresolved")
                if instrument is not None:
                    projected[instrument.id] = instrument.model_copy(update={"asset_id": target, "mapping_verified": True,
                        "mapping_verified_at": now, "mapping_expires_at": min(row["expires_at"] for row in facts),
                        "mapping_evidence_status": "verified"})
            for instrument in confirmed[target]:
                if instrument.id not in projected:
                    projected[instrument.id] = instrument
            checked = [row for row in identity_candidates(projected.values(), as_of=now) if row["kind"] == "identity"]
            for row in checked:
                issues.update(row["blockers"])
            ids = sorted({fact["instrument_id"] for fact in facts})
            proposals.append({"id": _digest({"target": target, "evidence_ids": sorted(row["id"] for row in facts)}),
                "canonical_asset_id": facts[0]["canonical_asset_id"], "instrument_ids": ids,
                "symbols": sorted({catalog[item].base for item in ids if item in catalog}),
                "venues": sorted({catalog[item].venue for item in ids if item in catalog}),
                "status": "ready" if not issues else "unverified" if "canonical_identity_unresolved" in issues else "conflict",
                "blockers": sorted(issues), "evidence_ids": sorted(row["id"] for row in facts),
                "source_urls": sorted({row["source_url"] for row in facts if row["source_url"]}),
                "observed_at": max(row["observed_at"] for row in facts), "expires_at": min(row["expires_at"] for row in facts)})
        bindings = [(identity, catalog_fingerprint(catalog[identity]), catalog[identity].asset_id,
                     catalog[identity].mapping_evidence_ids) for identity in sorted({row["instrument_id"] for row in records})
                    if identity in catalog]
        token = _digest({"selected": sorted(selected), "bindings": bindings, "proposals": proposals})
        return {"as_of": now, "proposals": proposals, "preview_token": token,
                "ready": sum(row["status"] == "ready" for row in proposals),
                "blocked": sum(row["status"] != "ready" for row in proposals)}

    def apply(self, evidence_ids: list[str], preview_token: str) -> dict:
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            preview = self.preview(evidence_ids)
            if preview["preview_token"] != preview_token:
                raise ValueError("Preview changed; review the new preview before applying")
            if preview["blocked"] or not preview["proposals"]:
                raise ValueError("Batch has unresolved evidence or conflicts; no mappings changed")
            catalog = self.store.instruments()
            for group in preview["proposals"]:
                evidence = {identity: {"status": "verified", "canonical_asset_id": group["canonical_asset_id"],
                    "catalog_fingerprint": catalog_fingerprint(catalog[identity]), "evidence_ids": group["evidence_ids"],
                    "verified_at": self.clock(), "expires_at": group["expires_at"]} for identity in group["instrument_ids"]}
                self.store.map_assets(group["instrument_ids"], group["canonical_asset_id"], evidence=evidence, connection=db)
            self._audit(db, "asset_verification_apply", {"evidence_ids": sorted(set(evidence_ids)),
                                                       "preview_token": preview_token})
        self.collector.instruments = self.store.instruments()
        return {"applied_groups": len(preview["proposals"]),
                "applied_markets": sum(len(row["instrument_ids"]) for row in preview["proposals"]),
                "evidence_ids": sorted(set(evidence_ids)), "audit": self.store.audit()}

    def revoke(self, evidence_ids: list[str], reason: str) -> dict:
        known = {row["id"] for row in self._records()}
        if not set(evidence_ids) <= known:
            raise ValueError("One or more evidence records do not exist")
        affected = []
        with self.store.lock, self.store.connect() as db:
            for identity, metadata in db.execute("SELECT instrument_id,metadata FROM asset_mapping_verifications"):
                if set(json.loads(metadata).get("evidence_ids", [])).intersection(evidence_ids):
                    affected.append(identity)
            db.executemany("UPDATE asset_identity_evidence SET status='revoked' WHERE id=?", [(value,) for value in evidence_ids])
            self.store.unmap_assets(affected, reason=reason, connection=db)
            self._audit(db, "asset_evidence_revoke", {"evidence_ids": evidence_ids, "reason": reason,
                                                      "affected_instrument_ids": affected})
        self.collector.instruments = self.store.instruments()
        return {"revoked_evidence": len(set(evidence_ids)), "affected_instrument_ids": affected,
                "audit": self.store.audit()}

    def review_packet(self, *, offset=0, limit=100) -> dict:
        """Read-only review export; proposals never count as confirmed coverage.

        Ready selection is limited to this page and must still pass the existing
        atomic apply revalidation. No mappings or evidence are created here.
        """
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("Review packet requires nonnegative offset and limit 1..200")
        catalog = self.store.instruments()
        now = self.clock()
        records = self._records()
        preview = self._preview(catalog, records, now)
        facts = {row["id"]: row for row in records}
        rows = preview["proposals"][offset:offset + limit]
        packets = []
        for row in rows:
            members = [catalog[identity] for identity in row["instrument_ids"] if identity in catalog]
            packets.append({**row, "review_state": "awaiting_review" if row["status"] == "ready" else "blocked",
                "markets": [{"instrument_id": item.id, "venue": item.venue, "active": item.active,
                    "native_symbol": item.native_symbol, "native_asset_id": item.native_asset_id or item.asset_id,
                    "native_currency_id": item.native_currency_id, "kind": item.kind,
                    "base": item.base, "quote": item.quote, "chain": item.chain,
                    "contract_size": item.contract_size, "linear": item.linear,
                    "current_asset_id": item.asset_id, "mapping_verified": item.mapping_verified,
                    "catalog_fingerprint": catalog_fingerprint(item)} for item in members],
                "evidence": [facts[identity] for identity in row["evidence_ids"] if identity in facts]})
        ready_ids = sorted({identity for row in rows if row["status"] == "ready" for identity in row["evidence_ids"]})
        ready_preview = self._preview(catalog, records, now, ready_ids) if ready_ids else None
        actual = [row for row in identity_candidates((item for item in catalog.values() if item.active), as_of=now)
                  if row["kind"] == "identity" and row["comparison_ready"] and len(row["venues"]) > 1]
        return {"as_of": now, "read_only": True, "coverage_scope": "active_catalog_identity_gate_only",
            "actual_comparable_cross_venue_groups": len(actual),
            "actual_comparable_cross_venue_markets": len({identity for row in actual for identity in row["instrument_ids"]}),
            "actual_groups": [{key: row[key] for key in ("id", "asset_ids", "venues", "instrument_ids")}
                              for row in actual[:limit]],
            "actual_groups_truncated": len(actual) > limit,
            "proposal_groups": len(preview["proposals"]), "ready_proposal_groups": preview["ready"],
            "blocked_proposal_groups": preview["blocked"], "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(preview["proposals"]) else None,
            "packets": packets, "page_ready_selection": {"evidence_ids": ready_ids,
                "preview_token": ready_preview["preview_token"] if ready_preview else None},
            "review_note": "Ready means eligible for explicit review, not approved or profitable. Verify exact "
                           "native currency/client/product, chain/address, units, source and expiry. "
                           "Prices, quote FX, depth and costs require independent checks."}

    def status(self, limit=200) -> dict:
        catalog = self.store.instruments()
        records = self._records()
        preview = self.preview()
        instruments = list(catalog.values())
        active = [item for item in instruments if item.active]
        verified = [item for item in active if item.mapping_verified and
                    (item.mapping_expires_at is None or item.mapping_expires_at > self.clock())]
        traceable = [item for item in verified if item.mapping_evidence_ids]
        with self.store.connect() as db:
            attempts = {venue: json.loads(record) for venue, record in db.execute("SELECT venue,record FROM asset_identity_sources")}
        sources = [{"venue": venue, **source, "state": "not_requested", "last_attempt_at": None,
                    "received_at": None, "response_rows": 0, "matched_markets": 0, "eligible_markets": 0,
                    "blocked_markets": 0, "error_type": None, "scope_count_scope": "active_catalog",
                    "scope_eligible_markets": 0, "scope_rejected_markets": 0, "scope_reasons": {},
                    "unmatched_currency_markets": 0, **attempts.get(venue, {}),
                    "client_ids": source["client_ids"], "product_kinds": source["product_kinds"]}
                   for venue, source in SOURCES.items()]
        current_public = [fact for fact in records if fact["status"] == "active"
                          and fact["evidence_kind"] == "public_contract_metadata"
                          and fact["expires_at"] > self.clock() and fact["instrument_id"] in catalog
                          and catalog[fact["instrument_id"]].active
                          and fact["catalog_fingerprint"] == catalog_fingerprint(catalog[fact["instrument_id"]])]
        by_venue = defaultdict(list)
        for instrument in active:
            by_venue[instrument.venue].append(instrument)
        platform_coverage = []
        for venue in sorted(set(by_venue) | set(SOURCES)):
            members = by_venue[venue]
            problems = Counter()
            for instrument in members:
                _, problem = public_source_scope(instrument, venue)
                if problem:
                    problems[problem] += 1
            facts = [fact for fact in current_public if fact["venue"] == venue]
            member_verified = [item for item in members if item.mapping_verified]
            blocked_reasons = defaultdict(set)
            for fact in facts:
                for problem in fact["blockers"]:
                    blocked_reasons[problem].add(fact["instrument_id"])
            platform_coverage.append({"venue": venue, "active_markets": len(members),
                "source_supported": venue in SOURCES, "client_ids": SOURCES.get(venue, {}).get("client_ids", []),
                "product_kinds": SOURCES.get(venue, {}).get("product_kinds", []),
                "source_scope_markets": len(members) - sum(problems.values()),
                "scope_rejected_markets": sum(problems.values()), "scope_reasons": dict(problems),
                "source_covered_markets": len({fact["instrument_id"] for fact in facts if not fact["blockers"]}),
                "source_blocked_markets": len({fact["instrument_id"] for fact in facts if fact["blockers"]}),
                "source_blockers": {problem: len(ids) for problem, ids in blocked_reasons.items()},
                "verified_markets": len(member_verified),
                "traceable_verified_markets": sum(bool(item.mapping_evidence_ids) for item in member_verified),
                "unverified_markets": len(members) - len(member_verified),
                "conflicting_markets": sum(item.mapping_evidence_status == "conflicting_evidence" for item in members),
                "scope_note": SOURCES.get(venue, {}).get("scope_note", "")})
        return {"as_of": self.clock(), "coverage": {"catalog_markets": len(instruments),
            "active_markets": len(active), "inactive_markets": len(instruments) - len(active),
            "market_count_scope": "active_catalog", "verified_markets": len(verified),
            "traceable_verified_markets": len(traceable), "legacy_verified_markets": len(verified) - len(traceable),
            "unverified_markets": len(active) - len(verified),
            "expired_markets": sum(item.mapping_evidence_status == "expired" for item in active),
            "evidence_records": len(records), "source_covered_markets": len({row["instrument_id"] for row in records
                if row["status"] == "active" and row["evidence_kind"] == "public_contract_metadata"
                and not row["blockers"] and row["expires_at"] > self.clock() and row["instrument_id"] in catalog
                and catalog[row["instrument_id"]].active
                and row["catalog_fingerprint"] == catalog_fingerprint(catalog[row["instrument_id"]])}),
            "source_observed_markets": len({row["instrument_id"] for row in records
                                           if row["evidence_kind"] == "public_contract_metadata"}),
            "ready_proposals": preview["ready"], "conflict_proposals": preview["blocked"]},
            "sources": sources, "platform_coverage": platform_coverage,
            "proposals": preview["proposals"][:limit], "evidence": records[:limit],
            "audit": [row for row in self.store.audit() if row["action"].startswith("asset_")],
            "truncated": {"evidence": len(records) > limit, "proposals": len(preview["proposals"]) > limit}}


def _service(request):
    return request.app.state.asset_verification


@router.get("")
def verification_status(request: Request, limit: int = Query(200, ge=1, le=2000)):
    return _service(request).status(limit)


@router.get("/review-packet")
def review_packet(request: Request, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    return _service(request).review_packet(offset=offset, limit=limit)


@router.post("/public-refresh")
async def refresh_public(payload: RefreshSources, request: Request):
    try:
        return await _service(request).public_refresh(payload.venues)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/evidence")
def import_evidence(payload: EvidenceBatch, request: Request):
    try:
        return _service(request).add_evidence(payload.items)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/preview")
def preview_evidence(payload: Selection, request: Request):
    try:
        return _service(request).preview(payload.evidence_ids)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/apply")
def apply_evidence(payload: ApplySelection, request: Request):
    try:
        return _service(request).apply(payload.evidence_ids, payload.preview_token)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/revoke")
def revoke_evidence(payload: RevokeSelection, request: Request):
    try:
        return _service(request).revoke(payload.evidence_ids, payload.reason)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
