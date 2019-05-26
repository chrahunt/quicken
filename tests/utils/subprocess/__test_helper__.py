"""
Use in subprocess tests to record expected state information.
"""
import os

from pathlib import Path


def record():
    test_state = Path(os.environ['TEST_STATE'])
    test_state.write_text(f'{os.getpid()} {os.getppid()}')
