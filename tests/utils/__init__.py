import copy
import errno
import logging
import os
import signal
import string
import sys
import tempfile
import textwrap

from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, List, TextIO, Tuple, Union

from quicken.lib._signal import settable_signals


logger = logging.getLogger(__name__)


@contextmanager
def chdir(path: Union[Path, str]) -> ContextManager:
    current_path = Path.cwd()
    try:
        os.chdir(str(path))
        yield
    finally:
        os.chdir(str(current_path))


@contextmanager
def isolated_filesystem() -> ContextManager[Path]:
    with tempfile.TemporaryDirectory() as d:
        with chdir(d):
            yield Path(d)


@contextmanager
def env(**kwargs) -> ContextManager:
    """Update environment only within context manager.

    Args:
        kwargs: Key-value pairs corresponding to environment variables to set in
            the with block. If an argument is set to `None` then the environment
            variable is removed. Only the provided environment variables are
            changed, any changes made to other environment variables in the with
            block are not undone.
    """
    def update(target, source):
        updated = {}
        for k, v in source.items():
            if v is None:
                try:
                    updated[k] = target.pop(k)
                except KeyError:
                    pass
            else:
                updated[k] = target.get(k, None)
                target[k] = v
        return updated

    previous_env = update(os.environ, kwargs)
    try:
        yield
    finally:
        update(os.environ, previous_env)


@contextmanager
def argv(args: List[str]) -> ContextManager:
    """Set argv within the context.
    """
    argv = sys.argv
    sys.argv = args
    try:
        yield
    finally:
        sys.argv = argv


@contextmanager
def umask(umask: int) -> ContextManager[None]:
    """Set umask within the context.
    """
    umask = os.umask(umask)
    try:
        yield
    finally:
        os.umask(umask)


@contextmanager
def preserved_signals() -> ContextManager[None]:
    handlers = [(s, signal.getsignal(s)) for s in settable_signals]
    try:
        yield
    finally:
        for sig, handler in handlers:
            try:
                signal.signal(sig, handler)
            except TypeError:
                # Can happen if handler is None (seen with signal 32, 33)
                pass
            except OSError as e:
                # SIGKILL/QUIT cannot be set, so just ignore.
                if e.errno != errno.EINVAL:
                    raise


@contextmanager
def captured_std_streams() -> ContextManager[Tuple[TextIO, TextIO, TextIO]]:
    """Capture standard streams and provide an interface for interacting with
    them.

    Be careful with returned file objects - if stdout/stderr are read before the
    block has exited it could block. Also if any file descriptors are left open.
    """
    stdin_r, stdin_w = os.pipe()
    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    stdin_old, stdout_old, stderr_old = \
        sys.stdin, sys.stdout, sys.stderr

    # We close the files explicitly at the end of ths scope.
    sys.stdin = os.fdopen(stdin_r, closefd=False)
    sys.stdout = os.fdopen(stdout_w, 'w', closefd=False)
    sys.stderr = os.fdopen(stderr_w, 'w', closefd=False)
    try:
        yield os.fdopen(stdin_w, 'w'), os.fdopen(stdout_r), os.fdopen(stderr_r)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.close(stdin_r)
        os.close(stdout_w)
        os.close(stderr_w)
        sys.stdin, sys.stdout, sys.stderr = \
            stdin_old, stdout_old, stderr_old


@contextmanager
def kept(o, attr):
    current_attr = copy.copy(getattr(o, attr))
    try:
        yield
    finally:
        setattr(o, attr, current_attr)


def write_text(path: Path, text: str, **params):
    new_text = string.Template(textwrap.dedent(text)).substitute(params)
    path.write_text(new_text)


@contextmanager
def local_module():
    with isolated_filesystem() as path:
        with kept(sys, 'path'):
            sys.path.append(str(path))
            with kept(sys, 'modules'):
                yield
