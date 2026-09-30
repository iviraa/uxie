"""AST-guided single-line bug injection. Every mutant must still parse."""

from __future__ import annotations

import ast
import random
from dataclasses import dataclass

_OP_SRC = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}
_OP_FLIP = {
    ast.Lt: "<=",
    ast.LtE: "<",
    ast.Gt: ">=",
    ast.GtE: ">",
    ast.Eq: "!=",
    ast.NotEq: "==",
    ast.Is: "is not",
    ast.IsNot: "is",
    ast.In: "not in",
    ast.NotIn: "in",
}


@dataclass(frozen=True)
class Mutation:
    path: str
    line: int
    op: str
    before: str
    after: str


@dataclass(frozen=True)
class _Edit:
    line: int
    start: int  # byte offsets within the line
    end: int
    new: bytes
    op: str


def _gap(lines: list[bytes], line: int, start: int, end: int) -> bytes:
    return lines[line - 1][start:end]


def _candidates(tree: ast.AST, lines: list[bytes], allowed: set[int]) -> list[_Edit]:
    out: list[_Edit] = []

    def one_line(*nodes: ast.AST) -> int | None:
        rows = {getattr(n, "lineno", None) for n in nodes} | {
            getattr(n, "end_lineno", None) for n in nodes
        }
        return rows.pop() if len(rows) == 1 and None not in rows else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            ln = one_line(node)
            op = type(node.ops[0])
            if ln in allowed and op in _OP_SRC:
                s, e = node.left.end_col_offset or 0, node.comparators[0].col_offset
                gap = _gap(lines, ln, s, e)
                if gap.strip() == _OP_SRC[op].encode():
                    new = gap.replace(gap.strip(), _OP_FLIP[op].encode())
                    out.append(_Edit(ln, s, e, new, "compare"))
        elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
            ln = one_line(node)
            if ln in allowed:
                s, e = node.values[0].end_col_offset or 0, node.values[1].col_offset
                gap = _gap(lines, ln, s, e)
                src, dst = (b"and", b"or") if isinstance(node.op, ast.And) else (b"or", b"and")
                if gap.strip() == src:
                    out.append(_Edit(ln, s, e, gap.replace(src, dst), "boolop"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            ln = one_line(node)
            if ln in allowed:
                s, e = node.col_offset, node.operand.col_offset
                if _gap(lines, ln, s, e).strip() == b"not":
                    out.append(_Edit(ln, s, e, b"", "drop-not"))
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
            v = node.value
            ln = one_line(v)
            if ln in allowed and isinstance(v.value, bool):
                new = b"False" if v.value else b"True"
                out.append(_Edit(ln, v.col_offset, v.end_col_offset or 0, new, "flip-return"))
        elif isinstance(node, ast.Call | ast.Subscript | ast.Slice):
            for c in _int_consts(node):
                ln = one_line(c)
                if ln in allowed:
                    new = str(int(c.value) + 1).encode()  # type: ignore[arg-type]
                    out.append(_Edit(ln, c.col_offset, c.end_col_offset or 0, new, "off-by-one"))
    return out


def _int_consts(node: ast.AST) -> list[ast.Constant]:
    """Integer literals used as range() bounds or subscript/slice indexes."""
    if isinstance(node, ast.Call):
        if not (isinstance(node.func, ast.Name) and node.func.id == "range"):
            return []
        args: list[ast.expr] = list(node.args)
    elif isinstance(node, ast.Subscript):
        args = [node.slice]
    else:
        assert isinstance(node, ast.Slice)
        args = [a for a in (node.lower, node.upper) if a is not None]
    return [
        a
        for a in args
        if isinstance(a, ast.Constant) and type(a.value) is int and 0 <= a.value <= 16
    ]


LOGIC = ("compare", "boolop", "drop-not", "off-by-one", "flip-return")
BEHAVIOR = ("drop-update", "swap-stmts", "swallow-error", "drop-guard", "swap-args")


def _seg(n: ast.AST) -> tuple[int, int, int] | None:
    """(line, start col, end col) for a node that sits on one line."""
    ln, end = getattr(n, "lineno", None), getattr(n, "end_lineno", None)
    if ln is None or ln != end:
        return None
    return ln, n.col_offset, n.end_col_offset or 0  # type: ignore[attr-defined]


def _is_state(t: ast.AST) -> bool:
    return isinstance(t, ast.Attribute | ast.Subscript)


def _guard(test: ast.AST) -> bool:
    for n in ast.walk(test):
        if isinstance(n, ast.Compare) and any(
            isinstance(c, ast.Constant) and c.value is None for c in n.comparators
        ):
            return True
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return True
    return False


def _behavior(tree: ast.AST, lines: list[bytes], allowed: set[int]) -> list[_Edit]:
    out: list[_Edit] = []

    def add(n: ast.AST, new: bytes, op: str) -> None:
        seg = _seg(n)
        if seg and seg[0] in allowed:
            out.append(_Edit(seg[0], seg[1], seg[2], new, op))

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AugAssign)
            and _is_state(node.target)
            or (isinstance(node, ast.Assign) and any(_is_state(t) for t in node.targets))
        ):
            add(node, b"pass", "drop-update")
        elif isinstance(node, ast.ExceptHandler):
            for stmt in node.body:
                if isinstance(stmt, ast.Raise):
                    add(stmt, b"pass", "swallow-error")
        elif isinstance(node, ast.If) and _guard(node.test):
            add(node.test, b"False", "drop-guard")
        elif isinstance(node, ast.Call) and len(node.args) >= 2:
            a, b = node.args[0], node.args[1]
            sa, sb = _seg(a), _seg(b)
            if (
                isinstance(a, ast.Name)
                and isinstance(b, ast.Name)
                and a.id != b.id
                and sa
                and sb
                and sa[0] == sb[0]
                and sa[0] in allowed
            ):
                raw = lines[sa[0] - 1]
                new = raw[sb[1] : sb[2]] + raw[sa[2] : sb[1]] + raw[sa[1] : sa[2]]
                out.append(_Edit(sa[0], sa[1], sb[2], new, "swap-args"))
        for body in (getattr(node, f, None) for f in ("body", "orelse", "finalbody")):
            if not isinstance(body, list):
                continue
            simple = (ast.Expr, ast.AugAssign, ast.Assign)
            for x, y in zip(body, body[1:], strict=False):
                sx, sy = _seg(x), _seg(y)
                if (
                    isinstance(x, simple)
                    and isinstance(y, simple)
                    and sx
                    and sy
                    and sy[0] == sx[0] + 1
                    and sx[0] in allowed
                    and not any(isinstance(t, ast.Name) for t in getattr(x, "targets", []))
                    and lines[sx[0] - 1].strip() != lines[sy[0] - 1].strip()
                ):
                    out.append(_Edit(sx[0], 0, 0, b"", "swap-stmts"))  # swap this line and next
    return out


