from datetime import datetime, timedelta
import os
import threading
import time

from quicken._internal.xdg import RuntimeDir

from ..utils import isolated_filesystem
from ..utils.pytest import non_windows
from ..utils.watch import wait_for_create, wait_for_delete
from ..utils.path import get_bound_path


pytestmark = non_windows


def test_wait_for_create_notices_existing_file():
    with isolated_filesystem() as p:
        runtime_dir = RuntimeDir(dir_path=p)
        file = get_bound_path(runtime_dir, 'example.txt')
        file.write_text('hello', encoding='utf-8')
        assert wait_for_create(file, timeout=0.01)


def test_wait_for_create_fails_missing_file():
    with isolated_filesystem() as p:
        runtime_dir = RuntimeDir(dir_path=p)
        file = get_bound_path(runtime_dir, 'example.txt')
        assert not wait_for_create(file, timeout=0.01)


def test_watch_for_create_notices_file_fast():
    with isolated_filesystem() as p:
        # To rule out dependence on being in the cwd.
        os.chdir('/')
        runtime_dir = RuntimeDir(dir_path=p)
        file = get_bound_path(runtime_dir, 'example.txt')
        writer_timestamp: datetime = None

        def create_file():
            nonlocal writer_timestamp
            time.sleep(0.05)
            file.write_text('hello', encoding='utf-8')
            writer_timestamp = datetime.now()

        t = threading.Thread(target=create_file)
        t.start()
        result = wait_for_create(file, timeout=1)
        t.join()
        timestamp = datetime.now()
        assert result, 'File must have been created'
        assert timestamp - writer_timestamp < timedelta(seconds=0.05)


def test_wait_for_delete_notices_missing_file():
    with isolated_filesystem() as p:
        runtime_dir = RuntimeDir(dir_path=p)
        file = get_bound_path(runtime_dir, 'example.txt')
        assert wait_for_delete(file, timeout=0.01)


def test_wait_for_delete_fails_existing_file():
    with isolated_filesystem() as p:
        runtime_dir = RuntimeDir(dir_path=p)
        file = get_bound_path(runtime_dir, 'example.txt')
        file.write_text('hello', encoding='utf-8')
        assert not wait_for_delete(file, timeout=0.1)


def test_watch_for_delete_notices_file_fast():
    with isolated_filesystem() as p:
        # To rule out dependence on being in the cwd.
        os.chdir('/')
        runtime_dir = RuntimeDir(dir_path=p)
        file = get_bound_path(runtime_dir, 'example.txt')
        file.write_text('hello', encoding='utf-8')
        writer_timestamp: datetime = None

        def create_file():
            nonlocal writer_timestamp
            time.sleep(0.05)
            writer_timestamp = datetime.now()
            file.unlink()

        t = threading.Thread(target=create_file)
        t.start()
        result = wait_for_delete(file, timeout=1)
        t.join()
        timestamp = datetime.now()
        assert result, 'File must have been removed'
        assert timestamp - writer_timestamp < timedelta(seconds=0.1)
