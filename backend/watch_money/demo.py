"""Explicit illustrative data. Never stored or represented as live market data."""
from .models import Instrument, MarketSnapshot, now_ms


def demo_markets():
    stamp = now_ms()
    instruments, snapshots = [], []
    for symbol, price in [('BTC', 68000), ('ETH', 2400), ('SOL', 150)]:
        for venue, factor, kind in [('demo-alpha', 1, 'spot'), ('demo-beta', 1.002, 'perpetual')]:
            identity = f'{venue}:{symbol}:USDT:{kind}'
            instruments.append(Instrument(id=identity, venue=venue, native_symbol=f'{symbol}/USDT',
                                          asset_id=f'demo:{symbol}', base=symbol, quote='USDT',
                                          kind=kind, mapping_verified=True))
            snapshots.append(MarketSnapshot(instrument_id=identity, venue=venue,
                                             bid=price * factor * .9999, ask=price * factor * 1.0001,
                                             last=price * factor, volume=100000, volume_unit='USDT',
                                             funding_rate=.0001 if kind == 'perpetual' else None,
                                             funding_interval_hours=8 if kind == 'perpetual' else None,
                                             funding_kind='predicted' if kind == 'perpetual' else None,
                                             next_funding_at=stamp + 3600_000 if kind == 'perpetual' else None,
                                             source_at=stamp, received_at=stamp, source='illustrative-demo',
                                             is_demo=True, issues=['SIMULATED_NOT_LIVE']))
    return instruments, snapshots
