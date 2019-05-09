"""Test script helpers.
"""
import os
import subprocess
import string
import sys

from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

from quicken._scripts import get_attribute_accumulator, ScriptHelper

from .utils import env, isolated_filesystem, kept


def test_attribute_accumulator():
    result = None

    def check(this_result):
        nonlocal result
        result = this_result

    get_attribute_accumulator(check).foo.bar.baz()

    assert result == ['foo', 'bar', 'baz']

    get_attribute_accumulator(check).__init__.hello._.world()

    assert result == ['__init__', 'hello', '_', 'world']


@contextmanager
def local_module():
    with isolated_filesystem() as path:
        with kept(sys, 'path'):
            sys.path.append(str(path))
            with kept(sys, 'modules'):
                yield


def write_text(path: Path, text: str, **params):
    new_text = string.Template(dedent(text)).substitute(params)
    path.write_text(new_text)


def test_helper_runs_module_and_func():
    # Given a module and function specification
    # When the script helper is requested to import the function
    # Then it will return the correct function
    with local_module():
        module = Path('pkg/module.py')
        module.parent.mkdir(parents=True)
        write_text(
            module,
            '''
            def example():
                return 5
            ''',
        )
        Path('pkg/__init__.py').touch()

        helper = ScriptHelper(['pkg', 'module', '_', 'example'])
        func = helper.get_func()
        assert func() == 5


def test_helper_runs_module_without_func():
    # Given a module specification
    # When the script helper is requested to import the function
    # Then it will return the correct function

    # Technically pip doesn't appear to support only-module console_script entrypoint
    # specs, but we'll test for it anyway.
    with local_module():
        module = Path('pkg/module.py')
        module.parent.mkdir(parents=True)
        write_text(
            module,
            '''
            import sys

            from types import ModuleType


            class Module(ModuleType):
                def __call__(self):
                    return 5


            sys.modules[__name__].__class__ = Module
            ''',
        )
        Path('pkg/__init__.py').touch()

        helper = ScriptHelper(['pkg', 'module'])
        func = helper.get_func()
        assert func() == 5


def test_script_runs_server(virtualenvs):
    # Given a project that declares a quicken script
    # When the script is executed
    # And the script is executed again
    # Then the script will have been executed in the server
    venv = virtualenvs.create()
    # Ensure we're at least on 19.1.
    venv.run(['-m', 'pip', 'install', '--upgrade', 'pip'])
    quicken_path = Path(__file__).parent / '..'
    venv.run(['-m', 'pip', 'install', quicken_path])

    with isolated_filesystem() as path:
        write_text(
            path / 'pyproject.toml',
            '''
            [tool.poetry]
            name = "hello"
            version = "0.1.0"
            description = "hello package"
            authors = ["author <author@example.com>"]
            packages = [
                { include = "hello.py" },
            ]
            
            [tool.poetry.dependencies]
            quicken = "*"

            [tool.poetry.scripts]
            hello = "quicken.script:hello._.main"

            [build-system]
            requires = ["poetry>=0.12"]
            build-backend = "poetry.masonry.api"
            ''',
        )

        write_text(
            path / 'hello.py',
            '''
            import os
            
            from pathlib import Path


            def main():
                Path(os.environ['TEST_STATE']).write_text(
                    f'{os.getpid()} {os.getppid()}'
                )
            ''',
        )

        venv.run(['-m', 'pip', 'install', '.'])

        test_pid = os.getpid()
        state_file = path / 'test1.txt'
        # TODO: Better control over server lifetime in tests under subprocess.
        with env(QUICKEN_IDLE_TIMEOUT=str(5), TEST_STATE=str(state_file)):
            result = subprocess.run([str(venv.path / 'bin' / 'hello')])
            assert result.returncode == 0
            text = state_file.read_text(encoding='utf-8')
            runner_pid_1, runner_ppid_1 = [int(v) for v in text.split()]
            assert test_pid != runner_pid_1
            assert test_pid != runner_ppid_1

        with env(TEST_STATE=str(state_file)):
            result = subprocess.run([str(venv.path / 'bin' / 'hello')])
            assert result.returncode == 0
            text = state_file.read_text(encoding='utf-8')
            runner_pid_2, runner_ppid_2 = [int(v) for v in text.split()]
            assert runner_pid_1 != runner_pid_2
            assert runner_ppid_1 == runner_ppid_2


def test_script_different_venv_different_servers():
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # And the project is also installed in venv2
    # When the script in venv1 is executed
    # And the script in venv2 is executed
    # Then they will have been executed by different servers
    ...


def test_script_idle_timeout():
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    ...

def test_control_script_status():
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with status
    # Then the server status will be output
    ...


def test_control_script_stop():
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with stop
    # Then the server will stop
    ...
