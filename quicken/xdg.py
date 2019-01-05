from contextlib import contextmanager, ExitStack
from functools import wraps
import os
from pathlib import Path, PosixPath
import stat
import threading
from typing import Any, ContextManager, Union


@contextmanager
def chdir(fd: int) -> ContextManager:
    cwd = os.open('.', os.O_RDONLY)
    # os.chdir is equivalent but PyCharm complains about invalid argument type.
    os.fchdir(fd)
    try:
        yield
    finally:
        os.fchdir(cwd)
        os.close(cwd)


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


class RuntimeDir:
    """Helper class to create/manage the application runtime directory.

    If a dir_path is not provided then attempts to find a suitable path:

    - $XDG_RUNTIME_DIR/{base_name}
    - $TMPDIR/{base_name}-{uid}
    - /tmp/{base_name}-{uid}

    otherwise `__init__` fails.

    The emphasis here is on securely creating the directory and ensuring its
    attributes, as well as providing an interface for operating on files in
    the directory without race conditions.

    If `os.*` unconditionally supported `dir_fd` we would suggest using that,
    but since this is not available on all platforms we instead use BoundPath,
    which does chdir to the directory before providing arguments as a relative
    path.
    """
    def __init__(self, base_name: str = None, dir_path=None):
        """
        Args:
            base_name: the name to use for the runtime directory within the
                temporary file location.
            dir_path: when provided, overrides the default temporary directory
                creation behavior.
        """
        if dir_path is None:
            if base_name is None:
                raise ValueError(
                    'At least one of `base_name` or `dir_path` must be'
                    ' provided.')
            dir_path = self._get_runtime_dir(base_name)
        self._path = dir_path
        # Open first.
        try:
            self._fd = os.open(dir_path, os.O_RDONLY)
        except FileNotFoundError:
            Path(dir_path).mkdir(mode=0o700)
            self._fd = os.open(dir_path, os.O_RDONLY)
        # Test after open to avoid toctou, also since we do not trust the mode
        # passed to mkdir.
        result = os.stat(self._fd)
        if not stat.S_ISDIR(result.st_mode):
            raise RuntimeError(f'{dir_path} must be a directory')
        if result.st_uid != os.getuid():
            raise RuntimeError(f'{dir_path} must be owned by the current user')
        user_rwx = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        if stat.S_IMODE(result.st_mode) != user_rwx:
            raise RuntimeError(f'{dir_path} must have permissions 700')
        # At this point the directory referred to by self._fd is:
        # * owned by the user
        # * has permission 700
        # unless explicitly changed by the user or root.

    def fileno(self) -> int:
        return self._fd

    def path(self, *args) -> BoundPath:
        """Execute action in directory so relative paths are resolved inside the
        directory without specific operations needing to support `dir_fd`.
        """
        result = BoundPath(*args, dir_fd=self._fd)
        if result.is_absolute():
            raise ValueError('Provided argument must not be absolute')
        return result

    @staticmethod
    def _get_runtime_dir(base_name):
        try:
            return f"{os.environ['XDG_RUNTIME_DIR']}/{base_name}"
        except KeyError:
            uid = os.getuid()
            base_name = f'{base_name}-{uid}'
            try:
                return f"{os.environ['TMPDIR']}/{base_name}"
            except KeyError:
                return f'/tmp/{base_name}'

    def __str__(self):
        return self._path
