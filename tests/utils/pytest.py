from __future__ import annotations

import os
import re
import sys

import pytest

from _pytest.capture import DontReadFromInput, EncodedFile

from quicken._internal._multiprocessing_reduction import register

windows_only = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="Windows only"
)


non_windows = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="Non-Windows only"
)


_name_re = re.compile(r"(?P<file>.+?)::(?P<name>.+?) \(.*\)$")


def current_test_name():
    try:
        name = os.environ["PYTEST_CURRENT_TEST"]
    except KeyError:
        return "<outside test>"
    m = _name_re.match(name)
    if not m:
        raise RuntimeError(f"Could not extract test name from {name}")
    return m.group("name")


def reduce_encodedfile(obj: EncodedFile):
    """Register handler for the replaced stdout/stderr that pytest
    uses.
    """
    return EncodedFile, (obj.buffer, obj.encoding)


register(EncodedFile, reduce_encodedfile)


def reduce_dontreadfrominput(obj: DontReadFromInput):
    return DontReadFromInput, tuple()


register(DontReadFromInput, reduce_dontreadfrominput)
