"""Public standard-tier fee references and explicitly assumed local overrides.

No account API is used. Catalog evidence, user assumptions, and persistence
timestamps are separate; merely reading a profile never refreshes its source.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ConfigDict, Field, model_validator

from .adapters.cex import SPECS
from .models import Instrument, Model, now_ms
from .storage import Storage

Kind = Literal["spot", "perpetual"]
Liquidity = Literal["maker", "taker"]
CATALOG_PATH = Path(__file__).with_name("adapters") / "public_fees.json"
OVERRIDES_KEY = "fee_profiles"
VENUE_PRODUCTS = {venue: set(spec.products) for venue, spec in SPECS.items()}
VENUE_PRODUCTS.update({venue: {"perpetual"} for venue in (
    "dydx", "paradex", "aster", "edgex", "variational", "gmx", "jupiter-perps", "drift",
)})
VENUE_PRODUCTS.update({"hyperliquid": {"spot", "perpetual"}, "lighter": {"spot", "perpetual"}})
VENUE_PRODUCTS.update({venue: {"spot"} for venue in (
    "uniswap", "pancakeswap", "aerodrome", "curve", "raydium", "orca",
)})
REVIEWED_SOURCE_PREFIXES = {
    "okx": ("https://www.okx.com/learn/", "https://www.okx.com/help/", "https://www.okx.com/fees"),
    "binance": ("https://www.binance.com/en/fee/",),
    "bybit": ("https://www.bybit.com/en/help-center/",),
    "bitget": ("https://www.bitget.com/support/",),
    "hyperliquid": ("https://hyperliquid.gitbook.io/hyperliquid-docs/",),
    "lighter": ("https://docs.lighter.xyz/",),
}


class FeeScope(Model):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    quote_currencies: list[str] = Field(default_factory=list, max_length=100)
    settlement_currencies: list[str] = Field(default_factory=list, max_length=100)
    linear: bool | None = None
    exclude_namespaces: bool = False
    requires_market_review: bool = False


class FeeProfileUpdate(Model):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    venue: str = Field(min_length=1, max_length=50)
    kind: Kind
    liquidity: Liquidity
    rate: float | None = Field(default=None, ge=-0.1, le=1, strict=True)
    source: str | None = Field(default=None, max_length=2000)
    conditions: str = Field(default="Explicit user fee assumption", max_length=4000)
    fee_currency: str | None = Field(default=None, max_length=200)
    updated_at: int | None = Field(default=None, ge=0)
    scope: FeeScope | None = None
    reset: bool = False

    @model_validator(mode="after")
    def explicit_rate_or_reset(self):
        if not self.reset and "rate" not in self.model_fields_set:
            raise ValueError("Provide an explicit rate (null means unknown), or reset=true")
        return self


class ReviewedFee(Model):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    venue: str
    kind: Kind
    liquidity: Liquidity
    rate: float = Field(ge=-0.1, le=1, strict=True)
    source: str = Field(min_length=1)
    conditions: str = Field(min_length=1)
    updated_at: int | None = Field(default=None, ge=0)
    checked_at: int = Field(gt=0)
    fee_currency: str | None = None
    tier: str = Field(default="standard", min_length=1)
    source_verified: Literal[True]
    evidence: str = Field(min_length=1)
    scope: FeeScope = Field(default_factory=FeeScope)

    @model_validator(mode="after")
    def reviewed_public_source(self):
        parsed = urlsplit(self.source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Reviewed public fee evidence requires a credential-free HTTPS source")
        if not self.source.startswith(REVIEWED_SOURCE_PREFIXES.get(self.venue, ())):
            raise ValueError("Fee source is outside the reviewed official publisher list")
        return self


def profile_key(venue: str, kind: str, liquidity: str) -> str:
    if venue not in VENUE_PRODUCTS or kind not in {"spot", "perpetual"} or liquidity not in {"maker", "taker"}:
        raise ValueError("Unknown fee profile identity")
    return f"{venue}:{kind}:{liquidity}"


class FeeProfileService:
    def __init__(self, store: Storage, catalog_path: str | Path | None = None,
                 clock: Callable[[], int] | None = None):
        self.store = store
        self.clock = clock or now_ms
        path = Path(catalog_path) if catalog_path is not None else CATALOG_PATH
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if catalog.get("schema_version") != 1:
            raise ValueError("Unsupported public fee catalog schema")
        self.defaults = {}
        for raw in catalog.get("reviewed_profiles", []):
            entry = ReviewedFee.model_validate(raw)
            key = profile_key(entry.venue, entry.kind, entry.liquidity)
            if key in self.defaults:
                raise ValueError("Duplicate reviewed fee profile")
            if entry.kind not in VENUE_PRODUCTS[entry.venue]:
                raise ValueError("Reviewed fee profile product is outside local venue scope")
            self.defaults[key] = entry.model_dump()

    def _default(self, venue: str, kind: Kind, liquidity: Liquidity) -> dict:
        key = profile_key(venue, kind, liquidity)
        result = {"key": key, "venue": venue, "kind": kind, "liquidity": liquidity,
                  "rate": None, "source": None, "conditions": "No reviewed public standard-tier fee available",
                  "updated_at": None, "checked_at": None, "saved_at": None, "fee_currency": None, "tier": "standard",
                  "source_verified": False, "evidence_status": "unknown", "scope": FeeScope().model_dump()}
        if kind not in VENUE_PRODUCTS[venue]:
            result.update(evidence_status="not_applicable", conditions="Product outside this venue profile's declared scope")
        elif key in self.defaults:
            result.update(self.defaults[key])
            result["evidence_status"] = "official_public"
            if ((result["updated_at"] is not None and result["updated_at"] > self.clock())
                    or result["checked_at"] > self.clock()):
                result.update(rate=None, source_verified=False, evidence_status="future_evidence")
        return result

    @staticmethod
    def _apply_scope(profile: dict, instrument: Instrument) -> dict:
        scope = profile["scope"]
        reasons = []
        if scope["quote_currencies"] and instrument.quote not in scope["quote_currencies"]:
            reasons.append("quote currency outside reviewed scope")
        if scope["settlement_currencies"] and instrument.settle not in scope["settlement_currencies"]:
            reasons.append("settlement currency outside reviewed scope")
        if scope["linear"] is not None and instrument.linear != scope["linear"]:
            reasons.append("contract type outside reviewed scope")
        if scope["exclude_namespaces"] and ":" in instrument.native_symbol:
            reasons.append("namespace/deployer-specific fees require separate evidence")
        if scope["requires_market_review"] and profile["evidence_status"] == "official_public":
            reasons.append("market zone/discount eligibility cannot be verified from instrument metadata")
        if reasons:
            return {**profile, "rate": None, "source_verified": False, "evidence_status": "out_of_scope",
                    "conditions": profile["conditions"] + "; " + "; ".join(reasons)}
        return profile

    def _row(self, venue: str, kind: Kind, liquidity: Liquidity, overrides: dict,
             instrument: Instrument | None = None) -> dict:
        default = self._default(venue, kind, liquidity)
        override = overrides.get(default["key"])
        effective = dict(override if override is not None else default)
        if effective.get("updated_at") is not None and effective["updated_at"] > self.clock():
            effective.update(rate=None, source_verified=False, evidence_status="future_evidence")
        if instrument is not None:
            effective = self._apply_scope(effective, instrument)
        return {"key": default["key"], "venue": venue, "kind": kind, "liquidity": liquidity,
                "applicable": kind in VENUE_PRODUCTS[venue],
                "default": default, "override": override, "effective": effective}

    def effective(self, venue: str, kind: Kind, liquidity: Liquidity) -> dict:
        return self._row(venue, kind, liquidity, self.store.get(OVERRIDES_KEY, {}))["effective"]

    def resolve(self, instrument: Instrument, liquidity: Liquidity) -> dict:
        kind = "perpetual" if instrument.kind == "perpetual" else "spot"
        return self._row(instrument.venue, kind, liquidity, self.store.get(OVERRIDES_KEY, {}), instrument)["effective"]

    def audit(self) -> list[dict]:
        with self.store.connect() as db:
            rows = db.execute("SELECT at,action,payload FROM audit WHERE action IN "
                              "('fee_profile_override','fee_profile_reset') ORDER BY id DESC LIMIT 500").fetchall()
        return [{"at": at, "action": action, "payload": json.loads(payload)} for at, action, payload in rows]

    def get(self, venue: str | None = None, kind: Kind | None = None, liquidity: Liquidity | None = None,
            instrument: Instrument | None = None) -> dict:
        if instrument is not None:
            venue, kind = instrument.venue, "perpetual" if instrument.kind == "perpetual" else "spot"
        if venue is not None and venue not in VENUE_PRODUCTS:
            raise ValueError("Unknown fee venue")
        overrides = self.store.get(OVERRIDES_KEY, {})
        profiles = [self._row(v, k, role, overrides, instrument)
                    for v in sorted(VENUE_PRODUCTS) if venue is None or v == venue
                    for k in ("spot", "perpetual") if kind is None or k == kind
                    for role in ("maker", "taker") if liquidity is None or role == liquidity]
        return {"profiles": profiles, "audit": self.audit(), "as_of": self.clock(),
                "scope": "public_standard_tier_and_explicit_user_assumptions", "account_api_used": False}

    def update(self, payload: FeeProfileUpdate) -> dict:
        key = profile_key(payload.venue, payload.kind, payload.liquidity)
        if payload.kind not in VENUE_PRODUCTS[payload.venue]:
            raise ValueError("Product outside this venue profile's declared scope")
        at = self.clock()
        default = self._default(payload.venue, payload.kind, payload.liquidity)
        with self.store.lock, self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute("SELECT value FROM kv WHERE key=?", (OVERRIDES_KEY,)).fetchone()
            overrides = json.loads(saved[0]) if saved else {}
            previous = overrides.get(key)
            previous_effective = self._row(payload.venue, payload.kind, payload.liquidity, overrides)["effective"]
            if payload.reset:
                overrides.pop(key, None)
                updated = None
            else:
                updated = {"key": key, "venue": payload.venue, "kind": payload.kind,
                           "liquidity": payload.liquidity, "rate": payload.rate, "source": payload.source,
                           "conditions": payload.conditions, "fee_currency": payload.fee_currency,
                           "updated_at": payload.updated_at if payload.updated_at is not None else at,
                           "saved_at": at, "checked_at": None, "tier": "user_assumption", "source_verified": False,
                           "evidence_status": "user_override", "scope": payload.scope.model_dump()
                           if payload.scope is not None else default["scope"]}
                overrides[key] = updated
            db.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (OVERRIDES_KEY, json.dumps(overrides, allow_nan=False)))
            audit = {"key": key, "previous_override": previous, "override": updated,
                     "previous_effective": previous_effective,
                     "effective": self._row(payload.venue, payload.kind, payload.liquidity, overrides)["effective"]}
            db.execute("INSERT INTO audit(at,action,payload) VALUES (?,?,?)", (at,
                       "fee_profile_reset" if payload.reset else "fee_profile_override",
                       json.dumps(audit, allow_nan=False)))
        row = self._row(payload.venue, payload.kind, payload.liquidity, overrides)
        return {"profile": row, "audit": self.audit()}


router = APIRouter(prefix="/api/fees", tags=["fees"])


def _service(request: Request) -> FeeProfileService:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Fee profile storage is unavailable")
    return FeeProfileService(store)


@router.get("")
def get_profiles(request: Request, venue: str | None = None, kind: Kind | None = None,
                 liquidity: Liquidity | None = None, instrument_id: str | None = Query(default=None)):
    service = _service(request)
    instrument = None
    if instrument_id is not None:
        collector = getattr(request.app.state, "collector", None)
        instruments = collector.instruments if collector is not None else service.store.instruments()
        instrument = instruments.get(instrument_id)
        if instrument is None:
            raise HTTPException(404, "Unknown instrument for fee profile scope")
    try:
        return service.get(venue, kind, liquidity, instrument)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("")
def update_profile(payload: FeeProfileUpdate, request: Request):
    try:
        return _service(request).update(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
