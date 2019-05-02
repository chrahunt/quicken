"""User-facing decorator.
"""
from __future__ import annotations

import sys

from functools import wraps

from ._typing import MYPY_CHECK_RUNNING
from .._timings import report

if MYPY_CHECK_RUNNING:
    from typing import Callable, Optional

    from ._types import JSONType, MainFunction, MainProvider


def quicken(
    name: str,
    # /,
    *,
    runtime_dir_path: Optional[str] = None,
    log_file: Optional[str] = None,
    server_idle_timeout: Optional[float] = None,
    bypass_server: Callable[[], bool] = None,
    reload_server: Callable[[JSONType, JSONType], bool] = None,
    user_data: JSONType = None,
):
    """Decorate a function that returns the main application entry point.

    To benefit most from the speedup, you must do required imports within
    the decorated function itself - leaving as few outside as possible.

    Doing any configuration processing outside the returned entry point function
    will lead to unexpected results! Only the entry point function is executed
    with the correct environment, cwd, argv, and other process attributes.

    Args:
        name: application name - must be unique per application, PyPI package
            name + command name is a good choice
        runtime_dir_path: directory used for application server state. If not
            provided then we fall back to:
            - `$XDG_RUNTIME_DIR/quicken-{name}`
            - `$TMPDIR/quicken-{name}-{uid}`
            - `/tmp/quicken-{name}-{uid}`
            If the directory exists it must be owned by the current (real) uid
            and have permissions 700.
        log_file: optional log file used by the server, must be an absolute
            path. If not provided the default is:
            - `$XDG_CACHE_HOME/quicken-{name}/server.log` or
            - `$HOME/.cache/quicken-{name}/server.log`.
        server_idle_timeout: time in seconds after which the server will shut
            down if no requests are received or being processed.
        bypass_server: if this function returns True then we run the entry point
            directly instead of trying to start or use an application server.
        reload_server: if this function returns True then we start a new server
            before running the entry point function - use it to check for
            updates for example. Receives the old and new user_data objects.
        user_data: JSON-serializable data provided to reload_server
    """
    def function_handler(main_provider: MainProvider) -> MainFunction:
        @wraps(main_provider)
        def wrapper() -> Optional[int]:
            if sys.platform.startswith('win'):
                return main_provider()()

            if bypass_server and bypass_server():
                return main_provider()()

            # Lazy import to avoid overhead.
            report('load quicken library')
            from ._lib import _server_runner_wrapper
            report('end load quicken library')
            return _server_runner_wrapper(
                name,
                main_provider,
                runtime_dir_path=runtime_dir_path,
                log_file=log_file,
                server_idle_timeout=server_idle_timeout,
                reload_server=reload_server,
                user_data=user_data,
            )

        return wrapper

    return function_handler