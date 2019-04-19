import sys


__all__ = ['contained_children', 'kill_children']


if not sys.platform.startswith('win'):
    from ._process_nonwin import *
else:
    from ._process_win import *
