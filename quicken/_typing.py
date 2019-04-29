from typing import Callable, List, Mapping, Union


NoneFunction = Callable[[], None]
JSONType = Union[str, int, float, bool, None, Mapping[str, 'JSONType'], List['JSONType']]
