"""Local real-CCXT parse CPU/loop comparison; no HTTP/exchange validation."""
import asyncio
import json
from pathlib import Path
from time import perf_counter

import ccxt.async_support as ccxt

from watch_money.adapters.bulk_tickers import BulkTickerParser


async def measure(operation):
    done = False
    delays = []
    async def heartbeat():
        while not done:
            start = perf_counter()
            await asyncio.sleep(.005)
            delays.append(max(0., perf_counter() - start - .005) * 1000)
    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    start = perf_counter()
    result = await operation()
    elapsed = perf_counter() - start
    await asyncio.sleep(.01)
    done = True
    await task
    return result, {'wall_ms': round(elapsed * 1000, 3), 'loop_samples': len(delays),
                    'max_loop_delay_ms': round(max(delays), 3)}


async def main():
    output = {'scope': '7000 synthetic spot markets per exchange, real installed CCXT parser, mocked public HTTP. '
                       'Same response/metadata per baseline and worker run; not live acceptance.',
              'venues': {}}
    for venue in ('okx', 'bitget'):
        client = getattr(ccxt, venue)()
        rows, markets = [], []
        for index in range(7000):
            base = f'COIN{index}'
            symbol = f'{base}/USDT'
            market_id = f'{base}-USDT' if venue == 'okx' else f'{base}USDT'
            markets.append({'id': market_id, 'symbol': symbol, 'base': base, 'quote': 'USDT',
                'baseId': base, 'quoteId': 'USDT', 'spot': True, 'swap': False, 'future': False,
                'option': False, 'contract': False, 'linear': False, 'inverse': False, 'active': True,
                'type': 'spot', 'precision': {'price': .01, 'amount': .001}})
            rows.append({'instId': market_id, 'instType': 'SPOT', 'ts': '1800000000000', 'bidPx': '99.01',
                'askPx': '100.03', 'last': '99.50', 'open24h': '90', 'volCcy24h': '198000', 'vol24h': '2000'}
                if venue == 'okx' else {'symbol': market_id, 'ts': '1800000000000', 'bidPr': '99.01',
                'askPr': '100.03', 'lastPr': '99.50', 'open': '90', 'quoteVolume': '198000',
                'baseVolume': '2000', 'change24h': '.1055'})
        client.set_markets(markets)
        async def response(_):
            return {'data': rows}
        if venue == 'okx':
            client.publicGetMarketTickers = response
        else:
            client.publicSpotGetV2SpotMarketTickers = response
        parser = BulkTickerParser()
        try:
            original, baseline = await measure(lambda: client.fetch_tickers(None, {'type': 'spot'}))
            actual, worker = await measure(lambda: parser.fetch(client, None, {'type': 'spot'}))
            assert original == actual and len(actual) == 7000
            output['venues'][venue] = {'baseline_inline': baseline, 'bounded_worker': worker,
                                       'identical_all_metrics': True, 'markets': len(actual),
                                       'parser': parser.diagnostics()}
        finally:
            await parser.close()
            await client.close()
    target = Path('docs/evidence/bulk-ticker-parser-synthetic.json')
    target.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
