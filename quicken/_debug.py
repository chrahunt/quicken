import functools
import logging


logger = logging.getLogger(__name__)


def get_wrapper(cls, name, fn):
    @functools.wraps(fn)
    def callback(*args, **kwargs):
        logger.debug(f'{cls.__name__}.{name}()')
        return fn(*args, **kwargs)
    return callback


def log_calls(cls):
    import inspect
    import types
    for name, fn in inspect.getmembers(cls):
        if isinstance(fn, (types.FunctionType, types.MethodType)):
            setattr(cls, name, get_wrapper(cls, name, fn))


def lineno():
    import inspect
    frame = inspect.getouterframes(inspect.currentframe())[1]
    return frame.f_lineno
