import functools
import logging


logger = logging.getLogger(__name__)


def log_calls(obj):
    import inspect
    if inspect.isclass(obj):
        return _handle_class(obj)
    return _log_calls(obj)


def _handle_class(cls):
    import inspect
    for name, fn in inspect.getmembers(cls):
        logged_member = _log_calls(fn, cls.__name__)
        if logged_member != fn:
            setattr(cls, name, logged_member)
    return cls


def _log_calls(obj, namespace=None):
    import inspect
    try:
        name = obj.__name__
    except AttributeError:
        name = None
    if namespace is not None:
        name = f'{namespace}.{name}'

    if inspect.isgeneratorfunction(obj):
        return _handle_generator_function(obj, name)
    if inspect.iscoroutinefunction(obj):
        return _handle_coroutine_function(obj, name)
    if inspect.isfunction(obj):
        return _handle_function(obj, name)
    return obj


def _handle_function(f, name):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        logger.debug(f'(call) {name}()')
        return f(*args, **kwargs)
    return wrapper


def _handle_generator_function(f, name):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        logger.debug(f'(next) {name}()')
        yield from f(*args, **kwargs)

    def provider(*args, **kwargs):
        logger.debug(f'(invoke) {name}()')
        return wrapper(*args, **kwargs)
    return provider


def _handle_coroutine_function(f, name):
    @functools.wraps(f)
    async def wrapper(*args, **kwargs):
        logger.debug(f'(await) {name}()')
        return await f(*args, **kwargs)

    def provider(*args, **kwargs):
        logger.debug(f'(invoke) {name}()')
        return wrapper(*args, **kwargs)
    return provider


def lineno():
    import inspect
    frame = inspect.getouterframes(inspect.currentframe())[1]
    return frame.f_lineno
