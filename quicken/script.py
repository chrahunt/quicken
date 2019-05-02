"""Entrypoint wrapper that starts a quicken server around the provided
command.
"""
import importlib
import sys

from ._scripts import (
    get_attribute_accumulator, get_nested_attr, parse_script_spec
)


__all__ = []


def callback(parts):
    from .lib import quicken
    module_parts, function_parts = parse_script_spec(parts)
    module_name = '.'.join(module_parts)
    function_name = '.'.join(function_parts)
    name = f'quicken.entrypoint.{module_name}.{function_name}'

    @quicken(name)
    def main():
        module = importlib.import_module(module_name)
        return get_nested_attr(module, function_parts)

    return main()


sys.modules[__name__] = get_attribute_accumulator(callback)
