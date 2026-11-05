"""Bounded, public-only direct vs ANY timing evidence; never adjusts source time."""
import argparse
from collections import Counter
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


class RequestPacer:
    """Shared request-start pacing, in addition to the two in-flight slots."""
    def __init__(self, gap=.25):
        self.gap = gap
        self.lock = asyncio.Lock()
        self.next_start = 0.

    async def wait(self):
        async with self.lock:
            delay = max(0., self.next_start-time.monotonic())
            if delay:
                await asyncio.sleep(delay)
            self.next_start = time.monotonic()+self.gap


async def request(client, semaphore, kind, instrument, symbols, wall_ms, *, pacer=None, timeout_seconds=20):
    path = '/api/v5/public/time' if kind == 'clock' else '/api/v5/public/funding-rate'
    params = {} if kind == 'clock' else {'instId': instrument}
    queued = time.perf_counter()
    async with semaphore:
        if pacer is not None:
            await pacer.wait()
        start = wall_ms()
        monotonic_start = time.perf_counter()
        result = {'kind': kind, 'instrument': instrument, 'started_at': start,
                  'queue_wait_ms': round((monotonic_start-queued)*1000, 3),
                  'timeout_seconds': timeout_seconds, 'response_bytes': 0, 'retryable_next_cycle': False}
        stage = 'request'
        try:
            async with asyncio.timeout(timeout_seconds):
                async with client.stream('GET', BASE+path, params=params) as response:
                    result['http_status'] = response.status_code
                    stage = 'body'
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        result['response_bytes'] = size
                        if size > LIMIT:
                            raise ValueError('response size bound')
                        chunks.append(chunk)
                    received = wall_ms()  # body received, before JSON decoding
                    result.update(received_at=received,
                                  request_ms=round((time.perf_counter()-monotonic_start)*1000, 3))
                    result['http_date'] = response.headers.get('date', '')[:128]
                    age = response.headers.get('age', '')
                    result['http_cache_age_seconds'] = int(age) if age.isdigit() and len(age) < 12 else None
                    if response.status_code != 200:
                        result['error'] = 'http_status'
                        result['outcome'] = 'http_error'
                        result['retryable_next_cycle'] = response.status_code == 408 or response.status_code >= 500
                        return result
                stage = 'decode'
                parse_start = time.perf_counter()
                payload = json.loads(b''.join(chunks))
                result['decode_ms'] = round((time.perf_counter()-parse_start)*1000, 3)
                result['decoded_at'] = wall_ms()
                code = str(payload.get('code', ''))
                result['api_code'] = code if re.fullmatch(r'\d{1,10}', code) else 'unknown'
                if code != '0' or not isinstance(payload.get('data'), list):
                    result.update(error='api_response', outcome='api_error')
                    return result
                rows = payload['data']
                if len(rows) > 2000:
                    result.update(error='row_count_bound', outcome='invalid_response')
                    return result
                if kind == 'clock':
                    server = timestamp(rows[0].get('ts')) if rows and isinstance(rows[0], dict) else None
                    result['server_ts'] = server
                    result['clock_offset_bounds_ms'] = [server-received, server-start] if server else None
                else:
                    result.update(summarize(rows, received, symbols))
                result['outcome'] = 'success'
        except (httpx.TimeoutException, TimeoutError) as exc:
            result.update(error=type(exc).__name__, outcome='timeout', timeout_stage=stage,
                          retryable_next_cycle=True)
        except httpx.TransportError as exc:
            result.update(error=type(exc).__name__, outcome='transport_error', retryable_next_cycle=True)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            result.update(error=type(exc).__name__, outcome='invalid_response')
        finally:
            # Include failures and partial reads, without inventing received_at.
            result['finished_at'] = wall_ms()
            result['elapsed_ms'] = round((time.perf_counter()-monotonic_start)*1000, 3)
            if result.get('error'):
                result['error_stage'] = stage
        return result


def report_summary(cycles):
    requests = [row for cycle in cycles for row in cycle['requests']]
    streams = {}
    for row in requests:
        key = row['kind']+(':'+row['instrument'] if row['instrument'] else '')
        item = streams.setdefault(key, {'attempts': 0, 'successes': 0, 'timeouts': 0,
                                       'errors': {}, 'max_elapsed_ms': 0, 'responses_with_overage': 0})
        item['attempts'] += 1
        item['successes'] += row.get('outcome') == 'success'
        item['timeouts'] += row.get('outcome') == 'timeout'
        if row.get('error'):
            item['errors'][row['error']] = item['errors'].get(row['error'], 0)+1
        item['max_elapsed_ms'] = max(item['max_elapsed_ms'], row['elapsed_ms'])
        item['responses_with_overage'] += row.get('source_older_than_60s', 0) > 0
    pairs = [pair for cycle in cycles for pair in cycle['pairs']]
    return {'completed_cycles': len(cycles), 'request_attempts': len(requests),
            'outcomes': dict(Counter(row['outcome'] for row in requests)), 'streams': streams,
            'pairs_with_same_ts': sum(pair['same_ts'] is True for pair in pairs),
            'pairs_with_different_ts': sum(pair['same_ts'] is False for pair in pairs),
            'pairs_unavailable': sum(pair['same_ts'] is None for pair in pairs)}


