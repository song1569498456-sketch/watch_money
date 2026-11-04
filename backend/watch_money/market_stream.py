"""Full initial state followed by field changes, removals and freshness transitions."""
from __future__ import annotations


def market_delta(previous: dict | None, current: dict) -> dict:
    if previous is None or previous.get('mode') != current.get('mode'):
        return {'type': 'markets', **current}
    old_instruments = {i['id']: i for i in previous['instruments']}
    new_instruments = {i['id']: i for i in current['instruments']}
    old_snapshots = {s['instrument_id']: s for s in previous['snapshots']}
    new_snapshots = {s['instrument_id']: s for s in current['snapshots']}

    def changed(snapshot):
        old = old_snapshots.get(snapshot['instrument_id'])
        if old is None:
            return True
        # Age is rendered against the browser clock. Stale flags still transmit
        # at their transitions even when no source event arrives.
        ignored = {'age_seconds', 'field_ages_seconds'}
        return {k: v for k, v in snapshot.items() if k not in ignored} != {
            k: v for k, v in old.items() if k not in ignored}

    return {'type': 'markets_delta', 'mode': current.get('mode'),
            'instruments': [i for key, i in new_instruments.items() if old_instruments.get(key) != i],
            'snapshots': [s for s in new_snapshots.values() if changed(s)],
            'removed_instruments': sorted(old_instruments.keys() - new_instruments.keys()),
            'removed_snapshots': sorted(old_snapshots.keys() - new_snapshots.keys())}
