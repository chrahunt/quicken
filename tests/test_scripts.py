"""Test script helpers.
"""
from quicken._scripts import get_attribute_accumulator


def test_attribute_accumulator():
    result = None

    def check(this_result):
        nonlocal result
        result = this_result

    get_attribute_accumulator(check).foo.bar.baz()

    assert result == ['foo', 'bar', 'baz']

    get_attribute_accumulator(check).__init__.hello._.world()

    assert result == ['__init__', 'hello', '_', 'world']
