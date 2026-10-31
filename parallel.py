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
    retry_other: bool = False,
) -> Dict[str, int]:
    """Share items across multiple named workers running in parallel threads.

    Each worker runs under contextvars.copy_context().run so console output and
    stop requests reach the task context. Returns how many items each worker finished.
    After 3 consecutive errors, that worker stops and the other workers carry on.
    With retry_other, an item that fails goes to a worker that hasn't tried it yet (for example a
    cloud AI out of quota hands the video to the local model); on_error is called only when every
    worker that could take it has failed it.
    """
    if not workers:
        return {}

    pending = [(item, frozenset(), None) for item in items]   # (item, workers that failed it, last error)
    if not pending:
        return {name: 0 for name in workers}

    counts: Dict[str, int] = {name: 0 for name in workers}
    cond = threading.Condition()
    alive = set(workers)
    in_flight = [0]

    def _report(name: str, item: Any, exc: Exception) -> None:
        if on_error:
            try:
                on_error(name, item, exc)
            except Exception:
                pass

    def _stranded() -> list:
        """Items no live worker can still try; removed from pending. Call with cond held."""
        stuck = [p for p in pending if not (alive - p[1])]
        for p in stuck:
            pending.remove(p)
        return stuck

    def _take(name: str):
        with cond:
            while not should_stop():
                for i, (item, tried, _) in enumerate(pending):
                    if name not in tried:
                        in_flight[0] += 1
                        return pending.pop(i)
                if in_flight[0] == 0 or not retry_other:
                    return None
                cond.wait(0.2)   # another worker may still fail an item over to this one
            return None

    def _worker_loop(name: str, fn: Callable[[Any], None]) -> None:
        consecutive_errors = 0
        try:
            while consecutive_errors < 3:
                job = _take(name)
                if job is None:
                    break
                item, tried, _ = job
                try:
                    fn(item)
                except Exception as exc:
                    consecutive_errors += 1
                    with cond:
                        in_flight[0] -= 1
                        tried = tried | {name}
                        retry = retry_other and bool(alive - tried)
                        if retry:
                            pending.append((item, tried, exc))
                        cond.notify_all()
                    if retry:
                        safe_print(f"   Warning: {name} failed ({str(exc).splitlines()[0][:160]}); trying the other AI.")
                    else:
                        _report(name, item, exc)
                    continue
                with cond:
                    in_flight[0] -= 1
                    counts[name] += 1
                    cond.notify_all()
                consecutive_errors = 0
        finally:
            with cond:
                alive.discard(name)
                stuck = _stranded()
                cond.notify_all()
            for item, _, exc in stuck:
                if exc is not None:
                    _report(name, item, exc)

    threads = []
    for name, fn in workers.items():
        ctx = contextvars.copy_context()
        t = threading.Thread(target=ctx.run, args=(_worker_loop, name, fn))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    return counts
