"""
Use in subprocess tests to record expected state information.
"""
import json
import os

from pathlib import Path


def record(**kwargs):
    test_state = Path(os.environ['_TEST_STATE'])
    d = {
        'pid': os.getpid(),
        'ppid': os.getppid(),
        **kwargs,
    }
    s = json.dumps(d)
    test_state.write_text(s, encoding='utf-8')
