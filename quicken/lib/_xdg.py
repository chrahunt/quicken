from __future__ import annotations

import os
import stat

from contextlib import contextmanager

from ._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import ContextManager


@contextmanager
def chdir(fd) -> ContextManager:
    """
    Args:
        fd: anything suitable for passing to os.chdir(), or something with a
            `fileno` member.
    """
    cwd = os.open('.', os.O_RDONLY)
    if hasattr(fd, 'fileno'):
        fd = fd.fileno()
    os.chdir(fd)
    try:
        yield
    finally:
        os.fchdir(cwd)
        os.close(cwd)


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
            dir_path = runtime_dir(base_name)
        self._path = dir_path
        # Open first.
        try:
            self._fd = os.open(dir_path, os.O_RDONLY)
        except FileNotFoundError:
            os.mkdir(dir_path, mode=0o700)
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

    def __str__(self):
        return self._path


def runtime_dir(base_name):
    try:
        return f"{os.environ['XDG_RUNTIME_DIR']}/{base_name}"
    except KeyError:
        uid = os.getuid()
        base_name = f'{base_name}-{uid}'
        try:
            return f"{os.environ['TMPDIR']}/{base_name}"
        except KeyError:
            return f'/tmp/{base_name}'


def cache_dir(base_name):
    try:
        return os.path.join(os.environ['XDG_CACHE_HOME'], base_name)
    except KeyError:
        return os.path.join(os.environ['HOME'], '.cache', base_name)
