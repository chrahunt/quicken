from __future__ import annotations

import os
import threading

from io import TextIOWrapper
from multiprocessing.reduction import recv_handle, register, send_handle

from ._imports import multiprocessing_connection
from ._signal import blocked_signals, signal_range
from ._typing import MYPY_CHECK_RUNNING
from ._xdg import chdir

if MYPY_CHECK_RUNNING:
    from typing import TextIO


_fd_sharing_base_path_fd = None


def set_fd_sharing_base_path_fd(fd: int):
    global _fd_sharing_base_path_fd
    _fd_sharing_base_path_fd = fd


def reduce_textio(obj: TextIO):
    """Simpler version of multiprocessing.resource_sharer._ResourceSharer
    that:

    * doesn't require authkey (but does require base path to be set which should
      be in a secure folder
    * doesn't require random unix socket (so when we import multiprocessing.connection
      we can stub out tempfile, saving 5ms).
    """
    # Picklable object that contains a callback id to be used by the
    # receiving process.
    if obj.readable() == obj.writable():
        raise ValueError(
            'TextIO object must be either readable or writable, but not both.')
    fd = obj.fileno()
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
    with blocked_signals(signal_range):
        # Inherits blocked signals.
        t.start()

    return rebuild_textio, (path, obj.readable(), obj.writable())


def rebuild_textio(path: str, readable: bool, _writable: bool) -> TextIO:
    # UNIX socket path name is limited to 108 characters, so cd to the directory
    # and refer to it as a relative path.
    with chdir(os.path.dirname(path)):
        with multiprocessing_connection.Client(os.path.basename(path)) as c:
            c.send(os.getpid())
            fd = recv_handle(c)
    flags = 'r' if readable else 'w'
    return open(fd, flags)


register(TextIOWrapper, reduce_textio)
