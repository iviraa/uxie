import ast
import random

import pytest

from bench.mutate import mutate

SRC = """\
def f(xs, n):
    if len(xs) < n and n is not None:
        return True
    for i in range(3):
        xs[0] = i
    if not xs:
        return False
    return xs[1:]
"""


def _all_mutants(src: str, allowed: set[int]) -> set[tuple[str, str]]:
    seen = set()
    for seed in range(60):
        got = mutate("f.py", src, allowed, random.Random(seed))
        if got:
            new, m = got
            ast.parse(new)  # always valid python
            assert new.splitlines()[m.line - 1].strip() == m.after
            assert src.splitlines()[m.line - 1].strip() == m.before
            seen.add((m.op, m.after))
    return seen


def test_covers_each_operator():
    seen = _all_mutants(SRC, set(range(1, 9)))
    assert ("compare", "if len(xs) <= n and n is not None:") in seen
    assert ("compare", "if len(xs) < n and n is None:") in seen
    assert ("boolop", "if len(xs) < n or n is not None:") in seen
    assert ("drop-not", "if xs:") in seen
    assert ("flip-return", "return False") in seen
    assert ("off-by-one", "for i in range(4):") in seen
    assert ("off-by-one", "xs[1] = i") in seen
    assert ("off-by-one", "return xs[2:]") in seen


def test_only_touches_allowed_lines():
    seen = _all_mutants(SRC, {3})
    assert seen == {("flip-return", "return False")}


def test_handles_unicode_offsets():
    src = 'x = "héllo"; y = x == "é"\n'
    ((op, after),) = _all_mutants(src, {1})
    assert after == 'x = "héllo"; y = x != "é"'


@pytest.mark.parametrize("src", ["def (:\n", "x = 1\n"])
def test_nothing_to_do(src):
    assert mutate("f.py", src, {1}, random.Random(0)) is None
