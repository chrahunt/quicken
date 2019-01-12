import asyncio
import os
from pathlib import Path

import pytest

from quicken._concurrent_futures import ProcessExecutor

from .utils import isolated_filesystem


@pytest.mark.asyncio
async def test_process_executor_runs_nested_function():
    executor = ProcessExecutor()
    loop = asyncio.get_running_loop()
    with isolated_filesystem():
        output_file = Path.cwd() / 'output.txt'
        def nested_function():
            output_file.write_text(str(os.getpid()), encoding='utf-8')
        result = await loop.run_in_executor(executor, nested_function)
        assert result.exitcode == 0, 'Must have exited cleanly'
        pid = os.getpid()
        other_pid = output_file.read_text(encoding='utf-8')
        assert other_pid, 'Function must have executed and written pid'
        assert pid != int(other_pid),\
            'Function must have executed in another process'


@pytest.mark.asyncio
async def test_process_executor_runs_exiting_function():
    executor = ProcessExecutor()
    loop = asyncio.get_running_loop()
    exit_code = 5
    def runner():
        os._exit(exit_code)
    result = await loop.run_in_executor(executor, runner)
    assert result.exitcode == exit_code, 'Must match expected exit code'
