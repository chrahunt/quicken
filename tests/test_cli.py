import logging
import os
import subprocess
import sys

from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

import pytest

from quicken._cli import parse_args, parse_file

from .utils import (
    captured_std_streams,
    chdir,
    env,
    isolated_filesystem,
    local_module,
    write_text,
)
from .utils.process import contained_children
from .utils.pytest import non_windows
from .utils.subprocess_helper import track_state


logger = logging.getLogger(__name__)


pytestmark = non_windows


@contextmanager
def sys_path(path):
    current_sys_path = sys.path
    sys.path = sys.path.copy()
    sys.path.append(path)
    try:
        yield
    finally:
        sys.path = current_sys_path


def test_args_ctl_passthru():
    _, args = parse_args(['-f', './script.py', '--', '--ctl'])
    assert args.f == './script.py'
    assert args.args == ['--ctl']


#def test_args_module_passthru():
#    _, args = parse_args(['-m', 'pytest', '--', '-s', '-ra'])
#    assert args.m == 'pytest'
#    assert args.args == ['-s', '-ra']


def test_file_args_passthru():
    _, args = parse_args(['-f', 'foo', '--', '-m', 'hello'])
    assert args.f == 'foo'
    assert args.args == ['-m', 'hello']


def test_file_evaluation():
    # Given a package hello with
    #
    # hello/
    #   __init__.py
    #   foo.py
    #
    # # hello/__init__.py
    # foo = 1
    #
    # # script.py
    # from hello import foo
    # import hello.foo
    #
    # if __name__ == '__main__':
    #     print(foo)
    #
    # should print 1
    with local_module():
        module = Path('hello')
        module.mkdir()
        write_text(
            module / '__init__.py',
            'foo = 1',
        )
        write_text(module / 'foo.py', '')
        write_text(
            Path('script.py'),
            '''
            from hello import foo
            import hello.foo

            if __name__ == '__main__':
                print(foo)
            '''
        )

        prelude, main = parse_file('script.py')

        prelude()

        with captured_std_streams() as (stdin, stdout, stderr):
            main()

        output = stdout.read()
        assert output == '1\n'


def pytest_exception_location(exc_info):
    entry = exc_info.traceback[1]
    # The pytest traceback information line number is one less than actual.
    return str(entry.path), entry.lineno + 1


def test_file_prelude_backtrace_line_numbering():
    # Given a file `script.py` that raises an exception in its prelude
    # And the file is parsed
    # When the prelude section is executed
    # Then the backtrace should have the correct exception
    # And the line number should match the line in the file
    with isolated_filesystem():
        write_text(
            Path('script.py'),
            '''\
            import os
            raise RuntimeError('example')

            if __name__ == '__main__':
                raise RuntimeError('example2')
            '''
        )

        prelude, main = parse_file('script.py')

        with pytest.raises(RuntimeError) as e:
            prelude()

        assert 'example' in str(e)
        filename, lineno = pytest_exception_location(e)
        assert filename == str(Path('script.py').absolute())
        assert lineno == 2


def test_file_main_backtrace_line_numbering():
    # Given a file `script.py` that raises an exception in its main part
    # And the file is parsed
    # When the prelude section is executed
    # Then the backtrace should have the correct exception
    # And the line number should match the line in the file
    with isolated_filesystem():
        write_text(
            Path('script.py'),
            '''\
            import os

            if __name__ == '__main__':
                os.getpid
                raise RuntimeError('example')
            '''
        )

        prelude, main = parse_file('script.py')

        prelude()

        with pytest.raises(RuntimeError) as e:
            main()

        filename, lineno = pytest_exception_location(e)
        assert filename == str(Path('script.py').absolute())
        assert lineno == 5


