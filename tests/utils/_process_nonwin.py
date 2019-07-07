"""Utilities for managing child processes within a scope - this ensures
tests run cleanly even on failure and also gives us a mechanism to
get debug info for our children.
"""
import logging
import os
import sys

from contextlib import contextmanager
from typing import ContextManager, List

import psutil
import process_tracker


process_tracker.install()


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


__all__ = [
    "active_children",
    "contained_children",
    "disable_child_tracking",
    "kill_children",
]


def _get_create_time(create_time):
    """Given basic process create time, return one that would
    match psutil.
    """
    boot_time = psutil.boot_time()
    clock_ticks = os.sysconf("SC_CLK_TCK")
    return boot_time + (create_time / clock_ticks)


def active_children() -> List[psutil.Process]:
    """Returns the active child processes.
    """
    out = []
    children = process_tracker.children()
    for pid, create_time in children:
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            continue
        else:
            if process.create_time() == _get_create_time(create_time):
                out.append(process)

    return out


@contextmanager
def contained_children(timeout=1, assert_graceful=True) -> ContextManager:
    """Automatically kill any Python processes forked in this context, for
    cleanup. Handles any descendants.

    Timeout is seconds to wait for graceful termination before killing children.
    """
    try:
        # TODO: What to yield here?
        yield
    finally:
        alive = kill_children(timeout)
        num_alive = len(alive)
        # Get current exception - if something was raised we should be raising
        # that.
        # XXX: Need to check use cases to see if there are any cases where
        #  we are expecting an exception outside of the 'contained_children'
        #  block.
        _, exc, _ = sys.exc_info()
        if assert_graceful and exc is None:
            assert not num_alive, f"Unexpected children still alive: {alive}"


def disable_child_tracking():
    # TODO: Actually needed?
    pids = [p.pid for p in active_children()]
    return pids


def kill_children(timeout=1) -> List[psutil.Process]:
    """
    Kill any active children, returning any that were not terminated within
    timeout.
    Args:
        timeout: time to wait before killing.

    Returns:
        list of processes that had to be killed forcefully.
    """
    procs = active_children()
    for p in procs:
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        logger.warning("Cleaning up child: %d", p.pid)
        p.kill()
    return alive
