from __future__ import annotations

import copy
import io
import logging
import os
import sys

# Registers TextIOWrapper handler.
from . import _multiprocessing
from ._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import Any, Dict, List


class RequestTypes:
    run_process = 'run_process'
    wait_process_done = 'wait_process_done'


class Request:
    def __init__(self, name: str, contents: Any):
        self.name = name
        self.contents = contents


class Response:
    def __init__(self, contents: Any):
        self.contents = contents


class StdStreams:
    def __init__(
        self,
        stdin: io.TextIOWrapper,
        stdout: io.TextIOWrapper,
        stderr: io.TextIOWrapper,
    ):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr


class ProcessState:
    def __init__(
        self,
        std_streams: StdStreams,
        cwd: str,
        umask: int,
        environment: Dict[str, str],
        argv: List[str],
    ):
        self.std_streams = std_streams
        self.cwd = cwd
        self.umask = umask
        self.environment = environment
        self.argv = argv

    @staticmethod
    def for_current_process() -> ProcessState:
        streams = StdStreams(sys.stdin, sys.stdout, sys.stderr)
        cwd = os.getcwd()
        # Only way to get umask is to set umask.
        umask = os.umask(0o077)
        os.umask(umask)
        environ = dict(os.environ)
        argv = list(sys.argv)
        return ProcessState(streams, cwd, umask, environ, argv)

    @staticmethod
    def _reset_loggers(stdout, stderr):
        def reset_handlers(logger):
            for h in logger.handlers:
                if isinstance(h, logging.StreamHandler):
                    # Check if using stdout/stderr in underlying stream and call
                    # setStream if so.
                    # XXX: Use setStream in Python 3.7
                    if h.stream == sys.stdout:
                        h.stream = stdout
                    elif h.stream == sys.stderr:
                        h.stream = stderr
        # For 'manager' property.
        # noinspection PyUnresolvedReferences
        loggers = logging.Logger.manager.loggerDict
        for _, item in loggers.items():
            if isinstance(item, logging.PlaceHolder):
                # These don't have their own handlers.
                continue
            reset_handlers(item)
        reset_handlers(logging.getLogger())

    @staticmethod
    def apply_to_current_process(state: ProcessState):
        streams = state.std_streams
        __class__._reset_loggers(streams.stdout, streams.stderr)
        sys.stdin, sys.stdout, sys.stderr = \
            streams.stdin, streams.stdout, streams.stderr
        os.chdir(str(state.cwd))
        os.umask(state.umask)
        os.environ = copy.deepcopy(state.environment)
        sys.argv = list(state.argv)
