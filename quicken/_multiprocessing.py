"""Transferring files over connections.
"""
from multiprocessing.reduction import DupFd, register
from io import TextIOWrapper
from typing import TextIO


def reduce_textio(obj: TextIO):
    # Picklable object that contains a callback id to be used by the
    # receiving process.
    if obj.readable() == obj.writable():
        raise ValueError(
            'TextIO object must be either readable or writable, but not both.')
    df = DupFd(obj.fileno())
    return rebuild_textio, (df, obj.readable(), obj.writable())


def rebuild_textio(df: DupFd, readable: bool, _writable: bool) -> TextIO:
    fd = df.detach()
    flags = 'r' if readable else 'w'
    return open(fd, flags)


register(TextIOWrapper, reduce_textio)
