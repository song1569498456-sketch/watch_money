"""Narrow OKX/Bitget public bulk paths with bounded off-loop CCXT parsing.

HTTP, sessions and rate limiters stay on their owning event loop. Only read-only
parse_tickers runs on a captured parser view. Unsupported routing uses CCXT.
Reviewed against CCXT 4.5.91 fetch_tickers:
https://github.com/ccxt/ccxt/blob/v4.5.91/python/ccxt/async_support/okx.py
https://github.com/ccxt/ccxt/blob/v4.5.91/python/ccxt/async_support/bitget.py
Equality tests guard routing
and all returned metrics. No account endpoints or credentials are involved.
"""
from __future__ import annotations

import asyncio
import ccxt
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from time import perf_counter, thread_time

from watch_money.models import now_ms
from watch_money.async_work import finish_owned


class ReceivedTickers(dict):
    def __init__(self, values, received_at):
        super().__init__(values)
        self.received_at = received_at


class BulkParseBusy(RuntimeError):
    pass


class BulkTickerParser:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bulk-ticker-parse')
        self.lock = asyncio.Lock()
        self.pending = 0
        self.closed = False
        self._close_task = None
        self.metrics = {'completed': 0, 'failed': 0, 'fallbacks': 0, 'last': None}
        self.reviewed_ccxt_version = '4.5.91'

    @staticmethod
    def supported(client, params):
        # Restrict to the standard spot/swap paths the adapter explicitly owns.
        # Unknown options/branches retain upstream behavior instead of guessing.
        if ccxt.__version__ != '4.5.91' or not type(client).__module__.startswith('ccxt.async_support.'):
            return False
        if client.id == 'okx':
            return params.get('type') in {'spot', 'swap'} and set(params) <= {'type'}
        if client.id == 'bitget':
            return (params.get('type') in {'spot', 'swap'}
                    and set(params) <= {'type', 'subType', 'productType'}
                    and not client.options.get('uta') and not client.options.get('stock'))
        return False

    @staticmethod
    def _parse(parser, rows, symbols):
        started, cpu = perf_counter(), thread_time()
        values = parser.parse_tickers(rows, symbols)
        return values, {'parse_wall_ms': round((perf_counter()-started)*1000, 3),
                        'parse_cpu_ms': round((thread_time()-cpu)*1000, 3), 'rows': len(rows)}

    async def _fallback(self, client, symbols, params):
        values = await client.fetch_tickers(symbols, params)
        # Earliest boundary exposed by generic CCXT: method return (its own
        # decoding/parsing is internal). Never timestamp after observers.
        received = now_ms()
        return ReceivedTickers(values, received) if isinstance(values, dict) else values

    async def fetch(self, client, symbols, params):
        if self.closed:
            raise BulkParseBusy('bulk parser closed')
        if not self.supported(client, params):
            self.metrics['fallbacks'] += 1
            return await self._fallback(client, symbols, params)
        if self.closed or self.pending >= 2:
            raise BulkParseBusy('bulk parser closed or at capacity')
        self.pending += 1
        try:
            async with self.lock:
                if self.closed:
                    raise BulkParseBusy('bulk parser closed')
                if client.markets is None:
                    await client.load_markets()
                # set_markets publishes replacement dictionaries synchronously;
                # capture both indexes together before awaiting the HTTP response. Parsers only
                # read these indexes and precision config. Do not copy sessions.
                parser = copy(client)
                parser.session = None
                parser.socks_proxy_sessions = None
                parser.options = dict(client.options)
                resolved = symbols
                if client.id == 'okx':
                    resolved = client.market_symbols(symbols)
                    response = await client.publicGetMarketTickers({'instType': client.convert_to_instrument_type(params['type'])})
                else:
                    market = client.market(symbols[0]) if symbols else None
                    market_type, remaining = client.handle_market_type_and_params('fetchTickers', market, params)
                    passed_subtype = client.safe_string(remaining, 'subType')
                    product, remaining = client.handle_product_type_and_params(market, remaining)
                    stock, remaining = client.handle_option_bool_and_params(remaining, 'fetchTickers', 'stock',
                        client.safe_bool(market, 'stock', False) if market else None)
                    uta, remaining = await client.handle_uta_and_params(remaining, 'fetchTickers', False)
                    if stock or uta:
                        self.metrics['fallbacks'] += 1
                        # Upstream rules changed or the selected market has special routing.
                        return await self._fallback(client, symbols, params)
                    if market_type == 'spot' and passed_subtype is None:
                        response = await client.publicSpotGetV2SpotMarketTickers(remaining)
                    else:
                        response = await client.publicMixGetV2MixMarketTickers(client.extend({'productType': product}, remaining))
                received_at = now_ms()  # Decoded HTTP response, never parse completion.
                rows = client.safe_list(response, 'data', [])
                job = asyncio.get_running_loop().run_in_executor(self.executor, self._parse, parser, rows, resolved)
                values, metrics = await finish_owned(job)
                self.metrics['completed'] += 1
                self.metrics['last'] = {**metrics, 'received_at': received_at, 'finished_at': now_ms()}
                return ReceivedTickers(values, received_at)
        except Exception:
            self.metrics['failed'] += 1
            raise
        finally:
            self.pending -= 1

    def diagnostics(self):
        return {**self.metrics, 'last': dict(self.metrics['last']) if self.metrics['last'] else None,
                'pending': self.pending, 'capacity': 1, 'max_pending': 2, 'closed': self.closed,
                'reviewed_ccxt_version': self.reviewed_ccxt_version, 'installed_ccxt_version': ccxt.__version__}

    async def close(self):
        self.closed = True
        if self._close_task is None:
            async def shutdown():
                async with self.lock:
                    self.executor.shutdown(wait=True)
            self._close_task = asyncio.create_task(shutdown())
        await finish_owned(self._close_task)
