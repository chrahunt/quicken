from typing import Callable


NoneFunctionT = Callable[[], None]
CliFactoryT = Callable[[], NoneFunctionT]
