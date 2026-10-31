"""
Parallel task execution helpers: shared worker queues with error tolerance and per-worker attribution.

Used when a task shares one batch of work across two AI models (e.g. local Ollama + cloud Claude).
"""
import contextvars
import queue
import threading
from typing import Any, Callable, Dict, Iterable, Optional


PRINT_LOCK = threading.Lock()


def safe_print(*args: Any, **kwargs: Any) -> None:
    """Print thread-safely guarded by PRINT_LOCK."""
    with PRINT_LOCK:
        print(*args, **kwargs)


def share(
    items: Iterable[Any],
    workers: Dict[str, Callable[[Any], None]],
    should_stop: Callable[[], bool] = lambda: False,
    on_error: Optional[Callable[[str, Any, Exception], None]] = None,
) -> Dict[str, int]:
    """Share items across multiple named workers running in parallel threads.

    Each worker runs under contextvars.copy_context().run so console output and
    stop requests reach the task context. Returns how many items each worker finished.
    After 3 consecutive errors, that worker stops and the other workers carry on.
    """
    if not workers:
        return {}

    item_list = list(items)
    if not item_list:
        return {name: 0 for name in workers}

    q: queue.Queue = queue.Queue()
    for item in item_list:
        q.put(item)

    counts: Dict[str, int] = {name: 0 for name in workers}
    counts_lock = threading.Lock()

    def _worker_loop(name: str, fn: Callable[[Any], None]) -> None:
        consecutive_errors = 0
        while not should_stop():
            try:
                item = q.get_nowait()
            except queue.Empty:
                break

            if should_stop():
                break

            try:
                fn(item)
                with counts_lock:
                    counts[name] += 1
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                if on_error:
                    try:
                        on_error(name, item, exc)
                    except Exception:
                        pass
                if consecutive_errors >= 3:
                    break

    threads = []
    for name, fn in workers.items():
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(_worker_loop, name, fn))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return counts
