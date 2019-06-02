"""Functions for translating between low-level structures
for passing over multiprocessing Connections.
"""
from __future__ import annotations

import os
import pickle
import threading

from io import BytesIO, FileIO, TextIOWrapper
from multiprocessing.reduction import recv_handle, send_handle

from ._imports import multiprocessing_connection
from ._signal import blocked_signals, signal_range
from ._typing import MYPY_CHECK_RUNNING
from ._xdg import chdir

if MYPY_CHECK_RUNNING:
    from typing import TextIO


class Pickler(pickle.Pickler):
    """Custom pickler class to avoid changing the global multiprocessing.reduction
    pickler.
    """
    _dispatch_table = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatch_table = self._dispatch_table

    @classmethod
    def dumps(cls, obj):
        buf = BytesIO()
        cls(buf).dump(obj)
        buf.seek(0)
        return buf.read()

    @classmethod
    def register(cls, cls2, reduce):
        cls._dispatch_table[cls2] = reduce


def dumps(obj):
    return Pickler.dumps(obj)


loads = pickle.loads


def register(cls, reduce):
    return Pickler.register(cls, reduce)


_fd_sharing_base_path_fd = None


def set_fd_sharing_base_path_fd(fd: int):
    global _fd_sharing_base_path_fd
    _fd_sharing_base_path_fd = fd


class Fd:
    """Tag for reduce_fd.
    """
    def __init__(self, fd):
        self.fd = fd


def reduce_fd(obj: Fd):
    """Simpler version of multiprocessing.resource_sharer._ResourceSharer
    that:

    * doesn't require authkey (but does require base path to be set which should
      be in a secure folder)
    * doesn't require random unix socket (so when we import multiprocessing.connection
      we can stub out tempfile, saving 5ms).
    """
    fd = obj.fd
    name = f'{os.getpid()}-{fd}'

    with chdir(_fd_sharing_base_path_fd):
        # In case a client crashed and we're re-using the pid.
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        path = os.path.abspath(name)
        listener = multiprocessing_connection.Listener(address=name)

    def target():
        conn = listener.accept()
        with chdir(_fd_sharing_base_path_fd):
            listener.close()
        pid = conn.recv()
        send_handle(conn, fd, pid)
        conn.close()

    t = threading.Thread(target=target)
    t.daemon = True
    # Blocking signals for any utility threads gives the main app the final say
    # w.r.t. signal disposition.
    with blocked_signals(signal_range):
        t.start()

    return rebuild_fd, (path,)


def rebuild_fd(path: str) -> Fd:
    # UNIX socket path name is limited to 108 characters, so cd to the directory
    # and refer to it as a relative path.
    with chdir(os.path.dirname(path)):
        with multiprocessing_connection.Client(os.path.basename(path)) as c:
            c.send(os.getpid())
            return Fd(recv_handle(c))


register(Fd, reduce_fd)


def reduce_textio(obj: TextIO):
    if obj.readable() == obj.writable():
        raise ValueError(
            'TextIO object must be either readable or writable, but not both.')
    fd = Fd(obj.fileno())
    return rebuild_textio, (fd, obj.readable(), obj.writable(), obj.encoding)


def rebuild_textio(fd: Fd, readable: bool, _writable: bool, encoding: str) -> TextIO:
    flags = 'r' if readable else 'w'
    return open(fd.fd, flags, encoding=encoding)


register(TextIOWrapper, reduce_textio)


def reduce_fileio(obj: FileIO):
    # Needed for pytest, which sets stdout and stderr to objects that contain
    # unbuffered temporary files.
    # File descriptors duplicated across processes preserve offset and other
    # properties so they should remain synchronized.
    fd = Fd(obj.fileno())
    mode = obj.mode
    return rebuild_fileio, (fd, mode)


def rebuild_fileio(fd: Fd, mode: str):
    # open returns a FileIO object when binary + buffering=0
    if 'b' not in mode:
        mode += 'b'
    # Opening an existing file descriptor with 'w' does not truncate the file.
    return open(fd.fd, mode, buffering=0)


register(FileIO, reduce_fileio)
