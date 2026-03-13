import queue
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional


class TkLogQueueWriter:
    """
    将print输出写入线程安全队列，供Tkinter主线程定时刷新到Text控件。
    """

    def __init__(self, q: "queue.Queue[str]"):
        self._q = q
        self._lock = threading.Lock()

    def write(self, s: str) -> None:
        if not s:
            return
        with self._lock:
            self._q.put(s)

    def flush(self) -> None:
        return


@dataclass(frozen=True)
class StdoutRedirect:
    restore: Callable[[], None]


def redirect_stdout_to_queue(q: "queue.Queue[str]") -> StdoutRedirect:
    old_out = sys.stdout
    old_err = sys.stderr
    writer = TkLogQueueWriter(q)
    sys.stdout = writer  # type: ignore[assignment]
    sys.stderr = writer  # type: ignore[assignment]

    def restore() -> None:
        sys.stdout = old_out
        sys.stderr = old_err

    return StdoutRedirect(restore=restore)


def safe_int(value: str, default: int) -> int:
    try:
        return int(value.strip())
    except Exception:
        return default


def safe_str(value: Optional[str], default: str) -> str:
    v = (value or "").strip()
    return v if v else default

