import errno
import os

from ptrace.binding import (
    PTRACE_EVENT_CLONE,
    PTRACE_EVENT_EXEC,
    PTRACE_EVENT_FORK,
    PTRACE_EVENT_VFORK,
    ptrace_attach,
    ptrace_cont,
    ptrace_detach,
    ptrace_geteventmsg,
    ptrace_getsiginfo,
    ptrace_setoptions,
)

from ptrace.binding.func import (
    PTRACE_O_TRACECLONE,
    PTRACE_O_TRACEEXEC,
    PTRACE_O_TRACEFORK,
    PTRACE_O_TRACEVFORK,
)

PTRACE_O_EXITKILL = 0x00100000


class Process:
    def __init__(self, pid):
        self.pid = pid

    def attach(self):
        return ptrace_attach(self.pid)

    def cont(self, signum=0):
        return ptrace_cont(self.pid, signum)

    def detach(self, signum=0):
        return ptrace_detach(self.pid, signum)

    def geteventmsg(self):
        return ptrace_geteventmsg(self.pid)

    def getsiginfo(self):
        return ptrace_getsiginfo(self.pid)

    def setoptions(self, options):
        ptrace_setoptions(self.pid, options)

    def consume_status(self, status):
        """Update internal state based on provided status.
        """
        if os.WIFSTOPPED(status):
            try:
                self.getsiginfo()
            except OSError as e:
                if e.errno == errno.EINVAL:
                    # group-stop
                    ...
                else:
                    # idk
                    ...
            else:
                # signal-delivery-stop
                ...
