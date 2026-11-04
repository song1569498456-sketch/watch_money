"""Durable metadata in SQLite; immutable snapshots in partitioned Parquet."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .models import Instrument, MarketSnapshot, Settings, now_ms


class Storage:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.history_dir = self.directory / 'history'
        self.history_dir.mkdir(exist_ok=True)
        self.db_path = self.directory / 'monitor.sqlite3'
        self.lock = RLock()
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS instruments (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS latest (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS mappings (
                    instrument_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, at INTEGER NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    def connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value):
        with self.connect() as db:
            db.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (key, json.dumps(value, allow_nan=False)))

    def settings(self) -> Settings:
        return Settings.model_validate(self.get('settings', {}))

    def save_instruments(self, instruments: list[Instrument]):
        instruments = [i if i.native_asset_id else i.model_copy(update={'native_asset_id': i.asset_id})
                       for i in instruments]
        with self.connect() as db:
            db.executemany('INSERT INTO instruments VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',
                           [(i.id, i.model_dump_json()) for i in instruments])

    def instruments(self) -> dict[str, Instrument]:
        with self.connect() as db:
            rows = db.execute('SELECT value FROM instruments').fetchall()
            mappings = dict(db.execute('SELECT instrument_id,asset_id FROM mappings'))
        result = {}
        for row in rows:
            instrument = Instrument.model_validate_json(row[0])
            if instrument.native_asset_id is None:
                instrument.native_asset_id = instrument.asset_id
            if instrument.id in mappings:
                instrument.asset_id = mappings[instrument.id]
                instrument.mapping_verified = True
            result[instrument.id] = instrument
        return result

    def map_asset(self, instrument_id: str, asset_id: str):
        self.map_assets([instrument_id], asset_id)

    def map_assets(self, instrument_ids: list[str], asset_id: str):
        # A single transaction prevents partially applied cross-venue confirmations.
        with self.lock, self.connect() as db:
            for instrument_id in dict.fromkeys(instrument_ids):
                previous = db.execute('SELECT asset_id FROM mappings WHERE instrument_id=?', (instrument_id,)).fetchone()
                db.execute('INSERT INTO mappings VALUES (?,?,?) ON CONFLICT(instrument_id) DO UPDATE SET asset_id=excluded.asset_id,at=excluded.at',
                           (instrument_id, asset_id, now_ms()))
                db.execute('INSERT INTO audit(at,action,payload) VALUES (?,?,?)',
                           (now_ms(), 'asset_mapping', json.dumps({'instrument_id': instrument_id, 'asset_id': asset_id,
                                                                  'previous': previous[0] if previous else None})))

    def audit(self):
        with self.connect() as db:
            return [{'at': r[0], 'action': r[1], 'payload': json.loads(r[2])}
                    for r in db.execute('SELECT at,action,payload FROM audit ORDER BY id DESC LIMIT 500')]

    def latest(self) -> dict[str, MarketSnapshot]:
        with self.connect() as db:
            return {row[0]: MarketSnapshot.model_validate_json(row[1])
                    for row in db.execute('SELECT id,value FROM latest')}

    def save_snapshots(self, snapshots: list[MarketSnapshot], archive: bool = True):
        if not snapshots:
            return
        with self.lock:
            with self.connect() as db:
                db.executemany('''INSERT INTO latest VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value
                               WHERE json_extract(excluded.value, '$.received_at') >= json_extract(latest.value, '$.received_at')''',
                               [(s.instrument_id, s.model_dump_json()) for s in snapshots])
            if not archive:
                return
            groups = {}
            for s in snapshots:
                date = datetime.fromtimestamp(s.received_at / 1000, UTC).strftime('%Y-%m-%d')
                venue = re.sub(r'[^A-Za-z0-9_-]', '_', s.venue)
                groups.setdefault((date, venue), []).append({
                    'instrument_id': s.instrument_id, 'received_at': s.received_at,
                    'source_at': s.source_at, 'payload': s.model_dump_json(),
                })
            for (date, venue), rows in groups.items():
                folder = self.history_dir / f'date={date}' / f'venue={venue}'
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f'{now_ms()}-{uuid.uuid4().hex}.parquet'
                temporary = target.with_suffix('.tmp')
                pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([
                    ('instrument_id', pa.string()), ('received_at', pa.int64()),
                    ('source_at', pa.int64()), ('payload', pa.string()),
                ])), temporary, compression='zstd')
                temporary.replace(target)

    def history(self, instrument_id: str, limit: int = 500, before: int | None = None):
        with self.lock:
            files = [str(p) for p in self.history_dir.glob('date=*/venue=*/*.parquet')]
            if not files:
                return []
            with duckdb.connect(':memory:') as db:
                rows = db.execute('''SELECT payload FROM read_parquet(?, hive_partitioning=false)
                    WHERE instrument_id=? AND received_at<=?
                    ORDER BY received_at DESC LIMIT ?''',
                    [files, instrument_id, before if before is not None else now_ms(), min(max(limit, 1), 10000)]).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def prune(self, retention_days: int):
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).date().isoformat()
        removed = 0
        with self.lock:
            for folder in self.history_dir.glob('date=*'):
                if folder.name.removeprefix('date=') < cutoff:
                    for path in folder.glob('venue=*/*.parquet'):
                        path.unlink()
                        removed += 1
        return removed

    def usage(self):
        files = list(self.history_dir.glob('date=*/venue=*/*.parquet'))
        return {'parquet_files': len(files), 'history_bytes': sum(p.stat().st_size for p in files)}
