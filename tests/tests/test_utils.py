import os

from ..utils import captured_std_streams, env


def test_captured_std_streams():
    text = 'hello world'
    with captured_std_streams() as (stdin, stdout, stderr):
        print(text)

    assert stdout.read() == f'{text}\n'
