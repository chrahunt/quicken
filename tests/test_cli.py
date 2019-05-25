import os
import subprocess
import sys

from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

import pytest

from quicken._cli import parse_args, parse_file

from .utils import captured_std_streams, chdir, env, isolated_filesystem, kept
from .utils.process import contained_children
from .utils.pytest import non_windows

import logging; logger = logging.getLogger(__name__)

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
    with isolated_filesystem() as path:
        Path('hello').mkdir()
        Path('hello/__init__.py').write_text('foo = 1')
        Path('hello/foo.py').write_text('')

        Path('script.py').write_text(dedent('''
        from hello import foo
        import hello.foo

        if __name__ == '__main__':
            print(foo)
        '''))

        with sys_path(str(path)):
            with kept(sys, 'modules'):
                prelude, main = parse_file(str(path / 'script.py'))

                prelude()

                with captured_std_streams() as (stdin, stdout, stderr):
                    main()

        output = stdout.read()
        assert output == '1\n'


def test_file_backtrace_line_numbering():
    # Given a file `script.py`:
    #
    # import os
    # raise RuntimeError('example')
    #
    # if __name__ == '__main__':
    #     raise RuntimeError('example2')
    #
    # When executed, the backtrace should have RuntimeError('example') coming
    # from the appropriate location.
    with isolated_filesystem():
        Path('script.py').write_text(dedent('''\
        import os
        raise RuntimeError('example')

        if __name__ == '__main__':
            raise RuntimeError('example2')
        '''))

        prelude, main = parse_file('script.py')

        with pytest.raises(RuntimeError) as e:
            prelude()

        assert 'example' in str(e)
        entry = e.traceback[1]
        assert str(entry.path) == str(Path('script.py').absolute())
        # the pytest lineno is one less than actual
        assert entry.lineno + 1 == 2


def test_file_main_backtrace_line_numbering():
    # Given a file `script.py`:
    #
    # import os
    #
    # if __name__ == '__main__':
    #     os.getpid
    #     raise RuntimeError('example')
    #
    # When executed, the backtrace should have RuntimeError('example') coming
    # from the appropriate location.
    with isolated_filesystem():
        Path('script.py').write_text(dedent('''\
        import os

        if __name__ == '__main__':
            os.getpid
            raise RuntimeError('example')
        '''))

        prelude, main = parse_file('script.py')

        prelude()

        with pytest.raises(RuntimeError) as e:
            main()

        entry = e.traceback[1]
        assert str(entry.path) == str(Path('script.py').absolute())
        assert entry.lineno + 1 == 5


def test_file_path_set():
    # Given a file `script.py`
    # Executed like python script.py
    # The Python interpreter sets __file__ to the value passed as the first
    # argument.
    # The problem with that is we execute main from a cwd that is different than
    # the initial execution for the module.
    # So we normalize the __file__ attribute to always be the full, resolved path
    # to the file.
    with isolated_filesystem() as path:
        Path('script.py').write_text(dedent('''
        print(__file__)

        if __name__ == '__main__':
            print(__file__)
        '''))

        prelude, main = parse_file('script.py')

        with captured_std_streams() as (stdin, stdout, stderr):
            prelude()

        assert stdout.read().strip() == str(path / 'script.py')

        with captured_std_streams() as (stdin, stdout, stderr):
            main()

        assert stdout.read().strip() == str(path / 'script.py')


def test_file_path_set_symlink():
    # Given a file `script.py`
    # __file__ should be the full, resolved path to the file.
    ...


def test_file_path_symlink_modified():
    ...


# TODO: Make console scripts nicer to access for tests.
def run_cli(*args, **kwargs):
    runner_path = Path('runner.py')
    runner_path.write_text(dedent('''
    from quicken._cli import main

    main()
    '''))

    cmd = [
        sys.executable,
        str(runner_path.absolute()),
    ]

    if args:
        cmd.extend(args[0])

    return subprocess.run(cmd, *args[1:], **kwargs)


def test_file_argv_set(log_file_path):
    # Given a file `script.py`
    # sys.argv should start with `script.py` and be followed by any
    # other arguments
    with isolated_filesystem() as path:
        Path('script.py').write_text(dedent('''
        import sys

        if __name__ == '__main__':
            print(sys.argv[0])
            print(sys.argv[1])
        '''))

        args = ['hello']
        with env(QUICKEN_LOG=str(log_file_path)):
            with contained_children():
                result = run_cli(
                    [
                        '-f',
                        str(Path('script.py')),
                        *args
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
        assert result.stdout.decode('utf-8') == f'script.py\n{args[0]}\n'


def test_file_server_name_uses_absolute_path(log_file_path):
    # Given a file a/script.py
    # And a server started from a/script.py
    # When in a
    # And quicken -f script.py is executed
    # Then the server should be used for the call
    with isolated_filesystem() as path:
        script_a = Path('a/script.py')
        script_a.parent.mkdir(parents=True)
        script_a.write_text(dedent('''
        import os

        if __name__ == '__main__':
            print(os.getpid())
            print(os.getppid())
        '''))

        with env(QUICKEN_LOG=str(log_file_path)):
            with contained_children():
                result = run_cli(
                    [
                        '-f',
                        str(script_a),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            current_pid = str(os.getpid())
            runner_pid_1, parent_pid_1 = result.stdout.decode('utf-8').split()
            assert runner_pid_1 != current_pid
            assert parent_pid_1 != current_pid

            with chdir('a'):
                with contained_children():
                    result = run_cli(
                        [
                            '-f',
                            script_a.name,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )

                runner_pid_2, parent_pid_2 = result.stdout.decode('utf-8').split()
                assert runner_pid_2 != current_pid
                assert parent_pid_2 != current_pid
                assert runner_pid_1 != runner_pid_2
                assert parent_pid_1 == parent_pid_2
