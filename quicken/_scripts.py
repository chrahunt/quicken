"""Helpers for "console_scripts"/"script" interceptors.
"""
def parse_script_spec(parts):
    """
    Returns:
        (module_parts, function_parts)
    """
    try:
        i = parts.index('_')
    except ValueError:
        return parts, []
    else:
        return parts[:i], parts[i+1:]


def get_nested_attr(o, parts):
    for name in parts:
        o = getattr(o, name)
    return o


def get_attribute_accumulator(callback, context=None):
    """Who knows what someone may put in their entry point spec.

    We try to take the most flexible approach here and accept as much as
    possible.

    Args:
        callback: called when the accumulator is called with the gathered
            names as the first argument.
        context: names that should have explicit returned values.
    """
    # Use variable in closure to reduce chance of conflicting name.
    parts = []

    class Accumulator:
        def __getattribute__(self, name):
            if name == '__call__':
                return object.__getattribute__(self, name)

            if context:
                try:
                    return context[name]
                except KeyError:
                    pass

            parts.append(name)
            return self

        def __call__(self, *args, **kwargs):
            nonlocal parts
            current_parts = parts
            parts = []
            return callback(current_parts, *args, **kwargs)

    return Accumulator()
