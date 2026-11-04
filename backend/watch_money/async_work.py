"""Wait for owned work to finish before propagating caller cancellation."""
import asyncio


async def finish_owned(future):
    """Keep the owned future shielded through *every* cancellation request.

    Cancelling the asyncio wrapper of an executor job doesn't stop its thread.
    Owners must not release their admission lock or finish shutdown on that basis.
    Cancellation is deferred, not discarded; a stuck worker also keeps its owner
    pending. All callers must avoid cancelling the owned future directly.
    """
    cancellation = None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as exc:
            cancellation = exc
        except Exception:
            break
    if cancellation is not None:
        if not future.cancelled():
            future.exception()  # Retrieve a worker error even when cancellation wins.
        raise cancellation
    return future.result()
