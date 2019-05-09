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

from ._scripts import wrapper_script


__all__ = []


def callback(helper):
    from .lib import quicken

    log_file = os.environ.get('QUICKEN_LOG')
    # 1 day
    DEFAULT_IDLE_TIMEOUT = 86400
    idle_timeout = float(
        os.environ.get('QUICKEN_IDLE_TIMEOUT', DEFAULT_IDLE_TIMEOUT)
    )

    wrapper = quicken(
        helper.name,
        log_file=log_file,
        reload_server=operator.ne,
        user_data=helper.metadata,
        server_idle_timeout=idle_timeout,
    )

    return wrapper(helper.get_func)()


sys.modules[__name__] = wrapper_script(callback)
