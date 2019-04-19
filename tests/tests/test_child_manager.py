import multiprocessing

from ctypes import c_int
from multiprocessing.sharedctypes import RawArray

import pytest

from ..utils.process import contained_children, kill_children
from ..utils.pytest import non_windows


pytestmark = non_windows


@pytest.mark.timeout(30, callback=kill_children)
def test_child_manager_handles_multiple_children():
    maxdepth = 5
    tospawn = 2
    total = tospawn ** (maxdepth + 1)
    array = RawArray(c_int, total)

    def target(depth, i):
        if depth == maxdepth:
            return
        base = i * tospawn
        next_depth = depth + 1
        for i in range(tospawn):
            global_i = base + i
            p = multiprocessing.Process(target=target, args=(next_depth, global_i))
            p.start()
            p.join()
            idx = 2 ** next_depth - 2 + global_i
            array[idx] = p.pid

    with contained_children() as manager:
        target(0, 0)
