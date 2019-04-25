#!/usr/bin/env python
"""
Helper to make venv python invocation independent of OS/shell.

Usage:
    python.py <args>

Refers to AGENT_OS and VENV.
"""
import os
import sys

from pathlib import Path


def get_python_path(venv: Path):
    python_paths = {
        'Linux': Path('bin') / 'python',
        'Windows_NT': Path('Scripts') / 'python.exe',
    }
    return venv / python_paths[os.environ['AGENT_OS']]


if __name__ == '__main__':
    python_path = get_python_path(Path(os.environ['VENV']).absolute())
    args = [str(python_path), *sys.argv[1:]]
    os.execv(python_path, args)
