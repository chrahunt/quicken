import os
import threading

from contextlib import contextmanager, ExitStack
from pathlib import PosixPath
from functools import wraps
from typing import Any, ContextManager, Union

from quicken.lib._xdg import chdir


@contextmanager
def lock_guard(l: Union[threading.Lock, threading.RLock]):
    l.acquire()
    try:
        yield
    finally:
        l.release()

class BoundPath(PosixPath):
    _lock = threading.RLock()

    def __init__(self, *_, dir_fd: int):
        self._dir_fd = dir_fd
        super().__init__()

    def __getattribute__(self, name: str) -> Any:
        """Intercept and execute all functions in the context of the
        directory.
        """
        attr = super().__getattribute__(name)
        if callable(attr):
            @wraps(attr)
            def wrapper(*args, **kwargs):
                with ExitStack() as stack:
                    stack.enter_context(lock_guard(self._lock))
                    try:
                        stack.enter_context(chdir(self._dir_fd))
                    except AttributeError:
                        # Avoids issues during Path construction, before
                        # __init__ is called.
                        pass
                    return attr(*args, **kwargs)
            return wrapper
        return attr

    @property
    def dir(self):
        return self._dir_fd

    def pass_to(self, callback):
        return callback(self)


def get_bound_path(context, *args) -> BoundPath:
    """Execute action in directory so relative paths are resolved inside the
    directory without specific operations needing to support `dir_fd`.
    """
    result = BoundPath(*args, dir_fd=context.fileno())
    if result.is_absolute():
        raise ValueError('Provided argument must not be absolute')
    return result
