from __future__ import annotations
import copy
from dataclasses import dataclass
import io
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

# Registers TextIOWrapper handler.
from . import _multiprocessing


class RequestTypes:
    get_server_state = 'get_server_state'
    run_process = 'run_process'


@dataclass
class Request:
    name: str
    contents: Any


@dataclass
class Response:
    contents: Any


@dataclass
class StdStreams:
    stdin: io.TextIOWrapper
    stdout: io.TextIOWrapper
    stderr: io.TextIOWrapper


@dataclass
class ProcessState:
    std_streams: StdStreams
    cwd: Path
    umask: int
    environment: Dict[str, str]
    argv: List[str]

    @staticmethod
    def for_current_process() -> ProcessState:
        streams = StdStreams(sys.stdin, sys.stdout, sys.stderr)
        cwd = Path.cwd()
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
        os.chdir(str(state.cwd))
        os.umask(state.umask)
        os.environ = copy.deepcopy(state.environment)
        sys.argv = list(state.argv)


@dataclass
class ServerState:
    start_time: float
    pid: int
    context: Dict[str, str]
