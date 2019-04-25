"""Signal helpers.
"""
import errno
import logging
import os
import signal
import sys

from contextlib import contextmanager
from typing import Set


logger = logging.getLogger(__name__)


def _settable_signal(sig) -> bool:
    """Check whether provided signal may be set.
    """
    try:
        old = signal.signal(sig, lambda _num, _frame: ...)
    except OSError as e:
        # POSIX response
        assert e.errno == errno.EINVAL
        return False
    except ValueError as e:
        # Windows response
        assert e.args[0] == 'invalid signal value'
        return False
    else:
        signal.signal(sig, old)
        return True


signal_range = set(range(1, signal.NSIG))
# XXX: Can be signal.valid_signals() in 3.8+
settable_signals = set(filter(_settable_signal, signal_range))

if not sys.platform.startswith('win'):
    forwarded_signals = settable_signals - {
        # We skip SIGCHLD because it interferes with tests that use multiprocessing.
        # We do not expect the client to receive the signal in any case anyway.
        signal.SIGCHLD,
        signal.SIGCLD,
    }
else:
    forwarded_signals = settable_signals


@contextmanager
def blocked_signals(signals: Set[signal.Signals]):
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def pthread_getsigmask():
    return signal.pthread_sigmask(signal.SIG_BLOCK, [])


class SignalProxy:
    """Implements behavior for proxying signals to another process:

    1. All signals are sent to the target process except SIGCHLD, SIGT*
    2. If SIGT* is received then we forward it and stop the current process.
    """
    def set_target(self, pid):
        self._pid = pid
        self._install_handler()

    def _install_handler(self):
        for sig in forwarded_signals:
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, num, _frame):
        # Be sure all functions called in this method are re-entrant.
        os.kill(self._pid, num)
        # The SIGT* functions are handled differently, stopping the current
        # process and the handler process if received.
        # * SIGTSTP is received usually in response to C-z on the terminal
        # * SIGTTIN is received when reading from stdin after being backgrounded
        # We want to behave 'as' the target process as much as possible, so when
        # one of these is received we stop ourselves so it will look natural in
        # a shell context if e.g. C-z is pressed.
        # XXX: signal.SIGTTOU is omitted since it is only conditional and we
        #  don't check the condition.
        # XXX: For consistent experience, user CLI applications must stop when
        #  receiving these signals - we may be able to relax this restriction
        #  later if we can determine whether the remote process was stopped.
        if num in [signal.SIGTSTP, signal.SIGTTIN]:
            os.kill(os.getpid(), signal.SIGSTOP)
