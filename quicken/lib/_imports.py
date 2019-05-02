"""Patched imports, for improving startup speed.

Where we identify that a dependency has imported some heavy module but doesn't
use it, we can provide that dependency here but with any imports stubbed out.
"""
from ._typing import MYPY_CHECK_RUNNING

# IDE helper
if MYPY_CHECK_RUNNING:
    import asyncio

    import fasteners

    import multiprocessing

cache = {}


def __getattr__(name):
    ...
