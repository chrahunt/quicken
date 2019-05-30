"""Test script helpers.
"""
import os
import subprocess
import string
import sys

from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

import pytest

from quicken._scripts import get_attribute_accumulator, ScriptHelper

from .utils import env, isolated_filesystem, kept
from .utils.process import active_children, contained_children
from .utils.pytest import non_windows
from .utils.subprocess import track_state


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


@non_windows
def test_script_runs_server(virtualenvs):
    # Given a project that declares a quicken script
    # When the script is executed
    # And the script is executed again
    # Then the script will have been executed in the server
    # And the second script will have been executed in the same server
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
                from __test_helper__ import record
                record()
            ''',
        )

        venv.run(['-m', 'pip', 'install', '.'])

        with contained_children():
            test_pid = os.getpid()
            with track_state() as run1:
                result = subprocess.run([str(venv.path / 'bin' / 'hello')])
                assert result.returncode == 0
                assert test_pid != run1.pid
                assert test_pid != run1.ppid

            with track_state() as run2:
                result = subprocess.run([str(venv.path / 'bin' / 'hello')])
                assert result.returncode == 0
                assert run1.pid != run2.pid
                assert run1.ppid == run2.ppid

            print(active_children())


@non_windows
def test_script_different_venv_different_servers(virtualenvs):
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # And the project is also installed in venv2
    # When the script in venv1 is executed
    # And the script in venv2 is executed
    # Then they will have been executed by different servers
    venv = virtualenvs.create()
    venv2 = virtualenvs.create()
    # Ensure we're at least on 19.1.
    venv.run(['-m', 'pip', 'install', '--upgrade', 'pip'])
    venv2.run(['-m', 'pip', 'install', '--upgrade', 'pip'])
    quicken_path = Path(__file__).parent / '..'
    venv.run(['-m', 'pip', 'install', quicken_path])
    venv2.run(['-m', 'pip', 'install', quicken_path])

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
                from __test_helper__ import record
                record()
            ''',
            )

        venv.run(['-m', 'pip', 'install', '.'])
        venv2.run(['-m', 'pip', 'install', '.'])

        with contained_children():
            test_pid = os.getpid()
            with track_state() as run1:
                result = subprocess.run([str(venv.path / 'bin' / 'hello')])
                assert result.returncode == 0
                assert test_pid != run1.pid
                assert test_pid != run1.ppid

            with track_state() as run2:
                result = subprocess.run([str(venv2.path / 'bin' / 'hello')])
                assert result.returncode == 0
                assert test_pid != run2.pid
                assert test_pid != run2.ppid
                assert run1.pid != run2.pid
                assert run1.ppid != run2.ppid


@pytest.mark.skip
def test_script_idle_timeout():
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # When the quicken script is executed
    # And QUICKEN_IDLE_TIMEOUT is set to a nonzero value
    # Then the server will shut down after that long without
    #  any requests.
    ...


@pytest.mark.skip
def test_control_script_status():
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with status
    # Then the server status will be output
    ...


@pytest.mark.skip
def test_control_script_stop():
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with stop
    # Then the server will stop
    ...
