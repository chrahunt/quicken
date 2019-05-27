import logging
import signal
import sys

from collections import namedtuple
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .path import BoundPath, chdir


logger = logging.getLogger(__name__)


if not sys.platform.startswith('win'):
    import watchdog.observers.inotify_buffer
    # Fix for https://github.com/gorakhargosh/watchdog/issues/390
    watchdog.observers.inotify_buffer.InotifyBuffer.delay = 0


Action = namedtuple('Action', 'path present')


def wait_for_create(path: BoundPath, timeout: float = 5) -> bool:
    return _wait_for(Action(path, True), timeout)


def wait_for_delete(path: BoundPath, timeout: float = 5) -> bool:
    return _wait_for(Action(path, False), timeout)


def _wait_for(action: Action, timeout: float = 5) -> bool:
    """
    Args:
        action:
        timeout:
    """
    def action_check():
        return action.path.exists() == action.present

    class Handler(FileSystemEventHandler):
        def on_deleted(self, _event):
            logger.debug('on_deleted')
            if action_check():
                logger.debug('Stopping observer')
                observer.stop()

        def on_created(self, _event):
            logger.debug('on_created')
            if action_check():
                logger.debug('Stopping observer')
                observer.stop()

    # Start observer first, otherwise we may receive unwatched events between
    # check and observer start.
    if hasattr(action.path, 'dir'):
        parent = action.path.dir
    elif isinstance(action.path, Path):
        parent = action.path.parent

    with chdir(parent):
        observer = Observer(timeout=0)
        # This correctly uses cwd as long as we start before changing directory.
        observer.schedule(Handler(), '.')
        observer.start()

        # Sanity check.
        if action_check():
            observer.stop()
            observer.join()
            return True

    def timeout_handler(_signum, _frame):
        observer.stop()

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout)

    # Will be killed by timer or event handler.
    logger.debug('Joining observer')
    observer.join()
    logger.debug('Observer returned')

    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)

    return action_check()
