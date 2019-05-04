from __future__ import annotations

import os
import multiprocessing
import sys
import threading

from io import TextIOWrapper
from multiprocessing.reduction import recv_handle, register, send_handle

from ._imports import multiprocessing_connection
from ._signal import blocked_signals, signal_range
from ._typing import MYPY_CHECK_RUNNING
from ._xdg import chdir

if MYPY_CHECK_RUNNING:
    from typing import TextIO


def run_in_process(
        target, name=None, args=(), kwargs=None, allow_detach=False,
        timeout=None):
    """Run provided target in a multiprocessing.Process.

    This function does not require that the `target` and arguments
    are picklable. Only the return value of `target` must be.

    Args:
        target: same as multiprocessing.Process
        name: same as multiprocessing.Process
        args: same as multiprocessing.Process
        kwargs: same as multiprocessing.Process
        allow_detach: passes a callback as the first argument to the function
            that, when invoked, detaches from the parent by forking.
        timeout: seconds after which processing will be aborted and
            the child process killed

    Returns:
        The return value of `target`

    Raises:
        *: Any exception raised by `target`.
        TimeoutError: If a timeout occurs.
    """
    if not kwargs:
        kwargs = {}

    def launcher():
        # multiprocessing doesn't offer a good way to detach from the parent
        # process, allowing the child to exist without being cleaned up at
        # parent close. So given
        #
        # 1. parent process (which invoked run_in_process)
        # 2. runner process (executing target function)
        #
        # we fork (2), creating (3) then continue executing in (3) and forcibly
        # exit (2).
        #
        # The downside of this approach is that any exceptions from the
        # process after detaching will not be propagated to the caller
        # (and Windows incompatibility).
        def detach(result=None):
            # Indicate no exception.
            child_pipe.send(False)
            child_pipe.send(result)
            pid = os.fork()
            if pid:
                # Ensure we don't return to caller within the subprocess.
                os._exit(0)

        new_args = list(args)
        if allow_detach:
            new_args.insert(0, detach)
        try:
            result = target(*new_args, **kwargs)
        except:
            child_pipe.send(True)
            from tblib import pickling_support
            pickling_support.install()
            child_pipe.send(sys.exc_info())
            # Wait for signal from parent process to avoid exit/read race
            # condition.
            child_pipe.recv()
            # We don't really want the exception traced by multiprocessing
            # so exit like Python would.
            sys.exit(1)
        else:
            child_pipe.send(False)
            child_pipe.send(result)
            child_pipe.recv()

    ctx = multiprocessing.get_context('fork')

    child_pipe, parent_pipe = ctx.Pipe()
    p = ctx.Process(target=launcher, name=name)
    p.start()

    ready = multiprocessing_connection.wait([p.sentinel, parent_pipe], timeout=timeout)

    # Timeout
    if not ready:
        p.kill()
        raise TimeoutError('Timeout running function.')

    exc = None
    result = None
    if parent_pipe in ready:
        error = parent_pipe.recv()
        if error:
            from tblib import pickling_support
            pickling_support.install()
            _, exception, tb = parent_pipe.recv()
            exc = exception.with_traceback(tb)
        else:
            result = parent_pipe.recv()

    if p.sentinel in ready:
        # This can happen if the child process closes file descriptors, but we
        # do not handle it.
        assert p.exitcode is not None, 'Exit code must exist'
        if p.exitcode:
            if not exc:
                exc = RuntimeError(
                    f'Process died with return code {p.exitcode}')

    else:
        # Indicate OK to continue.
        parent_pipe.send(True)
        p.join()

    if exc:
        raise exc
    return result


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
