import multiprocessing as mp

from datetime import datetime

import pytest

from .utils.pytest import non_windows


pytestmark = non_windows


@pytest.fixture
def spawn_ctx():
    return mp.get_context('spawn')


def noop():
    pass


def import_quicken():
    from quicken import cli_factory


def test_python_spawn_time(spawn_ctx: mp):
    p = spawn_ctx.Process(target=noop)
    start = datetime.now()
    p.start()
    p.join()
    end = datetime.now()
    print(end - start)


def test_cli_wrapper_import_time_is_not_high(spawn_ctx):
    # Should be higher than python -c ''
    # But not higher than python -c 'import quicken.
    p = spawn_ctx.Process(target=import_quicken)
    start = datetime.now()
    p.start()
    p.join()
    end = datetime.now()
    print(end - start)
