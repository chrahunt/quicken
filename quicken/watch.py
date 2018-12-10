import fcntl
import os
from pathlib import Path
import signal
from typing import Callable


def wait_for_create(path: Path, timeout: float = 5) -> bool:
    return _wait_for(path, lambda: path.exists(), timeout)


def wait_for_delete(path: Path, timeout: float = 5) -> bool:
    return _wait_for(path, lambda: not path.exists(), timeout)


def _wait_for(
        path: Path, predicate: Callable[[], bool], timeout: float = 5) -> bool:
    """
    Args:
        path
        timeout
    """
    waiting = True
    success = False

    fd = os.open(path.parent, os.O_RDONLY)
    fcntl.fcntl(fd, fcntl.F_SETSIG, 0)
    fcntl.fcntl(
        fd, fcntl.F_NOTIFY,
        fcntl.DN_MODIFY | fcntl.DN_CREATE | fcntl.DN_MULTISHOT)

    def file_handler(_signum, _frame):
        nonlocal success, waiting
        if predicate():
            # Test passed.
            success = True
            waiting = False
    signal.signal(signal.SIGIO, file_handler)

    def timeout_handler(_signum, _frame):
        nonlocal waiting
        waiting = False
    signal.signal(signal.SIGALRM, timeout_handler)

    signal.setitimer(signal.ITIMER_REAL, timeout)
    # Wait for signals.
    while waiting:
        signal.pause()

    # Clean up.
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGIO, signal.SIG_DFL)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    os.close(fd)

    return success
