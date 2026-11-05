"""Opt-in bounded scheduling evidence. Never inspect locals, arguments or environment."""
from collections import deque
from copy import deepcopy
import os
from pathlib import Path
import sys
import threading
import time

try:
    import resource
except ImportError:  # Windows has no Unix resource module.
    resource = None


def resources():
    if resource is not None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result = {key: getattr(usage, key) for key in
                  ('ru_utime', 'ru_stime', 'ru_maxrss', 'ru_nvcsw', 'ru_nivcsw')}
        result['provider'] = 'resource.getrusage'
    else:
        usage = os.times()
        # Keep genuine cumulative CPU counters. Unknown Unix-only counters are
        # null, never fabricated zeros or differently defined memory measures.
        result = {'ru_utime': usage.user, 'ru_stime': usage.system,
                  'ru_maxrss': None, 'ru_nvcsw': None, 'ru_nivcsw': None,
                  'provider': 'os.times'}
    # Fixed cgroup v2 counters only; absent/unreadable counters remain unknown.
    for name, keys in {'cpu.stat': {'usage_usec', 'nr_throttled', 'throttled_usec'},
                       'memory.events': {'high', 'max', 'oom', 'oom_kill'}}.items():
        values = None
        try:
            lines = (Path('/sys/fs/cgroup') / name).read_text().splitlines()
            values = {k: int(v) for k, v in (line.split() for line in lines) if k in keys}
        except (OSError, ValueError):
            pass
        result[name] = values
    return result


def stacks(loop_ident):
    frames = sys._current_frames()
    result = []
    # Loop first even if more threads than the bounded capture budget.
    identifiers = sorted(frames, key=lambda ident: ident != loop_ident)[:16]
    for ident in identifiers:
        frame = frames[ident]
        trace = []
        while frame is not None and len(trace) < 24:
            code = frame.f_code
            trace.append({'file': Path(code.co_filename).name, 'function': code.co_name,
                          'line': frame.f_lineno})
            frame = frame.f_back
        result.append({'role': 'event_loop' if ident == loop_ident else 'other', 'stack': trace})
    return result


class StallProbe:
    def __init__(self, *, interval=.5, threshold=2., cooldown=10., capacity=12):
        self.interval, self.threshold, self.cooldown = interval, threshold, cooldown
        self.events = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.heartbeat = time.monotonic()
        self.loop_ident = None
        self.last_capture = float('-inf')
        self.total = 0
        self.baseline = None

    def start(self):
        if self.thread is not None:
            return
        self.baseline = {'at_ms': int(time.time() * 1000), 'resources': resources()}
        self.loop_ident = threading.get_ident()
        self.beat()
        self.thread = threading.Thread(target=self._run, daemon=True, name='stall-probe')
        self.thread.start()

    def beat(self):
        with self.lock:
            self.heartbeat = time.monotonic()

    def observe(self, now, previous):
        with self.lock:
            age = max(0., now - self.heartbeat)
        gap = max(0., now - previous - self.interval)
        if max(age, gap) < self.threshold or now - self.last_capture < self.cooldown:
            return
        self.last_capture = now
        event = {'at_ms': int(time.time() * 1000), 'loop_heartbeat_age_ms': round(age * 1000, 3),
                 'watchdog_delay_ms': round(gap * 1000, 3),
                 'kind': 'shared_scheduling_gap' if gap >= self.threshold else 'loop_heartbeat_stall',
                 'resources': resources(), 'threads': stacks(self.loop_ident)}
        with self.lock:
            self.total += 1
            self.events.append(event)

    def _run(self):
        previous = time.monotonic()
        while not self.stop.wait(self.interval):
            now = time.monotonic()
            self.observe(now, previous)
            previous = now

    def snapshot(self):
        with self.lock:
            return {'enabled': True, 'running': bool(self.thread and self.thread.is_alive()),
                    'baseline': deepcopy(self.baseline), 'threshold_seconds': self.threshold, 'cooldown_seconds': self.cooldown,
                    'capacity': self.events.maxlen, 'total_captures': self.total,
                    'events': deepcopy(list(self.events)),
                    'meaning': 'Stacks at capture, not causal proof. Shared gaps may be GIL, CPU throttling, '
                               'process/host suspension or scheduling. No network latency measurement. '
                               'Resources are cumulative; compare successive captures. maxrss is KiB on Linux. '
                               'Unavailable platform counters are null; provider identifies the CPU source.'}

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1.)
