from __future__ import annotations

# Top-level package should be free from any unnecessary imports, since they are
# unconditional.
__version__ = '0.1.0'

is_importing: bool = False
"""Whether Quicken is currently doing imports and the app would benefit
from not doing deferred loads.
"""


def set_importing(v: bool):
    global is_importing
    is_importing = v
