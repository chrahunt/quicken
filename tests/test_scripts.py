"""Test quicken.script and quicken.ctl_script along with associated helpers.
"""
import json
import subprocess
import time

from pathlib import Path

import pytest

from quicken._scripts import get_attribute_accumulator, ScriptHelper

from .utils import env, isolated_filesystem, local_module, write_text
from .utils.process import active_children, contained_children
from .utils.pytest import current_test_name, non_windows
from .utils.subprocess_helper import track_state


def test_attribute_accumulator():
    result = None

    def check(this_result):
        nonlocal result
        result = this_result

    get_attribute_accumulator(check).foo.bar.baz()

    assert result == ['foo', 'bar', 'baz']

    get_attribute_accumulator(check).__init__.hello._.world()

    assert result == ['__init__', 'hello', '_', 'world']


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


@pytest.fixture(scope='module')
def poetry_build_venv(venvs):
    venv = venvs.create()
    venv.install('poetry>=0.12')
    yield venv


basic_poetry_pyproject = '''
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
helloctl = "quicken.ctl_script:hello._.main"

[build-system]
requires = ["poetry>=0.12"]
build-backend = "poetry.masonry.api"
'''


@non_windows
def test_script_runs_server(quicken_venv, poetry_build_venv, venvs):
    # Given a project that declares a quicken script
    # When the script is executed
    # And the script is executed again
    # Then the script will have been executed in the server
    # And the script will have been executed in the same server the second time
    venv = venvs.create()
    venv.use_packages_from(quicken_venv)
    venv.use_packages_from(poetry_build_venv)

    with isolated_filesystem() as path:
        write_text(path / 'pyproject.toml', basic_poetry_pyproject)

        write_text(
            path / 'hello.py',
            '''
            def main():
                from __test_helper__ import record
                record()
            ''',
        )

        venv.install('--no-build-isolation', '.')

        with contained_children():
            with track_state() as run1:
                result = subprocess.run([str(venv.path / 'bin' / 'hello')])

            assert result.returncode == 0
            run1.assert_unrelated_to_current_process()

            with track_state() as run2:
                result = subprocess.run([str(venv.path / 'bin' / 'hello')])

            assert result.returncode == 0
            run2.assert_unrelated_to_current_process()
            run2.assert_same_parent_as(run1)

            print(active_children())


@non_windows
def test_script_different_venv_different_servers(quicken_venv, poetry_build_venv, venvs):
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # And the project is also installed in venv2
    # When the script in venv1 is executed
    # And the script in venv2 is executed
    # Then they will have been executed by different servers
    venv = venvs.create()
    venv.use_packages_from(quicken_venv)
    venv.use_packages_from(poetry_build_venv)
    venv2 = venvs.create()
    venv2.use_packages_from(quicken_venv)
    venv2.use_packages_from(poetry_build_venv)

    with isolated_filesystem() as path:
        write_text(path / 'pyproject.toml', basic_poetry_pyproject)

        write_text(
            path / 'hello.py',
            '''
            def main():
                from __test_helper__ import record
                record()
            ''',
        )

        venv.install('--no-build-isolation', '.')
        venv2.install('--no-build-isolation', '.')

        with contained_children():
            with track_state() as run1:
                result = subprocess.run([str(venv.path / 'bin' / 'hello')])

            assert result.returncode == 0
            run1.assert_unrelated_to_current_process()

            with track_state() as run2:
                result = subprocess.run([str(venv2.path / 'bin' / 'hello')])

            assert result.returncode == 0
            run1.assert_unrelated_to_current_process()
            run1.assert_unrelated_to(run2)


@non_windows
def test_script_reloads_server_on_file_change(quicken_venv, poetry_build_venv, venvs):
    # Given a problem that declares a quicken script
    # And the project is installed in venv1
    # And the script has already been executed (the server is up)
    # And the project has been updated with a new script
    # When the quicken script is executed
    # Then the command should be executed in a new server
    venv = venvs.create()
    venv.use_packages_from(quicken_venv)
    venv.use_packages_from(poetry_build_venv)

    with isolated_filesystem() as path:
        write_text(path / 'pyproject.toml', basic_poetry_pyproject)

        write_text(
            path / 'hello.py',
            '''
            def main():
                from __test_helper__ import record
                record(run='1')
            ''',
        )

        venv.install('--no-build-isolation', '.')

        with contained_children():
            with track_state() as run1:
                result = subprocess.run([venv.path / 'bin' / 'hello'])

            assert result.returncode == 0
            assert run1.run == '1'
            run1.assert_unrelated_to_current_process()

            write_text(
                path / 'hello.py',
                '''
                def main():
                    from __test_helper__ import record
                    record(run='2')
                ''',
            )

            venv.install('--no-build-isolation', path)

            with track_state() as run2:
                result = subprocess.run([venv.path / 'bin' / 'hello'])

            assert result.returncode == 0
            assert run2.run == '2'
            run2.assert_unrelated_to_current_process()
            run2.assert_unrelated_to(run1)


