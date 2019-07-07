import os

from pathlib import Path

import pytest

from quicken._internal._multiprocessing_asyncio import AsyncProcess

from .utils import isolated_filesystem
from .utils.pytest import non_windows


pytestmark = non_windows


@pytest.mark.asyncio
async def test_process_executor_runs_nested_function():
    with isolated_filesystem():
        output_file = Path.cwd() / "output.txt"

        def nested_function():
            output_file.write_text(str(os.getpid()), encoding="utf-8")

        process = AsyncProcess(target=nested_function)
        process.start()
        exitcode = await process.wait()
        assert exitcode == 0, "Must have exited cleanly"
        pid = os.getpid()
        other_pid = output_file.read_text(encoding="utf-8")
        assert other_pid, "Function must have executed and written pid"
        assert pid != int(other_pid), "Function must have executed in another process"


@pytest.mark.asyncio
async def test_process_executor_runs_exiting_function():
    exit_code = 5

    def runner():
        os._exit(exit_code)

    process = AsyncProcess(target=runner)
    process.start()
    exitcode = await process.wait()
    assert exitcode == exit_code, "Must match expected exit code"
