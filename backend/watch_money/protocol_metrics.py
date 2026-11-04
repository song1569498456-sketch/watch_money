"""Merge raw protocol observations without borrowing another field's clock."""
from .models import MarketSnapshot


def merge_protocol_metrics(old: MarketSnapshot | None, incoming: MarketSnapshot, now: int) -> dict:
    values = dict(old.protocol_metrics) if old else {}
    sources = dict(old.protocol_metric_sources) if old else {}
    times = dict(old.protocol_metric_timestamps) if old else {}
    receipts = dict(old.protocol_metric_received_at) if old else {}
    # Legacy envelope times may belong to a later price patch. Their field
    # provenance is unknown; never assign that envelope to an old raw metric.
    if old:
        for key in values:
            sources.setdefault(key, 'legacy_field_provenance_unavailable')
            times.setdefault(key, None)
    clears = set(incoming.cleared_protocol_metrics)
    for key in set(incoming.protocol_metrics) | clears:
        source = incoming.protocol_metric_sources.get(key, incoming.source)
        stamp = incoming.protocol_metric_timestamps.get(key, incoming.source_at)
        received = incoming.protocol_metric_received_at.get(key, incoming.received_at)
        previous = times.get(key)
        prior_received = receipts.get(key)
        # A deletion belongs to its source; another source cannot erase it.
        if key in clears and key in sources and source != sources[key]:
            continue
        older = False
        if previous is not None and previous <= min(now, prior_received if prior_received is not None else now) and stamp is not None:
            older = stamp < previous
        elif prior_received is not None:
            older = received < prior_received
        if key not in values and key in times and key not in clears:
            barrier = previous if previous is not None else prior_received
            observed = stamp if stamp is not None else received
            if barrier is not None and barrier <= min(now, prior_received if prior_received is not None else now):
                older = older or observed <= barrier
        if older:
            continue
        if key in clears:
            values.pop(key, None)
        else:
            values[key] = incoming.protocol_metrics[key]
        sources[key], times[key], receipts[key] = source, stamp, received
    return {'protocol_metrics': values, 'protocol_metric_sources': sources,
            'protocol_metric_timestamps': times, 'protocol_metric_received_at': receipts,
            'cleared_protocol_metrics': []}
