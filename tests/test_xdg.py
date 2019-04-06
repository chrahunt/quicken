import os
import tempfile

from pathlib import Path

from quicken._xdg import RuntimeDir

import pytest

from .utils import env
from .utils.pytest import non_windows


pytestmark = non_windows


def test_runtime_dir_uses_xdg_env_var():
    # Given XDG_RUNTIME_DIR is set
    # When RuntimeDir is invoked with name 'quicken-test'
    # Then it should succeed and its path should be
    #   $XDG_RUNTIME_DIR/quicken-test.
    with tempfile.TemporaryDirectory() as p:
        with env(XDG_RUNTIME_DIR=p):
            runtime_dir = RuntimeDir('quicken-test')
            assert str(runtime_dir) == os.path.join(p, 'quicken-test')
            os.fchdir(runtime_dir.fileno())
            assert str(runtime_dir) == os.getcwd()


def test_runtime_dir_uses_tmpdir_env_var():
    # Given XDG_RUNTIME_DIR is not set
    # And TMPDIR is set
    # When RuntimeDir is invoked with name 'quicken-test'
    # Then it should succeed and its path should be
    #   $TMPDIR/quicken-test-{uid}
    with tempfile.TemporaryDirectory() as p:
        with env(XDG_RUNTIME_DIR=None, TMPDIR=p):
            runtime_dir = RuntimeDir('quicken-test')
            uid = os.getuid()
            assert str(runtime_dir) == os.path.join(p, f'quicken-test-{uid}')
            os.fchdir(runtime_dir.fileno())
            assert str(runtime_dir) == os.getcwd()


def test_runtime_dir_uses_tmp_fallback():
    # Given XDG_RUNTIME_DIR and TMPDIR are not set
    # When RuntimeDir is invoked with name 'quicken-test'
    # Then it should succeed and its path should be
    #   /tmp/quicken-test-{uid}
    with env(XDG_RUNTIME_DIR=None, TMPDIR=None):
        runtime_dir = RuntimeDir('quicken-test')
        uid = os.getuid()
        assert str(runtime_dir) == f'/tmp/quicken-test-{uid}'
        os.fchdir(runtime_dir.fileno())
        assert str(runtime_dir) == os.getcwd()


def test_runtime_dir_fails_when_no_args():
    with pytest.raises(ValueError) as excinfo:
        _runtime_dir = RuntimeDir()
    v = str(excinfo.value)
    assert 'base_name' in v and 'dir_path' in v


def test_runtime_dir_fails_when_bad_permissions():
    # Given a directory that exists with permissions 770.
    # When a RuntimeDir is constructed from it then
    with tempfile.TemporaryDirectory() as p:
        os.chmod(p, 0o770)
        with pytest.raises(RuntimeError) as excinfo:
            _runtime_dir = RuntimeDir(dir_path=p)
        v = str(excinfo.value)
        assert 'must have permissions 700' in v


def test_runtime_dir_succeeds_creating_a_file():
    sample_text = 'hello'
    with tempfile.TemporaryDirectory() as p:
        runtime_dir = RuntimeDir(dir_path=p)
        file = runtime_dir.path('example')
        file.write_text(sample_text, encoding='utf-8')
        text = (Path(p) / 'example').read_text(encoding='utf-8')
        assert sample_text == text


def test_runtime_dir_path_fails_when_directory_unlinked_and_recreated():
    # Given a runtime dir that has been created.
    # And removed
    # And recreated manually
    # When the runtime dir is used to create a new file
    # Then the operation should fail.
    sample_text = 'hello'
    with tempfile.TemporaryDirectory() as p:
        runtime_dir = RuntimeDir(dir_path=p)
        file = runtime_dir.path('example')

    Path(p).mkdir()

    try:
        with pytest.raises(FileNotFoundError) as excinfo:
            file.write_text(sample_text, encoding='utf-8')

        assert 'example' in str(excinfo.value)
        assert Path(p).exists()
    finally:
        Path(p).rmdir()


def test_runtime_dir_rejects_absolute_paths():
    # Given a runtime dir that has been created.
    # When an absolute path is passed to `path`
    # Then it should reject with a ValueError.
    ...
