import ast


def is_main(node: ast.AST) -> bool:
    """Returns whether a node represents `if __name__ == '__main__':` or
    `if '__main__' == __name__:`.
    """
    if not isinstance(node, ast.If):
        return False

    test = node.test

    if not isinstance(test, ast.Compare):
        return False

    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False

    if len(test.comparators) != 1:
        return False

    left = test.left
    right = test.comparators[0]

    if isinstance(left, ast.Name):
        name = left
    elif isinstance(right, ast.Name):
        name = right
    else:
        return False

    if isinstance(left, ast.Str):
        str_part = left
    elif isinstance(right, ast.Str):
        str_part = right
    else:
        return False

    if name.id != "__name__":
        return False

    if not isinstance(name.ctx, ast.Load):
        return False

    if str_part.s != "__main__":
        return False

    return True
