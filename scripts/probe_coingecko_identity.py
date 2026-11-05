"""Bounded read-only Demo probe. Only writes a temporary cache, never user mappings."""
import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from watch_money.identity_catalog import CoinGeckoCatalog, EXCHANGES
from watch_money.storage import Storage


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--venues', nargs='+', choices=sorted(EXCHANGES), default=['gate'])
    parser.add_argument('--pages', type=int, choices=range(1, 21), default=2)
    parser.add_argument('--live', action='store_true', help='Explicitly authorize the bounded public requests')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='coingecko-probe-') as directory:
        provider = CoinGeckoCatalog(Storage(directory))
        policy_file = Path('/etc/codex/network-policy.json')
        if policy_file.exists():
            policy = json.loads(policy_file.read_text()).get('http_network_policy', {})
            hosts = {r.get('host') for r in policy.get('egress_rules', [])}
            if policy.get('type') == 'restricted' and 'api.coingecko.com' not in hosts:
                print(json.dumps({'attempted': False, 'reason': 'environment_policy_does_not_allow_api.coingecko.com',
                                  'key_configured': bool(provider.key), 'actual_permission_and_coverage': 'unverified'}))
                return
        if not args.live:
            print(json.dumps({'attempted': False, 'reason': 'pass --live after configuring Demo key and permitted network',
                              'key_configured': bool(provider.key)}))
            return
        try:
            result = await provider.snapshot(args.venues, max_pages=args.pages)
            print(json.dumps({k: v for k, v in result.items() if k not in ('coins', 'exchanges')} | {
                'coin_count': len(result['coins']), 'coins_with_platforms': sum(bool(c['platforms']) for c in result['coins']),
                'requested_page_cap': args.pages, 'user_mappings_changed': False}, indent=2))
        finally:
            await provider.close()


if __name__ == '__main__':
    asyncio.run(main())
