"""Some packages are a little overzealous with package-level imports.

We don't need all functionality they offer and can patch them out to get speed
ups.
"""
import sys

from contextlib import contextmanager
from types import ModuleType


@contextmanager
def patch_modules(modules=None, packages=None):
    """Within the scope, patch the provided modules so dummy values are imported
    instead.
    """
    if modules is None:
        modules = []
    if packages is None:
        packages = []

    current_modules = set(sys.modules.keys())
    for name in modules:
        if name not in current_modules:
            sys.modules[name] = ModuleType(name)

    for name in packages:
        if name not in current_modules:
            package = ModuleType(name)
            # Required if importing module from within package.
            package.__path__ = None
            sys.modules[name] = package

    try:
        yield
    finally:
        new_modules = set(sys.modules.keys()) - current_modules
        # Prevent possibly half-imported modules from impacting other users.
        for name in new_modules:
            sys.modules.pop(name)
