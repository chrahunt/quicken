from collections import namedtuple
from pathlib import Path
import signal

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


Action = namedtuple('Action', 'path present')


def wait_for_create(path: Path, timeout: float = 5) -> bool:
    return _wait_for(Action(path, True), timeout)


def wait_for_delete(path: Path, timeout: float = 5) -> bool:
    return _wait_for(Action(path, False), timeout)


def _wait_for(action: Action, timeout: float = 5) -> bool:
    """
    Args:
        path
        timeout
    """
    def action_check():
        return action.path.exists() == action.present

    # Sanity check.
    if action_check():
        return True

    class Handler(FileSystemEventHandler):
        def on_deleted(self, _event):
            if action_check():
                observer.stop()

        def on_created(self, _event):
            if action_check():
                observer.stop()

    observer = Observer()
    observer.schedule(Handler(), str(action.path.parent))

    def timeout_handler(_signum, _frame):
        observer.stop()

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout)

    observer.start()
    observer.join()

    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)

    return action_check()
