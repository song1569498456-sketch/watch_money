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
