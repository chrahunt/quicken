"""Test quicken.script and quicken.ctl_script along with associated helpers.
"""
from __future__ import annotations

import subprocess
import time

from pathlib import Path
from textwrap import dedent
from typing import Dict

import pytest

from quicken._internal.constants import (
    DEFAULT_IDLE_TIMEOUT,
    ENV_IDLE_TIMEOUT,
    ENV_LOG_FILE,
)
from quicken._internal.entrypoints import get_attribute_accumulator, ConsoleScriptHelper

from .utils import env, isolated_filesystem, load_json, local_module, write_text
from .utils.process import contained_children
from .utils.pytest import current_test_name, non_windows
from .utils.subprocess_helper import track_state


def test_attribute_accumulator():
    result = None

    def check(this_result):
        nonlocal result
        result = this_result

    get_attribute_accumulator(check).foo.bar.baz()

    assert result == ["foo", "bar", "baz"]

    get_attribute_accumulator(check).__init__.hello._.world()

    assert result == ["__init__", "hello", "_", "world"]


def test_helper_runs_module_and_func():
    # Given a module and function specification
    # When the script helper is requested to import the function
    # Then it will return the correct function
    with local_module():
        module = Path("pkg/module.py")
        module.parent.mkdir(parents=True)
        write_text(
            module,
            """
            def example():
                return 5
            """,
        )
        Path("pkg/__init__.py").touch()

        helper = ConsoleScriptHelper(["pkg", "module", "_", "example"])
        func = helper.get_func()
        assert func() == 5


def test_helper_runs_module_without_func():
    # Given a module specification
    # When the script helper is requested to import the function
    # Then it will return the correct function

    # Technically pip doesn't appear to support only-module console_script entrypoint
    # specs, but we'll test for it anyway.
    with local_module():
        module = Path("pkg/module.py")
        module.parent.mkdir(parents=True)
        write_text(
            module,
            """
            import sys

            from types import ModuleType


            class Module(ModuleType):
                def __call__(self):
                    return 5


            sys.modules[__name__].__class__ = Module
            """,
        )
        Path("pkg/__init__.py").touch()

        helper = ConsoleScriptHelper(["pkg", "module"])
        func = helper.get_func()
        assert func() == 5


@pytest.fixture(scope="module")
def poetry_build_venv(venvs):
    venv = venvs.create()
    venv.install("poetry>=0.12")
    yield venv


basic_poetry_pyproject = """
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
"""


def test_helper_text(**kwargs):
    def dict_to_dict_literal(d: Dict[str, str]):
        return "{" + ",".join(f"{k!r}: {v!r}" for k, v in d.items()) + "}"

    kwargs.setdefault("test_name", current_test_name())

    formatted_kwargs = dict_to_dict_literal(kwargs)

    return dedent(
        f"""
        def main():
            from __test_helper__ import record
            record(**{formatted_kwargs})
        """
    )


class Project:
    def __init__(self, base_dir):
        self._base_dir = base_dir

    def with_file(self, path, contents):
        write_text(self._base_dir / path, contents)
        return self

    def install_into(self, venv):
        venv.install("--no-build-isolation", str(self._base_dir))
        return InstalledProject(self, venv)

    def with_defaults(self):
        return self.with_poetry().with_helper_script()

    def with_poetry(self):
        return self.with_pyproject(basic_poetry_pyproject)

    def with_pyproject(self, contents):
        return self.with_file("pyproject.toml", contents)

    def with_script(self, contents):
        return self.with_file("hello.py", contents)

    def with_helper_script(self, **kwargs):
        return self.with_script(test_helper_text(**kwargs))


class InstalledProject:
    def __init__(self, project, venv):
        self._project = project
        self._venv = venv

    def run(self, name, *args, **kwargs):
        return subprocess.run([str(self._venv.path / "bin" / name), *args], **kwargs)

    def run_script(self, **kwargs):
        return self.run("hello", **kwargs)

    def run_tracked_script(self, **kwargs):
        with track_state() as run:
            return self.run_script(**kwargs), run

    def run_ctl_script(self, *args, **kwargs):
        return self.run("helloctl", *args, **kwargs)


@pytest.fixture
def projects(tmp_path_factory):
    def _projects():
        return Project(tmp_path_factory.mktemp("projects"))

    return _projects


@pytest.fixture
def project(projects):
    return projects()


@pytest.fixture
def poetry_venvs(quicken_venv, poetry_build_venv, venvs):
    def _poetry_venvs():
        venv = venvs.create()
        venv.use_packages_from(quicken_venv)
        venv.use_packages_from(poetry_build_venv)
        return venv

    return _poetry_venvs


@pytest.fixture
def poetry_venv(poetry_venvs):
    return poetry_venvs()


@pytest.fixture
def installed_project(poetry_venv, project):
    project.with_defaults()
    return project.install_into(poetry_venv)


@non_windows
def test_script_runs_server(installed_project):
    # Given a project that declares a quicken script
    # When the script is executed
    # And the script is executed again
    # Then the script will have been executed in the server
    # And the script will have been executed in the same server the second time
    with contained_children():
        result, run1 = installed_project.run_tracked_script()

        assert result.returncode == 0
        run1.assert_unrelated_to_current_process()

        result, run2 = installed_project.run_tracked_script()

        assert result.returncode == 0
        run2.assert_unrelated_to_current_process()
        run2.assert_same_parent_as(run1)


