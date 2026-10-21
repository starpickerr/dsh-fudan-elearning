"""Reentrant, process-shared state lock for independent local clients."""

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

from .errors import SyncCancelled

_registry_guard = threading.Lock()
_locks = {}
_held = threading.local()


@contextmanager
def state_lock(root, cancel_event=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = str(root)
    with _registry_guard:
        lock = _locks.setdefault(key, threading.RLock())
    while not lock.acquire(timeout=0.05):
        if cancel_event is not None and cancel_event.is_set():
            raise SyncCancelled("State update cancelled")
    try:
        handles = getattr(_held, "handles", None)
        if handles is None:
            handles = _held.handles = {}
        if key in handles:
            yield
            return
        descriptor = os.open(root / ".mcp-state.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
            else:
                import fcntl
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise SyncCancelled("State update cancelled")
                try:
                    if os.name == "nt":
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    pass
                except OSError as error:
                    if error.errno not in {11, 13, 35, 36}:
                        raise
                time.sleep(0.05)
            handles[key] = descriptor
            try:
                yield
            finally:
                del handles[key]
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    finally:
        lock.release()
