"""Run small read-only live probes; retain errors without credentials or response bodies."""
import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

PROBES = {
    'binance': ('GET', 'https://api.binance.com/api/v3/time', None),
    'okx': ('GET', 'https://openapi.okx.com/api/v5/public/time', None),
    'bybit': ('GET', 'https://api.bybit.com/v5/market/time', None),
    'bitget': ('GET', 'https://api.bitget.com/api/v2/public/time', None),
    'hyperliquid': ('POST', 'https://api.hyperliquid.xyz/info', {'type': 'meta'}),
    'lighter': ('GET', 'https://mainnet.zklighter.elliot.ai/api/v1/orderBooks', None),
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/live-probes.json')
    args = parser.parse_args()
    async with httpx.AsyncClient(timeout=15) as client:
        async def probe(name, request):
            method, url, body = request
            item = {'venue': name, 'url': url, 'at': datetime.now(UTC).isoformat()}
            try:
                result = await client.request(method, url, json=body) if body else await client.request(method, url)
                item.update(status_code=result.status_code, reachable=result.is_success)
                if result.is_success:
                    item['json_type'] = type(result.json()).__name__
            except Exception as exc:
                item.update(reachable=False, error=type(exc).__name__)
            return item
        report = await asyncio.gather(*(probe(n, r) for n, r in PROBES.items()))
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
