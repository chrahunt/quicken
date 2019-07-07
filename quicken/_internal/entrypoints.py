"""Helpers for "console_scripts"/"script" interceptors.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys

from quicken._internal._imports import sha512
from quicken._internal._logging import default_configuration
from quicken._internal._typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    from typing import Any, Callable, Dict, List, Tuple, Union


logger = logging.getLogger(__name__)


def getattr_nested(o: Any, parts: List[str]) -> Any:
    for name in parts:
        o = getattr(o, name)
    return o


class Name:
    """Dotted Python name, available as a string or list.
    """

    def __init__(self, parts: Union[List[str], str] = None):
        if parts is None:
            parts = []
        if isinstance(parts, str):
            self.name = parts
            self.parts = parts.split(".")
        elif isinstance(parts, list):
            self.name = ".".join(parts)
            self.parts = parts
        else:
            raise TypeError("parts must be a list or string")

    def __len__(self) -> int:
        return len(self.name)


def get_function(module_name: Name, function_name: Name):
    """Given a module and function name, retrieve the function.
    """
    import importlib

    module = importlib.import_module(module_name.name)
    return getattr_nested(module, function_name.parts)


def parse_entrypoint(spec: str) -> Tuple[Name, Name]:
    """Given a string like 'module.name:function.name', parse it into
    separate parts.
    """
    try:
        module_part, function_part = spec.split(":", maxsplit=1)
    except ValueError:
        return Name(spec), Name()
    else:
        return Name(module_part), Name(function_part)


class MainDetails:
    mtime: int
    ctime: int
    path: str


def get_main_details() -> MainDetails:
    """Get details for script referenced by __main__.
    """
    result = MainDetails()
    result.path = sys.modules["__main__"].__file__
    stat_result = os.stat(result.path)
    result.mtime = stat_result[stat.ST_MTIME]
    result.ctime = stat_result[stat.ST_CTIME]
    return result


def get_server_key(key: Dict[str, Any]) -> str:
    """
    Args:
        key: the details that determine which server to go to

    Returns:
        digest for locating server runtime directory
    """
    text = json.dumps(key, sort_keys=True, separators=(",", ":"))
    return sha512(text.encode("utf-8")).hexdigest()


def get_script_details(module: str, func: str) -> Tuple[str, Dict[str, Any]]:
    """
    Returns:
        (digest, reload_criteria)
    """
    main = sys.modules["__main__"].__file__
    bin_dir = os.path.dirname(main)
    # Data used for calculating server key.
    key = {"dir": bin_dir, "module": module, "func": func}
    digest = get_server_key(key)
    result = os.stat(main)
    # Keys used for determining if server reload required.
    key["mtime"] = result[stat.ST_MTIME]
    key["ctime"] = result[stat.ST_CTIME]
    logger.debug("Digest: %s", key, digest)
    return digest, key


if MYPY_CHECK_RUNNING:
    AttributeAccumulatorCallback = Callable[[List[str], ...], None]


def get_attribute_accumulator(callback: AttributeAccumulatorCallback):
    """Who knows what someone may put in their entry point spec.

    We try to take the most flexible approach here and accept as much as
    possible.

    Args:
        callback: called when the accumulator is called with the gathered
            names as the first argument.
    """
    # Use variable in closure to reduce chance of conflicting name.
    parts = []

    # As part of normal module processing, Python import machinery may query
    # attributes of our "module". The set of attributes may change over time but
    # will probably always be dunders. For that reason we will only recognize
    # a few dunder attributes as valid identifiers for user code.
    ALLOWED_DUNDERS = ["__init__", "__main__"]

    def is_dunder(name):
        return name.startswith("__") and name.endswith("__") and 4 < len(name)

    class Accumulator:
        def __getattribute__(self, name):
            if name == "__call__":
                return object.__getattribute__(self, name)

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


class ConsoleScriptHelper:
    """Helper class for console script.
    """

    def __init__(self, parts: List[str]):
        self._module_name, self._function_name = self._parse_script_spec(parts)
        self.digest, self.metadata = get_script_details(
            self._module_name.name, self._function_name.name
        )
        self.name = f"quicken.entrypoint.{self.digest}"

    def get_func(self):
        import importlib

        module = importlib.import_module(self._module_name.name)
        return getattr_nested(module, self._function_name.parts)

    def _parse_script_spec(self, parts: List[str]) -> Tuple[Name, Name]:
        """In order to preserve compatibility with pip and setuptools, users
        (library developers) can specify
        `<quicken entrypoint>:module.parts._.function.parts`. This function
        parses the dotted representations into module parts and function parts.
        Returns:
            module name, function name
        """
        try:
            i = parts.index("_")
        except ValueError:
            return Name(parts), Name()
        else:
            return Name(parts[:i]), Name(parts[i + 1 :])


def console_script(callback):
    """Wraps a callback meant to be a wrapper
    around a user-provided script.
    """

    def inner(parts):
        helper = ConsoleScriptHelper(parts)
        return callback(helper)

    default_configuration()

    return get_attribute_accumulator(inner)