def test_python_sets_file_path_using_argument():
    # Given a script, a/script.py
    # And a symlink a/foo pointing to script.py
    # When python executes <target> from <cwd>
    # Then __file__ should be <__file__>
    with isolated_filesystem() as path:
        parent = path / 'a'
        parent.mkdir()
        script = parent / 'script.py'
        write_text(
            script,
            '''
            print(__file__)
            '''
        )

        symlink = parent / 'foo'
        symlink.symlink_to(script.name)

        cases = [
            ['a', symlink.name],
            ['a', symlink],
            ['a', script.name],
            ['a', script],
            ['.', f'a/{symlink.name}'],
            ['.', symlink],
            ['.', f'a/{script.name}'],
            ['.', script],
        ]

        for cwd, file in cases:
            result = subprocess.run(
                [sys.executable, file],
                stdout=subprocess.PIPE,
                cwd=cwd,
            )
            output = result.stdout.decode('utf-8').strip()
            assert output == str(file)


def test_file_path_set_absolute():
    # Given a file `script.py`
    # And the code is split into prelude and main
    # When executed with the results of parse_file
    # Then __file__ should be the full, resolved path to the file
    with isolated_filesystem() as path:
        script = path / 'script.py'
        write_text(
            script,
            '''
            print(__file__)

            if __name__ == '__main__':
                print(__file__)
            '''
        )

        prelude, main = parse_file(str(script))

        with captured_std_streams() as (stdin, stdout, stderr):
            prelude()

        assert stdout.read().strip() == str(script)

        with captured_std_streams() as (stdin, stdout, stderr):
            main()

        assert stdout.read().strip() == str(script)


def test_file_path_symlink_uses_resolved_path():
    # Given a file `script.py`
    # And a symlink `foo` that points to it
    # When executed with the results of parse_file
    # Then __file__ should be the full, resolved path to the file
    with isolated_filesystem() as path:
        script = path / 'script.py'
        write_text(
            script,
            '''
            print(__file__)

            if __name__ == '__main__':
                print(__file__)
            '''
        )

        symlink = path / 'foo'
        symlink.symlink_to(script.name)

        prelude, main = parse_file(str(script))

        with captured_std_streams() as (stdin, stdout, stderr):
            prelude()

        assert stdout.read().strip() == str(script)

        with captured_std_streams() as (stdin, stdout, stderr):
            main()

        assert stdout.read().strip() == str(script)


@pytest.fixture
def quicken_script(quicken_venv):
    path = os.environ['PATH']
    bin_dir = quicken_venv.path / 'bin'
    with env(PATH=f'{bin_dir}:{path}'):
        yield


