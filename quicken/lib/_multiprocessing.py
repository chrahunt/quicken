from __future__ import annotations

import os
import multiprocessing
import sys

from ._imports import multiprocessing_connection


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