@non_windows
def test_script_idle_timeout(quicken_venv, poetry_build_venv, venvs):
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # When the quicken script is executed
    # And QUICKEN_IDLE_TIMEOUT is set to a nonzero value
    # Then the server will shut down after that long without
    #  any requests.
    venv = venvs.create()
    venv.use_packages_from(quicken_venv)
    venv.use_packages_from(poetry_build_venv)

    with isolated_filesystem() as path:
        write_text(path / 'pyproject.toml', basic_poetry_pyproject)

        write_text(
            path / 'hello.py',
            f'''
            def main():
                from __test_helper__ import record
                record(test_name='{current_test_name()}')
            ''',
        )

        venv.install('--no-build-isolation', '.')

        with contained_children():
            with env(QUICKEN_IDLE_TIMEOUT='0.2'):
                with track_state() as run1:
                    result = subprocess.run([venv.path / 'bin' / 'hello'])

                assert result.returncode == 0
                run1.assert_unrelated_to_current_process()

                time.sleep(0.3)

                with track_state() as run2:
                    result = subprocess.run([venv.path / 'bin' / 'hello'])

                assert result.returncode == 0
                run2.assert_unrelated_to_current_process()
                run2.assert_unrelated_to(run1)


@non_windows
def test_log_file_unwritable_fails_fast_script(quicken_venv, poetry_build_venv, venvs):
    # Given a QUICKEN_LOG path pointing to a location that is not writable
    # And the server is not up
    # When the decorated function is executed
    # Then an exception should be indicated
    venv = venvs.create()
    venv.use_packages_from(quicken_venv)
    venv.use_packages_from(poetry_build_venv)

    with isolated_filesystem() as path:
        write_text(path / 'pyproject.toml', basic_poetry_pyproject)

        write_text(
            path / 'hello.py',
            f'''
            def main():
                from __test_helper__ import record
                record(test_name='{current_test_name()}')
            ''',
        )

        venv.install('--no-build-isolation', '.')

        log_file = path / 'quicken.log'
        log_file.touch(0o000)

        with env(QUICKEN_LOG=str(log_file)):
            result = subprocess.run([venv.path / 'bin' / 'hello'])
            assert result.returncode != 0


@pytest.fixture
def quicken_project(quicken_venv, poetry_build_venv, venvs):
    venv = venvs.create()
    venv.use_packages_from(quicken_venv)
    venv.use_packages_from(poetry_build_venv)

    with isolated_filesystem() as path:
        write_text(path / 'pyproject.toml', basic_poetry_pyproject)

        write_text(
            path / 'hello.py',
            f'''
            def main():
                from __test_helper__ import record
                record(test_name='{current_test_name()}')
            ''',
        )

        venv.install('--no-build-isolation', '.')

        yield venv


@non_windows
def test_control_script_status(quicken_project):
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with status
    # Then the server status will be output
    # And the same server will be used for a subsequent command
    with contained_children():
        result = subprocess.run(
            [quicken_project.path / 'bin' / 'helloctl', 'status'],
            stdout=subprocess.PIPE,
        )
        assert result.returncode == 0
        assert "Status: 'down'" in result.stdout.decode('utf-8')

        result = subprocess.run(
            [quicken_project.path / 'bin' / 'helloctl', 'status', '--json'],
            stdout=subprocess.PIPE,
        )
        assert result.returncode == 0
        parsed_output = json.loads(result.stdout)
        assert len(parsed_output) == 1
        assert parsed_output['status'] == 'down'

        with track_state() as run1:
            result = subprocess.run([quicken_project.path / 'bin' / 'hello'])

        assert result.returncode == 0
        assert run1.test_name == current_test_name()
        run1.assert_unrelated_to_current_process()

        result = subprocess.run(
            [quicken_project.path / 'bin' / 'helloctl', 'status'],
            stdout=subprocess.PIPE,
        )
        assert result.returncode == 0
        stdout = result.stdout.decode('utf-8')
        assert "Status: 'up'" in stdout
        assert f'Pid: {run1.ppid}' in stdout

        result = subprocess.run(
            [quicken_project.path / 'bin' / 'helloctl', 'status', '--json'],
            stdout=subprocess.PIPE,
        )
        assert result.returncode == 0
        parsed_output = json.loads(result.stdout)
        assert parsed_output['pid'] == run1.ppid
        assert parsed_output['status'] == 'up'

        with track_state() as run2:
            result = subprocess.run([quicken_project.path / 'bin' / 'hello'])

        # Ensure same server is used after status check.
        assert result.returncode == 0
        assert run2.test_name == current_test_name()
        run2.assert_unrelated_to_current_process()
        run2.assert_same_parent_as(run1)


@non_windows
def test_control_script_stop(quicken_project):
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with stop
    # Then the server will stop
    # And the next command will be run with a new server
    with contained_children():
        with track_state() as run1:
            result = subprocess.run([quicken_project.path / 'bin' / 'hello'])

        assert result.returncode == 0
        assert run1.test_name == current_test_name()
        run1.assert_unrelated_to_current_process()

        result = subprocess.run(
            [quicken_project.path / 'bin' / 'helloctl', 'stop'],
            stdout=subprocess.PIPE,
        )

        assert result.returncode == 0

        with track_state() as run2:
            result = subprocess.run([quicken_project.path / 'bin' / 'hello'])

        # Ensure same server is used after status check.
        assert result.returncode == 0
        assert run2.test_name == current_test_name()
        run2.assert_unrelated_to_current_process()
        run2.assert_unrelated_to(run1)
