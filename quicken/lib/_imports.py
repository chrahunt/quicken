"""Patched imports, for improving startup speed.

Where we identify that a dependency has imported some heavy module but doesn't
use it, we can provide that dependency here but with any imports stubbed out.
"""
from __future__ import annotations

import sys

from ._import import patch_modules
from ._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    import asyncio
    import multiprocessing.connection as multiprocessing_connection

    from typing import Type

    from fasteners import InterProcessLock


class Modules:
    def __init__(self):
        self.__name__ = __name__
        self.__file__ = __file__

    @property
    def asyncio(self) -> asyncio:
        # Saves up to 5ms, and we don't use tls.
        with patch_modules(modules=['ssl']):
            import asyncio
        return asyncio

    @property
    def InterProcessLock(self) -> Type[InterProcessLock]:
        # Saves 2ms since we don't use the decorators.
        # We should probably just write our own at this point.
        with patch_modules(modules=['six']):
            from fasteners import InterProcessLock
        return InterProcessLock

    @property
    def multiprocessing_connection(self) -> Type[multiprocessing_connection]:
        # Saves 2ms since we don't use randomly-created sockets (tempfile, shutil)
        # Pre-load multiprocessing.reduction so the same shared pickler state is
        # available everywhere.
        import multiprocessing.reduction
        with patch_modules(modules=['tempfile']):
            import multiprocessing.connection
        return multiprocessing.connection


sys.modules[__name__] = Modules()
