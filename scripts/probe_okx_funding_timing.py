"""Bounded, public-only direct vs ANY timing evidence; never adjusts source time."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import re
import ssl
import os
import time

import httpx

BASE = 'https://www.okx.com'
LIMIT = 4 * 1024 * 1024
FIELDS = ('instId', 'ts', 'fundingRate', 'fundingTime', 'nextFundingTime')


def timestamp(value):
    try:
        number = int(value)
        return number if 100_000_000_000 <= number < 10_000_000_000_000 else None
    except (ValueError, TypeError):
        return None


def summarize(rows, received, symbols):
    ages = sorted((received-stamp)/1000 for row in rows
                  if isinstance(row, dict) and (stamp := timestamp(row.get('ts'))) is not None)
    selected = {}
    duplicates = []
    for row in rows:
        if not isinstance(row, dict) or row.get('instId') not in symbols:
            continue
        identity = row['instId']
        if identity in selected:
            duplicates.append(identity)
        selected[identity] = {key: str(row[key])[:128] for key in FIELDS if key in row}
    return {'markets': len(rows), 'valid_timestamp_count': len(ages),
            'source_age_p90_seconds': ages[math.ceil(len(ages)*.9)-1] if ages else None,
            'source_age_max_seconds': max(ages, default=None),
            'source_older_than_60s': sum(age > 60 for age in ages),
            'future_source_count': sum(age < 0 for age in ages),
            'selected': selected, 'duplicate_selected_ids': sorted(set(duplicates))}


async def request(client, semaphore, kind, instrument, symbols, wall_ms):
    path = '/api/v5/public/time' if kind == 'clock' else '/api/v5/public/funding-rate'
    params = {} if kind == 'clock' else {'instId': instrument}
    async with semaphore:
        start = wall_ms()
        monotonic_start = time.perf_counter()
        result = {'kind': kind, 'instrument': instrument, 'started_at': start}
        try:
            async with client.stream('GET', BASE+path, params=params) as response:
                result['http_status'] = response.status_code
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > LIMIT:
                        raise ValueError('response size bound')
                    chunks.append(chunk)
                received = wall_ms()  # body received, before JSON decoding
                result.update(received_at=received, response_bytes=size,
                              request_ms=round((time.perf_counter()-monotonic_start)*1000, 3))
                result['http_date'] = response.headers.get('date', '')[:128]
                age = response.headers.get('age', '')
                result['http_cache_age_seconds'] = int(age) if age.isdigit() and len(age) < 12 else None
                if response.status_code != 200:
                    result['error'] = 'http_status'
                    return result
            parse_start = time.perf_counter()
            payload = json.loads(b''.join(chunks))
            result['decode_ms'] = round((time.perf_counter()-parse_start)*1000, 3)
            result['decoded_at'] = wall_ms()
            code = str(payload.get('code', ''))
            result['api_code'] = code if re.fullmatch(r'\d{1,10}', code) else 'unknown'
            if code != '0' or not isinstance(payload.get('data'), list):
                result['error'] = 'api_response'
                return result
            rows = payload['data']
            if len(rows) > 2000:
                result['error'] = 'row_count_bound'
                return result
            if kind == 'clock':
                server = timestamp(rows[0].get('ts')) if rows and isinstance(rows[0], dict) else None
                result['server_ts'] = server
                result['clock_offset_bounds_ms'] = [server-received, server-start] if server else None
            else:
                result.update(summarize(rows, received, symbols))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            result['error'] = type(exc).__name__  # No response body, exception text, or headers dump.
        return result


async def probe(client, *, symbols, samples, interval, wall_ms=lambda: int(time.time()*1000), pause=asyncio.sleep):
    if (not 1 <= len(symbols) <= 5 or len(set(symbols)) != len(symbols)
            or any(not re.fullmatch(r'[A-Z0-9]{1,30}-[A-Z0-9]{1,15}-SWAP', value) for value in symbols)
            or not 1 <= samples <= 60 or not 5 <= interval <= 60):
        raise ValueError('bounded symbols/samples/interval required')
    output = {'scope': 'public OKX direct/ANY timing comparison; selected IDs are diagnostic samples, not coverage whitelist',
              'source_time_policy': 'Raw ts retained; 60s comparison gate unchanged. Clock offsets are evidence only.',
              'limits': {'max_concurrency': 2, 'max_response_bytes': LIMIT, 'samples': samples, 'interval': interval},
              'symbols': symbols, 'cycles': [], 'stopped_on_error': False}
    semaphore = asyncio.Semaphore(2)
    previous = {}
    for cycle in range(samples):
        requests = await asyncio.gather(request(client, semaphore, 'clock', None, symbols, wall_ms),
            request(client, semaphore, 'batch', 'ANY', symbols, wall_ms),
            *(request(client, semaphore, 'direct', symbol, symbols, wall_ms) for symbol in symbols))
        for row in requests:
            for symbol, selected in row.get('selected', {}).items():
                key = row['kind'], symbol
                stamp = timestamp(selected.get('ts'))
                selected['previous_ts'] = previous.get(key)
                selected['ts_advance_ms'] = stamp-previous[key] if stamp is not None and key in previous else None
                if stamp is not None:
                    previous[key] = stamp
        batch = requests[1].get('selected', {})
        pairs = []
        for direct in requests[2:]:
            symbol = direct['instrument']
            duplicate = (symbol in requests[1].get('duplicate_selected_ids', [])
                         or symbol in direct.get('duplicate_selected_ids', []))
            a = timestamp(batch.get(symbol, {}).get('ts'))
            b = timestamp(direct.get('selected', {}).get(symbol, {}).get('ts'))
            pairs.append({'instrument': symbol, 'batch_ts': a, 'direct_ts': b,
                          'duplicate_identity': duplicate,
                          'same_ts': a == b if not duplicate and a is not None and b is not None else None,
                          'direct_minus_batch_ms': b-a if not duplicate and a is not None and b is not None else None})
        output['cycles'].append({'index': cycle, 'requests': requests, 'pairs': pairs})
        if any(row.get('error') for row in requests):
            output['stopped_on_error'] = True
            break  # No repeated restricted/auth/rate-limit requests or automatic retries.
        if cycle+1 < samples:
            await pause(interval)
    return output


async def main(args):
    ca = os.environ.get('SSL_CERT_FILE') or os.environ.get('REQUESTS_CA_BUNDLE')
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                verify=ssl.create_default_context(cafile=ca)) as client:
        result = await probe(client, symbols=args.symbols.split(','), samples=args.samples, interval=args.interval)
    result['execution'] = 'live public network; inspect errors and sampling scope before drawing conclusions'
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'output': args.output, 'cycles': len(result['cycles']), 'stopped_on_error': result['stopped_on_error']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly run public network comparison')
    parser.add_argument('--symbols', default='BTC-USDT-SWAP')
    parser.add_argument('--samples', type=int, default=18)
    parser.add_argument('--interval', type=float, default=10)
    parser.add_argument('--output', required=True)
    options = parser.parse_args()
    if not options.live:
        parser.error('--live is required; do not run in a restricted jurisdiction/environment')
    asyncio.run(main(options))
