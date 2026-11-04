"""Bounded, opt-in public market payload diagnostics (not a wire capture)."""
from __future__ import annotations

import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import RLock

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DAY_FILE = re.compile(r"\d{4}-\d{2}-\d{2}\.jsonl\Z")
_OMITTED = {"headers", "requestheaders", "responseheaders", "url", "uri", "requesturl",
            "responseurl", "endpoint", "credentials"}
_SENSITIVE = {"token", "apikey", "secret", "password", "passwd", "passphrase", "authorization",
              "cookie", "setcookie", "privatekey", "signature", "credential", "bearer"}


def _redact(value, ancestors: set[int] | None = None, depth: int = 0):
    """Copy JSON values; reject unsupported/cyclic inputs rather than stringify them."""
    if depth > 64:
        raise ValueError("payload nesting limit")
    if value is None or type(value) in (str, int, float, bool):
        return value
    if not isinstance(value, (dict, list, tuple)):
        raise TypeError("payload must contain JSON values")
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        raise ValueError("cyclic payload")
    ancestors.add(id(value))
    try:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError("payload keys must be strings")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in _OMITTED:
                    continue
                if (normalized in _SENSITIVE or normalized.endswith(("token", "apikey", "secret", "password"))
                        or normalized.startswith(("secret", "privatekey"))):
                    result[key] = "[REDACTED]"
                else:
                    result[key] = _redact(item, ancestors, depth + 1)
            return result
        return [_redact(item, ancestors, depth + 1) for item in value]
    finally:
        ancestors.remove(id(value))


class RawMarketRecorder:
    """One recorder owns ``data_dir/recordings``; methods are safe across threads.

    ``record`` never raises ordinary payload/filesystem exceptions. Call it through
    ``asyncio.to_thread`` from async collectors. Venue filtering belongs to callers.
    Counters cover this instance's lifetime; disk usage includes earlier sessions.
    """

    def __init__(self, data_dir: str | Path, *, enabled: bool = False,
                 max_record_bytes: int = 262144, max_total_bytes: int = 104857600,
                 retention_days: int = 7):
        self.directory = Path(data_dir) / "recordings"
        self._lock = RLock()
        self._recorded = self._dropped = self._errors = self._bytes = 0
        self._config = self._validated({"enabled": enabled, "max_record_bytes": max_record_bytes,
                                       "max_total_bytes": max_total_bytes, "retention_days": retention_days})

    @staticmethod
    def _validated(config: dict) -> dict:
        if type(config["enabled"]) is not bool:
            raise ValueError("enabled must be a boolean")
        for key in ("max_record_bytes", "max_total_bytes", "retention_days"):
            if type(config[key]) is not int or config[key] < 1:
                raise ValueError(f"{key} must be a positive integer")
        if config["retention_days"] > 36500:
            raise ValueError("retention_days must be at most 36500")
        return config

    def configure(self, *, enabled: bool | None = None, max_record_bytes: int | None = None,
                  max_total_bytes: int | None = None, retention_days: int | None = None) -> dict:
        """Atomically validate/apply configuration; invalid settings raise ValueError.

        No files are deleted by configuring or reading status. Expiry is enforced
        on enabled record attempts. Lowering a quota does not delete retained data.
        """
        changes = {"enabled": enabled, "max_record_bytes": max_record_bytes,
                   "max_total_bytes": max_total_bytes, "retention_days": retention_days}
        with self._lock:
            self._config = self._validated({**self._config, **{k: v for k, v in changes.items() if v is not None}})
            return self.status()

    def _inventory(self, *, today: date | None = None) -> list[tuple[Path, int]]:
        if self.directory.is_symlink():
            raise OSError("recording directory cannot be a symlink")
        if not self.directory.exists():
            return []
        cutoff = today - timedelta(days=self._config["retention_days"] - 1) if today else None
        files = []
        for path in self.directory.iterdir():
            if not _DAY_FILE.fullmatch(path.name):
                continue
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if path.is_symlink() or not path.is_file():
                raise OSError("unexpected recording file type")
            if cutoff is not None and day < cutoff:
                path.unlink()
            else:
                files.append((path, path.stat().st_size))
        return files

    def status(self) -> dict:
        """Return safe counters and disk usage, without payloads, paths or errors."""
        with self._lock:
            disk_bytes, files = None, None
            try:
                inventory = self._inventory()
                disk_bytes, files = sum(size for _, size in inventory), len(inventory)
            except OSError:
                self._errors += 1
            return {**self._config, "recorded": self._recorded, "dropped": self._dropped,
                    "errors": self._errors, "bytes": self._bytes,
                    "disk_bytes": disk_bytes, "files": files}

    @staticmethod
    def _append(path: Path, encoded: bytes):
        with path.open("a+b") as handle:
            handle.seek(0, 2)
            previous_size = handle.tell()
            try:
                if handle.write(encoded) != len(encoded):
                    raise OSError("incomplete recording write")
                handle.flush()
            except Exception:
                # An interrupted write must not poison the next JSONL record.
                handle.truncate(previous_size)
                raise

    def record(self, venue: str, transport: str, payload: object) -> bool:
        """Persist one redacted JSON payload, returning whether it was written.

        Disabled calls return False without touching payloads/disk or counters.
        Oversize/quota drops increment dropped; malformed/I/O failures increment
        both dropped and errors. No exception text or rejected payload is saved.
        """
        with self._lock:
            if not self._config["enabled"]:
                return False
            try:
                if (not isinstance(venue, str) or not _LABEL.fullmatch(venue)
                        or not isinstance(transport, str) or not _LABEL.fullmatch(transport)):
                    raise ValueError("invalid venue or transport label")
                if isinstance(payload, (str, bytes, bytearray)):
                    # Text JSON must pass through redaction too; arbitrary wire
                    # strings might contain credentials and are never captured.
                    if len(payload) > self._config["max_record_bytes"]:
                        self._dropped += 1
                        return False
                    payload = json.loads(payload)
                if not isinstance(payload, (dict, list)):
                    raise TypeError("market payload must be an object or array")
                received_at_ms = int(time.time() * 1000)
                today = datetime.fromtimestamp(received_at_ms / 1000, UTC).date()
                entry = {"received_at_ms": received_at_ms, "venue": venue,
                         "transport": transport, "payload": _redact(payload)}
                encoded = (json.dumps(entry, ensure_ascii=False, allow_nan=False,
                                      separators=(",", ":")) + "\n").encode("utf-8")
                if len(encoded) > self._config["max_record_bytes"]:
                    self._dropped += 1
                    return False
                inventory = self._inventory(today=today)
                if sum(size for _, size in inventory) + len(encoded) > self._config["max_total_bytes"]:
                    self._dropped += 1
                    return False
                self.directory.mkdir(parents=True, exist_ok=True)
                self._append(self.directory / f"{today.isoformat()}.jsonl", encoded)
                self._recorded += 1
                self._bytes += len(encoded)
                return True
            except Exception:  # noqa: BLE001 - diagnostics must never interrupt collection.
                self._dropped += 1
                self._errors += 1
                return False