@non_windows
def test_script_different_venv_different_servers(poetry_venvs, project):
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # And the project is also installed in venv2
    # When the script in venv1 is executed
    # And the script in venv2 is executed
    # Then they will have been executed by different servers
    venv = poetry_venvs()
    venv2 = poetry_venvs()

    project.with_defaults()
    install1 = project.install_into(venv)
    install2 = project.install_into(venv2)

    with contained_children():
        result, run1 = install1.run_tracked_script()

        assert result.returncode == 0
        run1.assert_unrelated_to_current_process()

        result, run2 = install2.run_tracked_script()

        assert result.returncode == 0
        run1.assert_unrelated_to_current_process()
        run1.assert_unrelated_to(run2)


@non_windows
def test_script_reloads_server_on_file_change(poetry_venv, project):
    # Given a problem that declares a quicken script
    # And the project is installed in venv1
    # And the script has already been executed (the server is up)
    # And the project has been updated with a new script
    # When the quicken script is executed
    # Then the command should be executed in a new server
    project.with_poetry()
    project.with_helper_script(run="1")
    install = project.install_into(poetry_venv)

    with contained_children():
        result, run1 = install.run_tracked_script()

        assert result.returncode == 0
        assert run1.run == "1"
        run1.assert_unrelated_to_current_process()

        project.with_helper_script(run="2")
        install = project.install_into(poetry_venv)

        result, run2 = install.run_tracked_script()

        assert result.returncode == 0
        assert run2.run == "2"
        run2.assert_unrelated_to_current_process()
        run2.assert_unrelated_to(run1)


@non_windows
def test_script_respects_idle_timeout(installed_project):
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # When the quicken script is executed
    # And QUICKEN_IDLE_TIMEOUT is set to a nonzero value
    # Then the server will shut down after that long without
    #  any requests.
    with contained_children():
        with env(**{ENV_IDLE_TIMEOUT: "0.2"}):
            result, run1 = installed_project.run_tracked_script()

            assert result.returncode == 0
            run1.assert_unrelated_to_current_process()

            time.sleep(0.3)

            result, run2 = installed_project.run_tracked_script()

            assert result.returncode == 0
            run2.assert_unrelated_to_current_process()
            run2.assert_unrelated_to(run1)


@non_windows
def test_script_uses_default_idle_timeout(installed_project):
    # Given a project that declares a quicken script
    # And the project is installed in venv1
    # When the quicken script is executed
    # And QUICKEN_IDLE_TIMEOUT is not set
    # Then the default idle timeout will be used
    with contained_children():
        with env(**{ENV_IDLE_TIMEOUT: None}):
            result, run1 = installed_project.run_tracked_script()

            assert result.returncode == 0
            run1.assert_unrelated_to_current_process()

            result = installed_project.run_ctl_script(
                "status", "--json", stdout=subprocess.PIPE
            )

            assert result.returncode == 0
            parsed_output = load_json(result.stdout)
            assert parsed_output["idle_timeout"] == DEFAULT_IDLE_TIMEOUT

            result, run2 = installed_project.run_tracked_script()

            assert result.returncode == 0
            run2.assert_unrelated_to_current_process()
            run2.assert_same_parent_as(run1)


@non_windows
def test_log_file_unwritable_fails_fast_script(poetry_venv, project):
    # Given a QUICKEN_LOG path pointing to a location that is not writable
    # And the server is not up
    # When the decorated function is executed
    # Then an exception should be indicated
    project.with_poetry()
    project.with_script("main = lambda: None")
    install = project.install_into(poetry_venv)

    with isolated_filesystem() as path:
        log_file = path / "quicken.log"
        log_file.touch(0o000)

        with env(**{ENV_LOG_FILE: str(log_file)}):
            result = install.run_script()
            assert result.returncode == 1


@non_windows
def test_control_script_status(installed_project):
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with status
    # Then the server status will be output
    # And the same server will be used for a subsequent command
    with contained_children():
        result = installed_project.run_ctl_script("status", stdout=subprocess.PIPE)
        assert result.returncode == 0
        assert "Status: 'down'" in result.stdout.decode("utf-8")

        result = installed_project.run_ctl_script(
            "status", "--json", stdout=subprocess.PIPE
        )
        assert result.returncode == 0
        parsed_output = load_json(result.stdout)
        assert len(parsed_output) == 1
        assert parsed_output["status"] == "down"

        result, run1 = installed_project.run_tracked_script()

        assert result.returncode == 0
        assert run1.test_name == current_test_name()
        run1.assert_unrelated_to_current_process()

        result = installed_project.run_ctl_script("status", stdout=subprocess.PIPE)
        assert result.returncode == 0
        stdout = result.stdout.decode("utf-8")
        assert "Status: 'up'" in stdout
        assert f"Pid: {run1.ppid}" in stdout

        result = installed_project.run_ctl_script(
            "status", "--json", stdout=subprocess.PIPE
        )
        assert result.returncode == 0
        parsed_output = load_json(result.stdout)
        assert parsed_output["pid"] == run1.ppid
        assert parsed_output["status"] == "up"

        result, run2 = installed_project.run_tracked_script()

        # Ensure same server is used after status check.
        assert result.returncode == 0
        assert run2.test_name == current_test_name()
        run2.assert_unrelated_to_current_process()
        run2.assert_same_parent_as(run1)


@non_windows
def test_control_script_stop(installed_project):
    # Given a project that declares a quicken and quicken-ctl script
    # And the server is up
    # When the quicken-ctl script is executed with stop
    # Then the server will stop
    # And the next command will be run with a new server
    with contained_children():
        result, run1 = installed_project.run_tracked_script()

        assert result.returncode == 0
        assert run1.test_name == current_test_name()
        run1.assert_unrelated_to_current_process()

        result = installed_project.run_ctl_script("stop")

        assert result.returncode == 0

        result, run2 = installed_project.run_tracked_script()

        assert result.returncode == 0
        assert run2.test_name == current_test_name()
        run2.assert_unrelated_to_current_process()
        run2.assert_unrelated_to(run1)
