import os
import tempfile

from contextlib import contextmanager
from pathlib import Path

from .. import env


class _State:
    def __init__(self, path):
        self._path = path
        self._parsed = False
        self._pid = None
        self._ppid = None

    @property
    def ppid(self):
        if not self._parsed:
            self._parse()
        return self._ppid

    @property
    def pid(self):
        if not self._parsed:
            self._parse()
        return self._pid

    def _parse(self):
        self._parsed = True
        text = self._path.read_text(encoding='utf-8')
        pid, ppid = [int(v) for v in text.split()]
        self._pid = int(pid)
        self._ppid = int(ppid)


@contextmanager
def _add_test_helper():
    subprocess_path = str(Path(__file__).parent)
    paths = os.environ.get('PYTHONPATH', '').split(os.pathsep)
    if subprocess_path not in paths:
        paths.insert(0, subprocess_path)
    with env(PYTHONPATH=os.pathsep.join(paths)):
        yield


@contextmanager
def track_state():
    """Helper for getting state from a sub-process. The convention
    is the sub-process should write data to TEST_STATE:

    pid ppid other*
    """
    with tempfile.NamedTemporaryFile() as f:
        state_file = Path(f.name)
        with _add_test_helper():
            with env(TEST_STATE=str(state_file)):
                yield _State(state_file)
