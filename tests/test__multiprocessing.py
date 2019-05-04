import os
import multiprocessing
import time

from pathlib import Path

import pytest

from quicken.lib._multiprocessing import run_in_process

from .utils import isolated_filesystem
from .utils.process import contained_children
from .utils.pytest import non_windows


pytestmark = non_windows


def test_function_is_executed_in_separate_process():
    with isolated_filesystem() as path:
        def runner():
            pidfile.write_text(str(os.getpid()), encoding='utf-8')
        pidfile = Path(path) / 'pid.txt'
        run_in_process(target=runner)
        main_pid = str(os.getpid())
        runner_pid = pidfile.read_text(encoding='utf-8')
        assert runner_pid, 'Runner pid must have been set'
        assert main_pid != runner_pid, \
            'Function must have been run in separate process'


# XXX: Kind of ugly, as the exception information is shown in stderr by
#  multiprocessing.
def test_function_exception_is_reraised():
    message = 'example message'
    def runner():
        raise RuntimeError(message)
    with pytest.raises(RuntimeError) as exc_info:
        run_in_process(target=runner)
    assert message == exc_info.value.args[0]


def test_function_return_value_is_returned():
    value = 123
    def runner():
        return value
    result = run_in_process(target=runner)
    assert result == value


def test_function_gets_args():
    arg = '123'
    def runner(s):
        return s
    result = run_in_process(target=runner, args=(arg,))
    assert result == arg


def test_function_timeout_works():
    timeout = 0.5
    def runner():
        time.sleep(timeout * 2)
    with pytest.raises(TimeoutError):
        run_in_process(target=runner, timeout=timeout)


def test_function_detach_works():
    def runner(detach_process):
        detach_process()
        time.sleep(5)

    with contained_children() as manager:
        run_in_process(runner, allow_detach=True)
        assert not multiprocessing.active_children(), \
            'Multiprocessing should not see active children'

        assert manager.active_children(), \
            'Process should still be up'
