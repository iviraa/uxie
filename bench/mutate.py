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


def mutate(
    path: str, source: str, allowed_lines: set[int], rng: random.Random
) -> tuple[str, Mutation] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = source.encode().split(b"\n")
    cands = _candidates(tree, lines, allowed_lines)
    rng.shuffle(cands)
    for ed in cands:
        raw = lines[ed.line - 1]
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
