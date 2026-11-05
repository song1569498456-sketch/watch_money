"""Explicit conservative policy for official spot contract evidence; never auto-applies."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from .asset_verification import SOURCES, _digest, public_source_scope
from .identity import _chain_address, identity_candidates

POLICY_VERSION = "official-spot-contract-v1"
DAY_MS = 86_400_000
MIN_REMAINING_MS = 3_600_000


def build_plan(service, proposal_ids=None) -> dict:
    # One lock prevents local catalog/evidence mutation during the captured view.
    with service.store.lock:
        catalog, records, now = service.store.instruments(), service._records(), service.clock()
        with service.store.connect() as db:
            sources = {venue: json.loads(record) for venue, record in
                       db.execute("SELECT venue,record FROM asset_identity_sources ORDER BY venue")}
            mappings = list(db.execute("SELECT instrument_id,asset_id,at FROM mappings ORDER BY instrument_id"))
        plan = _plan(service, catalog, records, sources, now, mappings)
        if proposal_ids is None:
            return plan
        if not isinstance(proposal_ids, list) or not 1 <= len(proposal_ids) <= 100 or len(set(proposal_ids)) != len(proposal_ids):
            raise ValueError("Select 1..100 distinct proposal groups")
        selected = set(proposal_ids)
        known = {row["id"] for key in ("eligible_groups", "blocked_groups", "already_verified_groups") for row in plan[key]}
        if not selected <= known:
            raise ValueError("Selected proposal changed or disappeared; review again")
        scoped = {**plan, "selected_proposal_ids": sorted(selected)}
        for key in ("eligible_groups", "blocked_groups", "already_verified_groups"):
            scoped[key] = [row for row in plan[key] if row["id"] in selected]
        scoped["eligible_evidence_ids"] = sorted({identity for row in scoped["eligible_groups"] for identity in row["evidence_ids"]})
        if len(scoped["eligible_evidence_ids"]) > 10000:
            raise ValueError("Selected groups exceed 10000 evidence records; select fewer groups")
        scoped["counts"] = {key: len(scoped[key]) for key in ("eligible_groups", "blocked_groups", "already_verified_groups")}
        scoped["counts"]["eligible_markets"] = len({identity for row in scoped["eligible_groups"] for identity in row["instrument_ids"]})
        scoped["plan_token"] = _digest({"full_plan_token": plan["plan_token"], "selected_proposal_ids": sorted(selected)})
        return scoped


def _plan(service, catalog, records, sources, now, mappings):
    mapped_ids = {row[0] for row in mappings}
    active = [row for row in records if row["status"] == "active"]
    grouped = defaultdict(list)
    for fact in active:
        grouped[fact.get("canonical_asset_id") or "unresolved:" + fact["instrument_id"]].append(fact)
    proposals = service._preview(catalog, records, now, [fact["id"] for fact in active])["proposals"]
    previews = {row["canonical_asset_id"] or "unresolved:" + row["instrument_ids"][0]: row for row in proposals}
    eligible, blocked, approved = [], [], []
    for target, facts in sorted(grouped.items()):
        # Non-public proofs stay on the manual path, never silently become batch approval.
        row = dict(previews[target])
        issues = set(row["blockers"])
        members = [catalog.get(identity) for identity in row["instrument_ids"]]
        for fact in facts:
            instrument = catalog.get(fact["instrument_id"])
            source = SOURCES.get(fact.get("venue"))
            attempt = sources.get(fact.get("venue"), {})
            if fact.get("evidence_kind") != "public_contract_metadata":
                issues.add("manual_or_nonpublic_evidence_requires_review")
            if not source or fact.get("source_url") != source["endpoint"]:
                issues.add("official_endpoint_unverified")
            if instrument is not None:
                currency, issue = public_source_scope(instrument, fact.get("venue"))
                if issue or currency != fact.get("currency_id") or currency != fact.get("native_currency_id"):
                    issues.add("official_currency_scope_unverified")
                if not instrument.active or instrument.kind != "spot" or instrument.contract_size != 1:
                    issues.add("active_ordinary_spot_required")
                if instrument.native_asset_id != fact.get("native_asset_id"):
                    issues.add("native_identity_changed")
            canonical = _chain_address(target)
            if not canonical or target != ":".join(canonical) or fact.get("contracts") != [target]:
                issues.add("single_exact_chain_contract_required")
            digest = fact.get("source_digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                issues.add("source_digest_invalid")
            original = {key: value for key, value in fact.items() if key not in {"id", "status"}}
            if fact["id"] != _digest(original):
                issues.add("evidence_record_integrity_failed")
            observed, expiry = fact["observed_at"], fact["expires_at"]
            if not 0 <= now - observed <= DAY_MS:
                issues.add("evidence_older_than_24h_or_future")
            if expiry - now < MIN_REMAINING_MS or expiry > observed + 7 * DAY_MS:
                issues.add("evidence_validity_window_insufficient")
            if (attempt.get("state") != "observed" or attempt.get("received_at") != observed
                    or attempt.get("last_attempt_at", now + 1) > observed
                    or not source or attempt.get("endpoint") != source["endpoint"]):
                issues.add("latest_official_source_receipt_unavailable")
        if len({item.venue for item in members if item}) < 2:
            issues.add("two_distinct_official_venues_required")
        existing = [item for item in members if item and item.mapping_verified]
        if existing:
            if len(existing) == len(members) and all(item.asset_id == target for item in existing) and not issues:
                row.update(status="already_verified", blockers=[])
                approved.append(row)
                continue
            issues.add("existing_verified_mapping_requires_manual_review")
        if mapped_ids.intersection(row["instrument_ids"]):
            issues.add("existing_explicit_mapping_requires_manual_review")
        row.update(status="blocked" if issues else "eligible", blockers=sorted(issues))
        (blocked if issues else eligible).append(row)
    ids = sorted({identity for group in eligible for identity in group["evidence_ids"]})
    # Bind the entire relevant state, including conflicting evidence and source failures.
    # Wall time is deliberately absent; time eligibility is re-evaluated on apply.
    token = _digest({"policy": POLICY_VERSION, "catalog": {key: value.model_dump(mode="json")
                     for key, value in sorted(catalog.items())}, "records": records, "sources": sources, "mappings": mappings,
                     "eligible": eligible, "blocked": blocked, "approved": approved})
    return {"policy_version": POLICY_VERSION, "as_of": now, "plan_token": token,
            "eligible_evidence_ids": ids, "eligible_groups": eligible, "blocked_groups": blocked,
            "already_verified_groups": approved, "mappings_changed": False,
            "counts": {"eligible_groups": len(eligible), "eligible_markets": sum(len(g["instrument_ids"]) for g in eligible),
                       "blocked_groups": len(blocked), "already_verified_groups": len(approved)}}


def apply_plan(service, plan_token: str, proposal_ids=None) -> dict:
    plan = build_plan(service, proposal_ids)
    if plan["plan_token"] != plan_token:
        raise ValueError("Batch plan changed; review a new plan")
    if not plan["eligible_evidence_ids"]:
        raise ValueError("No eligible public spot groups; no mappings changed")
    preview = service.preview(plan["eligible_evidence_ids"])

    def guard():
        current = build_plan(service, proposal_ids)
        if current["plan_token"] != plan_token or current["eligible_evidence_ids"] != plan["eligible_evidence_ids"]:
            raise ValueError("Batch policy or source state changed; no mappings changed")
        return {"policy_version": POLICY_VERSION, "plan_token": plan_token,
                "selected_proposal_ids": proposal_ids,
                **({"counts": current["counts"], "skipped_groups": [
                    {key: row[key] for key in ("id", "canonical_asset_id", "status", "blockers")}
                    for row in current["blocked_groups"] + current["already_verified_groups"]]}
                   if proposal_ids is not None else {})}

    result = service.apply(plan["eligible_evidence_ids"], preview["preview_token"], _guard=guard)
    return {**result, "policy_version": POLICY_VERSION, "plan_token": plan_token, "mappings_changed": True}


def review_groups(service, *, offset=0, limit=25, source="", chain="", contract="", state="", blocker=""):
    """Filter whole policy groups, never hide conflicting evidence inside them."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid review page")
    with service.store.lock:
        plan = build_plan(service)
        catalog, records = service.store.instruments(), service._records()
        facts = {row["id"]: row for row in records}
        rows = []
        for key in ("eligible_groups", "blocked_groups", "already_verified_groups"):
            for row in plan[key]:
                canonical = _chain_address(row["canonical_asset_id"] or "")
                evidence = [facts[identity] for identity in row["evidence_ids"] if identity in facts]
                rows.append({**row, "chain": canonical[0] if canonical else None,
                    "contract": canonical[1] if canonical else None,
                    "sources": sorted({fact.get("venue", "") for fact in evidence}),
                    "evidence": [{key: fact.get(key) for key in ("id", "instrument_id", "evidence_kind", "source_url",
                        "source_field", "source_digest", "observed_at", "expires_at", "contracts", "status")} for fact in evidence]})
        rows.sort(key=lambda row: (row["canonical_asset_id"] or "", row["id"]))
        options = {"sources": sorted({value for row in rows for value in row["sources"] if value}),
                   "chains": sorted({row["chain"] for row in rows if row["chain"]}),
                   "blockers": dict(sorted(Counter(value for row in rows for value in row["blockers"]).items()))}
        filtered = [row for row in rows if (not source or source in row["sources"])
                    and (not chain or chain == row["chain"])
                    and (not contract or contract.casefold() in (row["canonical_asset_id"] or "").casefold())
                    and (not state or state == row["status"]) and (not blocker or blocker in row["blockers"])]
        actual = [row for row in identity_candidates(
            (item for item in catalog.values() if item.active), as_of=plan["as_of"])
            if row["kind"] == "identity" and row["comparison_ready"] and len(row["venues"]) > 1]
        return {"as_of": plan["as_of"], "policy_version": POLICY_VERSION, "read_only": True,
                "actual_comparable_groups": len(actual), "actual_comparable_markets": len({identity for row in actual for identity in row["instrument_ids"]}),
                "global_counts": plan["counts"], "filtered_groups": len(filtered),
                "filtered_counts": {status: sum(row["status"] == status for row in filtered) for status in ('eligible', 'blocked', 'already_verified')},
                "offset": offset, "limit": limit, "next_offset": offset+limit if offset+limit < len(filtered) else None,
                "groups": filtered[offset:offset+limit], "options": options}
