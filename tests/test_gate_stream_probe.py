"""Offline probe harness tests; these reports are not live Gate evidence."""

import asyncio
import importlib.util
from pathlib import Path

import pytest
from watch_money.models import MarketSnapshot, now_ms
from test_gate_perp_integration import instrument

spec = importlib.util.spec_from_file_location(
    "gate_probe", Path(__file__).parents[1] / "scripts/probe_gate_streams.py"
)
probe_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe_module)


class Adapter:
    closed = False

    async def discover(self):
        return [instrument(), instrument("BTC")]

    async def close(self):
        self.closed = True


class Stream:
    closed = False

    def supports_instrument(self, item):
        return True

    def select_instruments(self, items):
        return items

    def diagnostics(self):
        return {"state": "closed" if self.closed else "partial"}

    async def stream(self, items):
        at = now_ms()
        try:
            yield MarketSnapshot(
                instrument_id=items[0].id,
                venue="gate",
                source="synthetic",
                bid=100,
                ask=101,
                source_at=at - 61_000,
                received_at=at,
            )
            await asyncio.Event().wait()
        finally:
            self.closed = True

    async def close(self):
        self.closed = True


async def test_probe_captures_live_diagnostics_before_timeout_close_and_keeps_stale_counts():
    adapter, stream = Adapter(), Stream()
    report = await probe_module.probe(
        adapter, stream, seconds=0.01, max_markets=1, scopes={"futures:usdt", "futures:btc"}
    )
    assert adapter.closed and stream.closed
    assert report["source_streams"]["state"] == "partial"
    assert report["closed_source_streams"]["state"] == "closed"
    assert report["truncated"] and report["eligible_markets_in_requested_scopes"] == 2
    coverage = next(iter(report["coverage_by_scope"].values()))
    assert coverage["timed_bbo"] == 1 and coverage["fresh_bbo"] == 0
    assert report["received_patches"] == 1 and report["error_type"] is None


async def test_discovery_failure_is_safe_report_and_closes_resources():
    class Failed(Adapter):
        async def discover(self):
            raise OSError("private transport text must not appear")

    adapter, stream = Failed(), Stream()
    report = await probe_module.probe(adapter, stream, seconds=0.01, max_markets=1, scopes={"spot"})
    assert report["error_type"] == "OSError" and report["catalog_markets"] == 0
    assert "private transport" not in str(report)
    assert adapter.closed and stream.closed


def test_cli_requires_explicit_live_flag(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["probe_gate_streams.py", "--report", str(tmp_path / "report.json")])
    monkeypatch.setattr(probe_module, "CexAdapter", lambda _: pytest.fail("must not instantiate live client"))
    with pytest.raises(SystemExit) as result:
        probe_module.main()
    assert result.value.code == 2 and not (tmp_path / "report.json").exists()


def test_coverage_keeps_silent_stale_unknown_and_invalid_separate():
    items = [instrument(str(i)) for i in range(7)]
    at = now_ms()
    def snapshot(item, **changes):
        data = dict(instrument_id=item.id, venue='gate', source='synthetic',
                    bid=100, ask=101, source_at=at, received_at=at)
        return MarketSnapshot(**(data | changes))
    latest = {
        items[0].id: snapshot(items[0]),
        items[1].id: snapshot(items[1], source_at=at - 61000),
        items[2].id: snapshot(items[2], source_at=None),
        items[3].id: snapshot(items[3], bid=None),
        items[4].id: snapshot(items[4], bid=102),
        items[5].id: snapshot(items[5], source_at=at + 1),
    }
    rows = probe_module.coverage(items, latest, {'futures:usd1'}, at)
    assert rows['futures:usd1']['selected'] == 0
    assert rows['futures:0']['fresh_bbo'] == 1
    assert rows['futures:1']['stale_bbo'] == 1
    assert rows['futures:2']['untrusted_time'] == 1
    assert rows['futures:3']['missing_bbo'] == 1
    assert rows['futures:4']['invalid_bbo'] == 1
    assert rows['futures:5']['invalid_bbo'] == 1
    assert rows['futures:6']['unobserved'] == 1
    for row in rows.values():
        assert row['selected'] == sum(row[k] for k in (
            'fresh_bbo', 'stale_bbo', 'untrusted_time', 'missing_bbo', 'invalid_bbo', 'unobserved'))
    later = probe_module.coverage(items, latest, set(), at + 61001)
    assert later['futures:0']['fresh_bbo'] == 0
    assert later['futures:0']['stale_bbo'] == 1


async def test_silent_stream_is_sampled_without_cancelling_pending_receive(monkeypatch):
    clock = [1_800_000_000_000]
    monkeypatch.setattr(probe_module, 'now_ms', lambda: clock[0])
    elapsed = [0.0]
    monkeypatch.setattr(probe_module, 'monotonic', lambda: elapsed[0])
    async def controlled_wait(tasks, timeout):
        await asyncio.sleep(0)  # let the existing receive task yield or block
        done = {task for task in tasks if task.done()}
        if not done:
            elapsed[0] += .02
            if elapsed[0] >= .04:
                clock[0] += 61001
        return done, set(tasks) - done
    monkeypatch.setattr(probe_module.asyncio, 'wait', controlled_wait)
    class Silent(Stream):
        calls = 0
        async def stream(self, items):
            self.calls += 1
            yield MarketSnapshot(instrument_id=items[0].id, venue='gate', source='synthetic',
                                 bid=100, ask=101, source_at=clock[0], received_at=clock[0])
            await asyncio.Event().wait()
    stream = Silent()
    report = await probe_module.probe(Adapter(), stream, seconds=.05, max_markets=1,
                                      scopes={'futures:btc'}, sample_seconds=.01)
    samples = report['coverage_samples']
    assert 3 <= len(samples) <= 122
    assert stream.calls == 1 and stream.closed and report['received_patches'] == 1
    assert samples[0]['coverage_by_scope']['futures:btc']['unobserved'] == 1
    assert any(sample['coverage_by_scope']['futures:btc']['fresh_bbo'] == 1 for sample in samples)
    assert samples[-1]['coverage_by_scope']['futures:btc']['stale_bbo'] == 1
    assert samples[-1]['coverage_by_scope']['futures:btc']['fresh_bbo'] == 0
    assert report['coverage_by_scope'] == samples[-1]['coverage_by_scope']


@pytest.mark.parametrize('seconds,interval', [(0, 15), (601, 15), (10, 0), (10, float('nan'))])
async def test_probe_rejects_unbounded_sampling_before_discovery(seconds, interval):
    with pytest.raises(ValueError):
        await probe_module.probe(Adapter(), Stream(), seconds=seconds, max_markets=1,
                                 scopes={'spot'}, sample_seconds=interval)