def test_file_argv_set(log_file_path, quicken_script):
    # Given a file `script.py`
    # sys.argv should start with `script.py` and be followed by any
    # other arguments
    with isolated_filesystem():
        Path('script.py').write_text(dedent('''
        import sys

        if __name__ == '__main__':
            print(sys.argv[0])
            print(sys.argv[1])
        '''))

        args = ['hello']
        with env(QUICKEN_LOG=str(log_file_path)):
            with contained_children():
                result = subprocess.run(
                    ['quicken', '-f', 'script.py', 'hello'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

        assert result.returncode == 0, f'process must succeed: {result}'
        assert result.stdout.decode('utf-8') == f'script.py\n{args[0]}\n'


def test_file_server_name_uses_absolute_resolved_path(log_file_path, quicken_script):
    # Given a file `a/script.py`
    # And a symlink `a/foo` pointing to `script.py`
    # And a server started from `a/script.py`
    # When `quicken -f a/script.py` is executed from `.`
    # And `quicken -f a/foo` is executed from `.`
    # And `quicken -f script.py` is executed from `a`
    # And `quicken -f foo` is executed from `a`
    # Then the same server should be used to handle all of them
    with isolated_filesystem():
        base_dir = Path('a')
        base_dir.mkdir()
        script = base_dir / 'script.py'
        write_text(
            script,
            '''
            import __test_helper__

            if __name__ == '__main__':
                __test_helper__.record()
            '''
        )

        symlink = base_dir / 'foo'
        symlink.symlink_to(script.name)

        test_pid = os.getpid()

        with env(QUICKEN_LOG=str(log_file_path)):
            with contained_children():
                with track_state() as run1:
                    result = subprocess.run(
                        ['quicken', '-f', str(script)]
                    )

                assert result.returncode == 0
                assert run1.pid != test_pid
                assert run1.ppid != test_pid

                with track_state() as run2:
                    result = subprocess.run(
                        ['quicken', '-f', str(symlink)]
                    )

                assert result.returncode == 0
                assert run1.pid != run2.pid
                assert run1.ppid == run2.ppid

                with chdir('a'):
                    with track_state() as run3:
                        result = subprocess.run(
                            ['quicken', '-f', script.name]
                        )

                    assert result.returncode == 0
                    assert run1.pid != run3.pid
                    assert run1.ppid == run3.ppid

                    with track_state() as run4:
                        result = subprocess.run(
                            ['quicken', '-f', symlink.name]
                        )

                    assert result.returncode == 0
                    assert run1.pid != run4.pid
                    assert run1.ppid == run4.ppid


def test_file_path_symlink_modified(log_file_path, quicken_script):
    # Given a file `script.py`
    # And a symlink `foo` that points to it
    # And the server is already up, having been executed via the symlink
    # And `script.py` is updated
    # When the script is executed again via the symlink
    # Then the server will be reloaded
    with isolated_filesystem():
        base_dir = Path('a')
        base_dir.mkdir()
        script = base_dir / 'script.py'
        write_text(
            script,
            '''
            import __test_helper__

            if __name__ == '__main__':
                __test_helper__.record()
            '''
        )

        symlink = base_dir / 'foo'
        symlink.symlink_to(script.name)

        def update_file_mtime(path):
            result = os.stat(path)
            new_times = (result.st_atime, result.st_mtime + 1)
            os.utime(path, new_times)

        test_pid = os.getpid()

        with env(QUICKEN_LOG=str(log_file_path)):
            with contained_children():
                with track_state() as run1:
                    result = subprocess.run(
                        ['quicken', '-f', str(symlink)]
                    )

                assert result.returncode == 0
                assert run1.pid != test_pid
                assert run1.ppid != test_pid

                update_file_mtime(script)

                with track_state() as run2:
                    result = subprocess.run(
                        ['quicken', '-f', str(symlink)]
                    )

                assert result.returncode == 0
                assert run2.pid != test_pid
                assert run2.ppid != test_pid
                assert run1.pid != run2.pid
                assert run1.ppid != run2.ppid


def test_log_file_unwritable_fails_fast_cli(quicken_script):
    # Given a QUICKEN_LOG path pointing to a location that is not writable
    # When the CLI is executed
    # Then it should fail with a nonzero exit code and reasonable message
    with isolated_filesystem():
        script = Path('script.py')
        write_text(
            script,
            '''
            import __test_helper__

            if __name__ == '__main__':
                __test_helper__.record()
            '''
        )

        log_file = Path('example.log')
        log_file.touch(0o000, exist_ok=False)

        with env(QUICKEN_LOG=str(log_file.absolute())):
            with contained_children():
                result = subprocess.run(
                    ['quicken', '-f', script],
                    stderr=subprocess.PIPE,
                )

            assert result.returncode == 2

            stderr = result.stderr.decode('utf-8')
            assert str(log_file.absolute()) in stderr
            assert 'not writable' in stderr


def test_script_file_unreadable_fails_with_error(quicken_script):
    # Given a script file that is not readable
    # When the CLI is executed
    # Then it should fail with a nonzero exit code and reasonable message
    with isolated_filesystem():
        script = Path('script.py')
        script.touch(0o000, exist_ok=False)

        with contained_children():
            result = subprocess.run(
                ['quicken', '-f', str(script)],
                stderr=subprocess.PIPE,
            )

        assert result.returncode == 2
        stderr = result.stderr.decode('utf-8')
        assert str(script) in stderr
        assert 'Cannot read' in stderr
