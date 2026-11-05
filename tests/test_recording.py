"""Public diagnostic recording: actual disk bounds, redaction and concurrent writes."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from watch_money.recording import RawMarketRecorder


def entries(tmp_path):
    return [json.loads(line) for path in sorted((tmp_path / "recordings").glob("*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines()]


def test_disabled_is_default_and_switch_is_effective(tmp_path):
    recorder = RawMarketRecorder(tmp_path)
    assert not recorder.record("okx", "rest", object())
    assert not (tmp_path / "recordings").exists()
    assert recorder.status()["dropped"] == 0
    recorder.configure(enabled=True)
    assert recorder.record("okx", "rest", {"price": "100.25"})
    recorder.configure(enabled=False)
    assert not recorder.record("okx", "rest", {"price": 2})
    assert len(entries(tmp_path)) == 1


def test_recursively_redacts_without_mutating_payload_or_recording_request_context(tmp_path):
    recorder = RawMarketRecorder(tmp_path, enabled=True)
    payload = {"price": "102.30", "apiKey": "api-value", "info": [{"access_token": "token-value",
               "PRIVATE-KEY": "private-value", "secretKey": "secret-value", "symbol": "BTC"}],
               "headers": {"Authorization": "bearer-value"}, "url": "https://x/?key=url-value",
               "credentials": {"key": "credential-value"}, "count": 0, "available": False}
    assert recorder.record("okx", "rest:ccxt", payload)
    row = entries(tmp_path)[0]
    assert row["venue"] == "okx" and row["transport"] == "rest:ccxt"
    assert isinstance(row["received_at_ms"], int)
    saved = row["payload"]
    assert saved["apiKey"] == "[REDACTED]"
    assert saved["info"] == [{"access_token": "[REDACTED]", "PRIVATE-KEY": "[REDACTED]",
                              "secretKey": "[REDACTED]", "symbol": "BTC"}]
    assert not {"headers", "url", "credentials"}.intersection(saved)
    assert saved["price"] == "102.30" and saved["count"] == 0 and saved["available"] is False
    assert payload["apiKey"] == "api-value" and "headers" in payload
    serialized = json.dumps(row)
    assert all(secret not in serialized for secret in ["api-value", "token-value", "private-value",
                                                       "secret-value", "bearer-value", "url-value"])


def test_json_text_is_redacted_and_invalid_json_does_not_poison_file(tmp_path):
    recorder = RawMarketRecorder(tmp_path, enabled=True)
    assert recorder.record("alpha", "ws", b'{"token":"do-not-save","bid":123}')
    assert not recorder.record("alpha", "ws", '{"token":"malformed')
    assert recorder.record("alpha", "ws", {"bid": 124})
    assert entries(tmp_path)[0]["payload"] == {"token": "[REDACTED]", "bid": 123}
    assert recorder.status()["errors"] == 1


def test_per_record_limit_counts_utf8_envelope_and_newline(tmp_path):
    recorder = RawMarketRecorder(tmp_path, enabled=True, max_record_bytes=150)
    assert not recorder.record("alpha", "rest", {"message": "币" * 30})
    assert recorder.record("alpha", "rest", {"bid": 1})
    status = recorder.status()
    assert (status["recorded"], status["dropped"], status["errors"]) == (1, 1, 0)
    assert status["disk_bytes"] == status["bytes"] <= 150


def test_total_quota_includes_previous_session_and_never_truncates_retained_data(tmp_path):
    first = RawMarketRecorder(tmp_path, enabled=True)
    assert first.record("alpha", "rest", {"bid": 10})
    before = first.status()["disk_bytes"]
    recorder = RawMarketRecorder(tmp_path, enabled=True, max_total_bytes=before * 2 - 1)
    assert not recorder.record("alpha", "rest", {"bid": 10})
    assert recorder.status()["bytes"] == 0
    assert recorder.status()["disk_bytes"] == before
    assert len(entries(tmp_path)) == 1


def test_retention_uses_utc_dates_and_preserves_unrelated_files(tmp_path):
    folder = tmp_path / "recordings"
    folder.mkdir()
    today = datetime.now(UTC).date()
    stale = folder / f"{today - timedelta(days=2)}.jsonl"
    keep = folder / f"{today - timedelta(days=1)}.jsonl"
    stale.write_text("x" * 500)
    keep.write_text('{"fixture":"keep"}\n')
    unrelated = folder / "notes.txt"
    unrelated.write_text("do not delete")
    recorder = RawMarketRecorder(tmp_path, enabled=True, retention_days=2, max_total_bytes=250)
    assert recorder.status()["disk_bytes"] > 500  # Read-only status never prunes.
    assert recorder.record("alpha", "rest", {"bid": 10})
    assert not stale.exists() and keep.exists() and unrelated.read_text() == "do not delete"
    assert recorder.status()["files"] == 2
    assert recorder.status()["disk_bytes"] <= 250


def test_concurrent_records_have_valid_complete_lines_and_exact_counters(tmp_path):
    recorder = RawMarketRecorder(tmp_path, enabled=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(lambda n: recorder.record("alpha", "ws", {"sequence": n}), range(250)))
    assert all(outcomes)
    assert sorted(row["payload"]["sequence"] for row in entries(tmp_path)) == list(range(250))
    status = recorder.status()
    assert (status["recorded"], status["dropped"], status["errors"]) == (250, 0, 0)
    assert status["bytes"] == status["disk_bytes"]


def test_concurrent_quota_reservation_cannot_overshoot(tmp_path):
    recorder = RawMarketRecorder(tmp_path, enabled=True, max_total_bytes=1000)
    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(lambda n: recorder.record("alpha", "ws", {"sequence": n}), range(100)))
    status = recorder.status()
    assert 0 < sum(outcomes) < 100
    assert status["recorded"] == len(entries(tmp_path)) == sum(outcomes)
    assert status["recorded"] + status["dropped"] == 100
    assert status["bytes"] == status["disk_bytes"] <= 1000


def test_malformed_payloads_and_invalid_labels_fail_closed_then_recover(tmp_path):
    recorder = RawMarketRecorder(tmp_path, enabled=True)
    cyclic = {}
    cyclic["self"] = cyclic
    for payload in [object(), {"bid": float("nan")}, {"bid": float("inf")}, {1: "bad-key"}, cyclic]:
        assert not recorder.record("alpha", "ws", payload)
    assert not recorder.record("../outside", "ws", {"bid": 1})
    assert not recorder.record("alpha", "https://x/?token=hidden", {"bid": 1})
    assert recorder.status()["errors"] == recorder.status()["dropped"] == 7
    assert recorder.record("alpha", "ws", {"bid": 2})
    assert len(entries(tmp_path)) == 1


def test_filesystem_failure_does_not_escape_or_expose_exception(tmp_path):
    blocked = tmp_path / "recordings"
    blocked.write_text("not a directory")
    recorder = RawMarketRecorder(tmp_path, enabled=True)
    assert not recorder.record("alpha", "ws", {"bid": 1})
    status = recorder.status()
    assert status["errors"] >= 1 and status["dropped"] == 1
    assert "error" not in status and "last_error" not in status
    blocked.unlink()
    assert recorder.record("alpha", "ws", {"bid": 2})


def test_invalid_configuration_is_atomic(tmp_path):
    recorder = RawMarketRecorder(tmp_path)
    with pytest.raises(ValueError):
        recorder.configure(enabled=True, max_total_bytes=-1)
    assert recorder.status()["enabled"] is False
    for changes in [{"retention_days": 0}, {"max_record_bytes": True}, {"enabled": "yes"}]:
        with pytest.raises(ValueError):
            recorder.configure(**changes)
