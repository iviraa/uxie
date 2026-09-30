import random

import pytest

from bench.treemutate import BEHAVIOR, LOGIC, mutate, parser_for

GO = """package ledger

func (a *Account) Withdraw(n int, fee int) error {
	if a == nil {
		return errNil
	}
	if n <= a.balance && n > 0 {
		a.balance -= n
		a.count++
	} else if !a.overdraft {
		return errFunds
	}
	a.log(n)
	a.flush()
	err := a.save()
	if err != nil {
		return err
	}
	check(n, fee)
	return nil
}

func ok(xs []int) bool {
	return xs[0] < 3
}
"""

TS = """export class Cart {
  total = 0;
  add(price: number, qty: number): boolean {
    if (price == null) {
      return false;
    }
    if (qty >= 1 && !this.locked) {
      this.total += price * qty;
      this.items[0] = price;
    }
    this.log(price);
    this.save();
    try {
      this.sync();
    } catch (e) {
      throw e;
    }
    apply(price, qty);
    return true;
  }
}
"""


def _all(path: str, src: str, op: str) -> set[str]:
    allowed = set(range(1, src.count("\n") + 2))
    seen = set()
    for seed in range(120):
        got = mutate(path, src, allowed, random.Random(seed), ops=(op,))
        if got:
            new, m = got
            assert not parser_for(path).parse(new.encode()).root_node.has_error
            assert m.op == op and new != src
            seen.add(m.after)
    return seen


@pytest.mark.parametrize("op", LOGIC + BEHAVIOR)
def test_every_operator_fires_on_go_and_ts(op):
    go, ts = _all("pkg/ledger.go", GO, op), _all("src/cart.ts", TS, op)
    assert go or ts, f"{op} never applied"


def test_specific_behavioral_mutants():
    assert "" in _all("pkg/ledger.go", GO, "drop-update")  # `a.count++` deleted
    assert "return nil" in _all("pkg/ledger.go", GO, "swallow-error")
    assert "if false {" in _all("pkg/ledger.go", GO, "drop-guard")
    assert "check(fee, n)" in _all("pkg/ledger.go", GO, "swap-args")
    assert "a.flush()" in _all("pkg/ledger.go", GO, "swap-stmts")  # flush moved above log
    ts_swallow = _all("src/cart.ts", TS, "swallow-error")
    assert ";" in ts_swallow  # `throw e` inside catch removed
    assert "if (false) {" in _all("src/cart.ts", TS, "drop-guard")
    assert "apply(qty, price);" in _all("src/cart.ts", TS, "swap-args")


def test_only_touches_allowed_lines():
    src_lines = GO.splitlines()
    for seed in range(60):
        got = mutate("pkg/ledger.go", GO, {8}, random.Random(seed))  # `a.balance -= n` only
        if got:
            new, m = got
            assert m.line == 8
            diff = [
                i
                for i, (a, b) in enumerate(zip(src_lines, new.splitlines(), strict=False))
                if a != b
            ]
            assert diff and diff[0] == 7


def test_unparseable_or_unknown_input():
    assert mutate("x.go", "func (", {1}, random.Random(0)) is None
    assert mutate("x.rs", "fn main() {}", {1}, random.Random(0)) is None