async def probe(client, *, symbols, samples, interval, wall_ms=lambda: int(time.time()*1000),
                pause=asyncio.sleep, timeout_seconds=20, on_cycle=None):
    if (not 1 <= len(symbols) <= 5 or len(set(symbols)) != len(symbols)
            or any(not re.fullmatch(r'[A-Z0-9]{1,30}-[A-Z0-9]{1,15}-SWAP', value) for value in symbols)
            or not 1 <= samples <= 60 or not 5 <= interval <= 60 or not 1 <= timeout_seconds <= 60):
        raise ValueError('bounded symbols/samples/interval required')
    output = {'scope': 'public OKX direct/ANY timing comparison; selected IDs are diagnostic samples, not coverage whitelist',
              'source_time_policy': 'Raw ts retained; 60s comparison gate unchanged. Clock offsets are evidence only.',
              'limits': {'max_concurrency': 2, 'min_request_start_gap_seconds': .25, 'max_response_bytes': LIMIT,
                         'samples': samples, 'interval': interval, 'request_timeout_seconds': timeout_seconds,
                         'max_backoff_seconds': 60, 'max_all_failed_cycles': 3, 'same_cycle_retries': 0},
              'symbols': symbols, 'cycles': [], 'stopped_on_error': False, 'stop_reason': None, 'finished': False}
    semaphore = asyncio.Semaphore(2)
    previous = {}
    pacer = RequestPacer()
    failed_streak = all_failed_streak = 0
    for cycle in range(samples):
        requests = await asyncio.gather(request(client, semaphore, 'clock', None, symbols, wall_ms, pacer=pacer, timeout_seconds=timeout_seconds),
            request(client, semaphore, 'batch', 'ANY', symbols, wall_ms, pacer=pacer, timeout_seconds=timeout_seconds),
            *(request(client, semaphore, 'direct', symbol, symbols, wall_ms, pacer=pacer, timeout_seconds=timeout_seconds) for symbol in symbols))
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
        failures = [row for row in requests if row.get('error')]
        failed_streak = failed_streak+1 if failures else 0
        all_failed_streak = all_failed_streak+1 if len(failures) == len(requests) else 0
        terminal = any(not row['retryable_next_cycle'] for row in failures)
        if terminal or all_failed_streak >= 3:
            output['stopped_on_error'] = True
            output['stop_reason'] = 'non_retryable_response' if terminal else 'three_all_failed_cycles'
        delay = min(60, interval * 2 ** min(max(0, failed_streak-1), 3))
        output['cycles'][-1]['next_cycle_delay_seconds'] = (delay if cycle+1 < samples and not output['stopped_on_error'] else None)
        output['summary'] = report_summary(output['cycles'])
        if on_cycle is not None:
            on_cycle(output)
        if output['stopped_on_error']:
            break
        if cycle+1 < samples:
            await pause(delay)
    output['finished'] = True
    output['completed_requested_cycles'] = len(output['cycles']) == samples
    if on_cycle is not None:
        on_cycle(output)
    return output


async def main(args):
    ca = os.environ.get('SSL_CERT_FILE') or os.environ.get('REQUESTS_CA_BUNDLE')
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    def checkpoint(result):
        result['execution'] = 'live public network; inspect failures and scope before drawing conclusions'
        temporary = target.with_name(target.name+'.tmp')
        temporary.write_text(json.dumps(result, indent=2)+'\n')
        temporary.replace(target)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                verify=ssl.create_default_context(cafile=ca)) as client:
        result = await probe(client, symbols=args.symbols.split(','), samples=args.samples, interval=args.interval,
                             timeout_seconds=args.request_timeout, on_cycle=checkpoint)
    print(json.dumps({'output': args.output, 'cycles': len(result['cycles']),
                      'stopped_on_error': result['stopped_on_error'], 'stop_reason': result['stop_reason'],
                      'summary': result['summary']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly run public network comparison')
    parser.add_argument('--symbols', default='BTC-USDT-SWAP')
    parser.add_argument('--samples', type=int, default=18)
    parser.add_argument('--interval', type=float, default=10)
    parser.add_argument('--output', required=True)
    parser.add_argument('--request-timeout', type=float, default=20, help='Total request deadline in seconds (1..60)')
    options = parser.parse_args()
    if not options.live:
        parser.error('--live is required; do not run in a restricted jurisdiction/environment')
    asyncio.run(main(options))
