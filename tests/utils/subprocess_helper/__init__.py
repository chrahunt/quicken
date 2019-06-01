import json
import os
import tempfile

from contextlib import contextmanager
from pathlib import Path

from .. import env


class _State:
    def __init__(self, path):
        self._path = path
        self._parsed = False

    def __getattr__(self, name):
        if not self._parsed:
            self._parse()
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def _parse(self):

        self._parsed = True
        text = self._path.read_text(encoding='utf-8')
        if not text:
            raise RuntimeError('subprocess did not write to state file')
        self._data = json.loads(text)


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
            with env(_TEST_STATE=str(state_file)):
                state = _State(state_file)
                yield state

        # In case it wasn't already retrieved by the user.
        state._parse()
