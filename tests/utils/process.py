import sys


__all__ = [
    "active_children",
    "contained_children",
    "disable_child_tracking",
    "kill_children",
]


if not sys.platform.startswith("win"):
    from ._process_nonwin import *
else:
    from ._process_win import *
