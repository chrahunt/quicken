"""Provides pytest fixtures for efficiently working with virtual environments.

Includes the following optimizations:
* only install pip into a single virtual environment
* cached installation environment (clear with pytest --cache-clear)
* linked and cached virtual environments
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import venv

from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any, List

import pytest


_default_venv_args = {"symlinks": False if sys.platform.startswith("win") else True}


@pytest.fixture(scope="session")
def _venvs_root(request):
    """Act as the root virtual environment which hosts pip.
    """
    # Our config keys are organized under 'venvs':
    # * root - the root venv if applicable
    # * cached/ - top-level directory for cached venvs
    root_venv_dir = request.config.cache.get("venvs/root", None)

    # FIXME: More thorough check of existing virtual environment
    need_root_venv = root_venv_dir is None or not Path(root_venv_dir).exists()

    if need_root_venv:
        root_venv_dir = tempfile.mkdtemp()
        venv.create(root_venv_dir, **_default_venv_args, with_pip=True)
        root_venv = _Venv(Path(root_venv_dir))

        result = root_venv.install("--upgrade", "pip")
        assert result.returncode == 0

        request.config.cache.set("venvs/root", root_venv_dir)
    else:
        root_venv = _Venv(Path(root_venv_dir))

    yield root_venv


# XXX: May be better to use a smaller scope, but then we can't use it in other fixtures
#  as easily.
@pytest.fixture(scope="session")
def venvs(_venvs_root):
    """User interface for getting and retrieving virtualenvs.
    """
    keep_venvs = os.environ.get("PYTEST_VENVS_KEEP", False)

    if keep_venvs:
        mkdir = lambda *args, **kwargs: nullcontext(tempfile.mkdtemp(*args, **kwargs))
    else:
        mkdir = tempfile.TemporaryDirectory

    class Factory:
        def create(self):
            path = stack.enter_context(mkdir())
            venv.create(path, **_default_venv_args)
            v = _Venv(Path(path))
            v.use_packages_from(_venvs_root)
            return v

    with ExitStack() as stack:
        yield Factory()


class _Venv:
    def __init__(self, path: Path):
        self.path = path

    def install(self, *args, check=True):
        """Install into this virtual environment.
        """
        base_args = ["-m", "pip", "install"]
        result = self.run([*base_args, *args])

        if check:
            assert result.returncode == 0

        return result

    def use_packages_from(self, other: _Venv):
        """Link this venv to `other`, making the packages in `other` available.
        """
        prefix = "_pytest_venvs-"
        site_packages = self._get_site_packages()
        fd, path = tempfile.mkstemp(suffix=".pth", prefix=prefix, dir=site_packages)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(str(other._get_site_packages()))

    def run(self, cmd: List[Any], *args, **kwargs):
        """Passes-thru args to Python running in the context of this environment.
        """
        base_path = Path(self.path)
        if sys.platform.startswith("win"):
            interpreter = base_path / "Scripts" / "python.exe"
        else:
            interpreter = base_path / "bin" / "python"
        cmd.insert(0, interpreter)
        return subprocess.run([str(c) for c in cmd], *args, **kwargs)

    def _get_site_packages(self) -> Path:
        if sys.platform.startswith("win"):
            return self.path / "Lib" / "site-packages"

        major, minor, *_ = sys.version_info
        return self.path / "lib" / f"python{major}.{minor}" / "site-packages"
