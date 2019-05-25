import os
import multiprocessing
import time

from pathlib import Path

import pytest

# noinspection PyProtectedMember
from quicken.lib._multiprocessing import run_in_process
# noinspection PyProtectedMember
from quicken.lib._multiprocessing_reduction import (
    dumps,
    loads,
    set_fd_sharing_base_path_fd,
)

from .utils import isolated_filesystem
from .utils.process import active_children, contained_children
from .utils.pytest import non_windows
from .utils.watch import wait_for_create


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

    with contained_children():
        run_in_process(runner, allow_detach=True)
        assert not multiprocessing.active_children(), \
            'Multiprocessing should not see active children'

        assert active_children(), \
            'Process should still be up'


@pytest.mark.timeout(5)
def test_textiowrapper_serialization_works():
    shared_text = 'hello\n'

    def acceptor():
        listener = multiprocessing.connection.Listener('socket')
        conn = listener.accept()
        obj = loads(conn.recv())
        obj.write(shared_text)
        obj.flush()
        obj.close()

    with isolated_filesystem() as path:
        cwd_fd = os.open('.', os.O_RDONLY)
        set_fd_sharing_base_path_fd(cwd_fd)

        p = multiprocessing.Process(target=acceptor)
        p.start()

        wait_for_create(path / 'socket', 2)
        r, w = os.pipe()
        r_obj = os.fdopen(r, closefd=False)
        w_obj = os.fdopen(w, 'w', closefd=False)
        c = multiprocessing.connection.Client('socket')
        c.send(dumps(w_obj))
        text = r_obj.readline()
        assert text == shared_text
        os.close(r)
        os.close(w)
        p.join()
        assert p.exitcode == 0, 'Process must have exited cleanly'
