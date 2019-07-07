from ._typing import MYPY_CHECK_RUNNING


if MYPY_CHECK_RUNNING:
    from typing import Callable, List, Mapping, Optional, Union

    NoneFunction = Callable[[], None]
    JSONType = Union[
        str, int, float, bool, None, Mapping[str, "JSONType"], List["JSONType"]
    ]
    MainFunction = Callable[[], Optional[int]]
    MainProvider = Callable[[], MainFunction]
