"""Observe a running instance and preserve honest timed availability evidence."""
import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8000')
parser.add_argument('--hours', type=float, default=24)
parser.add_argument('--interval', type=float, default=30)
parser.add_argument('--output', default='data/soak.jsonl')
args = parser.parse_args()
if args.hours <= 0 or args.interval <= 0:
    parser.error('hours and interval must be positive')
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
started = time.monotonic()
errors = 0
samples = 0
with httpx.Client(timeout=20, trust_env=False) as client, path.open('a', encoding='utf-8') as output:
    while time.monotonic() - started < args.hours * 3600:
        row = {'at': datetime.now(UTC).isoformat(), 'elapsed_seconds': time.monotonic() - started}
        try:
            health = client.get(args.url + '/api/health')
            health.raise_for_status()
            status = client.get(args.url + '/api/status')
            status.raise_for_status()
            markets = client.get(args.url + '/api/markets')
            markets.raise_for_status()
            snapshots = markets.json()['snapshots']
            row.update(health=health.json(), venues=status.json()['venues'],
                       snapshots=len(snapshots), stale=sum(bool(s.get('stale')) for s in snapshots),
                       demo=sum(bool(s.get('is_demo')) for s in snapshots),
                       valid_live=sum(s.get('valid', False) and not s.get('stale') and not s.get('is_demo') for s in snapshots))
        except Exception as exc:
            row['error'] = type(exc).__name__
            errors += 1
        output.write(json.dumps(row) + '\n')
        output.flush()
        samples += 1
        time.sleep(min(args.interval, max(0, args.hours * 3600 - (time.monotonic() - started))))
    summary = {'summary': True, 'elapsed_seconds': time.monotonic() - started, 'samples': samples,
               'http_errors': errors, 'requested_hours': args.hours,
               'note': 'HTTP availability alone does not establish live market coverage or 24h acceptance.'}
    output.write(json.dumps(summary) + '\n')
    print(json.dumps(summary, indent=2))
raise SystemExit(1 if errors else 0)
