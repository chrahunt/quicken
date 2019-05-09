"""Helpers for "console_scripts"/"script" interceptors.
"""
from __future__ import annotations

import json
import os
import stat
import sys

try:
    # Faster to import than hashlib if _sha512 is present. See e.g. python/cpython#12742
    from _sha512 import sha512 as _sha512
except ImportError:
    from hashlib import sha512 as _sha512

from .lib._typing import MYPY_CHECK_RUNNING


if MYPY_CHECK_RUNNING:
    from typing import List


def get_script_details(module: str, func: str):
    """
    Returns:
        digest, reload_criteria
    """
    main = sys.modules['__main__'].__file__
    bin_dir = os.path.dirname(main)
    key = {
        'dir': bin_dir,
        'module': module,
        'func': func,
    }
    text = json.dumps(key, sort_keys=True, separators=(',', ':'))
    digest = _sha512(text.encode('utf-8'))
    result = os.stat(main)
    key['mtime'] = result[stat.ST_MTIME]
    key['ctime'] = result[stat.ST_CTIME]
    return digest, key


def get_nested_attr(o, parts):
    for name in parts:
        o = getattr(o, name)
    return o


def get_attribute_accumulator(callback, context=None):
    """Who knows what someone may put in their entry point spec.

    We try to take the most flexible approach here and accept as much as
    possible.

    Args:
        callback: called when the accumulator is called with the gathered
            names as the first argument.
        context: names that should have explicit returned values.
    """
    # Use variable in closure to reduce chance of conflicting name.
    parts = []

    # As part of normal module processing, Python import machinery may query
    # attributes of our "module". The set of attributes may change over time but
    # will probably always be dunders. For that reason we will only recognize
    # a few dunder attributes as valid identifiers for user code.
    ALLOWED_DUNDERS = ['__init__', '__main__']

    def is_dunder(name):
        return name.startswith('__') and name.endswith('__') and 4 < len(name)

    class Accumulator:
        def __getattribute__(self, name):
            if name == '__call__':
                return object.__getattribute__(self, name)

            if context:
                try:
                    return context[name]
                except KeyError:
                    pass

            if is_dunder(name) and name not in ALLOWED_DUNDERS:
                raise AttributeError(name)

            parts.append(name)
            return self

        def __call__(self, *args, **kwargs):
            nonlocal parts
            current_parts = parts
            parts = []
            return callback(current_parts, *args, **kwargs)

    return Accumulator()


class ScriptHelper:
    def __init__(self, parts: List[str]):
        module_parts, self.function_parts = parse_script_spec(parts)
        self.module_name = '.'.join(module_parts)
        self.function_name = '.'.join(self.function_parts)
        self.digest, self.metadata = get_script_details(
            self.module_name, self.function_name
        )
        self.name = f'quicken.entrypoint.{self.digest}'

    def get_func(self):
        import importlib
        module = importlib.import_module(self.module_name)
        return get_nested_attr(module, self.function_parts)


def parse_script_spec(parts):
    """
    Given ['hello', '_', 'world']
    Returns ('hello',), ('world',)
    Returns:
        (module_parts, function_parts)
    """
    try:
        i = parts.index('_')
    except ValueError:
        return parts, []
    else:
        return parts[:i], parts[i+1:]


def wrapper_script(callback):
    """Wraps a callback meant to be a wrapper
    around a user-provided script.
    """
    def inner(parts):
        helper = ScriptHelper(parts)
        return callback(helper)

    # Required
    module_context = {
        '__path__': '',
    }
    return get_attribute_accumulator(inner)
