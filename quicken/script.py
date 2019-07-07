"""Entrypoint wrapper that starts a quicken server around the provided
command, passing through all arguments.

The wrapper calculates a digest with:

1. parent directory of __main__.__file__
2. module + func
3.

This wrapper supports the following use cases:

1. wrapped module referred to by a single utility
2. different versions in different virtual environments
3. single named utility
"""
import operator
import os
import sys

from ._internal.constants import DEFAULT_IDLE_TIMEOUT, ENV_IDLE_TIMEOUT
from ._internal.entrypoints import ConsoleScriptHelper, console_script


# No public API.
__all__ = []


def callback(helper: ConsoleScriptHelper):
    from ._internal.decorator import quicken

    idle_timeout = float(os.environ.get(ENV_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT))

    wrapper = quicken(
        helper.name,
        reload_server=operator.ne,
        user_data=helper.metadata,
        server_idle_timeout=idle_timeout,
    )

    return wrapper(helper.get_func)()


# Overwrite our module so we can use __getattribute__ to intercept the
# user-library-provided module spec.
sys.modules[__name__] = console_script(callback)
