"""Some packages are a little overzealous with package-level imports.

We don't need all functionality they offer and can patch them out to get speed
ups.
"""
import sys

from collections import ChainMap
from contextlib import contextmanager
from types import ModuleType


@contextmanager
def patch_modules(*names):
    """Within the scope, patch the provided modules so dummy values are imported
    instead.
    """
    sys_modules = sys.modules
    # Intercept any module creation so we don't infect other users.
    fake_modules = {}
    sys.modules = ChainMap(fake_modules, sys_modules)
    for name in names:
        if name not in sys_modules:
            sys.modules[name] = ModuleType(name)

    try:
        yield
    finally:
        sys.modules = sys_modules
