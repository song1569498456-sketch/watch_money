#!/usr/bin/env python3
"""Rehearse public identity approval in a disposable DB; never open source writable."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from watch_money.asset_verification import SOURCES, AssetVerificationService
from watch_money.identity import identity_candidates
from watch_money.models import now_ms
from watch_money.storage import Storage

TABLES = {
    'instruments': ('id', 'value'),
    'mappings': ('instrument_id', 'asset_id', 'at'),
    'asset_mapping_verifications': ('instrument_id', 'metadata'),
    'asset_identity_evidence': ('id', 'record', 'status'),
    'asset_identity_sources': ('venue', 'record'),
}
EVIDENCE_FIELDS = {'id', 'currency_id', 'canonical_asset_id', 'contracts', 'blockers', 'source_field', 'source_digest', 'instrument_id', 'native_asset_id', 'native_currency_id', 'venue', 'observed_at', 'expires_at', 'evidence_kind', 'relationship', 'unit_multiplier', 'catalog_fingerprint'}
MAPPING_FIELDS = {'status', 'canonical_asset_id', 'catalog_fingerprint', 'evidence_ids', 'verified_at', 'expires_at'}
SOURCE_FIELDS = {'venue', 'state', 'last_attempt_at', 'received_at', 'response_rows', 'matched_markets', 'eligible_markets', 'blocked_markets', 'error_type', 'scope_count_scope', 'scope_eligible_markets', 'scope_rejected_markets', 'scope_reasons', 'unmatched_currency_markets'}
INSTRUMENT_FIELDS = {
    'id', 'venue', 'native_symbol', 'asset_id', 'native_asset_id', 'native_currency_id',
    'quote_asset_id', 'native_quote_asset_id', 'quote_identity_at', 'quote_identity_expires_at',
    'base', 'quote', 'settle', 'kind', 'mechanism', 'contract_size', 'linear', 'chain', 'active',
    'price_precision', 'amount_precision', 'min_amount', 'min_notional', 'mapping_verified',
    'mapping_evidence_ids', 'mapping_verified_at', 'mapping_expires_at', 'mapping_evidence_status',
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _record(table, value):
    record = json.loads(value)
    if table == 'instruments':
        return {key: val for key, val in record.items() if key in INSTRUMENT_FIELDS}
    if table == 'asset_mapping_verifications':
        return {key: val for key, val in record.items() if key in MAPPING_FIELDS}
    fields = EVIDENCE_FIELDS if table == 'asset_identity_evidence' else SOURCE_FIELDS
    result = {key: val for key, val in record.items() if key in fields}
    official = SOURCES.get(record.get('venue'), {}).get('endpoint')
    if table == 'asset_identity_sources' and official and record.get('endpoint') == official:
        result['endpoint'] = official
    if table == 'asset_identity_evidence':
        # Keep authentic machine-generated content hashes intact. Free-form manual
        # notes and URLs never leave the source connection, including in reports.
        result['source_url'] = official if official and record.get('source_url') == official else 'https://redacted.invalid/'
        result['note'] = ('Public currency-id/chain/contract metadata'
                          if record.get('note') == 'Public currency-id/chain/contract metadata' else '')
        if result['source_url'] == 'https://redacted.invalid/':
            result['source_field'] = 'redacted'
    return result


def capture(source: Path, *, max_bytes=64 * 1024 * 1024, max_rows=100000):
    """Read only five public metadata tables in one consistent bounded transaction."""
    source = Path(source).resolve(strict=True)
    result, count, size = {}, 0, 0
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for table, columns in TABLES.items():
            if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
                result[table] = []
                continue
            lengths = '+'.join(f'length(CAST({column} AS BLOB))' for column in columns)
            rows, byte_count = db.execute(f'SELECT count(*),coalesce(sum({lengths}),0) FROM {table}').fetchone()
            count += rows
            size += byte_count
            if count > max_rows or size > max_bytes:
                raise ValueError('Public identity metadata exceeds configured import limit')
            values = []
            for row in db.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY {columns[0]}'):
                row = list(row)
                json_index = 1 if table != 'mappings' else None
                if json_index is not None:
                    row[json_index] = _json(_record(table, row[json_index]))
                values.append(row)
            result[table] = values
        db.rollback()
    return result


def load_capture(snapshot, directory):
    store = Storage(directory)
    collector = SimpleNamespace(instruments={})
    AssetVerificationService(store, collector)  # create only disposable schemas
    with store.connect() as db:
        for table, columns in TABLES.items():
            db.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', snapshot[table])
    service = AssetVerificationService(store, collector)
    collector.instruments = store.instruments()
    return service


def coverage(service):
    catalog = service.store.instruments()
    groups = [row for row in identity_candidates((item for item in catalog.values() if item.active), as_of=service.clock())
              if row['kind'] == 'identity' and row['comparison_ready'] and len(row['venues']) > 1]
    return {'confirmed_cross_platform_groups': len(groups),
            'confirmed_cross_platform_markets': sum(len(row['instrument_ids']) for row in groups),
            'catalog_markets': len(catalog)}


async def rehearse(snapshot, *, refresh_public=False, client=None):
    from watch_money.identity_batch import apply_plan, build_plan
    with tempfile.TemporaryDirectory(prefix='identity-rehearsal-') as directory:
        service = load_capture(snapshot, directory)
        if client is not None:
            service.client, service._owns_client = client, False
        try:
            before = coverage(service)
            if refresh_public and snapshot['instruments']:
                await service.public_refresh(['gate', 'bitget'])
            sources = {venue: json.loads(record) for venue, record in capture(service.store.db_path)['asset_identity_sources']}
            plan = build_plan(service)
            ids = plan['eligible_evidence_ids']
            applied = False
            if ids:
                apply_plan(service, plan['plan_token'])
                applied = True
            after = coverage(service)
            if applied:
                service.revoke(ids, 'Disposable public identity rehearsal reversal')
            after_revoke = coverage(service)
            if before != after_revoke:
                raise RuntimeError('Disposable revoke did not restore baseline coverage; inspect policy before continuing')
            return {'provenance': 'source_database_metadata; fresh public requests only if explicitly enabled',
                    'actual_catalog_available': bool(snapshot['instruments']),
                    'fresh_public_refresh_requested': refresh_public,
                    'before': before, 'after_temporary_apply': after, 'after_temporary_revoke': after_revoke,
                    'temporary_applied_evidence_count': len(ids),
                    'baseline_coverage_restored': before == after_revoke,
                    'plan_token': plan['plan_token'], 'policy_version': plan['policy_version'],
                    'plan_counts': plan['counts'],
                    'blocked_reasons': dict(Counter(reason for group in plan['blocked_groups'] for reason in group['blockers'])),
                    'fresh_public_refresh_skipped_empty_catalog': refresh_public and not snapshot['instruments'],
                    'platform_sources': {venue: sources.get(venue, {'state': 'not_observed'}) for venue in SOURCES},
                    'limits': ['No live database mappings were changed.',
                               'Candidate groups are not approved coverage.',
                               'External 199 candidate groups / 440 markets are not imported or counted without their source data.',
                               'Identity coverage alone does not establish price freshness, executable profit or perpetual underlying identity.',
                               'Only previously unverified eligible groups may be batch approved; existing approvals require separate review.'],
                    'guard_validation': 'See tests/test_identity_rehearsal.py and tests/test_identity_batch.py; fixture checks are synthetic, not live-source observations.'}
        finally:
            await service.close()


def atomic_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.identity-report-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--refresh-public', action='store_true', help='Refresh Gate/Bitget official public registries in temporary DB only')
    parser.add_argument('--max-bytes', type=int, default=64 * 1024 * 1024)
    parser.add_argument('--max-rows', type=int, default=100000)
    args = parser.parse_args()
    source = args.source_db.resolve(strict=True)
    if (args.report.exists() and os.path.samefile(args.report, source)) or args.report.resolve() == source or args.report.resolve() in [Path(str(source) + suffix) for suffix in ('-wal', '-shm')]:
        parser.error('Report must not overwrite source database or SQLite sidecars')
    snapshot = capture(source, max_bytes=args.max_bytes, max_rows=args.max_rows)
    report = asyncio.run(rehearse(snapshot, refresh_public=args.refresh_public))
    after = capture(source, max_bytes=args.max_bytes, max_rows=args.max_rows)
    def digest(value):
        return hashlib.sha256(_json(value).encode()).hexdigest()
    report['source_protection'] = {'open_mode': 'SQLite mode=ro + query_only; SELECT whitelisted identity tables only',
                                   'public_metadata_sha256_before': digest(snapshot),
                                   'public_metadata_sha256_after': digest(after),
                                   'public_metadata_unchanged': snapshot == after,
                                   'scope': 'Sanitized identity metadata only; private tables were not inspected or hashed. Concurrent external writes can change this result.'}
    report['recorded_at'] = now_ms()
    atomic_report(args.report, report)
    print('Isolated identity rehearsal report written; source opened read-only.')


if __name__ == '__main__':
    main()
