"""Single-line bug injection for Go and TypeScript/JavaScript, guided by tree-sitter.

Logic mutations flip one expression; behavioral mutations change what the code does over
time (a dropped state update, reordered statements, a swallowed error, a removed guard,
swapped arguments). Every mutant must re-parse without errors.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import tree_sitter_go
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from bench.mutate import Mutation

LOGIC = ("compare", "boolop", "drop-not", "off-by-one", "flip-return")
BEHAVIOR = ("drop-update", "swap-stmts", "swallow-error", "drop-guard", "swap-args")

_PARSERS = {
    "go": Parser(Language(tree_sitter_go.language())),
    "ts": Parser(Language(tree_sitter_typescript.language_typescript())),
    "tsx": Parser(Language(tree_sitter_typescript.language_tsx())),
}
_FLIP = {
    "<": "<=",
    "<=": "<",
    ">": ">=",
    ">=": ">",
    "==": "!=",
    "!=": "==",
    "===": "!==",
    "!==": "===",
}
_NULLS = {"nil", "null", "undefined"}


@dataclass(frozen=True)
class _Edit:
    start: int  # byte offsets into the file
    end: int
    new: bytes
    op: str
    line: int  # 1-based, the line a reviewer should flag


def parser_for(path: str) -> Parser | None:
    if path.endswith(".go"):
        return _PARSERS["go"]
    if path.endswith((".tsx", ".jsx")):
        return _PARSERS["tsx"]
    if path.endswith((".ts", ".mts", ".cts", ".js", ".mjs", ".cjs")):
        return _PARSERS["ts"]
    return None


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _line(n: Node) -> int:
    return n.start_point[0] + 1


def _one_line(n: Node) -> bool:
    return n.start_point[0] == n.end_point[0]


def _text(n: Node) -> str:
    return (n.text or b"").decode()


def _is_state_target(n: Node | None) -> bool:
    return n is not None and n.type in (
        "selector_expression",
        "index_expression",
        "member_expression",
        "subscript_expression",
    )


def _candidates(root: Node, allowed: set[int]) -> list[_Edit]:
    out: list[_Edit] = []

    def add(start: int, end: int, new: str, op: str, line: int) -> None:
        if line in allowed:
            out.append(_Edit(start, end, new.encode(), op, line))

    for n in _walk(root):
        t = n.type
        if t == "if_statement":  # multi-line statement, but its condition sits on one line
            cond = n.child_by_field_name("condition")
            if cond is not None and any(_text(x) in _NULLS for x in _walk(cond)):
                inner = (
                    cond.named_children[0]
                    if cond.type == "parenthesized_expression" and cond.named_children
                    else cond
                )
                add(inner.start_byte, inner.end_byte, "false", "drop-guard", _line(n))
        if not _one_line(n):
            if t in ("block", "statement_block", "statement_list"):
                _swap_candidates(n, allowed, out)
            continue
        if t == "binary_expression":
            op = n.child_by_field_name("operator")
            if op is not None and _text(op) in _FLIP:
                add(op.start_byte, op.end_byte, _FLIP[_text(op)], "compare", _line(n))
            elif op is not None and _text(op) in ("&&", "||"):
                add(
                    op.start_byte,
                    op.end_byte,
                    "||" if _text(op) == "&&" else "&&",
                    "boolop",
                    _line(n),
                )
        elif t == "unary_expression" and _text(n).startswith("!"):
            arg = n.child_by_field_name("argument") or n.child_by_field_name("operand")
            if arg is not None:
                add(n.start_byte, n.end_byte, _text(arg), "drop-not", _line(n))
        elif (
            t in ("int_literal", "number")
            and n.parent is not None
            and n.parent.type
            in ("index_expression", "slice_expression", "subscript_expression", "binary_expression")
        ):
            txt = _text(n)
            if txt.isdigit() and int(txt) <= 16:
                add(n.start_byte, n.end_byte, str(int(txt) + 1), "off-by-one", _line(n))
        elif t == "return_statement":
            vals = [c for c in n.named_children if c.type != "comment"]
            flat = vals[0].named_children if vals and vals[0].type == "expression_list" else vals
            if len(flat) == 1 and flat[0].type in ("true", "false"):
                v = flat[0]
                add(
                    v.start_byte,
                    v.end_byte,
                    "false" if v.type == "true" else "true",
                    "flip-return",
                    _line(n),
                )
            if flat and _text(flat[-1]) == "err":  # go: swallow the error being returned
                e = flat[-1]
                add(e.start_byte, e.end_byte, "nil", "swallow-error", _line(n))
        elif t == "throw_statement" and any(a.type == "catch_clause" for a in _ancestors(n)):
            add(n.start_byte, n.end_byte, ";", "swallow-error", _line(n))
        elif t in ("inc_statement", "dec_statement") or (
            t == "assignment_statement" and _is_state_target(_go_assign_target(n))
        ):
            add(n.start_byte, n.end_byte, "", "drop-update", _line(n))
        elif t == "expression_statement" and n.named_children:
            e = n.named_children[0]
            if e.type in (
                "assignment_expression",
                "augmented_assignment_expression",
                "update_expression",
            ) and _is_state_target(
                e.child_by_field_name("left") or e.child_by_field_name("argument")
            ):
                add(n.start_byte, n.end_byte, ";", "drop-update", _line(n))
        args = n.child_by_field_name("arguments") if t == "call_expression" else None
        if args is not None:
            named = [a for a in args.named_children if a.type != "comment"]
            if (
                len(named) >= 2
                and all(a.type == "identifier" for a in named[:2])
                and _text(named[0]) != _text(named[1])
            ):
                a, b = named[0], named[1]
                new = (
                    _text(b)
                    + (args.text or b"")[
                        a.end_byte - args.start_byte : b.start_byte - args.start_byte
                    ].decode()
                    + _text(a)
                )
                add(a.start_byte, b.end_byte, new, "swap-args", _line(n))
    return out


def _go_assign_target(n: Node) -> Node | None:
    """First assignee of a Go assignment (`s.count = ...` -> `s.count`)."""
    left = n.child_by_field_name("left")
    return left.named_children[0] if left is not None and left.named_children else None


def _ancestors(n: Node) -> Iterator[Node]:
    p = n.parent
    while p is not None:
        yield p
        p = p.parent


_SIMPLE = {"expression_statement", "inc_statement", "dec_statement", "assignment_statement"}


def _swap_candidates(block: Node, allowed: set[int], out: list[_Edit]) -> None:
    stmts = [c for c in block.named_children if c.type != "comment"]
    for a, b in zip(stmts, stmts[1:], strict=False):
        if (
            a.type in _SIMPLE
            and b.type in _SIMPLE
            and _one_line(a)
            and _one_line(b)
            and _line(a) in allowed
            and _line(b) == _line(a) + 1
            and _text(a) != _text(b)
        ):
            # swap by rewriting the span between them, keeping indentation
            src = block.text or b""
            gap = src[a.end_byte - block.start_byte : b.start_byte - block.start_byte].decode()
            out.append(
                _Edit(
                    a.start_byte,
                    b.end_byte,
                    (_text(b) + gap + _text(a)).encode(),
                    "swap-stmts",
                    _line(a),
                )
            )


def mutate(
    path: str,
    source: str,
    allowed_lines: set[int],
    rng: random.Random,
    ops: tuple[str, ...] = LOGIC + BEHAVIOR,
) -> tuple[str, Mutation] | None:
    parser = parser_for(path)
    if parser is None:
        return None
    raw = source.encode()
    tree = parser.parse(raw)
    if tree.root_node.has_error:
        return None
    by_op: dict[str, list[_Edit]] = {}
    for e in _candidates(tree.root_node, allowed_lines):
        if e.op in ops:
            by_op.setdefault(e.op, []).append(e)
    kinds = sorted(by_op)
    rng.shuffle(kinds)  # pick a category first so frequent ones (compare) don't dominate
    for kind in kinds:
        edits = by_op[kind]
        rng.shuffle(edits)
        for e in edits:
            new = raw[: e.start] + e.new + raw[e.end :]
            if parser.parse(new).root_node.has_error or new == raw:
                continue
            lines_old, lines_new = raw.split(b"\n"), new.split(b"\n")
            before = lines_old[e.line - 1].decode().strip()
            after = lines_new[e.line - 1].decode().strip() if e.line <= len(lines_new) else ""
            return new.decode(), Mutation(path, e.line, e.op, before, after)
    return None


MutateFn = Callable[[str, str, set[int], random.Random], "tuple[str, Mutation] | None"]
