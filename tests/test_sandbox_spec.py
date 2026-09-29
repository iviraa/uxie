import importlib.util
from pathlib import Path

import pytest

from uxie.sandbox.spec import CONFIRMED, INCONCLUSIVE, LIKELY, REFUTED, RunOutcome, decide

_spec = importlib.util.spec_from_file_location(
    "run_test", Path(__file__).parents[1] / "sandbox" / "run_test.py"
)
run_test = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_test)


def _o(outcome):
    return RunOutcome(outcome, 0, 0.1)


@pytest.mark.parametrize(
    "head,base,want",
    [
        ("failed", "passed", CONFIRMED),
        ("failed", "error", LIKELY),
        ("failed", None, LIKELY),
        ("failed", "failed", INCONCLUSIVE),
        ("passed", None, REFUTED),
        ("error", None, INCONCLUSIVE),
        ("timeout", None, INCONCLUSIVE),
    ],
)
def test_decide(head, base, want):
    assert decide(_o(head), _o(base) if base else None) == want


@pytest.mark.parametrize(
    "msg,want",
    [
        ("TypeError: Account.__init__() got an unexpected keyword argument 'x'", True),
        ("TypeError: f() takes 2 positional arguments but 3 were given", True),
        ("ImportError: cannot import name 'total_pages' from 'ledger.statements'", True),
        ("ModuleNotFoundError: No module named 'ledger.fees'", True),
        ("AttributeError: module 'ledger' has no attribute 'fees'", True),
        ("AttributeError: 'NoneType' object has no attribute 'items'", False),  # a real bug
        ("TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'", False),
        ("AssertionError: assert 1 == 2", False),
        ("ledger.accounts.InsufficientFunds: x", False),
    ],
)
def test_cant_run(msg, want):
    assert run_test.cant_run(msg) is want
