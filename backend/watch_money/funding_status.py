"""Observation quality is independent of successful transport/poll status."""


def observation_state(status):
    observed = status.funding_observed_markets
    fresh = status.funding_fresh_markets
    if not observed:
        return 'not_observed'
    if fresh and fresh == status.funding_subscriptions:
        return 'fields_ready'
    if fresh:
        return 'partially_ready'
    if status.funding_source_future_markets >= observed:
        return 'source_time_future'
    if status.funding_source_stale_markets >= observed:
        return 'source_stale'
    if status.funding_source_missing_markets >= observed:
        return 'source_time_unknown'
    return 'incomplete_or_mixed'
