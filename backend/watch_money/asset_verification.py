"""Traceable public asset metadata import and explicit atomic mapping reviews."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .identity import _chain_address, identity_candidates
from .models import Instrument, now_ms

router = APIRouter(prefix="/api/assets/verification", tags=["asset-verification"])
SOURCES = {
    "gate": {"endpoint": "https://api.gateio.ws/api/v4/spot/currencies",
             "docs_url": "https://www.gate.com/docs/developers/apiv4/en/spot/#query-all-currency-information"},
    "bitget": {"endpoint": "https://api.bitget.com/api/v2/spot/public/coins",
               "docs_url": "https://www.bitget.com/docs/catalog/classic-spot-market/classic-spot-market"},
}
_CHAINS = {"ETH": "ethereum", "ERC20": "ethereum", "ETHEREUM": "ethereum", "BSC": "bsc",
           "BEP20": "bsc", "BASE": "base", "ARBITRUM": "arbitrum", "SOL": "solana", "SOLANA": "solana"}
_SCALED = re.compile(r"^(?:[0-9]{2,}|[0-9]+[KM])[A-Z]")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def catalog_fingerprint(instrument: Instrument) -> str:
    return _digest({"id": instrument.id, "native_asset_id": instrument.native_asset_id or instrument.asset_id,
                    "base": instrument.base, "kind": instrument.kind, "chain": instrument.chain,
                    "contract_size": instrument.contract_size, "linear": instrument.linear})


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
    venues: list[str] = Field(default_factory=lambda: ["gate", "bitget"], min_length=1, max_length=2)


def parse_public_metadata(venue: str, payload) -> list[dict]:
    """Parse only published public currency schemas; no name-based global ids."""
    if venue == "bitget":
        if not isinstance(payload, dict) or payload.get("code") != "00000":
            raise ValueError("Invalid Bitget public coins response")
        payload = payload.get("data")
    if venue not in SOURCES or not isinstance(payload, list):
        raise ValueError("Invalid public currency list")
    result = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            continue
        code = row.get("currency" if venue == "gate" else "coin")
        if not isinstance(code, str) or not code:
            continue
        contracts, unresolved = set(), False
        chains = row.get("chains")
        if not isinstance(chains, list) or not chains:
            unresolved = True
            chains = []
        selected_fields = []
        for network in chains:
            if not isinstance(network, dict):
                unresolved = True
                continue
            name = network.get("name" if venue == "gate" else "chain")
            address = network.get("addr" if venue == "gate" else "contractAddress")
            selected_fields.append({"network": name, "address": address})
            chain = _CHAINS.get(str(name).upper())
            identity = _chain_address(f"{chain}:{address}") if chain and isinstance(address, str) else None
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
        result.append({"currency_id": code.upper(), "canonical_asset_id": next(iter(contracts))
                       if len(contracts) == 1 and not blockers else None,
                       "contracts": sorted(contracts), "blockers": blockers,
                       "source_field": f"currencies[{index}].chains", "source_digest": _digest(selected_fields)})
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
            raise ValueError("Only Gate and Bitget reviewed public bulk endpoints are supported")
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=20, follow_redirects=False)
        catalog = self.store.instruments()
        records, attempts = [], []
        for venue in dict.fromkeys(venues):
            source = SOURCES[venue]
            attempt = {"venue": venue, **source, "state": "error", "last_attempt_at": self.clock(),
                       "received_at": None, "response_rows": 0, "matched_markets": 0,
                       "eligible_markets": 0, "blocked_markets": 0, "error_type": None}
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
                for instrument in catalog.values():
                    native = instrument.native_asset_id or instrument.asset_id
                    parts = native.split(":")
                    # This is a venue-local currency id join, not ticker matching
                    # across exchanges or stripping 1000/bridge/wrapped prefixes.
                    if len(parts) != 4 or parts[:3] != ["cex", venue, venue] or instrument.venue != venue:
                        continue
                    for currency in by_code.get(parts[3].upper(), []):
                        matched.add(instrument.id)
                        if not currency["blockers"]:
                            eligible.add(instrument.id)
                        record = {**currency, "instrument_id": instrument.id, "native_asset_id": native,
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
                               blocked_markets=len(matched) - len(eligible))
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
        sources = [attempts.get(venue, {"venue": venue, **source, "state": "not_requested", "last_attempt_at": None,
                    "received_at": None, "response_rows": 0, "matched_markets": 0, "eligible_markets": 0,
                    "blocked_markets": 0, "error_type": None}) for venue, source in SOURCES.items()]
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
            "sources": sources, "proposals": preview["proposals"][:limit], "evidence": records[:limit],
            "audit": [row for row in self.store.audit() if row["action"].startswith("asset_")],
            "truncated": {"evidence": len(records) > limit, "proposals": len(preview["proposals"]) > limit}}


def _service(request):
    return request.app.state.asset_verification


@router.get("")
def verification_status(request: Request, limit: int = Query(200, ge=1, le=2000)):
    return _service(request).status(limit)


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
