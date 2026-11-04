"""Full initial state followed by field changes, removals and freshness transitions."""
from __future__ import annotations


def _versioned(view):
    return view is not None and any(key in view for key in ('server_instance', 'view_seq', 'view_context'))


def _valid_version(view):
    return (view is not None and isinstance(view.get('server_instance'), str) and bool(view['server_instance'])
            and type(view.get('view_seq')) is int and view['view_seq'] > 0
            and isinstance(view.get('view_context'), dict))


def market_delta(previous: dict | None, current: dict) -> dict:
    versioned = _versioned(previous) or _versioned(current)
    reset = previous is None or previous.get('mode') != current.get('mode')
    if versioned:
        reset = reset or not (_valid_version(previous) and _valid_version(current))
        if not reset:
            reset = (previous['server_instance'] != current['server_instance']
                     or previous['view_context'] != current['view_context']
                     or current['view_seq'] <= previous['view_seq'])
    if reset:
        frame = {'type': 'markets', **current}
        if _versioned(current):
            frame['base_view_seq'] = None
        return frame
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

    metadata = ({key: current[key] for key in ('server_instance', 'view_seq', 'view_context')}
                | {'base_view_seq': previous['view_seq']}) if versioned else {}
    return {'type': 'markets_delta', 'mode': current.get('mode'), **metadata,
            'instruments': [i for key, i in new_instruments.items() if old_instruments.get(key) != i],
            'snapshots': [s for s in new_snapshots.values() if changed(s)],
            'removed_instruments': sorted(old_instruments.keys() - new_instruments.keys()),
            'removed_snapshots': sorted(old_snapshots.keys() - new_snapshots.keys())}
