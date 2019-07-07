"""Consistent output for timing information.
"""
import logging
import time


logger = logging.getLogger(__name__)


def report(name):
    """Used for reporting timing information.
    """
    logger.debug("%f: %s", time.perf_counter(), name)
