"""Same large public-frame fixture, inline vs bounded decode; no network calls."""
import asyncio
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from watch_money.adapters.dex import HyperliquidAdapter
from watch_money.adapters.dex_streaming import DexStreamAdapter


async def run(worker):
    adapter = HyperliquidAdapter()
    stream = DexStreamAdapter(adapter)
    frame = json.dumps({'channel': 'l2Book', 'data': {'coin': 'BTC', 'time': 1800000000000,
        'levels': [[{'px': str(n), 'sz': '1.25', 'n': 2} for n in range(12000)], []]}})
    async def recv():
        return frame
    if not worker:
        async def inline(raw):
            return json.loads(raw)
        stream._decode_hyperliquid = inline
    stop = asyncio.Event()
    delays = []
    async def heartbeat():
        while not stop.is_set():
            at = perf_counter()
            await asyncio.sleep(.001)
            delays.append(max(0, perf_counter() - at - .001) * 1000)
    task = asyncio.create_task(heartbeat())
    await asyncio.sleep(.002)
    started = perf_counter()
    for index in range(128):
        decoded = await stream._receive(SimpleNamespace(recv=recv))
        assert len(decoded['data']['levels'][0]) == 12000
        if (index + 1) % 64 == 0:
            await asyncio.sleep(0)
    elapsed = perf_counter() - started
    stop.set()
    await task
    await stream.close()
    await adapter.close()
    return {'frames': 128, 'frame_bytes': len(frame), 'seconds': round(elapsed, 4),
            'loop_samples': len(delays), 'max_loop_delay_ms': round(max(delays), 3)}


async def main():
    result = {'scope': 'synthetic repeated large frames, fixed payload/count; no live exchange or network acceptance',
              'inline': await run(False), 'bounded_worker': await run(True)}
    target = Path('docs/evidence/native-decode-synthetic.json')
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main())