def mutate(
    path: str,
    source: str,
    allowed_lines: set[int],
    rng: random.Random,
    ops: tuple[str, ...] = LOGIC,
) -> tuple[str, Mutation] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.encode().split(b"\n")
    by_op: dict[str, list[_Edit]] = {}
    for ed in _candidates(tree, lines, allowed_lines) + _behavior(tree, lines, allowed_lines):
        if ed.op in ops:
            by_op.setdefault(ed.op, []).append(ed)
    kinds = sorted(by_op)
    rng.shuffle(kinds)  # pick a category first so frequent ones (compare) don't dominate
    for kind in kinds:
        cands = by_op[kind]
        rng.shuffle(cands)
        for ed in cands:
            raw = lines[ed.line - 1]
            if ed.op == "swap-stmts":
                nxt = lines[ed.line]
                ind_a = raw[: len(raw) - len(raw.lstrip())]
                ind_b = nxt[: len(nxt) - len(nxt.lstrip())]
                new_line, new_next = ind_a + nxt.lstrip(), ind_b + raw.lstrip()
                mutated_lines = [*lines[: ed.line - 1], new_line, new_next, *lines[ed.line + 1 :]]
            else:
                new_line = raw[: ed.start] + ed.new + raw[ed.end :]
                mutated_lines = [*lines[: ed.line - 1], new_line, *lines[ed.line :]]
            mutated = b"\n".join(mutated_lines).decode()
            try:
                ast.parse(mutated)
            except SyntaxError:
                continue
            if mutated != source:
                return mutated, Mutation(
                    path, ed.line, ed.op, raw.decode().strip(), new_line.decode().strip()
                )
    return None
